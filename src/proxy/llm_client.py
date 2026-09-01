"""Forward requests to the real LLM API, or return mock responses.

Upstream resilience: transient failures (HTTP 408/429/5xx and transport
errors) are retried with exponential backoff, bounded by
LLM_RETRY_MAX_ATTEMPTS / LLM_RETRY_BACKOFF_SECONDS. A numeric Retry-After
header from the server overrides computed backoff; waits LONGER than the
30 s in-request budget fail fast instead of sleeping (e.g. daily-cap 429s).

On top of retries, a per-upstream circuit breaker (CLOSED/OPEN/HALF_OPEN,
LLM_BREAKER_*) fails fast once a sustained failure pattern is detected —
retries bound the cost of ONE bad call; the breaker bounds the cost of a
bad upstream.
"""

from __future__ import annotations

import logging
import time
import uuid
from asyncio import sleep
from typing import Any

import httpx

from .config import get_settings

logger = logging.getLogger("proxy")

# Statuses worth another attempt: 408 (request timeout) and 429 (rate
# limited); every 5xx is also retried. Other 4xx responses are deterministic
# (auth/validation problems the caller must fix) — retrying them just burns
# caller latency.
_RETRYABLE_STATUSES = frozenset({408, 429})

_MAX_BACKOFF_SECONDS = 8.0
_MAX_RETRY_AFTER_SECONDS = 30.0


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Best-effort parse of a numeric Retry-After header (uncapped seconds)."""
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _backoff_delay(base: float, attempt: int) -> float:
    """Exponential backoff for ``attempt`` (1-based), capped."""
    return min(base * (2 ** (attempt - 1)), _MAX_BACKOFF_SECONDS)


async def _post_with_retries(
    client: httpx.AsyncClient,
    url: str,
    json_body: dict[str, Any],
    headers: dict[str, str],
    *,
    max_attempts: int,
    backoff_seconds: float,
) -> httpx.Response:
    """POST once; retry a bounded number of times on transient failures.

    Retried: 408/429/5xx status responses (the server told us it did NOT
    succeed, so no double-billing risk) and TransportError. Connect errors
    never reached the server; read/write timeouts *may* have been processed
    upstream, but bounded retries match what the major LLM SDKs do by
    default. Any other failure — notably non-retryable 4xx — is re-raised
    on first occurrence.
    """
    last_error: Exception | None = None
    delay = 0.0
    for attempt in range(1, max_attempts + 1):
        try:
            resp = await client.post(url, json=json_body, headers=headers)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status not in _RETRYABLE_STATUSES and status < 500:
                raise
            retry_after = _retry_after_seconds(exc.response)
            if retry_after is not None and retry_after > _MAX_RETRY_AFTER_SECONDS:
                # Server asked for a longer wait than we will spend
                # in-request (e.g. a daily-cap 429) — surface the error now
                # rather than clamp-and-retry pointlessly.
                raise
            last_error = exc
            delay = (
                retry_after
                if retry_after is not None
                else _backoff_delay(backoff_seconds, attempt)
            )
        except httpx.TransportError as exc:
            last_error = exc
            delay = _backoff_delay(backoff_seconds, attempt)

        if attempt == max_attempts:
            assert last_error is not None  # set in every except branch above
            raise last_error
        logger.warning(
            "Upstream call failed (attempt %d/%d): %s — retrying in %.2fs",
            attempt,
            max_attempts,
            last_error,
            delay,
        )
        await sleep(delay)
    raise RuntimeError("retry loop exited without returning")  # pragma: no cover


# ---------------------------------------------------------------------------
# Circuit breaker (per-upstream fail-fast guard)
# ---------------------------------------------------------------------------


class CircuitOpenError(RuntimeError):
    """Raised with no network call made while a breaker is OPEN.

    chat.py maps this to an OpenAI-shaped 503 so callers see the same error
    contract as every other upstream failure.
    """


class CircuitBreaker:
    """CLOSED → OPEN → HALF_OPEN guard on consecutive upstream failures.

    A "failure" is a forward attempt that exhausted its retries and still
    ended in a retryable-class outcome (transport error, 408/429, 5xx) —
    the sustained-sickness signal that bounded retries alone never
    escalate. Any success resets the counter and CLOSES the breaker.

    While OPEN, callers fail fast with :class:`CircuitOpenError` until
    ``reset_seconds`` elapse; the next request then becomes a HALF_OPEN
    probe (single-flight). Probe success CLOSES the breaker; probe failure
    re-OPENs with a fresh cooldown. ``failure_threshold`` <= 0 disables the
    breaker entirely (opt-out for deployments that never want fail-fast).

    Single event loop, plain attributes: no locking needed beyond the
    probe flag, which only bounds concurrent probes (worst case a couple
    slip through — harmless, the state machine stays consistent).
    """

    def __init__(self, failure_threshold: int, reset_seconds: float) -> None:
        self.failure_threshold = failure_threshold
        self.reset_seconds = max(0.0, reset_seconds)
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False

    def _cooldown_elapsed(self) -> bool:
        assert self._opened_at is not None
        return time.monotonic() - self._opened_at >= self.reset_seconds

    @property
    def state(self) -> str:
        if self.failure_threshold <= 0:
            return "DISABLED"
        if self._opened_at is None:
            return "CLOSED"
        return "HALF_OPEN" if self._cooldown_elapsed() else "OPEN"

    def allow(self) -> bool:
        """Whether a request may go upstream right now."""
        if self.failure_threshold <= 0:
            return True
        if self._opened_at is None:
            return True
        if self._cooldown_elapsed() and not self._probe_in_flight:
            self._probe_in_flight = True
            return True
        return False

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None
        self._probe_in_flight = False

    def record_failure(self) -> None:
        if self.failure_threshold <= 0:
            return
        if self._opened_at is not None:
            # Failed HALF_OPEN probe — restart the cooldown.
            self._probe_in_flight = False
            self._opened_at = time.monotonic()
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._opened_at = time.monotonic()
            logger.warning(
                "Circuit OPEN for upstream after %d consecutive failures — "
                "failing fast for %.0fs",
                self._consecutive_failures,
                self.reset_seconds,
            )


# One breaker per allowlisted upstream base URL: a failure storm on one
# provider must not block callers whose keys point at another.
_breakers: dict[str, CircuitBreaker] = {}


def reset_circuit_breakers() -> None:
    """Drop all breaker state (settings changes, tests)."""
    _breakers.clear()


def _breaker_for(base_url: str) -> CircuitBreaker:
    breaker = _breakers.get(base_url)
    if breaker is None:
        cfg = get_settings()
        breaker = CircuitBreaker(
            cfg.llm_breaker_failure_threshold,
            cfg.llm_breaker_reset_seconds,
        )
        _breakers[base_url] = breaker
    return breaker


async def forward_to_llm(
    request_body: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> tuple[dict[str, Any], float]:
    """Forward a chat completion request to the configured LLM backend.

    BYOK (Phase 7): ``api_key`` is the CALLER's own key, used for the
    upstream Authorization header. When None the server's configured key is
    used — but chat.py refuses real (non-mock) traffic without a caller key
    before ever reaching here, so this fallback only serves mock/local flows.

    ``base_url`` — a pre-validated allowlisted upstream base URL chosen by
    the caller (see config.resolve_base_url); when None the configured
    LLM_API_BASE_URL is used.

    ``client`` — an existing AsyncClient to reuse. The FastAPI app passes
    its lifespan-managed shared client here (one connection pool for all
    requests); when None, a one-off client is created and closed for this
    call so direct/standalone callers keep working.

    Transient upstream failures are retried per ``_post_with_retries``;
    the returned latency therefore covers every attempt, matching what the
    caller actually waited. Note the coalescing lock holder may hold its
    slot across retries during an upstream flap — bounded by max attempts
    × backoff cap (~8s worst case at defaults).

    Returns (response_dict, latency_seconds).
    """
    cfg = get_settings()

    if cfg.mock_llm:
        return _mock_response(request_body), 0.02

    if not api_key:
        raise ValueError(
            "no API key supplied: BYOK mode requires the caller's "
            "Authorization: Bearer <key> for real upstream calls"
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    upstream = (base_url or cfg.llm_api_base_url).rstrip("/")
    url = f"{upstream}/chat/completions"
    max_attempts = max(1, cfg.llm_retry_max_attempts)
    backoff_seconds = max(0.0, cfg.llm_retry_backoff_seconds)

    breaker = _breaker_for(upstream)
    if not breaker.allow():
        raise CircuitOpenError(
            f"circuit OPEN for {upstream} — failing fast after repeated "
            "upstream failures; cooldown in progress"
        )

    start = time.perf_counter()
    try:
        if client is not None:
            resp = await _post_with_retries(
                client,
                url,
                request_body,
                headers,
                max_attempts=max_attempts,
                backoff_seconds=backoff_seconds,
            )
        else:
            async with httpx.AsyncClient(timeout=120.0) as own_client:
                resp = await _post_with_retries(
                    own_client,
                    url,
                    request_body,
                    headers,
                    max_attempts=max_attempts,
                    backoff_seconds=backoff_seconds,
                )
    except httpx.HTTPStatusError as exc:
        # Only retryable-class outcomes speak to upstream health. A 401
        # storm is the caller's problem, not the provider's — it must not
        # open the circuit for everyone else.
        if exc.response.status_code in _RETRYABLE_STATUSES or (
            exc.response.status_code >= 500
        ):
            breaker.record_failure()
        raise
    except httpx.TransportError:
        breaker.record_failure()
        raise
    breaker.record_success()
    return resp.json(), time.perf_counter() - start


def _mock_response(request_body: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic fake response for testing without an API key.

    The mock response echoes the last user message so a human can verify
    that the cached response matches what a real LLM would have returned.
    """
    messages = request_body.get("messages", [])
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = m.get("content", "")
            break

    model = request_body.get("model", "mock-model")
    mock_content = f'[MOCK RESPONSE for model="{model}"]\nYou asked: "{last_user}"'

    return {
        "id": f"chatcmpl-mock-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": mock_content,
                },
                "finish_reason": "stop",
                "logprobs": None,
            }
        ],
        "usage": {
            "prompt_tokens": _rough_token_count(last_user),
            "completion_tokens": _rough_token_count(mock_content),
            "total_tokens": _rough_token_count(last_user)
            + _rough_token_count(mock_content),
        },
    }


def _rough_token_count(text: str) -> int:
    """Quick-and-dirty token estimator (~4 chars per token)."""
    return max(1, len(text) // 4)

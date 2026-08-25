"""Forward requests to the real LLM API, or return mock responses.

Upstream resilience: transient failures (HTTP 408/429/5xx and transport
errors) are retried with exponential backoff, bounded by
LLM_RETRY_MAX_ATTEMPTS / LLM_RETRY_BACKOFF_SECONDS. A numeric Retry-After
header from the server overrides computed backoff (capped at 30 s).
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
    """Best-effort parse of a numeric Retry-After header, capped."""
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return min(max(0.0, float(raw)), _MAX_RETRY_AFTER_SECONDS)
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
            last_error = exc
            retry_after = _retry_after_seconds(exc.response)
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
    url = f"{(base_url or cfg.llm_api_base_url).rstrip('/')}/chat/completions"
    max_attempts = max(1, cfg.llm_retry_max_attempts)
    backoff_seconds = max(0.0, cfg.llm_retry_backoff_seconds)

    start = time.perf_counter()
    if client is not None:
        resp = await _post_with_retries(
            client,
            url,
            request_body,
            headers,
            max_attempts=max_attempts,
            backoff_seconds=backoff_seconds,
        )
        return resp.json(), time.perf_counter() - start

    async with httpx.AsyncClient(timeout=120.0) as own_client:
        resp = await _post_with_retries(
            own_client,
            url,
            request_body,
            headers,
            max_attempts=max_attempts,
            backoff_seconds=backoff_seconds,
        )
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

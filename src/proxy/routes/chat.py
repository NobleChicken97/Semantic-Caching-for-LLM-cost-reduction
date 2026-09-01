"""POST /v1/chat/completions — the core proxy endpoint.

Phase 2: Two-tier cache — exact match first, then semantic cosine-similarity fallback.
Phase 4+: per-prompt request coalescing (cache-stampede protection).
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..cache import _hash_prompt, log_request, lookup, store
from ..config import get_settings, resolve_base_url
from ..llm_client import forward_to_llm
from ..models import (
    CacheMetadata,
    ChatCompletionRequest,
    ChatCompletionResponse,
)
from ..security import LOCAL_USER_ID, derive_user_id

router = APIRouter()

logger = logging.getLogger("proxy")

# Cache-stampede protection: at most one in-flight upstream call per exact
# (prompt, model) hash. SCOPE LIMITATION: this guards a single process only.
# Multi-worker (uvicorn --workers N) or multi-instance deployments need a
# distributed lock (e.g. Redis SETNX) instead — deliberately out of scope here.
_inflight_locks: dict[str, asyncio.Lock] = {}


def _resolve_upstream_base(body: ChatCompletionRequest, request: Request) -> str | None:
    """Resolve the caller-selected upstream, validated against the allowlist.

    Precedence: X-LLM-Base-URL header (provider name or exact allowlisted
    URL) > ``provider`` body field > None (→ server's configured default).
    Raises ValueError for anything not on the allowlist — chat_completions
    turns that into a clean 400 before any network activity.
    """
    header_val = request.headers.get("X-LLM-Base-URL")
    if header_val:
        return resolve_base_url(header_val)
    if body.provider:
        return resolve_base_url(body.provider)
    return None


def _extract_caller_key(request: Request) -> str | None:
    """Return the caller's own API key from ``Authorization: Bearer <key>``.

    None when the header is absent, malformed, or carries an empty token.
    The raw key is never logged or persisted — it is forwarded upstream and,
    separately, reduced to an HMAC-derived user_id for cache scoping.
    """
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[len("Bearer ") :].strip()
        return token or None
    return None


def _missing_key_response() -> JSONResponse:
    """401 for real traffic arriving without a caller-supplied key."""
    return JSONResponse(
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
        content={
            "error": {
                "message": (
                    "This proxy runs in bring-your-own-key mode: send your own "
                    "provider key via 'Authorization: Bearer <your-key>'. The "
                    "server never substitutes its own key for real traffic."
                ),
                "type": "invalid_request_error",
                "code": 401,
            }
        },
    )


def _shared_client(request: Request) -> httpx.AsyncClient | None:
    """Return the lifespan-managed upstream client, if one exists.

    Falls back to None outside a running lifespan (direct/test calls),
    in which case forward_to_llm creates a one-off client itself.
    """
    return getattr(request.app.state, "http_client", None)


def _upstream_error_detail(exc: httpx.HTTPStatusError) -> str | None:
    """Best-effort extraction of the upstream error message, if any.

    Handles both OpenAI-style ``{"error": {"message": ...}}`` bodies and
    Google's list-wrapped ``[{"error": {...}}]`` REST shape. Returns None
    for anything unparseable — the generic message still goes out.
    """
    try:
        data = exc.response.json()
    except Exception:  # noqa: BLE001 - any decode failure means "no detail"
        return None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        data = data[0]
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            msg = err.get("message")
            if msg:
                return str(msg)[:300]
    return None


def _upstream_error_response(exc: httpx.HTTPError) -> JSONResponse:
    """Map an upstream httpx failure to an OpenAI-shaped error payload.

    HTTPStatusError → pass through the upstream status code (plus the
    upstream's own message when we can read it — e.g. Gemini's "high
    demand" 503s or unknown-field 400s);
    any other request failure (timeout, connect/reset) → 502 Bad Gateway.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        etype = "upstream_api_error"
        message = f"Upstream LLM API returned HTTP {status}"
        detail = _upstream_error_detail(exc)
        if detail:
            message += f": {detail}"
    else:
        status = 502
        etype = "upstream_connection_error"
        message = "Could not reach the upstream LLM API"
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": etype, "code": status}},
    )


def _log_failed_request(prompt_text: str, latency_ms: float, user_id: str) -> None:
    """Record a failed upstream call.

    Outcome 'ERROR' rows carry no fabricated cost or token counts.
    """
    log_request(
        prompt_text=prompt_text,
        outcome="ERROR",
        latency_ms=latency_ms,
        matched_entry_id=None,
        similarity_score=None,
        estimated_cost_usd=0.0,
        tokens_in=0,
        tokens_out=0,
        user_id=user_id,
    )


@router.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(body: ChatCompletionRequest, request: Request):
    prompt = body.canonical_prompt()
    bypass = request.headers.get("X-Cache-Bypass", "false").strip().lower() == "true"

    # --- Caller-selected upstream (allowlist-enforced) ---
    try:
        base_url = _resolve_upstream_base(body, request)
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": str(exc),
                    "type": "invalid_request_error",
                    "code": 400,
                }
            },
        )
    # `provider` is a proxy-side routing hint — never forwarded upstream.
    # exclude_unset: forward EXACTLY what the caller sent. Injecting Pydantic
    # defaults made every call carry temperature/top_p/n/stream/penalties,
    # which stricter OpenAI-compat layers reject outright (Gemini's endpoint
    # 400s on unknown frequency_penalty — found live in Phase B testing).
    payload = body.model_dump(
        exclude_none=True, exclude_unset=True, exclude={"provider"}
    )

    # --- BYOK gate + identity ---
    # Real (non-mock) traffic must carry the caller's own key. The key itself
    # is forwarded upstream and immediately reduced to a non-reversible
    # user_id for cache/log scoping — it is never stored or logged raw.
    caller_key = _extract_caller_key(request)
    if not get_settings().mock_llm and caller_key is None:
        logger.warning("Rejected keyless request in BYOK (non-mock) mode.")
        return _missing_key_response()
    user_id = derive_user_id(caller_key) if caller_key else LOCAL_USER_ID

    # --- Bypass path ---
    if bypass:
        t0 = time.perf_counter()
        try:
            raw_resp, _ = await forward_to_llm(
                payload,
                client=_shared_client(request),
                api_key=caller_key,
                base_url=base_url,
            )
        except httpx.HTTPError as exc:  # HTTPStatusError + RequestError base
            elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.error("Upstream LLM call failed on BYPASS: %s", exc)
            _log_failed_request(prompt, elapsed_ms, user_id)
            return _upstream_error_response(exc)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        raw_resp["cache_metadata"] = CacheMetadata(
            outcome="BYPASS", similarity_score=None
        ).model_dump()
        log_request(
            prompt_text=prompt,
            outcome="BYPASS",
            latency_ms=elapsed_ms,
            tokens_in=raw_resp.get("usage", {}).get("prompt_tokens", 0),
            tokens_out=raw_resp.get("usage", {}).get("completion_tokens", 0),
            user_id=user_id,
        )
        return raw_resp

    # --- Two-tier cache lookup (scoped to the requested model) ---
    t0 = time.perf_counter()
    cached = lookup(prompt, model=body.model, user_id=user_id)

    # --- On a miss, coalesce concurrent identical requests before paying
    #     for an upstream call (single-flight per prompt hash). ---
    if cached is None:
        key = _hash_prompt(prompt)
        lock = _inflight_locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                # Re-check under the lock: another coroutine may have
                # forwarded and stored this exact prompt while we waited.
                cached = lookup(prompt, model=body.model, user_id=user_id)
                if cached is None:
                    try:
                        raw_resp, _ = await forward_to_llm(
                            payload,
                            client=_shared_client(request),
                            api_key=caller_key,
                            base_url=base_url,
                        )
                    except httpx.HTTPError as exc:
                        elapsed_ms = (time.perf_counter() - t0) * 1000
                        logger.error("Upstream LLM call failed on MISS: %s", exc)
                        _log_failed_request(prompt, elapsed_ms, user_id)
                        return _upstream_error_response(exc)
                    elapsed_ms = (time.perf_counter() - t0) * 1000

                    # Store in cache (with embedding now)
                    entry_id = store(prompt, raw_resp, body.model, user_id=user_id)

                    raw_resp["cache_metadata"] = CacheMetadata(
                        outcome="MISS", similarity_score=None
                    ).model_dump()

                    cost = _estimate_cost(raw_resp)
                    log_request(
                        prompt_text=prompt,
                        outcome="MISS",
                        latency_ms=elapsed_ms,
                        matched_entry_id=entry_id,
                        similarity_score=None,
                        estimated_cost_usd=cost,
                        tokens_in=raw_resp.get("usage", {}).get("prompt_tokens", 0),
                        tokens_out=raw_resp.get("usage", {}).get(
                            "completion_tokens", 0
                        ),
                        user_id=user_id,
                    )
                    return raw_resp
        finally:
            # Pop the entry so the dict can't grow without bound. Safe even
            # while other waiters still hold this lock object: they finish
            # acquiring/releasing it normally; new arrivals either create a
            # fresh lock or — far more likely — find the freshly stored
            # cache entry on their own first lookup.
            if _inflight_locks.get(key) is lock:
                del _inflight_locks[key]

    # --- Served from cache: first-pass HIT, or a MISS that another
    #     coalesced request just filled while we waited on the lock. ---
    elapsed_ms = (time.perf_counter() - t0) * 1000
    score = cached.get("similarity_score", 1.0)
    cached["response"]["cache_metadata"] = CacheMetadata(
        outcome="HIT", similarity_score=round(score, 6)
    ).model_dump()

    log_request(
        prompt_text=prompt,
        outcome="HIT",
        latency_ms=elapsed_ms,
        matched_entry_id=cached["entry_id"],
        similarity_score=score,
        tokens_in=cached["response"].get("usage", {}).get("prompt_tokens", 0),
        tokens_out=cached["response"].get("usage", {}).get("completion_tokens", 0),
        estimated_cost_usd=_estimate_cost(cached["response"]),
        user_id=user_id,
    )
    return cached["response"]


def _estimate_cost(response_dict: dict) -> float:
    """Model-aware cost estimate (USD per 1M in/out tokens).

    Pricing table comes from config (DEFAULT_MODEL_PRICING + MODEL_PRICING
    env override). Unknown models — the common case for free-tier BYOK
    traffic — estimate at $0.00 rather than reporting a made-up number.
    """
    from ..config import get_settings

    model = response_dict.get("model", "")
    pricing = get_settings().model_pricing

    rates = pricing.get(model)
    if rates is None:
        # Longest-prefix match so dated snapshots ("gpt-4o-mini-2024-07-18")
        # inherit their family's pricing.
        candidates = [k for k in pricing if model.startswith(k)]
        if candidates:
            rates = pricing[max(candidates, key=len)]
        else:
            return 0.0

    usage = response_dict.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    cost = (prompt_tokens / 1_000_000) * rates[0] + (
        completion_tokens / 1_000_000
    ) * rates[1]
    return round(cost, 8)

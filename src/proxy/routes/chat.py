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
from ..llm_client import forward_to_llm
from ..models import (
    CacheMetadata,
    ChatCompletionRequest,
    ChatCompletionResponse,
)

router = APIRouter()

logger = logging.getLogger("proxy")

# Cache-stampede protection: at most one in-flight upstream call per exact
# (prompt, model) hash. SCOPE LIMITATION: this guards a single process only.
# Multi-worker (uvicorn --workers N) or multi-instance deployments need a
# distributed lock (e.g. Redis SETNX) instead — deliberately out of scope here.
_inflight_locks: dict[str, asyncio.Lock] = {}


def _shared_client(request: Request) -> httpx.AsyncClient | None:
    """Return the lifespan-managed upstream client, if one exists.

    Falls back to None outside a running lifespan (direct/test calls),
    in which case forward_to_llm creates a one-off client itself.
    """
    return getattr(request.app.state, "http_client", None)


def _upstream_error_response(exc: httpx.HTTPError) -> JSONResponse:
    """Map an upstream httpx failure to an OpenAI-shaped error payload.

    HTTPStatusError → pass through the upstream status code;
    any other request failure (timeout, connect/reset) → 502 Bad Gateway.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        etype = "upstream_api_error"
        message = f"Upstream LLM API returned HTTP {status}"
    else:
        status = 502
        etype = "upstream_connection_error"
        message = "Could not reach the upstream LLM API"
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": etype, "code": status}},
    )


def _log_failed_request(prompt_text: str, latency_ms: float) -> None:
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
    )


@router.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(body: ChatCompletionRequest, request: Request):
    prompt = body.canonical_prompt()
    bypass = request.headers.get("X-Cache-Bypass", "false").strip().lower() == "true"

    # --- Bypass path ---
    if bypass:
        t0 = time.perf_counter()
        try:
            raw_resp, _ = await forward_to_llm(
                body.model_dump(exclude_none=True),
                client=_shared_client(request),
            )
        except httpx.HTTPError as exc:  # HTTPStatusError + RequestError base
            elapsed_ms = (time.perf_counter() - t0) * 1000
            logger.error("Upstream LLM call failed on BYPASS: %s", exc)
            _log_failed_request(prompt, elapsed_ms)
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
        )
        return raw_resp

    # --- Two-tier cache lookup (scoped to the requested model) ---
    t0 = time.perf_counter()
    cached = lookup(prompt, model=body.model)

    # --- On a miss, coalesce concurrent identical requests before paying
    #     for an upstream call (single-flight per prompt hash). ---
    if cached is None:
        key = _hash_prompt(prompt)
        lock = _inflight_locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                # Re-check under the lock: another coroutine may have
                # forwarded and stored this exact prompt while we waited.
                cached = lookup(prompt, model=body.model)
                if cached is None:
                    try:
                        raw_resp, _ = await forward_to_llm(
                            body.model_dump(exclude_none=True),
                            client=_shared_client(request),
                        )
                    except httpx.HTTPError as exc:
                        elapsed_ms = (time.perf_counter() - t0) * 1000
                        logger.error("Upstream LLM call failed on MISS: %s", exc)
                        _log_failed_request(prompt, elapsed_ms)
                        return _upstream_error_response(exc)
                    elapsed_ms = (time.perf_counter() - t0) * 1000

                    # Store in cache (with embedding now)
                    entry_id = store(prompt, raw_resp, body.model)

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
                        tokens_out=raw_resp.get("usage", {}).get("completion_tokens", 0),
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
    )
    return cached["response"]


def _estimate_cost(response_dict: dict) -> float:
    """Rough cost estimate based on token counts.

    Uses gpt-3.5-turbo pricing as default; in a real deployment you'd
    key off the model name for accurate pricing.
    """
    usage = response_dict.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    # gpt-3.5-turbo: $0.50/1M input, $1.50/1M output
    cost = (prompt_tokens / 1_000_000) * 0.50 + (completion_tokens / 1_000_000) * 1.50
    return round(cost, 8)
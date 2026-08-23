"""POST /v1/chat/completions — the core proxy endpoint.

Phase 2: Two-tier cache — exact match first, then semantic cosine-similarity fallback.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Request

from ..cache import log_request, lookup, store
from ..config import settings
from ..llm_client import forward_to_llm
from ..models import (
    CacheMetadata,
    ChatCompletionRequest,
    ChatCompletionResponse,
)

router = APIRouter()


@router.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(body: ChatCompletionRequest, request: Request):
    prompt = body.canonical_prompt()
    bypass = request.headers.get("X-Cache-Bypass", "false").strip().lower() == "true"

    # --- Bypass path ---
    if bypass:
        t0 = time.perf_counter()
        raw_resp, _ = await forward_to_llm(body.model_dump(exclude_none=True))
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

    # --- Two-tier cache lookup ---
    cached = lookup(prompt)
    if cached is not None:
        score = cached.get("similarity_score", 1.0)
        cached["response"]["cache_metadata"] = CacheMetadata(
            outcome="HIT", similarity_score=round(score, 6)
        ).model_dump()

        log_request(
            prompt_text=prompt,
            outcome="HIT",
            latency_ms=0.0,
            matched_entry_id=cached["entry_id"],
            similarity_score=score,
            tokens_in=cached["response"].get("usage", {}).get("prompt_tokens", 0),
            tokens_out=cached["response"].get("usage", {}).get("completion_tokens", 0),
            estimated_cost_usd=_estimate_cost(cached["response"]),
        )
        return cached["response"]

    # --- Cache miss: forward to LLM ---
    t0 = time.perf_counter()
    raw_resp, _ = await forward_to_llm(body.model_dump(exclude_none=True))
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
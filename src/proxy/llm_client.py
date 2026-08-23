"""Forward requests to the real LLM API, or return mock responses."""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from .config import get_settings


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

    start = time.perf_counter()
    if client is not None:
        resp = await client.post(url, json=request_body, headers=headers)
        resp.raise_for_status()
        return resp.json(), time.perf_counter() - start

    async with httpx.AsyncClient(timeout=120.0) as own_client:
        resp = await own_client.post(url, json=request_body, headers=headers)
        resp.raise_for_status()
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
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
) -> tuple[dict[str, Any], float]:
    """Forward a chat completion request to the configured LLM backend.

    ``client`` — an existing AsyncClient to reuse. The FastAPI app passes
    its lifespan-managed shared client here (one connection pool for all
    requests); when None, a one-off client is created and closed for this
    call so direct/standalone callers keep working.

    Returns (response_dict, latency_seconds).
    """
    cfg = get_settings()

    if cfg.mock_llm:
        return _mock_response(request_body), 0.02

    headers = {
        "Authorization": f"Bearer {cfg.llm_api_key}",
        "Content-Type": "application/json",
    }
    url = f"{cfg.llm_api_base_url}/chat/completions"

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
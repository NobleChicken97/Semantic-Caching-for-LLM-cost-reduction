"""Pydantic models mirroring the OpenAI chat completions API shape.

OpenAI reference: https://platform.openai.com/docs/api-reference/chat/create
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str  # "system" | "user" | "assistant"
    content: str
    name: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    temperature: Optional[float] = Field(default=1.0, ge=0, le=2)
    max_tokens: Optional[int] = None
    top_p: Optional[float] = Field(default=1.0, ge=0, le=1)
    n: Optional[int] = 1
    stream: Optional[bool] = False
    stop: Optional[List[str]] = None
    presence_penalty: Optional[float] = Field(default=0, ge=-2, le=2)
    frequency_penalty: Optional[float] = Field(default=0, ge=-2, le=2)
    user: Optional[str] = None

    def canonical_prompt(self) -> str:
        """Return a stable, hashable representation of the prompt.

        Only the message content and roles matter for caching.
        Model params like temperature do not affect the prompt hash.
        """
        parts = [f"[{m.role}]{m.content}" for m in self.messages]
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Response models (mirror OpenAI shape)
# ---------------------------------------------------------------------------

class UsageInfo(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatMessageResponse(BaseModel):
    role: str = "assistant"
    content: str
    refusal: Optional[str] = None


class Choice(BaseModel):
    index: int = 0
    message: ChatMessageResponse
    finish_reason: Optional[str] = "stop"
    logprobs: Optional[Any] = None


class CacheMetadata(BaseModel):
    outcome: str  # "HIT" | "MISS" | "BYPASS"
    similarity_score: Optional[float] = None


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[Choice]
    usage: Optional[UsageInfo] = None
    cache_metadata: Optional[CacheMetadata] = None


# ---------------------------------------------------------------------------
# Metrics / purge models
# ---------------------------------------------------------------------------

class MetricsResponse(BaseModel):
    hit_rate: float
    total_requests: int
    estimated_cost_saved_usd: float
    avg_latency_hit_ms: Optional[float] = None
    avg_latency_miss_ms: Optional[float] = None


class PurgeRequest(BaseModel):
    entry_id: Optional[int] = None


class PurgeResponse(BaseModel):
    purged_count: int


class ThresholdSweepRequest(BaseModel):
    thresholds: List[float]


class ThresholdResult(BaseModel):
    threshold: float
    precision: float
    recall: float
    f1: float


class ThresholdSweepResponse(BaseModel):
    results: List[ThresholdResult]


# ---------------------------------------------------------------------------
# Dashboard models (Phase 5)
# ---------------------------------------------------------------------------

class CacheEntryOut(BaseModel):
    entry_id: int
    prompt_text: str
    model_used: str
    created_at: float
    expires_at: float
    hit_count: int
    last_hit_at: Optional[float] = None


class CacheEntriesResponse(BaseModel):
    entries: List[CacheEntryOut]


class LogEntryOut(BaseModel):
    log_id: int
    timestamp: float
    prompt_text: str
    outcome: str  # "HIT" | "MISS" | "BYPASS"
    matched_entry_id: Optional[int] = None
    similarity_score: Optional[float] = None
    latency_ms: float
    estimated_cost_usd: float
    tokens_in: int
    tokens_out: int


class LogsResponse(BaseModel):
    logs: List[LogEntryOut]
"""Pydantic models mirroring the OpenAI chat completions API shape.

OpenAI reference: https://platform.openai.com/docs/api-reference/chat/create
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: str  # "system" | "user" | "assistant"
    content: str
    name: str | None = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float | None = Field(default=1.0, ge=0, le=2)
    max_tokens: int | None = None
    top_p: float | None = Field(default=1.0, ge=0, le=1)
    n: int | None = 1
    stream: bool | None = False
    stop: list[str] | None = None
    presence_penalty: float | None = Field(default=0, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=0, ge=-2, le=2)
    user: str | None = None
    # Phase 7 BYOK extension (not part of the OpenAI schema): selects an
    # allowlisted provider by name. Never forwarded upstream — chat.py
    # excludes this field from the payload sent to the LLM API.
    provider: str | None = None

    def canonical_prompt(self) -> str:
        """Return a stable, hashable representation of the prompt.

        The model name is part of the cache identity: identical messages
        asked of different models must never collide, otherwise a gpt-4
        request could be served a gpt-3.5-turbo response whose body lies
        about which model produced it. Sampling params like temperature
        still do not affect the prompt hash.
        """
        parts = [f"[{m.role}]{m.content}" for m in self.messages]
        return f"[model]{self.model}\n" + "\n".join(parts)

    def embedding_text(self) -> str:
        """Message-only canonical text: the embedding input (Phase 9).

        Identical join to canonical_prompt() MINUS the ``[model]`` line. A
        constant model prefix dominates short user text in embedding space
        and compresses apparent distances between unrelated prompts
        (measured live: recall 1.0 / precision ~0.45 vs the documented
        R=0.9375/P=0.7895 tuned on raw strings). Model identity stays in
        the hash (canonical_prompt) and the ``model_used`` column/filter —
        the embedding never needed it, and the eval distribution this
        threshold was tuned on never had it.
        """
        return "\n".join(f"[{m.role}]{m.content}" for m in self.messages)


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
    refusal: str | None = None


class Choice(BaseModel):
    index: int = 0
    message: ChatMessageResponse
    finish_reason: str | None = "stop"
    logprobs: Any | None = None


class CacheMetadata(BaseModel):
    outcome: str  # "HIT" | "MISS" | "BYPASS"
    similarity_score: float | None = None


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    usage: UsageInfo | None = None
    cache_metadata: CacheMetadata | None = None


# ---------------------------------------------------------------------------
# Metrics / purge models
# ---------------------------------------------------------------------------


class UserUsage(BaseModel):
    """Per-user aggregate (Phase 7): one row per derived user_id."""

    user_id: str
    total_requests: int
    hits: int
    tokens_saved: int
    cost_saved_usd: float = 0.0


class MetricsResponse(BaseModel):
    hit_rate: float
    total_requests: int
    estimated_cost_saved_usd: float
    avg_latency_hit_ms: float | None = None
    avg_latency_miss_ms: float | None = None
    # Phase 7 headline metric + per-user breakdown.
    total_tokens_saved: int = 0
    per_user: list[UserUsage] = []
    # Phase 9.6: newest purge-audit row (None when never purged).
    last_purge: PurgeAudit | None = None


class PurgeRequest(BaseModel):
    entry_id: int | None = None


class PurgeResponse(BaseModel):
    purged_count: int


class PurgeAudit(BaseModel):
    """One recorded purge action (Phase 9.6): who, when, what scope."""

    timestamp: float
    purged_count: int = 0
    entry_id: int | None = None
    actor: str = "admin"


class ThresholdSweepRequest(BaseModel):
    thresholds: list[float]


class ThresholdResult(BaseModel):
    threshold: float
    precision: float
    recall: float
    f1: float


class ThresholdSweepResponse(BaseModel):
    results: list[ThresholdResult]


class AutoTuneRequest(BaseModel):
    """Omit ``thresholds`` to sweep the documented default grid."""

    thresholds: list[float] | None = None


class BorderlinePair(BaseModel):
    """A labeled pair sitting near the chosen threshold — the evidence
    behind the auto-tune pick (see BORDERLINE_BAND in eval.py)."""

    prompt_a: str
    prompt_b: str
    similarity: float
    should_match: bool


class AutoTuneResponse(BaseModel):
    best_threshold: float | None = None
    best_f1: float | None = None
    results: list[ThresholdResult] = []
    borderline: list[BorderlinePair] = []


# ---------------------------------------------------------------------------
# Dashboard models (Phase 5)
# ---------------------------------------------------------------------------


class CacheEntryOut(BaseModel):
    entry_id: int
    prompt_text: str
    model_used: str
    user_id: str
    created_at: float
    expires_at: float
    hit_count: int
    last_hit_at: float | None = None


class CacheEntriesResponse(BaseModel):
    entries: list[CacheEntryOut]


class LogEntryOut(BaseModel):
    log_id: int
    timestamp: float
    prompt_text: str
    outcome: str  # "HIT" | "MISS" | "BYPASS" | "ERROR"
    user_id: str
    matched_entry_id: int | None = None
    similarity_score: float | None = None
    latency_ms: float
    estimated_cost_usd: float
    tokens_in: int
    tokens_out: int


class LogsResponse(BaseModel):
    logs: list[LogEntryOut]

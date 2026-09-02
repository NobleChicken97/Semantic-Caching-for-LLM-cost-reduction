"""Central configuration loaded from environment variables.

Settings are read at the first ``get_settings()`` call and cached with
``lru_cache`` — NOT evaluated at module import time. Tests re-read the
environment via ``get_settings.cache_clear()`` after monkeypatching env vars.
Call sites must fetch ``get_settings()`` at point of use rather than holding
a module-level reference, so cleared caches are always picked up.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    # --- LLM backend ---
    llm_api_base_url: str
    llm_api_key: str

    # --- Mock mode (no real API key needed) ---
    mock_llm: bool

    # --- Cache ---
    cache_db_path: str
    cache_default_ttl_seconds: int  # 1 hour default

    # --- Similarity ---
    similarity_threshold: float

    # --- Semantic scan guardrail ---
    max_semantic_scan_entries: int

    # --- Upstream resilience ---
    # Transient upstream failures (HTTP 408/429/5xx, connection/transport
    # errors) are retried with exponential backoff; a numeric Retry-After
    # header wins over computed backoff when present.
    llm_retry_max_attempts: int  # total attempts including the first (1 = off)
    llm_retry_backoff_seconds: float

    # --- Circuit breaker (per upstream base URL) ---
    # Opens after this many CONSECUTIVE exhausted-failure forwards (transport
    # errors, 408/429, 5xx) and fails fast for reset_seconds; 0 disables.
    llm_breaker_failure_threshold: int
    llm_breaker_reset_seconds: float

    # --- Model pricing (USD per 1M input/output tokens) ---
    # Exact-name matches win; unknown models price at $0.00 (free-tier safe).
    model_pricing: dict[str, tuple[float, float]]

    # --- Admin ---
    # Empty string = admin endpoints unauthenticated (demo mode).
    admin_token: str

    # --- BYOK identity ---
    # HMAC key material for deriving user_ids from caller API keys.
    # Generate once (python -c "import secrets; print(secrets.token_hex(32))"),
    # keep out of git, never rotate — rotating orphans all scoped history.
    user_id_pepper: str

    # --- Server ---
    host: str
    port: int


# ---------------------------------------------------------------------------
# BYOK providers (Phase 7)
# ---------------------------------------------------------------------------
# The ONLY upstreams a caller may select via the X-LLM-Base-URL header or a
# `provider` request field. Anything else is rejected with a 400 before any
# network call happens — accepting arbitrary caller URLs would turn the proxy
# into an open relay and open an SSRF-shaped hole.
PROVIDER_BASE_URLS: dict[str, str] = {
    "openrouter": "https://openrouter.ai/api/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
}


def resolve_base_url(provider_or_url: str | None) -> str:
    """Validate a caller-supplied provider selection against the allowlist.

    Accepts either a provider key ("openrouter" / "gemini") or an exact
    allowlisted base URL (trailing slash tolerated). Returns the canonical
    base URL without a trailing slash, ready for "/chat/completions" to be
    appended. Raises ValueError for anything that isn't allowlisted.
    """
    if not provider_or_url or not provider_or_url.strip():
        raise ValueError("provider/base URL must be a non-empty string")
    candidate = provider_or_url.strip().rstrip("/")
    if candidate in PROVIDER_BASE_URLS:
        return PROVIDER_BASE_URLS[candidate]
    by_url = {url.rstrip("/"): url for url in PROVIDER_BASE_URLS.values()}
    if candidate in by_url:
        return candidate
    known = ", ".join(sorted(PROVIDER_BASE_URLS))
    raise ValueError(
        f"provider/base URL {provider_or_url!r} is not allowlisted "
        f"(allowed: {known}, or their exact base URLs)"
    )


# Baseline pricing so the legacy gpt-3.5-turbo estimate keeps working out of
# the box; extend/override via MODEL_PRICING, e.g.:
#   MODEL_PRICING="gpt-4o-mini=0.15,0.60;gemini-2.0-flash=0.10,0.40"
DEFAULT_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gpt-3.5-turbo": (0.50, 1.50),
}


def _parse_model_pricing(raw: str | None) -> dict[str, tuple[float, float]]:
    """Parse MODEL_PRICING entries shaped ``name=in_per_1M,out_per_1M``.

    Entries are separated by ``;``. Invalid entries are skipped with a
    printed warning rather than breaking startup.
    """
    table = dict(DEFAULT_MODEL_PRICING)
    if not raw:
        return table
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        try:
            name, prices = entry.split("=", 1)
            in_price, out_price = (p.strip() for p in prices.split(",", 1))
            table[name.strip()] = (float(in_price), float(out_price))
        except ValueError:
            print(f"[config] ignoring malformed MODEL_PRICING entry: {entry!r}")
    return table


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build :class:`Settings` from the current environment (cached).

    Use ``get_settings.cache_clear()`` to invalidate — e.g. in tests after
    ``monkeypatch.setenv(...)``.
    """
    return Settings(
        llm_api_base_url=os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1"),
        llm_api_key=os.getenv("LLM_API_KEY", "sk-placeholder"),
        mock_llm=os.getenv("MOCK_LLM", "false").strip().lower() == "true",
        cache_db_path=os.getenv("CACHE_DB_PATH", "cache.db"),
        cache_default_ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "3600")),
        similarity_threshold=float(os.getenv("SIMILARITY_THRESHOLD", "0.85")),
        max_semantic_scan_entries=int(os.getenv("MAX_SEMANTIC_SCAN_ENTRIES", "5000")),
        llm_retry_max_attempts=int(os.getenv("LLM_RETRY_MAX_ATTEMPTS", "3")),
        llm_retry_backoff_seconds=float(os.getenv("LLM_RETRY_BACKOFF_SECONDS", "0.5")),
        llm_breaker_failure_threshold=int(
            os.getenv("LLM_BREAKER_FAILURE_THRESHOLD", "5")
        ),
        llm_breaker_reset_seconds=float(os.getenv("LLM_BREAKER_RESET_SECONDS", "30")),
        model_pricing=_parse_model_pricing(os.getenv("MODEL_PRICING")),
        admin_token=os.getenv("ADMIN_TOKEN", ""),
        user_id_pepper=os.getenv("USER_ID_PEPPER", ""),
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
    )

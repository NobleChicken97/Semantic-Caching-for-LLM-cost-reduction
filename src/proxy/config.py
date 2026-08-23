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
    llm_model: str

    # --- Mock mode (no real API key needed) ---
    mock_llm: bool

    # --- Cache ---
    cache_db_path: str
    cache_default_ttl_seconds: int  # 1 hour default

    # --- Similarity ---
    similarity_threshold: float

    # --- Semantic scan guardrail ---
    max_semantic_scan_entries: int

    # --- Admin ---
    # Empty string = admin endpoints unauthenticated (demo mode).
    admin_token: str

    # --- Server ---
    host: str
    port: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build :class:`Settings` from the current environment (cached).

    Use ``get_settings.cache_clear()`` to invalidate — e.g. in tests after
    ``monkeypatch.setenv(...)``.
    """
    return Settings(
        llm_api_base_url=os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1"),
        llm_api_key=os.getenv("LLM_API_KEY", "sk-placeholder"),
        llm_model=os.getenv("LLM_MODEL", "gpt-3.5-turbo"),
        mock_llm=os.getenv("MOCK_LLM", "false").strip().lower() == "true",
        cache_db_path=os.getenv("CACHE_DB_PATH", "cache.db"),
        cache_default_ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "3600")),
        similarity_threshold=float(os.getenv("SIMILARITY_THRESHOLD", "0.85")),
        max_semantic_scan_entries=int(os.getenv("MAX_SEMANTIC_SCAN_ENTRIES", "5000")),
        admin_token=os.getenv("ADMIN_TOKEN", ""),
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
    )

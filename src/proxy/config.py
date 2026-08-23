"""Central configuration loaded from environment variables."""

import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    # --- LLM backend ---
    llm_api_base_url: str = os.getenv(
        "LLM_API_BASE_URL", "https://api.openai.com/v1"
    )
    llm_api_key: str = os.getenv("LLM_API_KEY", "sk-placeholder")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-3.5-turbo")

    # --- Mock mode (no real API key needed) ---
    mock_llm: bool = os.getenv("MOCK_LLM", "false").strip().lower() == "true"

    # --- Cache ---
    cache_db_path: str = os.getenv("CACHE_DB_PATH", "cache.db")
    cache_default_ttl_seconds: int = int(
        os.getenv("CACHE_TTL_SECONDS", "3600")  # 1 hour default
    )

    # --- Similarity (Phase 2+; hardcoded for Phase 1) ---
    similarity_threshold: float = float(
        os.getenv("SIMILARITY_THRESHOLD", "0.85")
    )

    # --- Server ---
    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "8000"))


settings = Settings()
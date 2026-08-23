"""FastAPI proxy entry point — Phase 2: semantic caching with BGE embeddings."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

from pathlib import Path

from fastapi.responses import FileResponse

from .cache import get_metrics, list_cache_entries, purge, recent_logs
from .config import settings
from .database import init_db, seed_test_pairs
from .eval import run_threshold_sweep
from .models import (
    CacheEntriesResponse,
    LogsResponse,
    MetricsResponse,
    PurgeRequest,
    PurgeResponse,
    ThresholdSweepRequest,
    ThresholdSweepResponse,
)
from .routes.chat import router as chat_router

STATIC_DIR = Path(__file__).resolve().parent / "static"

logger = logging.getLogger("proxy")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB, seed test data, warm the embedding model."""
    init_db()
    seed_test_pairs()
    logger.info("Database initialized.")

    # Preload the embedding model so the first request isn't slow
    from .embedding import embed_texts

    try:
        _ = embed_texts(["warmup hello world"])
        logger.info("Embedding model loaded (BAAI/bge-small-en-v1.5).")
    except Exception:
        logger.exception("Failed to warm embedding model — will retry on first request.")

    yield  # app runs here

    logger.info("Shutting down.")


app = FastAPI(
    title="Semantic Cache Proxy",
    version="0.4.0",
    description=(
        "Semantic caching proxy for LLM APIs using BGE-small embeddings. "
        "Exact-match tier + cosine similarity fallback, TTL invalidation, "
        "bypass header, threshold-sweep evaluation. "
        "Mirrors the OpenAI /v1/chat/completions shape."
    ),
    lifespan=lifespan,
)

app.include_router(chat_router)


# ---------------------------------------------------------------------------
# Health + metrics + purge (unchanged from Phase 1)
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "phase": 2}


@app.get("/metrics", response_model=MetricsResponse)
async def metrics():
    return get_metrics()


@app.post("/cache/purge", response_model=PurgeResponse)
async def cache_purge(body: PurgeRequest):
    count = purge(entry_id=body.entry_id)
    return PurgeResponse(purged_count=count)


@app.post("/eval/threshold-sweep", response_model=ThresholdSweepResponse)
async def threshold_sweep(body: ThresholdSweepRequest):
    """Precision/recall/F1 at each requested threshold against the labeled pairs."""
    return ThresholdSweepResponse(results=run_threshold_sweep(body.thresholds))


@app.get("/cache/entries", response_model=CacheEntriesResponse)
async def cache_entries(q: Optional[str] = None):
    """List cache entries newest-first (optional substring filter on prompt)."""
    return CacheEntriesResponse(entries=list_cache_entries(q))


@app.get("/logs/recent", response_model=LogsResponse)
async def logs_recent(limit: int = 50):
    """Return the most recent request-log rows (max 500)."""
    return LogsResponse(logs=recent_logs(limit))


@app.get("/dashboard", include_in_schema=False)
async def dashboard():
    """Serve the Phase 5 metrics dashboard (single-page, Chart.js via CDN)."""
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


# ---------------------------------------------------------------------------
# Run standalone
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.proxy.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
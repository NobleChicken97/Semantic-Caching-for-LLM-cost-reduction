"""FastAPI proxy entry point — Phase 1: exact-string-match caching."""

from __future__ import annotations

from fastapi import FastAPI

from .config import settings
from .database import init_db, seed_test_pairs
from .routes.chat import router as chat_router

# We'll add these routes when their phases arrive; stubbed for now.
from .cache import get_metrics, purge
from .models import (
    MetricsResponse,
    PurgeRequest,
    PurgeResponse,
)

app = FastAPI(
    title="Semantic Cache Proxy",
    version="0.1.0",
    description=(
        "Phase 1 — exact-string-match caching proxy for LLM APIs. "
        "Mirrors the OpenAI /v1/chat/completions shape."
    ),
)

app.include_router(chat_router)


@app.on_event("startup")
async def on_startup():
    init_db()
    seed_test_pairs()


# ---------------------------------------------------------------------------
# Phase 1 health + metrics endpoints (lightweight, already wired)
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "phase": 1}


@app.get("/metrics", response_model=MetricsResponse)
async def metrics():
    return get_metrics()


@app.post("/cache/purge", response_model=PurgeResponse)
async def cache_purge(body: PurgeRequest):
    count = purge(entry_id=body.entry_id)
    return PurgeResponse(purged_count=count)


# ---------------------------------------------------------------------------
# Run standalone
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "proxy.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
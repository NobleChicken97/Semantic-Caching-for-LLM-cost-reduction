"""FastAPI proxy entry point — Phase 2: semantic caching with BGE embeddings."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse

from .cache import get_metrics, list_cache_entries, prune_old_logs, purge, recent_logs
from .config import get_settings
from .database import init_db, seed_test_pairs
from .eval import run_auto_tune, run_threshold_sweep
from .models import (
    AutoTuneRequest,
    AutoTuneResponse,
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


async def require_admin_token(
    authorization: str | None = Header(default=None),
) -> None:
    """Bearer-token gate for admin endpoints (review fix #5).

    No-op while ``ADMIN_TOKEN`` is unset so the mock-mode demo stays
    frictionless; set ADMIN_TOKEN in any real deployment to lock down
    /cache/purge, /eval/threshold-sweep and /dashboard.
    """
    expected = get_settings().admin_token
    if not expected:
        return
    if authorization != f"Bearer {expected}":
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing admin bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: shared HTTP client, init DB, seed data, warm embeddings."""
    cfg = get_settings()
    if not cfg.admin_token:
        logger.warning("ADMIN_TOKEN not set — admin endpoints are unauthenticated.")
    if not cfg.user_id_pepper:
        logger.warning(
            "USER_ID_PEPPER not set — derived user_ids are keyed by an empty "
            "secret. Required before serving real BYOK traffic."
        )

    # One shared httpx client (connection pool) reused by every upstream
    # call instead of a new client per request (review fix #7).
    app.state.http_client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))

    try:
        init_db()
        seed_test_pairs()
        # Phase 7.6: lazy retention pass at startup — roll >30-day raw logs
        # into the permanent daily_metrics table and prune them. Cheap at
        # this scale; a scheduler is deliberately not built for it.
        try:
            pruned = prune_old_logs()
            if pruned:
                logger.info(
                    "Pruned %d request-log row(s) past the 30-day window.", pruned
                )
        except Exception:
            logger.exception("Log retention pass failed — will retry next startup.")
        logger.info("Database initialized.")

        # Preload the embedding model so the first request isn't slow
        from .embedding import embed_texts

        try:
            _ = embed_texts(["warmup hello world"])
            logger.info("Embedding model loaded (BAAI/bge-small-en-v1.5).")
        except Exception:
            logger.exception(
                "Failed to warm embedding model — will retry on first request."
            )

        yield  # app runs here
    finally:
        await app.state.http_client.aclose()
        logger.info("Shutting down.")


app = FastAPI(
    title="Semantic Cache Proxy",
    version="0.5.0",
    description=(
        "Semantic caching proxy for LLM APIs using BGE-small embeddings. "
        "Exact-match tier + cosine similarity fallback, TTL invalidation, "
        "bypass header, threshold-sweep and auto-tune evaluation. "
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
    return {"status": "ok", "phase": 7}


@app.get("/metrics", response_model=MetricsResponse)
async def metrics():
    return get_metrics()


@app.post(
    "/cache/purge",
    response_model=PurgeResponse,
    dependencies=[Depends(require_admin_token)],
)
async def cache_purge(body: PurgeRequest):
    count = purge(entry_id=body.entry_id)
    return PurgeResponse(purged_count=count)


@app.post(
    "/eval/threshold-sweep",
    response_model=ThresholdSweepResponse,
    dependencies=[Depends(require_admin_token)],
)
async def threshold_sweep(body: ThresholdSweepRequest):
    """Precision/recall/F1 at each requested threshold against the labeled pairs."""
    return ThresholdSweepResponse(results=run_threshold_sweep(body.thresholds))


@app.post(
    "/eval/auto-tune",
    response_model=AutoTuneResponse,
    dependencies=[Depends(require_admin_token)],
)
async def auto_tune(body: AutoTuneRequest):
    """Sweep thresholds, pick the F1-optimal one, and surface the borderline pairs.

    Developer aid: omits the threshold grid to use the documented default;
    ties on F1 break toward the lower (higher-recall) threshold. The
    borderline pairs are the labeled examples sitting within ±0.03 of the
    pick — the concrete evidence behind the recommendation.
    """
    tune = run_auto_tune(body.thresholds)
    best = tune["best"]
    return AutoTuneResponse(
        best_threshold=best.threshold if best else None,
        best_f1=best.f1 if best else None,
        results=tune["results"],
        borderline=tune["borderline"],
    )


@app.get("/cache/entries", response_model=CacheEntriesResponse)
async def cache_entries(q: str | None = None):
    """List cache entries newest-first (optional substring filter on prompt)."""
    return CacheEntriesResponse(entries=list_cache_entries(q))


@app.get("/logs/recent", response_model=LogsResponse)
async def logs_recent(limit: int = 50):
    """Return the most recent request-log rows (max 500)."""
    return LogsResponse(logs=recent_logs(limit))


@app.get(
    "/dashboard",
    include_in_schema=False,
    dependencies=[Depends(require_admin_token)],
)
async def dashboard():
    """Serve the Phase 5 metrics dashboard (single-page, Chart.js via CDN)."""
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


# ---------------------------------------------------------------------------
# Run standalone
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    _cfg = get_settings()
    uvicorn.run(
        "src.proxy.main:app",
        host=_cfg.host,
        port=_cfg.port,
        reload=True,
    )

"""Integration tests for the FastAPI endpoints via httpx + ASGI transport."""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

os.environ["MOCK_LLM"] = "true"


PROMPT_FRANCE = {
    "model": "gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "What is the capital of France?"}],
}
PROMPT_FRANCE_PARAPHRASE = {
    "model": "gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "Tell me the capital of France."}],
}
PROMPT_WATER = {
    "model": "gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "What is the boiling point of water?"}],
}


@pytest.fixture
async def client(monkeypatch, tmp_path):
    """Async test client with an isolated temp database.

    Patch settings BEFORE the lifespan runs so init_db creates the
    schema in the temp file, not the project-root cache.db.
    """
    db_path = str(tmp_path / "test_cache.db")
    monkeypatch.setenv("CACHE_DB_PATH", db_path)

    # Re-import settings and patch the cached value
    from proxy.config import settings

    monkeypatch.setattr(settings, "cache_db_path", db_path)

    # Also init the DB manually (lifespan does it too, but we want a clean
    # predictable state before the first request).
    from proxy.database import init_db, seed_test_pairs

    init_db()
    seed_test_pairs()

    from proxy.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHealth:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["phase"] == 2


class TestChatCompletions:
    @pytest.mark.asyncio
    async def test_first_request_miss(self, client):
        resp = await client.post("/v1/chat/completions", json=PROMPT_FRANCE)
        assert resp.status_code == 200
        data = resp.json()
        assert data["cache_metadata"]["outcome"] == "MISS"

    @pytest.mark.asyncio
    async def test_identical_request_hit(self, client):
        await client.post("/v1/chat/completions", json=PROMPT_FRANCE)
        resp = await client.post("/v1/chat/completions", json=PROMPT_FRANCE)
        data = resp.json()
        assert data["cache_metadata"]["outcome"] == "HIT"
        assert data["cache_metadata"]["similarity_score"] == 1.0

    @pytest.mark.asyncio
    async def test_paraphrase_semantic_hit(self, client):
        await client.post("/v1/chat/completions", json=PROMPT_FRANCE)
        resp = await client.post("/v1/chat/completions", json=PROMPT_FRANCE_PARAPHRASE)
        data = resp.json()
        assert data["cache_metadata"]["outcome"] == "HIT"
        assert data["cache_metadata"]["similarity_score"] >= 0.85

    @pytest.mark.asyncio
    async def test_unrelated_prompt_miss(self, client):
        await client.post("/v1/chat/completions", json=PROMPT_FRANCE)
        resp = await client.post("/v1/chat/completions", json=PROMPT_WATER)
        data = resp.json()
        assert data["cache_metadata"]["outcome"] == "MISS"

    @pytest.mark.asyncio
    async def test_bypass_header(self, client):
        await client.post("/v1/chat/completions", json=PROMPT_FRANCE)
        resp = await client.post(
            "/v1/chat/completions",
            json=PROMPT_FRANCE,
            headers={"X-Cache-Bypass": "true"},
        )
        data = resp.json()
        assert data["cache_metadata"]["outcome"] == "BYPASS"

    @pytest.mark.asyncio
    async def test_cache_metadata_present_on_every_response(self, client):
        resp = await client.post("/v1/chat/completions", json=PROMPT_FRANCE)
        data = resp.json()
        assert "cache_metadata" in data
        assert data["cache_metadata"]["outcome"] in ("HIT", "MISS", "BYPASS")


class TestMetrics:
    @pytest.mark.asyncio
    async def test_metrics_after_requests(self, client):
        await client.post("/v1/chat/completions", json=PROMPT_FRANCE)
        await client.post("/v1/chat/completions", json=PROMPT_FRANCE)

        resp = await client.get("/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_requests"] == 2
        assert data["hit_rate"] == 0.5


class TestPurge:
    @pytest.mark.asyncio
    async def test_purge_all(self, client):
        await client.post("/v1/chat/completions", json=PROMPT_FRANCE)
        resp = await client.post("/v1/chat/completions", json=PROMPT_FRANCE)
        assert resp.json()["cache_metadata"]["outcome"] == "HIT"

        purge_resp = await client.post("/cache/purge", json={})
        assert purge_resp.status_code == 200

        resp = await client.post("/v1/chat/completions", json=PROMPT_FRANCE)
        assert resp.json()["cache_metadata"]["outcome"] == "MISS"


class TestThresholdSweepEndpoint:
    @pytest.mark.asyncio
    async def test_sweep_returns_structure(self, client):
        """POST /eval/threshold-sweep returns a result per threshold."""
        resp = await client.post(
            "/eval/threshold-sweep",
            json={"thresholds": [0.80, 0.85, 0.90]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 3
        for r in data["results"]:
            assert set(r.keys()) == {"threshold", "precision", "recall", "f1"}
            assert 0.0 <= r["precision"] <= 1.0
            assert 0.0 <= r["recall"] <= 1.0
            assert 0.0 <= r["f1"] <= 1.0

    @pytest.mark.asyncio
    async def test_sweep_seeded_data_recall_at_low_threshold(self, client):
        """With seeded pairs, a low threshold must catch most positives."""
        resp = await client.post("/eval/threshold-sweep", json={"thresholds": [0.50]})
        assert resp.status_code == 200
        r = resp.json()["results"][0]
        assert r["recall"] >= 0.9

    @pytest.mark.asyncio
    async def test_sweep_empty_thresholds(self, client):
        resp = await client.post("/eval/threshold-sweep", json={"thresholds": []})
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    @pytest.mark.asyncio
    async def test_sweep_missing_body_field_is_422(self, client):
        resp = await client.post("/eval/threshold-sweep", json={})
        assert resp.status_code == 422


class TestCacheEntriesEndpoint:
    @pytest.mark.asyncio
    async def test_lists_stored_entries_newest_first(self, client):
        await client.post("/v1/chat/completions", json=PROMPT_FRANCE)
        await client.post("/v1/chat/completions", json=PROMPT_WATER)

        resp = await client.get("/cache/entries")
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        assert len(entries) == 2
        # Stored prompts are canonicalized with a [role] prefix
        assert {e["prompt_text"] for e in entries} == {
            "[user]What is the capital of France?",
            "[user]What is the boiling point of water?",
        }
        for e in entries:
            assert set(e.keys()) == {
                "entry_id", "prompt_text", "model_used",
                "created_at", "expires_at", "hit_count", "last_hit_at",
            }

    @pytest.mark.asyncio
    async def test_substring_filter(self, client):
        await client.post("/v1/chat/completions", json=PROMPT_FRANCE)
        await client.post("/v1/chat/completions", json=PROMPT_WATER)

        resp = await client.get("/cache/entries", params={"q": "France"})
        entries = resp.json()["entries"]
        assert len(entries) == 1
        assert "France" in entries[0]["prompt_text"]  # canonical form keeps content

        resp = await client.get("/cache/entries", params={"q": "zzz-no-match"})
        assert resp.json()["entries"] == []

    @pytest.mark.asyncio
    async def test_empty_cache_returns_empty_list(self, client):
        resp = await client.get("/cache/entries")
        assert resp.status_code == 200
        assert resp.json()["entries"] == []


class TestLogsRecentEndpoint:
    @pytest.mark.asyncio
    async def test_returns_logged_requests_newest_first(self, client):
        await client.post("/v1/chat/completions", json=PROMPT_FRANCE)
        await client.post("/v1/chat/completions", json=PROMPT_FRANCE)  # HIT
        await client.post(
            "/v1/chat/completions", json=PROMPT_WATER,
            headers={"X-Cache-Bypass": "true"},
        )  # BYPASS

        resp = await client.get("/logs/recent")
        assert resp.status_code == 200
        logs = resp.json()["logs"]
        assert len(logs) == 3
        assert [l["outcome"] for l in logs] == ["BYPASS", "HIT", "MISS"]
        assert all(l["estimated_cost_usd"] >= 0 for l in logs)
        assert all(l["latency_ms"] >= 0 for l in logs)

    @pytest.mark.asyncio
    async def test_limit_respected(self, client):
        await client.post("/v1/chat/completions", json=PROMPT_FRANCE)
        await client.post("/v1/chat/completions", json=PROMPT_WATER)

        resp = await client.get("/logs/recent", params={"limit": 1})
        logs = resp.json()["logs"]
        assert len(logs) == 1

    @pytest.mark.asyncio
    async def test_limit_clamped_to_valid_range(self, client):
        resp = await client.get("/logs/recent", params={"limit": 0})
        assert resp.status_code == 200
        resp = await client.get("/logs/recent", params={"limit": 100000})
        assert resp.status_code == 200
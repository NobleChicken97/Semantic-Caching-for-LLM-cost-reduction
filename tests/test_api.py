"""Integration tests for the FastAPI endpoints via httpx + ASGI transport."""

from __future__ import annotations

import os
from typing import ClassVar

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
PROMPT_FRANCE_GPT4 = {
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "What is the capital of France?"}],
}


@pytest.fixture
async def client(monkeypatch, tmp_path):
    """Async test client with an isolated temp database.

    Settings are re-read from the environment via ``get_settings.cache_clear()``
    BEFORE the lifespan runs, so init_db creates the schema in the temp file,
    not the project-root cache.db.
    """
    db_path = str(tmp_path / "test_cache.db")
    monkeypatch.setenv("CACHE_DB_PATH", db_path)

    from proxy.config import get_settings

    get_settings.cache_clear()

    # Also init the DB manually (lifespan does it too, but we want a clean
    # predictable state before the first request).
    from proxy.database import init_db, seed_test_pairs

    init_db()
    seed_test_pairs()

    from proxy.main import app, lifespan

    # httpx's ASGITransport does NOT run FastAPI lifespan events, so we
    # enter the lifespan explicitly: it creates app.state.http_client,
    # runs init_db/seed (idempotent), and closes the client afterwards.
    transport = ASGITransport(app=app)
    async with (
        lifespan(app),
        AsyncClient(transport=transport, base_url="http://test") as ac,
    ):
        yield ac

    # Don't leak this test's tmp path into the cached settings of other tests.
    get_settings.cache_clear()


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
    async def test_identical_messages_different_model_miss(self, client):
        """Same messages, different model → MISS, never a cross-model HIT."""
        await client.post("/v1/chat/completions", json=PROMPT_FRANCE)
        resp = await client.post("/v1/chat/completions", json=PROMPT_FRANCE_GPT4)
        data = resp.json()
        assert data["cache_metadata"]["outcome"] == "MISS"
        # The response must claim the requested model, not the cached one.
        assert data["model"] == "gpt-4"

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


class TestRequestCoalescing:
    @pytest.mark.asyncio
    async def test_concurrent_identical_prompts_forward_once(self, client, monkeypatch):
        """Review fix #3 — 5 concurrent identical requests trigger exactly
        ONE upstream LLM call; the rest are served from the just-filled cache.
        """
        import asyncio

        from proxy.llm_client import _mock_response
        from proxy.routes import chat as chat_module

        calls = {"count": 0}

        async def slow_mock_forward(request_body, *, client=None, api_key=None, base_url=None):
            calls["count"] += 1
            await asyncio.sleep(0.25)  # simulate real upstream latency
            return _mock_response(request_body), 0.25

        monkeypatch.setattr(chat_module, "forward_to_llm", slow_mock_forward)

        payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Coalesce me please"}],
        }
        responses = await asyncio.gather(
            *[client.post("/v1/chat/completions", json=payload) for _ in range(5)]
        )

        assert all(r.status_code == 200 for r in responses)
        assert calls["count"] == 1
        # Every caller gets the same answer, whether MISS or coalesced HIT.
        contents = {r.json()["choices"][0]["message"]["content"] for r in responses}
        assert len(contents) == 1


class TestUpstreamErrors:
    """Review fix #4 — upstream failures become OpenAI-shaped API errors."""

    @pytest.mark.asyncio
    async def test_upstream_http_status_error_passes_status_through(self, client, monkeypatch):
        import httpx

        from proxy.routes import chat as chat_module

        async def raise_status(request_body, *, client=None, api_key=None, base_url=None):
            req = httpx.Request("POST", "https://upstream.test/v1/chat/completions")
            resp = httpx.Response(429, request=req)
            raise httpx.HTTPStatusError("rate limited", request=req, response=resp)

        monkeypatch.setattr(chat_module, "forward_to_llm", raise_status)

        resp = await client.post("/v1/chat/completions", json=PROMPT_FRANCE)
        assert resp.status_code == 429
        data = resp.json()
        assert set(data.keys()) == {"error"}
        assert data["error"]["code"] == 429
        assert data["error"]["type"] == "upstream_api_error"
        assert "message" in data["error"]

    @pytest.mark.asyncio
    async def test_upstream_connection_error_returns_502(self, client, monkeypatch):
        import httpx

        from proxy.routes import chat as chat_module

        async def raise_conn(request_body, *, client=None, api_key=None, base_url=None):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(chat_module, "forward_to_llm", raise_conn)

        resp = await client.post("/v1/chat/completions", json=PROMPT_FRANCE)
        assert resp.status_code == 502
        data = resp.json()
        assert data["error"]["code"] == 502
        assert data["error"]["type"] == "upstream_connection_error"

    @pytest.mark.asyncio
    async def test_failed_request_writes_no_cache_entry_but_logs_error(self, client, monkeypatch):
        import httpx

        from proxy.routes import chat as chat_module

        async def raise_status(request_body, *, client=None, api_key=None, base_url=None):
            req = httpx.Request("POST", "https://upstream.test/v1/chat/completions")
            resp = httpx.Response(500, request=req)
            raise httpx.HTTPStatusError("boom", request=req, response=resp)

        monkeypatch.setattr(chat_module, "forward_to_llm", raise_status)

        resp = await client.post("/v1/chat/completions", json=PROMPT_WATER)
        assert resp.status_code == 500

        # No cache entry was stored for the failed prompt.
        entries = await client.get("/cache/entries")
        assert entries.json()["entries"] == []

        # But the failure IS logged, with zeroed cost/tokens.
        logs = (await client.get("/logs/recent")).json()["logs"]
        assert [l["outcome"] for l in logs] == ["ERROR"]
        assert logs[0]["estimated_cost_usd"] == 0.0
        assert logs[0]["tokens_in"] == 0
        assert logs[0]["tokens_out"] == 0


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
        # Stored prompts are canonicalized with [model] + [role] prefixes
        assert {e["prompt_text"] for e in entries} == {
            "[model]gpt-3.5-turbo\n[user]What is the capital of France?",
            "[model]gpt-3.5-turbo\n[user]What is the boiling point of water?",
        }
        for e in entries:
            assert set(e.keys()) == {
                "entry_id", "prompt_text", "model_used", "user_id",
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


class TestProviderAllowlist:
    """Phase 7.1 — caller-selected upstream is allowlist-enforced."""

    @pytest.fixture
    def upstream_spy(self, monkeypatch):
        """Capture forward_to_llm kwargs while keeping mock responses."""
        import httpx  # noqa: F401

        from proxy.llm_client import _mock_response
        from proxy.routes import chat as chat_module

        captured = {}

        async def spy(request_body, *, client=None, api_key=None, base_url=None):
            captured["base_url"] = base_url
            return _mock_response(request_body), 0.02

        monkeypatch.setattr(chat_module, "forward_to_llm", spy)
        return captured

    @pytest.mark.asyncio
    async def test_provider_name_maps_to_allowlisted_url(self, client, upstream_spy):
        resp = await client.post(
            "/v1/chat/completions",
            json={**PROMPT_WATER, "provider": "openrouter"},
        )
        assert resp.status_code == 200
        assert upstream_spy["base_url"] == "https://openrouter.ai/api/v1"

    @pytest.mark.asyncio
    async def test_exact_allowlisted_header_accepted(self, client, upstream_spy):
        resp = await client.post(
            "/v1/chat/completions",
            json=PROMPT_WATER,
            headers={"X-LLM-Base-URL": "https://generativelanguage.googleapis.com/v1beta/openai/"},
        )
        assert resp.status_code == 200
        # Trailing slash normalized to canonical form.
        assert upstream_spy["base_url"] == (
            "https://generativelanguage.googleapis.com/v1beta/openai"
        )

    @pytest.mark.asyncio
    async def test_non_allowlisted_url_rejected_400(self, client, upstream_spy):
        resp = await client.post(
            "/v1/chat/completions",
            json=PROMPT_WATER,
            headers={"X-LLM-Base-URL": "https://evil.example.com/v1"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"]["type"] == "invalid_request_error"
        assert "allowlist" in data["error"]["message"]
        # Rejection happens before any forwarding attempt.
        assert "base_url" not in upstream_spy

    @pytest.mark.asyncio
    async def test_unknown_provider_name_rejected_400(self, client, upstream_spy):
        resp = await client.post(
            "/v1/chat/completions",
            json={**PROMPT_WATER, "provider": "not-a-provider"},
        )
        assert resp.status_code == 400
        assert "base_url" not in upstream_spy

    @pytest.mark.asyncio
    async def test_header_wins_over_provider_field(self, client, upstream_spy):
        resp = await client.post(
            "/v1/chat/completions",
            json={**PROMPT_WATER, "provider": "openrouter"},
            headers={"X-LLM-Base-URL": "gemini"},
        )
        assert resp.status_code == 200
        assert upstream_spy["base_url"] == (
            "https://generativelanguage.googleapis.com/v1beta/openai"
        )

    @pytest.mark.asyncio
    async def test_omitted_selection_uses_configured_default(self, client, upstream_spy):
        resp = await client.post("/v1/chat/completions", json=PROMPT_WATER)
        assert resp.status_code == 200
        assert upstream_spy["base_url"] is None


class TestByokKeyForwarding:
    """Phase 7.2 — caller keys go upstream; no key + real mode → 401."""

    @pytest.fixture
    def real_mode(self, monkeypatch):
        monkeypatch.setenv("MOCK_LLM", "false")
        from proxy.config import get_settings

        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    @pytest.fixture
    def key_spy(self, monkeypatch):
        """Capture forward_to_llm kwargs while keeping mock responses."""
        from proxy.llm_client import _mock_response
        from proxy.routes import chat as chat_module

        captured = {}

        async def spy(request_body, *, client=None, api_key=None, base_url=None):
            captured["api_key"] = api_key
            return _mock_response(request_body), 0.02

        monkeypatch.setattr(chat_module, "forward_to_llm", spy)
        return captured

    def test_forward_to_llm_refuses_keyless_real_call(self, monkeypatch):
        """Defense in depth: llm_client itself refuses to use the server key."""
        import asyncio

        import pytest as _pytest

        from proxy.config import get_settings
        from proxy.llm_client import forward_to_llm

        monkeypatch.setenv("MOCK_LLM", "false")
        get_settings.cache_clear()
        try:
            with _pytest.raises(ValueError, match="BYOK"):
                asyncio.run(forward_to_llm({"model": "x", "messages": []}))
        finally:
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_no_key_in_real_mode_returns_401(self, client, real_mode):
        resp = await client.post("/v1/chat/completions", json=PROMPT_WATER)
        assert resp.status_code == 401
        data = resp.json()
        assert data["error"]["type"] == "invalid_request_error"
        assert "bring-your-own-key" in data["error"]["message"]

    @pytest.mark.asyncio
    async def test_malformed_auth_header_counts_as_missing(self, client, real_mode):
        resp = await client.post(
            "/v1/chat/completions",
            json=PROMPT_WATER,
            headers={"Authorization": "Token sk-something"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_bearer_prefix_without_token_counts_as_missing(self, client, real_mode):
        resp = await client.post(
            "/v1/chat/completions",
            json=PROMPT_WATER,
            headers={"Authorization": "Bearer   "},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_caller_key_forwarded_upstream_not_server_key(self, client, key_spy):
        from proxy.config import get_settings

        resp = await client.post(
            "/v1/chat/completions",
            json=PROMPT_WATER,
            headers={"Authorization": "Bearer sk-caller-abc123"},
        )
        assert resp.status_code == 200
        # The exact caller key reaches the upstream call site…
        assert key_spy["api_key"] == "sk-caller-abc123"
        # …which is NOT the server's configured key.
        assert key_spy["api_key"] != get_settings().llm_api_key

    @pytest.mark.asyncio
    async def test_mock_mode_still_works_without_any_key(self, client, key_spy):
        """Existing local/CI contract: MOCK_LLM=true + no header = unchanged."""
        resp = await client.post("/v1/chat/completions", json=PROMPT_WATER)
        assert resp.status_code == 200
        assert resp.json()["cache_metadata"]["outcome"] == "MISS"


class TestSharedHttpClient:
    """Review fix #7 — one lifespan-managed upstream client, reused."""

    @pytest.mark.asyncio
    async def test_shared_http_client_reused_across_requests(self, client, monkeypatch):
        import httpx

        from proxy.llm_client import _mock_response
        from proxy.main import app
        from proxy.routes import chat as chat_module

        shared = getattr(app.state, "http_client", None)
        assert isinstance(shared, httpx.AsyncClient)

        received = {}

        async def spy_forward(request_body, *, client=None, api_key=None, base_url=None):
            received["client"] = client
            return _mock_response(request_body), 0.02

        monkeypatch.setattr(chat_module, "forward_to_llm", spy_forward)

        await client.post("/v1/chat/completions", json=PROMPT_WATER)
        await client.post("/v1/chat/completions", json=PROMPT_WATER)

        # Same client object handed to forward_to_llm on both requests.
        assert app.state.http_client is shared
        assert received["client"] is shared


class TestMultiUserIsolation:
    """Phase 7.3/7.7 — end-to-end proof that users never see each other's cache."""

    ALICE: ClassVar[dict[str, str]] = {"Authorization": "Bearer sk-alice-test-key"}
    BOB: ClassVar[dict[str, str]] = {"Authorization": "Bearer sk-bob-test-key"}

    @pytest.mark.asyncio
    async def test_identical_prompt_two_users_no_cross_hit(self, client):
        r1 = await client.post("/v1/chat/completions", json=PROMPT_FRANCE, headers=self.ALICE)
        assert r1.json()["cache_metadata"]["outcome"] == "MISS"

        # Same exact prompt from Bob must MISS — Alice's entry is invisible.
        r2 = await client.post("/v1/chat/completions", json=PROMPT_FRANCE, headers=self.BOB)
        assert r2.json()["cache_metadata"]["outcome"] == "MISS"

        # Both now HIT their OWN entries on repeat.
        r3 = await client.post("/v1/chat/completions", json=PROMPT_FRANCE, headers=self.ALICE)
        r4 = await client.post("/v1/chat/completions", json=PROMPT_FRANCE, headers=self.BOB)
        assert r3.json()["cache_metadata"]["outcome"] == "HIT"
        assert r4.json()["cache_metadata"]["outcome"] == "HIT"
        assert r3.json()["choices"][0]["message"]["content"] == \
            r1.json()["choices"][0]["message"]["content"]

        # Two physically distinct entries for the same canonical prompt.
        entries = (await client.get("/cache/entries")).json()["entries"]
        france = [e for e in entries if "capital of France" in e["prompt_text"]]
        assert len(france) == 2

    @pytest.mark.asyncio
    async def test_derived_user_ids_stable_and_distinct(self, client):
        await client.post("/v1/chat/completions", json=PROMPT_WATER, headers=self.ALICE)
        await client.post("/v1/chat/completions", json=PROMPT_WATER, headers=self.BOB)

        from proxy.security import derive_user_id

        entries = (await client.get("/cache/entries")).json()["entries"]
        ids = {e["user_id"] for e in entries}
        assert ids == {derive_user_id("sk-alice-test-key"), derive_user_id("sk-bob-test-key")}

    @pytest.mark.asyncio
    async def test_paraphrase_does_not_cross_users(self, client):
        await client.post("/v1/chat/completions", json=PROMPT_FRANCE, headers=self.ALICE)
        resp = await client.post(
            "/v1/chat/completions",
            json=PROMPT_FRANCE_PARAPHRASE,
            headers=self.BOB,
        )
        assert resp.json()["cache_metadata"]["outcome"] == "MISS"

    @pytest.mark.asyncio
    async def test_keyless_mock_traffic_lands_on_local_user(self, client):
        await client.post("/v1/chat/completions", json=PROMPT_WATER)
        entries = (await client.get("/cache/entries")).json()["entries"]
        assert all(e["user_id"] == "local" for e in entries)


class TestAdminAuth:
    """Review fix #5 — optional bearer auth on admin endpoints."""

    @pytest.fixture
    def admin_token(self, monkeypatch):
        monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
        from proxy.config import get_settings

        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_purge_requires_token_when_set(self, client, admin_token):
        resp = await client.post("/cache/purge", json={})
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid or missing admin bearer token"

    @pytest.mark.asyncio
    async def test_purge_succeeds_with_correct_token(self, client, admin_token):
        resp = await client.post(
            "/cache/purge",
            json={},
            headers={"Authorization": "Bearer test-admin-token"},
        )
        assert resp.status_code == 200
        assert "purged_count" in resp.json()

    @pytest.mark.asyncio
    async def test_no_token_required_when_unset(self, client):
        """Default (ADMIN_TOKEN unset) keeps the demo frictionless."""
        resp = await client.post("/cache/purge", json={})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_dashboard_gated_when_set(self, client, admin_token):
        assert (await client.get("/dashboard")).status_code == 401
        assert (
            await client.get(
                "/dashboard", headers={"Authorization": "Bearer test-admin-token"}
            )
        ).status_code == 200


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
    async def test_hit_latency_is_measured_not_zero(self, client):
        """Review fix #2 — HIT rows must carry real measured latency."""
        await client.post("/v1/chat/completions", json=PROMPT_FRANCE)  # MISS
        await client.post("/v1/chat/completions", json=PROMPT_FRANCE)  # HIT

        resp = await client.get("/logs/recent")
        logs = resp.json()["logs"]
        hit_rows = [l for l in logs if l["outcome"] == "HIT"]
        assert len(hit_rows) == 1
        assert hit_rows[0]["latency_ms"] > 0

    @pytest.mark.asyncio
    async def test_limit_clamped_to_valid_range(self, client):
        resp = await client.get("/logs/recent", params={"limit": 0})
        assert resp.status_code == 200
        resp = await client.get("/logs/recent", params={"limit": 100000})
        assert resp.status_code == 200
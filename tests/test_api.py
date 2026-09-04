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
        assert data["phase"] == 7

    @pytest.mark.asyncio
    async def test_root_serves_service_card(self, client):
        """The bare URL answers with an endpoint map instead of a bare 404."""
        resp = await client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "Semantic Cache Proxy"
        assert data["version"] == "0.5.0"
        for key in (
            "chat_completions",
            "health",
            "metrics",
            "dashboard",
            "cache_entries",
            "recent_logs",
        ):
            assert key in data["endpoints"]


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

        async def slow_mock_forward(
            request_body, *, client=None, api_key=None, base_url=None
        ):
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
    async def test_upstream_http_status_error_passes_status_through(
        self, client, monkeypatch
    ):
        import httpx

        from proxy.routes import chat as chat_module

        async def raise_status(
            request_body, *, client=None, api_key=None, base_url=None
        ):
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
    async def test_failed_request_writes_no_cache_entry_but_logs_error(
        self, client, monkeypatch
    ):
        import httpx

        from proxy.routes import chat as chat_module

        async def raise_status(
            request_body, *, client=None, api_key=None, base_url=None
        ):
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


class TestUpstreamRetries:
    """Bounded retry on transient upstream failures (llm_client level)."""

    PAYLOAD: ClassVar[dict] = {
        "model": "gpt-x",
        "messages": [{"role": "user", "content": "hi"}],
    }

    @pytest.fixture
    def no_sleep(self, monkeypatch):
        """Capture retry delays without actually sleeping."""
        from proxy import llm_client as llm_module

        delays: list[float] = []

        async def fake_sleep(seconds):
            delays.append(seconds)

        monkeypatch.setattr(llm_module, "sleep", fake_sleep)
        return delays

    @staticmethod
    def _ok_payload():
        return {
            "id": "chatcmpl-ok",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-x",
            "choices": [],
        }

    @staticmethod
    def _stub(script):
        """httpx-client stand-in whose .post replays ``script`` steps.

        Each step is an Exception instance to raise, or a tuple
        ``(status, json_payload[, headers_dict])`` to return.
        """
        import httpx as _httpx

        class Stub:
            def __init__(self):
                self.calls = 0
                self._script = list(script)

            async def post(self, url, json=None, headers=None):
                self.calls += 1
                step = self._script.pop(0)
                if isinstance(step, Exception):
                    raise step
                status, payload = step[0], step[1]
                resp_headers = step[2] if len(step) > 2 else None
                req = _httpx.Request("POST", url)
                return _httpx.Response(
                    status, request=req, json=payload, headers=resp_headers
                )

        return Stub()

    def _real_mode(self, monkeypatch, *, attempts: str | None = None):
        """Switch off mock mode (+ optional retry override), fresh settings."""
        monkeypatch.setenv("MOCK_LLM", "false")
        if attempts is not None:
            monkeypatch.setenv("LLM_RETRY_MAX_ATTEMPTS", attempts)
        from proxy.config import get_settings
        from proxy.llm_client import reset_circuit_breakers

        get_settings.cache_clear()
        # Fresh breaker state per test: failures recorded by one test must
        # never leak into the next (the breaker registry is module-level).
        reset_circuit_breakers()
        return get_settings

    @pytest.mark.asyncio
    async def test_503_then_success_retries_once(self, monkeypatch, no_sleep):
        get_settings = self._real_mode(monkeypatch)
        try:
            from proxy.llm_client import forward_to_llm

            stub = self._stub([(503, {}), (200, self._ok_payload())])
            body, _lat = await forward_to_llm(self.PAYLOAD, client=stub, api_key="sk-t")
            assert stub.calls == 2
            assert body["id"] == "chatcmpl-ok"
            # First backoff step = base * 2^0.
            assert no_sleep == [pytest.approx(0.5)]
        finally:
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_401_fails_fast_without_retry(self, monkeypatch, no_sleep):
        get_settings = self._real_mode(monkeypatch)
        try:
            import httpx

            from proxy.llm_client import forward_to_llm

            stub = self._stub([(401, {"error": "bad key"})])
            with pytest.raises(httpx.HTTPStatusError):
                await forward_to_llm(self.PAYLOAD, client=stub, api_key="sk-bad")
            assert stub.calls == 1
            assert no_sleep == []
        finally:
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_connect_error_exhausts_all_attempts(self, monkeypatch, no_sleep):
        import httpx

        get_settings = self._real_mode(monkeypatch, attempts="2")
        try:
            from proxy.llm_client import forward_to_llm

            stub = self._stub(
                [httpx.ConnectError("refused"), httpx.ConnectError("refused")]
            )
            with pytest.raises(httpx.ConnectError):
                await forward_to_llm(self.PAYLOAD, client=stub, api_key="sk-t")
            assert stub.calls == 2
            assert len(no_sleep) == 1
        finally:
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_retry_after_header_overrides_backoff(self, monkeypatch, no_sleep):
        get_settings = self._real_mode(monkeypatch)
        try:
            from proxy.llm_client import forward_to_llm

            stub = self._stub(
                [(429, {}, {"Retry-After": "7"}), (200, self._ok_payload())]
            )
            body, _lat = await forward_to_llm(self.PAYLOAD, client=stub, api_key="sk-t")
            assert stub.calls == 2
            assert no_sleep == [7.0]
            assert body["id"] == "chatcmpl-ok"
        finally:
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_long_retry_after_fails_fast(self, monkeypatch, no_sleep):
        """A Retry-After beyond the in-request budget surfaces immediately."""
        import httpx

        get_settings = self._real_mode(monkeypatch)
        try:
            from proxy.llm_client import forward_to_llm

            stub = self._stub([(429, {}, {"Retry-After": "3600"})])
            with pytest.raises(httpx.HTTPStatusError):
                await forward_to_llm(self.PAYLOAD, client=stub, api_key="sk-t")
            assert stub.calls == 1
            assert no_sleep == []
        finally:
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_max_attempts_one_disables_retry(self, monkeypatch, no_sleep):
        import httpx

        get_settings = self._real_mode(monkeypatch, attempts="1")
        try:
            from proxy.llm_client import forward_to_llm

            stub = self._stub([(503, {})])
            with pytest.raises(httpx.HTTPStatusError):
                await forward_to_llm(self.PAYLOAD, client=stub, api_key="sk-t")
            assert stub.calls == 1
            assert no_sleep == []
        finally:
            get_settings.cache_clear()


class TestCircuitBreaker:
    """Per-upstream fail-fast guard (CLOSED/OPEN/HALF_OPEN), unit level."""

    @staticmethod
    def _breaker(threshold=3, reset=0.0):
        from proxy.llm_client import CircuitBreaker

        return CircuitBreaker(threshold, reset)

    def test_opens_after_threshold_consecutive_failures(self):
        b = self._breaker(threshold=3, reset=30.0)
        assert b.state == "CLOSED"
        b.record_failure()
        b.record_failure()
        assert b.state == "CLOSED"
        b.record_failure()
        assert b.state == "OPEN"
        assert not b.allow()

    def test_success_resets_consecutive_counter(self):
        b = self._breaker(threshold=2)
        b.record_failure()
        b.record_success()
        b.record_failure()
        assert b.state == "CLOSED"

    def test_cooldown_admits_single_probe_then_reopens_on_failure(self, monkeypatch):
        from proxy import llm_client as llm_module

        clock = {"now": 1000.0}

        class FakeTime:
            @staticmethod
            def monotonic():
                return clock["now"]

        monkeypatch.setattr(llm_module, "time", FakeTime)
        b = self._breaker(threshold=1, reset=30.0)
        b.record_failure()
        assert not b.allow()  # OPEN, cooldown running
        clock["now"] += 31.0  # cooldown elapses
        assert b.allow()  # HALF_OPEN probe admitted
        assert not b.allow()  # probe is single-flight
        b.record_failure()  # probe failed -> fresh cooldown
        assert not b.allow()
        clock["now"] += 31.0
        assert b.allow()  # probe admitted again after the new cooldown

    def test_probe_success_closes_breaker(self):
        b = self._breaker(threshold=1, reset=0.0)
        b.record_failure()
        assert b.allow()
        b.record_success()
        assert b.state == "CLOSED"
        assert b.allow()

    def test_threshold_zero_disables_breaker(self):
        b = self._breaker(threshold=0)
        for _ in range(10):
            b.record_failure()
        assert b.state == "DISABLED"
        assert b.allow()

    @pytest.mark.asyncio
    async def test_forward_fails_fast_without_network_when_open(self, monkeypatch):
        """After N exhausted failures the next forward raises before any I/O."""
        import httpx

        get_settings = self._real_mode(monkeypatch, attempts="1")
        try:
            from proxy.llm_client import CircuitOpenError, forward_to_llm

            stub = self._stub([(503, {})] * 5)
            for _ in range(5):
                with pytest.raises(httpx.HTTPStatusError):
                    await forward_to_llm(self.PAYLOAD, client=stub, api_key="sk-t")
            assert stub.calls == 5
            with pytest.raises(CircuitOpenError):
                await forward_to_llm(self.PAYLOAD, client=stub, api_key="sk-t")
            assert stub.calls == 5  # OPEN: zero additional network calls
        finally:
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_forward_ignores_non_retryable_4xx_for_breaker(self, monkeypatch):
        """A 401 storm is the caller's fault — it must not open the circuit."""
        import httpx

        get_settings = self._real_mode(monkeypatch, attempts="1")
        try:
            from proxy.llm_client import forward_to_llm

            stub = self._stub([(401, {"error": "bad key"})] * 10)
            for _ in range(10):
                with pytest.raises(httpx.HTTPStatusError):
                    await forward_to_llm(self.PAYLOAD, client=stub, api_key="sk-bad")

            ok = self._stub([(200, self._ok_payload())])
            body, _lat = await forward_to_llm(self.PAYLOAD, client=ok, api_key="sk-t")
            assert body["id"] == "chatcmpl-ok"  # still CLOSED, request went out
        finally:
            get_settings.cache_clear()

    # Reuse TestUpstreamRetries' helpers (plain-staticmethod aliases — no
    # subclassing, which would re-collect the base class's tests).
    _real_mode = TestUpstreamRetries._real_mode
    _stub = staticmethod(TestUpstreamRetries._stub)
    _ok_payload = staticmethod(TestUpstreamRetries._ok_payload)
    PAYLOAD = TestUpstreamRetries.PAYLOAD


class TestTokenEstimation:
    """tiktoken-based token counting with heuristic fallback."""

    def test_uses_tiktoken_when_available(self):
        from proxy.llm_client import _estimate_tokens

        try:
            import tiktoken
        except ImportError:
            pytest.skip("tiktoken not installed")
        enc = tiktoken.get_encoding("cl100k_base")
        text = "The quick brown fox jumps over the lazy dog."
        assert _estimate_tokens(text) == len(enc.encode(text))

    def test_heuristic_fallback_branch(self, monkeypatch):
        """With the encoding forced unavailable, len//4 (min 1) is used."""
        from proxy import llm_client as llm_module

        monkeypatch.setattr(llm_module, "_TOKEN_ENCODING", None)
        monkeypatch.setattr(llm_module, "_ENCODING_LOAD_TRIED", True)
        assert llm_module._estimate_tokens("abcdefgh") == 2  # 8 chars // 4
        assert llm_module._estimate_tokens("ab") == 1  # min 1

    def test_never_returns_zero_for_empty_text(self):
        from proxy.llm_client import _estimate_tokens

        assert _estimate_tokens("") >= 1


class TestCircuitBreakerEndpoint:
    @pytest.mark.asyncio
    async def test_open_circuit_returns_503_openai_shape(self, client, monkeypatch):
        """Circuit-open rejections surface as OpenAI-shaped 503s, logged not cached."""
        monkeypatch.setenv("MOCK_LLM", "false")
        from proxy import llm_client as llm_module
        from proxy.config import get_settings

        get_settings.cache_clear()
        try:
            breaker = llm_module.CircuitBreaker(1, 30.0)
            breaker.record_failure()  # -> OPEN
            monkeypatch.setattr(
                llm_module,
                "_breakers",
                {"https://api.openai.com/v1": breaker},
            )

            resp = await client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer sk-tester"},
                json=PROMPT_FRANCE,
            )
            assert resp.status_code == 503
            data = resp.json()
            assert data["error"]["code"] == 503
            assert data["error"]["type"] == "upstream_circuit_open"

            # The rejection is logged with zeroed cost/tokens, never cached.
            logs = (await client.get("/logs/recent")).json()["logs"]
            assert [entry["outcome"] for entry in logs] == ["ERROR"]
            entries = (await client.get("/cache/entries")).json()["entries"]
            assert entries == []
        finally:
            get_settings.cache_clear()


class TestUpstreamErrorDetail:
    @pytest.mark.asyncio
    async def test_upstream_error_message_includes_upstream_detail(
        self, client, monkeypatch
    ):
        """The upstream's own diagnostic text rides along in our error body."""
        import httpx

        from proxy.routes import chat as chat_module

        async def raise_503(request_body, *, client=None, api_key=None, base_url=None):
            req = httpx.Request("POST", "https://upstream.test/v1/chat/completions")
            resp = httpx.Response(
                503,
                request=req,
                json=[
                    {
                        "error": {
                            "code": 503,
                            "message": "This model is currently experiencing high demand.",
                        }
                    }
                ],
            )
            raise httpx.HTTPStatusError("svc", request=req, response=resp)

        monkeypatch.setattr(chat_module, "forward_to_llm", raise_503)

        resp = await client.post("/v1/chat/completions", json=PROMPT_FRANCE)
        assert resp.status_code == 503
        assert "high demand" in resp.json()["error"]["message"]


class TestUpstreamPayloadFidelity:
    """Forward EXACTLY the caller's fields — never injected Pydantic defaults.

    Regression: injecting temperature/top_p/n/stream/penalties defaults made
    Gemini's OpenAI-compat endpoint 400 on unknown 'frequency_penalty'.
    """

    @pytest.fixture
    def body_spy(self, monkeypatch):
        from proxy.llm_client import _mock_response
        from proxy.routes import chat as chat_module

        captured = {}

        async def spy(request_body, *, client=None, api_key=None, base_url=None):
            captured["payload"] = request_body
            return _mock_response(request_body), 0.02

        monkeypatch.setattr(chat_module, "forward_to_llm", spy)
        return captured

    @pytest.mark.asyncio
    async def test_unset_defaults_not_forwarded(self, client, body_spy):
        resp = await client.post("/v1/chat/completions", json=PROMPT_WATER)
        assert resp.status_code == 200
        payload = body_spy["payload"]
        assert set(payload.keys()) == {"model", "messages"}
        for banned in (
            "temperature",
            "top_p",
            "n",
            "stream",
            "presence_penalty",
            "frequency_penalty",
        ):
            assert banned not in payload

    @pytest.mark.asyncio
    async def test_explicit_params_forwarded_verbatim(self, client, body_spy):
        resp = await client.post(
            "/v1/chat/completions",
            json={**PROMPT_WATER, "temperature": 0.3, "max_tokens": 50},
        )
        assert resp.status_code == 200
        payload = body_spy["payload"]
        assert payload["temperature"] == 0.3
        assert payload["max_tokens"] == 50
        assert "top_p" not in payload  # still unset → still not sent


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


class TestRequestValidation:
    @pytest.mark.asyncio
    async def test_empty_messages_is_422(self, client):
        """Zero messages is not a request (OpenAI requires >= 1)."""
        resp = await client.post(
            "/v1/chat/completions", json={"model": "gpt-3.5-turbo", "messages": []}
        )
        assert resp.status_code == 422


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


class TestAutoTuneEndpoint:
    @pytest.mark.asyncio
    async def test_auto_tune_seeded_data_shape(self, client):
        """Default grid: a best pick plus per-threshold results and borderline pairs."""
        resp = await client.post("/eval/auto-tune", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {
            "best_threshold",
            "best_f1",
            "results",
            "borderline",
        }
        assert data["best_threshold"] is not None
        assert 0.0 <= data["best_f1"] <= 1.0
        for r in data["results"]:
            assert set(r.keys()) == {"threshold", "precision", "recall", "f1"}
        for p in data["borderline"]:
            assert set(p.keys()) == {
                "prompt_a",
                "prompt_b",
                "similarity",
                "should_match",
            }

    @pytest.mark.asyncio
    async def test_auto_tune_explicit_thresholds(self, client):
        resp = await client.post("/eval/auto-tune", json={"thresholds": [0.50]})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["threshold"] == 0.50
        assert data["best_threshold"] == 0.50

    @pytest.mark.asyncio
    async def test_auto_tune_empty_thresholds_returns_null_best(self, client):
        resp = await client.post("/eval/auto-tune", json={"thresholds": []})
        assert resp.status_code == 200
        data = resp.json()
        assert data["best_threshold"] is None
        assert data["results"] == []
        assert data["borderline"] == []


class TestCacheEntriesEndpoint:
    @pytest.mark.asyncio
    async def test_lists_stored_entries_newest_first(self, client):
        await client.post("/v1/chat/completions", json=PROMPT_FRANCE)
        await client.post("/v1/chat/completions", json=PROMPT_WATER)

        resp = await client.get("/cache/entries")
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        assert len(entries) == 2
        # Stored prompts are message-only canonical text (Phase 9: the [model]
        # line is hash identity, not embedding input — see embedding_text()).
        assert {e["prompt_text"] for e in entries} == {
            "[user]What is the capital of France?",
            "[user]What is the boiling point of water?",
        }
        for e in entries:
            assert set(e.keys()) == {
                "entry_id",
                "prompt_text",
                "model_used",
                "user_id",
                "created_at",
                "expires_at",
                "hit_count",
                "last_hit_at",
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
            headers={
                "X-LLM-Base-URL": "https://generativelanguage.googleapis.com/v1beta/openai/"
            },
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
    async def test_omitted_selection_uses_configured_default(
        self, client, upstream_spy
    ):
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
    async def test_bearer_prefix_without_token_counts_as_missing(
        self, client, real_mode
    ):
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

        async def spy_forward(
            request_body, *, client=None, api_key=None, base_url=None
        ):
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
        r1 = await client.post(
            "/v1/chat/completions", json=PROMPT_FRANCE, headers=self.ALICE
        )
        assert r1.json()["cache_metadata"]["outcome"] == "MISS"

        # Same exact prompt from Bob must MISS — Alice's entry is invisible.
        r2 = await client.post(
            "/v1/chat/completions", json=PROMPT_FRANCE, headers=self.BOB
        )
        assert r2.json()["cache_metadata"]["outcome"] == "MISS"

        # Both now HIT their OWN entries on repeat.
        r3 = await client.post(
            "/v1/chat/completions", json=PROMPT_FRANCE, headers=self.ALICE
        )
        r4 = await client.post(
            "/v1/chat/completions", json=PROMPT_FRANCE, headers=self.BOB
        )
        assert r3.json()["cache_metadata"]["outcome"] == "HIT"
        assert r4.json()["cache_metadata"]["outcome"] == "HIT"
        assert (
            r3.json()["choices"][0]["message"]["content"]
            == r1.json()["choices"][0]["message"]["content"]
        )

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
        assert ids == {
            derive_user_id("sk-alice-test-key"),
            derive_user_id("sk-bob-test-key"),
        }

    @pytest.mark.asyncio
    async def test_paraphrase_does_not_cross_users(self, client):
        await client.post(
            "/v1/chat/completions", json=PROMPT_FRANCE, headers=self.ALICE
        )
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

    @pytest.mark.asyncio
    async def test_query_param_token_fallback_for_browser(self, client, admin_token):
        """Browsers can't send Authorization headers on a link: /dashboard?token=
        must work for every gated endpoint (dashboard HTML + JSON APIs)."""
        assert (
            await client.get("/dashboard?token=test-admin-token")
        ).status_code == 200
        assert (
            await client.get("/metrics?token=test-admin-token")  # ungated anyway
        ).status_code == 200
        resp = await client.get(
            "/eval/threshold-sweep?token=test-admin-token"
        )  # GET not allowed; POST below
        assert resp.status_code in (401, 405)
        sweep = await client.post(
            "/eval/threshold-sweep?token=test-admin-token", json={"thresholds": [0.85]}
        )
        assert sweep.status_code == 200
        purge = await client.post("/cache/purge?token=test-admin-token", json={})
        assert purge.status_code == 200

    @pytest.mark.asyncio
    async def test_wrong_query_token_rejected(self, client, admin_token):
        resp = await client.get("/dashboard?token=wrong-token")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_header_wins_over_query_token(self, client, admin_token):
        """Both present: a WRONG header must not be rescued by a valid ?token."""
        resp = await client.get(
            "/dashboard?token=test-admin-token",
            headers={"Authorization": "Bearer nope"},
        )
        assert resp.status_code == 401


class TestLogsRecentEndpoint:
    @pytest.mark.asyncio
    async def test_returns_logged_requests_newest_first(self, client):
        await client.post("/v1/chat/completions", json=PROMPT_FRANCE)
        await client.post("/v1/chat/completions", json=PROMPT_FRANCE)  # HIT
        await client.post(
            "/v1/chat/completions",
            json=PROMPT_WATER,
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

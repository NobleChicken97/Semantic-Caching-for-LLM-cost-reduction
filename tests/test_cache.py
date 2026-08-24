"""Tests for the cache layer — exact-match + semantic lookup.

These tests use the cache functions directly (no HTTP) so the async
fixture and lifespan issues don't apply.
"""

from __future__ import annotations

import os
import tempfile
import time

import pytest

from proxy.cache import (
    _exact_lookup,
    _hash_prompt,
    get_metrics,
    log_request,
    lookup,
    prune_old_logs,
    purge,
    store,
)
from proxy.config import get_settings

# Always use mock mode
os.environ["MOCK_LLM"] = "true"


SAMPLE_RESPONSE = {
    "id": "chatcmpl-test",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "gpt-3.5-turbo",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "Paris"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
}


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    """Every test gets a temporary database path.

    Settings are re-read from the environment via ``cache_clear`` — no more
    patching attributes on the cached singleton (that workaround existed only
    because settings used to be frozen at import time).
    """
    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_cache_")
    os.close(fd)

    monkeypatch.setenv("CACHE_DB_PATH", path)
    get_settings.cache_clear()

    from proxy.database import init_db, seed_test_pairs

    init_db()
    seed_test_pairs()

    yield

    # Don't leak this test's tmp path into the cached settings of other tests.
    get_settings.cache_clear()

    try:
        os.unlink(path)
    except OSError:
        pass


class TestModelAwareCost:
    """Phase 7.4 — pricing table; unknown models cost $0.00."""

    def test_known_model_uses_table(self):
        from proxy.routes.chat import _estimate_cost

        resp = {"model": "gpt-3.5-turbo",
                "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}}
        assert _estimate_cost(resp) == pytest.approx(2.0)

    def test_unknown_model_costs_zero(self):
        from proxy.routes.chat import _estimate_cost

        resp = {"model": "some-free-openrouter-model",
                "usage": {"prompt_tokens": 999_999, "completion_tokens": 999_999}}
        assert _estimate_cost(resp) == 0.0

    def test_prefix_match_inherits_family_pricing(self):
        from proxy.routes.chat import _estimate_cost

        resp = {"model": "gpt-3.5-turbo-1106",
                "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0}}
        assert _estimate_cost(resp) == pytest.approx(0.5)

    def test_env_override_extends_table(self, monkeypatch):
        from proxy.config import get_settings
        from proxy.routes.chat import _estimate_cost

        monkeypatch.setenv("MODEL_PRICING", "gemini-flash=1.00,3.00")
        get_settings.cache_clear()
        try:
            resp = {"model": "gemini-flash",
                    "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0}}
            assert _estimate_cost(resp) == pytest.approx(1.0)
        finally:
            get_settings.cache_clear()


class TestSettingsFactory:
    """Review fix #8 — settings are read per-call, not frozen at import."""

    def test_env_changes_picked_up_after_cache_clear(self, monkeypatch):
        monkeypatch.setenv("SIMILARITY_THRESHOLD", "0.5")
        get_settings.cache_clear()
        try:
            assert get_settings().similarity_threshold == 0.5
        finally:
            # Restore for any subsequent lookups in this session.
            get_settings.cache_clear()

    def test_settings_are_frozen(self):
        from dataclasses import FrozenInstanceError

        with pytest.raises(FrozenInstanceError):
            get_settings().similarity_threshold = 0.1


class TestHash:
    def test_same_prompt_same_hash(self):
        assert _hash_prompt("hello") == _hash_prompt("hello")

    def test_different_prompt_different_hash(self):
        assert _hash_prompt("hello") != _hash_prompt("world")


class TestExactMatchCache:
    def test_store_and_retrieve(self):
        entry_id = store("What is the capital of France?", SAMPLE_RESPONSE, "gpt-3.5-turbo")
        assert entry_id > 0

        result = _exact_lookup("What is the capital of France?")
        assert result is not None
        assert result["response"]["choices"][0]["message"]["content"] == "Paris"

    def test_exact_match_is_exact(self):
        store("prompt A", SAMPLE_RESPONSE, "gpt-3.5-turbo")
        result = _exact_lookup("prompt B")
        assert result is None

    def test_miss_returns_none(self):
        result = _exact_lookup("never stored")
        assert result is None


class TestTwoTierLookup:
    def test_exact_match_hit(self):
        store("What is the capital of France?", SAMPLE_RESPONSE, "gpt-3.5-turbo")
        result = lookup("What is the capital of France?")
        assert result is not None
        assert result["similarity_score"] == 1.0

    def test_semantic_paraphrase_hit(self):
        store("What is the capital of France?", SAMPLE_RESPONSE, "gpt-3.5-turbo")
        result = lookup("Tell me the capital of France.")
        assert result is not None
        assert result["similarity_score"] >= get_settings().similarity_threshold

    def test_unrelated_miss(self):
        store("What is the capital of France?", SAMPLE_RESPONSE, "gpt-3.5-turbo")
        result = lookup("How do I bake a chocolate cake?")
        assert result is None


class TestModelIsolation:
    """The model name is part of the cache identity (review fix #1)."""

    def test_same_prompt_different_model_is_exact_miss(self):
        """Identical prompt text under a different model must MISS even though
        the SHA-256 of the text matches — the exact tier filters by model_used."""
        store("What is the capital of France?", SAMPLE_RESPONSE, "gpt-3.5-turbo")

        result = lookup("What is the capital of France?", model="gpt-4")
        assert result is None

    def test_same_prompt_same_model_still_hits(self):
        store("What is the capital of France?", SAMPLE_RESPONSE, "gpt-3.5-turbo")

        result = lookup("What is the capital of France?", model="gpt-3.5-turbo")
        assert result is not None
        assert result["similarity_score"] == 1.0

    def test_semantic_tier_never_crosses_models(self):
        """A paraphrase stored for gpt-3.5-turbo must not semantically hit
        when queried for gpt-4 — the semantic tier filters by model too."""
        store("What is the capital of France?", SAMPLE_RESPONSE, "gpt-3.5-turbo")

        # Exact tier misses (different text); only the semantic tier could
        # serve this, and it must refuse because the model differs.
        result = lookup("Tell me the capital of France.", model="gpt-4")
        assert result is None


class TestPurge:
    def test_purge_single_entry(self):
        eid = store("prompt X", SAMPLE_RESPONSE, "gpt-3.5-turbo")
        assert _exact_lookup("prompt X") is not None

        count = purge(entry_id=eid)
        assert count == 1
        assert _exact_lookup("prompt X") is None

    def test_purge_all(self):
        store("A", SAMPLE_RESPONSE, "gpt-3.5-turbo")
        store("B", SAMPLE_RESPONSE, "gpt-3.5-turbo")
        count = purge()
        assert count == 2

    def test_purge_all_with_log_reference(self):
        """A logged request referencing an entry must not break the purge."""
        eid = store("A", SAMPLE_RESPONSE, "gpt-3.5-turbo")
        log_request("A", "HIT", 1.0, matched_entry_id=eid)

        assert purge() == 1

        from proxy.database import get_connection

        conn = get_connection()
        try:
            refs = conn.execute(
                "SELECT COUNT(*) FROM request_log WHERE matched_entry_id IS NOT NULL"
            ).fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM request_log").fetchone()[0]
        finally:
            conn.close()
        assert refs == 0
        assert total == 1  # log row survives, only the reference is cleared

    def test_purge_single_with_log_reference(self):
        eid = store("A", SAMPLE_RESPONSE, "gpt-3.5-turbo")
        log_request("A", "HIT", 0.0, matched_entry_id=eid)
        assert purge(entry_id=eid) == 1


class TestTtlExpiry:
    def test_expired_entry_unreachable_via_lookup(self):
        """Phase 4.6 — an entry past expires_at is invisible and cleaned up."""
        import time as _time

        from proxy.database import get_connection

        eid = store("ephemeral prompt", SAMPLE_RESPONSE, "gpt-3.5-turbo")
        assert lookup("ephemeral prompt") is not None

        # Force the entry into the past without sleeping out a real TTL
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE cache_entries SET expires_at = ? WHERE entry_id = ?",
                (_time.time() - 10, eid),
            )
            conn.commit()
        finally:
            conn.close()

        # Exact tier must refuse the expired entry (and delete it)
        assert lookup("ephemeral prompt") is None

        conn = get_connection()
        try:
            remaining = conn.execute(
                "SELECT COUNT(*) FROM cache_entries WHERE entry_id = ?", (eid,)
            ).fetchone()[0]
        finally:
            conn.close()
        assert remaining == 0

    def test_semantic_lookup_skips_expired_entries(self):
        """The semantic tier's SQL filter excludes expired entries."""
        import time as _time

        from proxy.database import get_connection

        # Store under one string; probe with a paraphrase so the EXACT
        # tier misses and only the SEMANTIC tier can serve the entry.
        eid = store("What is the boiling point of water?", SAMPLE_RESPONSE, "gpt-3.5-turbo")

        conn = get_connection()
        try:
            conn.execute(
                "UPDATE cache_entries SET expires_at = ? WHERE entry_id = ?",
                (_time.time() - 10, eid),
            )
            conn.commit()
        finally:
            conn.close()

        result = lookup("Tell me the boiling point of water.")
        assert result is None


class TestEmbeddingDeserialization:
    """Corrupt/odd stored embeddings must degrade gracefully, never 500."""

    def test_truncated_blob_raises_value_error(self):
        import numpy as np

        from proxy.cache import _deserialize_embedding

        # A blob holding only 10 floats (e.g. partial write): frombuffer
        # silently returns the short array, so the length check must be
        # what catches it.
        short = np.zeros(10, dtype=np.float32).tobytes()
        with pytest.raises(ValueError):
            _deserialize_embedding(short)

    def test_zero_blob_raises_value_error(self):
        import numpy as np

        from proxy.cache import _deserialize_embedding

        zeros = np.zeros(384, dtype=np.float32).tobytes()
        with pytest.raises(ValueError):
            _deserialize_embedding(zeros)

    def test_unnormalized_blob_renormalized_to_unit_length(self):
        import numpy as np

        from proxy.cache import _deserialize_embedding

        rng = np.random.default_rng(42)
        raw = (rng.standard_normal(384) * 7.0).astype(np.float32)  # far from unit
        vec = _deserialize_embedding(raw.tobytes())
        assert vec.shape == (384,)
        assert abs(float(np.linalg.norm(vec)) - 1.0) < 1e-5

    def test_scan_skips_corrupt_row_without_raising(self):
        """A truncated blob in the table must not blow up the semantic scan."""
        from proxy.database import get_connection

        eid = store("What is the boiling point of water?", SAMPLE_RESPONSE, "gpt-3.5-turbo")

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT prompt_embedding FROM cache_entries WHERE entry_id = ?", (eid,)
            ).fetchone()
            truncated = bytes(row["prompt_embedding"])[: 100 * 4]  # 100 floats
            conn.execute(
                "UPDATE cache_entries SET prompt_embedding = ? WHERE entry_id = ?",
                (truncated, eid),
            )
            conn.commit()
        finally:
            conn.close()

        # Paraphrase probe -> exact tier misses, semantic tier hits the
        # corrupt row and must SKIP it (returning a clean miss, not raising).
        assert lookup("Tell me the boiling point of water.") is None

    def test_scaled_stored_vector_scores_same_after_renorm(self):
        """A stored vector drifted off unit length must still hit correctly."""
        import numpy as np

        from proxy.cache import lookup as _lookup
        from proxy.database import get_connection

        store("What is the boiling point of water?", SAMPLE_RESPONSE, "gpt-3.5-turbo")
        probe = "Tell me the boiling point of water."

        baseline = _lookup(probe)
        assert baseline is not None
        base_score = baseline["similarity_score"]

        # Corrupt the stored vector to 5x its original magnitude.
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT prompt_embedding FROM cache_entries WHERE entry_id = ?",
                (baseline["entry_id"],),
            ).fetchone()
            blown_up = (np.frombuffer(row["prompt_embedding"], dtype=np.float32) * 5.0).tobytes()
            conn.execute(
                "UPDATE cache_entries SET prompt_embedding = ? WHERE entry_id = ?",
                (blown_up, baseline["entry_id"]),
            )
            conn.commit()
        finally:
            conn.close()

        rescaled = _lookup(probe)
        assert rescaled is not None
        assert abs(rescaled["similarity_score"] - base_score) < 1e-3


class TestUserScoping:
    """Phase 7.3 — cache entries never cross users."""

    def test_derive_user_id_deterministic_and_distinct(self):
        from proxy.security import derive_user_id

        assert derive_user_id("sk-alice") == derive_user_id("sk-alice")
        assert derive_user_id("sk-alice") != derive_user_id("sk-bob")
        assert len(derive_user_id("sk-alice")) == 24

    def test_same_prompt_two_users_two_entries_no_cross_hit(self):
        resp_a = {**SAMPLE_RESPONSE, "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "Alice's Paris"},
             "finish_reason": "stop"}]}
        resp_b = {**SAMPLE_RESPONSE, "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "Bob's Paris"},
             "finish_reason": "stop"}]}

        id_a = store("What is the capital of France?", resp_a, "gpt-3.5-turbo", user_id="alice")
        id_b = store("What is the capital of France?", resp_b, "gpt-3.5-turbo", user_id="bob")
        assert id_a != id_b  # composite unique allows one entry per (hash, user)

        hit_a = lookup("What is the capital of France?", model="gpt-3.5-turbo", user_id="alice")
        hit_b = lookup("What is the capital of France?", model="gpt-3.5-turbo", user_id="bob")
        assert hit_a["response"]["choices"][0]["message"]["content"] == "Alice's Paris"
        assert hit_b["response"]["choices"][0]["message"]["content"] == "Bob's Paris"

    def test_semantic_tier_never_crosses_users(self):
        store("What is the capital of France?", SAMPLE_RESPONSE, "gpt-3.5-turbo", user_id="alice")

        # Bob's paraphrase must NOT semantically match Alice's cached answer.
        miss = lookup("Tell me the capital of France.", model="gpt-3.5-turbo", user_id="bob")
        assert miss is None

        # ...but Alice's own paraphrase still hits her entry.
        hit = lookup("Tell me the capital of France.", model="gpt-3.5-turbo", user_id="alice")
        assert hit is not None

    def test_default_user_is_local_for_legacy_calls(self):
        store("legacy prompt", SAMPLE_RESPONSE, "gpt-3.5-turbo")
        from proxy.cache import _exact_lookup

        assert _exact_lookup("legacy prompt") is not None
        assert _exact_lookup("legacy prompt", user_id="someone-else") is None


class TestLogRetention:
    """Phase 7.6 — 30-day hot/cold retention with a permanent daily rollup."""

    def _seed(self):
        # Two HITs and one MISS older than the window, one fresh row.
        log_request("old-hit-1", "HIT", 1.0, estimated_cost_usd=0.001,
                    tokens_in=10, tokens_out=20)
        log_request("old-hit-2", "HIT", 1.0, estimated_cost_usd=0.002,
                    tokens_in=5, tokens_out=5)
        log_request("old-miss", "MISS", 2.0, estimated_cost_usd=0.010,
                    tokens_in=999, tokens_out=999)
        log_request("fresh-hit", "HIT", 1.0, estimated_cost_usd=0.004,
                    tokens_in=7, tokens_out=3)

        old = time.time() - 40 * 86_400
        from proxy.database import get_connection

        conn = get_connection()
        try:
            conn.execute(
                "UPDATE request_log SET timestamp = ? "
                "WHERE prompt_text IN ('old-hit-1','old-hit-2','old-miss')",
                (old,),
            )
            conn.commit()
        finally:
            conn.close()

    def test_prune_rolls_up_then_deletes(self):
        self._seed()

        pruned = prune_old_logs(days=30)
        assert pruned == 3

        from proxy.database import get_connection

        conn = get_connection()
        try:
            remaining = conn.execute(
                "SELECT prompt_text FROM request_log"
            ).fetchall()
            assert [r["prompt_text"] for r in remaining] == ["fresh-hit"]

            rollup = conn.execute("SELECT * FROM daily_metrics").fetchone()
        finally:
            conn.close()
        assert rollup["total_requests"] == 3
        assert rollup["hits"] == 2
        assert rollup["tokens_saved"] == 40  # only HIT rows count as saved

    def test_metrics_totals_survive_pruning(self):
        self._seed()
        before = get_metrics()

        prune_old_logs(days=30)
        after = get_metrics()

        for key in ("total_requests", "hit_rate",
                    "estimated_cost_saved_usd", "total_tokens_saved"):
            assert after[key] == pytest.approx(before[key]), key
        # The fresh row is still raw and visible.
        assert after["total_requests"] == 4

    def test_prune_is_idempotent(self):
        self._seed()
        assert prune_old_logs(days=30) == 3
        assert prune_old_logs(days=30) == 0

        m = get_metrics()
        assert m["total_requests"] == 4
        # 40 from the pruned rollup + 10 still in the raw window.
        assert m["total_tokens_saved"] == 50


class TestTokensSavedAndPerUserMetrics:
    """Phase 7.4 — headline tokens-saved metric + per-user breakdown."""

    def test_tokens_saved_counts_hits_only(self):
        log_request("a", "HIT", 1.0, estimated_cost_usd=0.001,
                    tokens_in=10, tokens_out=20)
        log_request("b", "MISS", 2.0, estimated_cost_usd=0.002,
                    tokens_in=100, tokens_out=200)  # a real generation: not saved
        log_request("c", "BYPASS", 3.0, tokens_in=5, tokens_out=5)
        log_request("d", "ERROR", 4.0)

        m = get_metrics()
        assert m["total_requests"] == 4
        assert m["total_tokens_saved"] == 30

    def test_per_user_breakdown_sums_to_global(self):
        log_request("p1", "HIT", 1.0, estimated_cost_usd=0.001,
                    tokens_in=10, tokens_out=20, user_id="alice")
        log_request("p2", "HIT", 1.0, estimated_cost_usd=0.002,
                    tokens_in=1, tokens_out=2, user_id="alice")
        log_request("p3", "HIT", 1.0, estimated_cost_usd=0.004,
                    tokens_in=100, tokens_out=200, user_id="bob")
        log_request("p4", "MISS", 1.0, user_id="bob")

        m = get_metrics()
        by_user = {u["user_id"]: u for u in m["per_user"]}
        assert set(by_user) == {"alice", "bob"}
        assert by_user["alice"]["tokens_saved"] == 33
        assert by_user["alice"]["hits"] == 2
        assert by_user["alice"]["total_requests"] == 2
        assert by_user["bob"]["tokens_saved"] == 300
        assert by_user["bob"]["hits"] == 1
        assert by_user["bob"]["total_requests"] == 2
        assert sum(u["tokens_saved"] for u in m["per_user"]) == m["total_tokens_saved"]
        assert round(
            sum(u["cost_saved_usd"] for u in m["per_user"]), 6
        ) == m["estimated_cost_saved_usd"]


class TestSemanticScanGuardrail:
    """Review fix #6 — warn once when the O(n) semantic scan gets large."""

    def test_warns_when_scan_exceeds_configured_limit(self, monkeypatch, caplog):
        import logging

        from proxy import cache as cache_module

        monkeypatch.setenv("MAX_SEMANTIC_SCAN_ENTRIES", "2")
        get_settings.cache_clear()

        original = cache_module._scan_limit_warned
        cache_module._scan_limit_warned = False  # reset once-per-process flag
        try:
            store("alpha prompt one", SAMPLE_RESPONSE, "gpt-3.5-turbo")
            store("beta prompt two", SAMPLE_RESPONSE, "gpt-3.5-turbo")
            store("gamma prompt three", SAMPLE_RESPONSE, "gpt-3.5-turbo")

            with caplog.at_level(logging.WARNING, logger="proxy"):
                lookup("please tell me about alpha prompt one")  # forces semantic tier

            assert any(
                "MAX_SEMANTIC_SCAN_ENTRIES" in rec.getMessage()
                for rec in caplog.records
            )
            assert cache_module._scan_limit_warned is True
        finally:
            cache_module._scan_limit_warned = original
            get_settings.cache_clear()


class TestRequestLogAndMetrics:
    def test_empty_metrics(self):
        m = get_metrics()
        assert m["total_requests"] == 0
        assert m["hit_rate"] == 0.0

    def test_metrics_after_hits_and_misses(self):
        log_request("p1", "HIT", 1.0, similarity_score=1.0, estimated_cost_usd=0.001)
        log_request("p2", "MISS", 100.0, estimated_cost_usd=0.002)
        log_request("p3", "HIT", 2.0, similarity_score=1.0, estimated_cost_usd=0.003)

        m = get_metrics()
        assert m["total_requests"] == 3
        assert m["hit_rate"] == pytest.approx(2 / 3, rel=0.01)
        assert m["estimated_cost_saved_usd"] == 0.004  # 0.001 + 0.003
        assert m["avg_latency_hit_ms"] == 1.5  # (1+2)/2
        assert m["avg_latency_miss_ms"] == 100.0
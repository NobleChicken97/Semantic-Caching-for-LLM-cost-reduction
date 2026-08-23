"""Tests for the cache layer — exact-match + semantic lookup.

These tests use the cache functions directly (no HTTP) so the async
fixture and lifespan issues don't apply.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from proxy.cache import (
    _exact_lookup,
    _hash_prompt,
    get_metrics,
    log_request,
    lookup,
    purge,
    store,
)
from proxy.config import settings

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
    """Every test gets a temporary database path."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_cache_")
    os.close(fd)

    monkeypatch.setenv("CACHE_DB_PATH", path)
    monkeypatch.setattr(settings, "cache_db_path", path)

    from proxy.database import init_db, seed_test_pairs

    init_db()
    seed_test_pairs()

    yield

    try:
        os.unlink(path)
    except OSError:
        pass


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
        assert result["similarity_score"] >= settings.similarity_threshold

    def test_unrelated_miss(self):
        store("What is the capital of France?", SAMPLE_RESPONSE, "gpt-3.5-turbo")
        result = lookup("How do I bake a chocolate cake?")
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
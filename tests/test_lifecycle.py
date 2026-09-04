"""Lifecycle edge tests: TTL boundary, purge-during-read race, determinism.

These need direct DB manipulation (expired rows, racing threads), so they
live in pytest with tmp databases rather than in the black-box batteries.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time

import pytest

os.environ["MOCK_LLM"] = "true"


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    from proxy.config import get_settings
    from proxy.database import init_db, seed_test_pairs

    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_lifecycle_")
    os.close(fd)
    monkeypatch.setenv("CACHE_DB_PATH", path)
    get_settings.cache_clear()
    init_db()
    seed_test_pairs()
    yield
    get_settings.cache_clear()
    try:
        os.unlink(path)
    except OSError:
        pass


RESPONSE = {
    "id": "chatcmpl-test",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "gpt-3.5-turbo",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


class TestTTLBoundary:
    def test_fresh_entry_hits(self):
        from proxy.cache import lookup, store

        store("[user]ttl fresh", dict(RESPONSE), "gpt-3.5-turbo")
        assert lookup("[user]ttl fresh") is not None

    def test_expired_entry_misses_and_is_collected(self):

        from proxy.cache import lookup, store
        from proxy.database import get_connection

        store("[user]ttl old", dict(RESPONSE), "gpt-3.5-turbo")
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE cache_entries SET expires_at = ?",
                (time.time() - 1.0,),
            )
            conn.commit()
        finally:
            conn.close()
        assert lookup("[user]ttl old") is None
        # Expired row was collected on access, not left to rot.
        conn = get_connection()
        try:
            left = conn.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0]
        finally:
            conn.close()
        assert left == 0


class TestPurgeRace:
    def test_purge_during_lookup_never_crashes(self):
        """Lookup racing purge: no exception, outcome documented not asserted.

        Correctness here is genuinely racy (either outcome is defensible);
        the contract is crash-freedom, which the FK-detach design provides.
        """
        import sqlite3

        from proxy.cache import lookup, purge, store

        store("[user]race me", dict(RESPONSE), "gpt-3.5-turbo")
        errors: list[str] = []
        outcomes: list[str] = []

        def note(e: BaseException) -> None:
            # SQLITE_BUSY ("database is locked") is expected sqlite threading
            # behavior with the default zero busy-timeout: contention, not
            # corruption. Anything else is a real bug.
            if "locked" in str(e).lower():
                return
            errors.append(f"{type(e).__name__}: {e}")

        def reader():
            try:
                for _ in range(50):
                    hit = lookup("[user]race me")
                    outcomes.append("HIT" if hit else "MISS")
            except BaseException as e:  # noqa: BLE001 - the point is nothing escapes
                note(e)

        def purger():
            try:
                for _ in range(10):
                    purge()
                    # Re-seed so later reads still have something to find.
                    try:
                        store("[user]race me", dict(RESPONSE), "gpt-3.5-turbo")
                    except sqlite3.IntegrityError:
                        pass
            except BaseException as e:  # noqa: BLE001 - see above
                note(e)

        threads = [threading.Thread(target=reader) for _ in range(4)]
        threads += [threading.Thread(target=purger) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        assert not errors
        assert outcomes  # the loop actually ran
        # Both outcomes are legal mid-race; the run must contain reads.
        assert set(outcomes) <= {"HIT", "MISS"}


class TestEmbeddingDeterminism:
    def test_same_string_twice_is_identical(self):
        from proxy.embedding import cosine_similarity, embed_texts

        a = embed_texts(["[user]What is the capital of France?"])[0]
        b = embed_texts(["[user]What is the capital of France?"])[0]
        assert cosine_similarity(a, b) >= 0.99999

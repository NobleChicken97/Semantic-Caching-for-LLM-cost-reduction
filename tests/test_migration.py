"""Phase 7.3 migration tests — pre-BYOK databases upgrade to user-scoped v2."""

from __future__ import annotations

import os
import sqlite3

import pytest

os.environ["MOCK_LLM"] = "true"

LEGACY_SCHEMA = """
CREATE TABLE cache_entries (
    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_text TEXT NOT NULL,
    prompt_hash TEXT NOT NULL UNIQUE,
    prompt_embedding BLOB,
    response_json TEXT NOT NULL,
    model_used TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0,
    last_hit_at REAL
);
CREATE TABLE request_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    prompt_text TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN ('HIT','MISS','BYPASS')),
    matched_entry_id INTEGER,
    similarity_score REAL,
    latency_ms REAL NOT NULL,
    estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (matched_entry_id) REFERENCES cache_entries(entry_id)
);
CREATE INDEX idx_cache_hash ON cache_entries(prompt_hash);
"""


@pytest.fixture
def legacy_db(monkeypatch, tmp_path):
    """A database in the pre-BYOK (Phase 1-6) shape containing one row."""
    path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    conn.execute(
        """
        INSERT INTO cache_entries
            (prompt_text, prompt_hash, response_json, model_used,
             created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("[model]gpt-3.5-turbo\n[user]hi", "deadbeef", "{}", "gpt-3.5-turbo", 1.0, 2.0),
    )
    conn.execute(
        """
        INSERT INTO request_log
            (timestamp, prompt_text, prompt_hash, outcome, latency_ms)
        VALUES (?, ?, ?, ?, ?)
        """,
        (1.0, "[model]gpt-3.5-turbo\n[user]hi", "deadbeef", "HIT", 5.0),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("CACHE_DB_PATH", path)
    from proxy.config import get_settings

    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


class TestUserScopingMigration:
    def test_rebuild_adds_user_id_and_preserves_rows(self, legacy_db):
        from proxy.database import _table_columns, init_db

        init_db()

        conn = sqlite3.connect(legacy_db)
        conn.row_factory = sqlite3.Row
        try:
            assert "user_id" in _table_columns(conn, "cache_entries")
            assert "user_id" in _table_columns(conn, "request_log")

            cache_row = conn.execute("SELECT * FROM cache_entries").fetchone()
            assert cache_row["user_id"] == "local"
            log_row = conn.execute("SELECT * FROM request_log").fetchone()
            assert log_row["user_id"] == "local"
            assert log_row["outcome"] == "HIT"  # data survived intact

            # Composite uniqueness (Phase 9: prompt_hash is model-free, so the
            # key is (prompt_hash, user_id, model_used)). Same triple again
            # for 'local' is rejected (legacy row uses model gpt-3.5-turbo)...
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO cache_entries
                        (prompt_text, prompt_hash, response_json, model_used,
                         user_id, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("dup", "deadbeef", "{}", "gpt-3.5-turbo", "local", 3.0, 4.0),
                )
            # ...but the SAME hash is allowed for a DIFFERENT user...
            conn.execute(
                """
                INSERT INTO cache_entries
                    (prompt_text, prompt_hash, response_json, model_used,
                     user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("dup", "deadbeef", "{}", "m", "someone-else", 3.0, 4.0),
            )
            # ...and for the SAME user under a DIFFERENT model.
            conn.execute(
                """
                INSERT INTO cache_entries
                    (prompt_text, prompt_hash, response_json, model_used,
                     user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("dup", "deadbeef", "{}", "other-model", "local", 3.0, 4.0),
            )
        finally:
            conn.close()

    def test_init_db_is_idempotent_after_migration(self, legacy_db):
        from proxy.database import init_db

        init_db()
        init_db()  # must not raise or duplicate anything

        conn = sqlite3.connect(legacy_db)
        try:
            count = conn.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0]
        finally:
            conn.close()
        assert count == 1

    def test_fresh_database_gets_v2_schema_directly(self, tmp_path, monkeypatch):
        path = str(tmp_path / "fresh.db")
        monkeypatch.setenv("CACHE_DB_PATH", path)
        from proxy.config import get_settings
        from proxy.database import _table_columns, init_db

        get_settings.cache_clear()
        try:
            init_db()
            conn = sqlite3.connect(path)
            try:
                assert "user_id" in _table_columns(conn, "cache_entries")
                assert "user_id" in _table_columns(conn, "request_log")
                indexes = {
                    r[1]
                    for r in conn.execute("PRAGMA index_list(cache_entries)").fetchall()
                }
                assert "idx_cache_hash_user_model" in indexes
                # The pre-Phase-9 two-column index must be gone: it would
                # reject legitimate same-prompt-different-model rows.
                assert "idx_cache_hash_user" not in indexes
            finally:
                conn.close()
        finally:
            get_settings.cache_clear()

    def test_stale_outcome_check_is_rebuilt_and_rows_preserved(self, legacy_db):
        """Databases created before the upstream-error contract keep a
        CHECK(outcome IN ('HIT','MISS','BYPASS')) — writing an ERROR row into
        them raised IntegrityError (surfaced as a raw HTTP 500). init_db must
        detect the stale CHECK, rebuild the table, and preserve every row."""
        from proxy.database import _request_log_check_is_stale, init_db

        conn = sqlite3.connect(legacy_db)
        assert _request_log_check_is_stale(conn)  # fixture is pre-ERROR shape
        conn.close()

        init_db()

        conn = sqlite3.connect(legacy_db)
        conn.row_factory = sqlite3.Row
        try:
            assert not _request_log_check_is_stale(conn)
            # The historical HIT row survived the rebuild.
            row = conn.execute("SELECT * FROM request_log").fetchone()
            assert row["outcome"] == "HIT"
            assert row["latency_ms"] == 5.0
            # The whole point: an ERROR row is now writable.
            conn.execute(
                """
                INSERT INTO request_log
                    (timestamp, prompt_text, prompt_hash, outcome, latency_ms)
                VALUES (?, ?, ?, 'ERROR', ?)
                """,
                (2.0, "failed prompt", "cafebad", 12.0),
            )
            assert conn.execute("SELECT COUNT(*) FROM request_log").fetchone()[0] == 2
        finally:
            conn.close()

    def test_partially_migrated_db_still_gets_check_rebuild(self, legacy_db):
        """The real-world case: Phase 7 already ALTERed user_id into
        request_log, so the user_id branch is a no-op — the CHECK rebuild
        must still fire (this exact DB shape shipped the bug)."""
        from proxy.database import _request_log_check_is_stale, _table_columns, init_db

        # Simulate the partial state: user_id present, CHECK still stale.
        conn = sqlite3.connect(legacy_db)
        conn.execute(
            "ALTER TABLE request_log ADD COLUMN user_id TEXT NOT NULL DEFAULT 'local'"
        )
        conn.commit()
        conn.close()

        init_db()

        conn = sqlite3.connect(legacy_db)
        try:
            assert "user_id" in _table_columns(conn, "request_log")
            assert not _request_log_check_is_stale(conn)
            conn.execute(
                "INSERT INTO request_log (timestamp, prompt_text, prompt_hash, "
                "outcome, latency_ms) VALUES (2.0, 'x', 'y', 'ERROR', 1.0)"
            )
        finally:
            conn.close()

    def test_check_rebuild_is_idempotent(self, legacy_db):
        from proxy.database import _request_log_check_is_stale, init_db

        init_db()
        init_db()  # second pass: stale check already fixed — must be a no-op

        conn = sqlite3.connect(legacy_db)
        try:
            assert not _request_log_check_is_stale(conn)
            assert conn.execute("SELECT COUNT(*) FROM request_log").fetchone()[0] == 1
        finally:
            conn.close()

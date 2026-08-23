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

            # Composite uniqueness: same hash again for 'local' is rejected...
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO cache_entries
                        (prompt_text, prompt_hash, response_json, model_used,
                         user_id, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("dup", "deadbeef", "{}", "m", "local", 3.0, 4.0),
                )
            # ...but the SAME hash is allowed for a DIFFERENT user.
            conn.execute(
                """
                INSERT INTO cache_entries
                    (prompt_text, prompt_hash, response_json, model_used,
                     user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("dup", "deadbeef", "{}", "m", "someone-else", 3.0, 4.0),
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
                assert "idx_cache_hash_user" in indexes
            finally:
                conn.close()
        finally:
            get_settings.cache_clear()

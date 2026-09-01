"""SQLite database setup, migrations, and helper queries."""

import sqlite3
from pathlib import Path

from .config import get_settings


def _db_path() -> Path:
    return Path(get_settings().cache_db_path)


def get_connection() -> sqlite3.Connection:
    """Return a new connection for the current thread/request."""
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Final schema (Phase 7 / schema v2): every cache entry and log row is scoped
# to a derived user_id. prompt_hash is intentionally NOT inline-UNIQUE any
# more — two users may cache the identical prompt, so uniqueness is the
# composite (prompt_hash, user_id) index below.
_SCHEMA_V2 = """
    CREATE TABLE IF NOT EXISTS cache_entries (
        entry_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        prompt_text     TEXT    NOT NULL,
        prompt_hash     TEXT    NOT NULL,  -- SHA-256 of canonical prompt
        prompt_embedding BLOB,
        response_json   TEXT    NOT NULL,  -- full OpenAI-shaped response JSON
        model_used      TEXT    NOT NULL,
        user_id         TEXT    NOT NULL DEFAULT 'local',
        created_at      REAL    NOT NULL,
        expires_at      REAL    NOT NULL,
        hit_count       INTEGER NOT NULL DEFAULT 0,
        last_hit_at     REAL
    );

    -- 'ERROR' marks failed upstream calls (no fabricated cost/tokens).
    -- NOTE: CREATE TABLE IF NOT EXISTS means pre-existing databases keep the
    -- old constraint until recreated; _migrate_user_scoping detects and
    -- rebuilds such tables.
    CREATE TABLE IF NOT EXISTS request_log (
        log_id            INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp         REAL    NOT NULL,
        prompt_text       TEXT    NOT NULL,
        prompt_hash       TEXT    NOT NULL,
        outcome           TEXT    NOT NULL CHECK(outcome IN ('HIT','MISS','BYPASS','ERROR')),
        matched_entry_id  INTEGER,
        similarity_score  REAL,
        latency_ms        REAL    NOT NULL,
        estimated_cost_usd REAL   NOT NULL DEFAULT 0.0,
        tokens_in         INTEGER NOT NULL DEFAULT 0,
        tokens_out        INTEGER NOT NULL DEFAULT 0,
        user_id           TEXT    NOT NULL DEFAULT 'local',
        FOREIGN KEY (matched_entry_id)
            REFERENCES cache_entries(entry_id)
    );

    CREATE TABLE IF NOT EXISTS labeled_test_pairs (
        pair_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        prompt_a    TEXT    NOT NULL,
        prompt_b    TEXT    NOT NULL,
        should_match INTEGER NOT NULL CHECK(should_match IN (0, 1))
    );

    -- Phase 7.6: permanent daily rollup. Raw request_log rows are pruned
    -- after LOG_RETENTION_DAYS, but their aggregates live here forever so
    -- lifetime totals never regress.
    CREATE TABLE IF NOT EXISTS daily_metrics (
        date           TEXT    PRIMARY KEY,  -- UTC date, e.g. 2026-08-23
        total_requests INTEGER NOT NULL DEFAULT 0,
        hits           INTEGER NOT NULL DEFAULT 0,
        tokens_saved   INTEGER NOT NULL DEFAULT 0,
        cost_saved_usd REAL    NOT NULL DEFAULT 0.0
    );
"""


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [row[1] for row in rows]


def _migrate_user_scoping(conn: sqlite3.Connection) -> None:
    """Bring pre-Phase-7 databases up to schema v2 (idempotent).

    Two situations handled:
      * Brand-new DBs: _SCHEMA_V2 already created v2 tables -> no-op.
      * Legacy DBs: tables exist WITHOUT user_id (and cache_entries carries an
        inline UNIQUE(prompt_hash)). SQLite cannot ALTER constraints, so
        cache_entries is rebuilt row-by-row with every historical row assigned
        to the 'local' user (pre-BYOK deployments were single-user).
        request_log gets the user_id column added — AND is rebuilt wholesale
        if its outcome CHECK still lacks 'ERROR' (databases created before
        the upstream-error contract keep a CHECK(outcome IN
        ('HIT','MISS','BYPASS')); without the rebuild, every failed-upstream
        log write raises IntegrityError and surfaces as a raw HTTP 500).
    """
    fk_was_on = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    # Rebuilding a parent table fails under FK enforcement when child rows
    # reference it; migration re-checks integrity by copying rows explicitly.
    conn.execute("PRAGMA foreign_keys=OFF")

    try:
        if "cache_entries" in _existing_tables(conn) and (
            "user_id" not in _table_columns(conn, "cache_entries")
        ):
            conn.executescript(
                """
                CREATE TABLE cache_entries_v2 (
                    entry_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    prompt_text     TEXT    NOT NULL,
                    prompt_hash     TEXT    NOT NULL,
                    prompt_embedding BLOB,
                    response_json   TEXT    NOT NULL,
                    model_used      TEXT    NOT NULL,
                    user_id         TEXT    NOT NULL DEFAULT 'local',
                    created_at      REAL    NOT NULL,
                    expires_at      REAL    NOT NULL,
                    hit_count       INTEGER NOT NULL DEFAULT 0,
                    last_hit_at     REAL
                );
                INSERT INTO cache_entries_v2
                    (prompt_text, prompt_hash, prompt_embedding, response_json,
                     model_used, user_id, created_at, expires_at, hit_count, last_hit_at)
                SELECT prompt_text, prompt_hash, prompt_embedding, response_json,
                       model_used, 'local', created_at, expires_at, hit_count, last_hit_at
                  FROM cache_entries;
                DROP TABLE cache_entries;
                ALTER TABLE cache_entries_v2 RENAME TO cache_entries;
                """
            )

        if "request_log" in _existing_tables(conn) and (
            "user_id" not in _table_columns(conn, "request_log")
        ):
            conn.execute(
                "ALTER TABLE request_log "
                "ADD COLUMN user_id TEXT NOT NULL DEFAULT 'local'"
            )

        if _request_log_check_is_stale(conn):
            # outcome CHECK predates the upstream-error contract. SQLite
            # cannot ALTER a CHECK constraint: rebuild the table, preserving
            # every row. request_log is a child table (nothing references
            # it), so the rebuild is FK-safe under foreign_keys=OFF.
            conn.executescript(
                """
                CREATE TABLE request_log_v2 (
                    log_id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp         REAL    NOT NULL,
                    prompt_text       TEXT    NOT NULL,
                    prompt_hash       TEXT    NOT NULL,
                    outcome           TEXT    NOT NULL
                        CHECK(outcome IN ('HIT','MISS','BYPASS','ERROR')),
                    matched_entry_id  INTEGER,
                    similarity_score  REAL,
                    latency_ms        REAL    NOT NULL,
                    estimated_cost_usd REAL   NOT NULL DEFAULT 0.0,
                    tokens_in         INTEGER NOT NULL DEFAULT 0,
                    tokens_out        INTEGER NOT NULL DEFAULT 0,
                    user_id           TEXT    NOT NULL DEFAULT 'local',
                    FOREIGN KEY (matched_entry_id)
                        REFERENCES cache_entries(entry_id)
                );
                INSERT INTO request_log_v2
                    (log_id, timestamp, prompt_text, prompt_hash, outcome,
                     matched_entry_id, similarity_score, latency_ms,
                     estimated_cost_usd, tokens_in, tokens_out, user_id)
                SELECT log_id, timestamp, prompt_text, prompt_hash, outcome,
                       matched_entry_id, similarity_score, latency_ms,
                       estimated_cost_usd, tokens_in, tokens_out, user_id
                  FROM request_log;
                DROP TABLE request_log;
                ALTER TABLE request_log_v2 RENAME TO request_log;
                """
            )
    finally:
        if fk_was_on:
            conn.execute("PRAGMA foreign_keys=ON")


def _request_log_check_is_stale(conn: sqlite3.Connection) -> bool:
    """True when request_log's outcome CHECK predates the 'ERROR' outcome.

    Reads the stored CREATE TABLE sql: legacy databases carry
    CHECK(outcome IN ('HIT','MISS','BYPASS')) — writing an ERROR row into
    them raises IntegrityError. Brand-new v2 tables contain 'ERROR' and
    never enter this path.
    """
    if "request_log" not in _existing_tables(conn):
        return False
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='request_log'"
    ).fetchone()
    return "'ERROR'" not in (row[0] or "").upper()


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


def init_db() -> None:
    """Create/migrate tables as needed (idempotent)."""
    conn = get_connection()
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.executescript(_SCHEMA_V2)
        _migrate_user_scoping(conn)

        # Composite uniqueness: same hash is fine across users, never twice
        # for one user. Also serves plain prompt_hash lookups (prefix rule).
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_cache_hash_user
                ON cache_entries(prompt_hash, user_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_log_timestamp
                ON request_log(timestamp)
            """
        )
        conn.commit()
    finally:
        conn.close()


def seed_test_pairs() -> None:
    """Insert a small set of labeled test pairs for Phase 3 validation."""
    conn = get_connection()
    try:
        existing = conn.execute("SELECT COUNT(*) FROM labeled_test_pairs").fetchone()[0]
        if existing > 0:
            return
        pairs = [
            # ---- should_match = 1 (paraphrases) ----
            ("What is the capital of France?", "Tell me the capital of France.", 1),
            (
                "How do I reset my password?",
                "I forgot my password, how can I reset it?",
                1,
            ),
            (
                "Summarize the plot of Inception.",
                "Can you give me a summary of the movie Inception?",
                1,
            ),
            ("What is 2 + 2?", "Calculate two plus two.", 1),
            (
                "Explain quantum computing in simple terms.",
                "Give me a simple explanation of quantum computing.",
                1,
            ),
            ("Write a haiku about the ocean.", "Compose a haiku on the sea.", 1),
            ("What year did WWII end?", "When did World War II finish?", 1),
            ("Translate 'hello' to Spanish.", "How do you say hello in Spanish?", 1),
            (
                "List three benefits of exercise.",
                "Name three advantages of working out.",
                1,
            ),
            (
                "Define machine learning.",
                "What is the definition of machine learning?",
                1,
            ),
            # ---- should_match = 0 (near-misses / different intent) ----
            ("What is the capital of France?", "What is the population of France?", 0),
            ("Summarize the plot of Inception.", "Who directed Inception?", 0),
            ("How do I reset my password?", "How do I change my username?", 0),
            ("What is 2 + 2?", "What is the square root of 16?", 0),
            ("Write a haiku about the ocean.", "Write a limerick about the ocean.", 0),
            (
                "Explain quantum computing in simple terms.",
                "Explain classical computing in simple terms.",
                0,
            ),
            ("What year did WWII end?", "What year did WWI start?", 0),
            ("Translate 'hello' to Spanish.", "Translate 'goodbye' to Spanish.", 0),
            (
                "List three benefits of exercise.",
                "List three risks of over-exercising.",
                0,
            ),
            ("Define machine learning.", "Define deep learning.", 0),
            # ---- edge cases: very short prompts ----
            ("What is AI?", "Define artificial intelligence.", 1),
            ("Hi", "Goodbye", 0),
            # ---- edge cases: typos ----
            ("What is the capital of France?", "What is the captial of France?", 1),
            # ---- edge cases: code snippets ----
            (
                "Explain what this Python code does: sorted(items, key=len)",
                "What does the Python expression sorted(items, key=len) do?",
                1,
            ),
            ("def add(a, b): return a + b", "def multiply(a, b): return a * b", 0),
            # ---- additional paraphrases ----
            ("How do I make coffee?", "What's the best way to brew coffee?", 1),
            (
                "My laptop won't turn on.",
                "My laptop does not start when I press the power button.",
                1,
            ),
            (
                "Recommend a good sci-fi book.",
                "Can you suggest a great science fiction novel?",
                1,
            ),
            ("Fix my bicycle tire.", "Translate 'good morning' to French.", 0),
            (
                "Best programming language for beginners?",
                "Give me a brief history of the Roman Empire.",
                0,
            ),
            (
                "How do I bake chocolate chip cookies?",
                "How do I change a flat tire on a car?",
                0,
            ),
        ]
        conn.executemany(
            "INSERT INTO labeled_test_pairs (prompt_a, prompt_b, should_match) VALUES (?, ?, ?)",
            pairs,
        )
        conn.commit()
    finally:
        conn.close()

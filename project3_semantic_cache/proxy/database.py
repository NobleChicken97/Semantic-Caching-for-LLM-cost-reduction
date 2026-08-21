"""SQLite database setup, migrations, and helper queries."""

import sqlite3
import time
from pathlib import Path

from .config import settings


def _db_path() -> Path:
    return Path(settings.cache_db_path)


def get_connection() -> sqlite3.Connection:
    """Return a new connection for the current thread/request."""
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create tables if they don't already exist (idempotent)."""
    conn = get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS cache_entries (
                entry_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt_text     TEXT    NOT NULL,
                prompt_hash     TEXT    NOT NULL UNIQUE,  -- SHA-256 of canonical prompt
                prompt_embedding BLOB,                    -- NULL in Phase 1
                response_json   TEXT    NOT NULL,          -- full OpenAI-shaped response JSON
                model_used      TEXT    NOT NULL,
                created_at      REAL    NOT NULL,
                expires_at      REAL    NOT NULL,
                hit_count       INTEGER NOT NULL DEFAULT 0,
                last_hit_at     REAL
            );

            CREATE INDEX IF NOT EXISTS idx_cache_hash
                ON cache_entries(prompt_hash);

            CREATE TABLE IF NOT EXISTS request_log (
                log_id            INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp         REAL    NOT NULL,
                prompt_text       TEXT    NOT NULL,
                prompt_hash       TEXT    NOT NULL,
                outcome           TEXT    NOT NULL CHECK(outcome IN ('HIT','MISS','BYPASS')),
                matched_entry_id  INTEGER,
                similarity_score  REAL,
                latency_ms        REAL    NOT NULL,
                estimated_cost_usd REAL   NOT NULL DEFAULT 0.0,
                tokens_in         INTEGER NOT NULL DEFAULT 0,
                tokens_out        INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (matched_entry_id)
                    REFERENCES cache_entries(entry_id)
            );

            CREATE INDEX IF NOT EXISTS idx_log_timestamp
                ON request_log(timestamp);

            CREATE TABLE IF NOT EXISTS labeled_test_pairs (
                pair_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt_a    TEXT    NOT NULL,
                prompt_b    TEXT    NOT NULL,
                should_match INTEGER NOT NULL CHECK(should_match IN (0, 1))
            );
        """)
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
            ("How do I reset my password?", "I forgot my password, how can I reset it?", 1),
            ("Summarize the plot of Inception.", "Can you give me a summary of the movie Inception?", 1),
            ("What is 2 + 2?", "Calculate two plus two.", 1),
            ("Explain quantum computing in simple terms.", "Give me a simple explanation of quantum computing.", 1),
            ("Write a haiku about the ocean.", "Compose a haiku on the sea.", 1),
            ("What year did WWII end?", "When did World War II finish?", 1),
            ("Translate 'hello' to Spanish.", "How do you say hello in Spanish?", 1),
            ("List three benefits of exercise.", "Name three advantages of working out.", 1),
            ("Define machine learning.", "What is the definition of machine learning?", 1),
            # ---- should_match = 0 (near-misses / different intent) ----
            ("What is the capital of France?", "What is the population of France?", 0),
            ("Summarize the plot of Inception.", "Who directed Inception?", 0),
            ("How do I reset my password?", "How do I change my username?", 0),
            ("What is 2 + 2?", "What is the square root of 16?", 0),
            ("Write a haiku about the ocean.", "Write a limerick about the ocean.", 0),
            ("Explain quantum computing in simple terms.", "Explain classical computing in simple terms.", 0),
            ("What year did WWII end?", "What year did WWI start?", 0),
            ("Translate 'hello' to Spanish.", "Translate 'goodbye' to Spanish.", 0),
            ("List three benefits of exercise.", "List three risks of over-exercising.", 0),
            ("Define machine learning.", "Define deep learning.", 0),
        ]
        conn.executemany(
            "INSERT INTO labeled_test_pairs (prompt_a, prompt_b, should_match) VALUES (?, ?, ?)",
            pairs,
        )
        conn.commit()
    finally:
        conn.close()
"""Cache layer — exact-string-match for Phase 1.

In Phase 2 this will be extended with embedding-based semantic matching.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, Optional

from .config import settings
from .database import get_connection


def _hash_prompt(prompt: str) -> str:
    """SHA-256 of the canonical prompt string."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def lookup(prompt_text: str) -> Optional[Dict[str, Any]]:
    """Exact-match cache lookup by prompt hash.

    Returns the cached response dict if found and not expired, else None.
    """
    prompt_hash = _hash_prompt(prompt_text)
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT entry_id, response_json, expires_at
              FROM cache_entries
             WHERE prompt_hash = ?
          ORDER BY created_at DESC
             LIMIT 1
            """,
            (prompt_hash,),
        ).fetchone()

        if row is None:
            return None

        # TTL check
        if time.time() > row["expires_at"]:
            _delete_entry(conn, row["entry_id"])
            return None

        # Update hit stats
        conn.execute(
            "UPDATE cache_entries SET hit_count = hit_count + 1, last_hit_at = ? WHERE entry_id = ?",
            (time.time(), row["entry_id"]),
        )
        conn.commit()

        return {
            "entry_id": row["entry_id"],
            "response": json.loads(row["response_json"]),
        }
    finally:
        conn.close()


def store(
    prompt_text: str, response_dict: Dict[str, Any], model_used: str
) -> int:
    """Store a new cache entry. Returns the new entry_id."""
    prompt_hash = _hash_prompt(prompt_text)
    now = time.time()
    expires_at = now + settings.cache_default_ttl_seconds

    conn = get_connection()
    try:
        # Replace any existing entry for this exact prompt hash
        conn.execute("DELETE FROM cache_entries WHERE prompt_hash = ?", (prompt_hash,))

        cursor = conn.execute(
            """
            INSERT INTO cache_entries
                (prompt_text, prompt_hash, response_json, model_used,
                 created_at, expires_at, hit_count)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            (
                prompt_text,
                prompt_hash,
                json.dumps(response_dict, ensure_ascii=False),
                model_used,
                now,
                expires_at,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def _delete_entry(conn, entry_id: int) -> None:
    conn.execute("DELETE FROM cache_entries WHERE entry_id = ?", (entry_id,))
    conn.commit()


def purge(entry_id: Optional[int] = None) -> int:
    """Purge a single entry or the entire cache. Returns count deleted.

    Nulls out foreign-key references in request_log before deleting,
    so we don't lose the request history when purging cache entries.
    """
    conn = get_connection()
    try:
        if entry_id is not None:
            conn.execute(
                "UPDATE request_log SET matched_entry_id = NULL WHERE matched_entry_id = ?",
                (entry_id,),
            )
            cursor = conn.execute(
                "DELETE FROM cache_entries WHERE entry_id = ?", (entry_id,)
            )
        else:
            conn.execute("UPDATE request_log SET matched_entry_id = NULL")
            cursor = conn.execute("DELETE FROM cache_entries")
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def log_request(
    prompt_text: str,
    outcome: str,
    latency_ms: float,
    matched_entry_id: Optional[int] = None,
    similarity_score: Optional[float] = None,
    estimated_cost_usd: float = 0.0,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> None:
    """Write a row into the request_log table."""
    prompt_hash = _hash_prompt(prompt_text)
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO request_log
                (timestamp, prompt_text, prompt_hash, outcome,
                 matched_entry_id, similarity_score, latency_ms,
                 estimated_cost_usd, tokens_in, tokens_out)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                prompt_text,
                prompt_hash,
                outcome,
                matched_entry_id,
                similarity_score,
                latency_ms,
                estimated_cost_usd,
                tokens_in,
                tokens_out,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_metrics() -> Dict[str, Any]:
    """Aggregate metrics from the request_log."""
    conn = get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM request_log"
        ).fetchone()[0]
        hits = conn.execute(
            "SELECT COUNT(*) FROM request_log WHERE outcome = 'HIT'"
        ).fetchone()[0]

        hit_rate = hits / total if total > 0 else 0.0

        cost_saved = conn.execute(
            """
            SELECT COALESCE(SUM(estimated_cost_usd), 0)
              FROM request_log
             WHERE outcome = 'HIT'
            """
        ).fetchone()[0]

        hit_lat = conn.execute(
            """
            SELECT COALESCE(AVG(latency_ms), 0)
              FROM request_log
             WHERE outcome = 'HIT'
            """
        ).fetchone()[0]

        miss_lat = conn.execute(
            """
            SELECT COALESCE(AVG(latency_ms), 0)
              FROM request_log
             WHERE outcome = 'MISS'
            """
        ).fetchone()[0]

        return {
            "hit_rate": round(hit_rate, 4),
            "total_requests": total,
            "estimated_cost_saved_usd": round(cost_saved, 6),
            "avg_latency_hit_ms": round(hit_lat, 2),
            "avg_latency_miss_ms": round(miss_lat, 2),
        }
    finally:
        conn.close()
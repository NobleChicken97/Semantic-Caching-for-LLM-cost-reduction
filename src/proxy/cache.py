"""Cache layer — exact-match + semantic (embedding-based) lookup.

Phase 2: Adds BGE-small embedding generation and cosine similarity search.
Exact match (by SHA-256 hash) is tried first; on miss, falls back to a
semantic nearest-neighbor search over stored embeddings.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, List, Optional

import numpy as np

from .config import settings
from .database import get_connection


def _hash_prompt(prompt: str) -> str:
    """SHA-256 of the canonical prompt string."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _serialize_embedding(vec: np.ndarray) -> bytes:
    """Pack a float32 numpy array to raw bytes for SQLite storage."""
    return vec.astype(np.float32).tobytes()


def _deserialize_embedding(blob: bytes) -> np.ndarray:
    """Unpack raw bytes back to a float32 numpy array."""
    return np.frombuffer(blob, dtype=np.float32)


# ---------------------------------------------------------------------------
# Lookup: exact match -> semantic fallback
# ---------------------------------------------------------------------------


def lookup(
    prompt_text: str,
    *,
    threshold: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Two-tier lookup: exact hash FIRST, then semantic nearest-neighbor.

    Returns ``{"entry_id", "response", "similarity_score"}`` on a hit,
    or ``None`` on a miss.
    """
    threshold = threshold if threshold is not None else settings.similarity_threshold

    # --- Tier 1: exact string match ---
    exact = _exact_lookup(prompt_text)
    if exact is not None:
        exact["similarity_score"] = 1.0
        return exact

    # --- Tier 2: semantic search ---
    return _semantic_lookup(prompt_text, threshold)


def _exact_lookup(prompt_text: str) -> Optional[Dict[str, Any]]:
    """Return the cached response for an exact hash match, or None."""
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


def _semantic_lookup(
    prompt_text: str,
    threshold: float,
) -> Optional[Dict[str, Any]]:
    """Embed the prompt and find the nearest cached entry by cosine similarity.

    Returns the cached response only if similarity >= threshold.
    """
    from .embedding import cosine_similarity, embed_texts

    query_vec = embed_texts([prompt_text])[0]

    conn = get_connection()
    try:
        # Fetch all non-expired entries that HAVE an embedding
        now = time.time()
        rows = conn.execute(
            """
            SELECT entry_id, prompt_embedding, response_json, expires_at
              FROM cache_entries
             WHERE prompt_embedding IS NOT NULL
               AND expires_at > ?
            """,
            (now,),
        ).fetchall()

        best_score = -1.0
        best_entry = None

        for row in rows:
            try:
                stored_vec = _deserialize_embedding(row["prompt_embedding"])
            except (ValueError, TypeError):
                continue  # skip corrupt embedding

            score = cosine_similarity(query_vec, stored_vec)
            if score > best_score:
                best_score = score
                if score >= threshold:
                    best_entry = {
                        "entry_id": row["entry_id"],
                        "response": json.loads(row["response_json"]),
                    }

        if best_entry is None:
            return None

        # Update hit stats on the winning entry
        conn.execute(
            "UPDATE cache_entries SET hit_count = hit_count + 1, last_hit_at = ? WHERE entry_id = ?",
            (now, best_entry["entry_id"]),
        )
        conn.commit()

        best_entry["similarity_score"] = round(best_score, 6)
        return best_entry
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def store(
    prompt_text: str,
    response_dict: Dict[str, Any],
    model_used: str,
) -> int:
    """Store a new cache entry with its embedding. Returns the new entry_id."""
    from .embedding import embed_texts

    prompt_hash = _hash_prompt(prompt_text)
    embedding = embed_texts([prompt_text])[0]
    now = time.time()
    expires_at = now + settings.cache_default_ttl_seconds

    conn = get_connection()
    try:
        # Replace any existing entry for this exact prompt hash
        conn.execute(
            "DELETE FROM cache_entries WHERE prompt_hash = ?", (prompt_hash,)
        )

        cursor = conn.execute(
            """
            INSERT INTO cache_entries
                (prompt_text, prompt_hash, prompt_embedding, response_json,
                 model_used, created_at, expires_at, hit_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                prompt_text,
                prompt_hash,
                _serialize_embedding(embedding),
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


# ---------------------------------------------------------------------------
# Helpers (unchanged from Phase 1)
# ---------------------------------------------------------------------------


def _detach_log_references(conn, entry_ids_sql: str, params: tuple = ()) -> None:
    """Null out request_log.matched_entry_id pointing at soon-deleted entries.

    request_log has a FOREIGN KEY to cache_entries(entry_id) and connections
    run with PRAGMA foreign_keys=ON, so deleting cache entries without
    detaching these references raises IntegrityError. Log rows are kept
    (only the reference is cleared) so metrics history survives purges.
    """
    conn.execute(
        f"UPDATE request_log SET matched_entry_id = NULL WHERE matched_entry_id IN ({entry_ids_sql})",
        params,
    )


def _delete_entry(conn, entry_id: int) -> None:
    _detach_log_references(conn, "?", (entry_id,))
    conn.execute("DELETE FROM cache_entries WHERE entry_id = ?", (entry_id,))
    conn.commit()

def purge(entry_id: Optional[int] = None) -> int:
    """Purge a single entry or the entire cache. Returns count deleted."""
    conn = get_connection()
    try:
        if entry_id is not None:
            _detach_log_references(conn, "?", (entry_id,))
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
        total = conn.execute("SELECT COUNT(*) FROM request_log").fetchone()[0]
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


def list_cache_entries(search: Optional[str] = None) -> List[Dict[str, Any]]:
    """List cache entries newest-first for the dashboard cache browser.

    ``search`` does a substring match on prompt_text when provided.
    """
    conn = get_connection()
    try:
        base = """
            SELECT entry_id, prompt_text, model_used, created_at, expires_at,
                   hit_count, last_hit_at
              FROM cache_entries
        """
        if search:
            rows = conn.execute(
                base + " WHERE prompt_text LIKE ? ORDER BY created_at DESC",
                (f"%{search}%",),
            ).fetchall()
        else:
            rows = conn.execute(base + " ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def recent_logs(limit: int = 50) -> List[Dict[str, Any]]:
    """Return the most recent request_log rows, newest first."""
    limit = max(1, min(limit, 500))
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT log_id, timestamp, prompt_text, outcome, matched_entry_id,
                   similarity_score, latency_ms, estimated_cost_usd,
                   tokens_in, tokens_out
              FROM request_log
          ORDER BY timestamp DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

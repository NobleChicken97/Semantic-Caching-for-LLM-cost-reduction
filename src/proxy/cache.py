"""Cache layer — exact-match + semantic (embedding-based) lookup.

Phase 2: Adds BGE-small embedding generation and cosine similarity search.
Exact match (by SHA-256 hash) is tried first; on miss, falls back to a
semantic nearest-neighbor search over stored embeddings.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from typing import Any

import numpy as np

from .config import get_settings
from .database import get_connection
from .text import (
    antonym_swapped_equal,
    content_words,
    entities,
    fact_types,
    has_negation,
    jaccard,
    number_tokens,
    strip_tags,
    template_jaccard,
    typo_bridged,
)

logger = logging.getLogger("proxy")

# Veto bands (Phase 9, all calibrated in scripts/calibrate_trust.py against
# the 31 labeled pairs — zero positives may fire; the script is the proof).
# ENTITY_TEMPLATE_FLOOR gates signal 1 so aliasing entities ("WWII" vs
# "World War II", template overlap 0.0) never veto a true paraphrase.
ENTITY_TEMPLATE_FLOOR = 0.2
# Fix C band: content overlap at/below MAX with template overlap at/above
# MIN means "same skeleton, different content words" — unless every differing
# word has a near-duplicate across (typo bridge). Calibrated: the "captial"
# typo pair sits at (0.33, 0.71) and is bridged; verb swaps sit at the same
# coordinates unbridged and must veto; nearest labeled positive below MIN is
# template 0.333, nearest target above it 0.5 — MIN=0.4 splits both with margin.
VETO_CONTENT_MAX = 0.34
VETO_TEMPLATE_MIN = 0.4

# Warn only once per process when the semantic scan exceeds the configured
# entry cap — a slow-degradation tripwire, not a hard failure.
_scan_limit_warned = False


def _hash_prompt(prompt: str) -> str:
    """SHA-256 of the canonical prompt string."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def semantic_veto(query_text: str, candidate_text: str) -> bool:
    """True when a semantic HIT must be refused (Phase 9, Fix B/C/D).

    Five independent signals; any one fires the veto. Every signal requires
    evidence on BOTH sides (single-side absence can never veto) and every
    constant below was calibrated against the 31 labeled pairs with zero
    positives firing (see scripts/calibrate_trust.py):
      1. entity swap — disjoint capitalized-token sets (sentence-initial
         excluded) plus shared template (Jaccard >= ENTITY_TEMPLATE_FLOOR).
      2. fact-type swap — disjoint keyword sets, ungated (no labeled
         positive carries disjoint fact keywords).
      3. template collision (Fix C) — content overlap <= VETO_CONTENT_MAX
         with template overlap >= VETO_TEMPLATE_MIN, unless a typo bridge
         connects every differing word (difflib >= 0.8 both directions).
      4. negation/antonym (Fix D) — negation markers mismatched, or token
         multisets equal after exactly one listed antonym substitution.
      5. number mismatch — both sides carry digit tokens and the sets differ
         ("15/200" vs "20/200"; "2" vs "2" is safe).
    Returns False for everything else — the cosine threshold remains the
    primary gate, and vetoed lookups surface as MISS.
    """
    query, candidate = strip_tags(query_text), strip_tags(candidate_text)
    qe, ce = entities(query), entities(candidate)
    if (
        qe
        and ce
        and not (qe & ce)
        and jaccard(query, candidate) >= ENTITY_TEMPLATE_FLOOR
    ):
        return True
    qf, cf = fact_types(query), fact_types(candidate)
    if qf and cf and not (qf & cf):
        return True
    if (
        jaccard(query, candidate) <= VETO_CONTENT_MAX
        and template_jaccard(query, candidate) >= VETO_TEMPLATE_MIN
        and not typo_bridged(query, candidate)
    ):
        return True
    if has_negation(query) != has_negation(candidate):
        return True
    if antonym_swapped_equal(query, candidate):
        return True
    qn, cn = number_tokens(query), number_tokens(candidate)
    return bool(qn and cn and qn != cn)


def _serialize_embedding(vec: np.ndarray) -> bytes:
    """Pack a float32 numpy array to raw bytes for SQLite storage."""
    return vec.astype(np.float32).tobytes()


def _deserialize_embedding(blob: bytes) -> np.ndarray:
    """Unpack raw bytes back to a unit-length float32 numpy array.

    Validates before use so a corrupt/truncated row can never reach the
    similarity math as a wrong-shape vector (np.frombuffer silently returns
    a SHORTER array for a truncated blob, and np.dot then raises an
    uncaught ValueError mid-scan). Raises ValueError — which
    _semantic_lookup already catches per-row — for:

      * blobs whose float count != embedding_dim() (truncated/garbage),
      * zero-norm or non-finite vectors (division would produce NaN).

    Also re-normalizes defensively: cosine_similarity assumes unit vectors,
    and a stored vector that drifted from unit length would silently
    distort scores instead of failing loudly.
    """
    from .embedding import embedding_dim

    vec = np.frombuffer(blob, dtype=np.float32)
    if vec.size != embedding_dim():
        raise ValueError(
            f"embedding blob has {vec.size} floats, expected {embedding_dim()}"
        )
    norm = float(np.linalg.norm(vec))
    if not np.isfinite(norm) or norm == 0.0:
        raise ValueError("embedding blob is zero-valued or non-finite")
    return (vec / norm).astype(np.float32)


# ---------------------------------------------------------------------------
# Lookup: exact match -> semantic fallback
# ---------------------------------------------------------------------------


def lookup(
    prompt_text: str,
    *,
    threshold: float | None = None,
    model: str | None = None,
    user_id: str = "local",
) -> dict[str, Any] | None:
    """Two-tier lookup: exact hash FIRST, then semantic nearest-neighbor.

    ``model`` scopes the search to entries stored for that same model —
    identical prompts served by different models are distinct cache keys.
    When ``model`` is None the lookup matches any stored model (used by
    direct callers/tests; the HTTP route always passes a model).

    ``user_id`` scopes the search to one caller's entries — responses are
    NEVER shared across users (Phase 7 BYOK requirement).

    Returns ``{"entry_id", "response", "similarity_score"}`` on a hit,
    or ``None`` on a miss.
    """
    threshold = (
        threshold if threshold is not None else get_settings().similarity_threshold
    )

    # --- Tier 1: exact string match ---
    exact = _exact_lookup(prompt_text, model=model, user_id=user_id)
    if exact is not None:
        exact["similarity_score"] = 1.0
        return exact

    # --- Tier 2: semantic search ---
    return _semantic_lookup(prompt_text, threshold, model=model, user_id=user_id)


def _exact_lookup(
    prompt_text: str,
    model: str | None = None,
    user_id: str = "local",
) -> dict[str, Any] | None:
    """Return the cached response for an exact hash + same-model match, or None."""
    prompt_hash = _hash_prompt(prompt_text)
    conn = get_connection()
    try:
        sql = """
            SELECT entry_id, response_json, expires_at
              FROM cache_entries
             WHERE prompt_hash = ?
               AND user_id = ?
        """
        params: list[Any] = [prompt_hash, user_id]
        if model is not None:
            sql += "           AND model_used = ?"
            params.append(model)
        sql += "         ORDER BY created_at DESC\n             LIMIT 1"

        row = conn.execute(sql, tuple(params)).fetchone()

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
    model: str | None = None,
    user_id: str = "local",
) -> dict[str, Any] | None:
    """Embed the prompt and find the nearest cached entry by cosine similarity.

    SCALING NOTE (review fix #6): this is an O(n) full scan over stored
    embeddings — fine up to a few thousand entries, but swap in a real ANN
    index (FAISS / sqlite-vec / pgvector) beyond that.

    Returns the cached response only if similarity >= threshold, restricted
    to entries stored for ``model`` when a model is given (a gpt-4 request
    must never semantically hit an answer generated for gpt-3.5-turbo) and to
    the caller's own ``user_id`` — semantic hits never cross users.
    """
    global _scan_limit_warned

    from .embedding import cosine_similarity, embed_texts

    query_vec = embed_texts([prompt_text])[0]

    conn = get_connection()
    try:
        # Fetch all non-expired entries that HAVE an embedding
        now = time.time()
        sql = """
            SELECT entry_id, prompt_text, prompt_embedding, response_json, expires_at
              FROM cache_entries
             WHERE prompt_embedding IS NOT NULL
               AND expires_at > ?
               AND user_id = ?
        """
        params: list[Any] = [now, user_id]
        if model is not None:
            sql += "           AND model_used = ?"
            params.append(model)
        rows = conn.execute(sql, tuple(params)).fetchall()

        limit = get_settings().max_semantic_scan_entries
        if len(rows) > limit and not _scan_limit_warned:
            logger.warning(
                "Semantic scan is comparing against %d entries "
                "(MAX_SEMANTIC_SCAN_ENTRIES=%d) — O(n) scan latency will grow "
                "linearly; consider an ANN index (FAISS/sqlite-vec/pgvector).",
                len(rows),
                limit,
            )
            _scan_limit_warned = True

        best_score = -1.0
        best_entry = None
        best_prompt = None

        for row in rows:
            # Degenerate candidates (no content words: ":)", "!!!", ...) are
            # skipped: they carry no meaning to match, so any semantic hit
            # against them is noise by construction. Exact-tier repeats
            # still hit — only the semantic tier ignores them. Defense in
            # depth: no live false-hit against such an entry has ever been
            # observed (an early 0.88-0.94 sighting turned out to be shared
            # test-suffix tokens, not the entry), but the rule costs nothing
            # and labeled recall provably cannot regress (all labeled cache
            # entries carry >=1 content word — see tests/test_trust.py).
            if not content_words(strip_tags(row["prompt_text"] or "")):
                continue
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
                    best_prompt = row["prompt_text"]

        if best_entry is None:
            return None

        # Veto (Fix B/C/D): a cleared threshold is necessary but not sufficient.
        # Refused hits return MISS (and are logged as MISS) — the candidate
        # stays stored, so an EXACT repeat still hits via the exact tier.
        if best_prompt is not None and semantic_veto(prompt_text, best_prompt):
            logger.info(
                "Semantic HIT vetoed (entity/fact mismatch above threshold %.3f).",
                best_score,
            )
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
    response_dict: dict[str, Any],
    model_used: str,
    user_id: str = "local",
) -> int:
    """Store a new cache entry with its embedding. Returns the new entry_id.

    Scoped to (``user_id``, ``model_used``): the same prompt may exist for
    many users and many models (the composite UNIQUE(prompt_hash, user_id)
    index plus the model filter enforce one row per user per model), and a
    re-store replaces only that user's entry for that model. Log references
    to the replaced row are detached first so metrics history survives.
    """
    from .embedding import embed_texts

    prompt_hash = _hash_prompt(prompt_text)
    embedding = embed_texts([prompt_text])[0]
    now = time.time()
    expires_at = now + get_settings().cache_default_ttl_seconds

    conn = get_connection()
    try:
        # Replace this user's existing entry for this exact (prompt, model).
        # NOTE: prompt_hash is model-free since Phase 9 (message-only text),
        # so the model predicate is load-bearing here — without it, storing
        # for model B would delete model A's row and trip the log FK.
        old = conn.execute(
            "SELECT entry_id FROM cache_entries "
            "WHERE prompt_hash = ? AND user_id = ? AND model_used = ?",
            (prompt_hash, user_id, model_used),
        ).fetchone()
        if old is not None:
            _detach_log_references(conn, "?", (old["entry_id"],))
            conn.execute(
                "DELETE FROM cache_entries WHERE entry_id = ?", (old["entry_id"],)
            )

        cursor = conn.execute(
            """
            INSERT INTO cache_entries
                (prompt_text, prompt_hash, prompt_embedding, response_json,
                 model_used, user_id, created_at, expires_at, hit_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                prompt_text,
                prompt_hash,
                _serialize_embedding(embedding),
                json.dumps(response_dict, ensure_ascii=False),
                model_used,
                user_id,
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


def purge(entry_id: int | None = None, *, actor: str = "admin") -> int:
    """Purge a single entry or the entire cache. Returns count deleted.

    Writes one audit row (timestamp, count, scope, actor) in the same
    transaction so "who purged, when" is always answerable. ``actor`` is
    the caller IP recorded by the route — there is a single admin token,
    so IP is the best available identity (documented, not oversold).
    """
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
        purged = cursor.rowcount
        conn.execute(
            "INSERT INTO purge_audit (timestamp, purged_count, entry_id, actor)"
            " VALUES (?, ?, ?, ?)",
            (time.time(), purged, entry_id, actor),
        )
        conn.commit()
        return purged
    finally:
        conn.close()


def last_purge() -> dict[str, Any] | None:
    """Newest purge-audit row, or None when nothing was ever purged."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT timestamp, purged_count, entry_id, actor FROM purge_audit"
            " ORDER BY audit_id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def log_request(
    prompt_text: str,
    outcome: str,
    latency_ms: float,
    matched_entry_id: int | None = None,
    similarity_score: float | None = None,
    estimated_cost_usd: float = 0.0,
    tokens_in: int = 0,
    tokens_out: int = 0,
    user_id: str = "local",
) -> None:
    """Write a row into the request_log table.

    Stores the derived ``user_id`` — never the raw caller key.
    """
    prompt_hash = _hash_prompt(prompt_text)
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO request_log
                (timestamp, prompt_text, prompt_hash, outcome,
                 matched_entry_id, similarity_score, latency_ms,
                 estimated_cost_usd, tokens_in, tokens_out, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                user_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def prune_old_logs(days: int = 30, *, now: float | None = None) -> int:
    """Roll request_log rows older than ``days`` into daily_metrics, then delete.

    Phase 7.6 hot/cold retention: raw rows are operational data with a
    30-day window; their aggregates move into the permanent daily_metrics
    table so lifetime totals never regress. Runs in a single transaction —
    re-running is a no-op once rows are gone. Returns the number of raw
    rows pruned.
    """
    cutoff = (now if now is not None else time.time()) - days * 86_400
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO daily_metrics
                (date, total_requests, hits, tokens_saved, cost_saved_usd)
            SELECT date(timestamp, 'unixepoch'),
                   COUNT(*),
                   SUM(CASE WHEN outcome = 'HIT' THEN 1 ELSE 0 END),
                   COALESCE(SUM(CASE WHEN outcome = 'HIT'
                                     THEN tokens_in + tokens_out ELSE 0 END), 0),
                   ROUND(COALESCE(SUM(CASE WHEN outcome = 'HIT'
                                      THEN estimated_cost_usd ELSE 0 END), 0), 6)
              FROM request_log
             WHERE timestamp < ?
             GROUP BY date(timestamp, 'unixepoch')
             ON CONFLICT(date) DO UPDATE SET
                total_requests = total_requests + excluded.total_requests,
                hits           = hits           + excluded.hits,
                tokens_saved   = tokens_saved   + excluded.tokens_saved,
                cost_saved_usd = cost_saved_usd + excluded.cost_saved_usd
            """,
            (cutoff,),
        )
        deleted = conn.execute("DELETE FROM request_log WHERE timestamp < ?", (cutoff,))
        conn.commit()
        return deleted.rowcount
    finally:
        conn.close()


def _rollup_totals(conn: sqlite3.Connection) -> tuple[int, int, int, float]:
    """Lifetime aggregates from the permanent daily_metrics table."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(total_requests), 0),
               COALESCE(SUM(hits), 0),
               COALESCE(SUM(tokens_saved), 0),
               COALESCE(SUM(cost_saved_usd), 0)
          FROM daily_metrics
        """
    ).fetchone()
    return int(row[0]), int(row[1]), int(row[2]), float(row[3])


def get_metrics() -> dict[str, Any]:
    """Aggregate metrics from request_log (+ daily_metrics rollup).

    Lifetime totals (requests, hits, hit_rate, cost saved, tokens saved)
    union the permanent rollup with remaining raw rows so numbers stay
    correct across the 30-day pruning boundary. Latency averages and the
    per-user breakdown reflect the raw window only — the rollup is global
    by design.
    """
    conn = get_connection()
    try:
        r_total, r_hits, r_tokens, r_cost = _rollup_totals(conn)

        total = r_total + conn.execute("SELECT COUNT(*) FROM request_log").fetchone()[0]
        hits = (
            r_hits
            + conn.execute(
                "SELECT COUNT(*) FROM request_log WHERE outcome = 'HIT'"
            ).fetchone()[0]
        )

        hit_rate = hits / total if total > 0 else 0.0

        cost_saved = (
            r_cost
            + conn.execute(
                """
            SELECT COALESCE(SUM(estimated_cost_usd), 0)
              FROM request_log
             WHERE outcome = 'HIT'
            """
            ).fetchone()[0]
        )

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

        # Phase 7: tokens-saved is the headline metric for BYOK free-tier
        # users — only HIT rows represent generation we didn't pay for again.
        tokens_saved = (
            r_tokens
            + conn.execute(
                """
            SELECT COALESCE(SUM(tokens_in + tokens_out), 0)
              FROM request_log
             WHERE outcome = 'HIT'
            """
            ).fetchone()[0]
        )

        per_user_rows = conn.execute(
            """
            SELECT user_id,
                   COUNT(*)                                            AS total_requests,
                   SUM(CASE WHEN outcome = 'HIT' THEN 1 ELSE 0 END)     AS hits,
                   COALESCE(SUM(CASE WHEN outcome = 'HIT'
                                     THEN tokens_in + tokens_out ELSE 0 END), 0)
                                                                       AS tokens_saved,
                   ROUND(COALESCE(SUM(CASE WHEN outcome = 'HIT'
                                      THEN estimated_cost_usd ELSE 0 END), 0), 6)
                                                                       AS cost_saved_usd
              FROM request_log
          GROUP BY user_id
          ORDER BY tokens_saved DESC
            """
        ).fetchall()
        per_user = [dict(row) for row in per_user_rows]

        audit = conn.execute(
            "SELECT timestamp, purged_count, entry_id, actor FROM purge_audit"
            " ORDER BY audit_id DESC LIMIT 1"
        ).fetchone()

        return {
            "hit_rate": round(hit_rate, 4),
            "total_requests": total,
            "estimated_cost_saved_usd": round(cost_saved, 6),
            "avg_latency_hit_ms": round(hit_lat, 2),
            "avg_latency_miss_ms": round(miss_lat, 2),
            "total_tokens_saved": int(tokens_saved),
            "per_user": per_user,
            "last_purge": dict(audit) if audit is not None else None,
        }
    finally:
        conn.close()


def list_cache_entries(search: str | None = None) -> list[dict[str, Any]]:
    """List cache entries newest-first for the dashboard cache browser.

    ``search`` does a substring match on prompt_text when provided.
    """
    conn = get_connection()
    try:
        base = """
            SELECT entry_id, prompt_text, model_used, user_id,
                   created_at, expires_at, hit_count, last_hit_at
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


def recent_logs(limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent request_log rows, newest first."""
    limit = max(1, min(limit, 500))
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT log_id, timestamp, prompt_text, outcome, matched_entry_id,
                   similarity_score, latency_ms, estimated_cost_usd,
                   tokens_in, tokens_out, user_id
              FROM request_log
          ORDER BY timestamp DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

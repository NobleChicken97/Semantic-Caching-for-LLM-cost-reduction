"""Threshold validation — precision/recall sweep over labeled test pairs.

Implements the core evaluation loop described in the PRD: embed every
labeled prompt pair once, compute cosine similarity per pair, then
classify at each requested threshold and report precision/recall/F1.
"""

from __future__ import annotations

from .database import get_connection
from .embedding import cosine_similarity, embed_texts
from .models import ThresholdResult


def load_labeled_pairs() -> list[dict]:
    """Load all labeled test pairs from the database."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT prompt_a, prompt_b, should_match FROM labeled_test_pairs"
        ).fetchall()
        return [
            {
                "prompt_a": row["prompt_a"],
                "prompt_b": row["prompt_b"],
                "should_match": bool(row["should_match"]),
            }
            for row in rows
        ]
    finally:
        conn.close()


def _precision_recall_f1(tp: int, fp: int, fn: int) -> tuple:
    """Compute precision/recall/F1 with safe zero-division handling."""
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    return precision, recall, f1


def pair_similarities() -> list[tuple]:
    """Embed each pair once and return [(similarity, should_match), ...]."""
    pairs = load_labeled_pairs()
    if not pairs:
        return []

    # Batch-embed all prompts in one pass (2 texts per pair)
    texts: list[str] = []
    for p in pairs:
        texts.extend([p["prompt_a"], p["prompt_b"]])
    vectors = embed_texts(texts)

    out: list[tuple] = []
    for i, p in enumerate(pairs):
        sim = float(cosine_similarity(vectors[2 * i], vectors[2 * i + 1]))
        out.append((sim, p["should_match"]))
    return out


def run_threshold_sweep(thresholds: list[float]) -> list[ThresholdResult]:
    """Evaluate precision/recall/F1 at every threshold against the pairs."""
    scored = pair_similarities()
    if not scored:
        return []

    results: list[ThresholdResult] = []
    for t in thresholds:
        tp = fp = fn = tn = 0
        for sim, label in scored:
            predicted_match = sim >= t
            if label and predicted_match:
                tp += 1
            elif not label and predicted_match:
                fp += 1
            elif label and not predicted_match:
                fn += 1
            else:
                tn += 1

        precision, recall, f1 = _precision_recall_f1(tp, fp, fn)
        results.append(
            ThresholdResult(
                threshold=t,
                precision=round(precision, 4),
                recall=round(recall, 4),
                f1=round(f1, 4),
            )
        )
    return results

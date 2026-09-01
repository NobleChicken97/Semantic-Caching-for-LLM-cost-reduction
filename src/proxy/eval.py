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


def pair_similarity_details() -> list[dict]:
    """Embed each pair once and return per-pair detail dicts.

    Each dict carries the pair's prompts alongside its cosine similarity
    and label, so callers can explain *which* pairs sit near a threshold
    rather than only aggregate counts.
    """
    pairs = load_labeled_pairs()
    if not pairs:
        return []

    # Batch-embed all prompts in one pass (2 texts per pair)
    texts: list[str] = []
    for p in pairs:
        texts.extend([p["prompt_a"], p["prompt_b"]])
    vectors = embed_texts(texts)

    out: list[dict] = []
    for i, p in enumerate(pairs):
        sim = float(cosine_similarity(vectors[2 * i], vectors[2 * i + 1]))
        out.append(
            {
                "prompt_a": p["prompt_a"],
                "prompt_b": p["prompt_b"],
                "similarity": sim,
                "should_match": p["should_match"],
            }
        )
    return out


def pair_similarities() -> list[tuple]:
    """Embed each pair once and return [(similarity, should_match), ...]."""
    return [(d["similarity"], d["should_match"]) for d in pair_similarity_details()]


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


# Thresholds swept by /eval/auto-tune when the caller supplies none — the
# same grid documented in docs/THRESHOLD_ANALYSIS.md.
DEFAULT_SWEEP_THRESHOLDS = [0.80, 0.82, 0.85, 0.88, 0.90, 0.93, 0.95]

# Pairs whose similarity falls within this band of the chosen threshold are
# reported as "borderline" — they are the ones that would flip to the other
# side of the decision if the threshold moved slightly.
BORDERLINE_BAND = 0.03

# Cap on reported borderline pairs so the response stays readable.
MAX_BORDERLINE = 10


def run_auto_tune(thresholds: list[float] | None = None) -> dict:
    """Sweep thresholds, pick the F1-optimal one, and surface borderline pairs.

    F1 ties break toward the LOWER threshold: at equal F1 the extra recall
    is worth more than the extra precision for a cache (a false hit serves a
    slightly-off answer; a false miss just pays for one more generation).

    Returns ``{"best": ThresholdResult | None, "results": [...],
    "borderline": [detail dicts sorted by distance to the threshold]}``.
    ``best`` is ``None`` when the dataset or threshold list is empty.
    """
    if thresholds is None:
        thresholds = list(DEFAULT_SWEEP_THRESHOLDS)

    if not thresholds:
        return {"best": None, "results": [], "borderline": []}

    details = pair_similarity_details()
    if not details:
        return {"best": None, "results": [], "borderline": []}

    results = run_threshold_sweep(thresholds)
    best = max(results, key=lambda r: (r.f1, -r.threshold))

    borderline = [
        d for d in details if abs(d["similarity"] - best.threshold) <= BORDERLINE_BAND
    ]
    borderline.sort(key=lambda d: abs(d["similarity"] - best.threshold))
    return {
        "best": best,
        "results": results,
        "borderline": borderline[:MAX_BORDERLINE],
    }

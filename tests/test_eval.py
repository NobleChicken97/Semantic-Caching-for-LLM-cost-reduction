"""Tests for the threshold-sweep evaluation module (Phase 3)."""

from __future__ import annotations

import itertools
import os
import tempfile

import pytest

os.environ["MOCK_LLM"] = "true"


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    """Every test gets a temporary database path."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_eval_")
    os.close(fd)

    monkeypatch.setenv("CACHE_DB_PATH", path)

    from proxy.config import get_settings

    get_settings.cache_clear()

    from proxy.database import init_db

    init_db()

    yield path

    get_settings.cache_clear()

    try:
        os.unlink(path)
    except OSError:
        pass


def _insert_pairs(pairs):
    """Insert (prompt_a, prompt_b, should_match) tuples directly."""
    from proxy.database import get_connection

    conn = get_connection()
    try:
        conn.executemany(
            "INSERT INTO labeled_test_pairs (prompt_a, prompt_b, should_match) VALUES (?, ?, ?)",
            [(a, b, int(s)) for a, b, s in pairs],
        )
        conn.commit()
    finally:
        conn.close()


class TestLoadLabeledPairs:
    def test_empty_db_returns_no_pairs(self):
        from proxy.eval import load_labeled_pairs

        assert load_labeled_pairs() == []

    def test_loads_inserted_pairs(self):
        from proxy.eval import load_labeled_pairs

        _insert_pairs([("a", "a", 1), ("x", "y", 0)])
        pairs = load_labeled_pairs()
        assert len(pairs) == 2
        assert pairs[0]["should_match"] is True
        assert pairs[1]["should_match"] is False


class TestThresholdSweep:
    def test_identical_pair_is_perfect_at_threshold_just_under_1(self):
        """A pair of identical strings has similarity ~= 1.0 (float32).

        Use 0.999 rather than exactly 1.0: float32 dot products of a
        unit vector with itself land within ~1e-7 of 1.0 on either side,
        so an exact >=1.0 comparison would be flaky.
        """
        from proxy.eval import run_threshold_sweep

        _insert_pairs([("hello world", "hello world", 1)])
        results = run_threshold_sweep([0.999])
        assert len(results) == 1
        r = results[0]
        assert r.threshold == 0.999
        assert r.precision == 1.0
        assert r.recall == 1.0
        assert r.f1 == 1.0

    def test_mixed_dataset_known_outcomes(self):
        """Identical pair (sim ~= 1.0) + distinct pair (sim < 1.0) at t=0.999.

        Only the identical pair matches: TP=1, FP=0, FN=0
        -> precision = recall = f1 = 1.0.
        """
        from proxy.eval import run_threshold_sweep

        _insert_pairs(
            [
                ("same text", "same text", 1),
                ("alpha prompt", "beta prompt", 0),
            ]
        )
        results = run_threshold_sweep([0.999])
        r = results[0]
        assert r.precision == 1.0
        assert r.recall == 1.0
        assert r.f1 == 1.0

    def test_all_negative_predictions_zero_division_safe(self):
        """At an impossible threshold nothing matches; metrics must not crash."""
        from proxy.eval import run_threshold_sweep

        _insert_pairs([("p", "q", 1)])
        results = run_threshold_sweep([2.0])
        r = results[0]
        assert r.precision == 0.0
        assert r.recall == 0.0
        assert r.f1 == 0.0

    def test_recall_monotonically_nonincreasing(self):
        """Recall can only drop as the threshold rises (mathematical invariant)."""
        from proxy.database import seed_test_pairs
        from proxy.eval import run_threshold_sweep

        seed_test_pairs()
        thresholds = [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
        results = run_threshold_sweep(thresholds)
        recalls = [r.recall for r in results]
        for prev, curr in itertools.pairwise(recalls):
            assert curr <= prev + 1e-9

    def test_empty_thresholds_returns_empty_results(self):
        from proxy.eval import run_threshold_sweep

        _insert_pairs([("a", "a", 1)])
        assert run_threshold_sweep([]) == []


class TestSeededDataset:
    def test_seeded_dataset_has_31_pairs(self):
        """The expanded labeled set meets the PRD minimum of 20-30 pairs."""
        from proxy.database import seed_test_pairs
        from proxy.eval import load_labeled_pairs

        seed_test_pairs()
        pairs = load_labeled_pairs()
        assert len(pairs) >= 30
        positives = sum(1 for p in pairs if p["should_match"])
        negatives = len(pairs) - positives
        assert positives >= 10
        assert negatives >= 10

    def test_seed_matches_published_json_dataset(self):
        """Drift guard: seed_test_pairs() and data/labeled_test_pairs.json agree.

        The JSON is the reproducibility artifact consumers (and the README
        table) read; the inline seed is the source of truth. If this test
        fails, one of them changed without the other — re-run
        scripts/export_test_pairs.py.
        """
        import json
        from pathlib import Path

        from proxy.database import get_connection, seed_test_pairs

        seed_test_pairs()
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT pair_id, prompt_a, prompt_b, should_match "
                "FROM labeled_test_pairs ORDER BY pair_id"
            ).fetchall()
        finally:
            conn.close()

        json_path = (
            Path(__file__).resolve().parent.parent / "data" / "labeled_test_pairs.json"
        )
        published = json.loads(json_path.read_text(encoding="utf-8"))

        assert published["count"] == len(rows)
        for row, pair in zip(rows, published["pairs"], strict=False):
            assert pair["pair_id"] == row["pair_id"]
            assert pair["prompt_a"] == row["prompt_a"]
            assert pair["prompt_b"] == row["prompt_b"]
            assert pair["should_match"] is bool(row["should_match"])


class TestAutoTune:
    def test_empty_db_returns_null_best(self):
        from proxy.eval import run_auto_tune

        tune = run_auto_tune()
        assert tune["best"] is None
        assert tune["results"] == []
        assert tune["borderline"] == []

    def test_empty_thresholds_returns_empty(self):
        """Explicit empty grid mirrors /eval/threshold-sweep's [] -> []. contract."""
        from proxy.eval import run_auto_tune

        _insert_pairs([("a", "a", 1)])
        tune = run_auto_tune([])
        assert tune["best"] is None
        assert tune["results"] == []

    def test_default_thresholds_used_when_omitted(self):
        from proxy.eval import DEFAULT_SWEEP_THRESHOLDS, run_auto_tune

        _insert_pairs([("same text", "same text", 1)])
        tune = run_auto_tune(None)
        assert [r.threshold for r in tune["results"]] == DEFAULT_SWEEP_THRESHOLDS

    def test_f1_tie_breaks_toward_lower_threshold(self):
        """An identical pair scores F1=1.0 at every threshold below its sim;
        the pick must be the lowest of the tied grid (recall-favoring)."""
        from proxy.eval import run_auto_tune

        _insert_pairs([("hello world", "hello world", 1)])
        tune = run_auto_tune([0.80, 0.85, 0.90])
        assert tune["best"].threshold == 0.80
        assert tune["best"].f1 == 1.0

    def test_borderline_pairs_respect_band(self):
        """Every reported borderline pair sits within the band of the pick."""
        from proxy.database import seed_test_pairs
        from proxy.eval import BORDERLINE_BAND, run_auto_tune

        seed_test_pairs()
        tune = run_auto_tune([0.80, 0.85, 0.88, 0.90])
        assert tune["best"] is not None
        for p in tune["borderline"]:
            assert abs(p["similarity"] - tune["best"].threshold) <= BORDERLINE_BAND
            assert isinstance(p["should_match"], bool)

    def test_borderline_sorted_by_distance_to_threshold(self):
        from proxy.database import seed_test_pairs
        from proxy.eval import run_auto_tune

        seed_test_pairs()
        tune = run_auto_tune([0.80, 0.85, 0.88, 0.90])
        distances = [
            abs(p["similarity"] - tune["best"].threshold) for p in tune["borderline"]
        ]
        assert distances == sorted(distances)

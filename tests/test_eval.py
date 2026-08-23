"""Tests for the threshold-sweep evaluation module (Phase 3)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

os.environ["MOCK_LLM"] = "true"


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    """Every test gets a temporary database path."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_eval_")
    os.close(fd)

    monkeypatch.setenv("CACHE_DB_PATH", path)

    from proxy.config import settings

    monkeypatch.setattr(settings, "cache_db_path", path)

    from proxy.database import init_db

    init_db()

    yield path

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

        _insert_pairs([
            ("same text", "same text", 1),
            ("alpha prompt", "beta prompt", 0),
        ])
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
        for prev, curr in zip(recalls, recalls[1:]):
            assert curr <= prev + 1e-9

    def test_empty_thresholds_returns_empty_results(self):
        from proxy.eval import run_threshold_sweep

        _insert_pairs([("a", "a", 1)])
        assert run_threshold_sweep([]) == []


class TestSeededDataset:
    def test_seeded_dataset_has_32_pairs(self):
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

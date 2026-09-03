"""Lexical-overlap calibration for the Fix C decision.

Computes Jaccard similarity over stopword-stripped content words (with
number-word normalization) for every labeled pair plus the live collision
probes. Answers one question: does ANY global floor separate the collision
class (username/password, thanks/greeting) from all 16 labeled positives?

Run: python scripts/analyze_overlap.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

# Single source of truth lives in src/proxy/text.py (also used by the live
# veto) so calibration evidence and shipped rules can never drift apart.
from proxy.text import jaccard


def main() -> None:
    data = json.loads(Path("data/labeled_test_pairs.json").read_text(encoding="utf-8"))[
        "pairs"
    ]
    rows = []
    for p in data:
        rows.append(
            (
                p["pair_id"],
                bool(p["should_match"]),
                jaccard(p["prompt_a"], p["prompt_b"]),
            )
        )
    probes = [
        (
            "username/password",
            "How do I change my username?",
            "How do I reset my password?",
        ),
        ("thanks/greeting", "Thanks, that is all!", "Good morning!"),
        (
            "finland/france",
            "What is the capital of Finland?",
            "What is the capital of France?",
        ),
        (
            "population/capital",
            "What is the population of France?",
            "What is the capital of France?",
        ),
    ]
    print(f"{'id':>4} {'label':>6} {'jacc':>6}")
    for pid, want, j in sorted(rows, key=lambda r: r[2]):
        print(f"{pid:>4} {want!s:>6} {j:>6.3f}")
    print("--- collision probes (want MISS) ---")
    for name, a, b in probes:
        print(f"{name:>18} {jaccard(a, b):>6.3f}")
    pos_min = min(j for _, want, j in rows if want)
    print(f"---\nmin Jaccard over 16 positives: {pos_min:.3f}")
    print("max safe global floor: < lowest positive that must survive")


if __name__ == "__main__":
    main()

"""Run the official threshold sweep and print a markdown-ready table."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

fd, tmp_db = tempfile.mkstemp(suffix=".db", prefix="sweep_")
os.close(fd)
os.environ["CACHE_DB_PATH"] = tmp_db

from proxy.config import settings  # noqa: E402

settings.cache_db_path = tmp_db

from proxy.database import get_connection, init_db, seed_test_pairs  # noqa: E402
from proxy.eval import pair_similarities, run_threshold_sweep  # noqa: E402

init_db()
seed_test_pairs()

THRESHOLDS = [0.80, 0.82, 0.85, 0.88, 0.90, 0.93, 0.95]

results = run_threshold_sweep(THRESHOLDS)

print("| Threshold | Precision | Recall | F1 |")
print("|-----------|-----------|--------|-----|")
for r in results:
    print(f"| {r.threshold:.2f}      | {r.precision:.4f}   | {r.recall:.4f} | {r.f1:.4f} |")

print("\nPer-pair similarities (for borderline analysis):")
scored = pair_similarities()
conn = get_connection()
try:
    rows = conn.execute(
        "SELECT prompt_a, prompt_b, should_match FROM labeled_test_pairs"
    ).fetchall()
finally:
    conn.close()

flagged = []
for (sim, label), row in zip(scored, rows):
    if label == 1 and sim < max(THRESHOLDS):
        flagged.append((sim, row["prompt_a"], row["prompt_b"]))
    if label == 0 and sim >= min(THRESHOLDS) - 0.02:
        flagged.append((sim, row["prompt_a"], row["prompt_b"]))

try:
    os.unlink(tmp_db)
except OSError:
    pass

print(f"\nPositives missed below 0.95 or negatives near thresholds ({len(flagged)}):")
for sim, a, b in sorted(flagged, key=lambda x: -x[0]):
    print(f"  {sim:.4f}  {a[:50]!r} vs {b[:50]!r}")

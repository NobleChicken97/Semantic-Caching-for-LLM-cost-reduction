"""Export seed_test_pairs() to data/labeled_test_pairs.json for reproducibility."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Use a FRESH temp database: seed_test_pairs() skips non-empty tables,
# so exporting against a pre-existing cache.db would export stale rows.
fd, tmp_db = tempfile.mkstemp(suffix=".db", prefix="export_pairs_")
os.close(fd)
os.environ["CACHE_DB_PATH"] = tmp_db


from proxy.database import get_connection, init_db, seed_test_pairs

init_db()
seed_test_pairs()

conn = get_connection()
try:
    rows = conn.execute(
        "SELECT pair_id, prompt_a, prompt_b, should_match FROM labeled_test_pairs ORDER BY pair_id"
    ).fetchall()
finally:
    conn.close()

data = {
    "description": (
        "Labeled test pairs for threshold validation of the semantic cache. "
        "should_match=1 means the two prompts are paraphrases that a correct "
        "cache SHOULD serve as hits; 0 means they must NOT match."
    ),
    "source": "proxy.database.seed_test_pairs()",
    "model": "BAAI/bge-small-en-v1.5",
    "count": len(rows),
    "pairs": [
        {
            "pair_id": r["pair_id"],
            "prompt_a": r["prompt_a"],
            "prompt_b": r["prompt_b"],
            "should_match": bool(r["should_match"]),
        }
        for r in rows
    ],
}

out_path = os.path.join(os.path.dirname(__file__), "..", "data", "labeled_test_pairs.json")
os.makedirs(os.path.dirname(out_path), exist_ok=True)
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

try:
    os.unlink(tmp_db)
except OSError:
    pass

print(f"Exported {len(rows)} pairs -> {os.path.normpath(out_path)}")

"""Trust calibration: semantic similarities on the POST-Fix-A distribution.

Embeds every labeled pair in message-only canonical form ("[user]<text>",
the exact input Fix A feeds the model) and reports per-pair cosine
similarity alongside Jaccard overlap and the label, plus which veto signals
would fire. Used to calibrate and re-verify the Fix B rules from
measurement instead of guessing.

Run: python scripts/calibrate_trust.py  (needs torch + sentence-transformers)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from proxy.cache import (
    semantic_veto,
)
from proxy.embedding import cosine_similarity, embed_texts

# Same helpers the live veto uses (src/proxy/text.py) — including the
# ENTITY_TEMPLATE_FLOOR gate, so this table reproduces shipped behavior.
from proxy.text import (
    jaccard,
    template_jaccard,
)


def main() -> None:
    data = json.loads(Path("data/labeled_test_pairs.json").read_text(encoding="utf-8"))[
        "pairs"
    ]
    probes = [
        (
            "P-USER",
            "How do I change my username?",
            "How do I reset my password?",
            False,
        ),
        ("P-THANKS", "Thanks, that is all!", "Good morning!", False),
        (
            "P-FIN",
            "What is the capital of Finland?",
            "What is the capital of France?",
            False,
        ),
        (
            "P-POP",
            "What is the population of France?",
            "What is the capital of France?",
            False,
        ),
    ]
    rows = [
        (p["pair_id"], p["prompt_a"], p["prompt_b"], bool(p["should_match"]))
        for p in data
    ]
    rows += [(n, a, b, w) for n, a, b, w in probes]

    texts = []
    for _, a, b, _ in rows:
        texts += [f"[user]{a}", f"[user]{b}"]
    vecs = embed_texts(texts)

    print(f"{'id':>6} {'label':>6} {'sim':>7} {'cj':>6} {'tj':>6} veto")
    for i, (pid, a, b, want) in enumerate(rows):
        sim = cosine_similarity(vecs[2 * i], vecs[2 * i + 1])
        cj = jaccard(a, b)
        tj = template_jaccard(a, b)
        veto = semantic_veto(f"[user]{a}", f"[user]{b}")
        flag = ""
        if want and (sim < 0.85 or veto):
            flag = "  <-- RECALL RISK"
        if not want and sim >= 0.85 and not veto:
            flag = "  <-- STILL HOLED"
        print(
            f"{pid!s:>6} {want!s:>6} {sim:>7.4f} {cj:>6.3f} {tj:>6.3f} {veto!s:>5}{flag}"
        )


if __name__ == "__main__":
    main()

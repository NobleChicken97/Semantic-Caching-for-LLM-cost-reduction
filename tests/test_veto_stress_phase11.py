"""Phase 11 (read-only): adversarial stress of the shipped veto logic.

Standalone file — touches nothing existing. Exercises ``semantic_veto``
plus the REAL embedding model (no mocks) over sentence pairs chosen to
pressure the Phase 10 shared-skeleton gate (VETO_TEMPLATE_SHARED_MIN = 3):

  A. boundary: exactly 2 / 3 / 4 shared template tokens, both meanings
  B. documented residuals (4+-token synonym swaps) — must still veto
  C. order-swap blindness (reordered near-duplicates must never veto)
  D. entity substitution in untested categories (currency, dates, authors,
     role nouns, scientists, lowercase products)
  E. random paraphrase batch across structures (must never veto; sims observed)
  F. direct attacks on the 3-token rule itself (correct refusals at exactly 3,
     residual hits at exactly 3, one threshold-domain probe)

Hard asserts cover VETO True/False only (pure lexical, deterministic).
Similarity scores and the implied HIT/MISS decision (threshold read live
from config) are printed per case and reviewed in test_Result.md — a
surprising sim band is evidence, never an assert, so embedding drift can
never false-alarm this file.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from proxy.cache import semantic_veto
from proxy.config import get_settings
from proxy.embedding import cosine_similarity, embed_texts
from proxy.text import jaccard, shared_template_count, template_jaccard

# id, text_a, text_b, expect_veto, note
CASES: list[tuple[str, str, str, bool, str]] = [
    # ---- A. shared-count boundary -------------------------------------
    ("A1", "see you really soon", "see you later",
     False, "2 shared; template exactly 0.40; gate must exempt"),
    ("A2", "Is the cat asleep?", "Is the dog asleep?",
     True, "exactly 3 shared; different meaning; must still refuse"),
    ("A3", "talk to you later", "talk to you soon",
     True, "exactly 3 shared TRUE paraphrase: documented residual, must veto"),
    ("A4", "How do I change my email?", "How do I reset my password?",
     True, "4 shared; verb+object swap; must refuse"),
    ("A5", "How do I cook rice?", "How do I fix cars?",
     True, "exactly 3 shared stopwords; unrelated; gate must not over-exempt"),
    ("A6", "Kindly reset my password", "Please reset my password",
     False, "politeness swap, content 0.50: survives on content, not gate"),
    ("A7", "What time is it now?", "What time is it currently?",
     True, "4 shared TRUE paraphrase (now/currently): residual class"),
    ("A8", "see you next week", "see you next month",
     False, "3 shared but content 0.50: veto silent; threshold decides"),
    # ---- B. documented residuals --------------------------------------
    ("B1", "I will talk to you later", "I will talk to you soon",
     True, "5 shared TRUE paraphrase: residual, must veto per docs"),
    # ---- C. order-swap blindness --------------------------------------
    ("C1", "What is the capital of France?", "The capital of France is what?",
     False, "identical content words; reorder must never veto"),
    ("C2", "Who wrote Hamlet?", "Hamlet was written by whom?",
     False, "1 shared template token; gate reinforces, must not veto"),
    ("C3", "Reset my password now", "Now reset my password",
     False, "identical multiset; must never veto"),
    # ---- D. entity substitution, new categories ------------------------
    ("D1", "What is the currency of Japan?", "What is the currency of Brazil?",
     True, "currency entities; signal 1 must fire"),
    ("D2", "When was modern Germany founded?", "When was ancient Rome founded?",
     True, "date/founding entities; signal 1 must fire"),
    ("D3", "Who wrote Hamlet?", "Who wrote Macbeth?",
     True, "author entities; signal 1 must fire"),
    ("D4", "Who is the CEO of Apple?", "Who is the founder of Apple?",
     True, "role-noun fact types; signal 2 must fire"),
    ("D5", "When was Einstein born?", "When was Newton born?",
     True, "scientist entities; signal 1 must fire"),
    ("D6", "When did the iphone launch?", "When did the ipad launch?",
     True, "lowercase products: no entities; signal 3 backstop must fire"),
    # ---- E. random paraphrase batch (never veto) -----------------------
    ("E1", "hello there", "hi there",
     False, "greeting pair; template 0.33 kills Fix C anyway"),
    ("E2", "good morning", "morning",
     False, "containment-like shortening; must not veto"),
    ("E3", "bye for now", "goodbye for now",
     False, "2 shared; second gate validation beyond see-you"),
    ("E4", "How can I recover my account?", "How do I recover my account?",
     False, "auxiliary swap; identical content"),
    ("E5", "Define photosynthesis", "Define photosyntesis",
     False, "typo; template 0.33; threshold's job"),
    ("E6", "My laptop won't turn on.", "My laptop does not start.",
     False, "same-meaning negation pair; must not veto"),
    ("E7", "What is 2 + 2?", "Calculate two plus two.",
     False, "number-word normalization probe"),
    ("E8", "Turn off the lights", "Switch off the lights",
     False, "command synonym; antonym list must not misfire on 'off'"),
    ("E9", "Enable dark mode", "Turn on dark mode",
     False, "command paraphrase; enable/on is not an antonym swap"),
    # ---- F. attacks on the 3-token rule --------------------------------
    ("F1", "Where is the airport?", "Where is the station?",
     True, "exactly 3 shared; different place; correct refusal"),
    ("F2", "Who won the game?", "Who lost the game?",
     False, "'who' is not a stopword so cj=0.50 escapes Fix C; won/lost "
     "absent from antonym list: veto silent, threshold decides (observe)"),
]


def test_veto_stress_matrix():
    threshold = get_settings().similarity_threshold
    texts: list[str] = []
    for _, a, b, _, _ in CASES:
        texts += [f"[user]{a}", f"[user]{b}"]
    vecs = embed_texts(texts)

    print(f"\nthreshold={threshold} cases={len(CASES)}")
    print(f"{'id':>4} {'veto':>5} {'exp':>5} {'sim':>7} "
          f"{'cj':>5} {'tj':>5} {'sh':>3} {'decision':>8}  note")
    mismatches: list[str] = []
    for i, (cid, a, b, exp, note) in enumerate(CASES):
        sim = cosine_similarity(vecs[2 * i], vecs[2 * i + 1])
        veto = semantic_veto(f"[user]{a}", f"[user]{b}")
        cj = jaccard(a, b)
        tj = template_jaccard(a, b)
        sh = shared_template_count(a, b)
        decision = "HIT" if (sim >= threshold and not veto) else "MISS"
        flag = "" if veto == exp else "  <-- VETO SURPRISE"
        if veto != exp:
            mismatches.append(cid)
        print(f"{cid:>4} {veto!s:>5} {exp!s:>5} {sim:>7.4f} "
              f"{cj:>5.3f} {tj:>5.3f} {sh:>3} {decision:>8}{flag}  {note}")
    print(f"veto mismatches: {mismatches if mismatches else 'none'}")
    assert not mismatches, f"veto surprises in {mismatches}"

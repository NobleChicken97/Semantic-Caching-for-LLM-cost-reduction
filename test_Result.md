# Phase 11 veto stress — results (read-only, no code touched)

Date: 2026-09-04. New file only: `tests/test_veto_stress_phase11.py`
(29 cases). Live code, zero mocks: real `embed_texts` (BGE-small, local),
real `semantic_veto`, threshold read live from config (`0.85`).
Result: **1 passed, veto mismatches: none (29/29)** in 28.21 s.

## Decision path under test (as read in `src/`)

`routes/chat.py` → `cache.lookup()` → Tier 1 exact-hash → Tier 2
`_semantic_lookup`: nearest embedding with sim >= threshold becomes the
candidate → `semantic_veto(query, candidate)` refusal turns it into MISS.
So HIT = (sim >= 0.85) AND (no veto). The three "signals" in the brief map
to: (1) cosine threshold gate, (2) content/template Jaccard evidence,
(3) the 5-rule veto refusal. Veto can only *remove* hits, never create them —
every MISS below whose sim clears 0.85 is a veto earn; every HIT above 0.85
on a different-meaning pair is a veto escape (threshold-domain finding).

## Raw output (verbatim, HF warning + progress lines trimmed)

```text
threshold=0.85 cases=29
  id  veto   exp     sim    cj    tj  sh decision  note
  A1 False False  0.9441 0.250 0.400   2      HIT  2 shared; template exactly 0.40; gate must exempt
  A2  True  True  0.8718 0.333 0.600   3     MISS  exactly 3 shared; different meaning; must still refuse
  A3  True  True  0.9591 0.333 0.600   3     MISS  exactly 3 shared TRUE paraphrase: documented residual, must veto
  A4  True  True  0.7465 0.000 0.500   4     MISS  4 shared; verb+object swap; must refuse
  A5  True  True  0.5311 0.000 0.429   3     MISS  exactly 3 shared stopwords; unrelated; gate must not over-exempt
  A6 False False  0.9836 0.500 0.600   3      HIT  politeness swap, content 0.50: survives on content, not gate
  A7  True  True  0.9792 0.333 0.667   4     MISS  4 shared TRUE paraphrase (now/currently): residual class
  A8 False False  0.9081 0.500 0.600   3      HIT  3 shared but content 0.50: veto silent; threshold decides
  B1  True  True  0.9641 0.333 0.714   5     MISS  5 shared TRUE paraphrase: residual, must veto per docs
  C1 False False  0.9853 1.000 1.000   6      HIT  identical content words; reorder must never veto
  C2 False False  0.9622 0.200 0.143   1      HIT  1 shared template token; gate reinforces, must not veto
  C3 False False  0.9935 1.000 1.000   4      HIT  identical multiset; must never veto
  D1  True  True  0.8235 0.333 0.714   5     MISS  currency entities; signal 1 must fire
  D2  True  True  0.7588 0.200 0.429   3     MISS  date/founding entities; signal 1 must fire
  D3  True  True  0.8324 0.500 0.500   2     MISS  author entities; signal 1 must fire
  D4  True  True  0.8960 0.500 0.714   5     MISS  role-noun fact types; signal 2 must fire
  D5  True  True  0.8165 0.333 0.600   3     MISS  scientist entities; signal 1 must fire
  D6  True  True  0.8905 0.333 0.667   4     MISS  lowercase products: no entities; signal 3 backstop must fire
  E1 False False  0.9814 0.000 0.333   1      HIT  greeting pair; template 0.33 kills Fix C anyway
  E2 False False  0.9448 0.500 0.500   1      HIT  containment-like shortening; must not veto
  E3 False False  0.9766 0.333 0.500   2      HIT  2 shared; second gate validation beyond see-you
  E4 False False  0.9960 1.000 0.714   5      HIT  auxiliary swap; identical content
  E5 False False  0.8549 0.333 0.333   1      HIT  typo; template 0.33; threshold's job
  E6 False False  0.9288 0.200 0.222   2      HIT  same-meaning negation pair; must not veto
  E7 False False  0.8942 0.333 0.000   3      HIT  number-word normalization probe
  E8 False False  0.9807 0.500 0.600   3      HIT  command synonym; antonym list must not misfire on 'off'
  E9 False False  0.9658 0.500 0.400   2      HIT  command paraphrase; enable/on is not an antonym swap
  F1  True  True  0.8055 0.333 0.600   3     MISS  exactly 3 shared; different place; correct refusal
  F2 False False  0.8772 0.500 0.600   3      HIT  'who' is not a stopword so cj=0.50 escapes Fix C; won/lost absent from antonym list: veto silent, threshold decides (observe)
veto mismatches: none
1 passed in 28.21s
```

Pre-flight note: a lexical-only pre-run (no model) initially flagged my own
F2 expectation as wrong — "who" is not in STOPWORDS, so cj=0.50 escapes Fix C
by construction. The test file was corrected before the model run; the run
above is the corrected file's first and only embedding run.

## Summary table

| id | pair (short) | veto | sim | decision | correct? |
|----|---|:---:|---:|---|---|
| A1 | see-you-really-soon / later (sh=2, tj=0.40) | F | 0.9441 | HIT | yes — gate exempts at boundary |
| A2 | cat/dog asleep (sh=3) | T | 0.8718 | MISS | yes — veto earned (sim clears thr) |
| A3 | talk-to-you later/soon (sh=3, true paraphrase) | T | 0.9591 | MISS | residual, as documented |
| A4 | change-email / reset-password (sh=4) | T | 0.7465 | MISS | yes (both layers agree) |
| A5 | cook-rice / fix-cars (sh=3 stopwords) | T | 0.5311 | MISS | yes — no over-exemption |
| A6 | kindly/please reset password | F | 0.9836 | HIT | yes |
| A7 | time now/currently (sh=4, true paraphrase) | T | 0.9792 | MISS | residual, as documented |
| A8 | see-you next week/month (different meaning!) | F | 0.9081 | **HIT** | **NO — new FP (threshold-domain)** |
| B1 | I-will-talk-to-you later/soon (sh=5, true) | T | 0.9641 | MISS | residual, as documented |
| C1 | capital-of-France reorder | F | 0.9853 | HIT | yes |
| C2 | Hamlet active/passive (sh=1) | F | 0.9622 | HIT | yes |
| C3 | reset-password word shuffle | F | 0.9935 | HIT | yes |
| D1 | currency Japan/Brazil | T | 0.8235 | MISS | yes (both layers agree) |
| D2 | Germany/Rome founded | T | 0.7588 | MISS | yes |
| D3 | Hamlet/Macbeth (sh=2!) | T | 0.8324 | MISS | yes — signal 1 needs no sh>=3 |
| D4 | CEO/founder of Apple | T | 0.8960 | MISS | yes — veto earned (signal 2) |
| D5 | Einstein/Newton born | T | 0.8165 | MISS | yes |
| D6 | iphone/ipad launch (lowercase) | T | 0.8905 | MISS | yes — veto earned (signal-3 backstop) |
| E1–E4 | greetings/shortening/auxiliary | F | 0.94–1.00 | HIT | yes, all |
| E5 | photosynthe
...[truncated 3881 chars]
"""Tiny lexical helpers shared by the cache veto and the analysis scripts.

Dependency-free on purpose: the entity/template gate runs on the request
hot path, so it cannot pull in NLP packages. The same functions back
``scripts/analyze_overlap.py`` and ``scripts/calibrate_trust.py`` so the
shipped rule and the calibration evidence can never drift apart.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter

# Stopwords for content-word comparison. Negations (not/no/nor/never) are
# deliberately KEPT: "safe" vs "not safe" must not look identical.
# Trailing fragments are contraction debris ("don't" -> "don", "t").
STOPWORDS = frozenset(
    {
        "what",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "am",
        "the",
        "a",
        "an",
        "of",
        "and",
        "or",
        "to",
        "in",
        "for",
        "on",
        "at",
        "how",
        "do",
        "does",
        "did",
        "can",
        "could",
        "would",
        "should",
        "will",
        "you",
        "your",
        "yours",
        "me",
        "my",
        "mine",
        "i",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "there",
        "their",
        "they",
        "them",
        "he",
        "she",
        "we",
        "us",
        "our",
        "by",
        "with",
        "from",
        "as",
        "so",
        "if",
        "then",
        "than",
        "too",
        "very",
        "when",
        "which",
        "while",
        "also",
        "t",
        "s",
        "re",
        "ve",
        "ll",
        "d",
        "m",
    }
)

NUMBERS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "thirteen": "13",
    "fourteen": "14",
    "fifteen": "15",
    "sixteen": "16",
    "seventeen": "17",
    "eighteen": "18",
    "nineteen": "19",
    "twenty": "20",
    "thirty": "30",
    "forty": "40",
    "fifty": "50",
    "sixty": "60",
    "seventy": "70",
    "eighty": "80",
    "ninety": "90",
    "hundred": "100",
    "thousand": "1000",
}

# Fact-type nouns that change the question while the template stays fixed
# ("capital of France" vs "population of France"). Curated from the labeled
# set and the spotlight probes — extend here when new ones surface, with a
# test each (see tests/test_trust.py).
FACT_TYPES = frozenset(
    {
        "capital",
        "population",
        "currency",
        "language",
        "area",
        "gdp",
        "president",
        "king",
        "queen",
        "mayor",
        "founder",
        "ceo",
        "author",
        "director",
    }
)


def content_words(text: str) -> set[str]:
    """Lowercased content tokens with number words normalized to digits."""
    toks = re.findall(r"\w+", text.lower())
    return {NUMBERS.get(t, t) for t in toks if t not in STOPWORDS}


def jaccard(a: str, b: str) -> float:
    """Jaccard similarity over content words (0.0 when disjoint)."""
    sa, sb = content_words(a), content_words(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def strip_tags(text: str) -> str:
    """Remove ``[role]``/``[model]`` markers so lexical rules operate on
    user content. Without this, the ``[user]`` prefix shifts every token
    index by one and sentence-initial words ("What") misread as entities."""
    return re.sub(r"\[[^\]]*\]", " ", text)


def entities(text: str) -> set[str]:
    """Capitalized tokens, sentence-initial tokens excluded (Fix B signal 1).

    Sentence-initial capitals are grammar, not entities ("What is the..."
    must not count "What"). Single-word prompts therefore never veto —
    conservative by design.
    """
    found: set[str] = set()
    for sent in re.split(r"[.!?]+", strip_tags(text)):
        toks = re.findall(r"[A-Za-z][\w']*", sent)
        for i, t in enumerate(toks):
            if i == 0:
                continue
            if t[0].isupper():
                found.add(t.lower())
    return found


def fact_types(text: str) -> set[str]:
    """Fact-type keywords present as whole words (Fix B signal 2)."""
    return set(re.findall(r"[a-z]+", text.lower())) & FACT_TYPES


def template_jaccard(a: str, b: str) -> float:
    """Jaccard over ALL lowercased word tokens: the surface template.

    Unlike content Jaccard this keeps stopwords — "How do I change my X"
    vs "How do I reset my X" share their skeleton here (0.71) while
    differing in content (0.33). The pair (content, template) separates
    same-template collisions from true paraphrases; neither alone does.
    """
    sa = set(re.findall(r"\w+", a.lower()))
    sb = set(re.findall(r"\w+", b.lower()))
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def shared_template_count(a: str, b: str) -> int:
    """Count of shared lowercased word tokens: the skeleton's strut count.

    Backs the Fix C shared-skeleton gate in ``semantic_veto``. Fewer than
    three shared tokens ("see you", "how do") is not evidence of shared
    structure — two shared stopwords occur across vast numbers of unrelated
    utterances, while template Jaccard on tiny token sets still clears 0.4
    automatically (2 shared of 4 union = 0.5). The gate therefore requires
    >= 3 shared tokens before a "same skeleton" claim can veto.
    """
    sa = set(re.findall(r"\w+", a.lower()))
    sb = set(re.findall(r"\w+", b.lower()))
    return len(sa & sb)


def typo_bridged(a: str, b: str) -> bool:
    """True when every unshared content word has a near-duplicate across.

    Distinguishes typo variations ("captial" vs "capital", difflib 0.86)
    from genuine word swaps ("change" vs "reset", ~0.4): the former must
    never veto, the latter must. Either side empty means entailment-like
    containment, not a collision — no veto.
    """
    sa, sb = content_words(a), content_words(b)
    only_a, only_b = sa - sb, sb - sa
    if not only_a or not only_b:
        return True
    return all(
        difflib.get_close_matches(w, list(only_b), n=1, cutoff=0.8) for w in only_a
    ) and all(
        difflib.get_close_matches(w, list(only_a), n=1, cutoff=0.8) for w in only_b
    )


# Negation markers and antonym pairs (Fix D). Deliberately small, curated
# lists with the same philosophy as FACT_TYPES: extend with a test each.
# Bare "no" is excluded (too common: "know", "no problem" would misfire —
# whole-word match only, and even that proved too risky in calibration).
NEGATION_WORDS = frozenset({"not", "never", "without", "neither", "nor", "none"})

ANTONYM_PAIRS = frozenset(
    {
        frozenset({"enable", "disable"}),
        frozenset({"open", "close"}),
        frozenset({"open", "closed"}),
        frozenset({"start", "stop"}),
        frozenset({"allow", "deny"}),
        frozenset({"allow", "forbid"}),
        frozenset({"increase", "decrease"}),
        frozenset({"add", "remove"}),
        frozenset({"create", "delete"}),
        frozenset({"lock", "unlock"}),
        frozenset({"connect", "disconnect"}),
        frozenset({"on", "off"}),
        frozenset({"true", "false"}),
        frozenset({"yes", "no"}),
        frozenset({"safe", "unsafe"}),
        frozenset({"healthy", "unhealthy"}),
        frozenset({"legal", "illegal"}),
        frozenset({"buy", "sell"}),
        frozenset({"push", "pull"}),
    }
)


def has_negation(text: str) -> bool:
    """True when the text carries an explicit negation marker."""
    low = text.lower()
    if "n't" in low:
        return True
    return bool(set(re.findall(r"[a-z]+", low)) & NEGATION_WORDS)


def antonym_swapped_equal(a: str, b: str) -> bool:
    """True when the two token multisets match after exactly one antonym
    substitution in either direction ("enable X" vs "disable X")."""
    ca = Counter(re.findall(r"\w+", a.lower()))
    cb = Counter(re.findall(r"\w+", b.lower()))
    if ca == cb:
        return False
    for pair in ANTONYM_PAIRS:
        x, y = tuple(pair)
        for src, dst in ((ca, cb), (cb, ca)):
            if src.get(x, 0) > dst.get(x, 0):
                trial = Counter(src)
                trial[x] -= 1
                if trial[x] <= 0:
                    del trial[x]
                trial[y] += 1
                if trial == dst:
                    return True
    return False


def number_tokens(text: str) -> set[str]:
    """Digit strings in the text, with number words normalized ("two"->"2").

    Roman numerals and spelled-out magnitudes beyond the NUMBERS table are
    out of scope (documented limit, same as the veto's non-English limit).
    """
    toks = re.findall(r"\w+", text.lower())
    return {NUMBERS.get(t, t) for t in toks if t.isdigit() or t in NUMBERS}

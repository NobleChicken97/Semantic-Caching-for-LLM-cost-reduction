"""Tiny lexical helpers shared by the cache veto and the analysis scripts.

Dependency-free on purpose: the entity/template gate runs on the request
hot path, so it cannot pull in NLP packages. The same functions back
``scripts/analyze_overlap.py`` and ``scripts/calibrate_trust.py`` so the
shipped rule and the calibration evidence can never drift apart.
"""

from __future__ import annotations

import re

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

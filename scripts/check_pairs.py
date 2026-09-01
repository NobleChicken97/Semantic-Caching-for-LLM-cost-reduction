"""One-off diagnostic: score candidate labeled pairs against BGE-small."""

import os
import sys

os.environ["MOCK_LLM"] = "true"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from proxy.embedding import cosine_similarity, embed_texts

CANDIDATES = [
    ("What is AI?", "Define artificial intelligence.", 1),
    ("What is the capital of France?", "What is the captial of France?", 1),
    (
        "Explain what this Python code does: sorted(items, key=len)",
        "What does the Python expression sorted(items, key=len) do?",
        1,
    ),
    ("How do I make coffee?", "What's the best way to brew coffee?", 1),
    (
        "My laptop won't turn on.",
        "My laptop does not start when I press the power button.",
        1,
    ),
    (
        "Recommend a good sci-fi book.",
        "Can you suggest a great science fiction novel?",
        1,
    ),
    ("Hi", "Goodbye", 0),
    ("What's the weather in Tokyo?", "Who wrote the play Hamlet?", 0),
    ("def add(a, b): return a + b", "def multiply(a, b): return a * b", 0),
    ("Fix my bicycle tire.", "Translate 'good morning' to French.", 0),
    (
        "Best programming language for beginners?",
        "Give me a brief history of the Roman Empire.",
        0,
    ),
    (
        "How do I bake chocolate chip cookies?",
        "How do I change a flat tire on a car?",
        0,
    ),
]

texts = []
for a, b, _ in CANDIDATES:
    texts.extend([a, b])
vecs = embed_texts(texts)

for i, (a, b, label) in enumerate(CANDIDATES):
    s = float(cosine_similarity(vecs[2 * i], vecs[2 * i + 1]))
    ok = (label == 1 and s >= 0.85) or (label == 0 and s < 0.85)
    flag = "OK          " if ok else "**BORDERLINE**"
    print(f"{s:.4f}  label={label}  {flag}  {a[:44]!r} vs {b[:44]!r}")

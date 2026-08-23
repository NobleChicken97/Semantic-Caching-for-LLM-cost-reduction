"""Local embedding model wrapper — BGE-small-en-v1.5 via sentence-transformers.

Lazily loads the model on first use to avoid blocking app startup.
"""

from __future__ import annotations

import numpy as np

_model = None


def _get_model():
    """Lazy singleton: load BGE-small on first access."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(
            "BAAI/bge-small-en-v1.5",
            device="cpu",  # CPU-friendly, per the master guide
        )
    return _model


def embed_texts(texts: list[str], normalize: bool = True) -> np.ndarray:
    """Embed a batch of prompt strings.

    Returns a 2-D numpy array of shape (len(texts), 384).
    """
    if not texts:
        return np.empty((0, 384), dtype=np.float32)

    model = _get_model()
    embeddings = model.encode(
        texts,
        normalize_embeddings=normalize,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    # Guard against a single string being squeezed to 1-D
    if embeddings.ndim == 1:
        embeddings = embeddings.reshape(1, -1)
    return embeddings.astype(np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two normalized vectors.

    Both vectors are assumed already L2-normalized (unit length),
    so cosine similarity is simply the dot product.
    """
    return float(np.dot(a, b))


def embedding_dim() -> int:
    """Return the embedding dimension (384 for bge-small)."""
    return 384
"""Unit tests for the embedding module."""

from __future__ import annotations

import numpy as np
import pytest

from proxy.embedding import cosine_similarity, embed_texts, embedding_dim


class TestEmbedding:
    def test_embedding_dim(self):
        assert embedding_dim() == 384

    def test_embed_single_text(self):
        vec = embed_texts(["hello world"])
        assert isinstance(vec, np.ndarray)
        assert vec.shape == (1, 384)
        assert vec.dtype == np.float32

    def test_embed_multiple_texts(self):
        texts = ["first prompt", "second prompt", "third prompt"]
        vecs = embed_texts(texts)
        assert vecs.shape == (3, 384)

    def test_embed_empty_list(self):
        vecs = embed_texts([])
        assert vecs.shape == (0, 384)

    def test_embeddings_are_normalized(self):
        """BGE-small embeddings should be L2-normalized (unit vectors)."""
        vec = embed_texts(["test"])[0]
        norm = float(np.linalg.norm(vec))
        assert pytest.approx(norm, rel=1e-4) == 1.0

    def test_cosine_similarity_identical(self):
        v = embed_texts(["same thing"])[0]
        assert pytest.approx(cosine_similarity(v, v), rel=1e-4) == 1.0

    def test_cosine_similarity_different(self):
        v1 = embed_texts(["capital of France"])[0]
        v2 = embed_texts(["boiling point of water"])[0]
        score = cosine_similarity(v1, v2)
        assert 0.0 <= score <= 1.0
        # These are very different topics — score should be moderate at best
        assert score < 0.95

    def test_cosine_similarity_semantically_close(self):
        v1 = embed_texts(["What is the capital of France?"])[0]
        v2 = embed_texts(["Tell me the capital of France."])[0]
        score = cosine_similarity(v1, v2)
        assert score > 0.85  # paraphrases should be close

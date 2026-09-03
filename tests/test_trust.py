"""Phase 9 semantic-trust tests: message-only embeddings + entity veto.

Covers Fix A (the [model] line is hash identity, never embedding input)
and Fix B (two-signal veto: entity swap with template gate, fact-type
swap). Follows the AAA pattern with scenario-named tests.
"""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient

os.environ["MOCK_LLM"] = "true"


def _france(model="gpt-3.5-turbo"):
    return {
        "model": model,
        "messages": [{"role": "user", "content": "What is the capital of France?"}],
    }


async def _outcome(client, payload, bypass=False):
    headers = {"X-Cache-Bypass": "true"} if bypass else None
    resp = await client.post("/v1/chat/completions", json=payload, headers=headers)
    assert resp.status_code == 200
    return resp.json()["cache_metadata"]


@pytest.fixture
async def client(monkeypatch, tmp_path):
    """Isolated-DB async client (mirrors test_api.py)."""
    db_path = str(tmp_path / "test_trust.db")
    monkeypatch.setenv("CACHE_DB_PATH", db_path)

    from proxy.config import get_settings

    get_settings.cache_clear()

    from proxy.database import init_db, seed_test_pairs

    init_db()
    seed_test_pairs()

    from proxy.main import app, lifespan

    transport = ASGITransport(app=app)
    async with (
        lifespan(app),
        AsyncClient(transport=transport, base_url="http://test") as ac,
    ):
        yield ac

    get_settings.cache_clear()


class TestEmbeddingText:
    def test_excludes_model_line(self):
        from proxy.models import ChatCompletionRequest

        req = ChatCompletionRequest(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "What is the capital of France?"}],
        )
        assert "[model]" not in req.embedding_text()
        assert "[user]What is the capital of France?" == req.embedding_text()

    def test_canonical_prompt_unchanged(self):
        """The hash identity keeps the [model] line (Fix A changes input only)."""
        from proxy.models import ChatCompletionRequest

        req = ChatCompletionRequest(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "What is the capital of France?"}],
        )
        assert req.canonical_prompt() == (
            "[model]gpt-3.5-turbo\n[user]What is the capital of France?"
        )

    def test_multimessage_join(self):
        from proxy.models import ChatCompletionRequest

        req = ChatCompletionRequest(
            model="x",
            messages=[
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": "Hi"},
            ],
        )
        assert req.embedding_text() == "[system]Be brief.\n[user]Hi"


class TestLexicalHelpers:
    def test_entities_skips_sentence_initial(self):
        from proxy.text import entities

        assert entities("What is the capital of Finland?") == {"finland"}
        assert entities("What is it?") == set()
        assert entities("Hi") == set()

    def test_entities_finds_real_entities(self):
        from proxy.text import entities

        assert entities("What is AI?") == {"ai"}

    def test_entities_multiword(self):
        from proxy.text import entities

        assert entities("When did World War II finish?") == {"world", "war", "ii"}

    def test_fact_types(self):
        from proxy.text import fact_types

        assert fact_types("What is the capital of France?") == {"capital"}
        assert fact_types("What is the population of France?") == {"population"}
        assert fact_types("What is the capital city?") == {"capital"}
        assert fact_types("How do I reset my password?") == set()

    def test_jaccard_basics(self):
        from proxy.text import jaccard

        assert jaccard("a b c", "a b c") == 1.0
        assert jaccard("a b", "c d") == 0.0
        assert jaccard("What is 2 + 2?", "Calculate two plus two.") > 0.0


class TestEntityVeto:
    def test_entity_swap_fires(self):
        from proxy.cache import entity_veto

        assert entity_veto(
            "What is the capital of Finland?", "What is the capital of France?"
        )

    def test_fact_swap_fires(self):
        from proxy.cache import entity_veto

        assert entity_veto(
            "What is the population of France?",
            "What is the capital of France?",
        )

    def test_alias_paraphrase_survives(self):
        """WWII vs World War II: disjoint surfaces, same entity family, but
        near-zero template overlap, so the gate holds the veto off."""
        from proxy.cache import entity_veto

        assert not entity_veto(
            "What year did WWII end?", "When did World War II finish?"
        )

    def test_identical_never_vetoes(self):
        from proxy.cache import entity_veto

        q = "What is the capital of France?"
        assert not entity_veto(q, q)

    def test_single_side_empty_never_vetoes(self):
        from proxy.cache import entity_veto

        assert not entity_veto("What is 2 + 2?", "Calculate two plus two.")
        assert not entity_veto("How do I reset my password?", "Reset my password!")

    def test_mixed_case_never_vetoes(self):
        from proxy.cache import entity_veto

        assert not entity_veto("what is the capital of france?", "WHAT IS IT?")


class TestTrustThroughRoute:
    @pytest.mark.asyncio
    async def test_stored_text_is_message_only(self, client):
        await client.post("/v1/chat/completions", json=_france())
        resp = await client.get("/cache/entries")
        texts = [e["prompt_text"] for e in resp.json()["entries"]]
        assert texts == ["[user]What is the capital of France?"]

    @pytest.mark.asyncio
    async def test_spotlight_entity_swaps_miss(self, client):
        seed = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "What is the capital of France?"}],
        }
        assert (await _outcome(client, seed))["outcome"] == "MISS"
        for country in ("Finland", "Norway", "Japan"):
            meta = await _outcome(
                client,
                {
                    "model": "gpt-3.5-turbo",
                    "messages": [
                        {
                            "role": "user",
                            "content": f"What is the capital of {country}?",
                        }
                    ],
                },
            )
            assert meta["outcome"] == "MISS", country
        meta = await _outcome(
            client,
            {
                "model": "gpt-3.5-turbo",
                "messages": [
                    {"role": "user", "content": "What is the population of France?"}
                ],
            },
        )
        assert meta["outcome"] == "MISS"

    @pytest.mark.asyncio
    async def test_paraphrase_recall_preserved(self, client):
        seed = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "What year did WWII end?"}],
        }
        assert (await _outcome(client, seed))["outcome"] == "MISS"
        meta = await _outcome(
            client,
            {
                "model": "gpt-3.5-turbo",
                "messages": [
                    {"role": "user", "content": "When did World War II finish?"}
                ],
            },
        )
        assert meta["outcome"] == "HIT"

    @pytest.mark.asyncio
    async def test_vetoed_prompt_still_caches_exactly(self, client):
        finland = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {"role": "user", "content": "What is the capital of Finland?"}
            ],
        }
        france = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "What is the capital of France?"}],
        }
        assert (await _outcome(client, france))["outcome"] == "MISS"
        assert (await _outcome(client, finland))["outcome"] == "MISS"
        meta = await _outcome(client, finland)
        assert meta["outcome"] == "HIT"
        assert meta["similarity_score"] == 1.0

    @pytest.mark.asyncio
    async def test_cross_model_isolation_on_identical_messages(self, client):
        assert (await _outcome(client, _france("gpt-3.5-turbo")))["outcome"] == "MISS"
        assert (await _outcome(client, _france("gpt-4")))["outcome"] == "MISS"
        assert (await _outcome(client, _france("gpt-3.5-turbo")))["outcome"] == "HIT"
        assert (await _outcome(client, _france("gpt-4")))["outcome"] == "HIT"

    @pytest.mark.asyncio
    async def test_veto_logs_as_miss(self, client):
        await _outcome(
            client,
            {
                "model": "gpt-3.5-turbo",
                "messages": [
                    {"role": "user", "content": "What is the capital of France?"}
                ],
            },
        )
        await _outcome(
            client,
            {
                "model": "gpt-3.5-turbo",
                "messages": [
                    {"role": "user", "content": "What is the capital of Finland?"}
                ],
            },
        )
        resp = await client.get("/logs/recent?limit=5")
        outcomes = [r["outcome"] for r in resp.json()["logs"]]
        assert "MISS" in outcomes

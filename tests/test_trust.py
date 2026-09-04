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


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """Tmp database for direct store()/lookup() tests (mirrors test_cache.py).

    Route tests bring their own `client` fixture which re-points the path
    again — harmless layering, same isolation guarantee.
    """
    import tempfile

    from proxy.config import get_settings
    from proxy.database import init_db, seed_test_pairs

    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_trust_")
    os.close(fd)
    monkeypatch.setenv("CACHE_DB_PATH", path)
    get_settings.cache_clear()
    init_db()
    seed_test_pairs()
    yield
    get_settings.cache_clear()
    try:
        os.unlink(path)
    except OSError:
        pass


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


class TestSemanticVeto:
    def test_entity_swap_fires(self):
        from proxy.cache import semantic_veto

        assert semantic_veto(
            "What is the capital of Finland?", "What is the capital of France?"
        )

    def test_fact_swap_fires(self):
        from proxy.cache import semantic_veto

        assert semantic_veto(
            "What is the population of France?",
            "What is the capital of France?",
        )

    def test_alias_paraphrase_survives(self):
        """WWII vs World War II: disjoint surfaces, same entity family, but
        near-zero template overlap, so the gate holds the veto off."""
        from proxy.cache import semantic_veto

        assert not semantic_veto(
            "What year did WWII end?", "When did World War II finish?"
        )

    def test_identical_never_vetoes(self):
        from proxy.cache import semantic_veto

        q = "What is the capital of France?"
        assert not semantic_veto(q, q)

    def test_single_side_empty_never_vetoes(self):
        from proxy.cache import semantic_veto

        assert not semantic_veto("What is 2 + 2?", "Calculate two plus two.")
        assert not semantic_veto("How do I reset my password?", "Reset my password!")

    def test_mixed_case_never_vetoes(self):
        from proxy.cache import semantic_veto

        assert not semantic_veto("what is the capital of france?", "WHAT IS IT?")

    def test_degenerate_candidate_skipped(self):
        """A cached entry with no content words (":)") must never win a
        semantic lookup, however short the query is (measured: unrelated
        prompts scoring 0.88-0.94 against such an entry)."""
        from proxy.cache import lookup, store

        store("[user]:)", {"ok": True}, "gpt-3.5-turbo")
        assert lookup("[user]hello world") is None
        assert lookup("[user]aaaaaaaaaaaaaaaaaaaa") is None

    def test_degenerate_candidate_exact_repeat_still_hits(self):
        from proxy.cache import lookup, store

        store("[user]:)", {"ok": True}, "gpt-3.5-turbo")
        hit = lookup("[user]:)")
        assert hit is not None
        assert hit["similarity_score"] == 1.0

    def test_labeled_entries_all_have_content_words(self):
        """Recall-safety proof for the degenerate skip: no labeled cache
        entry (pair-A side) is content-free, so the rule cannot veto one."""
        import json
        from pathlib import Path

        from proxy.text import content_words, strip_tags

        pairs = json.loads(
            Path("data/labeled_test_pairs.json").read_text(encoding="utf-8")
        )["pairs"]
        bare = [
            p["pair_id"] for p in pairs if not content_words(strip_tags(p["prompt_a"]))
        ]
        assert bare == []


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


class TestPurgeAudit:
    def test_purge_writes_audit_row(self, monkeypatch, tmp_path):
        from proxy.cache import last_purge, purge, store
        from proxy.config import get_settings
        from proxy.database import init_db

        monkeypatch.setenv("CACHE_DB_PATH", str(tmp_path / "audit.db"))
        get_settings.cache_clear()
        try:
            init_db()
            assert last_purge() is None
            eid = store("[user]audit me", {"ok": True}, "gpt-3.5-turbo")
            assert purge(entry_id=eid, actor="tester") == 1
            row = last_purge()
            assert row["purged_count"] == 1
            assert row["entry_id"] == eid
            assert row["actor"] == "tester"
            assert row["timestamp"] > 0
        finally:
            get_settings.cache_clear()

    def test_full_purge_audit_defaults(self, monkeypatch, tmp_path):
        from proxy.cache import last_purge, purge
        from proxy.config import get_settings
        from proxy.database import init_db

        monkeypatch.setenv("CACHE_DB_PATH", str(tmp_path / "audit2.db"))
        get_settings.cache_clear()
        try:
            init_db()
            assert purge() == 0
            row = last_purge()
            assert row["purged_count"] == 0
            assert row["entry_id"] is None
            assert row["actor"] == "admin"
        finally:
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_metrics_carries_last_purge(self, client):
        await client.post("/v1/chat/completions", json=_france())
        resp = await client.get("/metrics")
        assert resp.json()["last_purge"] is None
        purge_resp = await client.post("/cache/purge", json={})
        assert purge_resp.status_code == 200
        m = (await client.get("/metrics")).json()
        assert m["last_purge"]["purged_count"] == 1
        assert m["last_purge"]["entry_id"] is None
        # ASGI transport reports the real client IP (127.0.0.1), which is
        # exactly the identity the route records.
        assert m["last_purge"]["actor"] == "127.0.0.1"


class TestVetoNeverFiresOnPositives:
    """Systematic recall guard: the veto must stay silent on every labeled
    paraphrase, whatever future tuning does to the bands above."""

    def test_no_labeled_positive_is_vetoed(self):
        import json
        from pathlib import Path

        from proxy.cache import semantic_veto

        pairs = json.loads(
            Path("data/labeled_test_pairs.json").read_text(encoding="utf-8")
        )["pairs"]
        positives = [p for p in pairs if p["should_match"]]
        assert len(positives) == 16
        for p in positives:
            assert not semantic_veto(p["prompt_a"], p["prompt_b"]), p["pair_id"]


class TestVetoSignals:
    def test_template_collision_fires(self):
        from proxy.cache import semantic_veto

        assert semantic_veto(
            "How do I change my username?", "How do I reset my password?"
        )
        assert semantic_veto(
            "How do I update my password?", "How do I reset my password?"
        )

    def test_short_synonym_swap_exempt(self):
        """Phase 10: "see you later"/"see you soon" (sim 0.96) vetoed by Fix C
        — content 0.333, template 0.5 — on a two-shared-token "skeleton".
        The shared-count gate exempts it; the embedding decides (HIT)."""
        from proxy.cache import semantic_veto

        assert not semantic_veto("see you later", "see you soon")
        assert not semantic_veto("see you later!", "see you soon!")

    def test_template_gate_boundary_three_shared_tokens(self):
        """door/window shares exactly three ({is,the,open}) — still vetoed."""
        from proxy.cache import semantic_veto

        assert semantic_veto("Is the door open?", "Is the window open?")

    def test_typo_bridge_saves(self):
        from proxy.cache import semantic_veto

        assert not semantic_veto(
            "What is the captial of France?", "What is the capital of France?"
        )

    def test_negation_mismatch_fires(self):
        from proxy.cache import semantic_veto

        assert semantic_veto(
            "Is Paris the capital of France?", "Is Paris not the capital?"
        )
        assert semantic_veto("How do I enable X?", "How do I disable X?")
        assert not semantic_veto(
            "My laptop won't turn on.", "My laptop does not start."
        )

    def test_antonym_swap_fires(self):
        from proxy.cache import semantic_veto

        assert semantic_veto("Is the door open?", "Is the door closed?")
        # Different questions, different objects: veto is the correct
        # outcome here too (door vs window share only a template).
        assert semantic_veto("Is the door open?", "Is the window open?")

    def test_number_mismatch_fires(self):
        from proxy.cache import semantic_veto

        assert semantic_veto("What is 15 percent of 200?", "What is 20 percent of 200?")
        assert not semantic_veto("What is 2 + 2?", "Calculate two plus two.")
        assert not semantic_veto(
            "What is the capital of France?", "Tell me the capital."
        )

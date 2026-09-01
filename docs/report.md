# Project Report — Semantic Caching Layer for LLM Cost Reduction

> **Last updated:** 2026-09-01 (verification pass) · **Status:** v1 scope shipped end-to-end in the canonical remote tree (114/114 tests verified); the local Desktop working tree lost 2 directories to a folder move — both verified recoverable byte-identical (see §3 and `todos.md` P0).

## 1. What this is

A drop-in FastAPI proxy that sits in front of any OpenAI-compatible LLM API, recognizes when a new prompt means roughly the same thing as one it has already answered, and serves the cached response instead of paying for another generation — with measured metrics proving how much it saved. Clients keep their existing OpenAI code and only change the base URL.

It also runs a BYOK (bring-your-own-key) multi-user mode in which each caller's provider key is HMAC-derived into a stable `user_id`; cache tiers, metrics, and the dashboard all scope on that ID, so two callers asking the same question get two separate cache entries and never see each other's answers.

## 2. Who it's for / what problem it solves

For developers running LLM-backed apps that hit the API with prompts that paraphrase the same underlying question all day. Every reword is otherwise billed as a fresh generation, and exact-string caching can't help. The harder real questions are: what counts as "the same question," what threshold gets that right without serving confidently wrong answers, and how do you make cache entries expire.

## 3. Current status (stated plainly, no rounding up)

**Done and working in code:**

- Two-tier lookup: O(1) SHA-256 exact-hash match, then semantic fallback with `BAAI/bge-small-en-v1.5` (384-dim, CPU) and cosine similarity on L2-normalized vectors.
- Threshold validation: 31 hand-labeled pairs (16 paraphrases + 15 near-misses), measured P/R/F1 across 7 thresholds via `POST /eval/threshold-sweep`. Default 0.85 is empirically F1-optimal (F1=0.857).
- TTL expiry, manual single-entry + full purge (`POST /cache/purge`), `X-Cache-Bypass` header.
- `GET /metrics` with hit rate, cost saved, latency (HIT vs MISS), `total_tokens_saved`, and per-user breakdown.
- BYOK multi-user: provider allowlist (`openrouter`/`gemini`), HMAC-derived user_id, per-user cache scoping, model-aware pricing, 30-day log retention with permanent `daily_metrics` rollup.
- Upstream resilience: bounded retries on 408/429/5xxx + transport errors with exponential backoff; honors `Retry-After` (and surfaces >30 s waits immediately rather than sleeping them out).
- Concurrency: single-process per-prompt-hash coalescing, shared lifespan-managed httpx client, OpenAI-shaped error responses on upstream failure (status passthrough or 502; logged as `outcome='ERROR'`, no fabricated cost/tokens).
- CI: 4-job GitHub Actions (lint, test matrix py3.10/3.11/3.12 + Windows 3.11, docker smoke, non-blocking `pip-audit` SARIF), BGE model baked into the image, CPU-only torch pinned before `requirements.txt` to block CUDA wheels.
- **114/114 tests verified passing** against the complete canonical tree (remote `main`, run 2026-09-01, 3 m 32 s); content of the local `src/` is byte-identical to the remote apart from two missing files (below).

**Two files lost from the local Desktop working tree (both verified recoverable, byte-identical, from three sources):**

- **`src/proxy/routes/chat.py` + `src/proxy/routes/__init__.py`** — `main.py:26` imports the chat router from here; without it `uvicorn src.proxy.main:app` fails on import and the test suite collapses (measured 2026-09-01: 62 passed, 4 failed, 162 errors — every failure is the same `ModuleNotFoundError: No module named 'proxy.routes'`).
- **`src/proxy/static/index.html`** — served by `/dashboard` via `FileResponse`; without it `/dashboard` 500s.

Root cause (established, not speculation): the project was moved out of an OneDrive-synced path; the OneDrive copy at `C:\Users\arpan.ARPAN\OneDrive\Desktop\projects\...` still contains both files and the full 11-commit git history (verified; its `.git` needed only `HEAD` + `config` re-created — now done), and GitHub remote `main` has them too. All three copies are content-identical. **Fix:** copy the two directories from either source (plain file copy works; the Desktop `.git` is a non-functional stub, so see `todos.md` P0 for the two verified restore routes). Then re-run `python -m pytest tests/ -q` (expect 114) and start uvicorn.

**Not done / requires owner:**

- Live cloud deploy to Render (artifacts in place: `Dockerfile`, `render.yaml`, `Procfile`; defaults to `MOCK_LLM=true`).
- The pre-deploy verification runbook in `LAUNCH_CHECKLIST.md` (BYOK isolation with two real keys, upstream 401/400 contract, dashboard per-user scoping).
- Stretch goal: wire this proxy in front of the sibling RAG / Agent projects and report real before/after cost numbers.

## 4. Tech stack (one-line "why" for non-obvious choices)

| Layer | Choice | Why |
|---|---|---|
| Proxy server | FastAPI + Uvicorn | Async, trivial to mirror the OpenAI request/response shape; same framework as the sibling projects so reuse is real |
| LLM forwarding | httpx (async) | Native async client, connection pooling on a lifespan-managed shared client |
| Validation | Pydantic v2 | OpenAI-shaped models that drive the contract for both incoming requests and outgoing responses |
| Embeddings | `sentence-transformers` · `BAAI/bge-small-en-v1.5` (CPU) | 384-dim, L2-normalized, good EN quality, small enough to bake into a Docker image; pinned to keep threshold reproducibility |
| Vector math | numpy | Cosine reduces to `np.dot` on unit vectors; one 2D `embed_texts()` call returns the whole batch |
| Storage | SQLite (WAL, foreign keys ON) | Single-file, no extra service; portable enough to swap to Postgres/Redis later; one shared schema already includes `user_id` scoping, `daily_metrics` rollup, and a composite uniqueness index `(prompt_hash, user_id)` |
| Dashboard | FastAPI + Chart.js via CDN (single service) | Zero new Python deps, deploys as one unit; the static directory was lost from the working tree and has to be restored |
| Testing | pytest + pytest-asyncio | 114 tests; per-test DB isolation via `tmp_path`; `MOCK_LLM=true` workflow-wide |

## 6. Architecture at a glance

```
Client → POST /v1/chat/completions  (OpenAI-shaped; carries BYOK key + provider)
  → FastAPI proxy:
       1. Resolve user_id = HMAC-SHA256(USER_ID_PEPPER, Authorization Bearer)[:24]
       2. Bypass header?  → forward upstream, log as BYPASS, return
       3. Exact-hash lookup (composite UNIQUE(prompt_hash, user_id))
            HIT → return cached response, log HIT (latency measured end-to-end)
       4. Semantic lookup (cosine ≥ SIMILARITY_THRESHOLD, scoped to user_id + model)
            HIT → return cached response, log HIT
       5. Else → forward_to_llm(client, api_key, base_url)
            upstream error → OpenAI-shaped error response, log ERROR (no cache write)
            upstream 200 → embed prompt, store (prompt, embedding, response, model,
            user_id, expires_at) under composite key, log MISS, return
```

Every request — HIT, MISS, BYPASS, or ERROR — writes a `request_log` row with latency, tokens, model-aware estimated USD cost, similarity score (when matched), and `user_id`. `GET /metrics` and `/dashboard` aggregate from that table and union in the permanent `daily_metrics` rollup so lifetime totals survive the 30-day raw retention prune.

## 7. Features that actually work today, demoable

- **Mock-mode end-to-end:** `MOCK_LLM=true` → `uvicorn src.proxy.main:app` → first identical request: `cache_metadata.outcome == "MISS"`, second: `HIT`, similarity 1.0; a paraphrase: `HIT` with similarity ~0.9.
- **Threshold sweep:** `POST /eval/threshold-sweep` with any threshold list returns P/R/F1 against the seeded 31 pairs.
- **Metrics:** `GET /metrics` returns hit rate, total requests, total tokens saved, total cost saved, average HIT vs MISS latency, and a per-user breakdown table.
- **Cache browser:** `GET /cache/entries?q=France` lists entries newest-first with substring filter.
- **Recent logs:** `GET /logs/recent?limit=50` returns the most recent request-log rows for the live log panel.
- **Admin endpoints (`/cache/purge`, `/eval/threshold-sweep`, `/dashboard`) require `Authorization: Bearer <ADMIN_TOKEN>` when `ADMIN_TOKEN` is set; unset = open (demo only).**

## 8. Testing / quality snapshot

- **114 tests** collected and **114 passed** against the complete canonical tree (remote `main` extracted and run 2026-09-01, 3 m 32 s; Windows py3.11.9). The local Desktop tree currently shows 62 passed / 4 failed / 162 errors — every single failure is the one missing module `proxy.routes` (verified: same `ModuleNotFoundError` in all of them); restoring the two lost directories returns it to 114.
- Coverage: unit + integration across `tests/test_api.py` (55), `test_cache.py` (40), `test_embedding.py` (8), `test_eval.py` (8), `test_migration.py` (3) — exact match, semantic paraphrase hits, model isolation, TTL expiry, two-user cache isolation, error contract, upstream retries, payload fidelity (no Pydantic-default leakage), coalescing, ADMIN_TOKEN auth, semantic-scan guardrail, embedding-deserialization hardening, log retention rollup, and the BYOK schema migration.
- **CI matrix:** py3.10 / 3.11 / 3.12 on Linux + py3.11 on Windows; `ruff` lint job; `docker-smoke` job builds the image with GHA cache, asserts `torch.cuda.is_available()` is `False` in it, and runs a 22-check black-box HTTP smoke suite against the containerized uvicorn; `pip-audit` publishes SARIF to the Security tab (non-blocking).
- **ruff:** the CI-pinned floor (`ruff>=0.6.0`) is clean; a newer local ruff 0.16.4 flags 10 import-sort `I001` warnings in `tests/test_api.py`/`test_cache.py` (cosmetic, auto-fixable with `--fix`). `ruff format --check` remains deliberately not gated (16 files would need reformatting; tracked in todos.md P2).

## 9. The single most interesting decision or trade-off

**The similarity threshold = 0.85, justified by a measured F1 curve on a labeled set — not by vibes.** Below 0.85, near-miss antonym pairs (e.g. *"Translate 'hello' to Spanish."* vs *"Translate 'goodbye' to Spanish."* at cosine 0.864) start getting served as wrong cached answers. Above 0.88, genuine paraphrases (*"What is 2 + 2?"* ↔ *"Calculate two plus two."* at 0.860) stop hitting the cache entirely — which silently destroys the project's cost-saving purpose. 0.85 sits at the knee: F1 = 0.857, perfect for interview conversations because the answer is data-driven, not hand-waved. The full curve, borderline-pair tables, and a methodology caveat (pairwise-vs-scan-max is a conservative lower bound) live in `docs/THRESHOLD_ANALYSIS.md`.

## 10. One-paragraph summary

A drop-in FastAPI proxy that caches both exact and semantically-similar prompts in front of any OpenAI-compatible LLM, with TTL, manual purge, bypass, and a measured threshold curve that lands on 0.85 as the F1-optimal default. Phase 7 added BYOK multi-user mode with a provider allowlist, HMAC-derived user isolation, per-user metrics, and a permanent rollup so lifetime cost-saved numbers survive a 30-day log prune. The canonical codebase is feature-complete and verified: 114/114 tests pass against the remote tree, and CI runs lint + a 4-way test matrix + a Docker smoke suite. The local Desktop copy is the same project minus two directories (`routes/`, `static/`) lost in a folder move out of OneDrive — both verified byte-identical on the GitHub remote and the old OneDrive copy, restorable with a plain file copy or a git-repo repair + checkout (exact commands in `todos.md` P0). The interesting interview story isn't the cache; it's the threshold choice, and the data backing it is right there in the repo.
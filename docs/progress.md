# 📊 Project Progress — Semantic Caching Layer for LLM Cost Reduction

> **Last updated:** 2026-08-25  
> **Overall completion:** ~85 % of v1 scope  
> **Current stage:** Phases 1–5 complete (incl. live dashboard at `/dashboard`). Phase 6 artifacts built and Docker-verified locally; the actual cloud deploy + git commits await user action.

---

## Legend

| Symbol | Meaning |
|--------|---------|
| ✅ | Done — code merged, tested, verified |
| 🔧 | Partially done — implemented but incomplete or untested |
| ❌ | Not started |
| ⏭️ | Out of scope / stretch |

---

## Phase 1 — Proxy Skeleton + Exact-Match Cache (Target: 2 days)

**Status: ✅ COMPLETE**  
**Git commit:** `0157904` — *"phase 1: proxy skeleton + exact match cache (passing e2e tests)"*

| # | Requirement | Status | Evidence |
|---|------------|--------|----------|
| 1.1 | FastAPI service mirroring OpenAI `/v1/chat/completions` request/response shape | ✅ | [`main.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/src/proxy/main.py) — FastAPI app with lifespan, version 0.2.0 |
| 1.2 | OpenAI-shaped request model (`ChatCompletionRequest`) with all standard params | ✅ | [`models.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/src/proxy/models.py) L17–L43 — `model`, `messages`, `temperature`, `max_tokens`, `top_p`, `n`, `stream`, `stop`, `presence_penalty`, `frequency_penalty`, `user` |
| 1.3 | OpenAI-shaped response model (`ChatCompletionResponse`) with `choices`, `usage`, `cache_metadata` | ✅ | [`models.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/src/proxy/models.py) L74–L81 |
| 1.4 | Exact-string-match cache via SHA-256 hash to validate proxy plumbing end-to-end | ✅ | [`cache.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/src/proxy/cache.py) `_hash_prompt()` + `_exact_lookup()` — SHA-256 hash index in `cache_entries.prompt_hash` |
| 1.5 | Repeated identical request returns cached response with `cache_metadata` populated | ✅ | [`test_api.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/tests/test_api.py) `test_identical_request_hit` — asserts `outcome == "HIT"` and `similarity_score == 1.0` |
| 1.6 | SQLite database with `cache_entries`, `request_log`, `labeled_test_pairs` tables | ✅ | [`database.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/src/proxy/database.py) `init_db()` — all three tables with correct schema, indexes, and foreign keys |
| 1.7 | LLM forwarding client (real + mock mode) | ✅ | [`llm_client.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/src/proxy/llm_client.py) — `forward_to_llm()` with `MOCK_LLM` toggle, `_mock_response()` echoes last user message |
| 1.8 | Configuration via environment variables | ✅ | [`config.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/src/proxy/config.py) — `Settings` dataclass reading from `os.getenv()` |
| 1.9 | Health endpoint | ✅ | `GET /health` → `{"status": "ok", "phase": 2}` |

### Phase 1 Deliverables
- **Files created:** `main.py`, `config.py`, `models.py`, `cache.py`, `database.py`, `llm_client.py`, `routes/chat.py`, `.env.example`, `requirements.txt`
- **Tests passing:** `test_api.py` (6 tests), `test_cache.py` (9 tests covering hash, exact match, purge, metrics)

---

## Phase 2 — Semantic Matching (Target: 2–3 days)

**Status: ✅ COMPLETE** (code implemented + tests pass, but NOT yet committed to git — working tree changes only)

| # | Requirement | Status | Evidence |
|---|------------|--------|----------|
| 2.1 | BGE-small-en-v1.5 embeddings on every incoming prompt | ✅ | [`embedding.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/src/proxy/embedding.py) — lazy-loaded `SentenceTransformer("BAAI/bge-small-en-v1.5")`, CPU-only, L2-normalized |
| 2.2 | Cosine similarity search against stored entries (numpy) | ✅ | [`cache.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/src/proxy/cache.py) `_semantic_lookup()` — iterates all non-expired entries, computes cosine similarity via `np.dot()` on L2-normalized vectors |
| 2.3 | Configurable similarity threshold via env var | ✅ | [`config.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/src/proxy/config.py) `SIMILARITY_THRESHOLD` env var, defaults to `0.85` |
| 2.4 | Two-tier lookup: exact hash first → semantic fallback | ✅ | [`cache.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/src/proxy/cache.py) `lookup()` calls `_exact_lookup()` first, then `_semantic_lookup()` |
| 2.5 | Embeddings stored as BLOB in `cache_entries.prompt_embedding` | ✅ | `_serialize_embedding()` / `_deserialize_embedding()` — float32 numpy ↔ raw bytes |
| 2.6 | Model warmup on startup | ✅ | [`main.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/src/proxy/main.py) lifespan — `embed_texts(["warmup hello world"])` during startup |
| 2.7 | Paraphrase detection tested | ✅ | [`test_api.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/tests/test_api.py) `test_paraphrase_semantic_hit` — "Tell me the capital of France" matches "What is the capital of France?" |
| 2.8 | Embedding unit tests | ✅ | [`test_embedding.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/tests/test_embedding.py) — 7 tests: dim, batch, empty, normalization, cosine similarity |
| 2.9 | `sentence-transformers` + `numpy` added to requirements | ✅ | [`requirements.txt`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/project3_semantic_cache/requirements.txt) — `sentence-transformers>=3.0.0`, `numpy>=1.24.0` |

### Phase 2 Deliverables
- **New files:** `embedding.py`, `test_embedding.py`, `test_cache.py` (expanded), `pyproject.toml`
- **Modified:** `cache.py` (added semantic lookup + store with embeddings), `main.py` (model warmup), `chat.py` (two-tier lookup), `requirements.txt`

---

## Phase 3 — Threshold Validation (Target: 2 days)

**Status: ✅ COMPLETE**

| # | Requirement | Status | Notes |
|---|------------|--------|-------|
| 3.1 | Author `LabeledTestPair` set (≥20–30 pairs, mix of "should match" and "should NOT match") | ✅ | [`database.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/src/proxy/database.py) `seed_test_pairs()` — **31 pairs** (16 should-match + 15 should-not-match) incl. edge cases: short prompts, typos ("captial"), code snippets. Labels empirically validated against real BGE similarities (`scripts/check_pairs.py`). Exported to [`data/labeled_test_pairs.json`](../data/labeled_test_pairs.json) |
| 3.2 | `ThresholdSweepRequest` / `ThresholdSweepResponse` Pydantic models | ✅ | [`models.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/src/proxy/models.py) |
| 3.3 | `POST /eval/threshold-sweep` endpoint | ✅ | New module [`eval.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/src/proxy/eval.py) — batch-embeds each unique prompt once, classifies per threshold from precomputed similarities; route in `main.py` (app v0.3.0) |
| 3.4 | Run sweep across ≥3 threshold values and document results | ✅ | Swept `[0.80…0.95]` (7 values); curve + borderline-pair analysis in [`THRESHOLD_ANALYSIS.md`](THRESHOLD_ANALYSIS.md); reproducible via `make sweep` / `scripts/run_sweep.py` |
| 3.5 | Pick a default threshold and justify it (precision/recall tradeoff) | ✅ | **0.85 confirmed F1-optimal (F1=0.857)**: antonym false positives appear below 0.85 (hello/goodbye Spanish @ 0.864), genuine paraphrases lost above (2+2 @ 0.860) |

---

## Phase 4 — Invalidation + Bypass (Target: 1–2 days)

**Status: ✅ MOSTLY COMPLETE**

| # | Requirement | Status | Evidence |
|---|------------|--------|----------|
| 4.1 | TTL-based expiry on cache entries | ✅ | [`cache.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/src/proxy/cache.py) — `store()` sets `expires_at = now + cache_default_ttl_seconds`. Both `_exact_lookup()` and `_semantic_lookup()` check `expires_at` and delete/skip expired entries |
| 4.2 | Manual `/cache/purge` endpoint (single entry + full purge) | ✅ | [`main.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/src/proxy/main.py) `POST /cache/purge` with optional `entry_id`. Foreign key references in `request_log` are safely detached before deletion |
| 4.3 | `X-Cache-Bypass` header handling end-to-end | ✅ | [`chat.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/src/proxy/routes/chat.py) L27 — reads `X-Cache-Bypass` header; if `"true"`, skips cache, logs as `"BYPASS"` |
| 4.4 | Test: bypass header test | ✅ | [`test_api.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/tests/test_api.py) `test_bypass_header` |
| 4.5 | Test: purge all / purge with log references | ✅ | [`test_cache.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/src/proxy/cache.py) — `test_purge_all`, `test_purge_single_entry`, `test_purge_all_with_log_reference`, `test_purge_single_with_log_reference` |
| 4.6 | Test: TTL expiry with short TTL in test mode | ✅ | [`test_cache.py::TestTtlExpiry`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/tests/test_cache.py) — 2 tests backdate `expires_at` (no sleeping): exact tier refuses + deletes; semantic tier's SQL filter skips expired rows via paraphrase probe |

---

## Phase 5 — Metrics + Dashboard (Target: 2 days)

**Status: ✅ COMPLETE — backend + single-service dashboard at `/dashboard`**

| # | Requirement | Status | Evidence |
|---|------------|--------|----------|
| 5.1 | `RequestLog` writes on every request (HIT, MISS, BYPASS) | ✅ | [`chat.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/src/proxy/routes/chat.py) — `log_request()` called in all three code paths |
| 5.2 | `GET /metrics` aggregating hit rate, cost saved, latency | ✅ | [`cache.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/src/proxy/cache.py) `get_metrics()` |
| 5.3 | Token/cost estimation | ✅ | [`chat.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/src/proxy/routes/chat.py) `_estimate_cost()` — gpt-3.5-turbo pricing ($0.50/1M input, $1.50/1M output) |
| 5.4 | Metrics Dashboard page (hit rate, cost saved, latency comparison) | ✅ | [`static/index.html`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/src/proxy/static/index.html) — hit-rate card + HIT/MISS doughnut, cost-saved card, latency bar chart, 5 s auto-refresh toggle |
| 5.5 | Cache Browser page (searchable table, manual purge per row) | ✅ | Cache tab — server-side `?q=` search via new `GET /cache/entries`; per-row Purge + Purge ALL with confirms |
| 5.6 | Threshold Sweep page (UI for running/viewing sweep results) | ✅ | Sweep tab — editable thresholds → P/R/F1 line chart + table, best-F1 row highlighted, links to THRESHOLD_ANALYSIS.md |
| 5.7 | Live Request Log page (recent requests with outcome, score, latency, cost) | ✅ | Log tab — polls new `GET /logs/recent?limit=` every 4 s; colored outcome badges |

> Technology decision: **FastAPI + Chart.js (single service)** over Streamlit — zero new Python deps, one deployable unit for Phase 6.

---

## Phase 6 — Deploy + Integrate (Stretch)

**Status: 🔧 ARTIFACTS COMPLETE & LOCALLY VERIFIED — live deploy requires user account**

| # | Requirement | Status | Notes |
|---|------------|--------|-------|
| 6.0 | Deployment artifacts | ✅ | `Dockerfile` (CPU torch + baked model, ~2.2 GB, ~14 s cold start) + `.dockerignore` + `render.yaml` (Blueprint, health check `/health`) + `Procfile`. Docker build/run verified locally: MISS→HIT + dashboard work in-container |
| 6.1 | Deploy proxy to Render/Railway free tier | ⏳ | Push repo → Render "New + → Blueprint" (or Railway from Procfile). Defaults to `MOCK_LLM=true` |
| 6.2 | Wire in front of Project 01 (RAG) or Project 02 (Agent) | ❌ | Stretch goal |
| 6.3 | Report real before/after cost numbers | ❌ | Depends on 6.2; **set API-key spend cap before enabling real LLM mode** |

---

## Core Requirements Mapping (from PRD)

| PRD # | Requirement | Status | Where |
|-------|------------|--------|-------|
| 01 | Proxy service between client and LLM API | ✅ | Phase 1 |
| 02 | On cache miss, embed + store with similarity index | ✅ | Phase 2 |
| 03 | On new request, embed + compare + serve if above threshold | ✅ | Phase 2 |
| 04 | Configurable threshold + precision/recall documentation | ✅ | `SIMILARITY_THRESHOLD` env var + measured curve across 7 thresholds in [`THRESHOLD_ANALYSIS.md`](THRESHOLD_ANALYSIS.md); default 0.85 justified by F1 peak |
| 05 | Cache invalidation: TTL + manual purge | ✅ | Phase 4 |
| 06 | Track + expose metrics: hit rate, cost saved, latency | ✅ | Phase 5 (backend only) |
| 07 | Bypass mechanism (header/flag) | ✅ | Phase 4 |

---

## Success Metrics (from Master Guide §3)

| Metric | Status | Notes |
|--------|--------|-------|
| Documented precision/recall curve across ≥3 threshold values | ✅ | 7 thresholds measured; see [`THRESHOLD_ANALYSIS.md`](THRESHOLD_ANALYSIS.md) + README table |
| Live dashboard showing hit rate + cumulative cost saved | ❌ | Backend `/metrics` done; dashboard UI pending |
| Drop-in compatibility (same API shape as underlying LLM) | ✅ | Mirrors OpenAI `/v1/chat/completions` exactly |

---

## Deliverables Checklist (from Master Guide §11)

| Deliverable | Status |
|-------------|--------|
| GitHub repo with README (problem → architecture → precision/recall table → how to run) | ✅ README populated incl. P/R table |
| `LabeledTestPair` set committed | 🔧 31 pairs in code **and** exported to [`data/labeled_test_pairs.json`](../data/labeled_test_pairs.json); *git commit itself awaiting user* |
| Live dashboard screenshot or demo video | ❌ |
| Integration stretch goal before/after cost comparison | ❌ |
| Spend cap on demo API key | ❌ N/A until deployed |

---

## Test Coverage Summary

| Test File | # Tests | What's Covered |
|-----------|---------|---------------|
| [`test_api.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/tests/test_api.py) | 30 | All Phase 1–5 endpoint tests, plus review-round additions: cross-model MISS, measured HIT latency, request coalescing (5 concurrent → 1 upstream call), upstream 429/502 OpenAI-shaped errors, no-cache-write-on-failure + ERROR log row, shared httpx client reuse, ADMIN_TOKEN auth matrix |
| [`test_cache.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/tests/test_cache.py) | 22 | Hash consistency, exact match store/retrieve, model-isolated lookups (exact + semantic tiers), two-tier lookup, purge ×4 (incl. FK regression), TTL expiry ×2, settings factory freshness + frozenness, scan-limit guardrail warning, metrics empty + after writes |
| [`test_embedding.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/tests/test_embedding.py) | 8 | Embedding dim, single/batch/empty, normalization, cosine similarity (identical, different, semantically close) |
| [`test_eval.py`](file:///c:/Users/arpan.ARPAN/OneDrive/Desktop/projects/Semantic%20caching%20layer%20for%20LLM%20cost%20reduction/tests/test_eval.py) | 8 | Sweep structure, identical-pair P/R/F1=1.0 @ t=0.999, mixed-dataset known outcomes, zero-division safety, recall monotonicity, empty inputs, dataset ≥30 pairs |
| **Total** | **68** | |

### Test Gaps
*(both gaps below were closed in the 2026-08-23 code-review fix round — see the tables at the bottom of this file)*
- ~~No tests for concurrent requests / race conditions~~ → `test_concurrent_identical_prompts_forward_once`
- ~~No error-handling test for upstream LLM API failure~~ → `TestUpstreamErrors` (3 tests)
---

## 2026-08-23 - Code-review fix round (11 issues, all resolved)

Source: deep code review of main (P0 correctness / P1 architecture / P2 reproducibility).
Every fix shipped with its proving test; full suite green after each item.

| # | Sev | Fix | Status |
|---|-----|-----|--------|
| 1 | P0 | Model name folded into cache identity: canonical_prompt() prefixes [model]; lookup()/_exact_lookup()/_semantic_lookup() accept model filter | Done + tests |
| 2 | P0 | HIT latency measured with perf_counter (was hardcoded 0.0) | Done + test |
| 3 | P0 | Single-process request coalescing per prompt hash (asyncio.Lock registry, bounded); documented multi-worker limitation | Done + concurrency test |
| 4 | P0 | Upstream httpx errors -> OpenAI-shaped JSON error (status passthrough / 502), outcome=ERROR logged, CHECK constraint widened; no fabricated cost/tokens | Done + tests |
| 5 | P1 | Optional ADMIN_TOKEN bearer auth on purge/sweep/dashboard; startup warning when unset | Done + tests |
| 6 | P1 | MAX_SEMANTIC_SCAN_ENTRIES guardrail warns once; O(n) scan documented as accepted limitation | Done + caplog test |
| 7 | P1 | Shared lifespan-managed httpx.AsyncClient on app.state; forward_to_llm(client=...) reuse with one-off fallback; SQLite pooling deliberately skipped | Done + reuse test |
| 8 | P1 | get_settings() lru_cache factory (frozen Settings); point-of-use reads; fixtures simplified via cache_clear(); import-time freeze bug gone | Done + freshness test |
| 9 | P2 | requirements-dev.txt (pytest/pytest-asyncio/ruff), Makefile + README install updated; ruff lint clean across src/tests/scripts | Done, verified locally |
| 10 | P2 | Dockerfile pins torch==2.5.1+cpu BEFORE requirements (pip can't resolve CUDA); image measured 2.11 GB (docker images, 2026-08-23); torch.cuda.is_available()==False verified in-container | Done, measured |
| 11 | P2 | Methodology caveat (pairwise vs scan-max F1 lower bound) in THRESHOLD_ANALYSIS.md + README | Docs-only |

Post-round state: 68 tests passing (was 51), ruff clean, README/.env.example/TECHNICAL_DETAIL.md updated.
Note for next reader: init_db is CREATE-IF-NOT-EXISTS, so databases created before the
outcome='ERROR' constraint change keep the old 3-outcome CHECK until recreated (fresh deploys unaffected).
---

## 2026-08-23 - CI pipeline round (GitHub Actions)

New: .github/workflows/ci.yml (4 jobs), .github/dependabot.yml, scripts/smoke_test.py (22-check black-box HTTP suite, verified 22/22 against a live local uvicorn server before committing to CI), pytest-cov added to dev deps.

| Job | Covers |
|-----|--------|
| lint | ruff across src/tests/scripts |
| test | pytest matrix: py3.10/3.11/3.12 ubuntu + py3.11 windows (dev parity); CPU-only torch pre-install (Dockerfile-matched pin, avoids multi-GB CUDA wheels); HF model cache keyed per-OS; coverage.xml + junit artifacts (py311-linux leg); black-box smoke vs live uvicorn on ubuntu legs |
| docker-smoke | buildx build with type=gha mode=max layer cache; in-container torch CPU-only assertion; same smoke suite driven from a second container of the same image (--network host + ro-mounted scripts/); image-size report; logs-on-failure + always-cleanup |
| security-audit | pip-audit -r requirements.txt, job-level continue-on-error (informational) |

Design decisions:
- Action versions from 2026-current docs/examples: checkout@v7, setup-python@v6, cache@v5, setup-buildx-action@v4, build-push-action@v7, upload-artifact@v4. Dependabot keeps them fresh; SHA-pinning is the documented next hardening step once SHAs can be captured.
- MOCK_LLM=true at workflow env level = CI can never spend money (mirrors README guarantee).
- Smoke suite asserts exact metrics accounting (+5 requests), OpenAI response contract keys, similarity floors kept loose (0.80) so upstream BGE weight updates don't flake CI (unit suite gates 0.85).
- ruff format --check deliberately NOT gated: 16 files would need reformatting (tracked as follow-up).

Follow-up tweak (same day, user-approved): Dependabot pip version-bump PRs disabled via
open-pull-requests-limit: 0 (floors are cosmetic under >= pinning); vulnerability coverage
strengthened instead of removed — security-audit job now publishes pip-audit SARIF to code
scanning (Security tab alerts persist until resolved), and Dependabot's separate CVE-driven
security-update PRs remain active. github-actions ecosystem stays on weekly grouped updates.

Docs consistency pass (same day): README API reference now documents ADMIN_TOKEN gating on purge/sweep/dashboard; stale "~2.2 GB" / "45 tests" / "51 tests" claims corrected across report.md, todos.md and guide.md.


---

## 2026-08-23 - Phase 7: BYOK Production Push (7.1-7.7 code complete)

Goal: 10-15 known users bring free-tier keys (OpenRouter / Gemini) through one proxy with zero cost risk and zero cross-user cache leakage.

| Item | Shipped |
|------|---------|
| 7.1 Provider allowlist | PROVIDER_BASE_URLS {openrouter, gemini}; X-LLM-Base-URL header or provider body field (excluded from upstream payload); exact-match + normalization; non-allowlisted -> 400 before any network call. 6 tests incl. precedence + rejection-before-forward |
| 7.2 BYOK forwarding | Authorization: Bearer parsed; MOCK_LLM=false + keyless -> 401 OpenAI-shaped (server key NEVER substituted); forward_to_llm(api_key=, base_url=) with ValueError defense-in-depth. 6 tests |
| 7.3 Identity + scoping | security.py derive_user_id = HMAC-SHA256(USER_ID_PEPPER, key)[:24]; LOCAL_USER_ID='local' for keyless mock traffic; startup warning when pepper unset. SCHEMA V2 MIGRATION: cache_entries rebuilt with user_id NOT NULL DEFAULT 'local' and inline UNIQUE(prompt_hash) replaced by composite UNIQUE(prompt_hash,user_id) - closes the cross-user INSERT collision the plan missed; request_log ALTER+backfill; legacy rows land under 'local'. Both lookup tiers + store + log_request scoped. Raw key never stored/logged. Tests: determinism, cross-user exact/semantic isolation, legacy rebuild preservation+idempotency, fresh-install schema, e2e multi-user via ASGI |
| 7.4 Metrics | total_tokens_saved (HIT rows only) headline on /metrics + dashboard card; per-user breakdown table; _estimate_cost now model-aware from DEFAULT_MODEL_PRICING + MODEL_PRICING env override (prefix match), unknown models = .00. Tests: hit-only sums, per-user==global reconciliation, zero-cost unknowns, prefix inheritance, env override |
| 7.5 Persistence | render.yaml ships commented persistent-disk block (/var/data + CACHE_DB_PATH) - enablement needs paid tier, documented in blueprint comments + TECHNICAL_DETAIL |
| 7.6 Retention | daily_metrics permanent rollup table; prune_old_logs(30d) transactional roll-up-and-delete, idempotent, wired lazily into lifespan; get_metrics unions rollup+raw so lifetime totals survive pruning. Tests: rollup correctness, totals-survive boundary, idempotency |
| 7.7 Verification | Automated: multi-user/provider ASGI tests green. Manual real-provider runbook added to README BYOK section |

Post-phase state: **100 tests passing** (was 68), ruff clean. Remaining human steps: generate USER_ID_PEPPER + ADMIN_TOKEN in the deployment env, attach Render disk (optional, paid), run README pre-launch runbook with two real keys, then open access.

---

## 2026-08-25 - Hardening round (embedding deserialization guard + docs sync)

Source: code-level analysis pass. Every change verified empirically before implementation; full suite green after.

| # | Fix | Detail |
|---|-----|--------|
| 1 | P1 — `_deserialize_embedding` hardened | `np.frombuffer` silently returns a SHORTER array for a truncated blob (verified: no exception for valid-but-short float counts), so the old per-row try/except never fired and `np.dot` raised an uncaught ValueError mid-scan → HTTP 500. Deserialize now validates float-count == `embedding_dim()`, rejects zero-norm/non-finite vectors, and re-normalizes defensively — raising ValueError, which `_semantic_lookup` already catches per-row. Zero caller changes. 5 proving tests added (`TestEmbeddingDeserialization`): truncated/zero blobs raise, renorm to unit length, scan survives a corrupt row end-to-end, 5×-scaled stored vector scores identically after renorm |
| 2 | Docs — README test counts synced | "68 tests" → "105" in tech-stack table, CI section, and project-layout tree; Phase 7 (BYOK) added to the Status & roadmap checklist |
| 3 | Docs — `/health` phase marker | Was frozen at `"phase": 2`; now reports 7. Updated together with its assertion (`test_health_returns_ok`) so the suite stays green |
| 4 | Docs — report.md archived | Historical snapshot banner added at top pointing to progress.md as source of truth (content left intact) |
| 5 | Polish — `__init__.py` marker | Package docstring no longer claims "Phase 1"; todos.md known-issue resolved |
| 6 | Docs — todos.md synced | Resolved checkboxes marked (restructure commits, cost-estimation debt closed by Phase 7 model-aware pricing); known-issues table updated |

Post-round state: **105 tests passing** (was 100), ruff clean.
Known remaining limitations (deliberate, documented): O(n) semantic scan with warn-only guardrail past MAX_SEMANTIC_SCAN_ENTRIES; single-process coalescing; no upstream retry/circuit-breaker in forward_to_llm.

---

## 2026-08-25 - Upstream resilience round (bounded retries in llm_client)

Design decisions (documented inline): retry only what is safe or industry-standard —
408/429/5xx status responses (server explicitly did not succeed → no double-billing
risk) and TransportError (connect errors never reached the server; read/write
timeouts *may* have been processed upstream, but bounded retries match the major
LLM SDKs' defaults). All other 4xx fail fast on first attempt. A numeric
Retry-After header overrides computed backoff (capped at 30 s); computed backoff
is exponential from LLM_RETRY_BACKOFF_SECONDS (default 0.5 s), capped at 8 s.

| # | Item | Detail |
|---|------|--------|
| 1 | `_post_with_retries` | Shared by both client paths (lifespan-managed + one-off). Warns per attempt with status/error and next delay. Returned latency covers every attempt — honest end-to-end wait; coalescing-lock holder may hold across retries during a flap, bounded ~attempts × 8 s |
| 2 | Config | `LLM_RETRY_MAX_ATTEMPTS` (default 3 total; `1` = off) and `LLM_RETRY_BACKOFF_SECONDS` (default 0.5) added to Settings/.env.example/README config table |
| 3 | Tests | `TestUpstreamRetries` ×5 via stub httpx client + captured fake sleep: 503→200 retries once at base backoff; 401 fails fast (1 call, no sleep); ConnectError exhausts attempts=2; Retry-After: 7 honored verbatim; attempts=1 disables retrying entirely |
| 4 | Docs | todos.md known-issue resolved (circuit breaker noted out-of-scope); README configuration rows |

Post-round state: **110 tests passing** (was 105), ruff clean. Existing upstream-error
contract tests were unaffected — they monkeypatch `forward_to_llm` wholesale.

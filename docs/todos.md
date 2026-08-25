# 📋 TODO List — Semantic Caching Layer for LLM Cost Reduction

> **Last updated:** 2026-08-25  
> **Status legend:** `[ ]` = not started · `[/]` = in progress · `[x]` = done

---

## Phase 1 — Proxy Skeleton + Exact-Match Cache ✅

- [x] Stand up FastAPI service mirroring OpenAI `/v1/chat/completions` shape
- [x] Implement `ChatCompletionRequest` / `ChatCompletionResponse` Pydantic models
- [x] Implement `canonical_prompt()` for stable hash key
- [x] SQLite schema: `cache_entries`, `request_log`, `labeled_test_pairs` tables
- [x] SHA-256 exact-string-match cache (`_hash_prompt` + `_exact_lookup`)
- [x] `store()` to insert new cache entries
- [x] `purge()` to delete single entry or entire cache
- [x] `_delete_entry()` with foreign key detachment from `request_log`
- [x] LLM forwarding client with `MOCK_LLM` toggle for testing
- [x] Mock response that echoes the last user message
- [x] Rough token count estimator (`~4 chars/token`)
- [x] Configuration via env vars (`Settings` dataclass)
- [x] `.env.example` with all configurable values documented
- [x] `GET /health` endpoint
- [x] `GET /metrics` endpoint returning `hit_rate`, `total_requests`, `estimated_cost_saved_usd`, latency averages
- [x] `POST /cache/purge` endpoint
- [x] Integration tests (`test_api.py`): health, first-miss, identical-hit, metadata presence
- [x] Unit tests (`test_cache.py`): hash, exact match, purge, metrics

---

## Phase 2 — Semantic Matching ✅

- [x] Add `sentence-transformers>=3.0.0` and `numpy>=1.24.0` to `requirements.txt`
- [x] Create `embedding.py` — lazy-loaded `BAAI/bge-small-en-v1.5` on CPU
- [x] `embed_texts()` returning 2D float32 array `(N, 384)`, L2-normalized
- [x] `cosine_similarity()` via dot product on unit vectors
- [x] `embedding_dim()` returning 384
- [x] Serialize/deserialize embeddings to/from BLOB (`float32.tobytes`)
- [x] `_semantic_lookup()` — iterate non-expired entries, compute cosine sim, return best match ≥ threshold
- [x] Two-tier `lookup()`: exact hash → semantic fallback
- [x] `store()` now generates and saves embedding alongside response
- [x] Model warmup in FastAPI `lifespan` handler
- [x] Update `chat.py` to use two-tier lookup
- [x] `pyproject.toml` with pytest asyncio config
- [x] Tests: `test_embedding.py` (dim, batch, empty, normalization, cosine similarity)
- [x] Tests: `test_cache.py` expanded (two-tier exact hit, semantic paraphrase hit, unrelated miss)
- [x] Tests: `test_api.py` (paraphrase semantic hit, unrelated miss)
- [x] **Commit Phase 2 changes to git** ← committed (repo is fully committed through Phase 7 + 2026-08-25 hardening round)

---

## Phase 3 — Threshold Validation 🔧

### 3.1 — Labeled Test Pair Dataset ✅
- [x] Initial 20 pairs seeded in `seed_test_pairs()` (10 should-match, 10 should-not-match)
- [x] Expand to ≥30 pairs → **31 pairs** (16 should-match, 15 should-not-match)
- [x] Add edge cases: very short ("Hi"/"Goodbye", "What is AI?"), typos ("captial"), code snippets (`sorted(items, key=len)` paraphrase; add-vs-multiply hard negative at sim 0.845). All labels empirically validated against real BGE similarities before committing (see `scripts/check_pairs.py`)
- [x] Exported pairs to standalone JSON: [`data/labeled_test_pairs.json`](../data/labeled_test_pairs.json) via `scripts/export_test_pairs.py`

### 3.2 — Threshold Sweep Endpoint ✅
- [x] `ThresholdSweepRequest` / `ThresholdSweepResponse` / `ThresholdResult` Pydantic models defined
- [x] Implement the sweep logic in new module `src/proxy/eval.py`:
  - [x] Load all `labeled_test_pairs`, batch-embed every unique prompt **once** (2 texts per pair in a single `embed_texts` call)
  - [x] Compute cosine similarity **once per pair**, then classify at each threshold from precomputed scores (mathematically identical to per-threshold embedding, ~7× cheaper)
  - [x] Classify: if `similarity >= threshold` → predicted "should match"
  - [x] Precision = TP/(TP+FP), recall = TP/(TP+FN), F1 = 2·P·R/(P+R) with zero-division-safe conventions
  - [x] Return `ThresholdSweepResponse` with results per threshold
- [x] Register `POST /eval/threshold-sweep` route in `main.py` (app bumped to v0.3.0)

### 3.3 — Threshold Analysis & Documentation ✅
- [x] Run sweep at thresholds `[0.80, 0.82, 0.85, 0.88, 0.90, 0.93, 0.95]` via `scripts/run_sweep.py`
- [x] Documented curve + borderline-pair analysis in [`THRESHOLD_ANALYSIS.md`](THRESHOLD_ANALYSIS.md); summary table also in root README
- [x] **Default 0.85 justified with measured data: F1 peaks there (0.857)** — below it antonym pairs (hello/goodbye Spanish @ 0.864) become false hits; above it genuine paraphrases (2+2 ↔ "calculate two plus two" @ 0.860) stop hitting
- [x] Tradeoff table created (markdown; chart deferred to Phase 5 dashboard)

### 3.4 — Tests for Threshold Sweep ✅
- [x] Unit tests (`tests/test_eval.py`, 8 tests): response structure, identical-pair P/R/F1=1.0, mixed-dataset known outcomes, all-negative safe division, recall monotonicity, empty inputs, dataset size ≥30
- [x] Integration tests (`tests/test_api.py::TestThresholdSweepEndpoint`, 4 tests): HTTP structure, low-threshold recall ≥0.9 on seeded data, empty thresholds, 422 validation
- Note: float32 dot products of unit vectors land within ±1e-7 of 1.0, so exact-match assertions use threshold 0.999 (documented in tests)

---

## Phase 4 — Invalidation + Bypass ✅ (mostly)

- [x] TTL-based expiry on cache entries (`expires_at = now + cache_default_ttl_seconds`)
- [x] `_exact_lookup()` checks `expires_at`, deletes expired entry
- [x] `_semantic_lookup()` filters `expires_at > now` in SQL query
- [x] TTL configurable via `CACHE_TTL_SECONDS` env var (default: 3600s)
- [x] Manual purge endpoint: `POST /cache/purge` (single entry via `entry_id` or full purge)
- [x] Foreign key safety: `_detach_log_references()` nullifies `request_log.matched_entry_id` before deletion
- [x] `X-Cache-Bypass` header handling in `chat.py` — bypass → forward directly → log as "BYPASS"
- [x] Tests: purge all, purge single, purge with log references, bypass header
- [x] **Test: TTL expiry** — `tests/test_cache.py::TestTtlExpiry` (2 tests): backdate `expires_at` in DB, verify exact tier refuses + deletes the entry, and semantic tier's SQL filter skips expired rows (paraphrase probe so exact tier can't serve it first)

---

## Phase 5 — Metrics + Dashboard 🔧

### 5.1 — Backend Metrics (Done)
- [x] `request_log` writes on every request path (HIT, MISS, BYPASS)
- [x] Fields logged: `timestamp`, `prompt_text`, `prompt_hash`, `outcome`, `matched_entry_id`, `similarity_score`, `latency_ms`, `estimated_cost_usd`, `tokens_in`, `tokens_out`
- [x] `GET /metrics` endpoint returning aggregated data
- [x] Cost estimation using gpt-3.5-turbo pricing
- [x] Tests: `test_metrics_after_requests`, `test_empty_metrics`, `test_metrics_after_hits_and_misses`

### 5.2 — Dashboard UI ✅ (FastAPI + Chart.js — single service at `/dashboard`)
> Technology decision (2026-08-21): **FastAPI + Chart.js** over Streamlit — zero new Python deps (Chart.js via CDN), deploys as ONE unit in Phase 6, same-port integration.

- [x] **Metrics Dashboard page**
  - [x] Hit rate percentage card + HIT/MISS split doughnut chart
  - [x] Total requests counter
  - [x] Cumulative cost saved (USD, 4-decimal precision)
  - [x] Hit vs Miss latency comparison (bar chart + summary card)
  - [x] Auto-refresh every 5 s (toggleable) + manual refresh button
- [x] **Cache Browser page**
  - [x] Table of `cache_entries` (id, prompt, model, created, expires, hits, last-hit) with server-side substring search (`?q=`) + client-side re-query on type
  - [x] Manual purge action per row (with confirm)
  - [x] "Purge ALL" button (with confirm; log history preserved per FK contract)
- [x] **Threshold Sweep page** (Phase 3.2 dependency met)
  - [x] Editable thresholds input + Run button → `POST /eval/threshold-sweep`
  - [x] Precision/recall/F1 line chart + results table
  - [x] Best-F1 row auto-highlighted; default 0.85 called out in caption linking THRESHOLD_ANALYSIS.md
- [x] **Live Request Log page**
  - [x] Polls `/logs/recent?limit=` every 4 s while tab visible (limit selector 25–250)
  - [x] Each row: outcome badge (HIT/MISS/BYPASS), similarity score, latency, tokens in/out, estimated cost
- [x] Supporting read-only endpoints added: `GET /cache/entries?q=`, `GET /logs/recent?limit=` (both tested, app v0.4.0)

---

## Phase 6 — Deploy + Integrate 🔧 (artifacts done & verified locally; live deploy needs your account)

- [x] Choose platform: **Render blueprint (`render.yaml`) + Railway/Heroku Procfile both provided** — pick either
- [x] Create `Procfile` (Railway/Heroku) and `render.yaml` (Render Blueprint, health check `/health`)
- [x] Add `Dockerfile` + `.dockerignore`: CPU-only torch (`torch==2.5.1+cpu` pin, image measured **2.11 GB** on 2026-08-23 vs ~4+ with CUDA), BGE model baked in for ~14 s cold starts. **Verified locally**: build succeeds; container healthy; MISS→HIT flow + dashboard all work inside it
- [x] Deployment guide added to README (incl. free-tier caveats: ephemeral SQLite, 512 MB RAM limit)
- [ ] Set environment variables on deployment platform ← **needs your account** (defaults to `MOCK_LLM=true` = zero-spend demo)
- [ ] Set spend cap on LLM API key for public deployment ← **do this BEFORE flipping `MOCK_LLM=false`**
- [ ] Deploy and verify `/health`, `/metrics`, `/v1/chat/completions` work remotely ← push repo → Render "New + → Blueprint"
- [ ] **(Stretch)** Wire in front of Project 01 (RAG) or Project 02 (Agent)
- [ ] **(Stretch)** Run real traffic through proxy, collect before/after cost data
- [ ] **(Stretch)** Document cost savings in README

---

## Phase 0 — Repo Restructuring 🔴 HIGH PRIORITY

The current repo has several structural issues that should be fixed **before** adding more features. This avoids compounding the mess and makes the project look professional for portfolio/interview use.

### Current Problems

```
REPO ROOT (Semantic caching layer for LLM cost reduction/)
├── README.md                          ← empty
├── docs/                              ← docs live here, but…
│   ├── Project3_Semantic_Cache_MASTER_GUIDE.md
│   ├── project3_semantic_cache_PRD.txt
│   ├── project3_semantic_cache_detail.txt
│   ├── progress.md / report.md / todos.md
│
└── project3_semantic_cache/           ← ⚠️ UNNECESSARY NESTING — all source is one level too deep
    ├── .env.example
    ├── cache.db                       ← ⚠️ BINARY committed to git
    ├── pyproject.toml                 ← only has pytest config, no project metadata
    ├── requirements.txt
    ├── .serena/                       ← tool config, should be gitignored
    ├── proxy/
    │   ├── __pycache__/               ← ⚠️ COMMITTED to git
    │   ├── routes/__pycache__/        ← ⚠️ COMMITTED to git
    │   └── (source files)
    └── tests/
        └── __pycache__/               ← ⚠️ COMMITTED to git
```

**Issues identified:**
1. **Unnecessary nesting** — `project3_semantic_cache/` adds a pointless directory level. Source should live at root
2. **No `.gitignore`** — `__pycache__/`, `cache.db`, `.env`, `.pytest_cache/`, `.serena/cache/` are all tracked
3. **Build artifacts in git** — 8 `.pyc` files and a binary `cache.db` are committed
4. **Empty README** — first thing a visitor sees
5. **No project-level `pyproject.toml`** — current one only has pytest config; no `[project]` metadata
6. **No `src/` or flat layout convention** — `proxy/` package sits inside `project3_semantic_cache/` instead of at a standard location
7. **No `Makefile` / `scripts/`** — no easy way to run common tasks

### Target Structure

```
Semantic caching layer for LLM cost reduction/
├── .gitignore                      ← NEW: comprehensive gitignore
├── README.md                       ← POPULATED: problem → arch → how-to-run
├── LICENSE                         ← NEW: choose MIT or Apache-2.0
├── Makefile                        ← NEW: make run, make test, make sweep
├── pyproject.toml                  ← EXPANDED: [project] metadata + [tool.pytest]
├── requirements.txt                ← MOVED from project3_semantic_cache/
├── .env.example                    ← MOVED from project3_semantic_cache/
│
├── docs/
│   ├── PRD.md                      ← RENAMED: cleaner name
│   ├── MASTER_GUIDE.md             ← RENAMED: cleaner name
│   ├── TECHNICAL_DETAIL.md         ← RENAMED: cleaner name
│   ├── progress.md
│   ├── report.md
│   └── todos.md
│
├── src/
│   └── proxy/                      ← MOVED from project3_semantic_cache/proxy/
│       ├── __init__.py
│       ├── main.py
│       ├── config.py
│       ├── models.py
│       ├── cache.py
│       ├── database.py
│       ├── embedding.py
│       ├── llm_client.py
│       └── routes/
│           ├── __init__.py
│           └── chat.py
│
├── tests/                          ← MOVED from project3_semantic_cache/tests/
│   ├── conftest.py
│   ├── test_api.py
│   ├── test_cache.py
│   └── test_embedding.py
│
└── data/
    └── labeled_test_pairs.json     ← NEW: exported from seed_test_pairs() for reproducibility
```

### Step-by-Step Restructuring Checklist

#### Step 1 — Create `.gitignore` (do this FIRST) ✅
- [x] Create root `.gitignore` with:
  ```
  # Python
  __pycache__/
  *.py[cod]
  *.egg-info/
  dist/
  build/
  .eggs/

  # Environment
  .env
  .venv/
  venv/

  # IDE
  .vscode/
  .idea/

  # Project runtime
  cache.db
  *.db-journal
  *.db-wal

  # Testing
  .pytest_cache/
  htmlcov/
  .coverage

  # Tools
  .serena/cache/
  ```

#### Step 2 — Remove tracked artifacts from git
- [x] `git rm --cached cache.db` *(staged in index — no commits made per user instruction)*
- [x] `git rm -r --cached **/__pycache__/` *(staged)*
- [x] `git rm -r --cached **/.pytest_cache/` *(staged)*
- [x] Commit: `"chore: add .gitignore, remove tracked build artifacts"` ✅ done (repo fully committed)

#### Step 3 — Flatten the directory structure ✅
- [x] Move `project3_semantic_cache/proxy/` → `src/proxy/`
- [x] Move `project3_semantic_cache/tests/` → `tests/`
- [x] Move `project3_semantic_cache/.env.example` → `.env.example` (root)
- [x] Move `project3_semantic_cache/requirements.txt` → `requirements.txt` (root)
- [x] Delete the now-empty `project3_semantic_cache/` directory *(dir removed except an empty locked shell held by OneDrive/LSP — delete manually after unlock)*
- [x] Update root `pyproject.toml`: `testpaths = ["tests"]` + `pythonpath = ["src"]` (pytest ≥7 native resolution — no editable install needed)
- [x] Update import paths: package-relative imports unchanged; `main.py` uvicorn string → `"src.proxy.main:app"`; full suite green at new layout (45 passed)

#### Step 4 — Rename docs for clarity ✅
- [x] Convert `docs/project3_semantic_cache_PRD.txt` → `docs/PRD.md` (markdown conversion, content preserved)
- [x] Rename `docs/Project3_Semantic_Cache_MASTER_GUIDE.md` → `docs/MASTER_GUIDE.md`
- [x] Convert `docs/project3_semantic_cache_detail.txt` → `docs/TECHNICAL_DETAIL.md` (markdown tables added)

#### Step 5 — Upgrade `pyproject.toml` ✅
- [x] Add `[project]` section with name (`semantic-cache-proxy`), version (0.2.0), description, requires-python (>=3.10), dependencies *(authors/license omitted — need owner name decision)*
- [ ] Add `[project.scripts]` for CLI entry point — *deferred: an ASGI app is not a console entry point; `make run` covers it*
- [x] Keep `[tool.pytest.ini_options]` section (+ `pythonpath = ["src"]`)
- [ ] Consider adding `[tool.ruff]` or `[tool.black]` for code formatting config

#### Step 6 — Add convenience files
- [x] Create `Makefile` with targets:
  - `make install` — `pip install -r requirements.txt`
  - `make run` — `uvicorn src.proxy.main:app --reload`
  - `make test` — `pytest tests/ -v`
  - `make sweep` — `curl -X POST localhost:8000/eval/threshold-sweep ...`
  - `make lint` — `ruff check src/ tests/`
- [ ] Create `LICENSE` (MIT recommended) ← **needs copyright owner name from user**
- [x] Export labeled test pairs to `data/labeled_test_pairs.json` for reproducibility (31 pairs via `scripts/export_test_pairs.py`)

#### Step 7 — Update all internal references ✅
- [x] Update `uvicorn.run()` call in `main.py` → `"src.proxy.main:app"`
- [x] Test imports unchanged (`from proxy.… import`) — resolved via pytest `pythonpath = ["src"]`
- [x] `conftest.py` imports unchanged
- [x] All 45 tests pass after restructuring *(now 68 after the 2026-08-23 review-fix round)*

#### Step 8 — Commit the restructure
- [x] Stage all changes ✅ done
- [x] Commit: `"refactor: flatten repo structure, add src/ layout, rename docs"` ✅ done
- [x] Verify git log is clean ✅ done

---

## Documentation & Polish

- [x] **README.md** populated with:
  - [x] Project title + one-line description
  - [x] Problem statement (why this exists)
  - [x] Architecture diagram (Mermaid)
  - [x] Tech stack table
  - [x] Quick start guide (`git clone` → `pip install` → `uvicorn` → test it, mock-mode first)
  - [x] API reference (all endpoints with example curl commands)
  - [x] Precision/recall table at each threshold (Phase 3 output)
  - [x] Configuration reference (env vars table from `.env.example`)
  - [ ] Screenshot/demo video of dashboard (once built)
  - [x] Resume line
- [x] **Commit Phase 2+3 to git** ← committed
- [ ] Add `LICENSE` file (MIT) ← needs copyright owner name
- [x] Add `Makefile` for common tasks

---

## Stretch Goals (from PRD)

- [ ] **Distributed mode** — multiple proxy instances sharing one cache backend without duplicate-write races
- [ ] **Auto-tune threshold** — adjust similarity threshold based on observed hit-rate vs target false-positive rate
- [ ] **Project integration** — wire proxy in front of Project 01 (RAG) or Project 02 (Agent) and report real cost savings

---

## Known Issues & Technical Debt

| Issue | Severity | Where | Status |
|-------|----------|-------|--------|
| `__pycache__/` directories committed to git | Low | Root `.gitignore` | ✅ Resolved — `.gitignore` added, artifacts untracked |
| `cache.db` binary committed to git | Low | Should be in `.gitignore` | ✅ Resolved — ignored |
| `_semantic_lookup()` loads ALL non-expired entries into memory | Medium | `cache.py` `_semantic_lookup` — fine at demo scale but O(N) per request; warn-only guardrail past `MAX_SEMANTIC_SCAN_ENTRIES` | Open (documented limitation; swap in FAISS/sqlite-vec/pgvector at scale) |
| `_rough_token_count` uses `len(text)//4` heuristic | Low | `llm_client.py` — `tiktoken` would be more accurate | Open (mock-mode only) |
| Cost estimation hardcoded to gpt-3.5-turbo pricing | Low | `chat.py` | ✅ Resolved — model-aware `_estimate_cost` with DEFAULT_MODEL_PRICING + MODEL_PRICING override + prefix match (Phase 7.4) |
| `__init__.py` package marker says "Phase 1" | Trivial | `src/proxy/__init__.py` | ✅ Resolved 2026-08-25 |
| `_detach_log_references()` uses f-string SQL — safe here (SQL literal, not user input) but worth noting | Low | `cache.py` | Informational |
| No upstream retry/circuit-breaker in `forward_to_llm` | Medium | `llm_client.py` | ✅ Resolved 2026-08-25 — bounded retries with exponential backoff (408/429/5xx + transport errors; `Retry-After` honored); `LLM_RETRY_MAX_ATTEMPTS` / `LLM_RETRY_BACKOFF_SECONDS`. Circuit breaker remains out of scope (single-instance demo scale) |

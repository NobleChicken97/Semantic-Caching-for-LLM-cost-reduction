# Build Plan — Semantic Caching Layer for LLM Cost Reduction

> Source of truth for what was built, in what order, where it actually is, and what's still ahead. Pair with `report.md` (status snapshot) and `progress.md` (session log).

## 1. Repo / project layout (current)

```
Semantic caching layer for LLM cost reduction/
├── .env.example                     All configurable env vars with docs
├── .dockerignore                    Keeps the Docker build context small
├── Dockerfile                       CPU-only torch pin + baked BGE model
├── Makefile                         Convenience targets (install / run / test / lint / sweep)
├── Procfile                         Railway / Heroku start command
├── pyproject.toml                   [project] metadata + [tool.pytest] (asyncio_mode, pythonpath)
├── README.md                        Problem → architecture → P/R table → quick start → API
├── render.yaml                      Render Blueprint, health check on /health
├── requirements.txt                 Pinned runtime deps
├── requirements-dev.txt             pytest / pytest-asyncio / pytest-cov / ruff
│
├── data/
│   └── labeled_test_pairs.json       31 hand-labeled pairs, exported for reproducibility
│
├── scripts/
│   ├── check_pairs.py                Empirically compare labels to BGE similarities
│   ├── export_test_pairs.py          Dump DB pairs → data/labeled_test_pairs.json
│   ├── run_sweep.py                  Offline sweep runner (no server needed)
│   ├── smoke_test.py                22-check black-box HTTP suite (CI smoke job)
│   └── pip_audit_to_sarif.py         Convert pip-audit JSON → SARIF for the Security tab
│
├── src/proxy/
│   ├── __init__.py
│   ├── main.py                       FastAPI app, lifespan, health/metrics/admin routes
│   ├── config.py                     Settings (lru_cache factory) + provider allowlist
│   ├── models.py                     OpenAI-shaped + BYOK Pydantic models
│   ├── cache.py                      Two-tier lookup, store, purge, metrics, rollup
│   ├── database.py                   Schema v2, idempotent migration, seed 31 pairs
│   ├── embedding.py                  BAAI/bge-small-en-v1.5 wrapper (CPU, L2-normalized)
│   ├── llm_client.py                 forward_to_llm + bounded retries + mock
│   ├── security.py                   HMAC user_id derivation (BYOK)
│   ├── eval.py                       Threshold sweep (batch-embed once, classify per t)
│   ├── routes/
│   │   ├── __init__.py
│   │   └── chat.py                   ⚠ MISSING from working tree — see Known issues
│   └── static/
│       └── index.html                ⚠ MISSING from working tree — see Known issues
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                   MOCK_LLM=true, isolated tmp_path DB per test
│   ├── test_api.py                   End-to-end via httpx ASGITransport (29 tests)
│   ├── test_cache.py                 Unit + integration (45 tests)
│   ├── test_embedding.py             BGE wrapper (8 tests)
│   ├── test_eval.py                  Sweep (8 tests)
│   └── test_migration.py             User-scoping schema migration (8 tests)
│
└── docs/                             This folder
```

## 2. Phases / milestones with current real status

| Phase | Goal | Status | Evidence |
|---|---|---|---|
| **0** Repo restructure | Root `.gitignore`, `src/` layout, `pyproject.toml [project]`, `Makefile`, renamed docs | ✅ Done | Flat layout, README, env, Makefile, pyproject all at root; docs renamed |
| **1** Proxy skeleton + exact-match cache | FastAPI mirroring OpenAI shape; SHA-256 exact cache; mock LLM; SQLite WAL+FK | ✅ Done | `cache.py`, `database.py`, `llm_client.py`, basic tests |
| **2** Semantic matching | BGE-small embeddings; cosine similarity; configurable threshold via env; two-tier lookup | ✅ Done | `embedding.py`, `_semantic_lookup` in `cache.py` |
| **3** Threshold validation | ≥30 labeled pairs (got 31, labels empirically checked against real similarities); sweep endpoint; default justified by measured F1 | ✅ Done | `eval.py`, `seed_test_pairs`, `docs/THRESHOLD_ANALYSIS.md`, README P/R table, 0.85 F1=0.857 |
| **4** Invalidation + bypass | TTL expiry on both tiers; manual purge; `X-Cache-Bypass` header | ✅ Done | `_exact_lookup` + `_semantic_lookup` honor `expires_at`; `POST /cache/purge`; bypass header in chat handler |
| **5** Metrics + dashboard | `request_log` writes on every path; `/metrics`; dashboard for metrics / sweep / cache / log | ⚠ Partial — `/dashboard` 500s because `src/proxy/static/index.html` is missing; metrics endpoints all work | `cache.py:get_metrics`, `list_cache_entries`, `recent_logs`; the HTML page is gone |
| **6** Deploy artifacts | Dockerfile, render.yaml, Procfile; image size controlled; CI matrix | ✅ Done | Dockerfile + render.yaml + Procfile in place; image measured 2.11 GB; `docker-smoke` job green |
| **6.5** Code review fix round (11 issues) | Model-aware cache, real HIT latency, coalescing, OpenAI-shaped errors, ADMIN_TOKEN, scan guardrail, shared client, settings factory, dev reqs, Dockerfile pin, methodology caveat | ✅ Done | Test counts went 51 → 68; ruff clean |
| **7** BYOK production push | Provider allowlist, BYOK forwarding, user identity, metrics, persistence, retention, verification runbook | ✅ Done | `security.py`, `config.PROVIDER_BASE_URLS`, schema v2, `daily_metrics` rollup; 68 → 100 tests |
| **7.1** Hardening + docs sync | Embedding-deserialization guard (`np.frombuffer` short-array bug); README test counts; `/health` phase marker; archived report.md | ✅ Done | 100 → 105 tests |
| **7.2** Upstream resilience | Bounded retries (408/429/5xx + transport errors, `Retry-After` honored, >30 s fails fast); payload fidelity (Pydantic defaults stripped); upstream error detail extraction | ✅ Done | 105 → 114 tests |
| **Live cloud deploy** | Render Blueprint applied; health green; BYOK runbook with two real keys | 🔧 Artifacts + runbook done; **owner action** (Render account, env secrets) | `render.yaml` ready; `docs/LAUNCH_CHECKLIST.md` Phases A–G written |
| **Stretch: integrate with sibling project** | Wire proxy in front of RAG / Agent; report before/after cost numbers | ❌ Not started | — |

## 3. Dependencies between phases (what blocks what)

```
0 (restructure) → 1 (proxy) → 2 (semantic) → 3 (threshold)
                                              ↓
                                     4 (invalidation) → 5 (dashboard) → 6 (deploy)
                                                                     ↓
                                                          6.5 (review fixes)
                                                                     ↓
                                                          7 (BYOK) → 7.1 → 7.2
                                                                     ↓
                                                            live cloud deploy
                                                                     ↓
                                                            stretch (sibling)
```

- **1 unblocks everything else** — without the OpenAI-shaped response contract and the SQLite schema, no later phase has anything to plug into.
- **3 unblocks any honest claim of "this is interview-grade."** Without measured P/R/F1, the threshold is hand-waved.
- **6.5 and 7.1 / 7.2 each unblock the next** — each round added new behavior and the proving tests that gate shipping it.

## 4. The MVP that is actually presentable today

The missing `src/proxy/routes/chat.py` and `src/proxy/static/index.html` are verified recoverable with one command from the remote (all copies content-identical; the restored tree passed 114/114 tests when run in this session). With them back, the smallest honestly presentable version is:

- Local `uvicorn src.proxy.main:app` with `MOCK_LLM=true`.
- `POST /v1/chat/completions` works end-to-end: first call MISS, second HIT, paraphrase HIT above 0.85.
- `POST /eval/threshold-sweep` returns the full P/R/F1 curve; README quotes the F1-optimal 0.85 with the full borderline-pair analysis in `docs/THRESHOLD_ANALYSIS.md`.
- `GET /metrics` shows live hit rate, tokens saved, per-user breakdown (after at least one mock request per user).
- `GET /cache/entries?q=France` and `GET /logs/recent?limit=50` populate the dashboard tabs.
- 114 tests pass (verified against the complete tree this session); ruff clean at the CI-pinned floor; CI green on lint + py3.10/3.11/3.12 + Windows py3.11 + docker-smoke.

That's already the full v1. The remaining work is owner-side: deploy, run the BYOK verification, and optionally wire it in front of a sibling project.

## 5. Where it actually sits right now

Code is feature-complete. The local Desktop working tree lost two directories in a folder move out of OneDrive (root cause verified this session — see `progress.md` 2026-09-01 session 2):

1. **`src/proxy/routes/`** (`chat.py` + `__init__.py`) — `src/proxy/main.py:26` imports the chat router from here. Without it `uvicorn` fails on import and the test suite measures 62 passed / 4 failed / 162 errors (all one `ModuleNotFoundError`).
2. **`src/proxy/static/index.html`** — served by `FileResponse` at `/dashboard`; without it the dashboard 500s.

Everything else in the tree is content-identical to remote `main` (verified by full diff; the only other deltas are today's docs, which are newer locally, and a root `skills2use.md` that exists only on the remote). Restore — either a plain file copy from the OneDrive remnant (or a fresh clone), or repair the local repo first:

```powershell
# Option A: plain copy (verified byte-identical files, no git needed)
$src = "C:\Users\arpan.ARPAN\OneDrive\Desktop\projects\Semantic caching layer for LLM cost reduction\src\proxy"
Copy-Item "$src\routes" "src\proxy\routes" -Recurse
Copy-Item "$src\static" "src\proxy\static" -Recurse

# Option B: repair the repo, then restore via git
# (the Desktop .git is a stub — git rejects it; the OneDrive .git works after
#  this session's HEAD/config repair)
Remove-Item -Recurse -Force .git
Copy-Item "C:\Users\arpan.ARPAN\OneDrive\Desktop\projects\Semantic caching layer for LLM cost reduction\.git" ".git" -Recurse
git checkout origin/main -- src/proxy/routes src/proxy/static

python -m pytest tests/ -q    # expect 114 passed
```

The OneDrive remnant also holds the only functioning local `.git` (11 commits). Treat it as the git-history source of record until the Desktop repo is re-synced and pushed; then archive it.

## 6. Decision points still on the table

- ~~Restore the chat router: inline vs `routes/chat.py`~~ **Resolved:** restore `routes/chat.py` as-is from the remote — `main.py`, `tests/conftest.py`, and 20+ test imports all reference `proxy.routes.chat` (including `_inflight_locks` and `_estimate_cost`), so re-splitting or inlining would mean rewriting tests for zero gain.
- ~~Restore the dashboard: rebuild vs recover~~ **Resolved:** recover `static/index.html` from the remote — it is the finished Phase 5 dashboard (four tabs, Chart.js, per-user table, purge actions, sweep runner), verified present and passing smoke checks in the `docker-smoke` CI job.
- **Live deploy budget:** owner decision. Default `MOCK_LLM=true` deploy is free; switching to a real LLM upstream means a spend cap on the provider key BEFORE flipping the flag. Details in `docs/LAUNCH_CHECKLIST.md` Phase E.
- **Sibling project integration:** which one to wire in front of (RAG or Agent), and whether to add a `enable_semantic_cache_proxy` flag to that project or just demonstrate the before/after on a fixed prompt set.
- **OneDrive remnant:** archive/delete after the Desktop repo is re-synced and verified (tracked in `todos.md` P1).

## 7. What success looks like at each level

- **Code-complete (today, once the two directories come back):** all 6 phases shipped + 6.5 + 7 + 7.1 + 7.2, 114 tests green (verified), ruff clean at the pinned floor, CI green, image under 2.5 GB.
- **Demoable (today, in mock mode):** an interviewer can `make run`, hit the three endpoints, see the curve, read the threshold justification.
- **Cloud-deployed (owner action):** anyone with the URL can hit `/health` and run the BYOK runbook end-to-end with two real keys.
- **Portfolio-presentation (stretch):** before/after cost numbers from a sibling project, ideally with a side-by-side chart.
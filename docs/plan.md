# Build Plan — Semantic Caching Layer for LLM Cost Reduction

> Source of truth for what was built, in what order, where it actually is, and what's still ahead. Pair with `progress.md` (session log) and `todos.md` (open work).

## 1. Repo / project layout (current)

```
Semantic caching layer for LLM cost reduction/
├── .env.example                     All configurable env vars with docs
├── .dockerignore                    Keeps the Docker build context small
├── Dockerfile                       CPU-only torch pin + baked BGE model
├── Makefile                         Convenience targets (install / run / test / lint / sweep)
├── Procfile                         Railway / Heroku start command
├── pyproject.toml                   [tool.pytest] config only (deps live in requirements*.txt)
├── README.md                        Problem → architecture → P/R table → quick start → API
├── render.yaml                      Render Blueprint, health check on /health
├── requirements.txt                 Pinned runtime deps
├── requirements-dev.txt             pytest / pytest-asyncio / pytest-cov / ruff
│
├── data/
│   └── labeled_test_pairs.json       31 hand-labeled pairs, exported for reproducibility
│
├── scripts/
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
│   ├── security.py                   keyed-BLAKE2b user_id derivation (BYOK)
│   ├── eval.py                       Threshold sweep (batch-embed once, classify per t)
│   ├── llm_client.py                 retries + per-upstream circuit breaker + tiktoken counting
│   ├── routes/
│   │   ├── __init__.py
│   │   └── chat.py                   POST /v1/chat/completions (BYOK, two-tier cache, coalescing)
│   └── static/
│       └── index.html                Single-page Chart.js dashboard
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                   MOCK_LLM=true, isolated tmp_path DB per test
│   ├── test_api.py                   End-to-end via httpx ASGITransport (73 tests)
│   ├── test_cache.py                 Unit + integration (40 tests)
│   ├── test_embedding.py             BGE wrapper (8 tests)
│   ├── test_eval.py                  Sweep + auto-tune + drift guard (15 tests)
│   └── test_migration.py             Schema migrations (6 tests)
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
| **5** Metrics + dashboard | `request_log` writes on every path; `/metrics`; dashboard for metrics / sweep / cache / log | ✅ Done | `cache.py:get_metrics`, `list_cache_entries`, `recent_logs`; single-page Chart.js dashboard at `/dashboard` (browser auth via `/dashboard?token=`) |
| **6** Deploy artifacts | Dockerfile, render.yaml, Procfile; image size controlled; CI matrix | ✅ Done | Dockerfile + render.yaml + Procfile in place; image measured 2.11 GB; `docker-smoke` job green |
| **6.5** Code review fix round (11 issues) | Model-aware cache, real HIT latency, coalescing, OpenAI-shaped errors, ADMIN_TOKEN, scan guardrail, shared client, settings factory, dev reqs, Dockerfile pin, methodology caveat | ✅ Done | Test counts went 51 → 68; ruff clean |
| **7** BYOK production push | Provider allowlist, BYOK forwarding, user identity, metrics, persistence, retention, verification runbook | ✅ Done | `security.py`, `config.PROVIDER_BASE_URLS`, schema v2, `daily_metrics` rollup; 68 → 100 tests |
| **7.1** Hardening + docs sync | Embedding-deserialization guard (`np.frombuffer` short-array bug); README test counts; `/health` phase marker; archived report.md | ✅ Done | 100 → 105 tests |
| **7.2** Upstream resilience | Bounded retries (408/429/5xx + transport errors, `Retry-After` honored, >30 s fails fast); payload fidelity (Pydantic defaults stripped); upstream error detail extraction | ✅ Done | 105 → 114 tests |
| **7.3–7.6** Post-7.2 rounds | `/eval/auto-tune`; per-upstream circuit breaker; ruff format + CI gate; tiktoken counting; LICENSE; dataset drift guard; `?token=` dashboard auth; `/` service card | ✅ Done | `eval.py`, `llm_client.CircuitBreaker`, `security.py` (keyed BLAKE2b); 114 → 142 tests |
| **Live cloud deploy** | Render Blueprint applied; BYOK verified on the public URL with two real providers | ✅ **Launched 2026-09-02** | `https://semantic-cache-proxy.onrender.com`; all runbook checks passed (401 / MISS→HIT ×2 / 400) |
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

## 4. Current state (post-recovery, post-launch)

The recovery-era sections that lived here (missing-files narrative, OneDrive restore instructions) are resolved — full history in `progress.md` 2026-09-01/02. What is presentable today:

- **Live service:** `https://semantic-cache-proxy.onrender.com` (Render free tier, `MOCK_LLM=false`, BYOK-verified with real Gemini + OpenRouter keys; runbook checks all passed).
- **Local demo:** `uvicorn src.proxy.main:app` with `MOCK_LLM=true`, or against real providers with `SC_*` env keys; MISS → HIT on exact and paraphrase prompts above the 0.85 threshold (F1 0.857, re-validated).
- **Evaluation surface:** `POST /eval/threshold-sweep` + `POST /eval/auto-tune`; README quotes the measured curve; drift-guard test pins the published dataset.
- **Observability:** `/metrics`, `/cache/entries`, `/logs/recent`, `/dashboard?token=` (Chart.js), hourly `live-monitor` workflow asserting the deployed contracts.
- **142 tests green; ruff lint + format gated in CI; CodeQL + pip-audit clean.**

The only remaining stretch item is sibling-project integration (see §6).

## 5. Persistence posture (decided)

Render free tier = ephemeral disk: cache entries and metric counters reset on every deploy and 15-min idle spin-down. **Decided 2026-09-03: stay free, re-warm manually** (demo script / `Warm-Cache`); the Starter-disk and Postgres-swap upgrade paths are documented in `LAUNCH_CHECKLIST.md` Phase E and can be adopted any time.

## 6. Decision points still on the table

- ~~Restore the chat router: inline vs `routes/chat.py`~~ **Resolved:** restore `routes/chat.py` as-is from the remote — `main.py`, `tests/conftest.py`, and 20+ test imports all reference `proxy.routes.chat` (including `_inflight_locks` and `_estimate_cost`), so re-splitting or inlining would mean rewriting tests for zero gain.
- ~~Restore the dashboard: rebuild vs recover~~ **Resolved:** recover `static/index.html` from the remote — it is the finished Phase 5 dashboard (four tabs, Chart.js, per-user table, purge actions, sweep runner), verified present and passing smoke checks in the `docker-smoke` CI job.
- ~~Live deploy budget~~ **Resolved:** launched on Render free (`MOCK_LLM=false`, BYOK — the server never spends its own quota). Persistence decision recorded in §5.
- **Sibling project integration:** which one to wire in front of (RAG or Agent), and whether to add a `enable_semantic_cache_proxy` flag to that project or just demonstrate the before/after on a fixed prompt set.
- ~~OneDrive remnant~~ **Resolved:** deleted 2026-09-02 after safety scan (git tip was an ancestor of remote; only tool caches differed).

## 7. What success looks like at each level

- **Code-complete (today):** all phases shipped + post-7.2 hardening rounds, 142 tests green, ruff lint + format gated in CI, CodeQL/pip-audit clean, image under 2.5 GB.
- **Demoable (today, live):** an interviewer can open the service URL, watch MISS → HIT via the demo script, and read the threshold justification with borderline-pair evidence (`/eval/auto-tune`).
- **Cloud-deployed (done):** BYOK verified on the public URL with two real providers; per-user isolation proven live.
- **Portfolio-presentation (stretch):** before/after cost numbers from a sibling project, ideally with a side-by-side chart.
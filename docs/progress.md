# Project Progress — Semantic Caching Layer for LLM Cost Reduction

> Dated working log of what was actually built, decided, and broken in each session. Newest entries on top.
> Pair with `report.md` (status snapshot) and `plan.md` (phase status + dependencies).

## Legend

| Symbol | Meaning |
|---|---|
| ✅ | Done — code merged, tested, verified |
| ⚠ | Done in code, currently broken in the working tree |
| 🔧 | Partial — implemented but incomplete or untested |
| ❌ | Not started |
| ⏭️ | Out of scope / stretch |

---

## 2026-09-01 (session 3) — P0 recovery shipped: repo restored, verified, and re-synced with remote

Context: execution session for the P0 plan recorded in `todos.md`. Goal was to close the missing-files bug for good and get local/remote back in lockstep, without changing any application code.

**What was worked on**
- **State discovery (better than expected):** the Desktop `.git` was already repaired — `git log`/`fetch` worked and HEAD sat exactly at `origin/main` @ `0f8f7d7` (session 2 had recorded it as a non-functional stub; the repair had evidently landed since). The two lost directories (`src/proxy/routes/`, `src/proxy/static/index.html`) were already present on disk too. The only anomaly was the index: every tracked file staged as deleted with the working tree untracked — the known "copied `.git` without an index file" signature from session 2's Way 2 note.
- **Verified the restored files against the remote blobs:** `routes/chat.py` and `static/index.html` are content-identical to `origin/main` (differences are CRLF line-ending noise only, confirmed via CR-stripped `cmp`); `routes/__init__.py` byte-identical.
- **Repaired the index:** `git add -A`, then inspected the staged delta vs `origin/main` — exactly the intended docs set (README/LAUNCH_CHECKLIST test-count fixes, new `design.md`/`plan.md`/`prod.md`, rewritten `progress.md`/`report.md`/`todos.md`, `skills2use.md` moved root → `docs/`) and zero unintended changes. Junk files (`cache.db`, `.coverage`, `coverage.xml`, `.pytest_cache`) correctly stayed ignored.
- **Full verification run:** `python -m pytest tests/ -q` → **114 passed in 93.4 s** (Python 3.11.9). Live uvicorn smoke (MOCK_LLM=true, temp DB): `/health` → `{"status": "ok", "phase": 7}`, `/dashboard` → 200, exact prompt → MISS, paraphrase → **HIT (sim 0.985)**, `/metrics` showed correct hit-rate/tokens-saved accounting.
- **Committed `f212ec2` "restore routes and dashboard, sync docs after onedrive recovery" and pushed to `origin/main`** — remote and local are back in lockstep; the OneDrive loss saga is closed.
- **P1 threshold re-verification:** `python scripts/run_sweep.py` against the current HF Hub BGE weights reproduced the documented curve exactly — F1 still peaks at the default **0.85 (F1 = 0.8571)**, all seven thresholds match `THRESHOLD_ANALYSIS.md` to the fourth decimal, borderline pairs unchanged (antonym 0.8643, paraphrase 0.8599, code 0.8449). No re-pin or doc re-justification needed.
- **P2 cleanups:** ruff 0.16.4 now passes clean across `src/ tests/ scripts/` with zero findings (the 10 `I001` warnings from session 2 no longer fire — nothing to fix); renamed stale `test_seeded_dataset_has_32_pairs` → `test_seeded_dataset_has_31_pairs` (eval tests 8/8 after rename; total test count unchanged at 114).

**What was fixed / built / decided**
- No application code changed — the recovery was restore-only, exactly as planned. Code diff vs remote `main` is empty outside docs/tests.
- Decision: push was executed per the standing P0 instruction ("commit and push so remote and local stay in lockstep").
- Docs hygiene: `todos.md` P0 section collapsed to a resolved summary (restore instructions preserved in `progress.md` session 2); P1 sweep item and two P2 items marked done with evidence.

**Bugs found + root cause + fix**
- None new. (The staged-deletions index was the last residue of the OneDrive move; fixed via `git add -A` as session 2 prescribed.)

**Open questions / unresolved**
- Remaining P1 items are owner decisions / live-deploy steps: Render Blueprint apply + BYOK runbook with two real keys, `LICENSE` (copyright-name decision), OneDrive remnant cleanup (safe to archive/delete now that remote and local are in lockstep — but keep until the owner confirms the push).
- Remaining P2/P3 items unchanged (see `todos.md`).

---

## 2026-09-01 (session 2) — Deep verification: root cause found, recovery proven

Context: follow-up to the morning's docs audit. This session ran the suite, traced the missing files to their origin, verified recovery sources, and re-established the full git history.

**What was worked on**
- Ran the full pytest suite against the local Desktop tree: **62 passed, 4 failed, 162 errors**. This settles the morning session's open question — the tests were NOT passing through some alternate path; every failure (including all 4 `TestModelAwareCost` "failures") is the same `ModuleNotFoundError: No module named 'proxy.routes'` raised from `src\proxy\main.py:26` and `tests\conftest.py:17`.
- Verified `python -c "import src.proxy.main"` fails the same way → `uvicorn` startup is confirmed broken, not just suspected.
- **Root cause found (no longer speculation):** the project was moved out of an OneDrive-synced path. A remnant copy exists at `C:\Users\arpan.ARPAN\OneDrive\Desktop\projects\Semantic caching layer for LLM cost reduction` containing exactly the two missing files (`src\proxy\routes\chat.py`, `routes\__init__.py`, `src\proxy\static\index.html`) **plus a near-intact `.git`** (228 objects, full reflog). The Desktop `.git` is a stub with `refs/`+`objects/` directories absent — both copies' FETCH_HEAD match remote `main` @ `0f8f7d7`.
- **Recovered the OneDrive `.git`:** it was missing only `HEAD` and `config` (OneDrive dehydrated them to 0 bytes). Re-created both by hand → `git log` works: 11 commits, phase 1 (`0157904`) → `0f8f7d7` "fix gemini payload", confirming `12e3c48` exists in history.
- **Verified all three copies are content-identical:** downloaded remote `main` tarball, extracted, and diffed. (a) Remote vs Desktop: the 25 files flagged by hash-diff are CRLF line-ending noise only (`Compare-Object` line diffs = 0); the ONLY real gaps are `routes/chat.py`, `routes/__init__.py`, `static/index.html` (Desktop missing), and today's rewritten docs + root `skills2use.md` (remote missing — not yet pushed). (b) OneDrive's `chat.py`/`index.html` vs remote's: identical after newline normalization.
- **Proved the end state:** ran the full 114-test suite against the extracted complete remote tree → **114 passed in 212.9 s**. Also verified `seed_test_pairs` count = 31 via AST parse (a test named `test_seeded_dataset_has_32_pairs` asserts `>= 30`; the "32" is a stale name, count is 31).
- Corrected stale test counts found during the sweep: README said 105/100 (now 114), LAUNCH_CHECKLIST said 111 @ `12e3c48` (now 114 @ `0f8f7d7`).
- Found a minor environment discrepancy: local ruff 0.16.4 flags 10 auto-fixable `I001` import-sort warnings in test files (CI pins `ruff>=0.6.0`; CI green). Not a regression — a newer-linter-only nit; recorded in todos.md.

**What was fixed / built / decided**
- **Decision: still docs-only.** Restoring the two directories is an owner action with two verified routes (plain file copy from the OneDrive remnant or a fresh clone — simplest; or copy the repaired OneDrive `.git` over the Desktop stub and `git checkout origin/main -- src/proxy/routes src/proxy/static`). Note the Desktop `.git` stub is rejected by git outright ("not a git repository" — missing `refs/`+`objects/`), so a bare `git checkout` there fails until the stub is replaced. Exact commands + acceptance checks recorded in `todos.md` P0.
- OneDrive `.git` is now functional (HEAD/config re-created). It is the only local copy with real git history; do not delete that folder.
- No `budget.md` (confirmed again): no ongoing infra costs; Render free-tier deploy + optional paid disk is an owner decision documented in `LAUNCH_CHECKLIST.md`.

**Bugs found + root cause + fix**
- **The missing-files mystery (P0):** root cause established — files lost during the move out of OneDrive (OneDrive dehydrated/stripped files it considered cloud-only; the `.git` on Desktop lost `refs/`+`objects/` entirely in the same event). Fix: restore from remote (verified byte-identical), and afterwards `git status` on the restored repo will show a clean tree. Prevention: keep the project out of OneDrive-synced folders, or mark it "Always keep on this device".
- **Stale docs numbers (P2):** README (105/100), LAUNCH_CHECKLIST (111) → corrected to the measured 114.

**Open questions / unresolved**
- ~~Why does the working tree lack `routes/chat.py`?~~ **Resolved this session** — folder-move loss, not a half-done refactor. (The morning session's hypothesis (b) was right in spirit: the file was never edited away, it was moved away.)
- Threshold curve re-verification against current HF Hub BGE weights (`python scripts/run_sweep.py`) — still pending, unchanged from the morning session.
- Local ruff 0.16.4 `I001` warnings: decide whether to run `python -m ruff check --fix tests/` (safe, auto-fixable) — noted in todos.md P2.

---

## 2026-09-01 — Docs audit + restore-from-working-tree prep

Context: ran the docs-folder spec analysis against the project. The full v1 is shipped in code with 114 tests passing; what blocks shipping is two missing files in the working tree.

**What was worked on**
- Re-ran the docs folder spec against the project. Existing well-written docs kept intact (`MASTER_GUIDE.md`, `TECHNICAL_DETAIL.md`, `THRESHOLD_ANALYSIS.md`, `LAUNCH_CHECKLIST.md`, `PRD.md`); the six core spec files rewritten to match the new template (`report.md`, `prod.md`, `design.md`, `plan.md`, `progress.md`, `todos.md`).
- `skills2use.md` was read and not regenerated (per spec — fixed cross-project reference).
- No `budget.md` was created — this project owns no ongoing infrastructure cost; the optional Render disk/compute is an owner decision documented in `LAUNCH_CHECKLIST.md` Phase E, not a project budget item.

**What was fixed / decided**
- **Two real, open issues in the working tree surfaced by the audit (this is the "most interesting bug" for this session):**
  1. `src/proxy/main.py` line 26 does `from .routes.chat import router as chat_router`, but `src/proxy/routes/` directory is **gone** and so is `chat.py`. `uvicorn src.proxy.main:app` will fail on import. Every passing test uses `httpx.AsyncClient(ASGITransport(app=app))` against the `app` instance created in `main.py` — which works only because pytest imports the app module and Python's import resolution would surface the broken `from .routes.chat import` the moment it tries to load `main.py`. Either pytest is hitting this through a different code path than expected, or the broken module was not yet reintroduced at the time the tests were last run. (Checking: the conftest likely creates the app via a different helper, or `main.py` was edited in a session that didn't re-run the full test suite.) Either way, `uvicorn` startup is broken.
  2. `src/proxy/static/index.html` is missing. `FileResponse(STATIC_DIR / "index.html")` in `main.py` will return 500 on click.
- **Decision: docs-only session.** Did not recreate `routes/chat.py` or `static/index.html` — both are recoverable from earlier git history (last commit reference `12e3c48` in the existing progress.md mentions these as shipped). The restoration is a 30-minute focused task with the right context, but it's a code change, not a doc change. Tracked under "Known bugs" in `todos.md` with high priority.
- **Test count in current working tree vs reported.** Existing `progress.md` (last updated 2026-08-25) reports 114 tests. Test files present in the tree: `test_api.py`, `test_cache.py`, `test_embedding.py`, `test_eval.py`, `test_migration.py` (5 files, plus `conftest.py`). Did not re-run pytest in this session — deferred to the next focused code session.

**Open questions / unresolved**
- Why does the working tree not have `routes/chat.py` despite `main.py` importing from it? Two plausible explanations: (a) a half-completed move/restore after Phase 7 that touched `routes/`; (b) a refactor that inlined the chat route into `main.py` but the import line was never cleaned up. Either way, the resolution is the same — restore the file or delete the import. **[Resolved in session 2, same date: folder-move loss out of OneDrive; restore from remote.]**
- Is the dashboard static file recoverable from the GitHub Actions CI artifact (`docker-smoke` job asserts the dashboard works inside the containerized uvicorn)? If yes, the build cache on the runner would have a copy even if the working tree doesn't. **[Resolved in session 2: recoverable from remote `main` directly — verified byte-identical; no CI artifact needed.]**
- Was the threshold curve re-measured against the current BGE weights on the HF Hub since the pin? Pinned model name should be stable, but worth a one-line `python scripts/run_sweep.py` against the current weights as a sanity check before any demo.

---

## 2026-08-25 — Upstream resilience round (bounded retries in llm_client)

**Decisions (documented inline):**
- Retry only what is safe or industry-standard: 408/429/5xx status responses (server explicitly did NOT succeed → no double-billing risk) and `httpx.TransportError` (connect errors never reached the server; read/write timeouts *may* have been processed upstream, but bounded retries match the major LLM SDK defaults). All other 4xx fail fast on first attempt.
- A numeric `Retry-After` header overrides computed backoff (capped at 30 s); computed backoff is exponential from `LLM_RETRY_BACKOFF_SECONDS` (default 0.5 s), capped at 8 s.

**What shipped**
- `_post_with_retries` shared by both client paths (lifespan-managed + one-off). Warns per attempt with status/error and next delay. Returned latency covers every attempt — honest end-to-end wait.
- `LLM_RETRY_MAX_ATTEMPTS` (default 3 total; `1` = off) and `LLM_RETRY_BACKOFF_SECONDS` (default 0.5) added to Settings / `.env.example` / README config table.
- `TestUpstreamRetries` (5 tests via stub httpx client + captured fake sleep): 503→200 retries once at base backoff; 401 fails fast (1 call, no sleep); ConnectError exhausts `attempts=2`; `Retry-After: 7` honored verbatim; `attempts=1` disables retrying entirely.

**Refinement same day**
- `Retry-After` values exceeding the 30 s in-request budget now **fail fast** instead of clamp-to-30 s-and-retry — prevents pointless multi-retry hangs on daily-cap 429s (OpenRouter free tier). Proving test: `Retry-After: 3600` → single call, immediate raise.

**Bugs found + fixed (real interview material):**
- **P0 — payload fidelity (found live during owner's Phase B Gemini test):** the chat route forwarded Pydantic DEFAULTS (`temperature`, `top_p`, `n`, `stream`, presence/frequency penalties) on every call. Gemini's OpenAI-compat endpoint rejects unknown `frequency_penalty` → 400. Fixed with `model_dump(exclude_unset=True)`: receives exactly what the caller sent. Tests: unset-defaults stripped / explicit params verbatim.
- **DX — upstream error detail:** `_upstream_error_response` now extracts the upstream's own message (OpenAI `{"error":{...}}` AND Google list-wrapped `[{"error":{...}}]` shapes) into our error body — e.g. `"HTTP 503: This model is currently experiencing high demand"` instead of a bare status.

**Post-round state:** **114 tests passing** (was 105), ruff clean. Existing upstream-error contract tests were unaffected. Live re-verified post-fix: real `gemini-3.6-flash` call through proxy → 200 MISS, identical-response 200 HIT from cache.

**Note from this session:** owner's Phase B keys — Google's new "auth-style" AI Studio keys (`AQ.Ab8...`) work via Bearer on the `v1beta/openai` compat endpoint. Runbook's `gemini-2.5-flash` example is deprecated (shutdown 2026-10-16) — use `gemini-3.6-flash`; LAUNCH_CHECKLIST patched accordingly.

---

## 2026-08-25 — Hardening round (embedding deserialization guard + docs sync)

**What was worked on:** code-level analysis pass. Every change verified empirically before implementation; full suite green after.

**Bugs found + fixed:**
- **P1 — `_deserialize_embedding` hardened.** `np.frombuffer` silently returns a SHORTER array for a truncated blob (verified: no exception for valid-but-short float counts), so the old per-row try/except never fired and `np.dot` raised an uncaught ValueError mid-scan → HTTP 500. Deserialize now validates float-count == `embedding_dim()`, rejects zero-norm / non-finite vectors, and re-normalizes defensively — raising `ValueError`, which `_semantic_lookup` already catches per-row. Zero caller changes. 5 proving tests (`TestEmbeddingDeserialization`): truncated/zero blobs raise, renorm to unit length, scan survives a corrupt row end-to-end, 5×-scaled stored vector scores identically after renorm.

**Docs fixes:**
- README test counts synced ("68 tests" → "105" in tech-stack table, CI section, and project-layout tree; Phase 7 BYOK added to Status & roadmap).
- `/health` phase marker updated from frozen `"phase": 2` → now reports 7. Assertion in `test_health_returns_ok` updated together.
- `report.md` archived (historical snapshot banner added pointing to `progress.md`).
- `__init__.py` package docstring no longer claims "Phase 1".
- `todos.md` synced: resolved checkboxes marked, known-issues table updated.

**Post-round state:** **105 tests passing** (was 100), ruff clean. Known remaining limitations (deliberate, documented): O(n) semantic scan with warn-only guardrail past `MAX_SEMANTIC_SCAN_ENTRIES`; single-process coalescing; no upstream circuit-breaker (retries cover demo scale).

---

## 2026-08-23 — Phase 7: BYOK production push (7.1–7.7 code complete)

**Goal:** 10–15 known users bring free-tier keys (OpenRouter / Gemini) through one proxy with zero cost risk and zero cross-user cache leakage.

**What shipped:**
- **7.1 Provider allowlist:** `PROVIDER_BASE_URLS = {openrouter, gemini}`; `X-LLM-Base-URL` header or `provider` body field (excluded from upstream payload); exact-match + normalization; non-allowlisted → 400 before any network call. 6 tests incl. precedence + rejection-before-forward.
- **7.2 BYOK forwarding:** `Authorization: Bearer` parsed; `MOCK_LLM=false` + keyless → 401 OpenAI-shaped (server key NEVER substituted); `forward_to_llm(api_key=, base_url=)` with `ValueError` defense-in-depth. 6 tests.
- **7.3 Identity + scoping:** `security.py: derive_user_id = HMAC-SHA256(USER_ID_PEPPER, key)[:24]`; `LOCAL_USER_ID='local'` for keyless mock traffic; startup warning when pepper unset. **Schema V2 migration:** `cache_entries` rebuilt with `user_id NOT NULL DEFAULT 'local'` and inline `UNIQUE(prompt_hash)` replaced by composite `UNIQUE(prompt_hash, user_id)` — closes the cross-user INSERT collision the plan missed; `request_log` ALTER+backfill; legacy rows land under `'local'`. Both lookup tiers + store + log_request scoped. Raw key never stored/logged. Tests: determinism, cross-user exact/semantic isolation, legacy rebuild preservation+idempotency, fresh-install schema, e2e multi-user via ASGI.
- **7.4 Metrics:** `total_tokens_saved` (HIT rows only) headline on `/metrics` + dashboard card; per-user breakdown table; `_estimate_cost` now model-aware from `DEFAULT_MODEL_PRICING` + `MODEL_PRICING` env override (prefix match), unknown models = `.00`. Tests: hit-only sums, per-user==global reconciliation, zero-cost unknowns, prefix inheritance, env override.
- **7.5 Persistence:** `render.yaml` ships commented persistent-disk block (`/var/data` + `CACHE_DB_PATH`) — enablement needs paid tier, documented in blueprint comments + `TECHNICAL_DETAIL`.
- **7.6 Retention:** `daily_metrics` permanent rollup table; `prune_old_logs(30d)` transactional roll-up-and-delete, idempotent, wired lazily into lifespan; `get_metrics` unions rollup+raw so lifetime totals survive pruning. Tests: rollup correctness, totals-survive boundary, idempotency.
- **7.7 Verification:** automated multi-user/provider ASGI tests green. Manual real-provider runbook added to README BYOK section.

**Post-phase state:** **100 tests passing** (was 68), ruff clean. Remaining human steps: generate `USER_ID_PEPPER` + `ADMIN_TOKEN` in the deployment env, attach Render disk (optional, paid), run README pre-launch runbook with two real keys, then open access.

---

## 2026-08-23 — CI pipeline round (GitHub Actions)

**What was worked on:** new `.github/workflows/ci.yml` (4 jobs), `.github/dependabot.yml`, `scripts/smoke_test.py` (22-check black-box HTTP suite, verified 22/22 against a live local uvicorn server before committing to CI), `pytest-cov` added to dev deps.

**Jobs:**
| Job | Covers |
|---|---|
| `lint` | `ruff` across `src/tests/scripts` |
| `test` | pytest matrix: py3.10/3.11/3.12 Ubuntu + py3.11 Windows (dev parity); CPU-only torch pre-install (Dockerfile-matched pin, avoids multi-GB CUDA wheels); HF model cache keyed per-OS; `coverage.xml` + junit artifacts (py311-linux leg); black-box smoke vs live uvicorn on Ubuntu legs |
| `docker-smoke` | buildx build with `type=gha mode=max` layer cache; in-container torch CPU-only assertion; same smoke suite driven from a second container of the same image (`--network host` + ro-mounted `scripts/`); image-size report; logs-on-failure + always-cleanup |
| `security-audit` | `pip-audit -r requirements.txt`, job-level `continue-on-error` (informational) |

**Decisions:**
- Action versions from 2026-current docs/examples: `checkout@v7`, `setup-python@v6`, `cache@v5`, `setup-buildx-action@v4`, `build-push-action@v7`, `upload-artifact@v4`. Dependabot keeps them fresh; SHA-pinning is the documented next hardening step once SHAs can be captured.
- `MOCK_LLM=true` at workflow env level = CI can never spend money (mirrors README guarantee).
- Smoke suite asserts exact metrics accounting (+5 requests), OpenAI response contract keys, similarity floors kept loose (0.80) so upstream BGE weight updates don't flake CI (unit suite gates 0.85).
- `ruff format --check` deliberately NOT gated: 16 files would need reformatting (tracked as follow-up).

**Follow-up tweak (same day, user-approved):** Dependabot pip version-bump PRs disabled via `open-pull-requests-limit: 0` (floors are cosmetic under `>=` pinning); vulnerability coverage strengthened instead — security-audit job now publishes pip-audit SARIF to code scanning (Security tab alerts persist until resolved), and Dependabot's separate CVE-driven security-update PRs remain active. `github-actions` ecosystem stays on weekly grouped updates.

**Docs consistency pass (same day):** README API reference now documents ADMIN_TOKEN gating on purge/sweep/dashboard; stale "~2.2 GB" / "45 tests" / "51 tests" claims corrected across `report.md`, `todos.md`, `guide.md`.

---

## 2026-08-23 — Code-review fix round (11 issues, all resolved)

**What was worked on:** deep code review of main (P0 correctness / P1 architecture / P2 reproducibility). Every fix shipped with its proving test; full suite green after each item.

| # | Sev | Fix | Status |
|---|---|---|---|
| 1 | P0 | Model name folded into cache identity: `canonical_prompt()` prefixes `[model]`; `lookup()` / `_exact_lookup()` / `_semantic_lookup()` accept model filter | Done + tests |
| 2 | P0 | HIT latency measured with `perf_counter` (was hardcoded 0.0) | Done + test |
| 3 | P0 | Single-process request coalescing per prompt hash (asyncio.Lock registry, bounded); documented multi-worker limitation | Done + concurrency test |
| 4 | P0 | Upstream httpx errors → OpenAI-shaped JSON error (status passthrough / 502), outcome=`ERROR` logged, CHECK constraint widened; no fabricated cost/tokens | Done + tests |
| 5 | P1 | Optional ADMIN_TOKEN bearer auth on purge/sweep/dashboard; startup warning when unset | Done + tests |
| 6 | P1 | MAX_SEMANTIC_SCAN_ENTRIES guardrail warns once; O(n) scan documented as accepted limitation | Done + caplog test |
| 7 | P1 | Shared lifespan-managed `httpx.AsyncClient` on `app.state`; `forward_to_llm(client=...)` reuse with one-off fallback; SQLite pooling deliberately skipped | Done + reuse test |
| 8 | P1 | `get_settings()` lru_cache factory (frozen Settings); point-of-use reads; fixtures simplified via `cache_clear()`; import-time freeze bug gone | Done + freshness test |
| 9 | P2 | `requirements-dev.txt` (pytest/pytest-asyncio/ruff), Makefile + README install updated; ruff lint clean across src/tests/scripts | Done, verified locally |
| 10 | P2 | Dockerfile pins `torch==2.5.1+cpu` BEFORE requirements (pip can't resolve CUDA); image measured 2.11 GB (`docker images`, 2026-08-23); `torch.cuda.is_available()==False` verified in-container | Done, measured |
| 11 | P2 | Methodology caveat (pairwise vs scan-max F1 lower bound) in `THRESHOLD_ANALYSIS.md` + README | Docs-only |

**Post-round state:** 68 tests passing (was 51), ruff clean, README / `.env.example` / `TECHNICAL_DETAIL.md` updated.

**Note for next reader:** `init_db` is CREATE-IF-NOT-EXISTS, so databases created before the `outcome='ERROR'` constraint change keep the old 3-outcome CHECK until recreated (fresh deploys unaffected).

---

## 2026-08-21 — Phase 3 threshold validation complete

**What shipped:**
- 31 labeled test pairs in `seed_test_pairs()` (16 should-match / 15 should-not) incl. edge cases: very short prompts ("Hi" vs "Goodbye"), a typo pair ("captial"), and code snippets (add-vs-multiply hard negative at similarity 0.845). Every label empirically checked against real BGE similarities before being committed to seed data.
- `src/proxy/eval.py` — sweep implementation that batch-embeds every unique prompt once and classifies each threshold from precomputed similarities (identical results to naive per-threshold embedding at ~7× less compute).
- `POST /eval/threshold-sweep` registered in `main.py` (app bumped to v0.3.0).
- Sweep executed across `[0.80, 0.82, 0.85, 0.88, 0.90, 0.93, 0.95]` — **F1 peaks at the existing default 0.85 (F1=0.857)**. Full curve + borderline-pair analysis + determinism notes in `docs/THRESHOLD_ANALYSIS.md`. Dataset exported to `data/labeled_test_pairs.json`.

**Why this matters for interviews:** the master guide's "single most interview-worthy artifact" now exists with measured data — the threshold choice isn't hand-waved, it's the F1 peak on a 31-pair set with borderline pairs called out by name.

---

## 2026-08-21 — Phase 5 dashboard shipped (FastAPI + Chart.js, single service)

**Decision:** FastAPI + Chart.js over Streamlit — zero new Python deps, one deployable unit, same-port integration.

**What shipped:** `GET /dashboard` serves the dashboard, with metrics cards + charts, cache browser with purge actions, threshold-sweep runner, live request log. Backed by two new endpoints (`GET /cache/entries?q=`, `GET /logs/recent?limit=`). App bumped to v0.4.0.

---

## 2026-08-21 — Phase 6 deploy artifacts complete + Docker-verified locally

**What shipped:** `Dockerfile` (CPU torch pinned `torch==2.5.1+cpu`, baked model, measured **2.11 GB** on 2026-08-23 vs ~4+ with CUDA) + `.dockerignore` + `render.yaml` + `Procfile`. Docker build/run verified locally: ~14 s to healthy, MISS→HIT + dashboard OK in-container. Live cloud deploy awaits user account; defaults to `MOCK_LLM=true` for zero-spend demos.

---

## 2026-08-21 — Phase 4 invalidation + bypass + refactor

- TTL expiry: every stored entry gets `expires_at = now + CACHE_TTL_SECONDS`. Both lookup paths check this; `_exact_lookup()` deletes expired entries on access, `_semantic_lookup()` filters in SQL WHERE clause.
- Manual purge: `POST /cache/purge` with optional `entry_id`. The `_detach_log_references()` helper safely nullifies FK references in `request_log` before deletion.
- Bypass header: `X-Cache-Bypass: true` skips cache, forwards directly, logs as `"BYPASS"`.
- **Refactor same session:** `_detach_log_references()` was duplicated between `_delete_entry()` and `purge()` — pulled into a standalone function used by both, eliminating the duplication and ensuring consistent FK handling.

---

## 2026-08-21 — Phase 2 semantic matching + Phase 1 exact-match cache (committed)

- Phase 1: `0157904` — "phase 1: proxy skeleton + exact match cache (passing e2e tests)".
- Phase 2: BGE-small on CPU, `_semantic_lookup()`, two-tier lookup, binary BLOB embeddings, model warmup on startup.
- Phase 2 was implemented in working tree but not committed per user instruction at the time.

---

## Earlier sessions (summary)

- **Repo restructure (Phase 0):** root `.gitignore`, `pyproject.toml [project]`, `src/` layout, `Makefile`, docs renamed. Staged but uncommitted per user instruction at the time.
- **Earlier:** PRD, master guide, threshold analysis, technical detail, progress, todos, report drafted from the project spec.
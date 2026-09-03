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

## 2026-09-04 — Lightsail migration + custom domain + dashboard rounds + docs refresh

**Infra (all live-verified, no assumptions):** Lightsail Small 2GB ($12/mo, `us-east-1a`, static IP `98.95.205.92`) + ECR repo + least-privilege IAM (`semcache-ci` push, `semcache-ecr-pull` host read-only). CD: push to `main` -> CI green -> ECR (`:latest` + `:sha`) -> host pulls + health-gated restart (`deploy.yml`, secrets in repo settings). Persistence proven: MISS -> HIT -> container restart -> HIT -> full host reboot -> HIT -> compose-down + image wipe + ECR re-pull -> HIT. Namecheap A record `semcache` -> static IP; Caddy single-domain block (no :80 catch-all, keeps the ACME challenge path uncontested) -> Let's Encrypt production cert observed in logs; `https://semcache.noblechicken.me/health` = ok.

**Dashboard (all screenshot-verified live):** Phase 8 rewrite -> Watermelon-lane re-skin (real OKLCH tokens from their public index.css) -> statement/ledger -> bento Overview (coral hero + tiles, peach gauge, alert strip) + hand-drawn SVG trend/sweep (Chart.js deleted) -> icon rail + sharp boxes + ambient orbs + gauge composition + dial zoom. Two genuine bugs caught by looking: `.hidden` does not reflect on SVGElement (theme icons) and an SVG-legend/y-tick collision. Fast loop documented: `scripts/fast-loop-dashboard.sh` (file into running container, seconds) decoupled from the 20-min pipeline; `scripts/check_dashboard.py` (contracts + WCAG contrast floor), `scripts/verify-dashboard.sh` (live markers), `scripts/Test-SemCache.ps1` (15-check adversarial battery).

**Docs:** README test counts 135 -> 142, Lightsail production section, dashboard section rewritten, live-monitor retargeted Lightsail (read-only: prod is MOCK mode so the 401 probe does not apply; Render URL now 503s). plan.md live/persistence sections updated; this entry added.

**Still open:** Lightsail snapshot + $15 billing alarm; sibling-project integration stretch.

---

## 2026-09-04 — Battery methodology war + template-sensitivity measurement

Three test-harness artifacts found and killed, each masquerading as product behavior: (1) shared run-id/family suffixes across prompts inflate cross-similarities (v1 T3, burst groups); (2) PowerShell 5.1 silently empties a bare $var directly before "?" (`"of $c?"` sent truncated text - proven by isolated repro, fixed with concat/${}); (3) reruns without clean-room purges compare against last run's templates. Standing rule now: prompts carry zero artificial tokens; freshness comes from per-group purges (-AdminToken); exact repeats reuse identical variables.

Also resolved: identical 6dp sims across runs are deterministic recomputation (same texts in, same sims out - verified), not staleness; short-phrase "collapse" and the emoji-attractor sightings were suffix artifacts (clean retests MISS correctly); the degenerate-candidate skip stays as cheap insurance with corrected provenance. Genuine residue, measured clean: same-template/different-topic cross-hits 4/20 (sourdough/composting/tidal/fermentation at 0.851-0.879), order-swap 0.985, antonym 0.944 - reported, not gated. Local-vs-host model drift noted (same texts differ ~0.04 between fresh Hub weights and baked image weights - re-bake image on next base refresh).

---

## 2026-09-04 — Phase 9 semantic-trust fixes (shipped, live-verified)

Owner's deep battery (`scripts/Test-SemCache-Deep.ps1`) found two defects: (1) eval/prod skew — threshold tuned on raw strings, production embedded `"[model]…\n[user]…"` text (live R=1.0/P~0.45 vs doc 0.9375/0.7895); (2) single-entity swaps HIT (Finland 0.87, Norway 0.90, Japan 0.87, population 0.91).

**Fix A (root cause):** `ChatCompletionRequest.embedding_text()` (message-only) feeds lookup/store/log; `canonical_prompt()` untouched for hashing. Cross-model isolation preserved via `model_used`. Exposed two real latent bugs on the way in: `store()` replace-scope and the two-column unique index both assumed model-in-hash — fixed (model-scoped replace with FK detach; triple-key index + idempotent migration). New tests caught a third live: `[user]` prefix shifted token indices so sentence-initial words misread as entities (fixed via tag stripping in `text.py`).

**Fix B (two-signal veto):** `entity_veto()` in `cache.py` — (1) disjoint capitalized sets + template gate Jaccard >= 0.2, (2) disjoint fact-type keywords ungated. Calibration (`scripts/calibrate_trust.py`, same helpers as shipped code) proves zero recall risk; the gate specifically saves the WWII/World-War-II paraphrase. Lexical rules live in dependency-free `src/proxy/text.py`, shared with both analysis scripts so evidence and code cannot drift.

**Fix C (lexical floor) deliberately NOT built:** Jaccard table proves no global floor exists (3 labeled positives at 0.000); post-Fix-A sims clear both collision probes on threshold alone (username 0.807, thanks 0.841).

**Measured after (clean-room live battery):** recall 15/16 = doc exactly; precision 0.82 beats doc 0.79; spotlight all-MISS; session 10/11 (thanks/greeting 0.851 = 0.001 over bar, named residue). 162/162 pytest green; sweep byte-identical. Prod migrated via one clean purge (metrics preserved by FK-detach). Forensics footnote: null matched_entry_ids mid-run traced to a dashboard Purge ALL click during the battery — led to a follow-up item to audit-trail purges.

---

## 2026-09-03 (later still) — senior code review executed: dead weight removed

Context: full maintainability review (all 8 requested categories) verified against usage-counted greps; Phase 1 (zero-risk) + Phase 2 (owner decisions) executed. Nothing behavioral changed — 142 tests must stay green.

**Removed as dead:**
- `LLM_MODEL` / `Settings.llm_model` — set in config but zero readers; docs claimed an effect that didn't exist (model identity always comes from the request).
- `_circuit_open_response(exc)` param — inert since the CodeQL static-message fix.
- `docs/assets/` — 3 orphaned dashboard PNGs, zero references since the README redesign.
- `scripts/check_pairs.py` — Phase-3 authoring tool, superseded by `run_sweep.py` + the drift guard.
- `docs/report.md` — archived snapshot; `plan.md`'s stale "pair with report.md" pointer fixed.

**De-risked:**
- `pyproject.toml` slimmed to `[tool.pytest.ini_options]` only. The `[project]` table (v0.2.0, deps missing tiktoken) was never used for installation and drifted — a "which deps are true?" trap.

**Consolidated / labeled:**
- `guide.md` → `docs/guide.md` with a currency banner, test counts 68 → 142, and Part 11 rewritten (the old audit was frozen pre-commit: "uncommitted phases", "only 2 commits exist" — all obsolete; its quirk list items 1/2/4/5/8 were all fixed in later rounds, now documented as such).
- MASTER_GUIDE / PRD / TECHNICAL_DETAIL carry "historical planning document" banners.
- `plan.md`: phase table updated through launch, recovery-era sections 4–5 replaced with current state, decision points closed out.

**Privacy:** personal tooling references (`skills.md`, `docs/skills2use.md`, `.serena/`) removed from the public repo and preserved in the owner's `sc-personal-notes` folder under the user profile. Notable find: `docs/skills2use.md` had already vanished from the working tree and been silently staged by a blind `git add -A` in `a35c20e` — recovered from git history. Lesson recorded: audit `git add -A` output before committing.

**Deliberately kept:** `Procfile` (zero-maintenance portability artifact, documented); `Makefile`; `pair_similarities()` wrapper (stable API); the coalescing double-lookup and metrics rollup union (load-bearing, verified).

---

## 2026-09-03 (later) — dashboard browser access shipped + live-monitor CI

- **`?token=` admin fallback shipped** (owner-approved): `require_admin_token` accepts `?token=<ADMIN_TOKEN>` when no header is present (header wins; constant-time compare via `hmac.compare_digest`); `index.html`'s `api()` helper now reads the token from the URL and attaches `Authorization: Bearer` to every purge/sweep fetch. Browsers can finally open `/dashboard?token=<ADMIN_TOKEN>`. 3 new tests → **142 total**.
- **New `live-monitor.yml` workflow** (hourly + manual dispatch): probes the deployed Render service and asserts this project's real contracts — `/health` phase 7, `/` service card, `/metrics` key shape, keyless POST → 401 with `invalid_request_error` (assertion verified against the live service before committing, not guessed). Complements the pre-ship pipeline (lint/matrix/docker-smoke/pip-audit/CodeQL) with post-ship monitoring; hourly pings keep instance-hours far under Render's 750/month free quota.
- `.gitignore`: added `.ruff_cache/` and local `smoke*.db` artifacts.

---

## 2026-09-03 — Phase E decided: free tier + manual re-warm

- Owner picked **Option C** (keep Render free, accept ephemeral state) over the Starter disk ($7.25/mo) and a Postgres swap. Documented in `LAUNCH_CHECKLIST.md` Phase E. Consequence: cache entries and metrics counters reset on every deploy *and* 15-min idle spin-down; re-warming is manual (demo calls), and counter history is not recoverable by re-caching.
- Local helper `C:\Users\arpan.ARPAN\sc-demo.ps1` (machine-local, not in repo) gives any PowerShell window a ready `Ask` against the live URL — the recurring "Ask is not recognized" friction is closed.

---

## 2026-09-02 (later) — post-launch checklist round

Context: working through the owner's remaining checklist. Shipped one small feature, deleted the remnant, and surfaced one auth-usability gap that needs an owner decision before touching live auth.

**Shipped**
- `GET /` service card (no more bare 404 at the bare URL): name, version, endpoint map. 1 new test → **139 total**, CI green on all nine checks, live-verified on Render after auto-redeploy.
- OneDrive remnant folder **deleted** after a two-part safety scan: (1) file-list diff vs `origin/main` showed only tool caches as extras; (2) the copy's git tip `0f8f7d7` proven an ancestor of remote `main` — zero unique history or data lost (OneDrive recycle bin holds it ~30 days regardless).
- Docs: OpenRouter's free quota documented as **per-account across all keys** (resets UTC midnight); launch + this round recorded.

**Found, not yet fixed (needs owner approval — it's an auth change on a live service)**
- **`/dashboard` is unreachable from a browser while `ADMIN_TOKEN` is set**: the gate reads only the `Authorization` header (browsers can't send it on a link) and the page has no token prompt, so it 401s before rendering. Live data is still viewable token-free via `/metrics`, `/cache/entries`, `/logs/recent`. Proposed fix (P2 in `todos.md`): `?token=` fallback in `require_admin_token` + token-aware dashboard JS.

**Operational notes**
- Every `git push` redeploys the Render service and **wipes the ephemeral free-tier disk** — the verification entries from the launch hour were reset by subsequent pushes. Expected; the Phase-E paid disk is the fix if history persistence matters.
- Dependabot PR closure and Security-tab alert review need GitHub authentication (`gh auth login`) or manual clicks — instructions handed to the owner.

---

## 2026-09-02 — 🚀 LAUNCHED: deployed to Render, BYOK verified on the public URL

Context: owner executed the deploy; agent diagnosed a live 500 and shipped the schema fix that it exposed. The project is now a running public service.

**Deployment**
- Render Blueprint applied → live at `https://semantic-cache-proxy.onrender.com` (free tier, docker runtime, `MOCK_LLM=false`, `USER_ID_PEPPER` + `ADMIN_TOKEN` set as env secrets, health check `/health`).
- Public verification (all passed): keyless → **401**; Gemini MISS → **HIT** (sim 1.0, `gemini-3.6-flash`); OpenRouter MISS → **HIT** (`minimax/minimax-m3:free` — quota bucket had reset at UTC midnight); non-allowlisted `X-LLM-Base-URL` → **400**; `/dashboard` → 401 without ADMIN_TOKEN, per-user accumulation visible with it.
- Note: `/` intentionally 404s (API has no root page); `/health`, `/metrics`, `/dashboard`, `/v1/chat/completions` are the surfaces.

**Bug found live + root cause + fix (shipped as `fdc91e8`/`c38f795`):**
- **Raw HTTP 500 on any failed upstream call against a pre-Aug-23 database.** The Phase-7 migration rebuilt `cache_entries` for user scoping but left legacy `request_log` tables carrying `CHECK(outcome IN ('HIT','MISS','BYPASS'))` — no `'ERROR'`. Every failed upstream call (429/401/5xx) therefore crashed writing its ERROR row: `IntegrityError` → unhandled → bare 500 with zero diagnosis. Fix: `_migrate_user_scoping` now detects the stale CHECK via `sqlite_master` and rebuilds the table in place, preserving all rows (3 new tests: rebuild+preserve, the partial-migration DB shape that shipped the bug, idempotency). **Proven against a copy of the owner's actual stale `cache.db`: same request that 500'd returned a clean OpenAI-shaped 429, DB migrated on boot, ERROR row logged, history intact.** 135 → 138 tests.

**Operational facts learned (documented in LAUNCH_CHECKLIST reality sheet):**
- OpenRouter's free-model 50/day limit is **per-account, shared across all keys** — a freshly created key does not reset the bucket; it resets at UTC midnight.
- The owner's machine runs an unrelated Docker stack (`trakplus-*`) publishing port 8000 alongside the local proxy's `127.0.0.1:8000` binding — verified benign (Windows specific-binding precedence; all probes reached the proxy), but worth knowing.
- Local API keys are standardized as persistent user env vars `SC_OPENROUTER_KEY` / `SC_GEMINI_KEY` / `SC_FREE_MODEL` (dedicated `semantic-cache-proxy`-named keys, distinct from the owner's personal keys).

**Remaining owner items:** close the 5 superseded Dependabot PRs; delete the OneDrive remnant folder; optional Render Starter + disk for persistence; optional `/` → `/dashboard` redirect.

---

## 2026-09-01 (session 6) — tiktoken, dev-floor sync, final acceptance — code-side roadmap complete

Context: closing out every remaining locally-implementable item. After this session, **everything left on the board requires the owner** (cloud deploy with real keys, destructive cleanup decisions) or a scope decision the todos explicitly park (ANN swap, streaming cache, distributed coalescing, per-tenant rate limiting). Test count 132 → **135**; black-box smoke 22/22.

**What shipped**

1. **tiktoken token counting (P2, the last deferred P2 that was code-side):** `_rough_token_count` (len//4) → `_estimate_tokens`: lazy-loaded `cl100k_base`, graceful fallback to the heuristic if the BPE tables can't load (air-gapped hosts) — a degraded estimate beats a hard failure on the metrics path. BPE tables prewarmed into the Docker image (`TIKTOKEN_CACHE_DIR=/app/.cache/tiktoken` + a bake-step call), so production cold starts never pay the download. `tiktoken>=0.8,<1` added to requirements. No test changes needed — verified first that no test pins heuristic token values (the exact-count metrics tests inject tokens via `log_request` directly). 3 new tests: tiktoken parity, forced-fallback branch, min-1 guarantee.
2. **Dev-dependency floors = tested versions:** discovered that local + CI have been running pytest 9.1.1 / pytest-asyncio 1.4.0 / pytest-cov 7.1.0 / ruff 0.16.4 all along (the `>=` floors install latest), with the 135-test suite green — so the four open Dependabot pip PRs were already empirically validated. Bumped `requirements-dev.txt` floors to those versions (supersedes the PRs' intent) and applied the actions-group PR's diff (`setup-python@v6→v7` ×3 in ci.yml). The 5 open PRs can be closed by the owner (or Dependabot auto-supersedes them); nothing to merge.
3. **Schema-from-JSON P2 resolved by decision, not code:** the drift guard from session 5 delivers the item's goal (fresh DB always matches the published set) without JSON-seeding's init-order/migration risk; `seed_test_pairs()` stays the source of truth, JSON stays the exported artifact. Documented in `todos.md`.
4. **Final acceptance:** full suite **135 passed**; ruff lint + format clean; **black-box smoke suite 22/22** against a live uvicorn (OpenAI contract, MISS→HIT, paraphrase hit, cross-model isolation, bypass, exact metrics accounting, logs, purge) — the same suite CI runs against the containerized server.

**Docs kept fresh:** README What's-new line reflects auto-tune/breaker/tiktoken; `design.md` §4.11 rewritten (tiktoken now supplies counts; honest-$0.00 pricing decision unchanged) + module table updated; LAUNCH_CHECKLIST status banner refreshed; test counts synced to 135.

**Remaining (all owner-side — nothing code-side left):**
- Apply the Render Blueprint + run the BYOK runbook with two real provider keys (needs your accounts/keys).
- Close/ignore the 5 open Dependabot PRs (superseded by the floor bumps; merging them is now a no-op).
- OneDrive remnant folder cleanup (destructive — your call; remote + local are in lockstep).
- LICENSE name confirmation (currently "Arpan Goyal" from git config).
- The parked P3 stretch items (ANN swap, streaming cache, distributed coalescing, per-tenant rate limiting, sibling integration) remain documented as deliberately deferred — they need production scale or another project to exist.

---

## 2026-09-01 (session 5) — Circuit breaker shipped, LICENSE added, drift guard

Context: continued from session 4 with CI verified green on all three prior pushes (checked via the GitHub API — the new `ruff format --check` gate passed). Scope: the circuit breaker stretch item, the LICENSE P1, and the dataset drift-guard P2. Test count 123 → **132**.

**What shipped**

1. **Circuit breaker (P3 stretch — the last big locally-implementable stretch item):**
   - `llm_client.py`: hand-rolled `CircuitBreaker` (zero new dependencies — the todos' "30-line" sketch). Per **upstream base URL** (registry dict): a failure storm on one provider never blocks another. States: CLOSED (normal) → OPEN after `LLM_BREAKER_FAILURE_THRESHOLD` (default 5) **consecutive** exhausted failures → after `LLM_BREAKER_RESET_SECONDS` (default 30 s) exactly one single-flight HALF_OPEN probe is admitted (success closes; failure restarts the cooldown). `threshold=0` disables.
   - Failure definition: only retryable-class outcomes (transport errors, 408/429, 5xx) count — a 401 storm is the caller's fault and must not open the circuit for everyone.
   - New settings `LLM_BREAKER_FAILURE_THRESHOLD` / `LLM_BREAKER_RESET_SECONDS` (+ `.env.example`, README config table). `reset_circuit_breakers()` helper for settings changes/tests.
   - `chat.py`: `CircuitOpenError` → OpenAI-shaped **503** `upstream_circuit_open` at both forward call sites (BYPASS + MISS paths), logged as `ERROR` with zeroed cost/tokens, never cached. 503 (not 502) because the proxy is deliberately shedding load.
   - 9 new tests: 5 unit (state machine incl. a fake-clock probe/reopen test), 2 through `forward_to_llm` (opens + fails fast with zero extra network calls; 401-storm doesn't count), 1 API-level (503 shape + error log + no cache entry). `TestUpstreamRetries._real_mode` now calls `reset_circuit_breakers()` so failures can't leak between tests (module-level registry).
2. **`LICENSE` (P1):** MIT, `Copyright (c) 2026 Arpan Goyal` — name taken from the git author identity; flagged to the owner as a one-line change if a different legal name is wanted.
3. **Drift guard (P2):** `test_seed_matches_published_json_dataset` asserts `seed_test_pairs()` and `data/labeled_test_pairs.json` agree row-for-row (count, prompts, labels) — closes the "inline seed vs published artifact" divergence risk; re-run `scripts/export_test_pairs.py` if it ever fires.
4. **Docs de-staled:** `design.md` §4.10 rewritten (was "no circuit breaker") + limitations list updated (breaker now exists but is per-process; removed the two resolved OneDrive-missing-file items); README error-contract note now mentions the 503 circuit-open shape; test counts synced 123 → 132 across README + LAUNCH_CHECKLIST.

**Bugs found + root cause + fix (tests caught real semantics issues)**
- `CircuitBreaker.state` reports **HALF_OPEN** the moment the cooldown has elapsed (not only while a probe is in flight) — initial test expectations assumed "OPEN" until probed. The code was right (state is a function of clock position, admission is a function of the single-flight flag); tests corrected, with the fake-clock test now pinning the real semantics.
- `_stub` staticmethod aliasing across test classes needed `staticmethod(...)` re-wrapping (plain alias re-bound `self`); fixed in the test.

**Deferred (unchanged)**
- tiktoken token counting and schema-from-JSON export remain owner calls; remaining stretch items (ANN swap, distributed coalescing, streaming cache, per-tenant rate limiting, sibling-project integration) need infra or scope decisions.

---

## 2026-09-01 (session 4) — `/eval/auto-tune` shipped + P2 polish batch

Context: first real feature session after the P0 recovery. Scope chosen from `todos.md`: the auto-tune stretch item (highest-value remaining development) plus four P2 polish items that were zero-ambiguity. Everything verified before commit; full suite green after each change.

**What shipped**

1. **`POST /eval/auto-tune` (app bumped to v0.5.0)** — the stretch-item developer aid:
   - `eval.py`: new `pair_similarity_details()` (per-pair similarity **with the prompts**, unlike the label-only tuples `pair_similarities()` returns — which is now a thin wrapper over it, API unchanged); `run_auto_tune(thresholds=None)`; constants `DEFAULT_SWEEP_THRESHOLDS` (the documented 0.80–0.95 grid), `BORDERLINE_BAND = 0.03`, `MAX_BORDERLINE = 10`.
   - Semantics: F1 ties break toward the **lower** threshold (at equal F1, a cache prefers recall — a false hit serves a slightly-off answer, a false miss just pays for one more generation). `borderline` = labeled pairs within ±0.03 of the pick, nearest first, max 10 — the evidence behind the number.
   - `models.py`: `AutoTuneRequest` (grid optional), `BorderlinePair`, `AutoTuneResponse`. `main.py`: endpoint registered, admin-gated like the sweep; empty grid / empty dataset → `best_threshold: null` (mirrors sweep's `[] → []` contract).
   - 9 new tests (6 unit in `test_eval.py`, 3 API in `test_api.py`) → **123 total**. Live-verified against a real uvicorn: picks **0.85 @ F1 0.8571** on the seeded set, borderline led by the sci-fi pair at 0.8513. Documented in README API reference + `TECHNICAL_DETAIL.md` endpoint list.
2. **P2: ruff format applied repo-wide** (18 files reformatted, zero behavior change — full suite re-run green) and `ruff format --check` is now a gating step in the CI lint job (the long-standing "deliberately not gated" note is closed).
3. **P2: requirements upper bounds** — `sentence-transformers>=3.0.0,<6` and explicit `torch>=2.0,<3.0`. Range check before committing: includes Docker/CI pin `2.5.1+cpu`, local `2.13.0`, ST `5.7.0` (the exact versions the curve was re-validated on this morning), so nothing in use is excluded; only silent future major bumps are blocked.
4. **P2: README Troubleshooting section** for the `MAX_SEMANTIC_SCAN_ENTRIES` warning (what it means, warn-only, shrink-the-scan vs ANN-swap responses; cites `design.md` §5 — reference verified).
5. **P2: README "What's new"** pointer to `docs/progress.md`. Test counts synced 114 → 123 in README tech-stack/CI/layout tables and `LAUNCH_CHECKLIST.md`.

**What was decided**
- Tiktoken for `_rough_token_count` **deferred** (still P2): it changes cost-estimate behavior and adds a dependency + download for a number that only matters on paid-model traffic — not a 95%-sure change, so it waits for an owner call.
- Schema-from-JSON export (P2) also left for an owner call: touches init/migration ordering.

**Bugs found + root cause + fix**
- None new. Ruff lint passed clean both before and after the format pass (no latent issues surfaced).

**Open questions / unresolved**
- Remaining P1 items are owner actions: Render Blueprint + BYOK runbook with real keys, `LICENSE` name, OneDrive remnant cleanup.
- Remaining P2/P3: tiktoken, schema-from-JSON export, circuit breaker, ANN swap, distributed coalescing, streaming cache, per-tenant rate limiting (see `todos.md`).

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
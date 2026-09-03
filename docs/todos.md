# TODO — Semantic Caching Layer for LLM Cost Reduction

> **Last updated:** 2026-09-04 (dashboard redesign shaped — brief pending confirmation; see P2 entry)
> **Status legend:** 🔴 = known bug · 🟡 = planned enhancement · 🟢 = nice-to-have / stretch · ✅ = done
> **Priority:** 🔴 P0 (blocks demo) · 🟠 P1 (blocks live deploy / key stories) · 🟡 P2 (polish) · 🟢 P3 (whenever)

---

## 🔴 P0 — Known bug — ✅ RESOLVED 2026-09-01 session 3 (was: blocks demo)

The two lost directories are restored, the working tree is verified end-to-end (114/114 tests + live uvicorn smoke), and local `main` is committed and pushed in lockstep with remote (`f212ec2`). See `progress.md` 2026-09-01 session 3 for the verification evidence. Kept for context:

- [x] **🔴 P0** Restore the two lost directories (`src/proxy/routes/`, `src/proxy/static/index.html`). Done 2026-09-01 session 3: files restored and verified content-identical to remote `main` (CRLF noise only). The restore routes (file copy from the OneDrive remnant, or checkout from repaired `.git`) are recorded in `progress.md` session 2 if ever needed again.
- [x] **🔴 P0** Verify after restore (acceptance): 2026-09-01 session 3 — `python -m pytest tests/ -q` → **114 passed in 93.4 s**; uvicorn boot → `/health` = `{"status": "ok", "phase": 7}`, `/dashboard` = 200, exact MISS → paraphrase HIT (sim 0.985), `/metrics` accounting correct.
  ```powershell
  python -m pytest tests/ -q      # expect: 114 passed
  $env:MOCK_LLM = "true"; python -m uvicorn src.proxy.main:app --host 127.0.0.1 --port 8000
  # /health -> {"status": "ok", "phase": 7}; /dashboard -> 200 with the four tabs
  ```
- [x] **🔴 P0** Rebuild the local git repo state: the Desktop `.git` turned out to be already repaired (HEAD @ `0f8f7d7` = remote `main`; git log/fetch functional). The odd staged-deletions index was resolved with `git add -A` → staged delta vs `origin/main` was exactly the docs edits + restored files. Committed as `f212ec2` "restore routes and dashboard, sync docs after onedrive recovery" and pushed to `origin/main`.

> Why not just re-download the whole repo? The Desktop tree is otherwise identical to remote `main` (verified by full content diff — the 25 hash-flagged files are line-ending noise only), so a targeted checkout loses nothing and keeps the working tree's docs/ (newer than remote) intact.

## 🟠 P1 — Blocks live deploy / key interview story

- [x] **🟠 P1** Render Blueprint applied and BYOK runbook verified on the PUBLIC URL — ✅ **2026-09-02: LAUNCHED.** Live at `https://semantic-cache-proxy.onrender.com` (free tier, `MOCK_LLM=false`, `USER_ID_PEPPER` + `ADMIN_TOKEN` set). All runbook checks passed on the deployed service: keyless → 401; Gemini MISS → HIT (sim 1.0); OpenRouter MISS → HIT via `minimax/minimax-m3:free`; non-allowlisted `X-LLM-Base-URL` → 400; `/dashboard` gated by ADMIN_TOKEN. Local pre-deploy validation also passed (Gemini + OpenRouter pairs, after fixing two real bugs found during it — see `progress.md` 2026-09-02).
- [x] **🟠 P1** Re-measure threshold curve against current BGE weights on the HF Hub. ✅ Done 2026-09-01 session 3: `python scripts/run_sweep.py` reproduced the documented curve exactly — F1 still peaks at the default **0.85 (F1=0.8571)**, borderline pairs unchanged (antonym pair 0.8643, code pair 0.8449). No re-pin or re-justification needed.
- [x] **🟠 P1** Added the `LICENSE` file (MIT, "Copyright (c) 2026 Arpan Goyal") — 2026-09-01 session 5. The name was taken from the git author identity; if you want a different legal name or the GitHub handle, it's a one-line edit.
- [x] **🟠 P1** OneDrive remnant copy deleted — ✅ 2026-09-02. Pre-deletion safety scan: the copy's git tip (`0f8f7d7`) is an ancestor of remote `main` (no unique history) and its only untracked extras were tool caches (`.pytest_cache/`, `.serena/cache/`). OneDrive's recycle bin retains it ~30 days if ever needed.

## 🟡 P2 — Polish

- [x] **🟡 P2** **Dashboard browser access — SHIPPED (2026-09-03, owner-approved).** `require_admin_token` now accepts `?token=<ADMIN_TOKEN>` as a fallback (header wins, `hmac.compare_digest`); the dashboard JS reads the token from the URL and attaches it to every purge/sweep call. Open `/dashboard?token=<ADMIN_TOKEN>`. 4 new tests (fallback works on all gated endpoints, wrong token 401, header wins over query token).
- [x] **🟡 P2** Bare-URL 404 softened: `GET /` now serves a service card (name, version, endpoint map) — 2026-09-02, live-verified, CI green.

- [x] **🟡 P2** Ruff `I001` import-sort warnings in tests/: ✅ re-checked 2026-09-01 session 3 with local ruff 0.16.4 — `ruff check src/ tests/ scripts/` passes clean with zero findings (the previously flagged warnings no longer fire; no fix needed).
- [x] **🟡 P2** `ruff format` applied to the whole codebase (2026-09-01 session 4: 18 files reformatted, tests re-run green) and `ruff format --check src/ tests/ scripts/` is now gated in the CI lint job.
- [x] **🟡 P2** Renamed `test_seeded_dataset_has_32_pairs` → `test_seeded_dataset_has_31_pairs` (2026-09-01 session 3; `tests/test_eval.py` 8/8 passing after rename).
- [x] **🟡 P2** Token counting via tiktoken (2026-09-01 session 6): `_estimate_tokens` uses `cl100k_base` (lazy-loaded; graceful `len//4` fallback if the BPE tables can't load), BPE tables prewarmed into the Docker image via `TIKTOKEN_CACHE_DIR`. Improves the tokens-saved headline and `estimated_cost_usd` honesty on paid-model rows.
- [x] **🟡 P2** Seed-data ↔ JSON sync — **resolved by drift guard (2026-09-01 session 5/6), not by JSON-seeding.** Decision: `seed_test_pairs()` stays the source of truth (no init-order/migration risk), and `test_seed_matches_published_json_dataset` fails the suite if the exported `data/labeled_test_pairs.json` ever drifts (fix = re-run `scripts/export_test_pairs.py`). This delivers the item's actual goal (a fresh DB always matches the canonical published set) without touching migration ordering.
- [x] **🟡 P2** Documented the `MAX_SEMANTIC_SCAN_ENTRIES` warning in a README **Troubleshooting** section (2026-09-01 session 4): what it means, that it's warn-only, and the two responses (shrink the scan / plan the ANN swap).
- [x] **🟡 P2** Added a "What's new" pointer to `docs/progress.md` at the top of the README (2026-09-01 session 4).

- [x] **🟡 P2** **Dashboard redesign — SHIPPED & LIVE (`de834f5`, 2026-09-04).** Single-file (`src/proxy/static/index.html`) visual + insights overhaul, no backend changes. Tracked sub-todos:
  - [x] T1 Confirm brief (auto light/dark, anime.js v4 ESM, auto-tune evidence + trend/speedup panels)
  - [x] T2 Motion codex searched (no match — anime.js path per skill); anime.js 4.5.0 ESM bundle verified (`dist/bundles/anime.esm.min.js`); graceful-degradation design (content correct even if CDN fails)
  - [x] T3 Rewrite landed: theme tokens + toggle, headline strip with speedup, trend chart, auto-tune panel, skeletons/empty/error states, count-ups + row stagger, `prefers-reduced-motion`
  - [x] T4 Verified: JS syntax OK, contracts OK, 11/11 dashboard tests OK, ruff OK, CI green → auto-deployed
  - [x] T5 Live check: host on `de834f5`, `/health` ok, all new markers present in served page (`scripts/verify-dashboard.sh`)

- [x] **🟡 P2** **Dashboard re-skin (Watermelon lane) — SHIPPED & LIVE (`97ab0ac`, 2026-09-04).** Sidebar shell, Inter Variable, OKLCH lime primary, amber chart ramp, dark-first. Tracked sub-todos:
  - [x] R1 Ground tokens (real `index.css`, no invented style)
  - [x] R2 Confirm brief (sidebar, dark-first, committed hero)
  - [x] R3 Implement (all Phase-8 insights/motion/states kept)
  - [x] R4 Verified: syntax/contracts/11-11 tests/ruff/contrast OK, CI green → auto-deployed; live markers confirmed (`scripts/verify-dashboard.sh`)

- [ ] **🟡 P2** **Dashboard round 3 — owner verdict 2026-09-04: still reads AI-made, full rethink.** New rule: look before shipping. Tracked sub-todos:
  - [x] V1 Screenshot the live dashboard as-is (done 2026-09-04 — first visual verification; critique recorded in chat: identical stat boxes, Chart.js-default doughnut, "OBSERVE" kicker, cramped trend labels)
  - [ ] V2 Collect named anchors: screenshot 2–3 reference consoles the owner confirms (no more invented taste)
  - [x] V3 Re-brief from evidence (statement + ledger, lime kept) → shipped `60d5abc` + bar-thickness polish `ca1b972` → live screenshot-verified (statement hero, ledger rows, full-width trend, no kicker/grid/doughnut)

- [ ] **🟡 P2** **Dashboard round 4 (bento lane) — owner supplied reference 2026-09-04: neo-brutalist pastel bento (dark shell, saturated cards, condensed uppercase type, pill controls, textured viz).** Tracked sub-todos:
  - [x] W1 Analyze reference (palette/type/radius/texture/composition) + map to our 4 tabs and data
  - [ ] W2 Confirm plan (structure, fonts, palette handling) via questions
  - [x] W3 Implement (shipped `652942e`) → verified (syntax, contracts, 11/11 tests, ruff, 12/12 contrast pairs) → CI green → auto-deployed → live screenshot-verified (coral hero, gauge, alert strip, ledger, trend bars confirmed rendering, per-user table)

- [ ] **🟡 P2** **Dashboard round 5 (custom viz) — owner verdict: Chart.js defaults are the remaining AI tell; reference draws every graphic by hand.** Tracked sub-todos:
  - [x] X1 Root-cause analysis (default-library rendering; texture/ornament gap; hero nesting pattern)
  - [ ] X2 Confirm scope via question
  - [x] X3 Custom SVG trend bars (rounded, dotted texture, tooltip chip, bucket dots) + custom SVG sweep lines (dots, best marker, hover values) + hero stat tiles + gauge tick ring + drop Chart.js — SHIPPED `8922d79`
  - [x] X4 Verified (syntax, contracts, 11/11 tests, ruff, 12 contrast pairs) → CI green → auto-deployed → live screenshot-verified every tab (overview, cache, sweep, logs)
  - [x] X5 Post-pass fixes, proven via fast-loop (HTML sweep legend, sidebar live dot on deep-link, expired TTL track hidden) — live-verified, committed for durability; pipeline redeploy is a no-op

- [ ] **🟡 P2** **Dashboard round 6 (rail + boxy + ambient) — owner brief 2026-09-04.** Tracked sub-todos:
  - [x] Y1 Scope locked (icon rail, sharper radii, hover micro-interactions, visibility audit, lowkey ambient orbs; CSS-keyframe compositors only, anime layer untouched)
  - [x] Y2 Implement (rail shell, 10–16px radii, transitions, sweep-readout ink fix, ambient orbs + reduced-motion off) + review fixes (icon toggle, HTML legends, deep-link dot, expired TTL) — live-verified via fast-loop + screenshots
  - [x] Y3 Verify (syntax, contracts, 11/11 tests, ruff, contrast) → fast-loop → screenshot → commit/push → docs
  - [x] Y4 Sharp-corner follow-up (all radii → 0; dots/orbs stay round by design) — live-verified via fast-loop + screenshot
  - [x] Y5 Gauge card composition (flex column, centered dial, 0–50× scale row) — live-verified via fast-loop + screenshot
  - [x] Y6 Dial zoom (204px render, scale untouched) — live-verified via fast-loop + screenshot

- [ ] **🟠 P1** **Docs refresh + adversarial battery (2026-09-04).**
  - [x] Q1 README: 135 -> 142 (3 spots), Lightsail production block, dashboard rewrite, monitor row, layout/scripts + artifacts lines, Phase 5/6 lines
  - [x] Q2 live-monitor retargeted Lightsail (Render 503s; read-only checks — 401 probe inapplicable in MOCK mode)
  - [x] Q3 plan.md live/persistence sections + progress.md session entry
  - [x] Q4 `scripts/Test-SemCache.ps1` (15 checks: exact/paraphrase/boundary/model-isolation/bypass/auth/validation/metrics/logs/TTL) — syntax-checked, awaiting owner run
  - [ ] Q5 Commit + push (CI validates) → close

- [ ] **🟠 P1** **Custom domain `semcache.noblechicken.me` (Namecheap → Lightsail → Caddy auto-HTTPS).** Tracked sub-todos:
  - [x] D1 Namecheap A record `semcache` → `98.95.205.92` (owner done; verified propagating via recursive + direct lookup)
  - [x] D2a Host pre-staged: `DOMAIN=semcache.noblechicken.me`, stack healthy on IP (cert issuance retries in background)
  - [x] D2b Caddyfile surgery (`1619254`) PUSHED post-propagation → pipeline deploying
  - [x] D3 Verified: "certificate obtained successfully" (Let's Encrypt production) in Caddy logs → `https://semcache.noblechicken.me/health` = ok over public TLS → dashboard screenshot-verified on the new origin (no cert warnings)
- [x] **🟡 P2** `requirements.txt` now caps the embedding-sensitive packages (2026-09-01 session 4): `sentence-transformers>=3.0.0,<6` and explicit `torch>=2.0,<3.0`. Ranges include every version in use (Docker/CI pin 2.5.1+cpu, local 2.13.0, ST 5.7.0 — curve re-verified identical on ST 5.7.0) while blocking silent future major bumps from shifting the threshold curve.

## 🟢 P3 — Nice-to-haves / stretch

- [ ] **🟢 P3** **Stretch — integrate with a sibling project.** Wire this proxy in front of the RAG or Agent project; report before/after cost numbers over a fixed prompt set; add a chart to whichever sibling's docs.
- [x] **🟢 P3** **Auto-tune threshold — DONE (2026-09-01 session 4).** `POST /eval/auto-tune`: sweeps a configurable threshold grid (documented default when omitted), picks the F1-optimal value (ties → lower threshold, favoring recall), and returns the borderline labeled pairs (±0.03 of the pick, nearest first, max 10). Admin-gated like the sweep; 9 new tests (unit + API), live-verified picking 0.85 @ F1 0.8571 on the seeded set.
- [x] **🟢 P3** **Circuit breaker — DONE (2026-09-01 session 5).** Hand-rolled `CircuitBreaker` in `llm_client.py` (no new dependency): per-upstream CLOSED/OPEN/HALF_OPEN, opens after `LLM_BREAKER_FAILURE_THRESHOLD` (default 5) consecutive exhausted failures, fails fast with an OpenAI-shaped 503 (`upstream_circuit_open`) for `LLM_BREAKER_RESET_SECONDS` (default 30 s), then admits one single-flight probe. Only retryable-class failures count; `0` disables. 9 new tests → 132 total.
- [ ] **🟢 P3** **Stretch — ANN index swap.** When `len(cache_entries)` exceeds the warn threshold sustainably, replace `_semantic_lookup`'s numpy loop with FAISS / sqlite-vec / pgvector. The function signature doesn't change; only the body.
- [ ] **🟢 P3** **Stretch — distributed coalescing.** `asyncio.Lock` is per-process. Multi-worker / multi-instance deployments need Redis SETNX (or equivalent) on `prompt_hash`. Note in a comment at the lock site.
- [ ] **🟢 P3** **Stretch — streaming response caching.** v1 caches complete responses only; streaming introduces chunk-level identity and partial-write recovery problems that are real scope.
- [ ] **🟢 P3** **Stretch — per-tenant rate limiting.** Out of scope for v1's "10–15 hobbyists" framing; matters if the proxy opens up more widely.

---

## ✅ Recently resolved (kept for context — see `progress.md` for details)

- [x] **Code-quality review + cleanup executed (2026-09-03, owner-approved):** dead `LLM_MODEL` config removed (was documented but never read); dead `exc` param dropped from `_circuit_open_response`; `docs/assets/` (3 orphaned PNGs), `scripts/check_pairs.py` (superseded one-off), and `docs/report.md` (archived, superseded by `progress.md`) deleted; `pyproject.toml` slimmed to pytest config only (stale `[project]` metadata was a dependency-drift hazard); historical banners added to MASTER_GUIDE/PRD/TECHNICAL_DETAIL; `guide.md` moved to `docs/` and refreshed to the current era (142 tests, live URL, Part-11 audit rewritten); personal tooling refs (`skills.md`, `skills2use.md`, `.serena/`) removed from the public repo — owner's copies preserved in `sc-personal-notes` under the user profile. `plan.md` de-staled (recovery narrative → current state). 
- [x] **All 3 CodeQL alerts resolved (2026-09-02):** `security.py` user derivation switched HMAC-SHA256 → keyed BLAKE2b (one-time user_id rotation, service <1 day old, no real users affected); both "information exposure through an exception" sites in `chat.py` now send static client messages (detail stays in server logs). 139 tests green, CI 9/9.
- [x] **Session 6 (2026-09-01):** tiktoken token counting (`_estimate_tokens`, lazy + fallback, Docker-prewarmed BPE tables); dev-dep floors bumped to the actually-tested versions (pytest 9.1.1 / pytest-asyncio 1.4.0 / pytest-cov 7.1.0 / ruff 0.16.3) superseding the 4 open Dependabot pip PRs; `setup-python@v6→v7` superseding the actions-group PR; final acceptance = **22/22 black-box smoke checks** against a live server, 135 tests green.
- [x] **Session 5 (2026-09-01):** circuit breaker shipped (`llm_client.py`, per-upstream CLOSED/OPEN/HALF_OPEN, env-tunable, disable-able; OpenAI-shaped 503 contract); `LICENSE` (MIT); seed-data ↔ JSON drift-guard test; docs de-staled (`design.md` §4.10 + limitations list, README error note + config table); 132 tests green.
- [x] **Session 4 (2026-09-01):** `/eval/auto-tune` shipped (app v0.5.0, 9 new tests → 123 total, live-verified); `ruff format` applied repo-wide + format check gated in CI; README Troubleshooting + What's-new sections; requirements upper bounds for torch/sentence-transformers; test-count 114→123 synced across README/LAUNCH_CHECKLIST.
- [x] **P0 recovery complete (2026-09-01 session 3):** `routes/` + `static/index.html` restored (content-identical to remote), index repaired via `git add -A`, committed `f212ec2` and pushed — remote and local back in lockstep. Verified: 114/114 tests, uvicorn smoke (health/dashboard/MISS→HIT/metrics).
- [x] **Threshold sweep re-verified (2026-09-01 session 3):** F1 still peaks at 0.85 against current HF Hub BGE weights — curve byte-for-byte matches `THRESHOLD_ANALYSIS.md`.

- [x] Phase 0 — repo restructure (`src/` layout, root `.gitignore`, `pyproject.toml [project]`, `Makefile`, renamed docs).
- [x] Phase 1 — proxy skeleton + exact-match cache.
- [x] Phase 2 — semantic matching with BGE-small.
- [x] Phase 3 — threshold validation; 31 labeled pairs; default 0.85 justified by F1=0.857.
- [x] Phase 4 — TTL expiry + manual purge + bypass header.
- [x] Phase 5 — backend metrics; dashboard HTML shipped (now missing — see P0 above).
- [x] Phase 6 — deploy artifacts (Dockerfile, render.yaml, Procfile); Docker-verified locally.
- [x] Phase 6.5 — 11-issue code-review fix round (51 → 68 tests).
- [x] Phase 7 — BYOK production push (68 → 100 tests).
- [x] Phase 7.1 — embedding-deserialization hardening + docs sync (100 → 105 tests).
- [x] Phase 7.2 — upstream resilience (bounded retries, payload fidelity, error detail) (105 → 114 tests).
- [x] CI pipeline (lint, test matrix py3.10/3.11/3.12 + Windows py3.11, docker-smoke, non-blocking pip-audit SARIF).
- [x] `pip-audit` SARIF publishing to GitHub Security tab.
- [x] `report.md` archived; `progress.md` is the source of truth.
- [x] Admin auth on purge/sweep/dashboard; OpenAI-shaped upstream errors; model-aware cost estimation; provider allowlist; HMAC-derived user isolation; permanent `daily_metrics` rollup surviving 30-day retention prune.

---

## Known limitations (documented, accepted — see `design.md` §5 for the full list)

These are deliberate scope decisions, not oversights:

- Semantic scan is O(n) per request with a warn-only guardrail past `MAX_SEMANTIC_SCAN_ENTRIES` (default 5000).
- Coalescing is single-process (`asyncio.Lock`); multi-worker needs a distributed lock.
- No SQLite connection pool — WAL gives concurrent readers; pooling adds complexity without measured payoff.
- Pairwise F1 in `THRESHOLD_ANALYSIS.md` is a conservative lower bound for live scan-max behavior.
- BYOK identity depends on `USER_ID_PEPPER` (never rotate).
- Free-tier deploys lose cache/history on redeploy (paid disk fixes).
- Circuit breaker is per-process (like coalescing); multi-instance deployments get independent breakers per instance.
- No streaming response caching.
- Per-user metrics only cover the raw 30-day window; the rollup is global by design.
# TODO — Semantic Caching Layer for LLM Cost Reduction

> **Last updated:** 2026-09-01 (session 3 — P0 recovery shipped, repo re-synced with remote)
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

- [ ] **🟠 P1** Apply Render Blueprint (`render.yaml`) and run `docs/LAUNCH_CHECKLIST.md` Phases A–D end-to-end.
  - Generate `USER_ID_PEPPER` and `ADMIN_TOKEN` (32-byte hex each, never rotate); set as Render env secrets.
  - With two real keys (Alice via OpenRouter free model, Bob via Gemini flash), confirm: MISS → HIT for Alice; MISS → HIT for Bob (different `user_id`s in `/cache/entries`); keyless → 401; non-allowlisted `X-LLM-Base-URL` → 400; dashboard shows both users accumulating independently.
  - Acceptance: public URL `/health` returns 200; the BYOK runbook's 4 checks all pass.
- [x] **🟠 P1** Re-measure threshold curve against current BGE weights on the HF Hub. ✅ Done 2026-09-01 session 3: `python scripts/run_sweep.py` reproduced the documented curve exactly — F1 still peaks at the default **0.85 (F1=0.8571)**, borderline pairs unchanged (antonym pair 0.8643, code pair 0.8449). No re-pin or re-justification needed.
- [ ] **🟠 P1** Add the `LICENSE` file (MIT) — pending copyright-owner name decision from you.
- [ ] **🟠 P1** Decide the fate of the OneDrive remnant copy (`C:\Users\arpan.ARPAN\OneDrive\Desktop\projects\Semantic caching layer for LLM cost reduction`): it holds the only local git history (11 commits, now functional after HEAD/config repair) — keep until the Desktop repo has been re-synced from remote and verified, then archive/delete to avoid future confusion.

## 🟡 P2 — Polish

- [x] **🟡 P2** Ruff `I001` import-sort warnings in tests/: ✅ re-checked 2026-09-01 session 3 with local ruff 0.16.4 — `ruff check src/ tests/ scripts/` passes clean with zero findings (the previously flagged warnings no longer fire; no fix needed).
- [ ] **🟡 P2** `ruff format --check` is deliberately not gated in CI (16 files would need reformatting). Pick a session to run `ruff format src/ tests/ scripts/`, commit, and then enable the format check in `.github/workflows/ci.yml`.
- [x] **🟡 P2** Renamed `test_seeded_dataset_has_32_pairs` → `test_seeded_dataset_has_31_pairs` (2026-09-01 session 3; `tests/test_eval.py` 8/8 passing after rename).
- [ ] **🟡 P2** Replace `_rough_token_count` (`len(text)//4` heuristic) with `tiktoken` for accuracy on paid-model rows. Mock traffic doesn't need it; BYOK free-tier traffic doesn't need it; matters only when someone proxies a paid model and wants the `estimated_cost_usd` to be honest.
- [ ] **🟡 P2** Make the schema file/dir more discoverable: right now `data/labeled_test_pairs.json` exists but the `seed_test_pairs()` source-of-truth lives inline in `database.py`. Consider exporting a migration from the JSON on init so a fresh DB populated from JSON matches the canonical set.
- [ ] **🟡 P2** Document the `MAX_SEMANTIC_SCAN_ENTRIES` warning as part of the README troubleshooting section (currently only in `docs/TECHNICAL_DETAIL.md` Known limitations and in the warning itself). One paragraph: "if you see this log line, here's what it means and how to plan the ANN swap."
- [ ] **🟡 P2** Add a "what's new" section to the README pointing to `docs/progress.md` so visitors know there's a session-by-session history.
- [ ] **🟡 P2** `requirements.txt` / `requirements-dev.txt` should declare a maximum version for the embedding-sensitive packages (`sentence-transformers`, `torch`) so a `pip install --upgrade` doesn't silently change the threshold curve. Today only floors are declared.

## 🟢 P3 — Nice-to-haves / stretch

- [ ] **🟢 P3** **Stretch — integrate with a sibling project.** Wire this proxy in front of the RAG or Agent project; report before/after cost numbers over a fixed prompt set; add a chart to whichever sibling's docs.
- [ ] **🟢 P3** **Stretch — auto-tune threshold.** Add a `/eval/auto-tune` endpoint that sweeps a configurable threshold list, picks the F1-optimal value, and prints the borderline pairs that drove the choice. (Doesn't need to be production-safe; it's a developer aid.)
- [ ] **🟢 P3** **Stretch — circuit breaker** in `llm_client.py`. Bounded retries cover demo scale; a paid-tier deployment with sustained upstream failure would benefit. Implementation sketch: `circuitpybreaker` or a 30-line hand-rolled sliding-window failure counter with OPEN/HALF_OPEN/CLOSED states.
- [ ] **🟢 P3** **Stretch — ANN index swap.** When `len(cache_entries)` exceeds the warn threshold sustainably, replace `_semantic_lookup`'s numpy loop with FAISS / sqlite-vec / pgvector. The function signature doesn't change; only the body.
- [ ] **🟢 P3** **Stretch — distributed coalescing.** `asyncio.Lock` is per-process. Multi-worker / multi-instance deployments need Redis SETNX (or equivalent) on `prompt_hash`. Note in a comment at the lock site.
- [ ] **🟢 P3** **Stretch — streaming response caching.** v1 caches complete responses only; streaming introduces chunk-level identity and partial-write recovery problems that are real scope.
- [ ] **🟢 P3** **Stretch — per-tenant rate limiting.** Out of scope for v1's "10–15 hobbyists" framing; matters if the proxy opens up more widely.

---

## ✅ Recently resolved (kept for context — see `progress.md` for details)

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
- No circuit breaker; retries cover demo scale.
- No streaming response caching.
- Per-user metrics only cover the raw 30-day window; the rollup is global by design.
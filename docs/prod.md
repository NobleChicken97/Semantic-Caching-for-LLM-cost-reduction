# Product & Requirements — Semantic Caching Layer for LLM Cost Reduction

> The original product brief lives in `docs/PRD.md`; this file restates it in the standard docs/spec format and is the "why" companion to `design.md`.

## 1. Vision

Reduce redundant LLM API spend and latency by transparently recognizing prompts that mean the same thing and serving the cached answer, with measured metrics that prove how much was saved — drop-in compatible with any OpenAI-shaped upstream so existing clients change only their base URL.

## 2. Target users

- **Primary:** developers running LLM-backed apps where users frequently rephrase the same question (chatbots, RAG over a stable corpus, internal tools, customer-support helpers). Most of their bill is repeated answers to the same handful of intents.
- **Secondary (BYOK mode):** small teams of 10–15 hobbyists / early users who each bring their own free-tier OpenRouter or Gemini key to share one proxy. They want isolation (nobody else's keys billed, nobody else's cached answers seen) without running their own infrastructure.
- **Interview / portfolio audience:** engineers evaluating the project who care about the precision/recall story on a labeled set more than the raw throughput story.

## 3. Core user stories (must-have for v1)

1. As a developer, I point my existing OpenAI client at `http://my-proxy/v1` and get identical responses back, with one extra `cache_metadata.outcome` field telling me HIT / MISS / BYPASS.
2. On a cache miss, the proxy embeds the prompt, forwards it to the real LLM, and stores the response — automatically, transparently.
3. On a subsequent identical or near-identical prompt, the proxy serves the cached response above a configurable similarity threshold without calling the LLM.
4. I can set `SIMILARITY_THRESHOLD` per deployment and run `POST /eval/threshold-sweep` to measure precision/recall/F1 against a labeled set so I can defend the choice with numbers, not vibes.
5. Cache entries expire by TTL; I can manually purge a single entry or the entire cache.
6. I can force a fresh generation by sending `X-Cache-Bypass: true` on a request.
7. I can read `GET /metrics` to see hit rate, total requests, total cost saved (USD), total tokens saved, and average HIT-vs-MISS latency.
8. (BYOK) Two callers using different provider keys asking the same question each get their own cache entry — neither can see the other's cached response, neither incurs the other's bill.

## 4. Stretch / post-MVP features

- Wire the proxy in front of one of the sibling projects (RAG or Agent) and report real before/after cost numbers from actual usage.
- Distributed mode: multiple proxy instances sharing one cache backend without duplicate-write races.
- Auto-tune the similarity threshold based on observed hit-rate vs a target false-positive rate.
- Streaming response caching.
- Per-tenant rate limiting / abuse controls.

## 5. Non-goals (deliberately out of scope, with reasoning)

- **Streaming response caching.** v1 caches complete responses only. Streaming introduces chunk-level identity problems, partial-cache write semantics, and real-time error recovery that all meaningfully expand scope.
- **Cache warming / pre-population pipelines.** Adds an ingestion path that's separate from the proxy's hot path; for v1 the cache is built by whatever traffic happens to flow through it.
- **Multi-model routing / fallback chains.** That's a different product (a router). The proxy forwards to exactly one allowlisted upstream per request and only acts as a cache on the way through.
- **Centralized management plane / multi-region coordination.** This is a single-process, single-host service. The architecture is portable to Redis/Postgres when needed; orchestration is the operator's problem.
- **ANN index for semantic lookup below the few-thousand-entry scale.** `np.dot` over an in-memory numpy array of all stored embeddings is fast at demo scale; FAISS/sqlite-vec/pgvector only earn their complexity past ~5k entries. The codebase ships a warn-only `MAX_SEMANTIC_SCAN_ENTRIES` guardrail (default 5000) to surface when that limit is being approached.
- **Circuit breaker / advanced resilience in front of upstream LLM.** v1 retries bounded transient failures; it does not trip an open circuit on sustained failure. Single-instance demo scale doesn't earn the complexity.
- **A multi-tenant, paid, hosted version of this proxy.** That's a business decision, not a v1 product decision. This codebase is the artifact; hosting is a separate problem.

## 6. Success criteria (what "done" looks like for this project)

The v1 is done when *all* of the following are true (current state in brackets):

- [x] The proxy mirrors the OpenAI `/v1/chat/completions` shape end-to-end with one added `cache_metadata` field on the response.
- [x] Two-tier cache: O(1) exact hash + cosine semantic fallback.
- [x] TTL-based invalidation and a working manual purge endpoint.
- [x] `X-Cache-Bypass` header skips cache without errors.
- [x] `GET /metrics` returns hit rate, total cost saved (model-aware USD), total tokens saved, hit-vs-miss latency, and a per-user breakdown.
- [x] Precision/recall curve measured across ≥3 thresholds against a labeled set of ≥30 pairs, default threshold justified in writing against the curve.
- [x] CI green across py3.10/3.11/3.12 + Windows 3.11 with lint + a black-box Docker smoke pass.
- [x] A README that an interviewer can read in five minutes and understand the project, the threshold choice, and how to run it.
- [ ] The `/v1/chat/completions` endpoint starts via `uvicorn src.proxy.main:app` **in the canonical tree** (verified: 114/114 tests pass against remote `main`, which includes `routes/chat.py`; the local Desktop copy lost that file in a folder move and needs a one-command `git checkout origin/main -- src/proxy/routes src/proxy/static` restore first — see `todos.md` P0).
- [ ] A live cloud deploy URL where anyone can hit `/health` and see `{"status": "ok", "phase": 7}`.
- [ ] The pre-launch verification runbook in `docs/LAUNCH_CHECKLIST.md` executed end-to-end with two real provider keys (Alice / Bob, OpenRouter / Gemini).
- [ ] Stretch: before/after cost numbers from real traffic through a sibling project.

The first eight are demonstrable from the canonical tree today (verified this session); the last four are owner tasks — the first of them blocked only on the file restore, the rest on owner actions.

## 7. How this project's "done" differs from typical

This is a portfolio/interview-grade project, not a product to sell. The success bar is **defensibility of the threshold choice in an interview**, **evidence the cache works on labeled data**, and **a deployable artifact** — not revenue, throughput, or uptime. That's why so much of the codebase is dedicated to one labeled set and one endpoint (`/eval/threshold-sweep`), and why the "interview story" framing appears in the source docs rather than the README.
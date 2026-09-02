# AI Engineering Portfolio — Project 03

> **⚠ Historical planning document** (pre-build). Written before implementation; kept for the project's narrative. Current truth: `docs/design.md` (architecture), `docs/progress.md` (what actually happened), `docs/todos.md` (open work).


> **Difficulty:** ●●●○○ Intermediate

## Semantic Caching Layer for LLM Cost Reduction

A drop-in proxy that sits in front of any LLM API, recognizes when a new prompt means roughly the same thing as one it's already answered, and serves the cached response instead of paying for another generation — with the numbers to prove how much it actually saved.

---

## Why this isn't a generic project

Almost nobody builds this — most people just complain about their API bill. This is an infra story, not a prompting story: cache key strategy, similarity threshold tuning, and invalidation are the real problems, and getting the threshold wrong either serves wrong answers (too loose) or never hits the cache at all (too strict). That's a genuinely interesting tradeoff to reason about and defend in an interview.

---

## Core Requirements

1. Build a proxy service that sits between a client and any LLM API — client sends a prompt to your proxy, proxy decides cache hit or miss, and either returns a cached response or forwards to the real LLM and caches the new response.
2. On a cache miss, embed the incoming prompt and store it alongside the response, with a similarity index for future lookups (not just exact string match).
3. On a new request, embed and compare against cached entries using cosine similarity; serve the cached response if similarity is above a configurable threshold.
4. Make the similarity threshold configurable per-deployment, and document the precision/recall tradeoff at different threshold values using a labeled test set (pairs of prompts marked as "should hit" / "should not hit").
5. Support cache invalidation: TTL-based expiry, and a manual purge endpoint for specific entries or the whole cache.
6. Track and expose metrics: hit rate, estimated cost saved (based on token counts of what would have been a fresh generation), and average latency for hits vs misses.
7. Support a bypass mechanism (a header or flag) so a caller can force a fresh generation when they need to skip the cache.

---

## Out of Scope (v1)

- Multi-tenant cache isolation — single shared cache namespace is fine for v1.
- Streaming response caching — cache complete responses only.
- Cache warming/pre-population pipelines.

---

## Stretch Goals

- Add a distributed mode where multiple proxy instances share one cache backend correctly (no duplicate cache writes racing).
- Auto-tune the similarity threshold based on observed hit-rate vs a target false-positive rate.
- Wire this proxy in front of Project 01 (RAG) or Project 02 (Agent) as a real integration, and report the cost savings on that specific workload.

---

## Success Metrics (what "done" looks like)

- Documented precision/recall curve across at least 3 threshold values on your labeled test set.
- Live dashboard showing hit rate and cumulative cost saved.
- Demonstrated drop-in compatibility: same API shape as the underlying LLM API, so a client only changes its base URL.

---

## Skills You'll Master

Caching strategy, similarity search, cost/latency instrumentation, proxy/middleware design, threshold tuning under precision/recall tradeoffs.

---

## Resume Line (target)

> "Built a semantic caching proxy for LLM APIs using embedding similarity matching, with a tuned threshold validated against a labeled test set, cutting redundant API spend with live hit-rate and cost-saved tracking."

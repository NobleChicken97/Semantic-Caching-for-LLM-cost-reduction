# Project 03 — Technical Detail
## Semantic Caching Layer for LLM Cost Reduction

---

## 1. Architecture Overview

```
Client
   -> POST to Proxy /v1/chat/completions (mirrors LLM API shape)
   -> Proxy:
        1. Check bypass header -> if set, skip to step 5
        2. Embed incoming prompt (BGE-small)
        3. Similarity search against cache index (top-1 nearest)
        4. If similarity >= threshold -> return cached response
           (log as HIT, record latency + tokens "saved")
        5. Else -> forward request to real LLM API
        6. Store new (prompt_embedding, response, metadata) in cache
        7. Return fresh response (log as MISS, record actual cost)
```

Metrics endpoint aggregates HIT/MISS logs into hit rate, cost saved, and latency comparison, served to a small dashboard.

---

## 2. Tech Stack (with rationale)

| Layer | Choice |
|-------|--------|
| Proxy server | FastAPI — async, easy to mirror the OpenAI/Anthropic request/response shape |
| Embeddings | BAAI/bge-small-en-v1.5 (CPU-friendly, consistent with Project 01 so you reuse the same model) |
| Similarity index | Start with a simple in-memory/SQLite + numpy cosine similarity for v1 (cache sizes are small enough this is fine); upgrade to Qdrant/Chroma only if you want infra-parity with Project 01 |
| Cache backend | SQLite or Redis for entries (prompt text, embedding, response, created_at, ttl, hit_count) |
| Metrics/dashboard | Streamlit (small, reused skill) or a plain FastAPI + Chart.js page |
| Token/cost calc | tiktoken or the relevant tokenizer for whichever model you're proxying, to estimate token counts for "cost saved" math |

---

## 3. Data Model

**CacheEntry**
- entry_id, prompt_text, prompt_embedding, response_text, model_used, created_at, expires_at, hit_count, last_hit_at

**RequestLog**
- log_id, timestamp, prompt_text, outcome (HIT/MISS), matched_entry_id (nullable), similarity_score (nullable), latency_ms, estimated_cost_usd, tokens_in, tokens_out

**LabeledTestPair**
- pair_id, prompt_a, prompt_b, should_match (bool) — used to validate the threshold, not used at runtime

---

## 4. API Contract

### `POST /v1/chat/completions` (mirrors underlying LLM API shape)
```
headers: { X-Cache-Bypass: true|false }
body: { messages, model, ...standard LLM params }
-> standard LLM-shaped response, plus an extra field:
   { ..., cache_metadata: { outcome: "HIT"|"MISS", similarity_score } }
```

### `GET /metrics`
```
-> { hit_rate, total_requests, estimated_cost_saved_usd,
     avg_latency_hit_ms, avg_latency_miss_ms }
```

### `POST /cache/purge`
```
body: { entry_id (optional) }   # omit to purge entire cache
-> { purged_count }
```

### `POST /eval/threshold-sweep`
```
body: { thresholds: [0.80, 0.85, 0.90, 0.95] }
-> precision/recall at each threshold against LabeledTestPair set
```

---

## 5. Build Plan (phased)

**Phase 1 — Proxy skeleton + exact-match cache (2 days)**
- FastAPI service mirroring the target LLM API's request/response shape
- Exact string-match cache first (no embeddings yet) to validate the proxy plumbing end-to-end

**Phase 2 — Semantic matching (2–3 days)**
- Add BGE embeddings on incoming prompts
- Cosine similarity search against stored entries
- Configurable threshold via env var/config

**Phase 3 — Threshold validation (2 days)**
- Author the LabeledTestPair set (at least 20–30 pairs, a mix of "should match" paraphrases and "should not match" near-misses)
- Build the /eval/threshold-sweep endpoint, run it across several threshold values, pick and justify your default

**Phase 4 — Invalidation + bypass (1–2 days)**
- TTL expiry on entries
- Manual purge endpoint
- Bypass header handling

**Phase 5 — Metrics + dashboard (2 days)**
- RequestLog table wired into every request
- /metrics endpoint aggregating hit rate, cost saved, latency
- Small dashboard visualizing these over time

**Phase 6 — Deploy + integrate (1–2 days, stretch)**
- Deploy proxy to Render/Railway free tier
- Point Project 01's generation call (or Project 02's LLM calls) through this proxy and report real before/after cost numbers

---

## 6. Testing Notes

- The threshold-sweep against LabeledTestPair IS your core test — document precision/recall at each threshold value in the README, this is the single most interview-worthy artifact in this project.
- Test TTL expiry with a short TTL in test mode rather than waiting out a real-world expiry window.
- Test concurrent requests for the same near-duplicate prompt don't create duplicate cache entries (relevant if you build the distributed-mode stretch goal).

---

## 7. Deployment

- Single FastAPI service, deployable to Render/Railway free tier.
- SQLite is sufficient for the cache backend at demo scale; note in the README that Redis/Postgres would be the production swap for higher concurrency.
- Keep a small, clearly-labeled demo LLM API key with a spend cap if the proxy is publicly reachable.

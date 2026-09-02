# PROJECT 03 — Semantic Caching Layer for LLM Cost Reduction

> **⚠ Historical planning document** (pre-build). Written before implementation; kept for the project's narrative. Current truth: `docs/design.md` (architecture), `docs/progress.md` (what actually happened), `docs/todos.md` (open work).

**Difficulty:** ●●●○○ Intermediate | **Est. total time:** ~10–13 days part-time

---

## 1. Ideation — Why This Project, Why It's Different

Almost nobody builds this. Most people just complain about their API bill and move
on. A semantic cache is an infra story, not a prompting story — the real problems
are cache key strategy, similarity threshold tuning, and invalidation. Get the
threshold wrong and you either serve confidently wrong answers (too loose) or never
hit the cache at all (too strict). That tradeoff is exactly the kind of thing that's
genuinely interesting to reason about and defend in an interview.

It's also the smallest of the three projects, which makes it a good first build —
and it's designed to plug directly in front of Project 01 (RAG) or Project 02
(Agent) afterward, so you get a real before/after cost story instead of a
synthetic benchmark.

**The interview story you're building toward:** *"I built a drop-in semantic cache
proxy, validated the similarity threshold against a labeled test set with a
documented precision/recall tradeoff, and wired it in front of my RAG system to
show real cost savings on real traffic."*

---

## 2. Goals vs. Non-Goals

### In scope (v1)
- A proxy service between a client and any LLM API — client talks to the proxy
  exactly like it would talk to the real API
- On a cache miss: embed the prompt, store it + the response, with a similarity
  index for future lookups (not just exact string match)
- On a new request: embed and compare against cached entries via cosine
  similarity; serve the cached response above a configurable threshold
- A configurable similarity threshold, documented against a labeled test set
  (pairs marked "should hit" / "should not hit")
- Cache invalidation: TTL-based expiry + a manual purge endpoint
- Metrics: hit rate, estimated cost saved, average latency (hits vs. misses)
- A bypass mechanism so a caller can force a fresh generation

### Explicitly out of scope (v1)
- Multi-tenant cache isolation — single shared cache namespace is fine for v1
- Streaming response caching — cache complete responses only
- Cache warming/pre-population pipelines

### Stretch goals (only after v1 is solid)
- Distributed mode: multiple proxy instances sharing one cache backend without
  duplicate-write races
- Auto-tune the similarity threshold based on observed hit-rate vs. a target
  false-positive rate
- Wire this proxy in front of Project 01 or Project 02 as a real integration and
  report actual cost savings on that workload

---

## 3. Success Metrics (what "done" looks like)
- [ ] Documented precision/recall curve across at least 3 threshold values on
      your labeled test set
- [ ] A live dashboard showing hit rate and cumulative cost saved
- [ ] Demonstrated drop-in compatibility — same API shape as the underlying LLM
      API, so a client only ever changes its base URL

---

## 4. Architecture & Workflow

```
Client
  -> POST to Proxy /v1/chat/completions  (mirrors the target LLM API's shape)
  -> Proxy:
       1. Check bypass header -> if set, skip straight to step 5
       2. Embed the incoming prompt (BGE-small)
       3. Similarity search against the cache index (top-1 nearest)
       4. If similarity >= threshold -> return the cached response
            (log as HIT, record latency + tokens "saved")
       5. Else -> forward the request to the real LLM API
       6. Store the new (prompt_embedding, response, metadata) in the cache
       7. Return the fresh response (log as MISS, record actual cost)

Metrics endpoint aggregates HIT/MISS logs into hit rate, cost saved, and
latency comparison, served to the dashboard.
```

---

## 5. Tech Stack (and why)

| Layer | Choice | Why |
|---|---|---|
| Proxy server | FastAPI | Async, trivial to mirror the OpenAI/Anthropic request/response shape |
| Embeddings | `BAAI/bge-small-en-v1.5` | CPU-friendly, and reuses the same model as Project 01 — one less thing to relearn |
| Similarity index | In-memory/SQLite + numpy cosine similarity for v1 | Cache sizes are small enough that this is genuinely sufficient — only upgrade to Qdrant/Chroma if you want infra-parity with Project 01 |
| Cache backend | SQLite (or Redis if you want the extra practice) | Entries: prompt text, embedding, response, created_at, ttl, hit_count |
| Metrics/dashboard | Streamlit (reused skill) or a plain FastAPI + Chart.js page | Either is fine; Streamlit is faster to build |
| Token/cost calc | `tiktoken` or the relevant tokenizer | Needed to estimate "cost saved" honestly |

### Hardware reality check (Lenovo IdeaPad Gaming 3, i5-10300H, GTX 1650, 16GB RAM)
- This is the lightest of the three projects on resources. A single embedding
  model + SQLite + FastAPI comfortably fits in 16GB RAM alongside your normal
  dev tools.
- No GPU dependency anywhere in this stack — embedding one short prompt per
  request on CPU is fast enough that latency won't be the bottleneck.
- If you later chain this in front of Project 01 or Project 02 for the stretch
  goal, keep an eye on total RAM if you're running all three projects' services
  simultaneously — close anything you're not actively demoing.

---

## 6. Data Model

**CacheEntry** — `entry_id, prompt_text, prompt_embedding, response_text, model_used, created_at, expires_at, hit_count, last_hit_at`

**RequestLog** — `log_id, timestamp, prompt_text, outcome (HIT/MISS), matched_entry_id (nullable), similarity_score (nullable), latency_ms, estimated_cost_usd, tokens_in, tokens_out`

**LabeledTestPair** — `pair_id, prompt_a, prompt_b, should_match (bool)` — used only to validate the threshold, never read at runtime

---

## 7. API Contract

```
POST /v1/chat/completions          (mirrors the underlying LLM API shape)
  headers: { X-Cache-Bypass: true|false }
  body: { messages, model, ...standard LLM params }
  -> standard LLM-shaped response, plus an extra field:
     { ..., cache_metadata: { outcome: "HIT"|"MISS", similarity_score } }

GET /metrics
  -> { hit_rate, total_requests, estimated_cost_saved_usd,
       avg_latency_hit_ms, avg_latency_miss_ms }

POST /cache/purge
  body: { entry_id (optional) }      # omit to purge the entire cache
  -> { purged_count }

POST /eval/threshold-sweep
  body: { thresholds: [0.80, 0.85, 0.90, 0.95] }
  -> precision/recall at each threshold against the LabeledTestPair set
```

---

## 8. UI Pages / Screens

1. **Metrics Dashboard page** — hit rate, total requests, cumulative cost saved,
   and a hit-vs-miss latency comparison, refreshed live or on demand.
2. **Cache Browser page** — searchable/sortable table of `CacheEntry` rows
   (prompt, hit_count, last_hit_at, expires_at), with a manual purge action per
   row or for the whole cache.
3. **Threshold Sweep page** — button to run `/eval/threshold-sweep` across
   several thresholds, table/curve of precision and recall at each value, with
   the chosen default threshold called out and justified in text.
4. **Live Request Log page** — recent requests streamed or polled, each row
   showing outcome (HIT/MISS), similarity score (if matched), latency, and
   estimated cost.

---

## 9. Build Plan — Phased TODO Checklist

### Phase 1 — Proxy skeleton + exact-match cache (2 days)
- [ ] Stand up a FastAPI service mirroring your target LLM API's request/response
      shape (decide upfront: OpenAI-shaped or Anthropic-shaped)
- [ ] Implement an exact-string-match cache first — no embeddings yet — purely to
      validate the proxy plumbing end-to-end
- [ ] Confirm a repeated identical request returns the cached response with the
      `cache_metadata` field populated correctly

### Phase 2 — Semantic matching (2–3 days)
- [ ] Add `bge-small-en-v1.5` embeddings on every incoming prompt
- [ ] Implement cosine similarity search against stored entries (numpy is fine
      at this scale — don't reach for a vector DB yet)
- [ ] Make the similarity threshold configurable via env var/config, not
      hardcoded

### Phase 3 — Threshold validation (2 days)
- [ ] Author the `LabeledTestPair` set: at least 20–30 pairs, a real mix of
      "should match" paraphrases and "should NOT match" near-misses (this is
      the part people skip — don't skip it)
- [ ] Build `/eval/threshold-sweep` and run it across several threshold values
- [ ] Pick a default threshold and write down *why*, in terms of the
      precision/recall tradeoff you measured

### Phase 4 — Invalidation + bypass (1–2 days)
- [ ] Implement TTL expiry on cache entries
- [ ] Implement the manual `/cache/purge` endpoint (single entry + full purge)
- [ ] Implement the `X-Cache-Bypass` header handling end-to-end

### Phase 5 — Metrics + dashboard (2 days)
- [ ] Wire `RequestLog` writes into every request, hit or miss
- [ ] Build `/metrics` aggregating hit rate, cost saved, latency
- [ ] Build the dashboard pages from Section 8

### Phase 6 — Deploy + integrate (1–2 days, stretch)
- [ ] Deploy the proxy to Render/Railway free tier
- [ ] Point Project 01's generation call (or Project 02's LLM calls) through this
      proxy and report real before/after cost numbers from actual usage

---

## 10. Testing Strategy
- [ ] Treat the threshold-sweep against `LabeledTestPair` as your core test —
      document precision/recall at each threshold value in the README; this is
      the single most interview-worthy artifact in this project
- [ ] Test TTL expiry using a short TTL in test mode rather than waiting out a
      real-world expiry window
- [ ] If you build the distributed-mode stretch goal: test that concurrent
      requests for the same near-duplicate prompt don't create duplicate cache
      entries (a race condition that's easy to introduce and easy to miss)

---

## 11. Deliverables Checklist
- [ ] GitHub repo with a README: problem statement → architecture →
      precision/recall table at each threshold (front and center) → how to run
      it locally
- [ ] `LabeledTestPair` set committed to the repo (reproducible threshold choice)
- [ ] Live dashboard screenshot or short demo video showing hit rate + cost saved
      accumulating over a session
- [ ] If you did the integration stretch goal: a clearly labeled before/after
      cost comparison from real Project 01/02 traffic
- [ ] A spend cap on the demo LLM API key if the proxy is publicly reachable

---

## 12. Resume Line (target)
> "Built a semantic caching proxy for LLM APIs using embedding similarity
> matching, with a tuned threshold validated against a labeled test set, cutting
> redundant API spend with live hit-rate and cost-saved tracking."

## 13. Skills You'll Walk Away With
Caching strategy, similarity search, cost/latency instrumentation,
proxy/middleware design, threshold tuning under precision/recall tradeoffs.

# Technical Design — Semantic Caching Layer for LLM Cost Reduction

> Companion to `prod.md` (the "why"). Pair these when explaining the project.

## 1. High-level architecture

```
                                    ┌──────────────────────────────────┐
                                    │  client (any OpenAI-shaped SDK)  │
                                    │  Authorization: Bearer <caller's> │
                                    │   openai.base_url = proxy URL    │
                                    └──────────────┬───────────────────┘
                                                   │
                                                   ▼
        ┌──────────────────────────────────────────────────────────────┐
        │  FastAPI proxy (src/proxy/main.py + routes/chat.py)          │
        │                                                              │
        │  lifespan-managed shared httpx.AsyncClient                  │
        │  BAAI/bge-small-en-v1.5 (CPU, L2-normalized, 384-dim)        │
        │                                                              │
        │   chat handler:                                              │
        │     1. resolve user_id = keyed-BLAKE2b(pepper, api_key)      │
        │     2. X-Cache-Bypass true? → forward → log BYPASS → return  │
        │     3. coalesce per (prompt_hash, model) via asyncio.Lock     │
        │     4. exact hash lookup   (composite UNIQUE prompt_hash+user)│
        │        HIT → return cached response, log HIT                 │
        │     5. semantic lookup     (cosine ≥ threshold, scoped to    │
        │        user + model)                                         │
        │        HIT → return cached response, log HIT                 │
        │     6. else → forward_to_llm(client, api_key, base_url)      │
        │         ├─ upstream error → OpenAI-shaped err, log ERROR     │
        │         └─ upstream 200  → store(prompt, emb, resp, ttl,    │
        │                                model, user) → log MISS       │
        └─────────────────┬──────────────────────────┬─────────────────┘
                          │                          │
                          ▼                          ▼
        ┌─────────────────────────┐    ┌────────────────────────────────┐
        │  SQLite (WAL, FK ON)    │    │  Upstream LLM (OpenAI-         │
        │  cache_entries          │    │   compatible) — openrouter or  │
        │  request_log            │    │   gemini via the allowlist     │
        │  daily_metrics (rollup) │    └────────────────────────────────┘
        │  labeled_test_pairs     │
        └─────────────────────────┘
                          │
                          ▼
        ┌──────────────────────────────────────────────────────────────┐
        │  GET /metrics (model-aware USD, tokens-saved, per-user)     │
        │  GET /cache/entries?q=  ·  GET /logs/recent?limit=            │
        │  GET /dashboard  (FastAPI + Chart.js single-page; needs the  │
        │                   static dir restored — see Known issues)    │
        └──────────────────────────────────────────────────────────────┘
```

## 2. Core data model (`src/proxy/database.py`)

**`cache_entries`**
- `entry_id` PK, `prompt_text`, `prompt_hash` (SHA-256 of canonical prompt), `prompt_embedding` (BLOB, float32, 384-dim), `response_json`, `model_used`, `user_id` (NOT NULL DEFAULT `'local'`), `expires_at`, `hit_count`, `last_hit_at`.
- Composite UNIQUE index `(prompt_hash, user_id)` — same prompt is fine across users, never twice for one user (replaces the inline `prompt_hash` UNIQUE that would have caused cross-user INSERT collisions).
- Single-column index on `prompt_hash` for the prefix-rule lookup that `idx_cache_hash_user` already supports.

**`request_log`**
- `log_id` PK, `timestamp`, `prompt_text`, `prompt_hash`, `outcome` CHECK ∈ {`HIT`, `MISS`, `BYPASS`, `ERROR`}, `matched_entry_id` (FK → `cache_entries.entry_id`, nullable), `similarity_score`, `latency_ms`, `estimated_cost_usd`, `tokens_in`, `tokens_out`, `user_id`.
- Index on `timestamp` for the `recent_logs()` and retention queries.
- Foreign keys are `ON DELETE SET NULL`-style via the `_detach_log_references()` helper, so purging cache entries nulls the `matched_entry_id` rather than cascading — metrics history survives.

**`labeled_test_pairs`** — `pair_id` PK, `prompt_a`, `prompt_b`, `should_match` CHECK ∈ {0,1}. Used only at `/eval/threshold-sweep` time, never read at runtime. Seeded with 31 hand-labeled pairs.

**`daily_metrics`** (Phase 7.6) — `date` PK (UTC date), `total_requests`, `hits`, `tokens_saved`, `cost_saved_usd`. Permanent rollup that survives the `prune_old_logs()` retention pass.

## 3. Major components

| Module | Owns |
|---|---|
| `main.py` | App construction, lifespan (httpx client + DB init + retention pass + embedding warmup), admin auth dependency, health/metrics/purge/sweep/cache-entries/logs/dashboard routes |
| `routes/chat.py` (or equivalent — see Known issues) | `POST /v1/chat/completions` handler: BYOK key extraction, user_id derivation, provider resolution, coalescing, two-tier lookup, forward_to_llm, response shape, logging |
| `cache.py` | Two-tier lookup (`lookup` / `_exact_lookup` / `_semantic_lookup`), `store`, `purge`, `_delete_entry`, `_detach_log_references`, `log_request`, `prune_old_logs` (retention rollup), `_rollup_totals`, `get_metrics`, `list_cache_entries`, `recent_logs` |
| `embedding.py` | Lazy-loaded `SentenceTransformer("BAAI/bge-small-en-v1.5")`, `embed_texts` (2D float32, L2-normalized), `embedding_dim` (=384), `cosine_similarity` (np.dot on unit vectors) |
| `llm_client.py` | `_post_with_retries` (bounded retry on 408/429/5xx + transport errors; honors `Retry-After`; surfaces >30 s waits immediately), `CircuitBreaker` (per-upstream CLOSED/OPEN/HALF_OPEN fail-fast guard), `forward_to_llm` (mock or real; shared lifespan client or one-off), `_mock_response` (echoes last user msg), `_estimate_tokens` (tiktoken with heuristic fallback) |
| `security.py` | `derive_user_id(api_key) = keyed-BLAKE2b(USER_ID_PEPPER, api_key)` (12-byte digest hex; HMAC-SHA256[:24] until 2026-09-02); `LOCAL_USER_ID = "local"` for keyless mock traffic |
| `config.py` | `Settings` (frozen dataclass), `get_settings()` (`lru_cache` factory), `PROVIDER_BASE_URLS` allowlist, `resolve_base_url()`, `DEFAULT_MODEL_PRICING`, `_parse_model_pricing()` |
| `models.py` | OpenAI-shaped request/response Pydantic models; BYOK `provider` extension field (never sent upstream); per-user metric model |
| `database.py` | Schema v2 (`_SCHEMA_V2`), `_migrate_user_scoping` (idempotent rebuild for legacy DBs), `seed_test_pairs` (31 pairs) |
| `eval.py` | `run_threshold_sweep` — batch-embed each unique prompt once, classify at every threshold from precomputed similarities, return P/R/F1 |
| `static/index.html` (missing) | Single-page dashboard: metrics cards, charts, cache browser, sweep runner, live log |

## 4. Non-trivial technical decisions (chosen / alternative(s) / why this won)

### 4.1 Two-tier lookup (exact hash → semantic fallback)

- **Chosen:** `_exact_lookup` (SHA-256) first; on miss, `_semantic_lookup` (BGE-small + cosine).
- **Alternative:** go straight to embedding on every request.
- **Why this won:** Identical prompts are far more common than paraphrases in production traffic, and SHA-256 lookup is O(1) on a composite index — identical re-requests avoid both an embedding call and a full scan. The semantic tier still serves the paraphrase case the exact tier can't.

### 4.2 Similarity threshold = 0.85 (data-driven)

- **Chosen:** `SIMILARITY_THRESHOLD=0.85`.
- **Alternatives considered:** 0.80 (more recall, more dangerous near-misses), 0.90 (perfect precision, lost half the recall), 0.95 (only serves nearly-identical prompts).
- **Why this won:** Measured F1 = 0.857 on the 31-pair labeled set; F1 falls in both directions from there. Below 0.85, near-miss antonym pairs (hello/goodbye Spanish at 0.864, quantum vs classical computing at 0.864) become false hits. Above 0.88, genuine paraphrases (2+2 at 0.860, sci-fi book recommendation at 0.851) stop hitting — silently destroying cost savings. Full curve and borderline-pair tables in `docs/THRESHOLD_ANALYSIS.md`. Methodology caveat: pairwise F1 is a conservative lower bound for live scan-max behavior.

### 4.3 In-memory numpy scan instead of a vector DB

- **Chosen:** `np.dot` over all stored embeddings per request, with a warn-only guardrail past `MAX_SEMANTIC_SCAN_ENTRIES` (default 5000).
- **Alternatives:** FAISS, sqlite-vec, pgvector, Chroma, Qdrant.
- **Why this won:** At demo scale (low-thousands of entries), a single numpy call over pre-normalized 384-dim vectors is sub-millisecond. The alternative would mean either a new service to run (Qdrant/Chroma) or a non-portable schema decision. The code is structured so the swap is one function.

### 4.4 BAAI/bge-small-en-v1.5 on CPU

- **Chosen:** `BAAI/bge-small-en-v1.5`, CPU only.
- **Why this won:** Same model used in the sibling RAG project (consistency), small enough to bake into a Docker image, no GPU dependency anywhere in the stack. Pinned by exact model name in the Dockerfile warmup so the threshold curve stays reproducible across rebuilds.

### 4.5 L2-normalized embeddings + cosine = np.dot

- **Chosen:** BGE-small returns L2-normalized vectors; cosine similarity reduces to `np.dot(query, stored)`.
- **Alternative:** cosine = `(q·s) / (||q|| * ||s||)` on un-normalized vectors.
- **Why this won:** Same result, half the per-pair work, and the math is trivially auditable. (The 2026-08-25 hardening round added a defensive re-normalization in `_deserialize_embedding` to catch a stored vector that drifted from unit length.)

### 4.6 SHA-256 of canonical prompt as exact-match key

- **Chosen:** `canonical_prompt() = "[model]{model}\n[role]{content}\n[role]{content}…"` then SHA-256. The model name is part of the cache identity.
- **Alternative:** hash just the messages; include sampling params (`temperature`, `top_p`) in the hash.
- **Why this won:** A gpt-4 request must never be served a gpt-3.5-turbo response whose body lies about which model produced it (the cache metadata would survive, but the response would mislead). Sampling params deliberately don't change the prompt — the *answer* should, and a cache hit for the same prompt at temperature 0.2 vs temperature 1.0 is the right behavior.

### 4.7 SQLite + WAL + FK ON (single file)

- **Chosen:** SQLite in WAL mode with `PRAGMA foreign_keys=ON`, no connection pool, no async driver.
- **Alternative:** Postgres (managed), Redis for cache + Postgres for metadata, an ORM.
- **Why this won:** Single-file, zero extra service, works on the laptop and on Render free tier with one disk attach. WAL gives concurrent readers, FK ON enforces the `request_log → cache_entries` invariant, and a connection per operation is cheap locally. The schema is intentionally portable (no SQLite-specific syntax in user-facing code; `INSERT OR REPLACE` is the only SQLite-ism) so the swap to Postgres is feasible.

### 4.8 BYOK over per-user accounts / shared pool

- **Chosen:** Each caller sends their own provider key; the proxy derives `user_id = keyed-BLAKE2b(USER_ID_PEPPER, key)` and scopes cache, logs, and metrics on it. The proxy itself never bills anything.
- **Alternative:** A shared pool of provider keys managed by the operator; rate-limiting per tenant.
- **Why this won:** The stated use case is 10–15 hobbyists with their own free keys, where the operator must not be on the hook for anyone's bill. A keyed MAC (BLAKE2b, replacing HMAC-SHA256 in 2026-09-02 to satisfy CodeQL's sensitive-data crypto checks — same guarantees) keeps the raw key out of every log row and out of the database; the pepper makes brute-force back to the original key infeasible; a same-key derivation is stable across restarts. Rotation of `USER_ID_PEPPER` is deliberately a one-time decision (it would orphan every user's scoped history) and called out as such in `.env.example`.

### 4.9 Provider allowlist, not free-form base URL

- **Chosen:** `PROVIDER_BASE_URLS = {"openrouter": "...", "gemini": "..."}`; `resolve_base_url()` accepts either a known provider name or the literal allowlisted base URL (header wins over body). Anything else → 400 before any network call.
- **Alternative:** Pass the upstream URL through verbatim.
- **Why this won:** A free-form URL turns the proxy into an open relay and an SSRF-shaped hole. Allowlist + canonicalization keeps both the deployment simple (OpenRouter today, more tomorrow) and the security model clear.

### 4.10 Bounded upstream retries + per-upstream circuit breaker

- **Chosen:** `_post_with_retries` retries 408/429/5xx + transport errors with exponential backoff from `LLM_RETRY_BACKOFF_SECONDS` (default 0.5 s, capped at 8 s). A numeric `Retry-After` header wins; waits > 30 s fail fast (daily-cap 429s). On top of that, a hand-rolled `CircuitBreaker` (per upstream base URL) opens after `LLM_BREAKER_FAILURE_THRESHOLD` (default 5) **consecutive** exhausted failures and fails fast with an OpenAI-shaped 503 for `LLM_BREAKER_RESET_SECONDS` (default 30 s); one HALF_OPEN probe then goes through — success closes, failure restarts the cooldown. Only retryable-class outcomes count as failures (a 401 storm is the caller's problem, not the provider's).
- **Alternative:** Unbounded retries; no breaker (the original v1 posture); `circuitpybreaker` dependency.
- **Why this won:** Retrying only what the server explicitly did NOT succeed on (4xx 408/429 + 5xx) keeps the no-double-billing guarantee. Retries bound the cost of ONE bad call; the breaker bounds the cost of a bad upstream — ~30 lines, zero dependencies, and one breaker per allowlisted base URL so a failure storm on one provider never blocks another. Set `LLM_BREAKER_FAILURE_THRESHOLD=0` to opt out.

### 4.11 Model-aware cost estimation with prefix inheritance

- **Chosen:** `_estimate_cost(model, tokens_in, tokens_out)` looks up `(in_per_1M, out_per_1M)` from `DEFAULT_MODEL_PRICING` + `MODEL_PRICING` env override, falling back to the longest-matching prefix (e.g. `gpt-4o-mini-2025-01-15` inherits `gpt-4o-mini`). Unknown model → `$0.00` (free-tier safe).
- **Alternative:** Hardcode gpt-3.5-turbo rates (the original Phase 5 behavior); keep the `len//4` heuristic for token counts (the original implementation, replaced 2026-09-01).
- **Why this won:** BYOK mode sees free models from OpenRouter and Gemini where there's literally no per-token price to estimate — better to honestly say $0.00 than to fabricate. Prefix matching means adding a dated variant doesn't require a new entry. Token **counts** now come from tiktoken `cl100k_base` (lazy-loaded, heuristic fallback if the BPE tables can't load; prewarmed into the Docker image), so the tokens-saved headline and per-user rollups are accurate rather than approximate — while the pricing *decision* (honest $0.00 for unknown models) is unchanged.

### 4.12 Single-process request coalescing (per prompt hash)

- **Chosen:** Per-`(prompt_hash, model, user_id)` `asyncio.Lock` registry in `routes/chat.py`; concurrent identical prompts share one upstream call.
- **Alternative:** Distributed lock (Redis SETNX); no coalescing.
- **Why this won:** A traffic spike of identical paraphrases (the same bot retry loop, a popular "what is the capital of France" question) would otherwise stampede the upstream and waste budget on N near-identical generations. The lock registry is bounded; multi-worker/multi-instance deployments need a distributed lock — documented at the lock site.

### 4.13 Shared lifespan-managed httpx client

- **Chosen:** One `httpx.AsyncClient(timeout=120s)` on `app.state.http_client`, reused by every `forward_to_llm` call. Standalone callers (scripts/tests) get a one-off client per call by design.
- **Alternative:** New client per request; a custom connection pool.
- **Why this won:** Reuses connections across requests and avoids per-request TCP/TLS setup; the one-off fallback keeps tests fast and self-contained.

### 4.14 30-day raw log retention with permanent rollup

- **Chosen:** `prune_old_logs()` rolls raw rows older than 30 days into `daily_metrics` (totals, hits, tokens saved, cost saved) and then deletes them, lazily on lifespan startup. `get_metrics()` unions the rollup with the raw window so lifetime totals never regress.
- **Alternative:** Keep raw forever; truncate by date without rollup.
- **Why this won:** A SQLite file that grows unboundedly is the kind of slow leak that bites a free-tier deployment at month six. A permanent per-day rollup is cheap, answers the "what did we save all-time" question forever, and per-user breakdown remains accurate within the 30-day window (the global rollup is intentionally global, not per-user — that trade-off is documented in `KNOWN LIMITATIONS`).

### 4.15 Single service dashboard (FastAPI + Chart.js via CDN)

- **Chosen:** `/dashboard` serves `src/proxy/static/index.html`; Chart.js loaded from CDN.
- **Alternative:** Streamlit; a separate frontend service.
- **Why this won:** Zero new Python deps, one deployable unit, same-port integration. (Note: the `src/proxy/static/` directory is currently missing from the working tree — see Known issues. Restore before demo.)

### 4.16 OpenAI-shaped error contract for upstream failures

- **Chosen:** Upstream failure → `{"error": {"message", "type", "code"}}` with the upstream status passed through, or `502` for transport-level failures. Logged as `outcome='ERROR'` with zeroed cost/tokens, never written to the cache.
- **Alternative:** Raise to the framework default 500; cache the failure.
- **Why this won:** OpenAI SDK clients expect OpenAI-shaped errors; defaulting to 500 surprises them. Caching a failure means a permanent bad entry — anti-pattern.

### 4.17 Message-only embedding input (Phase 9)

- **Chosen:** `ChatCompletionRequest.embedding_text()` (messages joined without the `[model]` line) feeds `lookup()`/`store()`/logging; `canonical_prompt()` keeps the model line for hash identity.
- **Alternative:** Re-tune the threshold upward on prefixed strings.
- **Why this won:** A constant model prefix dominates short user text in embedding space and inflated every live similarity (measured: recall 1.0 / precision ~0.45 vs the tuned R=0.9375/P=0.7895). Model isolation never needed the prefix (`model_used` column + filter). Re-tuning would launder a client-specific artifact into a permanent global constant. Caught real bugs on the way in: the old replace-scope in `store()` and the two-column unique index both assumed model-in-hash (fixed: model-scoped replace with FK detach, triple-key index with migration).

### 4.18 Two-signal semantic veto (Phase 9)

- **Chosen:** After a candidate clears the cosine threshold, `entity_veto()` can still refuse the HIT, logged as MISS: (1) entity swap — disjoint capitalized-token sets (sentence-initial excluded) *plus* shared template (Jaccard ≥ 0.2, calibrated); (2) fact-type swap — disjoint keyword sets (`capital`, `population`, …) with no gate. Lexical rules live in dependency-free `src/proxy/text.py`, shared verbatim with `scripts/analyze_overlap.py` and `scripts/calibrate_trust.py`.
- **Alternative:** NER model; pure threshold raise; global Jaccard floor.
- **Why this won:** NER is a heavy dep on the hot path; a threshold raise destroys recall; a global Jaccard floor provably kills labeled positives (three sit at 0.000 — measured in `analyze_overlap.py`). The template gate exists because calibration showed the naive entity rule vetoing a true paraphrase ("WWII" vs "World War II").

## 5. Known limitations & deliberately deferred work

These are documented, accepted decisions — not oversights:

1. **Semantic scan is O(n) per request.** Fine to a few thousand; warn-only guardrail at `MAX_SEMANTIC_SCAN_ENTRIES`. Beyond that, swap in an ANN index (FAISS / sqlite-vec / pgvector).
2. **Coalescing is single-process.** The per-hash `asyncio.Lock` protects only one uvicorn worker. Multi-worker deployments need a distributed lock (Redis SETNX).
3. **No SQLite connection pool.** Connections are cheap locally and WAL gives concurrent readers; pooling is added complexity with no measured payoff yet.
4. **Pairwise F1 is a conservative lower bound** for live scan-max behavior. Documented in `THRESHOLD_ANALYSIS.md`.
5. **The shared httpx client requires a running lifespan.** Direct calls without a lifespan get a one-off client by design.
6. **Per-user metrics only cover the raw 30-day window** — the lifetime rollup is global by design.
7. **BYOK identity depends on `USER_ID_PEPPER`** (never rotate, same as `ADMIN_TOKEN`).
8. **Persistence depends on a disk.** `render.yaml` ships a commented persistent-disk block (`/var/data` + `CACHE_DB_PATH=/var/data/cache.db`); the schema migrates itself wherever the file lives.
9. **Free-tier deployments lose cache/history on redeploy.** Acceptable for demo; paid plan with disk fixes it.
10. **The circuit breaker is per-process.** Like coalescing, breaker state lives in one uvicorn worker's memory; multi-instance deployments get independent breakers per instance (acceptable — each still protects its own callers).
11. **No streaming response caching.** v1 caches complete responses only.
12. **Veto blind spots (Phase 9, measured):** the guard sees nothing on all-lowercase, non-Latin-script, or single-letter-difference entities; near-duplicate negatives with no entities and no fact keywords (haiku/limerick, quantum/classical, hello/goodbye, exercise-risk, ML/DL) still clear 0.85 — expected live precision ≈0.73 vs 0.79 documented. Same-template/different-topic pairs ("Write two sentences about sourdough starters" vs "...pickling cucumbers", 4/20 cross-hit at 0.851-0.879) survive signal 2 deliberately: they carry food/activity nouns, not fact-type keywords, and extending FACT_TYPES to topics would overfit four data points while risking vetoes of legitimate same-topic queries. Short phatic collisions ("Thanks!" vs "Good morning") sit at 0.84-0.85 on threshold alone with no vetoable features — accepted residue, not a gate.
13. **Purge is unaudited.** No who/when record today — a mid-session purge looks identical to data loss in forensics (learned live 2026-09-04). Follow-up: log line + "last purged" on the dashboard.

## 6. Integration notes (external dependencies)

- **`sentence-transformers` + `BAAI/bge-small-en-v1.5`:** first import downloads ~130 MB from the HF Hub; subsequent builds use the HF cache. The Dockerfile runs `embed_texts(['warmup'])` during build to bake the model into the image, so cold starts don't re-download.
- **PyTorch CPU pin:** the Dockerfile installs `torch==2.5.1+cpu` from the PyTorch CPU index **before** `requirements.txt` so pip can't resolve a CUDA build from PyPI. Same pin in CI; `torch.cuda.is_available() == False` is asserted in the docker-smoke job.
- **Upstream LLM APIs:** OpenAI-compatible. `PROVIDER_BASE_URLS` whitelists OpenRouter (`https://openrouter.ai/api/v1`) and Gemini (`https://generativelanguage.googleapis.com/v1beta/openai`); both expose an OpenAI-shaped `/chat/completions`. Adding another provider = one line in the allowlist.
- **HTTP transport:** `httpx` async client; one shared client per app process. Retries are bounded (see 4.10).
- **Chart.js (dashboard):** loaded from CDN at view time; first view needs internet. Pure offline mode would mean vendoring the script into the static dir — deferred.
- **GitHub Actions runners:** CPU-only torch pre-install mirrors the Dockerfile pin; HF model cache is keyed per-OS; `MOCK_LLM=true` is set at workflow env level so CI can never spend money; black-box smoke suite asserts exact accounting (+5 requests on the same prompt) and keeps similarity floors loose (0.80) so upstream BGE weight updates don't flake CI (unit suite gates 0.85).
- **Dependabot:** pip version-bump PRs disabled (the `>=` floors make them cosmetic under pinning); vulnerability coverage strengthened instead — `pip-audit` SARIF publishes to the Security tab, while Dependabot's separate CVE-driven security-update PRs stay active. GitHub Actions ecosystem stays on weekly grouped updates.
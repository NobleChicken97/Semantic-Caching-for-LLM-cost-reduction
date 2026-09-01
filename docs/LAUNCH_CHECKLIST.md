# 🚀 LAUNCH CHECKLIST — Everything YOU must do (owner runbook)

> **Generated:** 2026-08-23 · Updated 2026-08-25: all commands are PowerShell-native
> (`curl.exe` + JSON breaks on Windows PowerShell 5.x — PS strips inner quotes → 422).
> Code status: **Phases 1–7 + auto-tune + circuit breaker + tiktoken shipped & pushed**, CI green, 135 tests passing.
> Everything below is *human-only* work: accounts, clicks, secrets, invites.

---

## ⌨️ Windows command equivalents (this machine has no `make`)

| Instead of | Run |
|---|---|
| `make run` | `python -m uvicorn src.proxy.main:app --reload --host 127.0.0.1 --port 8000` |
| `make test` | `python -m pytest tests/ -q` |
| `make lint` | `ruff check src/ tests/ scripts/` |
| curl-with-JSON | `Invoke-RestMethod` (see the `Ask` helper in Phase A2 / B4 / D) |

---

## Phase A — Local sanity (30 min · $0)

- [ ] **A1. Sync + test locally**
  ```powershell
  git pull origin main
  python -m pytest tests/ -q        # expect: 135 passed
  ```
- [ ] **A2. (Recommended) Dry-run BYOK without spending anything — Windows/PowerShell edition**

  > ⚠️ Do NOT use `curl.exe` with JSON on PowerShell 5.x — PS strips inner quotes
  > and the server answers `422 json_invalid`. Use `Invoke-RestMethod` (below).
  > There is no `make` on Windows either; run uvicorn directly.

  Terminal 1 — server:
  ```powershell
  $env:MOCK_LLM = "true"
  $env:USER_ID_PEPPER = (python -c "import secrets; print(secrets.token_hex(32))")
  python -m uvicorn src.proxy.main:app --host 127.0.0.1 --port 8000
  ```

  Terminal 2 — define a tiny helper once, then reuse it:
  ```powershell
  function Ask([string]$Key,[string]$Provider,[string]$Model="gpt-3.5-turbo",
               [string]$Content="What is the capital of France?",
               [string]$Base="http://127.0.0.1:8000"){
    $h=@{}; if($Key){$h.Authorization="Bearer $Key"}
    $b=@{model=$Model;messages=@(@{role="user";content=$Content})}
    if($Provider){$b.provider=$Provider}
    Invoke-RestMethod -Uri "$Base/v1/chat/completions" -Method Post -Headers $h `
      -ContentType "application/json" -Body ($b|ConvertTo-Json -Depth 5)
  }

  Ask "sk-test-alice"        # -> MISS   (.cache_metadata.outcome)
  Ask "sk-test-alice"        # -> HIT, similarity 1.0
  Ask "sk-test-bob"          # -> MISS  (Bob can't see Alice's entry!)
  Ask "sk-test-bob"          # -> HIT   (his own)
  (Invoke-RestMethod http://127.0.0.1:8000/cache/entries).entries |
    Select user_id, prompt_text      # -> 2 rows, different user_id values
  ```
  ✅ Pass = exactly that sequence: MISS, HIT, MISS, HIT + two scoped entries.

---

## Phase B — Accounts & API keys (45 min · $0, one optional $10)

### B1. OpenRouter key
1. https://openrouter.ai → sign up (GitHub login works, no credit card)
2. Profile icon → **Keys** → **Create Key** → name it → **copy immediately** (shown once)
3. **Pick today's free model:** https://openrouter.ai/models → filter **Price: Free** → copy an ID ending in `:free`
   > ⚠️ The `:free` roster **rotates**. As of mid-2026, DeepSeek/Mistral/Gemini are NOT free there; Llama/Qwen/Gemma variants usually are. Always re-check on launch day.
4. **Rate limits you inherit:** 20 req/min hard cap · **50 requests/day** fresh account.
   💡 One-time **$10 credit purchase permanently raises the daily cap to 1,000/day** (credits never expire). Recommended for whoever tests most — it's *their* key, *their* choice.

### B2. Gemini key
1. https://aistudio.google.com → sign in with Google → **Get API key** → Create (no GCP billing needed)
2. Note the current Flash model ID (e.g. `gemini-2.5-flash`) and your live quotas: AI Studio → **rate-limit** view
   - Free tier is roughly **10 RPM / few-hundred requests-per-day** for Flash-class (per *project*, shared by all keys in that project)
   - ⚠️ **Free-tier prompts may be used by Google to improve products** — fine for demo traffic, not for anything sensitive
3. ⚠️ **TIME-SENSITIVE:** new AI Studio keys default to "**auth keys**"; Google plans to reject *standard*-key requests in **September 2026**. Check your key's type in AI Studio and follow their migration prompt if shown.

### B3. Render account
1. https://render.com → **Get Started** → sign in **with GitHub** (authorizes repo access)
2. Free Hobby workspace is enough to launch (limits below).

### B4. Verify BOTH real keys locally — before deploying anything

Biggest de-risking step in this whole document: if something fails here, it's
your key or the code — not the deployment. Server runs locally with real
upstream calls; costs fractions of a cent of *your own* free quota.

Terminal 1 — restart the server in real-BYOK mode:
```powershell
$env:MOCK_LLM = "false"
$env:USER_ID_PEPPER = "paste-any-random-32-byte-hex-here"
python -m uvicorn src.proxy.main:app --host 127.0.0.1 --port 8000
```

Terminal 2 — define the helper once:
```powershell
function Ask([string]$Key,[string]$Provider,[string]$Model="gpt-3.5-turbo",
             [string]$Content="What is the capital of France?",
             [string]$Base="http://127.0.0.1:8000"){
  $h=@{}; if($Key){$h.Authorization="Bearer $Key"}
  $b=@{model=$Model;messages=@(@{role="user";content=$Content})}
  if($Provider){$b.provider=$Provider}
  Invoke-RestMethod -Uri "$Base/v1/chat/completions" -Method Post -Headers $h `
    -ContentType "application/json" -Body ($b|ConvertTo-Json -Depth 5)
}
```

Run the five checks:
```powershell
# 1) OpenRouter: MISS then HIT
Ask "<openrouter-key>" "openrouter" "<today-s-free-model-id>"
Ask "<openrouter-key>" "openrouter" "<today-s-free-model-id>"

# 2) Gemini: MISS then HIT (different user AND provider)
Ask "<gemini-key>" "gemini" "gemini-3.6-flash"
Ask "<gemini-key>" "gemini" "gemini-3.6-flash"

# 3) Keyless -> expect thrown error with status 401
try { Ask } catch { $_.Exception.Response.StatusCode.value__ }

# 4) Non-allowlisted base URL -> expect 400
try {
  Invoke-RestMethod -Uri "http://127.0.0.1:8000/v1/chat/completions" -Method Post `
    -Headers @{Authorization="Bearer <openrouter-key>"; "X-LLM-Base-URL"="https://attacker.example/v1"} `
    -ContentType "application/json" `
    -Body '{"model":"x","messages":[{"role":"user","content":"hi"}]}'
} catch { $_.Exception.Response.StatusCode.value__ }

# 5) Inspect scoping
(Invoke-RestMethod http://127.0.0.1:8000/cache/entries).entries |
  Select user_id, model_used, prompt_text
```
✅ **Pass =** check 1: OpenRouter MISS→HIT · check 2: Gemini MISS→HIT (its first
MISS proves answers never cross users or providers) · check 3 prints `401` ·
check 4 prints `400` · check 5 lists two rows with distinct `user_id` values.

🛑 Stop the server (Ctrl+C). Local validation complete — deploy with confidence.

---

## Phase C — Deploy to Render (~1 hr, first build ~10–15 min)

- [ ] **C1.** Render dashboard → **New +** → **Blueprint** → select this repo → it reads `render.yaml` → **Apply**
- [ ] **C2.** Watch **Events** until "live". First build pulls CPU torch + bakes the BGE model (~10–15 min; you have 500 build minutes/month).
- [ ] **C3.** Open `https://<your-service>.onrender.com/health` → expect `{"status": "ok", ...}`
- [ ] **C4.** Service → **Environment** tab → add:
  | Key | Value |
  |---|---|
  | `USER_ID_PEPPER` | output of `python -c "import secrets; print(secrets.token_hex(32))"` — **never rotate** |
  | `ADMIN_TOKEN` | another fresh 32-byte hex |
  | `MOCK_LLM` | `false` |
  Save → auto-redeploys (~3 min).
- [ ] **C5.** Verify lock-down (PowerShell — expect thrown status `401`):
  ```powershell
  try {
    Invoke-RestMethod -Uri "https://<your-service>.onrender.com/v1/chat/completions" `
      -Method Post -ContentType "application/json" `
      -Body '{"model":"x","messages":[{"role":"user","content":"hi"}]}'
  } catch { $_.Exception.Response.StatusCode.value__ }
  ```
  `/dashboard` should now demand the ADMIN_TOKEN too.

---

## Phase D — Real-provider verification on the PUBLIC URL (15 min)

The same isolation proof as B4, pointed at Render — proving the deployed
service scopes users correctly over the internet (four scripted checks below,
then the dashboard scoping glance).

Terminal 2 setup (server is already running on Render; nothing to start):
```powershell
function Ask([string]$Key,[string]$Provider,[string]$Model="gpt-3.5-turbo",
             [string]$Content="What is the capital of France?"){
  $Base = "https://<your-service>.onrender.com"   # ← paste your URL
  $h=@{}; if($Key){$h.Authorization="Bearer $Key"}
  $b=@{model=$Model;messages=@(@{role="user";content=$Content})}
  if($Provider){$b.provider=$Provider}
  Invoke-RestMethod -Uri "$Base/v1/chat/completions" -Method Post -Headers $h `
    -ContentType "application/json" -Body ($b|ConvertTo-Json -Depth 5)
}
```

> ⏳ First call may take **30–60 s** if the free instance was sleeping.

```powershell
# 1) Alice via OpenRouter: MISS -> HIT
Ask "<openrouter-key>" "openrouter" "<free-model-id>"
Ask "<openrouter-key>" "openrouter" "<free-model-id>"

# 2) Bob via Gemini, SAME question: MISS (never Alice's answer!) -> HIT
Ask "<gemini-key>" "gemini" "gemini-3.6-flash"
Ask "<gemini-key>" "gemini" "gemini-3.6-flash"

# 3) Keyless -> 401
try { Ask } catch { $_.Exception.Response.StatusCode.value__ }

# 4) Evil base URL -> 400
try {
  Invoke-RestMethod -Uri "https://<your-service>.onrender.com/v1/chat/completions" `
    -Method Post `
    -Headers @{Authorization="Bearer <openrouter-key>"; "X-LLM-Base-URL"="https://attacker.example/v1"} `
    -ContentType "application/json" `
    -Body '{"model":"x","messages":[{"role":"user","content":"hi"}]}'
} catch { $_.Exception.Response.StatusCode.value__ }
```

Browser: `/dashboard` (admin token when prompted) → "Tokens saved" card climbs
and the per-user table shows both users accumulating independently.
✅ All of the above = launch-ready; continue to Phase E/F.

---

## Phase E — Persistence decision (choose one)

| Option | Cost | What you get | How |
|---|---|---|---|
| **Free (start here)** | $0/mo | Cache/history reset on every deploy/restart; service sleeps after **15 min idle** → first visitor waits **30–60 s** | Do nothing |
| **Always-on + durable** | ≈ **$7.25/mo** (Starter compute $7 + disk $0.25/GB) | No spin-down; cache survives redeploys | Service → **Change Plan → Starter**, then **Disks → Add Disk** (`cache-disk`, mount `/var/data`, 1 GB) and add env `CACHE_DB_PATH=/var/data/cache.db`; schema migrates itself on boot |

---

## Phase F — Invite your 10–15 people

Send each person exactly three things:

```
URL:      https://<your-service>.onrender.com/v1/chat/completions
Auth:     Header "Authorization: Bearer <YOUR OWN OpenRouter-or-Gemini free key>"
Provider: Optional body field "provider": "openrouter" | "gemini"
          (or header X-LLM-Base-URL with the same values)
Rules:    Bring your own free key — the proxy never sees spend from anyone else,
          and nobody can read anyone else's cached answers.
          Heads-up: OpenRouter free keys = 50 req/day (1000 after a one-time
          $10 credit on YOUR account). Some free endpoints may log prompts.
```

---

## Phase G — Ongoing ops (light)

- [ ] **Weekly-ish:** glance at `/dashboard` + Security tab (CodeQL/pip-audit alerts appear here automatically)
- [ ] **Monthly:** merge Dependabot's grouped actions PR; close any stray pip PRs
- [ ] **If someone hits 429:** it's *their provider's* free cap — send them the OpenRouter $10-trick or Gemini tier notes above
- [ ] **Revisit only if needed:** rate limiting (abuse), MongoDB Atlas (scale), ANN index (>5k entries) — all documented as deliberate non-goals in TECHNICAL_DETAIL.md

---

## Reality sheet (verified Aug 2026)

| Constraint | Number |
|---|---|
| Render free instance | 512 MB RAM · 0.1 CPU · sleeps after **15 min idle** · cold start **30–60 s** · no disks |
| Render hours | 750 instance-hours/workspace/month (one sleeping service fits easily) |
| OpenRouter free | 20 RPM · **50 req/day** (<$10 lifetime credits) → 1,000/day after one-time $10 · roster rotates · some endpoints may log prompts |
| Gemini free | Indefinite, no card · Flash-class ≈ 10 RPM / low hundreds RPD per project · free-tier data may train Google products · **auth-key migration deadline Sept 2026** |

**Total launch cost: $0.** First meaningful upgrade, if ever: Render Starter $7/mo for always-on + persistence.

---

## 🔧 Troubleshooting quick reference

| Symptom | Cause | Fix |
|---|---|---|
| `422 json_invalid` on any request | Sent JSON through `curl.exe` on PowerShell (quotes stripped) | Use the `Ask` / `Invoke-RestMethod` pattern from this doc |
| `'make' is not recognized` | Windows has no make | Use the equivalents table at the top |
| First request hangs 30–60 s | Free instance was sleeping | Wait once; or upgrade to Starter |
| `401` with *provider*-flavored message (not BYOK text) | The upstream rejected that key | Key expired/typo — regenerate at provider |
| `429` from OpenRouter | That key hit its 50/day free cap | Wait for UTC midnight, or one-time $10 → 1,000/day. The proxy retries briefly (~1–2 s) then surfaces the 429; a provider `Retry-After` longer than ~30 s is surfaced immediately |
| Gemini `429 RESOURCE_EXHAUSTED` | Project-level free quota exhausted | Check AI Studio rate-limit view; try Flash-Lite or wait |
| Render build fails/timeout | Transient network during torch/model pull | **Manual Deploy → Deploy latest commit** to retry (build cache helps) |
| Cache/history gone after redeploy | Free-tier ephemeral disk | Expected — Phase E paid option fixes it |

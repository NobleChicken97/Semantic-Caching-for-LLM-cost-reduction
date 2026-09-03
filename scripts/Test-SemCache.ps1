<#Requires -Version 5.1
<#
.SYNOPSIS
  Adversarial black-box battery for the semantic-cache proxy (runs against the
  LIVE service; all assertions are behavioral, not metric-based).
.DESCRIPTION
  Every run uses a fresh $RunId suffix so prompts never collide with earlier
  runs or the demo cache. In MOCK mode response BODIES are placeholders, but
  every outcome below exercises real logic: hashing, embeddings, thresholding,
  model scoping, bypass, auth, validation, logging.
  Run: powershell -ExecutionPolicy Bypass -File scripts\Test-SemCache.ps1
  Side effects on prod: ~15 log rows + a few cache entries (TTL-expire).
.PARAMETER Base
  Proxy origin. Defaults to production.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\Test-SemCache.ps1
#>
param([string]$Base = "https://semcache.noblechicken.me")

$ErrorActionPreference = "Stop"
$script:pass = 0
$script:fail = 0
$RunId = Get-Date -Format "HHmmss"

function Ask([string]$prompt, [string]$model = "gpt-3.5-turbo", [bool]$bypass = $false) {
    $body = @{ model = $model; messages = @(@{ role = "user"; content = $prompt }) } |
        ConvertTo-Json -Depth 5 -Compress
    $headers = @{ "Content-Type" = "application/json" }
    if ($bypass) { $headers["X-Cache-Bypass"] = "true" }
    try {
        $r = Invoke-RestMethod -Method Post -Uri "$Base/v1/chat/completions" `
            -Headers $headers -Body $body
        return @{ ok = $true; meta = $r.cache_metadata; code = 200 }
    } catch {
        $code = [int]$_.Exception.Response.StatusCode
        return @{ ok = $false; meta = $null; code = $code }
    }
}

function Check([string]$name, $actual, $expected) {
    if ("$actual" -eq "$expected") {
        Write-Host "PASS  $name  [$actual]" -ForegroundColor Green
        $script:pass++
    } else {
        Write-Host "FAIL  $name  expected=$expected actual=$actual" -ForegroundColor Red
        $script:fail++
    }
}

function Observe([string]$name, $value) {
    Write-Host "INFO  $name  [$value] (behavioral note, not asserted)" -ForegroundColor Yellow
}

Write-Host "== T1 exact MISS then HIT =="
$p1 = "What eats deep-sea vents at run $RunId?"
Check "T1 fresh prompt is MISS" (Ask $p1).meta.outcome "MISS"
$r = Ask $p1
Check "T1 repeat is HIT" $r.meta.outcome "HIT"
Check "T1 exact similarity is 1.0" $r.meta.similarity_score 1

Write-Host "== T2 paraphrase HIT =="
$p1b = "Which creatures feed at deep-sea vents in run $RunId?"
$r = Ask $p1b
Check "T2 paraphrase is HIT" $r.meta.outcome "HIT"
$simOk = ($r.meta.similarity_score -ge 0.85) -and ($r.meta.similarity_score -lt 1.0)
Check "T2 similarity in [0.85, 1.0)" $simOk "True"

Write-Host "== T3 different intent is MISS =="
Check "T3 capital is MISS" (Ask "What is the capital of Japan ($RunId)?").meta.outcome "MISS"
Check "T3 population is MISS" (Ask "What is the population of Japan ($RunId)?").meta.outcome "MISS"

Write-Host "== T4 threshold boundary =="
Check "T4 arithmetic is MISS" (Ask "What is 2 + 2 ($RunId)?").meta.outcome "MISS"
$r = Ask "Calculate two plus two ($RunId)."
Check "T4 near-paraphrase is HIT" $r.meta.outcome "HIT"
Check "T4 near-paraphrase is not exact" ($r.meta.similarity_score -lt 1.0) "True"

Write-Host "== T5 model isolation =="
$p5 = "Name a deep-sea fish ($RunId)?"
Check "T5 model A is MISS" (Ask $p5 "gpt-3.5-turbo").meta.outcome "MISS"
Check "T5 same prompt other model is MISS" (Ask $p5 "gpt-4").meta.outcome "MISS"
Check "T5 model A repeats as HIT" (Ask $p5 "gpt-3.5-turbo").meta.outcome "HIT"

Write-Host "== T6 bypass never poisons =="
$p6 = "Describe hydrothermal vents ($RunId)."
Check "T6 first is MISS" (Ask $p6).meta.outcome "MISS"
Check "T6 bypass header is BYPASS" (Ask $p6 -bypass $true).meta.outcome "BYPASS"
Check "T6 post-bypass still HIT" (Ask $p6).meta.outcome "HIT"

Write-Host "== T7 admin gate =="
try {
    Invoke-RestMethod -Method Post -Uri "$Base/cache/purge" `
        -ContentType "application/json" -Body '{}' | Out-Null
    Check "T7 keyless purge is 401" "200" "401"
} catch {
    Check "T7 keyless purge is 401" ([int]$_.Exception.Response.StatusCode) 401
}

Write-Host "== T8/T9 validation =="
try {
    Invoke-WebRequest -Uri "$Base/v1/chat/completions" -UseBasicParsing | Out-Null
    Check "T8 GET is 405" "200" "405"
} catch {
    Check "T8 GET is 405" ([int]$_.Exception.Response.StatusCode) 405
}
try {
    Invoke-RestMethod -Method Post -Uri "$Base/v1/chat/completions" `
        -ContentType "application/json" -Body '{"model":"x"}' | Out-Null
    Check "T9 missing messages is 422" "200" "422"
} catch {
    Check "T9 missing messages is 422" ([int]$_.Exception.Response.StatusCode) 422
}
try {
    Invoke-RestMethod -Method Post -Uri "$Base/v1/chat/completions" `
        -ContentType "application/json" -Body 'not-json{{{' | Out-Null
    Check "T10 malformed JSON is 4xx" "200" "4xx"
} catch {
    $c = [int]$_.Exception.Response.StatusCode
    Check "T10 malformed JSON is 4xx" ($c -ge 400 -and $c -lt 500) "True"
}

Write-Host "== T11 metrics sanity =="
$m = Invoke-RestMethod "$Base/metrics"
$sane = ($m.total_requests -gt 0) -and ($m.hit_rate -ge 0) -and `
    ($m.hit_rate -le 1) -and ($m.per_user.Count -gt 0)
Check "T11 metrics sane" $sane "True"

Write-Host "== T12 logs mirror traffic =="
$logs = (Invoke-RestMethod "$Base/logs/recent?limit=3").logs
Check "T12 recent rows returned" ($logs.Count -gt 0) "True"
Check "T12 newest row has latency" ($logs[0].latency_ms -gt 0) "True"

Write-Host "== T13 TTL honesty =="
$e = (Invoke-RestMethod "$Base/cache/entries?q=deep-sea").entries | Select-Object -First 1
Check "T13 expires_at > created_at" ($e.expires_at -gt $e.created_at) "True"

Write-Host "== T14 edge observations =="
Observe "T14 empty prompt outcome" (Ask "").meta.outcome
Observe "T14 padded-case prompt outcome" (Ask "   WHAT EATS DEEP-SEA VENTS AT RUN $RunId?   ").meta.outcome

Write-Host ""
Write-Host "RESULT: $($script:pass) passed, $($script:fail) failed" -ForegroundColor $(if ($script:fail -eq 0) { "Green" } else { "Red" })
exit $(if ($script:fail -eq 0) { 0 } else { 1 })

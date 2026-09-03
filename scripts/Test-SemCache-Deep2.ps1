<#Requires -Version 5.1
<#
.SYNOPSIS
  Mega-battery v2: wide-variety + edge-case black-box tests for the live proxy.
.DESCRIPTION
  TOKEN DISCIPLINE v3 (hard-won across four confounded runs — read before
  touching prompts):
  - Prompts carry ZERO artificial tokens. No run-ids, no family suffixes.
    Any token shared across prompts (even a "(v123-bu)" stem) becomes a
    shared embedding token and inflates cross-prompt similarities, turning
    MISS verdicts into HITs. Same-template pairs are the only exception,
    and only where the test SEEKS template behavior (there: reported, and
    the shared template IS the subject under test).
  - Freshness across reruns comes from purging, never from decoration:
    with -AdminToken the script purges before EVERY group (fully
    deterministic reruns); without it, first-run-only validity (warned).
  - Exact repeats reuse identical variables. Paraphrase pairs are fixed
    strings. Concurrent external traffic during a run can still poison
    verdicts (detected once live via forensics) — do not click around
    mid-run.
  ASSERTED only where the contract is certain. Everything else is OBSERVED
  with similarity so surprises become evidence, not noise.
  Side effects on prod: log rows + cache entries (TTL-expire).
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\Test-SemCache-Deep2.ps1 -AdminToken (Read-Host "token")
#>
param([string]$Base = "https://semcache.noblechicken.me", [string]$AdminToken = "")

$ErrorActionPreference = "Stop"
$script:pass = 0
$script:fail = 0
$script:lastCode = $null

function AskFull([string]$prompt, [string]$model = "gpt-3.5-turbo", [hashtable]$extra = $null) {
    $msg = @(@{ role = "user"; content = $prompt })
    $b = @{ model = $model; messages = $msg }
    if ($extra -ne $null) { foreach ($k in $extra.Keys) { $b[$k] = $extra[$k] } }
    $body = $b | ConvertTo-Json -Depth 5 -Compress
    for ($attempt = 0; $attempt -lt 2; $attempt++) {
        try {
            $r = Invoke-RestMethod -Method Post -Uri "$Base/v1/chat/completions" `
                -ContentType "application/json" -Body $body
            $script:lastCode = 200
            return @{ ok = $true; meta = $r.cache_metadata; code = 200 }
        } catch {
            $resp = $_.Exception.Response
            # Transport failure (no HTTP response: DNS/TLS/reset) retries ONCE.
            # Real HTTP errors never retry (would hammer + mask product bugs).
            if ($resp -eq $null -and $attempt -eq 0) {
                Start-Sleep -Seconds 3
                continue
            }
            $script:lastCode = $(if ($resp -ne $null) { [int]$resp.StatusCode } else { 0 })
            return @{ ok = $false; meta = $null; code = $script:lastCode }
        }
    }
    return @{ ok = $false; meta = $null; code = 0 }
}

function Ask([string]$prompt) { return AskFull $prompt }

function Check([string]$name, $actual, $expected) {
    if ("$actual" -eq "$expected") {
        Write-Host "PASS  $name  [$actual]" -ForegroundColor Green
        $script:pass++
    } else {
        $detail = ""
        if ("$actual" -eq "" -and $script:lastCode -ne $null) { $detail = " (http=$($script:lastCode), 0=transport failure)" }
        Write-Host "FAIL  $name  expected=$expected actual=$actual$detail" -ForegroundColor Red
        $script:fail++
    }
}

function Observe([string]$name, $value) {
    Write-Host "INFO  $name  [$value]" -ForegroundColor Yellow
}

function CleanRoom([string]$why) {
    if ($AdminToken -eq "") {
        Write-Host "WARN  no -AdminToken: no purge before $why; first-run validity only" -ForegroundColor Yellow
        return
    }
    try {
        $pr = Invoke-RestMethod -Method Post -Uri "$Base/cache/purge" `
            -Headers @{ Authorization = "Bearer $AdminToken"; "Content-Type" = "application/json" } `
            -Body '{}'
        Write-Host "clean room before ${why}: purged $($pr.purged_count) entries"
    } catch {
        Write-Host "WARN  purge before $why failed, continuing dirty" -ForegroundColor Yellow
    }
}

CleanRoom "run"

Write-Host "== A multilingual (exact must HIT; paraphrase observed) =="
Check "A fr base MISS" (Ask "Quelle est la capitale de la France?").meta.outcome "MISS"
Check "A fr exact repeat HIT" (Ask "Quelle est la capitale de la France?").meta.outcome "HIT"
$r = Ask "Dis-moi la capitale de la France."
Observe "A fr paraphrase" "$($r.meta.outcome) sim=$($r.meta.similarity_score)"
Check "A de base MISS" (Ask "Was ist die Hauptstadt von Japan?").meta.outcome "MISS"
Check "A de exact repeat HIT" (Ask "Was ist die Hauptstadt von Japan?").meta.outcome "HIT"
$r = Ask "Nenne mir die Hauptstadt von Japan."
Observe "A de paraphrase" "$($r.meta.outcome) sim=$($r.meta.similarity_score)"

Write-Host "== B negations (observe: known-weak class) =="
CleanRoom "B"
Check "B base MISS" (Ask "Is coffee healthy?").meta.outcome "MISS"
$r = Ask "Is coffee unhealthy?"
Observe "B antonym" "$($r.meta.outcome) sim=$($r.meta.similarity_score)"
$r = Ask "Is coffee healthy, really?"
Observe "B softened repeat" "$($r.meta.outcome) sim=$($r.meta.similarity_score)"

Write-Host "== C numbers and dates (observe: single-token diffs) =="
CleanRoom "C"
Check "C base MISS" (Ask "It costs 5 dollars.").meta.outcome "MISS"
$r = Ask "It costs 50 dollars."
Observe "C amount swap" "$($r.meta.outcome) sim=$($r.meta.similarity_score)"
Check "C date MISS" (Ask "The meeting is on Monday.").meta.outcome "MISS"
$r = Ask "The meeting is on Tuesday."
Observe "C weekday swap" "$($r.meta.outcome) sim=$($r.meta.similarity_score)"

Write-Host "== D code pairs (labeled analog scores 0.8449: boundary exhibit) =="
CleanRoom "D"
Check "D py-add MISS" (Ask "def add(a, b): return a + b").meta.outcome "MISS"
$r = Ask "def multiply(a, b): return a * b"
Observe "D py-mul (want MISS per label)" "$($r.meta.outcome) sim=$($r.meta.similarity_score)"
$r = Ask "function add(a,b){return a+b}"
Observe "D js-same-task" "$($r.meta.outcome) sim=$($r.meta.similarity_score)"

Write-Host "== E hostile text (must store + serve exactly) =="
CleanRoom "E"
$x = "<script>alert(1)</script>"
Check "E xss MISS" (Ask $x).meta.outcome "MISS"
Check "E xss repeat HIT 1.0" ([double](Ask $x).meta.similarity_score -eq 1.0) "True"
$s = "'; DROP TABLE cache_entries; --"
Check "E sqli MISS" (Ask $s).meta.outcome "MISS"
Check "E sqli repeat HIT" (Ask $s).meta.outcome "HIT"

Write-Host "== F short phrases, emoji, unicode, case =="
CleanRoom "F"
Check "F emoji MISS" (Ask "Good luck launch day!").meta.outcome "MISS"
Check "F emoji repeat HIT" (Ask "Good luck launch day!").meta.outcome "HIT"
$er = Ask ":)"
Observe "F lone-emoji" "$($er.code) $($er.meta.outcome)"
Check "F padded MISS (true short-collapse test)" (Ask "   hello   world   ").meta.outcome "MISS"
Check "F padded upper HIT" (Ask "  HELLO   WORLD  ").meta.outcome "HIT"
Check "F accent MISS" (Ask "I love cafés in Paris.").meta.outcome "MISS"
$r = Ask "I love cafes in Paris."
Observe "F accent-stripped" "$($r.meta.outcome) sim=$($r.meta.similarity_score)"

Write-Host "== G roles and multi-turn shape (observe: role tags are identity) =="
CleanRoom "G"
$sys = @{ model = "gpt-3.5-turbo"; messages = @(
    @{ role = "system"; content = "Be brief." },
    @{ role = "user"; content = "What time is it?" }) } |
    ConvertTo-Json -Depth 5 -Compress
$r1 = Invoke-RestMethod -Method Post -Uri "$Base/v1/chat/completions" `
    -ContentType "application/json" -Body $sys
Observe "G system+user" "$($r1.cache_metadata.outcome)"
$r = Ask "What time is it?"
Observe "G user-only vs system+user" "$($r.meta.outcome) sim=$($r.meta.similarity_score)"
$turn = @{ model = "gpt-3.5-turbo"; messages = @(
    @{ role = "user"; content = "My dog is sick." },
    @{ role = "user"; content = "What should I feed him?" }) } |
    ConvertTo-Json -Depth 5 -Compress
$r2 = Invoke-RestMethod -Method Post -Uri "$Base/v1/chat/completions" `
    -ContentType "application/json" -Body $turn
Observe "G two-turn shape" "$($r2.cache_metadata.outcome)"

Write-Host "== H long single word (true attractor test, no shared tokens) =="
CleanRoom "H"
$w = ("a" * 400)
Check "H longword MISS" (Ask $w).meta.outcome "MISS"
Check "H longword repeat HIT" (Ask $w).meta.outcome "HIT"

Write-Host "== I invariance (documented: identity ignores these) =="
CleanRoom "I"
$ip = "Invariance probe."
Check "I base MISS" (Ask $ip).meta.outcome "MISS"
Check "I temperature ignored" (AskFull $ip "gpt-3.5-turbo" @{ temperature = 0; top_p = 0.5 }).meta.outcome "HIT"
Check "I provider ignored" (AskFull $ip "gpt-3.5-turbo" @{ provider = "openrouter" }).meta.outcome "HIT"
$mp = "Model probe."
Check "I empty-model first MISS" (AskFull $mp "").meta.outcome "MISS"
Check "I empty-model repeat exact HIT" ([double](AskFull $mp "").meta.similarity_score -eq 1.0) "True"

Write-Host "== J concurrency: 5 parallel identical fresh prompts, exactly one MISS =="
CleanRoom "J"
$jb = @{ model = "gpt-3.5-turbo"; messages = @(@{ role = "user"; content = "Concurrency probe." }) } |
    ConvertTo-Json -Depth 5 -Compress
Add-Type -AssemblyName System.Net.Http  # WinPS does not preload it (type-not-found otherwise)
$hc = New-Object System.Net.Http.HttpClient
$hc.Timeout = [timespan]::FromSeconds(60)
$tasks = @()
for ($i = 0; $i -lt 5; $i++) {
    $sc = New-Object System.Net.Http.StringContent($jb, [Text.Encoding]::UTF8, "application/json")
    $tasks += $hc.PostAsync("$Base/v1/chat/completions", $sc)
}
try {
    [void][System.Threading.Tasks.Task]::WaitAll($tasks)
    $outs = $tasks | ForEach-Object {
        (ConvertFrom-Json $_.Result.Content.ReadAsStringAsync().Result).cache_metadata.outcome
    }
} catch {
    $outs = @("TRANSPORT-FAULT")
    Observe "J transport fault (infra, not product)" $_.Exception.Message
}
$hc.Dispose()
$missCt = ($outs | Where-Object { $_ -eq "MISS" }).Count
Check "J single-flight (1 MISS + 4 HIT)" $missCt 1
Observe "J outcome spread" ($outs -join ",")

Write-Host "== K burst: 20 distinct prompts, template-sensitivity measurement =="
CleanRoom "K"
$topics = @(
    "bursts over lighthouses", "pickling cucumbers", "tuning a ukulele",
    "the fall of Constantinople", "migrating swallows", "sourdough starters",
    "black hole evaporation", "origami cranes", "the Silk Road", "composting",
    "aurora physics", "beekeeping basics", "the printing press", "tidal zones",
    "chess openings", "fermentation", "the Gold Rush", "knot tying",
    "volcano types", "night trains in Europe"
)
$recs = @()
$missAll = $true
for ($i = 0; $i -lt 20; $i++) {
    $t0 = Get-Date
    $r = Ask "Write two sentences about $($topics[$i])."
    $ms = ((Get-Date) - $t0).TotalMilliseconds
    $recs += [pscustomobject]@{ topic = $topics[$i]; ms = $ms; out = $r.meta.outcome; sim = $r.meta.similarity_score }
    if ($r.meta.outcome -ne "MISS") { $missAll = $false }
}
Check "K all 20 distinct MISS" $missAll "True"
$lats = $recs | ForEach-Object { $_.ms }
$hits = $recs | Where-Object { $_.out -ne "MISS" } | ForEach-Object { "$($_.topic)=$($_.out) $($_.sim)" }
if ($hits.Count -gt 0) { Observe "K non-MISS topics" ($hits -join " | ") }
$srt = $lats | Sort-Object
$avg = [math]::Round(($lats | Measure-Object -Average).Average, 1)
$p95 = [math]::Round($srt[[math]::Min(19, [int](0.95 * 20))], 1)
Observe "K MISS-path latency avg/p95 ms (sample, incl. mock upstream)" "$avg / $p95"
$slow = $recs | Sort-Object ms -Descending | Select-Object -First 3 |
    ForEach-Object { "$([math]::Round($_.ms))ms $($_.topic) [$($_.out)]" }
Observe "K 3 slowest" ($slow -join " | ")

Write-Host "== L reversed words (observe: order sensitivity) =="
CleanRoom "L"
Check "L base MISS" (Ask "Dog bites man.").meta.outcome "MISS"
$r = Ask "Man bites dog."
Observe "L word-order swap" "$($r.meta.outcome) sim=$($r.meta.similarity_score)"

Write-Host "== M entity swaps, suffix-free (veto must fire or threshold hold) =="
CleanRoom "M"
Check "M France seed MISS" (Ask "What is the capital of France?").meta.outcome "MISS"
foreach ($c in @("Finland", "Norway", "Japan")) {
    # NOTE: string-concat, never "of $c?" — PS 5.1 silently empties a bare
    # $var directly before "?" (proven by scripts/debug-expand.ps1).
    $q = "What is the capital of " + $c + "?"
    $r = Ask $q
    Check "M $c MISS" $r.meta.outcome "MISS"
    Observe "M $c sim" $r.meta.similarity_score
}
$r = Ask "What is the population of France?"
Check "M population MISS" $r.meta.outcome "MISS"
Observe "M population sim" $r.meta.similarity_score

Write-Host ""
Write-Host "STRICT: $($script:pass) passed, $($script:fail) failed" -ForegroundColor $(if ($script:fail -eq 0) { "Green" } else { "Red" })
Write-Host "INFO lines are evidence, not verdicts."
exit $(if ($script:fail -eq 0) { 0 } else { 1 })

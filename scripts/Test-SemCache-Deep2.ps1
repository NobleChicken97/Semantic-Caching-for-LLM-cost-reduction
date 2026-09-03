<#Requires -Version 5.1
<#
.SYNOPSIS
  Mega-battery v2: wide-variety + edge-case black-box tests for the live proxy.
.DESCRIPTION
  Complements Test-SemCache-Deep.ps1 (labeled set + spotlight + session).
  This pack probes: multilingual behavior, negations, numbers/dates, code
  pairs, hostile text (XSS/SQL), emoji, whitespace/unicode/case, roles,
  multi-turn shape, long words, model/temperature/provider invariance,
  concurrent coalescing (true parallelism via HttpClient tasks), and a
  20-prompt burst for latency evidence.
  ASSERTED only where the contract is certain (exact repeats, validation
  codes, documented invariance, single-MISS coalescing). Everything else is
  OBSERVED with similarity so surprises become evidence, not noise.
  Per-family suffixes keep runs collision-free; reruns are safe.
  Side effects on prod: log rows + cache entries (TTL-expire).
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\Test-SemCache-Deep2.ps1
#>
param([string]$Base = "https://semcache.noblechicken.me")

$ErrorActionPreference = "Stop"
$script:pass = 0
$script:fail = 0
$R = Get-Date -Format "HHmmss"
$G = @{
    fr = "v$R-fr"; de = "v$R-de"; neg = "v$R-ng"; num = "v$R-nu";
    code = "v$R-co"; xss = "v$R-xx"; emo = "v$R-em"; ws = "v$R-ws";
    role = "v$R-ro"; turn = "v$R-tu"; long = "v$R-lo"; inv = "v$R-iv";
    conc = "v$R-cc"; burst = "v$R-bu"; rev = "v$R-rv"; uni = "v$R-un"
}

function AskFull([string]$prompt, [string]$model = "gpt-3.5-turbo", [hashtable]$extra = $null) {
    $msg = @(@{ role = "user"; content = $prompt })
    $b = @{ model = $model; messages = $msg }
    if ($extra -ne $null) { foreach ($k in $extra.Keys) { $b[$k] = $extra[$k] } }
    $body = $b | ConvertTo-Json -Depth 5 -Compress
    try {
        $r = Invoke-RestMethod -Method Post -Uri "$Base/v1/chat/completions" `
            -ContentType "application/json" -Body $body
        return @{ ok = $true; meta = $r.cache_metadata; code = 200 }
    } catch {
        return @{ ok = $false; meta = $null; code = [int]$_.Exception.Response.StatusCode }
    }
}

function Ask([string]$prompt) { return (AskFull $prompt).meta }

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
    Write-Host "INFO  $name  [$value]" -ForegroundColor Yellow
}

Write-Host "== A multilingual (bge-small is English-centric: exact must HIT, paraphrase observed) =="
Check "A fr base MISS" (Ask "Quelle est la capitale de la France ($($G.fr))?").meta.outcome "MISS"
$r = Ask "Quelle est la capitale de la France ($($G.fr))?"
Check "A fr exact repeat HIT" $r.meta.outcome "HIT"
$r = Ask "Dis-moi la capitale de la France ($($G.fr))."
Observe "A fr paraphrase" "$($r.meta.outcome) sim=$($r.meta.similarity_score)"
Check "A de exact repeat MISS-then-HIT" (Ask "Was ist die Hauptstadt von Japan ($($G.de))?").meta.outcome "MISS"
$r = Ask "Was ist die Hauptstadt von Japan ($($G.de))?"
Check "A de exact repeat HIT" $r.meta.outcome "HIT"
$r = Ask "Nenne mir die Hauptstadt von Japan ($($G.de))."
Observe "A de paraphrase" "$($r.meta.outcome) sim=$($r.meta.similarity_score)"

Write-Host "== B negations / antonyms (observe: known-weak class) =="
Check "B base MISS" (Ask "Is coffee healthy ($($G.neg))?").meta.outcome "MISS"
$r = Ask "Is coffee unhealthy ($($G.neg))?"
Observe "B antonym" "$($r.meta.outcome) sim=$($r.meta.similarity_score)"
$r = Ask "Is coffee healthy, really ($($G.neg))?"
Observe "B softened repeat" "$($r.meta.outcome) sim=$($r.meta.similarity_score)"

Write-Host "== C numbers and dates (observe: single-token diffs) =="
Check "C base MISS" (Ask "It costs 5 dollars ($($G.num)).").meta.outcome "MISS"
$r = Ask "It costs 50 dollars ($($G.num))."
Observe "C amount swap" "$($r.meta.outcome) sim=$($r.meta.similarity_score)"
Check "C date MISS" (Ask "The meeting is on Monday ($($G.num)).").meta.outcome "MISS"
$r = Ask "The meeting is on Tuesday ($($G.num))."
Observe "C weekday swap" "$($r.meta.outcome) sim=$($r.meta.similarity_score)"

Write-Host "== D code pairs =="
Check "D py-add MISS" (Ask "def add(a, b): return a + b ($($G.code))").meta.outcome "MISS"
Check "D py-mul MISS (labeled analog)" (Ask "def multiply(a, b): return a * b ($($G.code))").meta.outcome "MISS"
$r = Ask "function add(a,b){return a+b} ($($G.code))"
Observe "D js-same-task" "$($r.meta.outcome) sim=$($r.meta.similarity_score)"

Write-Host "== E hostile text (must store + serve exactly; display escaping is dashboard-side) =="
$x = "<script>alert(1)</script> ($($G.xss))"
Check "E xss MISS" (Ask $x).meta.outcome "MISS"
Check "E xss repeat HIT 1.0" ([double](Ask $x).meta.similarity_score -eq 1.0) "True"
$s = "'; DROP TABLE cache_entries; -- ($($G.xss))"
Check "E sqli MISS" (Ask $s).meta.outcome "MISS"
Check "E sqli repeat HIT" (Ask $s).meta.outcome "HIT"

Write-Host "== F emoji / whitespace / unicode / case =="
Check "F emoji MISS" (Ask "Good luck launch day ($($G.emo))!").meta.outcome "MISS"
Check "F emoji repeat HIT" (Ask "Good luck launch day ($($G.emo))!").meta.outcome "HIT"
$er = Ask ":) ($($G.emo))"
Observe "F lone-emoji" $er.meta.outcome
Check "F padded lower HIT" (Ask "   hello   world ($($G.ws))   ").meta.outcome "MISS"
Check "F padded upper HIT" (Ask "  HELLO   WORLD ($($G.ws))  ").meta.outcome "HIT"
Check "F accent MISS" (Ask "I love cafés in Paris ($($G.uni)).").meta.outcome "MISS"
$r = Ask "I love cafes in Paris ($($G.uni))."
Observe "F accent-stripped" "$($r.meta.outcome) sim=$($r.meta.similarity_score)"

Write-Host "== G roles and multi-turn shape (observe: role tags are identity) =="
$sys = @{ model = "gpt-3.5-turbo"; messages = @(
    @{ role = "system"; content = "Be brief." },
    @{ role = "user"; content = "What time is it ($($G.role))?" }) } |
    ConvertTo-Json -Depth 5 -Compress
$r1 = Invoke-RestMethod -Method Post -Uri "$Base/v1/chat/completions" `
    -ContentType "application/json" -Body $sys
Observe "G system+user" "$($r1.cache_metadata.outcome)"
$r = Ask "What time is it ($($G.role))?"
Observe "G user-only vs system+user" "$($r.meta.outcome) sim=$($r.meta.similarity_score)"
$turn = @{ model = "gpt-3.5-turbo"; messages = @(
    @{ role = "user"; content = "My dog is sick ($($G.turn))." },
    @{ role = "user"; content = "What should I feed him?" }) } |
    ConvertTo-Json -Depth 5 -Compress
$r2 = Invoke-RestMethod -Method Post -Uri "$Base/v1/chat/completions" `
    -ContentType "application/json" -Body $turn
Observe "G two-turn shape" "$($r2.cache_metadata.outcome)"

Write-Host "== H long single word =="
$w = ("a" * 400) + " ($($G.long))"
Check "H longword MISS" (Ask $w).meta.outcome "MISS"
Check "H longword repeat HIT" (Ask $w).meta.outcome "HIT"

Write-Host "== I invariance (documented: identity ignores these) =="
$ip = "Invariance probe ($($G.inv))."
Check "I base MISS" (Ask $ip).meta.outcome "MISS"
Check "I temperature ignored" (AskFull $ip "gpt-3.5-turbo" @{ temperature = 0; top_p = 0.5 }).meta.outcome "HIT"
Check "I provider ignored" (AskFull $ip "gpt-3.5-turbo" @{ provider = "openrouter" }).meta.outcome "HIT"
$r = AskFull "Model probe ($($G.inv))." ""
Observe "I empty-string model" "$($r.code) $($r.meta.outcome)"

Write-Host "== J concurrency: 5 parallel identical fresh prompts, exactly one MISS =="
$jp = "Concurrency probe ($($G.conc))."
$jb = @{ model = "gpt-3.5-turbo"; messages = @(@{ role = "user"; content = $jp }) } |
    ConvertTo-Json -Depth 5 -Compress
$hc = New-Object System.Net.Http.HttpClient
$hc.Timeout = [timespan]::FromSeconds(60)
$tasks = @()
for ($i = 0; $i -lt 5; $i++) {
    $sc = New-Object System.Net.Http.StringContent($jb, [Text.Encoding]::UTF8, "application/json")
    $tasks += $hc.PostAsync("$Base/v1/chat/completions", $sc)
}
[void][System.Threading.Tasks.Task]::WaitAll($tasks)
$outs = $tasks | ForEach-Object {
    (ConvertFrom-Json $_.Result.Content.ReadAsStringAsync().Result).cache_metadata.outcome
}
$hc.Dispose()
$missCt = ($outs | Where-Object { $_ -eq "MISS" }).Count
Check "J single-flight (1 MISS + 4 HIT)" $missCt 1
Observe "J outcome spread" ($outs -join ",")

Write-Host "== K burst: 20 distinct prompts, latency evidence =="
$topics = @(
    "bursts over lighthouses", "pickling cucumbers", "tuning a ukulele",
    "the fall of Constantinople", "migrating swallows", "sourdough starters",
    "black hole evaporation", "origami cranes", "the Silk Road", "composting",
    "aurora physics", "beekeeping basics", "the printing press", "tidal zones",
    "chess openings", "fermentation", "the Gold Rush", "knot tying",
    "volcano types", "night trains in Europe"
)
$lats = @()
$missAll = $true
for ($i = 0; $i -lt 20; $i++) {
    $t0 = Get-Date
    $r = Ask "Write two sentences about $($topics[$i]) ($($G.burst))."
    $lats += ((Get-Date) - $t0).TotalMilliseconds
    if ($r.meta.outcome -ne "MISS") { $missAll = $false }
}
Check "K all 20 distinct MISS" $missAll "True"
$srt = $lats | Sort-Object
$avg = [math]::Round(($lats | Measure-Object -Average).Average, 1)
$p95 = [math]::Round($srt[[math]::Min(19, [int](0.95 * 20))], 1)
Observe "K MISS latency avg/p95 ms (O(n) scan evidence)" "$avg / $p95"

Write-Host "== L reversed words (observe: order sensitivity) =="
Check "L base MISS" (Ask "Dog bites man ($($G.rev)).").meta.outcome "MISS"
$r = Ask "Man bites dog ($($G.rev))."
Observe "L word-order swap" "$($r.meta.outcome) sim=$($r.meta.similarity_score)"

Write-Host ""
Write-Host "STRICT: $($script:pass) passed, $($script:fail) failed" -ForegroundColor $(if ($script:fail -eq 0) { "Green" } else { "Red" })
Write-Host "INFO lines are evidence, not verdicts: multilingual paraphrase MISS = English-centric"
Write-Host "embeddings (documented); antonym/number/date HITs = known near-duplicate residue class."
exit $(if ($script:fail -eq 0) { 0 } else { 1 })

import re
import sys

with open("src/proxy/static/index.html", encoding="utf-8") as f:
    h = f.read()

# 1. extract module JS for node --check
m = re.search(r'<script type="module">(.*?)</script>', h, re.DOTALL)
assert m, "module script not found"
with open(
    "C:\\Users\\ARPAN~1.ARP\\AppData\\Local\\Temp\\opencode\\dash-check.mjs",
    "w",
    encoding="utf-8",
) as f:
    f.write(m.group(1))
print("extracted", len(m.group(1)), "chars")

# 2. contract checks: every backend endpoint + auth + tabs + helpers present
need = [
    "/metrics",
    "/cache/entries",
    "/logs/recent",
    "/cache/purge",
    "/eval/threshold-sweep",
    "/eval/auto-tune",
    "ADMIN_TOKEN",
    "?token=",
    'data-tab="metrics"',
    'data-tab="cache"',
    'data-tab="sweep"',
    'data-tab="logs"',
    "esc(",
    "fmtAgo",
    "prefers-reduced-motion",
    "anime.esm.min.js",
    "chart.umd.min.js",
    "borderline",
    "should_match",
]
missing = [x for x in need if x not in h]
print("MISSING:", missing if missing else "none")
print("inline-onclick-left:", h.count("onclick="))
sys.exit(1 if missing else 0)

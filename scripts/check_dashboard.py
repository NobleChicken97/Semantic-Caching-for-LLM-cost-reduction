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
    "side-nav",
    "section-title",
    "live-text",
    "theme-toggle",
    "fonts.googleapis.com/css2?family=Inter",
    "76.8% 0.233 130.85",
    "tune-panel",
    "chart-trend",
    "m-speedup",
]
missing = [x for x in need if x not in h]
print("MISSING:", missing if missing else "none")
print("inline-onclick-left:", h.count("onclick="))
if missing:
    sys.exit(1)


# 3. contrast floor for the non-obvious text pairs (WCAG AA 4.5:1).
# Pairs are hand-picked hex approximations of the OKLCH tokens; oklch()
# pairs (near-black/near-white) pass trivially and need no check.
def _lum(rgb):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(rgb[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _ratio(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


pairs = {
    "hero-on-light (#3f6212/white)": ("#3f6212", "#ffffff"),
    "danger-on-light (#991b1b/#fee2e2)": ("#991b1b", "#fee2e2"),
    "amber-badge (#92400e/#fef3c7)": ("#92400e", "#fef3c7"),
    "flat-badge (#57575e/#ececea)": ("#57575e", "#ececea"),
}
bad = {k: round(_ratio(*v), 2) for k, v in pairs.items() if _ratio(*v) < 4.5}
print("CONTRAST:", bad if bad else "all >= 4.5:1")
sys.exit(1 if bad else 0)

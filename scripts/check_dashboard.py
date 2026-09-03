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
    'data-tab="overview"',
    'data-tab="cache"',
    'data-tab="sweep"',
    'data-tab="logs"',
    "gauge-val",
    "alert-strip",
    "alert-btn",
    "family=Archivo",
    "card-coral",
    "card-peri",
    "card-mint",
    "card-peach",
    "esc(",
    "fmtAgo",
    "prefers-reduced-motion",
    "anime.esm.min.js",
    "hero-tiles",
    "gauge-val",
    "trend-tip",
    "sweep-hover",
    "alert-strip",
    'id="ambient"',
    "theme-ico-moon",
    "navtext",
    "topBar",
    "arcPath",
    "borderline",
    "should_match",
    "side-nav",
    "section-title",
    "live-text",
    "theme-toggle",
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
    "coral-ink (#26120c/#f2664d)": ("#26120c", "#f2664d"),
    "coral-muted (#3a170b/#f2664d)": ("#3a170b", "#f2664d"),
    "peri-ink (#171839/#7b7ff2)": ("#171839", "#7b7ff2"),
    "peri-muted (#1d1e42/#7b7ff2)": ("#1d1e42", "#7b7ff2"),
    "mint-ink (#14332a/#cde9dc)": ("#14332a", "#cde9dc"),
    "mint-muted (#33604f/#cde9dc)": ("#33604f", "#cde9dc"),
    "peach-ink (#3a2410/#f4c69a)": ("#3a2410", "#f4c69a"),
    "peach-muted (#6e4a22/#f4c69a)": ("#6e4a22", "#f4c69a"),
}
bad = {k: round(_ratio(*v), 2) for k, v in pairs.items() if _ratio(*v) < 4.5}
print("CONTRAST:", bad if bad else "all >= 4.5:1")
sys.exit(1 if bad else 0)

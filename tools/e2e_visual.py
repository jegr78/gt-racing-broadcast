#!/usr/bin/env python3
"""Browser-free core of the visual acceptance run (tools/e2e.py --visual).

tools/visual-probe.js turns a rendered page into element facts (schema in
docs/superpowers/plans/2026-10-06-visual-acceptance-run.md); the rules here
turn facts into findings. Stdlib only, unit-tested by tests/test_e2e_visual.py."""
import collections
import html
import json
import re

Finding = collections.namedtuple("Finding", "rule selector detail")

# Relative luminance below which a surface reads as dark, roughly the mid-grey
# rgb(124, 124, 124); a UA-white control stands out against anything darker.
DARK_SURFACE_LUM = 0.2
# A 1 px shared edge between neighbouring rects is layout rounding, not an overlap.
OVERLAP_MIN_PX = 2
# Sub-pixel rounding differs between the CI runner's fonts and a producer machine's.
CLIP_TOLERANCE_PX = 2
# Overflow values that hide content, as opposed to e.g. "scroll" or "auto".
CLIPPING = ("hidden", "clip")


def blend(fg, bg):
    """*fg* [r, g, b, a] painted over the opaque colour *bg* (r, g, b)."""
    a = fg[3]
    return tuple(fg[i] * a + bg[i] * (1 - a) for i in range(3))


def effective_bg(chain):
    """Composite a background chain (element first, root last) over the white canvas."""
    rgb = (255, 255, 255)
    for layer in reversed(chain):
        rgb = blend(layer, rgb)
    return rgb


def luminance(rgb):
    """WCAG 2 relative luminance of an sRGB colour."""
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a, b):
    """WCAG 2 contrast ratio of two opaque colours."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def rule_overflow(facts):
    """Flag a page whose scroll_width exceeds its own viewport width."""
    w, sw = facts["viewport"]["w"], facts["scroll_width"]
    if sw > w + 1:
        return [Finding("overflow", "html", f"page is {sw} px wide in a {w} px viewport")]
    return []


def rule_unstyled_controls(facts):
    """Flag a form control (ua_key set) whose bg_raw is still the UA default while parent_bg composites to a dark surface."""
    out = []
    for el in facts["elements"]:
        ua = facts["ua"].get(el["ua_key"]) if el["ua_key"] else None
        if ua is None or el["bg_raw"] != ua:
            continue
        if luminance(effective_bg(el["parent_bg"])) < DARK_SURFACE_LUM:
            out.append(Finding("unstyled-control", el["sel"],
                               f"{el['tag']} keeps the browser default background {ua} on a dark surface"))
    return out


def _intersects(a, b):
    """True when rects *a* and *b* ([x, y, w, h]) overlap by at least `OVERLAP_MIN_PX` on both axes."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = min(ax + aw, bx + bw) - max(ax, bx)
    h = min(ay + ah, by + bh) - max(ay, by)
    return w >= OVERLAP_MIN_PX and h >= OVERLAP_MIN_PX


def _painted(el):
    """The element's rect minus what ancestor overflow clips (vis_rect), or rect when the probe gave none."""
    return el.get("vis_rect") or el["rect"]


def _never_clear(fixed, flow, facts):
    """True when no document scroll offset shows *flow* fully in the viewport and clear of the fixed element's band."""
    vh = facts["viewport"]["h"]
    max_s = max(0, facts.get("scroll_height", vh) - vh)
    fy, fh = fixed[1] - facts.get("scroll_y", 0), fixed[3]
    y, h = flow[1], flow[3]
    lo, hi = max(0, y + h - vh), min(max_s, y)
    if lo > hi:
        lo, hi = 0, max_s
    above = max(lo, y + h - fy) <= hi
    below = lo <= min(hi, y - fy - fh)
    return not (above or below)


def _overlaps(a, b, facts):
    """True when *a* and *b* overlap as the viewer sees them; a fixed box against in-flow content only when scrolling cannot separate them."""
    ra, rb = _painted(a), _painted(b)
    if bool(a.get("fixed")) == bool(b.get("fixed")):
        return _intersects(ra, rb)
    across = min(ra[0] + ra[2], rb[0] + rb[2]) - max(ra[0], rb[0])
    if across < OVERLAP_MIN_PX:
        return False
    fixed, flow = (ra, rb) if a.get("fixed") else (rb, ra)
    return _never_clear(fixed, flow, facts)


def rule_overlap(facts):
    """Flag interactive element pairs that overlap, excluding ancestor/descendant pairs via anc."""
    items = [el for el in facts["elements"] if el["interactive"]]
    out = []
    for n, a in enumerate(items):
        for b in items[n + 1:]:
            if a["i"] in b["anc"] or b["i"] in a["anc"]:
                continue
            if _overlaps(a, b, facts):
                out.append(Finding("overlap", a["sel"], f"overlaps {b['sel']}"))
    return out


def rule_clipped_text(facts):
    """Flag an element whose text content overflows its box without an ellipsis or a line-clamp."""
    out = []
    for el in facts["elements"]:
        if not el["has_text"]:
            continue
        if (el["overflow_x"] in CLIPPING and el["text_overflow"] != "ellipsis"
                and el["scroll_w"] > el["client_w"] + CLIP_TOLERANCE_PX):
            out.append(Finding("clipped-text", el["sel"],
                               f"content {el['scroll_w']} px wide in a {el['client_w']} px box"))
        elif (el["overflow_y"] in CLIPPING and not el["line_clamp"]
                and el["scroll_h"] > el["client_h"] + CLIP_TOLERANCE_PX):
            out.append(Finding("clipped-text", el["sel"],
                               f"content {el['scroll_h']} px high in a {el['client_h']} px box"))
    return out


def _is_large(el):
    """True for WCAG "large" text (>=24px, or >=18.66px bold), which gets the lower contrast threshold."""
    return el["font_size"] >= 24 or (el["font_size"] >= 18.66 and el["font_weight"] >= 700)


def rule_contrast(facts):
    """Flag text whose colour-on-background contrast ratio misses the WCAG AA threshold for its size."""
    out = []
    for el in facts["elements"]:
        if (not el["text"] or el["color"] is None or el["bg_image"] or el["disabled"]
                or el["opacity"] < 1):
            continue
        bg = effective_bg(el["bg_chain"])
        ratio = contrast_ratio(blend(el["color"], bg), bg)
        need = 3.0 if _is_large(el) else 4.5
        if ratio < need:
            out.append(Finding("contrast", el["sel"], f"{ratio:.2f}:1 < {need}:1"))
    return out


def rule_console(errors):
    """One finding per captured console error, detail truncated to 300 characters."""
    return [Finding("console", "console", msg[:300]) for msg in errors]


FACT_RULES = [rule_overflow, rule_clipped_text, rule_unstyled_controls, rule_overlap, rule_contrast]


def evaluate(facts, errors):
    """All findings for one rendered page, duplicates removed, order kept."""
    found = [f for rule in FACT_RULES for f in rule(facts)] + rule_console(errors)
    return list(dict.fromkeys(found))


# Every allowlist entry must name all four to be unambiguous about what it suppresses.
ALLOW_REQUIRED = ("surface", "rule", "selector", "reason")


def load_allowlist(path):
    """Read the allowlist JSON; every entry must be an object with rule, a non-empty reason, and surface, selector and optional detail as compilable regexes."""
    with open(path, encoding="utf-8") as fh:
        entries = json.load(fh)
    if not isinstance(entries, list):
        raise ValueError(f"{path}: top level must be a list")
    for n, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: entry {n} is not an object")
        missing = [k for k in ALLOW_REQUIRED if not str(entry.get(k, "")).strip()]
        if missing:
            raise ValueError(f"{path}: entry {n} lacks {', '.join(missing)}")
        for key in ("surface", "selector", "detail"):
            pattern = entry.get(key)
            if not pattern:
                continue
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"{path}: entry {n} has an invalid {key} regex: {exc}") from exc
    return entries


def _allows(entry, finding, surface, viewport):
    """True when one allowlist entry matches one finding for the given surface and viewport."""
    if entry["rule"] != finding.rule or not re.fullmatch(entry["surface"], surface):
        return False
    if entry.get("viewport") and entry["viewport"] != viewport:
        return False
    if not re.fullmatch(entry["selector"], finding.selector):
        return False
    return not entry.get("detail") or re.search(entry["detail"], finding.detail) is not None


def apply_allowlist(findings, entries, surface, viewport):
    """Drop allowlisted findings. Returns (kept findings, indices of the entries that matched)."""
    kept, used = [], set()
    for f in findings:
        hits = [n for n, e in enumerate(entries) if _allows(e, f, surface, viewport)]
        used.update(hits)
        if not hits:
            kept.append(f)
    return kept, used


# Desktop matches tools/e2e.py's existing --shots capture size; phone is a current iPhone CSS viewport.
VIEWPORTS = {"desktop": (1280, 800), "phone": (390, 844)}
# Gives in-flight CSS transitions/fade-ins time to finish after the ready expression turns true.
SETTLE_MS = 800

Surface = collections.namedtuple("Surface", "name base path viewports prep ready timeout_ms")
SurfaceResult = collections.namedtuple(
    "SurfaceResult", "surface viewport shot findings suppressed error")

# cockpit.html and race-control.html both seed #tally with this placeholder before data arrives.
TALLY_READY = ("(() => { const t = document.getElementById('tally');"
               " return !!t && t.textContent.trim() !== '…'; })()")
CC_VIEWS = ("home", "streams", "services", "profile", "settings", "preflight", "tools",
            "apps", "console", "logs", "report", "help", "wizard")
CC_READY = "document.readyState === 'complete'"
# control-center.html sets #pf-summary to "running..." while the checklist runs, then a final tally.
PREFLIGHT_READY = ("(() => { const s = document.getElementById('pf-summary');"
                   " return !!s && !s.textContent.startsWith('running'); })()")

SURFACES = [
    Surface("director-panel", "relay", "/panel", ("desktop",), None,
            "!!document.querySelector('#schedBody tr')", 15000),
    Surface("cockpit", "relay", "/cockpit?t={token}", ("desktop", "phone"), None,
            TALLY_READY, 15000),
    Surface("race-control", "relay", "/console/race-control?t={rc_token}",
            ("desktop", "phone"), None, TALLY_READY, 15000),
] + [
    # The preflight view runs real hardware/tool/port checks and needs a much longer timeout.
    Surface(f"cc-{view}", "ui", "/", ("desktop",), f"showView('{view}')",
            PREFLIGHT_READY if view == "preflight" else CC_READY,
            90000 if view == "preflight" else 15000)
    for view in CC_VIEWS
] + [
    # The primary buttons exist only in modals, which no view opens on its own.
    Surface("cc-quit-modal", "ui", "/", ("desktop",),
            "document.getElementById('quitmodal').showModal()",
            "!!document.querySelector('#quitmodal[open]')", 15000),
]


def surface_url(surface, urls):
    """Absolute URL of *surface*; *urls* holds the ui/relay base URLs and the two tokens."""
    return urls[surface.base] + surface.path.format(token=urls["token"], rc_token=urls["rc_token"])


def result_code(results):
    """1 when any surface has a finding or failed to render, else 0."""
    return 1 if any(r.findings or r.error for r in results) else 0


def summarize(results, unused_entries):
    """Plain-text pass/fail lines per surface view plus unused-allowlist warnings, for console output."""
    lines = []
    for r in results:
        mark = "FAIL" if (r.findings or r.error) else "PASS"
        tail = r.error or (f"{len(r.findings)} finding(s)" if r.findings else "")
        lines.append(f"  [{mark}] {r.surface} {r.viewport}" + (f": {tail}" if tail else ""))
        for f in r.findings:
            lines.append(f"      {f.rule} {f.selector}: {f.detail}")
    for e in unused_entries:
        lines.append(f"  [WARN] unused allowlist entry: {e['surface']} {e['rule']} {e['selector']}")
    bad = sum(1 for r in results if r.findings or r.error)
    lines.append(f"  {len(results) - bad} surface views clean, {bad} with findings")
    return "\n".join(lines)


_REPORT_CSS = """
:root { --bg:#f6f7f9; --ink:#14171c; --card:#fff; --line:#d8dce3; --bad:#b42318; --ok:#1a7f37; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0f1216; --ink:#e6e9ee; --card:#171b21; --line:#2a313b; --bad:#ff7b72; --ok:#56d364; } }
body { margin:0; padding:16px; background:var(--bg); color:var(--ink); font:14px/1.45 system-ui, sans-serif; }
section { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px; margin:0 0 16px; }
h2 { font-size:16px; margin:0 0 8px; } .FAIL { color:var(--bad); } .PASS { color:var(--ok); }
img { max-width:100%; border:1px solid var(--line); } code { font-size:12px; }
table { border-collapse:collapse; width:100%; } td { border-top:1px solid var(--line); padding:4px; vertical-align:top; }
"""


def render_report(results, unused_entries):
    """A self-contained HTML report: verdict, findings and screenshot per surface view."""
    esc = html.escape
    parts = ["<!doctype html><html lang=en><meta charset=utf-8>"
             "<meta name=viewport content='width=device-width, initial-scale=1'>"
             f"<title>Visual acceptance</title><style>{_REPORT_CSS}</style><body>",
             f"<h1>Visual acceptance</h1><pre>{esc(summarize(results, unused_entries))}</pre>"]
    for r in results:
        verdict = "FAIL" if (r.findings or r.error) else "PASS"
        parts.append(f"<section><h2><span class={verdict}>{verdict}</span> "
                     f"{esc(r.surface)} <small>{esc(r.viewport)}</small></h2>")
        if r.error:
            parts.append(f"<p class=FAIL>{esc(r.error)}</p>")
        if r.findings:
            rows = "".join(f"<tr><td>{esc(f.rule)}</td><td><code>{esc(f.selector)}</code></td>"
                           f"<td>{esc(f.detail)}</td></tr>" for f in r.findings)
            parts.append(f"<table>{rows}</table>")
        if r.suppressed:
            parts.append(f"<p>{r.suppressed} allowlisted finding(s) hidden</p>")
        if r.shot:
            parts.append(f'<img src="{esc(r.shot)}" alt="{esc(r.surface)} {esc(r.viewport)}" loading=lazy>')
        parts.append("</section>")
    if unused_entries:
        items = "".join(f"<li><code>{esc(e['surface'])} {esc(e['rule'])} {esc(e['selector'])}</code>"
                        f" {esc(e['reason'])}</li>" for e in unused_entries)
        parts.append(f"<section><h2>Unused allowlist entries</h2><ul>{items}</ul></section>")
    parts.append("</body></html>")
    return "\n".join(parts)

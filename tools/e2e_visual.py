#!/usr/bin/env python3
"""Browser-free core of the visual acceptance run (tools/e2e.py --visual).

tools/visual-probe.js turns a rendered page into element facts (schema in
docs/superpowers/plans/2026-10-06-visual-acceptance-run.md); the rules here
turn facts into findings. Stdlib only, unit-tested by tests/test_e2e_visual.py."""
import collections

Finding = collections.namedtuple("Finding", "rule selector detail")

# Relative luminance below which a surface reads as dark, roughly the mid-grey
# rgb(124, 124, 124); a UA-white control stands out against anything darker.
DARK_SURFACE_LUM = 0.2
# A 1 px shared edge between neighbouring rects is layout rounding, not an overlap.
OVERLAP_MIN_PX = 2


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


def rule_overlap(facts):
    """Flag interactive element pairs whose rects overlap, excluding ancestor/descendant pairs via anc."""
    items = [el for el in facts["elements"] if el["interactive"]]
    out = []
    for n, a in enumerate(items):
        for b in items[n + 1:]:
            if a["i"] in b["anc"] or b["i"] in a["anc"]:
                continue
            if _intersects(a["rect"], b["rect"]):
                out.append(Finding("overlap", a["sel"], f"overlaps {b['sel']}"))
    return out


def rule_console(errors):
    """One finding per captured console error, detail truncated to 300 characters."""
    return [Finding("console", "console", msg[:300]) for msg in errors]


FACT_RULES = [rule_overflow, rule_unstyled_controls, rule_overlap]


def evaluate(facts, errors):
    """All findings for one rendered page, duplicates removed, order kept."""
    found = [f for rule in FACT_RULES for f in rule(facts)] + rule_console(errors)
    return list(dict.fromkeys(found))

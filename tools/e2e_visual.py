#!/usr/bin/env python3
"""Browser-free core of the visual acceptance run (tools/e2e.py --visual).

tools/visual-probe.js turns a rendered page into element facts (schema in
docs/superpowers/plans/2026-10-06-visual-acceptance-run.md); the rules here
turn facts into findings. Stdlib only, unit-tested by tests/test_e2e_visual.py."""
import collections
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
    """Read the allowlist JSON; every entry needs surface, rule, selector and a non-empty reason."""
    with open(path, encoding="utf-8") as fh:
        entries = json.load(fh)
    if not isinstance(entries, list):
        raise ValueError(f"{path}: top level must be a list")
    for n, entry in enumerate(entries):
        missing = [k for k in ALLOW_REQUIRED if not str(entry.get(k, "")).strip()]
        if missing:
            raise ValueError(f"{path}: entry {n} lacks {', '.join(missing)}")
    return entries


def _allows(entry, finding, surface, viewport):
    """True when one allowlist entry matches one finding for the given surface and viewport."""
    if entry["surface"] != surface or entry["rule"] != finding.rule:
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

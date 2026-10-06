#!/usr/bin/env python3
"""Unit tests for the browser-free rules of the visual acceptance run (tools/e2e_visual.py)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import e2e_visual as v

DARK = [19, 23, 28, 1]          # director-panel --panel
WHITE_UA = "rgb(255, 255, 255)"


def _el(**kw):
    rec = {"i": 0, "sel": "#x", "tag": "div", "interactive": False, "text": "",
           "has_text": False, "rect": [0, 0, 100, 20], "client_w": 100, "scroll_w": 100,
           "client_h": 20, "scroll_h": 20, "overflow_x": "visible", "overflow_y": "visible",
           "text_overflow": "clip", "line_clamp": False, "color": [230, 230, 230, 1],
           "bg_chain": [DARK], "bg_image": False, "opacity": 1.0, "font_size": 14.0,
           "font_weight": 400, "disabled": False, "ua_key": None, "bg_raw": "rgba(0, 0, 0, 0)",
           "parent_bg": [DARK], "anc": []}
    rec.update(kw)
    return rec


def _facts(elements, w=1280, scroll_width=1280):
    return {"viewport": {"w": w, "h": 800}, "scroll_width": scroll_width,
            "ua": {"input": WHITE_UA, "select": WHITE_UA, "textarea": WHITE_UA,
                   "button": "rgb(239, 239, 239)"},
            "elements": elements}


def t_contrast_ratio_extremes():
    assert round(v.contrast_ratio((0, 0, 0), (255, 255, 255)), 2) == 21.0
    assert v.contrast_ratio((10, 10, 10), (10, 10, 10)) == 1.0


def t_effective_bg_composites_over_white():
    assert v.effective_bg([]) == (255, 255, 255), "no layer means the white canvas"
    assert v.effective_bg([[0, 0, 0, 0.5]]) == (127.5, 127.5, 127.5)
    assert v.effective_bg([[255, 0, 0, 1], [0, 0, 255, 1]]) == (255, 0, 0), \
        "the element's own opaque layer wins"


def t_overflow_flags_wide_page():
    assert v.rule_overflow(_facts([], w=390, scroll_width=390)) == []
    hits = v.rule_overflow(_facts([], w=390, scroll_width=512))
    assert [f.rule for f in hits] == ["overflow"], hits
    assert "512" in hits[0].detail


def t_unstyled_input_on_dark_panel_is_flagged():
    # The #397 case: a duration input left at the UA white on the dark Director Panel.
    el = _el(sel="#txDur", tag="input", interactive=True, ua_key="input",
             bg_raw=WHITE_UA, bg_chain=[[255, 255, 255, 1]])
    hits = v.rule_unstyled_controls(_facts([el]))
    assert [(f.rule, f.selector) for f in hits] == [("unstyled-control", "#txDur")], hits


def t_styled_input_passes():
    el = _el(tag="input", interactive=True, ua_key="input",
             bg_raw="rgb(23, 28, 34)", bg_chain=[[23, 28, 34, 1]])
    assert v.rule_unstyled_controls(_facts([el])) == []


def t_ua_input_on_light_surface_passes():
    el = _el(tag="input", interactive=True, ua_key="input", bg_raw=WHITE_UA,
             parent_bg=[[245, 245, 245, 1]])
    assert v.rule_unstyled_controls(_facts([el])) == [], "white on light is a design choice"


def t_overlap_flags_siblings_not_nesting():
    a = _el(i=0, sel="#a", interactive=True, rect=[0, 0, 50, 20])
    b = _el(i=1, sel="#b", interactive=True, rect=[40, 0, 50, 20])
    c = _el(i=2, sel="#c", interactive=True, rect=[5, 5, 10, 10], anc=[0])
    d = _el(i=3, sel="#d", interactive=True, rect=[51, 30, 10, 10])
    hits = v.rule_overlap(_facts([a, b, c, d]))
    pairs = sorted((f.selector, f.detail) for f in hits)
    assert pairs == [("#a", "overlaps #b")], pairs


def t_overlap_ignores_hairline_touch():
    a = _el(i=0, sel="#a", interactive=True, rect=[0, 0, 50, 20])
    b = _el(i=1, sel="#b", interactive=True, rect=[49, 0, 50, 20])
    assert v.rule_overlap(_facts([a, b])) == [], "a 1 px shared edge is not an overlap"


def t_console_errors_become_findings():
    hits = v.rule_console(["pageerror: TypeError: x is undefined"])
    assert [(f.rule, f.selector) for f in hits] == [("console", "console")], hits


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("PASS test_e2e_visual")

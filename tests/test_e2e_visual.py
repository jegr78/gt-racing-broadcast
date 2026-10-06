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


def t_clipped_text_without_ellipsis_is_flagged():
    el = _el(sel="#name", has_text=True, overflow_x="hidden", client_w=80, scroll_w=140)
    hits = v.rule_clipped_text(_facts([el]))
    assert [(f.rule, f.selector) for f in hits] == [("clipped-text", "#name")], hits


def t_ellipsis_and_scrollers_pass():
    ell = _el(has_text=True, overflow_x="hidden", client_w=80, scroll_w=140, text_overflow="ellipsis")
    scroller = _el(has_text=True, overflow_x="auto", client_w=80, scroll_w=140)
    subpixel = _el(has_text=True, overflow_x="hidden", client_w=80, scroll_w=81)
    clamp = _el(has_text=True, overflow_y="hidden", client_h=40, scroll_h=90, line_clamp=True)
    assert v.rule_clipped_text(_facts([ell, scroller, subpixel, clamp])) == []


def t_vertical_clip_is_flagged():
    el = _el(sel="#note", has_text=True, overflow_y="hidden", client_h=40, scroll_h=90)
    assert [f.rule for f in v.rule_clipped_text(_facts([el]))] == ["clipped-text"]


def t_low_contrast_text_is_flagged():
    el = _el(sel="#dim", text="ON AIR", color=[60, 66, 74, 1], bg_chain=[DARK])
    hits = v.rule_contrast(_facts([el]))
    assert [(f.rule, f.selector) for f in hits] == [("contrast", "#dim")], hits
    assert ":1" in hits[0].detail


def t_large_text_uses_the_3_to_1_threshold():
    grey = [118, 118, 118, 1]          # ~4.0:1 on DARK
    small = _el(text="x", color=grey, font_size=14.0)
    large = _el(text="x", color=grey, font_size=24.0)
    bold = _el(text="x", color=grey, font_size=19.0, font_weight=700)
    assert len(v.rule_contrast(_facts([small]))) == 1
    assert v.rule_contrast(_facts([large, bold])) == []


def t_contrast_skips_unknowable_cases():
    cases = [_el(text="x", color=[60, 66, 74, 1], bg_image=True),
             _el(text="x", color=[60, 66, 74, 1], disabled=True),
             _el(text="x", color=[60, 66, 74, 1], opacity=0.5),
             _el(text="x", color=None),
             _el(text="", color=[60, 66, 74, 1])]
    assert v.rule_contrast(_facts(cases)) == []


def t_allowlist_matches_surface_viewport_rule_selector():
    entries = [{"surface": "cockpit", "viewport": "phone", "rule": "contrast",
                "selector": "#dim", "reason": "placeholder dash"},
               {"surface": "cockpit", "rule": "console", "selector": "console",
                "detail": "favicon", "reason": "synthetic profile has no favicon"},
               {"surface": "cockpit", "rule": "overlap", "selector": "#never", "reason": "stale"}]
    found = [v.Finding("contrast", "#dim", "2.1:1 < 4.5:1"),
             v.Finding("console", "console", "console.error: GET /favicon.ico 404"),
             v.Finding("contrast", "#other", "2.1:1 < 4.5:1")]
    kept, used = v.apply_allowlist(found, entries, "cockpit", "phone")
    assert kept == [found[2]], kept
    assert used == {0, 1}, used
    kept, _ = v.apply_allowlist(found, entries, "cockpit", "desktop")
    assert found[0] in kept, "a viewport-bound entry must not match another viewport"


def t_allowlist_selector_is_a_full_regex():
    entries = [{"surface": "s", "rule": "contrast", "selector": r"#row-\d+", "reason": "r"}]
    kept, _ = v.apply_allowlist([v.Finding("contrast", "#row-12", "")], entries, "s", "desktop")
    assert kept == []
    kept, _ = v.apply_allowlist([v.Finding("contrast", "#row-12 > b", "")], entries, "s", "desktop")
    assert len(kept) == 1, "the selector must match in full"


def t_load_allowlist_requires_a_reason():
    import json, tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump([{"surface": "s", "rule": "contrast", "selector": "#a", "reason": ""}], fh)
    try:
        v.load_allowlist(fh.name)
        raise AssertionError("an entry without a reason must be rejected")
    except ValueError:
        pass
    finally:
        os.unlink(fh.name)


def t_shipped_allowlist_loads():
    path = os.path.join(os.path.dirname(__file__), "..", "tools", "visual-allowlist.json")
    assert isinstance(v.load_allowlist(path), list)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("PASS test_e2e_visual")

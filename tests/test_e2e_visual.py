#!/usr/bin/env python3
"""Unit tests for the browser-free rules of the visual acceptance run (tools/e2e_visual.py)."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "scripts"))
import e2e_visual as v
import gt7_tracks

DARK = [19, 23, 28, 1]          # director-panel --panel
WHITE_UA = "rgb(255, 255, 255)"


def _el(**kw):
    """Build one visible-element fact record with dark-panel defaults, overridden by kw."""
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
    """Build a desktop-viewport facts dict wrapping *elements* for the rule functions."""
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
    el = _el(sel="#txDur", tag="input", interactive=True, ua_key="input",
             bg_raw=WHITE_UA, bg_chain=[[255, 255, 255, 1]])
    hits = v.rule_unstyled_controls(_facts([el]))
    assert [(f.rule, f.selector) for f in hits] == [("unstyled-control", "#txDur")], \
        "a duration input left at the UA white background on a dark panel must flag as unstyled"


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


def t_overlap_uses_the_visible_rect_of_a_clipped_element():
    btn = _el(i=0, sel="#add", interactive=True, rect=[265, 727, 103, 34], vis_rect=[265, 727, 103, 28])
    head = _el(i=1, sel="#head", interactive=True, rect=[212, 756, 1068, 44], vis_rect=[212, 756, 1068, 44])
    assert v.rule_overlap(_facts([btn, head])) == [], "the clipped strip is not painted"
    del btn["vis_rect"]
    assert len(v.rule_overlap(_facts([btn, head]))) == 1, "without vis_rect the full rect still counts"


def _phone(elements, scroll_height):
    """Build 390x844 phone facts for *elements*, with scroll_height/scroll_y set for the fixed-bar overlap rules."""
    facts = _facts(elements, w=390, scroll_width=390)
    facts["viewport"]["h"] = 844
    facts["scroll_height"] = scroll_height
    facts["scroll_y"] = 0
    return facts


def t_overlap_ignores_a_fixed_bar_the_page_scrolls_clear():
    tab = _el(i=0, sel="#tab", interactive=True, fixed=True, rect=[0, 787, 98, 57])
    inp = _el(i=1, sel="#chatin", interactive=True, rect=[12, 787, 280, 44])
    assert v.rule_overlap(_phone([tab, inp], 986)) == [], "a 142 px scroll uncovers the input"


def t_overlap_flags_content_stuck_under_a_fixed_bar():
    tab = _el(i=0, sel="#tab", interactive=True, fixed=True, rect=[0, 787, 98, 57])
    inp = _el(i=1, sel="#chatin", interactive=True, rect=[12, 887, 280, 44])
    hits = v.rule_overlap(_phone([tab, inp], 931))
    assert [(f.selector, f.detail) for f in hits] == [("#tab", "overlaps #chatin")], \
        "without bottom padding the last input never scrolls clear of the bar"
    head = _el(i=0, sel="#head", interactive=True, fixed=True, rect=[0, 0, 390, 60])
    btn = _el(i=1, sel="#btn", interactive=True, rect=[10, 20, 100, 30])
    assert len(v.rule_overlap(_phone([head, btn], 2000))) == 1, \
        "content at the top of the document stays under a fixed header"


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


def t_allowlist_surface_is_a_full_regex():
    entries = [{"surface": "cc-.*", "rule": "contrast", "selector": "#ver", "reason": "r"}]
    f = v.Finding("contrast", "#ver", "3.57:1 < 4.5:1")
    assert v.apply_allowlist([f], entries, "cc-home", "desktop")[0] == []
    assert v.apply_allowlist([f], entries, "cockpit", "desktop")[0] == [f], "other surfaces stay judged"
    literal = [{"surface": "cockpit", "rule": "contrast", "selector": "#ver", "reason": "r"}]
    assert v.apply_allowlist([f], literal, "cockpit-x", "desktop")[0] == [f], "the surface must match in full"


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


def t_load_allowlist_rejects_a_non_object_entry():
    import json, tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(["#a"], fh)
    try:
        v.load_allowlist(fh.name)
        raise AssertionError("a non-object entry must be rejected")
    except ValueError:
        pass
    finally:
        os.unlink(fh.name)


def t_load_allowlist_rejects_an_invalid_regex():
    import json, tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump([{"surface": "s", "rule": "contrast", "selector": "#a(", "reason": "r"}], fh)
    try:
        v.load_allowlist(fh.name)
        raise AssertionError("an invalid selector regex must be rejected")
    except ValueError:
        pass
    finally:
        os.unlink(fh.name)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump([{"surface": "s", "rule": "contrast", "selector": "#a", "reason": "r", "detail": "("}], fh)
    try:
        v.load_allowlist(fh.name)
        raise AssertionError("an invalid detail regex must be rejected")
    except ValueError:
        pass
    finally:
        os.unlink(fh.name)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump([{"surface": "cc-(", "rule": "contrast", "selector": "#a", "reason": "r"}], fh)
    try:
        v.load_allowlist(fh.name)
        raise AssertionError("an invalid surface regex must be rejected")
    except ValueError:
        pass
    finally:
        os.unlink(fh.name)


def t_shipped_allowlist_loads():
    path = os.path.join(os.path.dirname(__file__), "..", "tools", "visual-allowlist.json")
    assert isinstance(v.load_allowlist(path), list)


def t_surfaces_cover_all_four_uis():
    names = {s.name for s in v.SURFACES}
    for want in ("director-panel", "cockpit", "race-control"):
        assert want in names, want
    assert {f"cc-{view}" for view in v.CC_VIEWS} <= names
    assert len(v.CC_VIEWS) == 13, v.CC_VIEWS
    assert "cc-quit-modal" in names, "the primary buttons only live in modals, so one modal must be a surface"
    phone = {s.name for s in v.SURFACES if "phone" in s.viewports}
    assert phone == {"cockpit", "race-control"}, phone
    assert len({s.name for s in v.SURFACES}) == len(v.SURFACES), "surface names must be unique"


def t_surface_url_fills_tokens():
    urls = {"ui": "http://127.0.0.1:1", "ui_pov": "http://127.0.0.1:3",
            "relay": "http://127.0.0.1:2", "token": "T", "rc_token": "R"}
    by = {s.name: s for s in v.SURFACES}
    assert v.surface_url(by["race-control"], urls) == "http://127.0.0.1:2/console/race-control?t=R"
    assert v.surface_url(by["cockpit"], urls) == "http://127.0.0.1:2/cockpit?t=T"
    assert v.surface_url(by["cc-home"], urls) == "http://127.0.0.1:1/"
    assert v.surface_url(by["cc-telemetry"], urls) == "http://127.0.0.1:3/"


def t_telemetry_surface_uses_the_solo_pov_control_center():
    tel = [s for s in v.SURFACES if s.name == "cc-telemetry"]
    assert len(tel) == 1 and tel[0].base == "ui_pov", tel
    assert "telemetry" not in v.CC_VIEWS, "the main Control Center has no solo POV profile"
    assert [s.name for s in v.SURFACES if s.base == "ui_pov"] == ["cc-telemetry"], \
        "only the Telemetry view may render against the solo POV Control Center"
    for sel in ("#tm-laps .tmitem", "#tm-charts path.tm-a", "#tm-sectors tbody tr", "#tm-err"):
        assert sel in tel[0].ready, f"the ready check must wait for {sel}"


def t_demo_learned_track_is_recognised():
    row = v.demo_track_row()
    assert row["official_id"] == v.DEMO_TRACK_ID
    assert 1500 < row["length_m"] < 3000, row["length_m"]
    with tempfile.TemporaryDirectory() as d:
        learned = os.path.join(d, "learned-tracks.json")
        with open(learned, "w", encoding="utf-8") as fh:
            json.dump(v.demo_learned_tracks(row), fh)
        db = gt7_tracks.TrackDB(os.path.join(d, "none.json"), os.path.join(d, "none.json"), learned)
        assert db.name(v.DEMO_TRACK_ID)["track"] == "Demo Circuit", "the learned line names the track"
        assert abs(db.line_length(v.DEMO_TRACK_ID) - row["length_m"]) < 1, \
            "the learned line is usable without a downloaded signatures.json"
        assert not db.has_downloaded_line(v.DEMO_TRACK_ID)


def t_report_shows_findings_escaped_and_verdicts():
    ok = v.SurfaceResult("cockpit", "phone", "cockpit-phone.png", [], 1, None)
    bad = v.SurfaceResult("director-panel", "desktop", "director-panel-desktop.png",
                          [v.Finding("contrast", "div > b<i>", "2.0:1 < 4.5:1")], 0, None)
    broken = v.SurfaceResult("race-control", "desktop", None, [], 0, "TimeoutError: ready")
    html = v.render_report([ok, bad, broken], [{"surface": "x", "rule": "overlap",
                                                 "selector": "#gone", "reason": "old"}])
    assert 'src="cockpit-phone.png"' in html
    assert "div &gt; b&lt;i&gt;" in html and "b<i>" not in html, "selectors must be escaped"
    assert html.count("FAIL") >= 2 and "PASS" in html
    assert "TimeoutError" in html and "#gone" in html, "errors and unused allowlist entries must show"


def t_result_code_and_summary():
    ok = v.SurfaceResult("a", "desktop", "a.png", [], 0, None)
    bad = v.SurfaceResult("b", "desktop", "b.png", [v.Finding("overflow", "html", "")], 0, None)
    assert v.result_code([ok]) == 0
    assert v.result_code([ok, bad]) == 1
    assert v.result_code([v.SurfaceResult("c", "phone", None, [], 0, "boom")]) == 1
    text = v.summarize([ok, bad], [])
    assert "[PASS] a desktop" in text and "[FAIL] b desktop" in text, text


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("PASS test_e2e_visual")

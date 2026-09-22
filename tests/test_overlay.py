#!/usr/bin/env python3
"""Stdlib unit checks for the per-profile overlay CSS/font helpers.
Run: python3 tests/test_overlay.py"""
import importlib.util, os, re, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "feeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
feeds = importlib.util.module_from_spec(spec); spec.loader.exec_module(feeds)

sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import overlay_build as ob


def _mkoverlay(tmp, hud_css=None, timer_css=None, fonts=None):
    od = os.path.join(tmp, "overlay")
    os.makedirs(os.path.join(od, "fonts"), exist_ok=True)
    if hud_css is not None:
        with open(os.path.join(od, "hud.css"), "w", encoding="utf-8") as f: f.write(hud_css)
    if timer_css is not None:
        with open(os.path.join(od, "timer.css"), "w", encoding="utf-8") as f: f.write(timer_css)
    for name, data in (fonts or {}).items():
        with open(os.path.join(od, "fonts", name), "wb") as f: f.write(data)
    return od

def t_splitscreen_page_wires_data_and_override():
    import os
    path = os.path.join(ROOT, "src", "obs", "splitscreen.html")
    assert os.path.exists(path), "src/obs/splitscreen.html missing"
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    assert "/splitscreen/data" in html          # polls the relay state
    assert "/splitscreen/override.css" in html  # per-league override link
    assert 'id="split-left"' in html and 'id="split-right"' in html


def t_splitscreen_is_an_overlay_page():
    assert "splitscreen" in feeds.OVERLAY_PAGES


def t_read_overlay_css_splitscreen_present():
    import tempfile, os
    with tempfile.TemporaryDirectory() as od:
        with open(os.path.join(od, "splitscreen.css"), "w") as fh:
            fh.write("#split-left{color:#fff}")
        assert feeds.read_overlay_css(od, "splitscreen") == b"#split-left{color:#fff}"


def t_read_overlay_css_present():
    with tempfile.TemporaryDirectory() as tmp:
        od = _mkoverlay(tmp, hud_css="#stint{left:10px}")
        assert feeds.read_overlay_css(od, "hud") == b"#stint{left:10px}"

def t_read_overlay_css_timer_is_now_unknown():
    # the timer page is merged into the HUD, so "timer" is no longer an overlay page
    with tempfile.TemporaryDirectory() as tmp:
        od = _mkoverlay(tmp, hud_css="#stint{left:10px}")
        assert feeds.read_overlay_css(od, "timer") == b""

def t_read_overlay_css_absent_is_empty():
    with tempfile.TemporaryDirectory() as tmp:
        od = _mkoverlay(tmp)  # no hud.css
        assert feeds.read_overlay_css(od, "hud") == b""

def t_read_overlay_css_no_dir_is_empty():
    assert feeds.read_overlay_css(None, "hud") == b""
    assert feeds.read_overlay_css("", "timer") == b""

def t_read_overlay_css_rejects_unknown_page():
    with tempfile.TemporaryDirectory() as tmp:
        od = _mkoverlay(tmp, hud_css="x")
        assert feeds.read_overlay_css(od, "../hud") == b""
        assert feeds.read_overlay_css(od, "panel") == b""

def t_resolve_overlay_font_ok():
    with tempfile.TemporaryDirectory() as tmp:
        od = _mkoverlay(tmp, fonts={"Title.woff2": b"OTTO"})
        hit = feeds.resolve_overlay_font(od, "Title.woff2")
        assert hit and hit[1] == "font/woff2"
        assert os.path.basename(hit[0]) == "Title.woff2"

def t_font_ctypes_out_is_identity_whitelist():
    # The handler re-derives the Content-Type header from this constant map
    # (defense vs. header injection, mirroring ASSET_CTYPES), so every ctype
    # resolve_overlay_font can return must map back to itself, or a valid font
    # would 404, and any unknown value must drop to None.
    for ctype in feeds.FONT_CTYPES.values():
        assert feeds.FONT_CTYPES_OUT.get(ctype) == ctype
    assert feeds.FONT_CTYPES_OUT.get("text/html; charset=utf-8") is None


def t_resolve_overlay_font_rejects_traversal_and_bad_ext():
    with tempfile.TemporaryDirectory() as tmp:
        od = _mkoverlay(tmp, fonts={"ok.ttf": b"x"})
        assert feeds.resolve_overlay_font(od, "../../etc/passwd") is None
        assert feeds.resolve_overlay_font(od, "ok.exe") is None
        assert feeds.resolve_overlay_font(od, "nope.woff2") is None
        assert feeds.resolve_overlay_font(None, "ok.ttf") is None
        assert feeds.resolve_overlay_font(od, ".woff2") is None


# resolve_preview_bg: the per-profile HUD-preview backdrop (overlay/preview-bg.*).
def t_resolve_preview_bg_present_jpg():
    with tempfile.TemporaryDirectory() as tmp:
        od = _mkoverlay(tmp)
        with open(os.path.join(od, "preview-bg.jpg"), "wb") as f: f.write(b"\xff\xd8\xff")
        hit = feeds.resolve_preview_bg(od)
        assert hit is not None and hit[0].endswith("preview-bg.jpg") and hit[1] == "image/jpeg"


def t_resolve_preview_bg_ext_precedence_jpg_over_png():
    with tempfile.TemporaryDirectory() as tmp:
        od = _mkoverlay(tmp)
        with open(os.path.join(od, "preview-bg.png"), "wb") as f: f.write(b"\x89PNG")
        with open(os.path.join(od, "preview-bg.jpg"), "wb") as f: f.write(b"\xff\xd8\xff")
        assert feeds.resolve_preview_bg(od)[1] == "image/jpeg"   # jpg first in PREVIEW_BG_EXTS


def t_resolve_preview_bg_png_when_only_png():
    with tempfile.TemporaryDirectory() as tmp:
        od = _mkoverlay(tmp)
        with open(os.path.join(od, "preview-bg.png"), "wb") as f: f.write(b"\x89PNG")
        assert feeds.resolve_preview_bg(od)[1] == "image/png"


def t_resolve_preview_bg_absent_or_no_dir_is_none():
    with tempfile.TemporaryDirectory() as tmp:
        assert feeds.resolve_preview_bg(_mkoverlay(tmp)) is None   # no per-profile, no assets
    assert feeds.resolve_preview_bg(None) is None


def t_resolve_preview_bg_falls_back_to_shared_default():
    # No per-profile override -> the shipped shared default in assets/ is used.
    with tempfile.TemporaryDirectory() as tmp:
        od = _mkoverlay(tmp)                      # no preview-bg here
        assets = os.path.join(tmp, "assets"); os.makedirs(assets)
        with open(os.path.join(assets, "preview-bg.jpg"), "wb") as f: f.write(b"\xff\xd8\xff")
        hit = feeds.resolve_preview_bg(od, assets)
        assert hit is not None and hit[0].endswith(os.path.join("assets", "preview-bg.jpg"))


def t_resolve_preview_bg_profile_overrides_shared_default():
    with tempfile.TemporaryDirectory() as tmp:
        od = _mkoverlay(tmp)
        with open(os.path.join(od, "preview-bg.png"), "wb") as f: f.write(b"\x89PNG")
        assets = os.path.join(tmp, "assets"); os.makedirs(assets)
        with open(os.path.join(assets, "preview-bg.jpg"), "wb") as f: f.write(b"\xff\xd8\xff")
        assert feeds.resolve_preview_bg(od, assets)[0].endswith(os.path.join("overlay", "preview-bg.png")), \
            "per-profile (overlay_dir) wins over the shared default"


def t_resolve_preview_bg_shared_default_only_when_no_overlay_dir():
    with tempfile.TemporaryDirectory() as tmp:
        assets = os.path.join(tmp, "assets"); os.makedirs(assets)
        with open(os.path.join(assets, "preview-bg.jpg"), "wb") as f: f.write(b"\xff\xd8\xff")
        assert feeds.resolve_preview_bg(None, assets)[1] == "image/jpeg"


def t_resolve_preview_bg_ctypes_are_asset_whitelisted():
    # The handler re-derives the header via ASSET_CTYPES[hit[1]], so every ctype
    # the resolver can return must be a key there (else a KeyError at request time).
    for _ext, ctype in feeds.PREVIEW_BG_EXTS:
        assert ctype in feeds.ASSET_CTYPES


# resolve_preview_frame: the Overlay.png broadcast frame from runtime graphics.
def t_resolve_preview_frame_present():
    with tempfile.TemporaryDirectory() as tmp:
        g = os.path.join(tmp, "graphics"); os.makedirs(g)
        with open(os.path.join(g, "Overlay.png"), "wb") as f: f.write(b"\x89PNG")
        hit = feeds.resolve_preview_frame(g)
        assert hit is not None and hit[1] == "image/png" and "image/png" in feeds.ASSET_CTYPES


def t_resolve_preview_frame_absent_or_no_dir_is_none():
    with tempfile.TemporaryDirectory() as tmp:
        assert feeds.resolve_preview_frame(os.path.join(tmp, "graphics")) is None
    assert feeds.resolve_preview_frame(None) is None


# overlay_build: the pure WYSIWYG compiler. (#114)

def t_ob_font_constants_match_relay():
    # Duplicated from the relay and pinned identical (anti-drift, like the
    # load_dotenv copies). If the relay tightens the whitelist, this must follow.
    assert ob.FONT_NAME_RE.pattern == feeds.FONT_NAME_RE.pattern
    assert ob.FONT_CTYPES == feeds.FONT_CTYPES
    assert set(ob.FONT_EXTS) == set(feeds.FONT_CTYPES.keys())


def t_ob_kind_props_constants():
    # Two kinds; text is a strict superset of box.
    assert set(ob.KIND_PROPS) == {"text", "box"}
    assert set(ob.KIND_BOX).issubset(set(ob.KIND_TEXT))
    # box has no text-only props
    for p in ("fontSize", "color", "align", "valign", "textTransform",
              "lineHeight", "letterSpacing", "textShadow", "fontFamily"):
        assert p not in ob.KIND_BOX
    # both kinds carry the shared box props
    for p in ("left", "top", "width", "height", "padding", "background",
              "borderRadius", "opacity", "rotation"):
        assert p in ob.KIND_BOX


def t_ob_extract_slots_kind_derives_props():
    html = ('<div id="a" data-edit="A" data-edit-kind="text"></div>'
            '<div id="b" data-edit="B" data-edit-kind="box"></div>'
            '<div id="c" data-edit="C" data-edit-kind="text" '
            'data-edit-props="teamNameMax,teamNameMin"></div>'
            '<div id="d" data-edit="D" data-edit-props="left,top"></div>')
    by = {s["id"]: s for s in ob.extract_slots(html)}
    assert by["a"]["props"] == list(ob.KIND_TEXT)
    assert by["b"]["props"] == list(ob.KIND_BOX)
    assert by["c"]["props"] == list(ob.KIND_TEXT) + ["teamNameMax", "teamNameMin"], \
        "extras are appended after the kind set, de-duplicated"
    # no kind + explicit props -> the explicit list (back-compat fallback)
    assert by["d"]["props"] == ["left", "top"]

def t_ob_extract_slots_from_real_hud():
    with open(os.path.join(ROOT, "src", "obs", "hud.html"), encoding="utf-8") as f:
        slots = ob.extract_slots(f.read())
    ids = [s["id"] for s in slots]
    # Each team is three independent slots (logo, number, name), plus the POV
    # placeholder box, the POV name label, the league-logo box (Solo Commentary
    # HUD) and the merged clock slot. (#136, #141, #130, #300)
    assert ids == ["stint", "session", "streamer", "round-top", "round-flag",
                   "round-country",
                   "team1-bar", "team1-logo", "team1-num", "team1-name",
                   "team1-brand", "team1-quali",
                   "team2-bar", "team2-logo", "team2-num", "team2-name",
                   "team2-brand", "team2-quali",
                   "team3-bar", "team3-logo", "team3-num", "team3-name",
                   "team3-brand", "team3-quali",
                   "race-control", "flag-status", "pov", "pov-name", "league-logo",
                   # Stream-chat slot (Solo Commentary HUD): a self-gating box
                   # rendering the read-only broadcast chat, hidden when
                   # /broadcast-chat/data is 404 or empty. (#300, #294)
                   "chat",
                   # Solo-mode telemetry block (#324): self-gating,
                   # hidden in endurance (no /telemetry/data there). The panel
                   # background and webcam frame are builder box slots too
                   # (position/size only; the webcam box also drives the real
                   # OBS "Solo Webcam" device transform, see
                   # OVERLAY_SLOT_OBS_SOURCES).
                   "tele-panel", "webcam",
                   "tele-tyres", "tele-trace", "tele-delta",
                   "tele-pred-lbl", "tele-pred",
                   "tele-fuel-lbl", "tele-fuel",
                   "tele-top-lbl", "tele-top",
                   "tele-clock",
                   "tele-avg-lbl", "tele-avg",
                   "tele-dist-lbl", "tele-dist",
                   # "tyres-capture" is a TOP-LEVEL slot (outside #tele) so it stays
                   # builder-editable in a Commentary profile that has no telemetry;
                   # it drives the "Solo Tyres/Fuel Capture" OBS device transform
                   # (#300)
                   "tyres-capture", "clock"]
    by_id = {s["id"]: s for s in slots}
    assert by_id["stint"]["label"] == "Stint banner"
    # default props (no data-edit-props) include the text set, not the team-only keys
    assert "fontSize" in by_id["stint"]["props"]
    assert "teamNameMax" not in by_id["stint"]["props"]
    # team name slot: the text kind plus the auto-fit extras (#136)
    assert by_id["team1-name"]["props"] == list(ob.KIND_TEXT) + [
        "teamNameMax", "teamNameMin"]
    # team number slot: the full text kind (standard props for all slots)
    assert by_id["team1-num"]["props"] == list(ob.KIND_TEXT)
    # team logo slots are the box kind PLUS align/valign so the operator can
    # left/right/top/bottom-position the brand mark inside its box (the img keeps
    # its aspect ratio and is placed by the box's flex alignment).
    assert by_id["team1-logo"]["props"] == list(ob.KIND_BOX) + ["align", "valign"]
    assert by_id["round-flag"]["props"] == list(ob.KIND_BOX), \
        "the round flag stays the plain box kind (already left-aligned by natural width)"
    # POV box: the box kind (still carries background/border via the kind)
    assert by_id["pov"]["props"] == list(ob.KIND_BOX)
    for p in ("background", "borderStyle", "borderColor", "borderWidth"):
        assert p in by_id["pov"]["props"], p
    assert by_id["pov"]["label"] == "POV box"
    assert by_id["pov-name"]["props"] == list(ob.KIND_TEXT), \
        "POV name label is a text slot (its old hand-curated set is now the kind)"


def t_ob_team_logo_alignment_compiles():
    # A team logo with align=left/valign=top compiles to the flex placement props;
    # this is what lets an operator push a wide brand mark to the left of its box
    # instead of the centered default.
    with open(os.path.join(ROOT, "src", "obs", "hud.html"), encoding="utf-8") as f:
        slots = ob.extract_slots(f.read())
    layout = ob.empty_layout("hud")
    layout["slots"]["team2-logo"] = {"align": "left", "valign": "top"}
    css = ob.compile_overlay_css(layout, slots)
    rule = re.search(r"#team2-logo\s*\{[^}]*\}", css).group(0)
    assert "justify-content: flex-start" in rule
    assert "align-items: flex-start" in rule


def t_ob_team_logo_img_keeps_aspect_for_alignment():
    # The logo <img> must NOT be forced to width:100%, or object-fit
    # centers it and the box's justify-content can never move it. It keeps its
    # natural aspect (width:auto, capped at the box) and the box centers by default
    # so the pre-existing look is preserved until an operator overrides align.
    with open(os.path.join(ROOT, "src", "obs", "hud.html"), encoding="utf-8") as f:
        style = ob.base_style(f.read())
    img_rule = re.search(r"\.team-logo img\s*\{[^}]*\}", style).group(0)
    assert "width: auto" in img_rule
    assert re.search(r"(?<!-)\bwidth:\s*100%", img_rule) is None  # not `width:100%` (max-width ok)
    assert "max-width: 100%" in img_rule
    box_rule = re.search(r"\.team-logo\s*\{[^}]*\}", style).group(0)
    assert "justify-content: center" in box_rule   # centered default preserved


def t_ob_hud_has_telemetry_slots():
    with open(os.path.join(ROOT, "src", "obs", "hud.html")) as f:
        ids = {s["id"] for s in ob.extract_slots(f.read())}
    assert {"tele-tyres", "tele-trace", "tele-delta", "tele-pred", "tele-fuel"} <= ids


def t_ob_hud_has_top_speed_slot():
    with open(os.path.join(ROOT, "src", "obs", "hud.html")) as f:
        ids = {s["id"] for s in ob.extract_slots(f.read())}
    assert "tele-top" in ids


def t_ob_hud_has_clock_slot():
    with open(os.path.join(ROOT, "src", "obs", "hud.html"), encoding="utf-8") as f:
        slots = ob.extract_slots(f.read())
    ids = [s["id"] for s in slots]
    assert "clock" in ids
    clock = next(s for s in slots if s["id"] == "clock")
    assert clock["label"] == "Clock"
    assert "left" in clock["props"] and "fontSize" in clock["props"]

def t_ob_hud_clock_base_is_finite_positionable():
    # The clock hugs its digits; it is not a full-canvas centered box (dragging
    # that moves nothing visibly). (#135)
    with open(os.path.join(ROOT, "src", "obs", "hud.html"), encoding="utf-8") as f:
        style = ob.base_style(f.read())
    clock_rule = re.search(r"#clock\s*\{[^}]*\}", style).group(0)
    assert "1920px" not in clock_rule, "clock must not span the full canvas width"

def t_ob_hud_pov_has_border_props_and_obs_position():
    with open(os.path.join(ROOT, "src", "obs", "hud.html"), encoding="utf-8") as f:
        html = f.read()
    pov = next(s for s in ob.extract_slots(html) if s["id"] == "pov")
    for p in ("background", "borderStyle", "borderColor", "borderWidth"):
        assert p in pov["props"], p
    pov_rule = re.search(r"#pov\s*\{[^}]*\}", ob.base_style(html)).group(0)
    # aligned to the OBS Feed POV box (pos 1496,644 bounds 384x216)
    assert "1496px" in pov_rule and "644px" in pov_rule
    assert "384px" in pov_rule and "216px" in pov_rule


def t_ob_base_style_and_body():
    html = ('<head><style>#x{left:1px}</style></head>'
            '<body>\n  <div id="x" data-edit="X"></div>\n<script>1</script></body>')
    assert ob.base_style(html) == "#x{left:1px}"
    body = ob.base_body(html)
    assert '<div id="x"' in body and "<script>" not in body


SLOTS = [{"id": "stint", "label": "Stint banner", "props": list(ob.DEFAULT_PROPS)},
         {"id": "team0", "label": "Team 1",
          "props": ["left", "top", "width", "height", "teamNameMax",
                    "teamNameMin", "fontFamily", "color"]}]


def t_ob_compile_px_and_text_props():
    css = ob.compile_overlay_css(
        {"version": 1, "page": "hud",
         "slots": {"stint": {"left": 800, "top": 30, "fontSize": 44,
                             "color": "#fff"}}}, SLOTS)
    assert "#stint {" in css
    assert "left: 800px" in css and "top: 30px" in css
    assert "font-size: 44px" in css and "color: #fff" in css


def t_ob_compile_align_maps_to_flex():
    css = ob.compile_overlay_css(
        {"slots": {"stint": {"align": "center"}}}, SLOTS)
    assert "justify-content: center" in css
    css = ob.compile_overlay_css({"slots": {"stint": {"align": "right"}}}, SLOTS)
    assert "justify-content: flex-end" in css


def t_ob_compile_team_autofit_vars():
    css = ob.compile_overlay_css(
        {"slots": {"team0": {"teamNameMax": 34, "teamNameMin": 18}}}, SLOTS)
    assert "--team-name-max: 34px" in css and "--team-name-min: 18px" in css


def t_ob_compile_respects_allowed_props():
    # fontSize is not in team0's allowed set -> never emitted even if present
    css = ob.compile_overlay_css({"slots": {"team0": {"fontSize": 50}}}, SLOTS)
    assert "font-size" not in css


def t_ob_compile_unknown_slot_ignored():
    css = ob.compile_overlay_css({"slots": {"bogus": {"left": 1}}}, SLOTS)
    assert "bogus" not in css and css.strip() == ""


def t_ob_compile_empty_layout_is_empty():
    assert ob.compile_overlay_css(ob.empty_layout("hud"), SLOTS).strip() == ""


def t_ob_compile_fonts_emit_font_face():
    css = ob.compile_overlay_css(
        {"slots": {}, "fonts": ["League.woff2"]}, SLOTS)
    assert '@font-face' in css and 'font-family: "League"' in css
    assert 'url(/overlay/fonts/League.woff2)' in css


def t_ob_compile_rejects_bad_font_name():
    css = ob.compile_overlay_css(
        {"slots": {}, "fonts": ["../evil.woff2", "ok.exe", "Good.ttf"]}, SLOTS)
    assert "evil" not in css and "ok.exe" not in css
    assert 'font-family: "Good"' in css


def t_ob_compile_customcss_last_and_verbatim():
    css = ob.compile_overlay_css(
        {"slots": {"stint": {"left": 10}}, "customCss": "/* mine */\n#stint{top:5px}"},
        SLOTS)
    assert css.rstrip().endswith("/* mine */\n#stint{top:5px}")
    assert "left: 10px" in css and css.index("left: 10px") < css.index("/* mine */")


def t_ob_compile_sanitizes_value_breakout():
    # A structured prop value must not be able to close the rule / inject CSS;
    # the customCss escape hatch is the only verbatim path.
    css = ob.compile_overlay_css(
        {"slots": {"stint": {"color": "red; } body{display:none"}}}, SLOTS)
    assert "display:none" not in css and "body{" not in css


def t_ob_migrate_imports_existing_css_into_customcss():
    layout = ob.migrate_layout("hud", "#stint{left:999px}/* hand-written */")
    assert layout["page"] == "hud" and layout["slots"] == {}
    assert layout["customCss"] == "#stint{left:999px}/* hand-written */"
    # and it compiles back out verbatim
    css = ob.compile_overlay_css(layout, SLOTS)
    assert "#stint{left:999px}" in css


def t_ob_font_family_is_file_stem():
    assert ob.font_family("League.woff2") == "League"
    assert ob.font_family("My-Font.ttf") == "My-Font"


def t_ob_google_fonts_curated_list():
    assert isinstance(ob.GOOGLE_FONTS, tuple) and len(ob.GOOGLE_FONTS) >= 10
    assert all(isinstance(n, str) and n for n in ob.GOOGLE_FONTS)
    assert "Oswald" in ob.GOOGLE_FONTS


def t_ob_google_font_filename_and_family():
    # spaces stripped, valid against the font-name whitelist, family = stem
    fn = ob.google_font_filename("Saira Condensed")
    assert fn == "SairaCondensed.woff2"
    assert ob.FONT_NAME_RE.match(fn) and fn.rsplit(".", 1)[1] in ob.FONT_EXTS
    assert ob.font_family(fn) == "SairaCondensed"
    assert ob.google_font_filename("Oswald") == "Oswald.woff2"


def t_ob_google_font_css_url():
    u = ob.google_font_css_url("Saira Condensed")
    assert u.startswith("https://fonts.googleapis.com/css2?family=Saira+Condensed")
    assert "wght@700" in u


def t_ob_font_cut_recognizes_suffixes():
    # base (no recognized suffix) -> None; cut suffixes parse to (family, style, weight)
    assert ob.font_cut("NunitoSans.woff2") is None
    assert ob.font_cut("My-Font.woff2") is None            # hyphen but not a cut word
    assert ob.font_cut("NunitoSans-Bold.woff2") == ("NunitoSans", "normal", "700")
    assert ob.font_cut("NunitoSans-Italic.woff2") == ("NunitoSans", "italic", "1 1000")
    assert ob.font_cut("NunitoSans-BoldItalic.woff2") == ("NunitoSans", "italic", "700")
    # case-insensitive on the suffix; must leave a non-empty base
    assert ob.font_cut("Oswald-bold.woff2") == ("Oswald", "normal", "700")
    assert ob.font_cut("-Bold.woff2") is None


def t_ob_compile_lone_font_unchanged():
    # A single file with no cut sibling stays the LEGACY descriptor-less face
    # (byte-identical to before, with no font-weight/font-style descriptors).
    css = ob.compile_overlay_css({"slots": {}, "fonts": ["League.woff2"]}, SLOTS)
    assert '@font-face { font-family: "League"; src: url(/overlay/fonts/League.woff2); }' in css
    assert "font-weight" not in css and "font-style" not in css


def t_ob_compile_groups_cut_siblings_with_descriptors():
    # base + italic sibling -> two faces under ONE family, with style/weight descriptors;
    # the base gets a weight RANGE so a variable roman renders true bold.
    css = ob.compile_overlay_css(
        {"slots": {}, "fonts": ["NunitoSans.woff2", "NunitoSans-Italic.woff2"]}, SLOTS)
    assert css.count('font-family: "NunitoSans"') == 2
    assert 'url(/overlay/fonts/NunitoSans.woff2)' in css
    assert 'url(/overlay/fonts/NunitoSans-Italic.woff2)' in css
    assert "font-style: italic" in css and "font-weight: 1 1000" in css
    # no stray "NunitoSans-Italic" family (sibling lives under the base family)
    assert 'font-family: "NunitoSans-Italic"' not in css


def t_ob_compile_groups_four_static_cuts():
    css = ob.compile_overlay_css(
        {"slots": {}, "fonts": ["Roboto.woff2", "Roboto-Bold.woff2",
                                "Roboto-Italic.woff2", "Roboto-BoldItalic.woff2"]}, SLOTS)
    assert css.count('font-family: "Roboto"') == 4
    assert "font-weight: 700" in css        # the -Bold / -BoldItalic exact weight
    assert "font-style: italic" in css
    assert 'font-family: "Roboto-Bold"' not in css


def t_ob_font_families_collapses_cut_siblings():
    fams = ob.font_families(["NunitoSans.woff2", "NunitoSans-Italic.woff2",
                             "NunitoSans-Bold.woff2", "League.woff2"])
    assert fams == ["League", "NunitoSans"]          # siblings collapsed, sorted
    assert ob.font_families(["Solo-Italic.woff2"]) == ["Solo-Italic"], \
        "a lone sibling with no base is kept (still selectable)"


def t_ob_google_font_cuts_url():
    u = ob.google_font_cuts_url("Nunito Sans")
    assert u.startswith("https://fonts.googleapis.com/css2?family=Nunito+Sans:")
    assert "ital,wght@0,400;0,700;1,400;1,700" in u


def t_ob_google_font_cut_filename():
    assert ob.google_font_cut_filename("Nunito Sans", "normal", "400") == "NunitoSans.woff2"
    assert ob.google_font_cut_filename("Nunito Sans", "normal", "700") == "NunitoSans-Bold.woff2"
    assert ob.google_font_cut_filename("Nunito Sans", "italic", "400") == "NunitoSans-Italic.woff2"
    assert ob.google_font_cut_filename("Nunito Sans", "italic", "700") == "NunitoSans-BoldItalic.woff2"


def t_ob_parse_google_font_cuts_latin_only():
    css = """
/* cyrillic */
@font-face { font-family:'X'; font-style:normal; font-weight:400;
  src: url(https://fonts.gstatic.com/s/x/cyr400.woff2) format('woff2');
  unicode-range: U+0301, U+0400-045F; }
/* latin */
@font-face { font-family:'X'; font-style:normal; font-weight:400;
  src: url(https://fonts.gstatic.com/s/x/lat400.woff2) format('woff2');
  unicode-range: U+0000-00FF, U+0131; }
/* latin */
@font-face { font-family:'X'; font-style:italic; font-weight:700;
  src: url(https://fonts.gstatic.com/s/x/lat700i.woff2) format('woff2');
  unicode-range: U+0000-00FF, U+0131; }
"""
    cuts = ob.parse_google_font_cuts(css)
    # only the latin blocks (U+0000-00FF) are kept, keyed by (style, weight)
    assert cuts[("normal", "400")].endswith("lat400.woff2")
    assert cuts[("italic", "700")].endswith("lat700i.woff2")
    assert ("normal", "400") in cuts and len(cuts) == 2     # the cyrillic block dropped


def t_ob_is_google_font_name():
    # valid families (incl. ones outside the curated catalog) pass
    for ok in ("Oswald", "Exo 2", "Roboto Condensed", "Big Shoulders Display", "A1"):
        assert ob.is_google_font_name(ok), ok
    # host/path/injection tricks and junk are rejected -> never fetched
    for bad in ("", " Oswald", "Oswald ", "../etc/passwd", "Evil/Font", "a@b",
                "x" * 60, "name\ninjection", None, 5):
        assert not ob.is_google_font_name(bad), bad


POVSLOTS = [{"id": "pov", "label": "POV box",
             "props": ["left", "top", "width", "height",
                       "background", "borderStyle", "borderColor", "borderWidth"]}]


def t_ob_compile_pov_border_and_background():
    css = ob.compile_overlay_css(
        {"slots": {"pov": {"background": "#0b0f1a", "borderStyle": "solid",
                           "borderColor": "#ff2a2a", "borderWidth": 4}}}, POVSLOTS)
    assert "#pov {" in css
    assert "background: #0b0f1a" in css
    assert "border-style: solid" in css
    assert "border-color: #ff2a2a" in css
    assert "border-width: 4px" in css


def t_ob_compile_border_width_is_px_gated():
    # borderWidth is numeric-only (px), like the other geometry props
    css = ob.compile_overlay_css({"slots": {"pov": {"borderWidth": "4; }#x{a:b"}}}, POVSLOTS)
    assert "border-width" not in css


def t_ob_compile_border_props_respect_allowed():
    # a text slot that does NOT allow border props must not emit them
    slots = [{"id": "stint", "label": "S", "props": list(ob.DEFAULT_PROPS)}]
    css = ob.compile_overlay_css({"slots": {"stint": {"borderStyle": "solid"}}}, slots)
    assert "border-style" not in css


def t_ob_sample_has_clock_in_hud_only():
    assert ob.SAMPLE["hud"].get("clock") == "1:23:45"
    assert "timer" not in ob.SAMPLE      # timer page is merged into hud


import json as _json

def t_obs_collection_has_no_timer_source():
    with open(os.path.join(ROOT, "src", "obs", "GT_Racing_Endurance.json"), encoding="utf-8") as f:
        col = _json.load(f)
    blob = _json.dumps(col)
    assert "HUD Race Timer" not in blob, "the separate timer source must be removed"
    assert "8088/timer" not in blob, "no scene item should point at the /timer page"
    assert "8088/hud" in blob   # the HUD page source remains

def t_obs_hud_overlay_renders_in_front():
    # OBS scene items: HIGHER index = front-most (verified by the base collection,
    # where HUD Overlay (text) sits AFTER the Overlay PNG frame so the text draws
    # on top of it). The HUD Overlay source must therefore render in FRONT of both
    # the Overlay frame AND Feed POV, so its #pov border frames the POV video.
    with open(os.path.join(ROOT, "src", "obs", "GT_Racing_Endurance.json"), encoding="utf-8") as f:
        col = _json.load(f)
    def items_of(src):
        return (src.get("settings") or {}).get("items") or []
    for src in col.get("sources", []):
        if src.get("id") not in ("scene", "group"):
            continue
        names = [it.get("name") for it in items_of(src)]
        hud = names.index("HUD Overlay") if "HUD Overlay" in names else None
        if hud is None:
            continue
        if "Overlay" in names:
            assert hud > names.index("Overlay"), src.get("name")  # text above frame
        if "Feed POV" in names:
            assert hud > names.index("Feed POV"), src.get("name")  # frame above PiP


def t_ob_sample_has_flag_and_brand_images():
    # The offline builder canvas must preview the image slots too, so SAMPLE
    # carries a flag key for the round flag and a brand key for each team logo,
    # both resolvable to bundled assets.
    h = ob.SAMPLE["hud"]
    flag = h.get("round-flag", {})
    assert isinstance(flag, dict) and flag.get("flag")
    for tid in ("team1-logo", "team2-logo", "team3-logo"):
        ent = h.get(tid, {})
        assert isinstance(ent, dict) and ent.get("brand"), tid
    # the sample keys must point at files that actually ship in src/assets/
    assert os.path.exists(os.path.join(ROOT, "src", "assets", "flags",
                                       flag["flag"] + ".svg"))
    for tid in ("team1-logo", "team2-logo", "team3-logo"):
        assert os.path.exists(os.path.join(ROOT, "src", "assets", "brands",
                                           h[tid]["brand"] + ".png")), tid
    # brand-name text slots preview with text
    for tid in ("team1-brand", "team2-brand", "team3-brand"):
        assert isinstance(h.get(tid), str) and h[tid], tid


def t_splitscreen_labels_source_in_collection_splitscreen_scene_only():
    import os, json
    with open(os.path.join(ROOT, "src", "obs", "GT_Racing_Endurance.json"),
              encoding="utf-8") as fh:
        d = json.load(fh)
    srcs = [s for s in d.get("sources", []) if s.get("name") == "Splitscreen Labels"]
    assert len(srcs) == 1, "exactly one Splitscreen Labels source expected"
    src = srcs[0]
    assert src.get("id") == "browser_source"
    assert src["settings"]["url"] == "http://127.0.0.1:8088/splitscreen"
    uuid = src["uuid"]
    def has_item(scene_name):
        for s in d["sources"]:
            if s.get("name") == scene_name and s.get("id") == "scene":
                return any(it.get("source_uuid") == uuid
                           for it in s["settings"]["items"])
        return False
    assert has_item("Splitscreen")
    assert not has_item("Stint")


SK = [{"id": "x", "label": "X", "props": list(ob.KIND_TEXT)}]


def _css_x(over):
    return ob.compile_overlay_css({"slots": {"x": over}}, SK)


def t_ob_compile_px_extras():
    css = _css_x({"padding": 6, "borderRadius": 4, "letterSpacing": 2})
    assert "padding: 6px" in css
    assert "border-radius: 4px" in css
    assert "letter-spacing: 2px" in css


def t_ob_compile_opacity_and_line_height():
    css = _css_x({"opacity": 0.5, "lineHeight": 1.2})
    assert "opacity: 0.5" in css and "line-height: 1.2" in css
    # out-of-range / non-number dropped
    assert "opacity" not in _css_x({"opacity": 2})
    assert "opacity" not in _css_x({"opacity": "x"})
    assert "line-height" not in _css_x({"lineHeight": 0})


def t_ob_compile_rotation():
    assert "transform: rotate(15deg)" in _css_x({"rotation": 15})
    assert "transform: rotate(-8deg)" in _css_x({"rotation": -8})
    assert "transform" not in _css_x({"rotation": 999})
    assert "transform" not in _css_x({"rotation": "x"})


def t_ob_compile_slant_clip_path():
    # positive slant leans "/"; text is untouched (no transform)
    assert ("clip-path: polygon(40px 0, 100% 0, calc(100% - 40px) 100%, 0 100%)"
            in _css_x({"slant": 40}))
    # negative slant leans "\"
    assert ("clip-path: polygon(0 0, calc(100% - 30px) 0, 100% 100%, 30px 100%)"
            in _css_x({"slant": -30}))
    # inclusive ±400 boundary is ACCEPTED (the mirror-critical clamp edge)
    assert "polygon(400px 0," in _css_x({"slant": 400})
    assert "100%, 400px 100%)" in _css_x({"slant": -400})
    assert "polygon(40.5px 0," in _css_x({"slant": 40.5}), \
        "a fractional slant keeps its decimals (no spurious .0 normalization)"


def t_ob_compile_slant_rejects():
    assert "clip-path" not in _css_x({"slant": 0})       # zero = no clip
    assert "clip-path" not in _css_x({"slant": 401})     # over +400
    assert "clip-path" not in _css_x({"slant": -401})    # under -400
    assert "clip-path" not in _css_x({"slant": "x"})     # non-number
    assert "clip-path" not in _css_x({"slant": True})    # bool rejected


def t_ob_compile_valign_and_text_transform():
    assert "align-items: center" in _css_x({"valign": "middle"})
    assert "align-items: flex-end" in _css_x({"valign": "bottom"})
    assert "text-transform: uppercase" in _css_x({"textTransform": "uppercase"})
    # unknown enum values dropped (no injection)
    assert "align-items" not in _css_x({"valign": "sideways"})
    assert "text-transform" not in _css_x({"textTransform": "evil; }"})


def t_ob_compile_font_weight_and_style():
    assert "font-weight: bold" in _css_x({"fontWeight": "bold"})
    assert "font-weight: normal" in _css_x({"fontWeight": "normal"})
    assert "font-style: italic" in _css_x({"fontStyle": "italic"})
    assert "font-style: normal" in _css_x({"fontStyle": "normal"})
    # out-of-scope / unknown values dropped (no injection, no numeric scale)
    assert "font-weight" not in _css_x({"fontWeight": "900"})
    assert "font-weight" not in _css_x({"fontWeight": "evil; }"})
    assert "font-style" not in _css_x({"fontStyle": "oblique"})


def t_ob_font_weight_style_are_text_only():
    assert "fontWeight" in ob.KIND_TEXT and "fontStyle" in ob.KIND_TEXT
    assert "fontWeight" not in ob.KIND_BOX and "fontStyle" not in ob.KIND_BOX


def t_ob_compile_text_shadow():
    css = _css_x({"textShadow": {"x": 0, "y": 2, "blur": 4, "color": "#000000"}})
    assert "text-shadow: 0px 2px 4px #000000" in css
    # all-zero offsets/blur -> invisible -> omitted
    assert "text-shadow" not in _css_x(
        {"textShadow": {"x": 0, "y": 0, "blur": 0, "color": "#000000"}})
    # missing/!str color dropped; non-dict dropped
    assert "text-shadow" not in _css_x({"textShadow": {"x": 1, "y": 1, "blur": 1}})
    assert "text-shadow" not in _css_x({"textShadow": "0 2px 4px red"})
    # color cannot inject (the _UNSAFE_VALUE gate via _safe_value)
    assert "text-shadow" not in _css_x(
        {"textShadow": {"x": 1, "y": 1, "blur": 1, "color": "red; } body{x:1"}})


def t_ob_compile_visible():
    # false -> display:none; true/absent -> no display rule; non-bool dropped
    assert "display: none" in _css_x({"visible": False})
    assert "display" not in _css_x({"visible": True})
    assert "display" not in _css_x({})
    assert "display" not in _css_x({"visible": "no"})
    assert "display" not in _css_x({"visible": 0})


def t_ob_visible_is_a_box_prop():
    # every slot (box + text) accepts visible
    assert "visible" in ob.KIND_BOX
    assert "visible" in ob.KIND_TEXT


def t_shipped_demo_overlay_css_matches_its_layout():
    # The demo profile ships a builder-authored HUD overlay: layout-hud.json is
    # the source, hud.css is its compiled output (what the relay serves). Guard
    # that the committed pair stays in sync; a hand-edit of one must not drift.
    import json
    def _read(*parts):
        with open(os.path.join(*parts), encoding="utf-8") as fh:
            return fh.read()
    od = os.path.join(ROOT, "profiles", "demo", "overlay")
    layout = json.loads(_read(od, "layout-hud.json"))
    slots = ob.extract_slots(_read(ROOT, "src", "obs", "hud.html"))
    compiled = ob.compile_overlay_css(layout, slots)
    on_disk = _read(od, "hud.css")
    assert compiled == on_disk, "demo hud.css is out of sync with layout-hud.json"
    # the race timer is an explicitly placed slot
    assert "#clock" in on_disk, "demo overlay has no clock/timer slot"


def t_hud_base_is_the_demo_standard():
    # The repo BASE hud.html ships the de-branded demo standard as its no-override
    # default: timer side-by-side with the stint, a fixed 54x48 dark number box and
    # the opaque race-control band. Locks those markers. (#206)
    with open(os.path.join(ROOT, "src", "obs", "hud.html"), encoding="utf-8") as f:
        style = ob.base_style(f.read())
    clock = re.search(r"#clock\s*\{[^}]*\}", style).group(0)
    assert "left: 986px" in clock and "236px" in clock, "clock not side-by-side"
    teamnum = re.search(r"\.team-num\s*\{[^}]*\}", style).group(0)
    assert "width: 54px" in teamnum and "#12161c" in teamnum, "number box not the fixed dark box"
    rc = re.search(r"#race-control\s*\{[^}]*\}", style).group(0)
    assert "#243039" in rc, "race-control not the opaque demo band"
    stint = re.search(r"#stint\s*\{[^}]*\}", style).group(0)
    assert "left: 712px" in stint, "stint not at the side-by-side position"


def t_flag_status_default_is_top_centered():
    # The base default places the flag-status banner at the top edge, just below
    # the stint/clock row, horizontally centred on the 1920-wide frame (x=960 ->
    # left 780 for the 360px box) with centred content. Locks the placement.
    with open(os.path.join(ROOT, "src", "obs", "hud.html"), encoding="utf-8") as f:
        style = ob.base_style(f.read())
    rule = re.search(r"#flag-status\s*\{[^}]*\}", style).group(0)
    assert "left: 780px" in rule and "top: 96px" in rule, "flag-status not top-centered"
    assert "justify-content: center" in rule, "flag-status content not centred"


def t_example_overlay_matches_demo_standard():
    # New leagues scaffold from `example` (profile_admin.create_profile), so the
    # example overlay must carry the SAME standard as the demo, or a stale example
    # override would fight the new base on every new profile.
    def _read(*parts):
        with open(os.path.join(*parts), encoding="utf-8") as fh:
            return fh.read()
    ex = os.path.join(ROOT, "profiles", "example", "overlay")
    de = os.path.join(ROOT, "profiles", "demo", "overlay")
    assert _read(ex, "hud.css") == _read(de, "hud.css"), "example hud.css != demo standard"
    assert os.path.isfile(os.path.join(ex, "layout-hud.json")), "example overlay has no layout-hud.json"
    assert _read(ex, "layout-hud.json") == _read(de, "layout-hud.json"), \
        "example layout-hud.json != demo standard"


def t_pov_box_from_css_full_rule():
    css = "#pov { left: 1516px; top: 600px; width: 384px; height: 216px; }"
    assert ob.pov_box_from_css(css) == {"left": 1516, "top": 600,
                                        "width": 384, "height": 216}


def t_pov_box_from_css_partial_keeps_only_present():
    assert ob.pov_box_from_css("#pov { left: 1516px; top: 600px; }") == \
        {"left": 1516, "top": 600}


def t_pov_box_from_css_absent_is_empty():
    assert ob.pov_box_from_css("#stint { left: 5px; }") == {}
    assert ob.pov_box_from_css("") == {}
    assert ob.pov_box_from_css(None) == {}


def t_pov_box_from_css_cascade_later_rule_wins_per_prop():
    # base rule then a customCss override appended later -> left overridden,
    # the other props retained from the earlier rule (per-property cascade).
    css = ("#pov { left: 1496px; top: 644px; width: 384px; height: 216px; }\n"
           "#pov { left: 1600px; }")
    assert ob.pov_box_from_css(css) == {"left": 1600, "top": 644,
                                        "width": 384, "height": 216}


def t_pov_box_from_css_ignores_pov_name_and_non_px():
    assert ob.pov_box_from_css("#pov-name { left: 99px; top: 10px; }") == {}
    # non-px value (e.g. a percentage) is skipped; px sibling still read.
    assert ob.pov_box_from_css("#pov { left: 50%; top: 600px; }") == {"top": 600}
    assert ob.pov_box_from_css("#pov {") == {}        # malformed: no closing brace


def t_pov_box_from_css_float_value():
    assert ob.pov_box_from_css("#pov { left: 1516.5px; }") == {"left": 1516.5}


def t_overlay_slot_obs_sources_constant():
    assert ob.OVERLAY_SLOT_OBS_SOURCES == {
        "pov":    {"scene": "Stint",   "source": "Feed POV"},
        "webcam": {"scene": "Program", "source": "Solo Webcam",
                   "export_scene": "Program"},
        "tyres-capture": {"scene": "Program", "source": "Solo Tyres/Fuel Capture",
                           "export_scene": "Program"},
    }
    # POV bakes whole-tree (no export_scene); the webcam/tyres bakes are scene-scoped.
    assert "export_scene" not in ob.OVERLAY_SLOT_OBS_SOURCES["pov"]
    assert ob.OVERLAY_SLOT_OBS_SOURCES["webcam"]["export_scene"] == "Program"
    assert ob.OVERLAY_SLOT_OBS_SOURCES["tyres-capture"]["export_scene"] == "Program"


def t_overlay_slot_obs_sources_has_tyres_capture():
    assert ob.OVERLAY_SLOT_OBS_SOURCES["tyres-capture"] == {
        "scene": "Program", "source": "Solo Tyres/Fuel Capture",
        "export_scene": "Program"}


def t_box_from_css_webcam_slot():
    css = "#webcam{left:14px;top:695px;width:336px;height:189px}"
    assert ob.box_from_css(css, "webcam") == {"left": 14, "top": 695,
                                              "width": 336, "height": 189}


def t_box_from_css_tyres_capture_slot():
    css = "#tyres-capture{left:7px;top:926px;width:245px;height:84px}"
    assert ob.box_from_css(css, "tyres-capture") == {"left": 7, "top": 926,
                                                       "width": 245, "height": 84}


def t_box_from_css_default_slot_is_pov():
    # box_from_css defaults to slot_id="pov", the same result as pov_box_from_css.
    css = "#pov { left: 1516px; top: 600px; width: 384px; height: 216px; }"
    assert ob.box_from_css(css) == ob.pov_box_from_css(css)


def t_pov_box_from_css_is_back_compat_wrapper():
    # pov_box_from_css must keep working byte-identically after the
    # generalization; released-product callers still import this exact name. (#324)
    css = "#pov { left: 1516px; top: 600px; }"
    assert ob.pov_box_from_css(css) == ob.box_from_css(css, "pov") == \
        {"left": 1516, "top": 600}


def t_ob_compile_shear_skewx():
    assert "transform: skewX(12deg)" in _css_x({"shear": 12})
    assert "transform: skewX(-20deg)" in _css_x({"shear": -20})
    assert "transform" not in _css_x({"shear": 90})      # 90 is degenerate
    assert "transform" not in _css_x({"shear": "x"})
    assert "transform" not in _css_x({"shear": True})


def t_ob_compile_rotation_and_shear_combine():
    css = _css_x({"rotation": 15, "shear": 10})
    # ONE combined transform, rotate before skewX
    assert "transform: rotate(15deg) skewX(10deg)" in css
    assert css.count("transform:") == 1


def t_ob_compile_slant_shear_gated_by_props():
    # a slot whose props lack slant/shear drops them (no injection)
    slots = [{"id": "x", "label": "X", "props": ["left", "top"]}]
    css = ob.compile_overlay_css(
        {"slots": {"x": {"slant": 40, "shear": 12, "rotation": 5}}}, slots)
    assert "clip-path" not in css and "transform" not in css


def t_ob_sample_covers_every_text_slot():
    """Every text-kind HUD slot has a non-empty SAMPLE entry so the builder
    canvas renders something for it. Box slots (images, the POV frame) are
    exempt: they carry an asset sample or are a frame with no text."""
    def _read(*parts):
        with open(os.path.join(*parts), encoding="utf-8") as fh:
            return fh.read()
    html = _read(ROOT, "src", "obs", "hud.html")
    slots = ob.extract_slots(html)
    sample = ob.SAMPLE["hud"]
    # A text slot is one whose prop set includes the text-only "fontSize"
    # (KIND_TEXT adds it; KIND_BOX does not).
    text_ids = [s["id"] for s in slots if "fontSize" in s["props"]]
    missing = [sid for sid in text_ids if not sample.get(sid)]
    assert not missing, "text slots missing a SAMPLE entry: %r" % missing


def t_ob_flag_presets_match_hud_states():
    """Every FLAG_PRESETS state is a real #flag-status[data-state="..."] hook
    in hud.html, so the builder's flag picker can colour the canvas. Guards
    drift if a state is renamed or removed from the page CSS."""
    def _read(*parts):
        with open(os.path.join(*parts), encoding="utf-8") as fh:
            return fh.read()
    html = _read(ROOT, "src", "obs", "hud.html")
    assert ob.FLAG_PRESETS, "FLAG_PRESETS must not be empty"
    for p in ob.FLAG_PRESETS:
        assert p.get("label"), "flag preset missing label: %r" % (p,)
        needle = 'data-state="%s"' % p["state"]
        assert needle in html, "flag state not a data-state hook in hud.html: %s" % p["state"]


def t_control_center_has_preview_data_panel():
    """The overlay builder ships the session-only Preview-data panel + the
    editable preview model the canvas renders from."""
    def _read(*parts):
        with open(os.path.join(*parts), encoding="utf-8") as fh:
            return fh.read()
    html = _read(ROOT, "src", "ui", "control-center.html")
    for needle in ('id="ov-preview-states"', 'id="ov-preview-fields"',
                   'ovState.preview', 'function ovFillSample',
                   'function ovPreviewReset', 'function ovFitName',
                   'flagPresets', "'overlay-preview:'"):
        assert needle in html, "control-center.html missing: %s" % needle


def t_preview_panel_is_outside_slot_panel():
    """#ov-panel is wiped by ovRenderPanel() (panel.textContent=''), so the
    session-only Preview-data panel must live OUTSIDE it or it is destroyed at
    runtime."""
    def _read(*parts):
        with open(os.path.join(*parts), encoding="utf-8") as fh:
            return fh.read()
    html = _read(ROOT, "src", "ui", "control-center.html")
    i = html.index('id="ov-panel"')
    j = html.index("</div>", i)          # first close tag = the #ov-panel close
    assert 'id="ov-preview"' not in html[i:j], \
        "Preview-data panel must not be inside #ov-panel (ovRenderPanel clears it)"


def t_intermission_is_an_overlay_page():
    assert "intermission" in feeds.OVERLAY_PAGES


def t_intermission_in_obs_page_paths():
    assert "/intermission" in feeds.OBS_PAGE_PATHS
    assert "/intermission/override.css" in feeds.OBS_PAGE_PATHS


def t_read_overlay_css_intermission_present():
    import tempfile, os
    with tempfile.TemporaryDirectory() as od:
        with open(os.path.join(od, "intermission.css"), "w") as fh:
            fh.write("#ichat{right:0}")
        assert feeds.read_overlay_css(od, "intermission") == b"#ichat{right:0}"


def t_intermission_page_polls_broadcast_chat_and_links_override():
    import os
    path = os.path.join(ROOT, "src", "obs", "intermission.html")
    assert os.path.exists(path), "src/obs/intermission.html missing"
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    assert "/broadcast-chat/data" in html         # reuses the existing endpoint
    assert "/intermission/override.css" in html   # per-league override link
    assert 'id="ichat"' in html and 'id="ichat-log"' in html


def t_ob_team_bar_is_a_box_slot_before_the_logo():
    # The tile colour bar must be the FIRST slot of its tile: the slots are
    # absolutely-positioned siblings, so DOM order is the paint order and a bar
    # after the logo would cover logo/number/model. (#555)
    with open(os.path.join(ROOT, "src", "obs", "hud.html"), encoding="utf-8") as f:
        slots = ob.extract_slots(f.read())
    ids = [s["id"] for s in slots]
    for n in (1, 2, 3):
        assert ids.index(f"team{n}-bar") < ids.index(f"team{n}-logo")
    by = {s["id"]: s for s in slots}
    assert by["team1-bar"]["props"] == list(ob.KIND_BOX)
    assert by["team1-quali"]["props"] == list(ob.KIND_TEXT)


def t_hud_page_publishes_team_colors_and_mode():
    # The base HUD must expose the colours as custom properties and the mode as a
    # body attribute, and contain NO league colour literal of its own.
    with open(os.path.join(ROOT, "src", "obs", "hud.html"), encoding="utf-8") as f:
        html = f.read()
    assert "--team-bg" in html and "--team-fg" in html
    assert "dataset.mode" in html
    assert "qualiLap" in html
    assert "background: var(--team-bg)" not in html, \
        "the bar carries no background in base; a profile decides"


if __name__ == "__main__":
    for n, fn in sorted(globals().items()):
        if n.startswith("t_") and callable(fn):
            fn(); print("ok", n)
    print("ALL PASS")

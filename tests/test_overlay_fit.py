#!/usr/bin/env python3
"""Generic max/min auto-fit for every overlay-builder text slot (#957).

Covers the slot model (every text slot offers fitMax/fitMin, box slots do not, the
team names keep their own pair), the compiler, the shared page script
src/ui/overlay-fit.js (driven in node with a fake DOM) and its wiring into the
relay pages and the Control Center canvas.
Run: python3 tests/test_overlay_fit.py"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import overlay_build as ob  # noqa: E402
import split_layout  # noqa: E402
import audience_widgets as aw  # noqa: E402

FIT_JS = os.path.join(ROOT, "src", "ui", "overlay-fit.js")
PAGES = ("hud.html", "splitscreen.html", "intermission.html")
TEAM_ALIAS = ("teamNameMax", "teamNameMin")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _builder_pages():
    """Every page markup the builder extracts slots from, by name."""
    pages = {name: _read("src", "obs", name) for name in PAGES}
    pages["splitscreen-builder"] = split_layout.builder_html(
        pages["hud.html"], pages["splitscreen.html"])
    widget = {"id": "audience-0123456789abcdef0123456789abcdef", "label": "Likes"}
    pages["audience-widget"] = aw.markup(widget)
    return pages


# ---- slot model -----------------------------------------------------------------

def t_text_kind_carries_the_fit_bounds_and_box_kind_does_not():
    assert "fitMax" in ob.KIND_TEXT and "fitMin" in ob.KIND_TEXT, ob.KIND_TEXT
    assert "fitMax" not in ob.KIND_BOX and "fitMin" not in ob.KIND_BOX, ob.KIND_BOX


def t_every_text_slot_on_every_page_offers_the_fit_bounds():
    seen_text = set()
    for page, html in _builder_pages().items():
        slots = ob.extract_slots(html)
        assert slots, page
        for slot in slots:
            props, sid = slot["props"], slot["id"]
            if "fontSize" not in props:            # a box slot: no font, no fit
                assert "fitMax" not in props and "fitMin" not in props, (page, sid, props)
                continue
            seen_text.add(sid)
            if any(p in props for p in TEAM_ALIAS):
                # Team names keep their own pair; never two competing max/min controls.
                assert set(TEAM_ALIAS) <= set(props), (page, sid, props)
                assert "fitMax" not in props and "fitMin" not in props, (page, sid, props)
            else:
                assert "fitMax" in props and "fitMin" in props, (page, sid, props)
                assert props.count("fitMax") == 1, (page, sid, props)
    # Previously unwired boxes on each page are text slots now.
    for sid in ("stint", "streamer", "tele-car", "clock", "split-left", "split-right",
                "ichat", "audience-0123456789abcdef0123456789abcdef"):
        assert sid in seen_text, sid


def t_team_name_slots_keep_their_own_pair_only():
    slots = {s["id"]: s for s in ob.extract_slots(_read("src", "obs", "hud.html"))}
    for n in (1, 2, 3):
        props = slots[f"team{n}-name"]["props"]
        assert props[-2:] == list(TEAM_ALIAS), props
        assert "fitMax" not in props and "fitMin" not in props, props


def t_explicit_fit_extras_are_not_duplicated():
    html = ('<div id="a" data-edit="A" data-edit-kind="text" '
            'data-edit-props="fitMax,fitMin"></div>'
            '<div id="b" data-edit="B" data-edit-props="left,top,fontSize"></div>'
            '<div id="c" data-edit="C" data-edit-props="left,top"></div>')
    by = {s["id"]: s["props"] for s in ob.extract_slots(html)}
    assert by["a"] == list(ob.KIND_TEXT), by["a"]
    assert by["b"] == ["left", "top", "fontSize", "fitMax", "fitMin"], by["b"]
    assert by["c"] == ["left", "top"], by["c"]


# ---- compiler -------------------------------------------------------------------

def t_compile_emits_the_fit_for_previously_unwired_text_slots():
    hud = ob.extract_slots(_read("src", "obs", "hud.html"))
    css = ob.compile_overlay_css({"slots": {
        "streamer": {"fitMax": 40, "fitMin": 18},
        "tele-car": {"fitMax": 30}}}, hud)
    assert "#streamer { --fit-max: 40px; --fit-min: 18px; }" in css, css
    assert "#tele-car { --fit-max: 30px; }" in css, css
    split = ob.extract_slots(_read("src", "obs", "splitscreen.html"))
    css = ob.compile_overlay_css({"slots": {"split-left": {"fitMax": 44, "fitMin": 20}}}, split)
    assert "#split-left { --fit-max: 44px; --fit-min: 20px; }" in css, css


def t_compile_drops_out_of_range_fit_bounds():
    hud = ob.extract_slots(_read("src", "obs", "hud.html"))
    for bad in (0, -5, 501, 1e300, True, "40", "40px", None, float("nan")):
        css = ob.compile_overlay_css({"slots": {"stint": {"fitMax": bad, "fitMin": bad}}}, hud)
        assert "--fit-" not in css, (bad, css)
    css = ob.compile_overlay_css({"slots": {"stint": {"fitMax": 500, "fitMin": 1}}}, hud)
    assert "#stint { --fit-max: 500px; --fit-min: 1px; }" in css, css


def t_compile_never_emits_the_fit_on_a_box_slot():
    hud = ob.extract_slots(_read("src", "obs", "hud.html"))
    css = ob.compile_overlay_css({"slots": {"pov": {"fitMax": 40},
                                            "team1-bar": {"fitMin": 12}}}, hud)
    assert css == "", css


def t_team_name_props_compile_unchanged():
    hud = ob.extract_slots(_read("src", "obs", "hud.html"))
    css = ob.compile_overlay_css({"slots": {"team1-name": {
        "teamNameMax": 34, "teamNameMin": 18, "fitMax": 40}}}, hud)
    assert css == "#team1-name { --team-name-max: 34px; --team-name-min: 18px; }\n", css


# ---- page wiring ----------------------------------------------------------------

_FIT_TAG = '<script src="/overlay-fit.js" data-fit-live></script>'


def t_every_builder_page_loads_the_shared_fit_script():
    for name in PAGES:
        html = _read("src", "obs", name)
        assert html.count(_FIT_TAG) == 1, name
        body = html[html.index("<body"):]
        # after the slot markup (the canvas takes the body up to the first script) and
        # before the page's own script, so the observer sees its first data render
        first_script = body.index("<script")
        assert body[first_script:].startswith(_FIT_TAG), name
        assert "overlay-fit.js" not in ob.base_body(html), name


def t_pages_carry_no_per_element_fit_calls():
    for name in PAGES:
        html = _read("src", "obs", name)
        for stale in ("fitText(", "fitName(", "shrinkToFit("):
            assert stale not in html, (name, stale)


def t_team_names_feed_the_generic_fit_through_their_own_pair():
    style = ob.base_style(_read("src", "obs", "hud.html"))
    rule = style[style.index(".team-name {"):]
    rule = rule[:rule.index("}")]
    assert "--fit-max: var(--team-name-max)" in rule, rule
    assert "--fit-min: var(--team-name-min)" in rule, rule
    assert "--team-name-max: 30px" in rule and "--team-name-min: 16px" in rule, rule


def _node():
    node = shutil.which("node")
    if not node:
        print("  (node not installed, JS check skipped)")
    return node


# A fake DOM: each element measures its text at 0.6 x font size per character.
_HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const registered = [], observers = [];
let scale = 1;
function mk(id, o) {
  o = o || {};
  return {id, textContent: o.text || '', clientWidth: o.w === undefined ? 200 : o.w,
    vars: o.vars || {}, visible: o.visible !== false,
    style: {fontSize: o.font || ''}, getClientRects() { return this.visible ? [{}] : []; }};
}
const els = {};
global.window = global;
global.CSS = {registerProperty(d) {
  if (registered.some(r => r.name === d.name)) throw new Error('InvalidModificationError');
  registered.push(d); }};
global.getComputedStyle = el => ({getPropertyValue: k => el.vars[k] || '',
  paddingLeft: '0px', paddingRight: '0px'});
global.MutationObserver = class { constructor(cb) { this.cb = cb; this.took = 0; observers.push(this); }
  observe(target, opts) { this.target = target; this.opts = opts; }
  takeRecords() { this.took++; return []; } disconnect() {} };
global.document = {
  readyState: 'complete', body: {tag: 'body'},
  currentScript: {hasAttribute: a => a === 'data-fit-live'},
  querySelectorAll: sel => sel === '[data-edit]' ? Object.values(els) : [],
  createRange: () => { let n; return {selectNodeContents(x) { n = x; },
    getBoundingClientRect() { return {width: n.textContent.length * parseFloat(n.style.fontSize || '40') * 0.6 * scale}; }}; },
  fonts: {ready: Promise.resolve(), addEventListener() {}},
  addEventListener() {},
};
vm.runInThisContext(fs.readFileSync(process.argv[2], 'utf8'));
const out = {};
"""


def _run(script):
    node = _node()
    if not node:
        return None
    res = subprocess.run([node, "-", FIT_JS], input=_HARNESS + script, capture_output=True,
                         text=True, encoding="utf-8", errors="replace", timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def t_fit_shrinks_a_long_text_and_leaves_a_slot_without_bounds_alone():
    out = _run(r"""
els.stint = mk('stint', {text: 'X'.repeat(20), vars: {'--fit-max': '40px', '--fit-min': '12px'}});
els.plain = mk('plain', {text: 'X'.repeat(20)});
RacecastFit.fitAll(document);
out.long = parseFloat(els.stint.style.fontSize); out.plain = els.plain.style.fontSize;
els.stint.textContent = 'XY'; RacecastFit.fitAll(document);
out.short = parseFloat(els.stint.style.fontSize);
els.stint.textContent = 'X'.repeat(500); RacecastFit.fitAll(document);
out.floor = parseFloat(els.stint.style.fontSize);
els.stint.vars = {'--fit-max': '40px'}; RacecastFit.fitAll(document);
out.defaultMin = parseFloat(els.stint.style.fontSize);
els.stint.vars = {'--fit-max': '1e300px'}; els.stint.textContent = 'X'; els.stint.clientWidth = 1000;
RacecastFit.fitAll(document);
out.capped = parseFloat(els.stint.style.fontSize);
els.stint.vars = {'--fit-max': '0px'}; RacecastFit.fitAll(document);
out.off = els.stint.style.fontSize;
console.log(JSON.stringify(out));""")
    if out is None:
        return
    # 20 chars x 0.6 x size <= 200 px -> 16 px
    assert out["long"] == 16, out
    assert out["plain"] == "", "a slot without --fit-max keeps its CSS font size"
    assert out["short"] == 40 and out["floor"] == 12, out
    assert out["defaultMin"] == 12, out
    assert out["capped"] == 500, out
    assert out["off"] == "", "clearing --fit-max hands the size back to the CSS"


def t_fit_measures_the_text_not_the_scroll_width():
    # #944: a centred title overflows on both sides, outside scrollWidth, so only the
    # measured text shows that it does not fit.
    with open(FIT_JS, encoding="utf-8") as fh:
        js = fh.read()
    assert ".scrollWidth" not in js and "createRange()" in js, "measure the text itself"


def t_fit_skips_hidden_and_zero_width_slots():
    out = _run(r"""
const vars = {'--fit-max': '40px', '--fit-min': '12px'};
els.hidden = mk('hidden', {text: 'X'.repeat(50), vars, visible: false, font: '33px'});
els.zero = mk('zero', {text: 'X'.repeat(50), vars, w: 0, font: '33px'});
RacecastFit.fitAll(document);
out.hidden = els.hidden.style.fontSize; out.zero = els.zero.style.fontSize;
console.log(JSON.stringify(out));""")
    if out is None:
        return
    assert out == {"hidden": "33px", "zero": "33px"}, out


def t_fit_honours_a_scaled_canvas():
    out = _run(r"""
els.a = mk('a', {text: 'X'.repeat(20), vars: {'--fit-max': '40px', '--fit-min': '12px'}});
scale = 0.5; RacecastFit.fit(els.a, {scale: 0.5});
out.scaled = parseFloat(els.a.style.fontSize);
console.log(JSON.stringify(out));""")
    if out is None:
        return
    assert out["scaled"] == 16, out


def t_fit_registers_non_inheriting_bounds():
    out = _run(r"""
RacecastFit.register();   // a second registration must not throw
out.registered = registered;
console.log(JSON.stringify(out));""")
    if out is None:
        return
    regs = {r["name"]: r for r in out["registered"]}
    assert set(regs) == {"--fit-max", "--fit-min"}, regs
    for reg in regs.values():
        assert reg["inherits"] is False, reg
        assert reg.get("initialValue") in ("0", "0px"), reg


def t_fit_watches_every_data_change():
    out = _run(r"""
els.s = mk('s', {text: 'XY', vars: {'--fit-max': '40px', '--fit-min': '12px'}});
const o = observers[0];
out.target = o && o.target && o.target.tag;
out.opts = o && o.opts;
els.s.textContent = 'X'.repeat(20);
o.cb([{type: 'childList'}]);
out.size = parseFloat(els.s.style.fontSize);
out.took = o.took;
console.log(JSON.stringify(out));""")
    if out is None:
        return
    assert out["target"] == "body", out
    opts = out["opts"]
    assert opts["childList"] and opts["subtree"] and opts["characterData"], opts
    assert {"hidden", "class", "style"} <= set(opts["attributeFilter"]), opts
    assert out["size"] == 16, out
    assert out["took"] >= 1, "own style writes are dropped, or the observer loops"


# ---- serving + canvas -----------------------------------------------------------

def t_relay_serves_the_fit_script():
    import test_console_gate as gate
    srv = gate._serve()
    try:
        code, body = gate._get(srv.server_address[1], "/overlay-fit.js")
    finally:
        srv.shutdown()
    assert code == 200 and "RacecastFit" in body, (code, body[:200])


def t_control_center_serves_the_fit_script_and_the_canvas_uses_it():
    import test_ui_server as ui
    httpd, port = ui._serve(ui._ctx())
    try:
        code, body = ui._get(port, "/overlay-fit.js")
    finally:
        httpd.shutdown()
    assert code == 200 and b"RacecastFit" in body, code
    html = _read("src", "ui", "control-center.html")
    assert '<script src="/overlay-fit.js"></script>' in html
    refit = html[html.index("function ovRefit("):]
    refit = refit[:refit.index("\n}\n")]
    assert "RacecastFit.fit(" in refit and "ovState.scale" in refit, refit
    assert "-name" not in refit and "editProps" not in refit, \
        "the canvas fit is generic: no per-slot list, no markup props lookup"
    for stale in ("function ovFitName(", "function ovFitText(", "function ovShrinkToFit("):
        assert stale not in html, stale


def t_build_ships_the_fit_script():
    text = _read("tools", "build.py")
    assert "overlay-fit.js" in text, "build verify must check the shared fit script ships"


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    for n, fn in sorted(globals().items()):
        if n.startswith("t_") and callable(fn):
            fn()
            print("ok", n)
    print("ALL PASS")

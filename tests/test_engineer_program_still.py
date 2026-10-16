#!/usr/bin/env python3
"""The Race Engineer desk's program still: markup anchors plus the poll lifecycle,
run under node when it is installed (skipped otherwise; the e2e desk renders it).
Run: python3 tests/test_engineer_program_still.py"""
import os
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(os.path.dirname(HERE), "src", "console", "race-engineer.html")


def _html():
    with open(PAGE, encoding="utf-8") as fh:
        return fh.read()


def _func_src(html, name):
    i = html.index(f"function {name}(")
    return html[i:html.index("\n", i)]


def _run_js(src):
    node = shutil.which("node")
    if not node:
        print("  (node not installed, JS check skipped)")
        return None
    return subprocess.run([node, "-"], input=src, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True, timeout=30).stdout


def t_program_card_sits_beside_the_live_instruments():
    h = _html()
    i, p, c = h.index('id="instruments"'), h.index('id="program-card"'), h.index("Compare B with A")
    assert i < p < c, "the program still belongs to the live-instruments block, before the analysis"
    assert 'id="program-toggle"' in h and 'aria-controls="program-frame"' in h


def t_track_map_leads_the_live_side_column_above_the_program():
    h = _html()
    trace, side, track, pgm, compare = (h.index('id="input-trace"'), h.index('id="live-side"'),
                                        h.index('id="map"'), h.index('id="program-card"'),
                                        h.index("Compare B with A"))
    assert trace < side < track < pgm < compare, "map sits in the live area, the program below it"
    assert track < h.index('id="plots"'), "the map left the comparison plots"


def t_program_still_uses_the_any_tier_cockpit_endpoint():
    # /cockpit/program is open to every console role, the engineer included; the
    # director-only /preview/* routes are not.
    body = _func_src(_html(), "pgmPoll")
    assert "RC_API('/cockpit/program')" in body and "/preview/" not in body


_HARNESS = r"""
const probes = [], timers = [];
class Image { constructor() { probes.push(this); } }
const setTimeout = (fn) => { timers.push(fn); return timers.length; };
const clearTimeout = id => { if (id) timers[id - 1] = null; };
const live = () => timers.filter(Boolean).length;
const mk = () => ({hidden: false, textContent: '', src: '', attrs: {},
  setAttribute(k, v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; this.src = ''; },
  classList: {s: new Set(), toggle(c, on) { on ? this.s.add(c) : this.s.delete(c); },
              add(c) { this.s.add(c); }, remove(c) { this.s.delete(c); }}});
const els = {}; const $ = id => els[id] || (els[id] = mk());
const document = {visibilityState: 'visible'};
const RC_API = p => p;
const state = {stopped: false};
const pgm = {gen: 0, timer: 0, running: false, open: true};
"""


def t_program_poll_runs_once_and_stops_when_hidden_or_revoked():
    h = _html()
    funcs = "\n".join(_func_src(h, n) for n in ("pgmPoll", "pgmHalt", "pgmWanted", "pgmSync"))
    out = _run_js(_HARNESS + funcs + """
pgmSync(); pgmSync();                       // the access poll calls this every tick
const once = probes.length;
probes[0].onload(); const polling = live();
pgm.open = false; pgmSync(); const inFlight = probes.at(-1);
const afterHide = live(), toggle = $('program-toggle').textContent;
pgm.open = true; pgmSync(); probes.at(-1).onload();
document.visibilityState = 'hidden'; pgmSync(); probes.at(-1).onload();
const backgroundTab = live();
document.visibilityState = 'visible'; pgmSync(); probes.at(-1).onerror();
const note = $('program-note').textContent;
state.stopped = true; pgmSync(); probes.at(-1).onload();
console.log([once, polling, afterHide, toggle, backgroundTab, note, live()].join('|'));""")
    if out is not None:
        assert out.strip() == "1|1|0|Show|0|Program unavailable|0", \
            f"one poll loop; HIDE, a background tab and lost access stop it: {out!r}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

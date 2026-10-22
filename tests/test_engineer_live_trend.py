#!/usr/bin/env python3
"""Race Engineer desk: the reference precedence (shared source, personal pick, shared
pin, best lap), the delta label naming it, the live-delta trend arrow, the steering readout and the input-trace
markup. The JS checks run under node when it is installed (skipped otherwise).
Run: python3 tests/test_engineer_live_trend.py"""
import os
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(os.path.dirname(HERE), "src", "console", "race-engineer.html")


def _html():
    with open(PAGE, encoding="utf-8") as fh:
        return fh.read()


def _func_src(html, name):
    """A top-level function: its line plus the indented continuation lines."""
    i = html.index(f"\nfunction {name}(") + 1
    lines = html[i:].split("\n")
    out = [lines[0]]
    for line in lines[1:]:
        if not line.startswith(" "):
            if line.startswith("}"):             # a closing brace on its own line
                out.append(line)
            break
        out.append(line)
    return "\n".join(out)


def _const_src(html, name):
    i = html.index(f"\nconst {name}=") + 1
    return html[i:html.index("\n", i)]


def _run_js(src):
    node = shutil.which("node")
    if not node:
        print("  (node not installed, JS check skipped)")
        return None
    return subprocess.run([node, "-"], input=src, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True, timeout=30).stdout


_HARNESS = r"""
let now = 0;
const performance = {now: () => now};
const mk = () => ({textContent: '', className: '', attrs: {}, checked: false,
  setAttribute(k, v) { this.attrs[k] = v; }, classList: {add() {}, remove() {}, toggle() {}}});
const els = {}; const $ = id => els[id] || (els[id] = mk());
const document = {querySelectorAll: () => []};
const options = () => {}, carLabel = r => String(r.car_id), referenceLabel = r => 'ref';
const NA = 'N/A';
const num = (v, d = 1) => typeof v === 'number' && Number.isFinite(v) ? v.toFixed(d) : NA;
const state = {follow: true, snapshot: null, laps: [], sessions: [], refs: [], refLaps: [],
  refSource: '', a: '', b: '', session: '', rejected: new Set(), newLaps: 0,
  pick: null, aKind: '', trend: {key: '', rows: []}};
const lap = (n, time, eligible = true) => ({id: 'L' + n, session_id: 'S', lap: n, state: 'confirmed',
  time_s: time, comparison_eligible: eligible, car_id: 1, track_id: 'T'});
const live = n => ({id: 'L' + n, session_id: 'S', lap: n, state: 'provisional',
  provisional_comparison_eligible: true, car_id: 1, track_id: 'T'});
"""


def t_lap_clocks_use_minutes_and_round_across_boundaries():
    h = _html()
    src = "\n".join(_func_src(h, name) for name in ("lapTime", "instruments", "lapLabel"))
    out = _run_js(_HARNESS + src + """
instruments({current: {lap: 2, elapsed_s: 125.678}, best_s: 62.345, theoretical_s: 59.9996});
console.log(['lap-time', 'best', 'theory'].map(id => $(id).textContent).join('|'));
console.log(lapLabel(lap(2, 62.345)));
console.log([0, .004, 3599.9996, null, NaN, Infinity, -1].map(lapTime).join('|'));
""")
    if out is not None:
        assert out.splitlines() == [
            '2:05.678|1:02.345|1:00.000', 'Lap 2 · 1:02.345',
            '0:00.000|0:00.004|60:00.000|N/A|N/A|N/A|N/A']


def _reference_src(h):
    return "\n".join(_func_src(h, n) for n in ("lapTime", "lapLabel", "sameCohort", "comparable",
                                              "usableRefs", "selections", "referenceText", "pinControls"))


def t_reference_prefers_source_pick_pin_then_best():
    h = _html()
    out = _run_js(_HARNESS + _reference_src(h) + """
const seen = [], show = () => { selections(); seen.push(state.a + '/' + state.aKind); };
state.laps = [lap(3, 90), lap(4, 92)]; state.snapshot = {current: live(5), pin: null, pin_revision: 0}; show();
state.pick = {id: 'L4', best: 'L3', pinRev: 0}; show();                        // personal pick
state.laps.push(lap(5, 95)); state.snapshot = {current: live(6), pin: null, pin_revision: 0}; show();
state.laps.push(lap(6, 88)); state.snapshot = {current: live(7), pin: null, pin_revision: 0}; show();  // new best drops the pick
const pin = {lap_id: 'L4', session_id: 'S', lap: 4};
state.snapshot = {current: live(7), pin, pin_revision: 1}; show();
state.laps.push(lap(7, 85)); state.snapshot = {current: live(8), pin, pin_revision: 1}; show();     // a pin outlasts a new best
state.pick = {id: 'L5', best: 'L7', pinRev: 1}; show();
state.snapshot = {current: live(8), pin: null, pin_revision: 2}; show();                          // a pin change drops the pick
state.follow = false; state.session = 'S0'; state.b = 'S0L1';
state.laps.push({...lap(1, 80), id: 'S0L1', session_id: 'S0'}, {...lap(2, 79), id: 'S0L2', session_id: 'S0'});
state.pick = {id: 'L5', best: 'L7', pinRev: 2}; show();                                        // a pick from another session
state.refSource = 'R'; state.a = 'r:x'; state.laps.push({...lap(3, 70), id: 'S0L3', session_id: 'S0'}); show();
console.log(seen.join('|'));
state.refSource = ''; state.follow = true; state.pick = null; state.laps = [lap(3, 90), lap(4, 92)];
state.snapshot = {current: live(5), pin: {lap_id: 'L2', session_id: 'S', lap: 2, by: 'Alex'}, pin_revision: 3};
selections(); console.log(state.a + '/' + state.aKind + '|' + referenceText() + '|' + $('pin-note').textContent);""")
    if out is not None:
        lines = out.splitlines()
        assert lines[0] == ("L3/best|L4/pick|L4/pick|L6/best|L4/pinned|L4/pinned|L5/pick|L7/best|"
                            "S0L2/best|r:x/source"), out
        assert lines[1] == "L2/pinned|Against pinned · Lap 2|Pinned lap detail no longer kept", out   # case gone


def t_old_snapshot_without_pin_keys_reads_as_no_pin():
    h = _html()
    out = _run_js(_HARNESS + _reference_src(h) + """
state.laps = [lap(3, 90), lap(4, 92)]; state.snapshot = {current: live(5)}; selections();
const before = state.a + '/' + state.aKind;
state.pick = {id: 'L4', best: 'L3', pinRev: 0}; selections();
console.log(before + '|' + state.a + '/' + state.aKind);""")
    if out is not None:
        assert out.strip() == "L3/best|L4/pick", out


def t_pin_controls_follow_the_pin_and_the_eligible_reference():
    h = _html()
    out = _run_js(_HARNESS + _reference_src(h) + """
const seen = [], show = () => { selections(); const b = $('pin-toggle');
  seen.push(b.textContent + '/' + (b.disabled ? 'off' : 'on') + '/' + $('pin-note').textContent); };
const ready = (n, t) => ({...lap(n, t), detail_state: 'ready'});
state.laps = [ready(3, 90), ready(4, 92)]; state.snapshot = {session_id: 'S', current: live(5), pin: null, pin_revision: 0}; show();
state.snapshot = {...state.snapshot, pin: {lap_id: 'L3', session_id: 'S', lap: 3, by: 'Alex'}, pin_revision: 1}; show();
state.laps[0].detail_state = 'evicted'; show();
state.snapshot = {session_id: 'S', current: live(5), pin: null, pin_revision: 2}; state.pinFail = 2; show();
state.pinFail = null; state.laps = [ready(3, 90)]; state.snapshot = {...state.snapshot, current: live(1)};
state.laps[0].comparison_eligible = false; show();                                         // no eligible A
console.log(seen.join('|'));""")
    if out is not None:
        assert out.strip() == ("Pin lap A/on/|Unpin lap 3/on/Pinned by Alex · Lap 3|"
                               "Unpin lap 3/on/Pinned lap detail no longer kept|"
                               "Pin lap A/on/Pin could not be saved|Pin lap A/off/"), out


def t_delta_label_names_the_reference():
    h = _html()
    src = "\n".join([_const_src(h, "TREND_S")] + [_func_src(h, n) for n in ("trend", "referenceText", "showDelta")])
    out = _run_js(_HARNESS + "const signed = (v, d = 3) => (Number.isFinite(v) && v > 0 ? '+' : '') + num(v, d);\n" + src + """
const seen = [];
state.snapshot = {current: live(30)};
state.laps = [lap(17, 90)]; state.a = 'L17'; state.aKind = 'pinned'; showDelta(1.234, 430); seen.push($('delta-note').textContent);
state.laps = [lap(23, 88)]; state.a = 'L23'; state.aKind = 'best'; showDelta(null); seen.push($('delta-note').textContent);
state.refLaps = [{...lap(3, 80), id: 'r:x', session_id: 'r:R:1'}]; state.a = 'r:x'; state.aKind = 'source'; showDelta(null);
seen.push($('delta-note').textContent);
console.log(seen.join('|'));""")
    if out is not None:
        assert out.strip() == ("Against pinned · Lap 17 · through 430 m|Against best · Lap 23|"
                               "Against ref · Lap 3"), out


def t_delta_trend_arrow_follows_the_hud_rule():
    h = _html()
    src = _const_src(h, "TREND_S") + "\n" + _func_src(h, "trend")
    out = _run_js(_HARNESS + src + """
const seen = [], show = () => seen.push($('delta-trend').textContent || '-');
state.a = 'L4'; state.snapshot = {current: live(5)};
trend(.10, 100); show();                                // one sample: no trend yet
now = 500; trend(.15, 150); show();                     // gap grows: losing now
now = 600; trend(.15, 150); show();                     // a repeated render adds nothing
now = 1000; trend(.05, 200); show();                    // shrinks against 1.5 s ago: gaining
now = 1100; trend(.06, 210); now = 2900; trend(.065, 220); show();   // older samples age out
trend(null, null); show();
state.a = 'L3'; trend(.5, 230); show();                 // a new reference starts over
console.log(seen.join('|') + '|' + $('delta-trend').attrs['aria-label']);""")
    if out is not None:
        assert out.strip() == "-|▲|▲|▼|-|-|-|No trend", out


def t_steering_reads_degrees_with_direction():
    h = _html()
    out = _run_js(_HARNESS + _func_src(h, "lapTime") + _func_src(h, "instruments") + """
const seen = [];
for (const steer_rad of [0.2, -0.2, 0.001, null]) {
  instruments({instruments: {steer_rad}}); seen.push($('steer').textContent); }
console.log(seen.join('|'));""")
    if out is not None:
        assert out.strip() == "11° L|11° R|0°|N/A", out


def t_average_fuel_estimates_the_laps_left_on_the_current_fuel():
    h = _html()
    assert 'id="fuel-avg"' in h and h.index('id="instruments"') < h.index('id="fuel-avg"') < h.index('id="input-trace"')
    out = _run_js(_HARNESS + _func_src(h, "lapTime") + _func_src(h, "duration") + _func_src(h, "instruments") + """
const seen = [];
for (const [avg, laps, fuel, lapAvg] of [[3.0612, 12, 85.7, 90], [3.0612, 12, null, 90], [null, 0, 40, 90],
                                         [1.0, 3, 100, 90], [2.5, 3, 40, null]]) {
  instruments({instruments: {fuel_l: fuel}, fuel_avg_l: avg, fuel_avg_laps: laps, lap_avg_s: lapAvg});
  seen.push($('fuel-avg').textContent + '/' + $('fuel-avg-note').textContent + '/' + $('fuel-time-note').textContent); }
console.log(seen.join('|'));""")
    if out is not None:
        assert out.strip() == ("3.06/≈ 28.0 laps left/≈ 42:00 time left|3.06/N/A/N/A|"
                               "N/A/No eligible lap with fuel use/N/A|1.00/≈ 100.0 laps left/≈ 2:30:00 time left|"
                               "2.50/≈ 16.0 laps left/N/A"), out


def t_shift_points_compare_the_lap_in_progress_with_the_vehicle_reference():
    h = _html()
    assert h.index('id="input-trace"') < h.index('id="shift-table"') < h.index('id="live-side"')
    harness = _HARNESS + """
const rowsOut = []; const table = {tBodies: [{replaceChildren() { rowsOut.length = 0; },
  insertRow() { const r = []; rowsOut.push(r); return {insertCell() { const c = {textContent: '', className: ''}; r.push(c); return c; }}; }}]};
els['shift-table'] = table;
const signed = (v, d = 3) => (Number.isFinite(v) && v > 0 ? '+' : '') + num(v, d);
"""
    out = _run_js(harness + _func_src(h, "renderShifts") + """
const show = () => rowsOut.map(r => r.map(c => c.textContent).join(',')).join(' / ') + ' | ' + $('shift-note').textContent;
renderShifts({shifts: {car_id: 1, lap: 3, reference: {kind: 'external', ratio_source: 'decoded transmission', warnings: [],
  targets: [{gear: 1, rpm: 6800.4}, {gear: 2, rpm: 7012}, {gear: 3, rpm: null}]},
  upshifts: [{from_gear: 2, to_gear: 3, pre_cut_rpm: 6950, deviation_rpm: -62, phase_status: 'measured'},
             {from_gear: 1, to_gear: 2, pre_cut_rpm: null, deviation_rpm: null, phase_status: 'ambiguous/missing'},
             {from_gear: 2, to_gear: 3, pre_cut_rpm: 7100, deviation_rpm: 88, phase_status: 'measured'}]}});
console.log(show());
renderShifts({shifts: {car_id: 1, lap: 4, reference: {kind: 'unavailable', targets: [], warnings: []}, upshifts: []}});
console.log(show());
renderShifts({shifts: null}); console.log(show());""")
    if out is not None:
        first, missing, none = out.strip().split("\n")
        assert first.startswith("1,6800,N/A,N/A,1 / 2,7012,7100,+88,2 / 3,N/A,N/A,N/A,0"), first
        assert "Telemetry" in missing and "Data management" in missing and missing.startswith(" |"), missing
        assert none.endswith("Waiting for a car"), none


def t_desk_shows_na_for_every_missing_value():
    h = _html()
    for prose in ("Shared reference unavailable", "Live analysis unavailable", "Analysis temporarily unavailable",
                  "Telemetry source unavailable", "Program unavailable", "Crew chat temporarily unavailable"):
        h = h.replace(prose, "")
    assert "Unavailable" not in h.replace("SourceUnavailable", ""), "a missing value still reads Unavailable"
    assert h.count(">N/A<") >= 14, h.count(">N/A<")
    assert "const NA='N/A'" in h
    assert "button:disabled{opacity:.5;cursor:default}" in h, "a disabled button must look disabled"


def t_input_trace_sits_with_the_live_values_and_uses_the_gated_route():
    h = _html()
    i, steer, trace, pgm = (h.index('id="instruments"'), h.index('id="steer"'),
                            h.index('id="input-trace"'), h.index('id="program-card"'))
    assert i < steer < trace < pgm, "steering and the trace belong to the live values"
    body = _func_src(h.replace("\nasync function trcPoll(", "\nfunction trcPoll("), "trcPoll")
    assert "request('/race-engineer/trace'" in body and "trcWanted()" in body
    assert 'id="delta-trend"' in h and h.index('id="delta-trend"') < h.index('id="delta"')


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

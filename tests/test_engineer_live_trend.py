#!/usr/bin/env python3
"""Race Engineer desk: the per-lap automatic reference (B = live lap, A = the lap just
completed), the live-delta trend arrow, the steering readout and the input-trace
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
const num = (v, d = 1) => typeof v === 'number' && Number.isFinite(v) ? v.toFixed(d) : 'Unavailable';
const state = {follow: true, snapshot: null, laps: [], sessions: [], refs: [], refLaps: [],
  refSource: '', a: '', b: '', session: '', rejected: new Set(), newLaps: 0, liveLap: null,
  autoA: null, trend: {key: '', rows: []}};
const lap = (n, time, eligible = true) => ({id: 'L' + n, session_id: 'S', lap: n, state: 'confirmed',
  time_s: time, comparison_eligible: eligible, car_id: 1, track_id: 'T'});
const live = n => ({id: 'L' + n, session_id: 'S', lap: n, state: 'provisional',
  provisional_comparison_eligible: true, car_id: 1, track_id: 'T'});
"""


def t_follow_live_compares_each_lap_with_the_lap_just_completed():
    h = _html()
    src = "\n".join([_const_src(h, "AUTO_A_WAIT_MS")] + [_func_src(h, n) for n in
                    ("lapLabel", "sameCohort", "comparable", "selections")])
    out = _run_js(_HARNESS + src + """
const seen = [];
state.laps = [lap(3, 90), lap(4, 92)]; state.snapshot = {current: live(5)};
selections(); seen.push(state.a);                      // page opened mid-lap: A = lap 4, not the fastest
state.a = 'L3'; state.autoA = null; selections(); seen.push(state.a);   // engineer override holds
state.snapshot = {current: live(6)}; selections(); seen.push(state.a || '-');   // lap 5 not in history yet
state.laps.push(lap(5, 95)); selections(); seen.push(state.a);          // arrives: A = lap 5
state.snapshot = {current: live(7)}; state.laps.push(lap(6, 99, false)); selections(); seen.push(state.a);
state.snapshot = {current: live(8)}; selections(); seen.push(state.a || '-');
now = 9000; selections(); seen.push(state.a);          // lap 7 never arrived: fall back to fastest
state.refSource = 'R'; state.a = 'r:x'; state.snapshot = {current: live(9)}; selections(); seen.push(state.a);
state.refSource = ''; state.follow = false; state.a = 'L4'; state.snapshot = {current: live(10)};
selections(); seen.push(state.a + '/' + state.b);
console.log(seen.join('|'));""")
    if out is not None:
        assert out.strip() == "L4|L3|-|L5|L3|-|L3|r:x|L4/L9", out


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
    out = _run_js(_HARNESS + _func_src(h, "instruments") + """
const seen = [];
for (const steer_rad of [0.2, -0.2, 0.001, null]) {
  instruments({instruments: {steer_rad}}); seen.push($('steer').textContent); }
console.log(seen.join('|'));""")
    if out is not None:
        assert out.strip() == "11° L|11° R|0°|Unavailable", out


def t_average_fuel_estimates_the_laps_left_on_the_current_fuel():
    h = _html()
    assert 'id="fuel-avg"' in h and h.index('id="instruments"') < h.index('id="fuel-avg"') < h.index('id="input-trace"')
    out = _run_js(_HARNESS + _func_src(h, "instruments") + """
const seen = [];
for (const [avg, laps, fuel] of [[3.0612, 12, 85.7], [3.0612, 12, null], [null, 0, 40], [2.5, 3, 0]]) {
  instruments({instruments: {fuel_l: fuel}, fuel_avg_l: avg, fuel_avg_laps: laps});
  seen.push($('fuel-avg').textContent + '/' + $('fuel-avg-note').textContent); }
console.log(seen.join('|'));""")
    if out is not None:
        assert out.strip() == ("3.06/≈ 28.0 laps left|3.06/Fuel level unavailable|"
                               "Unavailable/No eligible lap with fuel use|2.50/≈ 0.0 laps left"), out


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
        assert first.startswith("1,6800,Unknown,—,1 / 2,7012,7100,+88,2 / 3,Unavailable,—,—,0"), first
        assert "Telemetry" in missing and "Data management" in missing and missing.startswith(" |"), missing
        assert none.endswith("Waiting for a car"), none


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

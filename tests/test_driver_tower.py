#!/usr/bin/env python3
"""The OBS timing tower (src/obs/drivers.html) and the public timing page's finish:
the value column's cycle, the fuel column and the chequered flag. The JS checks run
under node when it is installed (skipped otherwise).
Run: python3 tests/test_driver_tower.py"""
import json
import os
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOWER = os.path.join(ROOT, "src", "obs", "drivers.html")
TIMING = os.path.join(ROOT, "src", "console", "timing.html")

# A DOM just large enough for the page's top-level code and its row builders.
_DOM = r"""
function node(tag) {
  return {tag: tag, className: "", textContent: "", style: {}, children: [], attrs: {},
          classList: {add() {}, remove() {}, toggle() {}},
          setAttribute(k, v) { this.attrs[k] = v; }, toggleAttribute() {},
          appendChild(c) { this.children.push(c); return c; },
          append(...c) { this.children.push(...c); }, prepend() {},
          replaceChildren(...c) { this.children = c; }};
}
const els = {};
const document = {getElementById: id => els[id] || (els[id] = node("div")),
                  createElement: node, createElementNS: (ns, t) => node(t), body: node("body"),
                  createTextNode: t => Object.assign(node("#text"), {textContent: String(t)})};
function fetch() { return new Promise(() => {}); }
function setTimeout() {}
function flat(n) { return [n].concat(...(n.children || []).map(flat)); }
function classes(n) { return flat(n).map(c => c.className).filter(Boolean); }
function text(n) { return flat(n).map(c => c.textContent).join(""); }
"""


def _script(path):
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    i = html.rindex("<script>") + len("<script>")
    return html[i:html.index("</script>", i)]


def _run(js):
    node = shutil.which("node")
    if not node:
        print("  (node not installed, JS check skipped)")
        return None
    out = subprocess.run([node, "-"], input=js, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _tower(body):
    return _run(_DOM + _script(TOWER) + "\n;console.log(JSON.stringify((function () {"
                + body + "})()));")


CAR = {"pos": 2, "entry": "Team B #2", "number": "2", "driver": "Ann Lind", "team": "Team B",
       "status": "racing", "gap_s": 1.44, "lapped": 0, "last_lap_ms": 118412, "fuel_l": 56.4,
       "finished": False, "compound": "RM", "pit_stops": 1}


def t_the_value_column_cycles_gap_interval_and_last_lap():
    got = _tower(f"""
      const e = Object.assign({json.dumps(CAR)}, {{interval_s: 0.52}});
      return [0, 1, 2, 3].map(function (k) {{
        Date.now = () => k * CYCLE_MS + 1;
        showFuel = true;
        return [column(e, 1), column(e, 0), phaseLabel()];
      }});""")
    if got is None:
        return
    assert [g[0] for g in got] == ["+1.4", "+0.5", "1:58.412", "+1.4"], \
        f"three phases, gap to the leader, interval to the car ahead, last lap: {got}"
    assert [g[1] for g in got[:2]] == ["Leader", "Leader"], f"the leader reads Leader in both gap phases: {got}"
    assert [g[2] for g in got] == ["GAP", "INT", "LAST LAP", "GAP"], f"the header names the phase: {got}"


def t_a_lapped_car_without_an_interval_reads_its_laps():
    got = _tower(f"""
      Date.now = () => CYCLE_MS + 1;
      return column(Object.assign({json.dumps(CAR)}, {{lapped: 1, interval_s: null}}), 5);""")
    if got is not None:
        assert got == "+1 L", f"the interval phase falls back to the lap count: {got}"


def t_fuel_reads_whole_percent_and_turns_amber_at_ten():
    got = _tower("""
      return [56.4, 10.0, 7.2, 10.4, 140, -3, null, undefined].map(function (f) {
        return fuelPct({fuel_l: f, status: "racing"});
      });""")
    if got is None:
        return
    assert got[:6] == [56, 10, 7, 10, 100, 0], f"whole percent, clamped to the tank: {got}"
    assert got[6:] == [None, None], f"no fuel reading, no percent: {got}"
    low = _tower("""
      return [56.4, 10.0, 10.4, 10.6, 7.2].map(function (f) {
        return classes(fuelCell({fuel_l: f, status: "racing"})).join(" ");
      });""")
    assert ["low" in c.split() for c in low] == [False, True, True, False, True], \
        f"amber at or below the 10 % the tower shows: {low}"


def t_a_racing_row_carries_tyre_and_fuel_columns():
    got = _tower(f"""
      showFuel = true; showTyre = true;
      const r = row({json.dumps(CAR)}, 1, "+1.4", "P1");
      return {{cls: classes(r), text: text(r)}};""")
    if got is None:
        return
    assert "dd-fuel" in got["cls"] and any(c.startswith("dd-tyre") for c in got["cls"]), got
    assert "56%" in got["text"], f"the fuel column reads the percent: {got}"
    assert not any("dd-flag" in c for c in got["cls"]), f"no flag before the finish: {got}"



def t_the_builder_sample_rows_have_the_live_columns():
    with open(TOWER, encoding="utf-8") as fh:
        html = fh.read()
    sample = html[html.index('<div id="dd-rows">'):html.index('<div id="drivers-fastest"')]
    rows = sample.split('<div class="dd-row">')[1:]
    assert len(rows) == 3, len(rows)
    for r in rows:
        order = [r.find(f'class="{c}') for c in ("dd-tyre", "dd-fuel", "dd-val", "dd-pit")]
        assert -1 not in order and order == sorted(order), \
            f"each sample row the builder draws has tyre, fuel, value and stops like a live row: {r}"

def t_stale_rows_show_no_fuel():
    got = _tower(f"""
      return ["est", "no_data", "dc"].map(function (s) {{
        return text(fuelCell(Object.assign({{}}, {json.dumps(CAR)}, {{status: s}})));
      }});""")
    if got is None:
        return
    assert got == ["", "", ""], f"a quiet car's last fuel reading is stale: {got}"


def t_a_finished_row_shows_the_flag_and_its_final_gap():
    got = _tower(f"""
      const e = Object.assign({{}}, {json.dumps(CAR)}, {{finished: true}});
      const out = {{}};
      for (const k of [0, 1]) {{
        Date.now = () => k * CYCLE_MS + 1;
        out["v" + k] = column(e, 1);
      }}
      out.leader = column(Object.assign({{}}, e, {{pos: 1}}), 0);
      out.lapped = column(Object.assign({{}}, e, {{lapped: 2}}), 3);
      showFuel = true; showTyre = true;
      const r = row(e, 1, out.v0, "P1");
      out.cls = classes(r);
      out.span = flat(r).filter(c => /dd-flag/.test(c.className)).map(c => c.style.gridColumn);
      showFuel = false; showTyre = false;
      out.bare = classes(row(e, 1, out.v0, "P1"));
      return out;""")
    if got is None:
        return
    assert got["v0"] == "+1.4" and got["v1"] == "+1.4", f"the final gap, no cycling: {got}"
    assert got["leader"] == "Leader" and got["lapped"] == "+2 L", got
    assert any("dd-flag" in c for c in got["cls"]), f"a finished row shows the flag: {got}"
    assert "dd-fuel" not in got["cls"] and not any(c.startswith("dd-tyre") for c in got["cls"]), \
        f"the flag replaces ring and fuel: {got}"
    assert got["span"] == ["span 2"], f"the flag spans the tyre and fuel columns: {got}"
    assert any("dd-flag" in c for c in got["bare"]), f"with both switches off the flag stays: {got}"


def t_a_finished_car_keeps_flag_and_gap_after_it_parks_or_leaves():
    got = _tower(f"""
      showFuel = true; showTyre = true;
      return ["box", "no_data", "dc"].map(function (s) {{
        const e = Object.assign({{}}, {json.dumps(CAR)}, {{finished: true, status: s}});
        const r = row(e, 1, column(e, 1), "P1");
        return {{value: column(e, 1), row: r.className, cls: classes(r)}};
      }});""")
    if got is None:
        return
    for g in got:
        assert g["value"] == "+1.4", f"the final gap, not the status: {got}"
        assert any("dd-flag" in c for c in g["cls"]), f"the flag stays: {got}"
        assert g["row"] == "dd-row", f"a finished row is neither dimmed nor boxed: {got}"


def t_estimated_rows_keep_their_gap_and_tag():
    got = _tower(f"""
      const e = Object.assign({{}}, {json.dumps(CAR)}, {{status: "est", finished: true}});
      return [0, 1].map(function (k) {{ Date.now = () => k * CYCLE_MS + 1; return column(e, 1); }});""")
    if got is None:
        return
    assert got == ["+1.4", "+1.4"], f"an estimate never cycles: {got}"


def t_the_public_page_flags_a_finished_row_with_its_gap():
    with open(TIMING, encoding="utf-8") as fh:
        page = fh.read()
    assert 'e.finished ? "Finished"' not in page, "the public page shows the flag, not the word"
    assert "function finishedGap(" in page and "chequer" in page, \
        "a finished row's gap cell carries the chequered flag"
    gap = page[page.index("gap: cell(\"gap\""):page.index("int: cell(")]
    assert gap.index("e.finished ? finishedGap") < gap.index("STATUS[e.status]"), \
        "a finish is final: the flag and gap stay when the car parks or leaves"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

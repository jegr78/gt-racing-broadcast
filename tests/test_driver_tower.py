#!/usr/bin/env python3
"""The OBS timing tower (src/obs/drivers.html) and the public timing page's finish:
the value column's cycle, the fuel column and the chequered flag. The JS checks run
under node when it is installed (skipped otherwise).
Run: python3 tests/test_driver_tower.py"""
import json
import os
import re
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
    return re.findall(r"<script>([\s\S]*?)</script>", html)[-1]


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


def t_the_value_column_cycles_gap_and_last_lap_only():
    got = _tower(f"""
      const e = {json.dumps(CAR)};
      return [0, 1, 2, 3].map(function (k) {{
        Date.now = () => k * CYCLE_MS + 1;
        showFuel = true;
        return column(e, 1);
      }});""")
    if got is None:
        return
    assert got == ["+1.4", "1:58.412", "+1.4", "1:58.412"], \
        f"two phases, gap then last lap, fuel never in the value column: {got}"


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
      return [56.4, 10.0, 10.4, 7.2].map(function (f) {
        return classes(fuelCell({fuel_l: f, status: "racing"})).join(" ");
      });""")
    assert ["low" in c.split() for c in low] == [False, True, False, True], \
        f"amber at or below 10 %: {low}"


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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

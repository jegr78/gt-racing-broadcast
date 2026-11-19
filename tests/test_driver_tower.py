#!/usr/bin/env python3
"""The OBS timing tower (src/obs/drivers.html) and the public timing page's finish:
the value column's cycle, the fresh lap time, the fuel column and the chequered flag. The JS checks run
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
          classList: {add() {}, remove() {}, toggle() {}}, remove() { this.removed = true; },
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
       "finished": False, "compound": "RM", "pit_stops": 1, "brand": "bmw", "brand_name": "BMW"}


def t_the_value_column_cycles_gap_and_interval():
    got = _tower(f"""
      const e = Object.assign({json.dumps(CAR)}, {{interval_s: 0.52}});
      return [0, 1, 2].map(function (k) {{
        Date.now = () => k * CYCLE_MS + 1;
        return [column(e, 1), column(e, 0), phaseLabel()];
      }});""")
    if got is None:
        return
    assert [g[0] for g in got] == ["+1.4", "+0.5", "+1.4"], \
        f"two phases, gap to the leader and interval to the car ahead: {got}"
    assert [g[1] for g in got] == ["Leader"] * 3, f"the leader reads Leader in both phases: {got}"
    assert [g[2] for g in got] == ["GAP", "INT", "GAP"], f"the header names the phase: {got}"


# Feeds payloads behind a fixed leader to renderRace at the given clock and reads each
# following row's value cell.
_RACE = """
  const LEAD = {pos: 1, entry: "Lead #1", status: "racing", last_lap_ms: 118000};
  function race(t, entries, extra) {
    Date.now = () => t;
    renderRace(Object.assign({mode: "race", entries: [LEAD].concat(entries)}, extra || {}));
    return rows.children.slice(1).map(function (r) {
      const v = r.children.filter(c => /dd-val/.test(c.className))[0];
      return {text: text(v), fl: / fl$/.test(v.className)};
    });
  }
"""


def _lap(**kw):
    return dict(CAR, **kw)


def t_a_new_lap_shows_for_the_league_seconds_then_the_gap():
    got = _tower(_RACE + f"""
      lastLapS = 5;
      const a = {json.dumps(_lap())}, b = {json.dumps(_lap(last_lap_ms=117000))};
      return [race(1000, [a]), race(2000, [b]), race(6999, [b]), race(7001, [b])].map(r => r[0].text);""")
    if got is None:
        return
    assert got == ["+1.4", "1:57.000", "1:57.000", "+1.4"], \
        f"a completed lap replaces the gap for five seconds, then the gap returns: {got}"


def t_the_lap_time_follows_the_league_setting():
    got = _tower(_RACE + f"""
      lastLapS = 2;
      const a = {json.dumps(_lap())}, b = {json.dumps(_lap(last_lap_ms=117000))};
      race(1000, [a]); race(2000, [b]);
      return [race(3999, [b])[0].text, race(4001, [b])[0].text];""")
    if got is None:
        return
    assert got == ["1:57.000", "+1.4"], f"two seconds when the league says two: {got}"


def t_a_page_load_does_not_flash_the_laps_already_set():
    got = _tower(_RACE + f"""
      const a = {json.dumps(_lap())}, late = {json.dumps(_lap(entry="Team C #3", number="3", pos=3))};
      const first = race(1000, [a]).map(r => r.text);
      const joined = race(2000, [a, late]).map(r => r.text);
      return [first, joined];""")
    if got is None:
        return
    assert got[0] == ["+1.4"], f"the first payload counts as already shown: {got}"
    assert got[1][1] == "+1.4", f"an entry the page has not seen does not flash either: {got}"


def t_no_lap_time_means_nothing_to_show():
    got = _tower(_RACE + f"""
      const a = {json.dumps(_lap())}, cleared = {json.dumps(_lap(last_lap_ms=None))};
      race(1000, [a]);
      return [race(2000, [cleared])[0].text, race(3000, [a])[0].text];""")
    if got is None:
        return
    assert got[0] == "+1.4", f"a cleared lap time shows no lap: {got}"
    assert got[1] == "1:58.412", f"a lap after a cleared one is new again: {got}"


def t_the_fastest_lap_shows_purple_only_while_it_shows():
    got = _tower(_RACE + f"""
      lastLapS = 5;
      const fl = {{fastest: {{entry: "Team B #2", driver: "Ann Lind", team: "Team B", ms: 117000}}}};
      const a = {json.dumps(_lap())}, b = {json.dumps(_lap(last_lap_ms=117000))};
      race(1000, [a], fl);
      return [race(2000, [b], fl)[0], race(8000, [b], fl)[0]];""")
    if got is None:
        return
    assert got[0] == {"text": "1:57.000", "fl": True}, f"the race's fastest lap shows purple: {got}"
    assert got[1] == {"text": "+1.4", "fl": False}, f"the gap after it is never purple: {got}"


def t_status_rows_keep_their_value_when_a_lap_lands():
    got = _tower(_RACE + f"""
      const out = {{}};
      for (const [k, extra] of [["est", {{status: "est"}}], ["box", {{status: "box"}}],
                                ["dc", {{status: "dc"}}], ["red", {{status: "red_flag"}}],
                                ["done", {{finished: true}}]]) {{
        lastLapS = 5;
        const a = Object.assign({json.dumps(_lap())}, {{entry: k}});
        const fl = {{fastest: {{entry: k, ms: 117000}}}};
        race(1000, [a], fl);
        const cell = race(2000, [Object.assign({{}}, a, extra, {{last_lap_ms: 117000}})], fl)[0];
        out[k] = cell.text;
        out.purple = (out.purple || []).concat(cell.fl ? [k] : []);
        out[k + "_after"] = race(3000, [Object.assign({{}}, a, {{last_lap_ms: 117000}})], fl)[0].text;
      }}
      return out;""")
    if got is None:
        return
    assert got["est"] == "+1.4EST" and got["box"] == "Box" and got["dc"] == "DC", got
    assert got["red"] == "Red flag" and got["done"] == "+1.4", got
    assert got["purple"] == [], f"a status or finished row never tints its value purple: {got}"
    assert got["box_after"] == "1:57.000", \
        f"the memory follows every row, so the lap shows out of the box within its seconds: {got}"


def t_the_page_takes_the_seconds_from_the_payload():
    got = _run(_DOM + _script(TOWER) + """
      ;(async function () {
        const out = [];
        for (const show of [{last_lap_s: 9}, {}]) {
          fetch = () => Promise.resolve({ok: true, json: () => Promise.resolve(
            {visible: true, mode: "race", entries: [], show: show})});
          await poll();
          out.push(lastLapS);
        }
        return out;
      })().then(r => console.log(JSON.stringify(r)));""")
    if got is not None:
        assert got == [9, 5], f"the league's seconds from the dashboard payload, else five: {got}"


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
        order = [r.find(f'class="{c}') for c in ("dd-pos", "dd-id", "dd-brand", "dd-num", "dd-name",
                                                  "dd-tyre", "dd-fuel", "dd-val", "dd-pit")]
        assert -1 not in order and order == sorted(order), \
            f"each sample row the builder draws has the live columns in order: {r}"
        assert '<img src="/drivers/brand/' in r, f"the builder canvas shows a logo: {r}"

def t_the_logo_sits_between_position_and_number_and_hides_when_missing():
    got = _tower(f"""
      const r = row({json.dumps(CAR)}, 1, "+1.4", "P1");
      const id = r.children[1];
      const img = id.children[0].children[0];
      const out = {{second: id.className, inner: id.children.map(c => c.className), src: img.attrs.src || img.src,
                    alt: img.alt}};
      img.onerror();
      out.removed = !!img.removed;
      const again = row({json.dumps(CAR)}, 1, "+1.4", "P1").children[1].children[0];
      out.retried = again.children.length;
      const none = row(Object.assign({{}}, {json.dumps(CAR)}, {{brand: ""}}), 1, "", "P1").children[1].children[0];
      out.empty = none.children.length;
      return out;""")
    if got is None:
        return
    assert got["second"] == "dd-id" and got["inner"] == ["dd-brand", "dd-num"], \
        f"the logo sits between position and number: {got}"
    assert got["src"] == "/drivers/brand/bmw" and got["alt"] == "BMW", got
    assert got["removed"] and got["retried"] == 0, f"a missing logo leaves an empty cell, no retry each second: {got}"
    assert got["empty"] == 0, f"no brand, no image: {got}"


def t_the_public_page_has_a_foldable_brand_column():
    with open(TIMING, encoding="utf-8") as fh:
        page = fh.read()
    assert '["pos", "Pos"], ["brand", ""], ["num", "#"]' in page, "the logo sits between position and number"
    assert 'const OPTIONAL = [["brand", "Brand"]' in page, "the reader can fold the logos away"
    assert "img.remove()" in page and "logoFailed" in page, "a missing logo shows nothing and waits a minute"
    assert "HOSTED ? hostedLogos[e.brand]" in page, "the hosted page takes logos from its socket, never per request"


def t_the_desks_and_the_cockpit_show_logos_through_the_console_route():
    for rel in (("console", "driver-desk.js"), ("cockpit", "cockpit.html")):
        with open(os.path.join(ROOT, "src", *rel), encoding="utf-8") as fh:
            src = fh.read()
        assert re.search(r"RC_API\(['\"]/drivers/brand/", src), f"{rel[-1]}: the logo goes through /console like its data"
        at = re.search(r"['\"]/drivers/brand/", src).start()
        assert ".remove()" in src[at - 400:at + 200], f"{rel[-1]}: a missing logo shows nothing"


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


def t_a_car_without_a_sender_reads_no_data_and_a_retired_car_dnf():
    got = _tower(f"""
      showFuel = true; showTyre = true;
      return ["no_sender", "retired"].map(function (s) {{
        const e = Object.assign({{}}, {json.dumps(CAR)}, {{status: s, pos: 5}});
        laps[e.entry] = {{ms: e.last_lap_ms, until: Date.now() + 9999}};
        const value = column(e, 4);
        const r = row(e, 4, value, "P0");
        return {{value: value, row: r.className, fuel: text(fuelCell(e)),
                 tyre: flat(r).filter(c => /dd-tyre/.test(c.className)).map(c => c.textContent)}};
      }});""")
    if got is None:
        return
    nosender, dnf = got
    assert nosender["value"] == "No data" and "off" in nosender["row"], f"no sender, no gap: {nosender}"
    assert dnf["value"] == "DNF" and "dnf" in dnf["row"], f"a retired car reads DNF: {dnf}"
    assert nosender["fuel"] == "" and dnf["fuel"] == "", f"neither shows fuel: {got}"
    assert dnf["tyre"] == [""], f"a retired car shows no compound: {dnf}"


def t_the_public_page_and_the_cockpit_name_no_data_and_dnf():
    with open(TIMING, encoding="utf-8") as fh:
        page = fh.read()
    with open(os.path.join(ROOT, "src", "cockpit", "cockpit.html"), encoding="utf-8") as fh:
        cockpit = fh.read()
    for name, src in (("timing", page), ("cockpit", cockpit)):
        status = re.search(r"STATUS = \{([^}]*)\}", src).group(1)
        assert re.search(r"no_sender: ['\"]No data['\"]", status) and re.search(r"retired: ['\"]DNF['\"]", status), \
            f"{name}: {status}"
    for rel in (("console", "driver-desk.js"), ("console", "race-snapshot.html")):
        with open(os.path.join(ROOT, "src", *rel), encoding="utf-8") as fh:
            src = fh.read()
        status = src[src.index("var STATUS = {"):src.index("};", src.index("var STATUS = {"))]
        assert 'no_sender: ["No sender"' in status and 'retired: ["DNF"' in status, f"{rel[-1]}: {status}"


def t_the_public_page_flags_a_finished_row_with_its_gap():
    with open(TIMING, encoding="utf-8") as fh:
        page = fh.read()
    assert 'e.finished ? "Finished"' not in page, "the public page shows the flag, not the word"
    assert "function finishedGap(" in page and "chequer" in page, \
        "a finished row's gap cell carries the chequered flag"
    gap = page[page.index("gap: cell(\"gap\""):page.index("int: cell(")]
    assert gap.index("e.finished ? finishedGap") < gap.index("STATUS[e.status]"), \
        "a finish is final: the flag and gap stay when the car parks or leaves"


def _function(path, name):
    """The source of one top-level JS function in a page or script, by brace matching."""
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    start = src.index("function " + name + "(")
    depth, i = 0, src.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        i += 1
        if depth == 0:
            return src[start:i]


NAME_PAGES = [os.path.join(ROOT, "src", *rel) for rel in (
    ("console", "timing.html"), ("console", "race-snapshot.html"), ("console", "driver-desk.js"),
    ("cockpit", "cockpit.html"))]


def t_every_page_shortens_a_long_name_the_same_way():
    cases = [["Lena Hart", 14], ["Alexander Fitzgerald", 14], ["Alexander Fitzgerald-Smithson", 14],
             ["xX_SpeedDemon_Xx", 14], ["Anna Maria Schmidt", 16], ["", 14], ["Ann", 3]]
    want = ["Lena Hart", "A. Fitzgerald", "A. Fitzgerald\u2026", "xX_SpeedDemon\u2026", "A. Maria Schmidt", "", "Ann"]
    for path in NAME_PAGES:
        got = _run(_function(path, "fitName") + "\n;console.log(JSON.stringify("
                   + json.dumps(cases) + ".map(c => fitName(c[0], c[1]))));")
        if got is None:
            return
        assert got == want, f"{os.path.basename(path)}: {got}"


def t_the_tower_only_cuts_the_surname():
    got = _tower("""return [surname("Lena Hart"), surname("Alexander Fitzgerald-Smithson"),
                            surname("xX_SpeedDemon_Xx")];""")
    if got is None:
        return
    assert got == ["HART", "FITZGER\u2026", "XX_SPEE\u2026"], got


def t_the_pages_show_names_through_the_rule():
    for path, uses in ((NAME_PAGES[0], ("fitName(e.driver", "fitName(fl.driver")),
                       (NAME_PAGES[1], ("fitName(e.driver",)),
                       (NAME_PAGES[2], ("fitName(e.driver", "fitName(fl.driver", "fitName(ev.driver",
                                        "fitName(s.name")),
                       (NAME_PAGES[3], ("fitName(e.driver", "fitName(fl.driver"))):
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for use in uses:
            assert use in src, f"{os.path.basename(path)} shows names through fitName: {use}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

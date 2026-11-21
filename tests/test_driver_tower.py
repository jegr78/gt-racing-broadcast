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
    assert [g[2] for g in got] == ["GAP TO LEADER", "INTERVAL", "GAP TO LEADER"], f"the header names the phase: {got}"


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
    sample = html[html.index('<div id="dd-rows"'):html.index('<div id="drivers-fastest"')]
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


def t_the_tower_shows_the_surname_and_leaves_the_cut_to_css():
    got = _tower("""return [surname("Lena Hart"), surname("Alexander Fitzgerald-Smithson"),
                            surname("xX_SpeedDemon_Xx")];""")
    if got is None:
        return
    assert got == ["HART", "FITZGERALD-SMITHSON", "XX_SPEEDDEMON_XX"], got
    with open(TOWER, encoding="utf-8") as fh:
        css = fh.read()
    assert re.search(r"\.dd-driver \{[^}]*text-overflow: ellipsis", css), "the column cuts what does not fit"


def t_the_header_badge_follows_the_value_column():
    got = _tower(_RACE + f"""
      const badge = document.getElementById("dd-phase");
      const out = [0, 1].map(function (k) {{
        race(k * CYCLE_MS + 1, [{json.dumps(CAR)}]);
        return [badge.textContent, badge.attrs["data-phase"]];
      }});
      [0, 1].forEach(function (k) {{
        Date.now = () => k * CYCLE_MS + 1;
        renderQualifying({{mode: "qualifying", qualifying: []}});
        out.push([badge.textContent, badge.attrs["data-phase"]]);
      }});
      return out;""")
    if got is None:
        return
    assert got == [["GAP TO LEADER", "gap"], ["INTERVAL", "interval"], ["GAP TO P1", "gap"], ["INTERVAL", "interval"]], \
        f"the badge names the column's phase for the override CSS, in qualifying the gap to P1: {got}"


def t_the_header_badge_is_readable_on_stream():
    with open(TOWER, encoding="utf-8") as fh:
        html = fh.read()
    rule = re.search(r"#dd-phase \{([^}]*)\}", html).group(1)
    size = int(re.search(r"font-size: (\d+)px", rule).group(1))
    assert size >= 14, f"the badge is at least as large as the 14 px session line: {rule}"
    assert "background" in rule, f"the badge is a filled pill, not dim corner text: {rule}"
    assert '<span id="dd-phase" data-phase="gap">GAP TO LEADER</span>' in html, "the builder sample shows the full badge"


def t_the_interval_phase_falls_back_to_the_gap():
    got = _tower(f"""
      Date.now = () => CYCLE_MS + 1;
      const e = Object.assign({{}}, {json.dumps(CAR)}, {{interval_s: null, gap_s: 3.24}});
      return [phaseLabel(), column(e, 1)];""")
    if got is None:
        return
    assert got == ["INTERVAL", "+3.2"], f"a running car without an interval shows its gap: {got}"


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


def _cars(cls, n, start=1):
    return [dict(CAR, entry=f"{cls} #{i}", number=str(i), pos=start + i - 1, **{"class": cls},
                 class_pos=i, class_gap_s=float(i - 1), class_interval_s=1.0 if i > 1 else None,
                 class_lapped=0, class_color="#c00")
            for i in range(1, n + 1)]


H = {"name": "H", "color": "#c00", "lap": 7, "laps": 20, "fastest": None}
G = {"name": "G", "color": "#0a0", "lap": 6, "laps": None, "fastest": None}


def _page_shape(book):
    return [["head:" + it["header"]["name"] if "header" in it else it["entry"] for it in page] for page in book]


def t_classes_fit_on_one_page():
    entries = _cars("H", 6) + _cars("G", 6, 7)
    got = _tower(f"return pages({json.dumps(entries)}, {json.dumps([H, G])}, DEFAULT_ROWS);")
    if got is None:
        return
    assert _page_shape(got) == [["head:H"] + [f"H #{i}" for i in range(1, 7)]
                                + ["head:G"] + [f"G #{i}" for i in range(1, 7)]], \
        f"two classes that fit share one page, each under its header: {_page_shape(got)}"


def t_classes_page_when_they_do_not_fit():
    entries = _cars("H", 10) + _cars("G", 10, 11)
    got = _tower(f"return pages({json.dumps(entries)}, {json.dumps([H, G])}, DEFAULT_ROWS);")
    if got is None:
        return
    assert _page_shape(got) == [["head:H"] + [f"H #{i}" for i in range(1, 11)],
                                ["head:G"] + [f"G #{i}" for i in range(1, 11)]], \
        f"one class per page, header first: {_page_shape(got)}"


def t_a_class_larger_than_a_page_is_cut_at_the_page():
    entries = _cars("H", 2) + _cars("G", 20, 3)
    got = _tower(f"return pages({json.dumps(entries)}, {json.dumps([H, G])}, DEFAULT_ROWS);")
    if got is None:
        return
    shape = _page_shape(got)
    assert len(shape) == 2 and len(shape[1]) == 15, f"the header counts toward the default 15 rows: {shape}"
    assert shape[1] == ["head:G"] + [f"G #{i}" for i in range(1, 15)], shape


def _px(css, sel, prop):
    return int(re.search(sel + r" \{[^}]*?" + prop + r": (\d+)px", css).group(1))


def _const(html, name):
    return int(re.search(rf"const {name} = (\d+);", html).group(1))


def t_an_untouched_builder_save_leaves_the_tower_without_a_height():
    with open(TOWER, encoding="utf-8") as fh:
        html = fh.read()
    sample = html[html.index('<div id="dd-rows">'):html.index('<div id="drivers-fastest"')]
    canvas = sample.count('<div class="dd-row">') * _px(html, r"\.dd-row", "height") \
        + 2 * _px(html, "#drivers-tower", "padding")
    assert canvas == _const(html, "LEGACY_PX"), \
        f"the builder saves the canvas height {canvas}, which the page reads as no height of its own"


# The default tower measured empty: top 136, 4 px padding, 40 px rows, the 44 px fastest lap box below.
AUTO = {"box": 8, "inner": 0, "top": 136, "rowH": 40, "below": 60}


def t_the_default_tower_grows_down_the_page():
    with open(TOWER, encoding="utf-8") as fh:
        html = fh.read()
    page_bottom, gap = _const(html, "PAGE_BOTTOM"), _const(html, "FL_GAP")
    assert (page_bottom, gap) == (1016, 16), "64 px margin at the bottom as at the top, 16 px to the fastest lap box"
    got = _tower(f"""
      const m = {json.dumps(AUTO)};
      return [capacity(m), capacity(Object.assign({{}}, m, {{below: 0}})),
              capacity(Object.assign({{}}, m, {{box: 128, inner: 120}})),
              capacity(Object.assign({{}}, m, {{top: 800}}))];""")
    if got is None:
        return
    assert got[0] == 20, f"20 rows end at 944, the fastest lap box at 960 to 1004 stays above 1016: {got}"
    assert 136 + 8 + 20 * 40 + gap + 44 <= page_bottom < 136 + 8 + 21 * 40 + gap + 44
    assert got[1] == 21, f"with the fastest lap box elsewhere the tower reaches the bottom margin: {got}"
    assert got[2] == 20, f"a saved height of the old 128 px canvas is no height of its own: {got}"
    assert got[3] == 5, f"low on the page the tower keeps the floor of 5 rows: {got}"


def t_a_set_tower_height_holds_the_rows_it_fits():
    got = _tower(f"""
      const m = {json.dumps(AUTO)};
      const set = (box, rowH) => capacity(Object.assign({{}}, m, {{box: box, inner: box - 8, rowH: rowH || 40}}));
      return [set(129), set(300), set(608), set(808), set(608, 30), set(608, 0)];""")
    if got is None:
        return
    assert got[:4] == [5, 7, 15, 20], f"a height over 128 px holds the whole rows it fits, at least 5: {got}"
    assert got[4] == 20 and got[5] == 15, f"the row height comes from the page, unmeasurable is 15: {got}"


def t_the_fastest_lap_box_follows_only_from_under_the_tower():
    tower = {"left": 48, "top": 136, "width": 500}
    got = _tower(f"""
      const t = {json.dumps(tower)};
      return [follows(t, {{left: 48, top: 760, width: 500}}), follows(t, {{left: 300, top: 900, width: 500}}),
              follows(t, {{left: 1400, top: 760, width: 400}}), follows(t, {{left: 48, top: 40, width: 500}})];""")
    if got is None:
        return
    assert got == [True, True, False, False], f"only a box below the tower, in its column, follows it: {got}"


def t_more_classes_share_a_page_in_a_taller_box():
    entries = _cars("H", 9) + _cars("G", 9, 10)
    got = _tower(f"return [pages({json.dumps(entries)}, {json.dumps([H, G])}, 20), "
                 f"pages({json.dumps(entries)}, {json.dumps([H, G])}, 5)];")
    if got is None:
        return
    tall, short = (_page_shape(b) for b in got)
    assert len(tall) == 1 and len(tall[0]) == 20, f"20 rows fit both classes with their headers: {tall}"
    assert [len(p) for p in short] == [5, 5], f"a 5-row box pages each class, cut at 5: {short}"


# Gives the stub DOM the measurements of a tower whose own (border-box) height is `box`,
# with the fastest lap box at its default spot.
_BOX = """
  function measured(box) {
    const tower = document.getElementById("drivers-tower"), fl = document.getElementById("drivers-fastest");
    globalThis.getComputedStyle = n => n === tower ? {paddingTop: "4px", paddingBottom: "4px"} : {height: "40px"};
    Object.assign(tower, {offsetHeight: box, clientHeight: box, offsetTop: 136, offsetLeft: 48, offsetWidth: 500,
                          getClientRects: () => [1]});
    Object.assign(fl, {offsetTop: 760, offsetLeft: 48, offsetWidth: 500, offsetHeight: 44});
  }
"""


def t_the_rendered_tower_follows_its_box():
    entries = [dict(CAR, entry=f"T #{i}", number=str(i), pos=i, gap_s=float(i)) for i in range(1, 31)]
    quali = [{"pos": i, "entry": f"T #{i}", "best_ms": 100000 + i, "gap_ms": i} for i in range(1, 31)]
    got = _tower(_BOX + f"""
      const out = [];
      const tower = document.getElementById("drivers-tower"), fl = document.getElementById("drivers-fastest");
      [null, 8, 128, 808, 300].forEach(function (box) {{
        if (box !== null) measured(box);
        renderRace({{mode: "race", entries: {json.dumps(entries)}}});
        const race = [rows.children.length, tower.style.height || "", fl.style.top || ""];
        renderQualifying({{mode: "qualifying", qualifying: {json.dumps(quali)}}});
        out.push(race.concat([rows.children.length]));
      }});
      return out;""")
    if got is None:
        return
    assert got[0] == [15, "", "", 15], f"unmeasured (hidden), the tower shows 15 and moves nothing: {got}"
    assert got[1][0] == 20 and got[1][3] == 20 and got[1][1] == "auto", f"no height: rows down the page: {got}"
    assert got[1][2] == f"{136 + 8 + 16}px", f"the fastest lap box sits 16 px under the tower: {got}"
    assert got[2][:2] == [20, "auto"], f"the old canvas height of 128 px is dropped for the default: {got}"
    assert got[3] == [20, "", "", 20], f"a set height of 808 px holds 20 rows and the box stays put: {got}"
    assert got[4] == [7, "", "", 7], f"a set height of 300 px holds 7: {got}"


# Renders a class-mode payload at clock t and reads each row: a header's name and lap, a car's
# position and value.
_CLASS_RACE = """
  function shown(t, d) {
    Date.now = () => t;
    renderRace(Object.assign({mode: "race"}, d));
    return rows.children.map(function (r) {
      if (/dd-class/.test(r.className)) {
        return {head: r.children[0].textContent, lap: r.children[1].textContent,
                kids: r.children[0].children.length, border: r.style.borderLeftColor};
      }
      const v = r.children.filter(c => /dd-val/.test(c.className))[0];
      return {pos: r.children[0].textContent, value: text(v), fl: / fl$/.test(v.className)};
    });
  }
"""


def t_class_rows_show_class_position_and_gap():
    lead = dict(CAR, entry="H #1", pos=1, **{"class": "H"}, class_pos=1, class_gap_s=0, class_interval_s=None,
                class_lapped=0, lapped=0, gap_s=0)
    g1 = dict(CAR, entry="G #1", pos=7, **{"class": "G"}, class_pos=1, class_gap_s=0, class_interval_s=None,
              class_lapped=0, lapped=1, gap_s=95.0)
    g2 = dict(CAR, entry="G #2", pos=8, **{"class": "G"}, class_pos=2, class_gap_s=2.34, class_interval_s=2.34,
              class_lapped=0, lapped=1, gap_s=97.3, interval_s=2.3)
    g3 = dict(g2, entry="G #3", pos=9, class_pos=3, class_gap_s=4.56, class_interval_s=2.22, finished=True)
    g4 = dict(g2, entry="G #4", pos=10, class_pos=4, class_gap_s=6.78, class_interval_s=2.22, status="est")
    d = {"entries": [lead, g1, g2, g3, g4], "classes": [H, G]}
    got = _tower(_CLASS_RACE + f"""
      const d = {json.dumps(d)};
      return [shown(1, d), shown(CYCLE_MS + 1, d)];""")
    if got is None:
        return
    gap, inter = got
    assert gap[2] == {"head": "G", "lap": "LAP 6", "kids": 0, "border": "#0a0"}, \
        f"the header names the class, its lap without a count, in its colour: {gap}"
    assert gap[0]["lap"] == "LAP 7 / 20", f"a class with a lap count reads it: {gap}"
    assert gap[3]["pos"] == "1" and gap[3]["value"] == "Leader", f"the class leader, not P7 at +1 L: {gap}"
    assert [r["pos"] for r in gap[4:]] == ["2", "3", "4"], f"positions within the class: {gap}"
    assert [r["value"] for r in gap[4:]] == ["+2.3", "+4.6", "+6.8EST"], \
        f"class gaps, never the overall lap down, also finished and estimated: {gap}"
    assert [r["value"] for r in inter[3:]] == ["Leader", "+2.3", "+4.6", "+6.8EST"], \
        f"INT reads the class interval: {inter}"


def t_a_class_row_still_flashes_its_new_lap():
    a = _cars("H", 1) + _cars("G", 2, 2)
    b = [dict(x) for x in a]
    b[2]["last_lap_ms"] = 117000
    fl = {"entry": "G #2", "driver": "Ann Lind", "team": "Team B", "ms": 117000}
    gf = dict(G, fastest=fl)
    got = _tower(_CLASS_RACE + f"""
      lastLapS = 5;
      shown(1000, {{entries: {json.dumps(a)}, classes: {json.dumps([H, gf])}}});
      return shown(2000, {{entries: {json.dumps(b)}, classes: {json.dumps([H, gf])}}});""")
    if got is None:
        return
    assert got[4] == {"pos": "2", "value": "1:57.000", "fl": True}, \
        f"a new lap shows in a class row, purple when it is its class's fastest: {got}"


def t_purple_follows_the_class_fastest_lap():
    a = _cars("H", 1) + _cars("G", 2, 2)
    b = [dict(x) for x in a]
    b[0]["last_lap_ms"], b[2]["last_lap_ms"] = 101000, 117000
    hf = dict(H, fastest={"entry": "H #1", "driver": "Lena Hart", "team": "Team H", "ms": 101000})
    gf = dict(G, fastest={"entry": "G #2", "driver": "Ann Lind", "team": "Team B", "ms": 117000})
    got = _tower(_CLASS_RACE + f"""
      lastLapS = 5;
      shown(1000, {{entries: {json.dumps(a)}, classes: {json.dumps([hf, gf])}}});
      return shown(2000, {{entries: {json.dumps(b)}, classes: {json.dumps([hf, gf])},
                           fastest: {json.dumps(hf["fastest"])}}});""")
    if got is None:
        return
    assert [got[1]["fl"], got[4]["fl"]] == [True, True], \
        f"each class's fastest lap shows purple, not only the overall one: {got}"
    got = _tower(_CLASS_RACE + f"""
      lastLapS = 5;
      shown(1000, {{entries: {json.dumps(a)}, classes: {json.dumps([H, G])}}});
      return shown(2000, {{entries: {json.dumps(b)}, classes: {json.dumps([H, G])},
                           fastest: {json.dumps(gf["fastest"])}}});""")
    if got is None:
        return
    assert got[4]["fl"] is False, f"in class mode the overall lap alone never paints a class row: {got}"


def t_qualifying_after_a_class_race_drops_the_class_look():
    d = {"entries": _cars("H", 3) + _cars("G", 3, 4),
         "classes": [dict(H, fastest={"entry": "H #1", "driver": "Lena Hart", "team": "Team H", "ms": 101000}), G]}
    quali = [dict(CAR, entry=f"Q #{i}", pos=i, best_ms=100000 + i, gap_ms=i) for i in range(1, 4)]
    got = _tower(_CLASS_RACE + f"""
      let grouped = null;
      rows.classList.toggle = (c, on) => {{ if (c === "grouped") grouped = !!on; }};
      rows.classList.remove = (c) => {{ if (c === "grouped") grouped = false; }};
      shown(1, {json.dumps(d)});
      const before = [grouped, document.getElementById("dd-fl-class").textContent];
      renderQualifying({{mode: "qualifying", qualifying: {json.dumps(quali)}}});
      const label = document.getElementById("dd-fl-class");
      return [before, grouped, label.textContent, label.style.color];""")
    if got is None:
        return
    before, grouped, label, color = got
    assert before == [True, "H"], f"the class race is grouped with a class label: {before}"
    assert grouped is False, "qualifying keeps the position stripes after a class race"
    assert label == "" and color == "", f"qualifying carries no class label: {got}"


def t_a_class_page_stays_for_gap_and_interval():
    d = {"entries": _cars("H", 10) + _cars("G", 10, 11), "classes": [H, G]}
    got = _tower(_CLASS_RACE + f"""
      const d = {json.dumps(d)};
      return [0, 1, 2, 3, 4].map(function (k) {{
        const r = shown(k * CYCLE_MS + 1, d);
        return [r[0].head, phaseLabel(), r[2].value];
      }});""")
    if got is None:
        return
    gap, it = "GAP TO LEADER", "INTERVAL"
    assert got == [["H", gap, "+1.0"], ["H", it, "+1.0"], ["G", gap, "+1.0"], ["G", it, "+1.0"],
                   ["H", gap, "+1.0"]], f"each class page shows the gap, then the interval, then the next class: {got}"


def t_class_names_are_text():
    bad = dict(G, name="<b>GT3</b>", color="red;background:url(x)")
    entries = [dict(c, **{"class": "<b>GT3</b>"}) for c in _cars("G", 2)]
    got = _tower(_CLASS_RACE + f"""
      return shown(1, {{entries: {json.dumps(entries)}, classes: [{json.dumps(H)}, {json.dumps(bad)}]}})[0];""")
    if got is None:
        return
    assert got["head"] == "<b>GT3</b>" and got["kids"] == 0, f"the class name is plain text: {got}"
    assert "url" not in got["border"], f"a colour that is not one never reaches the style: {got}"


def t_one_class_renders_as_today():
    entries = [dict(CAR, entry=f"T #{i}", number=str(i), pos=i, gap_s=float(i), lapped=0,
                    **{"class": None}, class_pos=None, class_gap_s=None)
               for i in range(1, 21)]
    got = _tower(_CLASS_RACE + f"""
      return [shown(1, {{entries: {json.dumps(entries)}, classes: []}}), shown(1, {{entries: {json.dumps(entries)}}}),
              classes(rows)];""")
    if got is None:
        return
    with_list, without, cls = got
    assert with_list == without, "an empty class list is the tower as before"
    assert [r["pos"] for r in without] == [str(i) for i in range(1, 16)], f"overall positions, 15 rows: {without}"
    assert [r["value"] for r in without[:3]] == ["Leader", "+2.0", "+3.0"], f"overall gaps: {without}"
    assert not any("dd-class" in c for c in cls), f"no class header outside class mode: {cls}"


def t_the_fastest_lap_box_cycles_the_classes():
    hf = dict(H, fastest={"entry": "H #1", "driver": "Lena Hart", "team": "Team H", "ms": 101000})
    gf = dict(G, fastest={"entry": "G #1", "driver": "Ann Lind", "team": "Team G", "ms": 112500})
    d = {"entries": _cars("H", 1) + _cars("G", 1, 2), "classes": [hf, gf],
         "fastest": hf["fastest"]}
    got = _tower(_CLASS_RACE + f"""
      const d = {json.dumps(d)};
      const out = [0, 1, 2].map(function (k) {{
        shown(k * CYCLE_MS + 1, d);
        const label = document.getElementById("dd-fl-class");
        return [label.textContent, label.style.color, document.getElementById("dd-fl-who").textContent,
                document.getElementById("dd-fl-time").textContent];
      }});
      shown(1, {{entries: d.entries, fastest: d.fastest}});
      out.push(document.getElementById("dd-fl-class").textContent);
      return out;""")
    if got is None:
        return
    assert got[:3] == [["H", "#c00", "HART · Team H", "1:41.000"], ["G", "#0a0", "LIND · Team G", "1:52.500"],
                       ["H", "#c00", "HART · Team H", "1:41.000"]], f"one class every 15 s, labelled: {got}"
    assert got[3] == "", f"no class label outside class mode: {got}"


def t_a_class_without_a_fastest_lap_leaves_the_box_to_the_others():
    hf = dict(H, fastest={"entry": "H #1", "driver": "Lena Hart", "team": "Team H", "ms": 101000})
    d = {"entries": _cars("H", 1) + _cars("G", 1, 2), "classes": [hf, G], "fastest": hf["fastest"]}
    got = _tower(_CLASS_RACE + f"""
      const d = {json.dumps(d)};
      const box = document.getElementById("drivers-fastest");
      let hidden = false;
      box.classList.toggle = (c, on) => {{ if (c === "none") hidden = !!on; }};
      return [0, 1].map(function (k) {{
        shown(k * CYCLE_MS + 1, d);
        return [document.getElementById("dd-fl-class").textContent, hidden];
      }});""")
    if got is None:
        return
    assert got == [["H", False], ["H", False]], f"the box never blanks for a class without a lap yet: {got}"


def t_a_paged_tower_shows_the_fastest_lap_of_its_class():
    hf = dict(H, fastest={"entry": "H #1", "driver": "Lena Hart", "team": "Team H", "ms": 101000})
    d = {"entries": _cars("H", 10) + _cars("G", 10, 11), "classes": [hf, G], "fastest": hf["fastest"]}
    got = _tower(_CLASS_RACE + f"""
      const d = {json.dumps(d)};
      const box = document.getElementById("drivers-fastest");
      let hidden = false;
      box.classList.toggle = (c, on) => {{ if (c === "none") hidden = !!on; }};
      return [0, 1, 2, 3].map(function (k) {{
        const r = shown(k * CYCLE_MS + 1, d);
        return [r[0].head, document.getElementById("dd-fl-class").textContent, hidden];
      }});""")
    if got is None:
        return
    assert got == [["H", "H", False], ["H", "H", False], ["G", "G", True], ["G", "G", True]], \
        f"the box follows the page's class and hides while that class has no lap: {got}"


def t_the_stripes_restart_in_each_class():
    d = {"entries": _cars("H", 3) + _cars("G", 3, 4), "classes": [H, G]}
    got = _tower(_CLASS_RACE + f"""
      let grouped = null;
      rows.classList.toggle = (c, on) => {{ if (c === "grouped") grouped = !!on; }};
      shown(1, {json.dumps(d)});
      const alt = rows.children.map(r => / alt\\b/.test(r.className));
      const inClass = grouped;
      shown(1, {{entries: {json.dumps(d["entries"])}, classes: []}});
      return [alt, inClass, grouped, rows.children.map(r => / alt\\b/.test(r.className))];""")
    if got is None:
        return
    alt, in_class, outside, plain = got
    assert alt == [False, False, True, False, False, False, True, False], \
        f"each class starts unstriped under its header: {alt}"
    assert in_class is True and outside is False, f"the grouped stripes only in class mode: {got}"
    assert not any(plain), f"outside class mode the nth-child stripes stay: {plain}"
    with open(TOWER, encoding="utf-8") as fh:
        css = fh.read()
    assert re.search(r"#dd-rows\.grouped \.dd-row:nth-child\(even\) \{ background: none;", css), \
        "the position stripes give way to the class stripes"
    assert re.search(r"#dd-rows\.grouped \.dd-row\.alt \{ background: var\(--dd-row\);", css), \
        "every second car of a class takes the row stripe"


def t_the_map_rings_the_class_and_est_wins():
    got = _tower(f"""
      line = {{points: [[0, 0], [100, 0], [100, 100], [0, 100]]}};
      drawLine();
      const base = {json.dumps(CAR)};
      drawCars([Object.assign({{}}, base, {{entry: "a", x: 10, z: 0, class_color: "#0a0"}}),
                Object.assign({{}}, base, {{entry: "b", x: 50, z: 0, class_color: "#0a0", status: "est"}}),
                Object.assign({{}}, base, {{entry: "c", x: 90, z: 0}})]);
      return els["dd-cars"].children.map(g => g.children[0].attrs).reverse();""")
    if got is None:
        return
    ring, est, plain = got
    assert ring["stroke"] == "#0a0" and ring["stroke-width"] == 0.8, f"a ring in the class colour: {ring}"
    assert est["stroke"] == "#ffb454" and "stroke-dasharray" in est, f"the EST ring keeps its amber: {est}"
    assert plain["stroke"] == "#fff" and plain["stroke-width"] == 0.4, f"no class, the white edge: {plain}"


# Just enough DOM and browser for the public page's two scripts.
_PUBLIC_DOM = r"""
class Node {}
function node(tag) {
  return Object.setPrototypeOf({tag: tag, className: "", textContent: "", style: {}, children: [], dataset: {}, listeners: {},
          hidden: false, value: "",
          get rows() { return this.children; },
          addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); },
          appendChild(c) { this.children.push(c); return c; }, append(...c) { this.children.push(...c); },
          prepend(...c) { this.children.unshift(...c); }, replaceChildren(...c) { this.children = c; },
          remove() {}}, Node.prototype);
}
const els = {timing: Object.assign(node("table"), {tHead: node("thead"), tBodies: [node("tbody")]})};
const document = {getElementById: id => els[id] || (els[id] = node("div")), createElement: node,
                  createTextNode: t => Object.assign(node("#text"), {textContent: String(t)})};
const store = STORE;
const localStorage = {getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }};
const window = {matchMedia: () => ({matches: false})};
const location = {pathname: "/console/timing", search: "?l=abc", href: "https://x/console/timing?l=abc"};
function fetch() { return new Promise(() => {}); }
function setTimeout() {}
function setInterval() {}
function flat(n) { return [n].concat(...(n.children || []).map(flat)); }
function text(n) { return flat(n).map(c => c.textContent).join(""); }
function body() {
  return els.timing.tBodies[0].children.map(function (r) {
    if (r.className === "cls") return {head: text(r), kids: r.children[0].children[0].children.length,
                                       color: r.children[0].children[0].style.color || ""};
    const c = {};
    r.children.forEach(td => { c[td.dataset.col] = text(td); });
    return c;
  });
}
function pick(value) {
  const sel = document.getElementById("class-filter");
  sel.value = value;
  sel.listeners.change.forEach(f => f());
}
"""


def _public(body, store=None):
    with open(TIMING, encoding="utf-8") as fh:
        html = fh.read()
    i = html.index('<script id="timing-logic">') + len('<script id="timing-logic">')
    logic = html[i:html.index("</script>", i)]
    return _run(_PUBLIC_DOM.replace("STORE", json.dumps(store or {})) + logic + _script(TIMING)
                + "\n;console.log(JSON.stringify((function () {" + body + "})()));")


def _public_cars():
    gt3 = [dict(CAR, entry=f"GT3 #{i}", number=str(i), driver=f"Driver {i}", pos=i, gap_s=float(i - 1),
                interval_s=1.0, **{"class": "GT3"}, class_color="#c00", class_pos=i,
                class_gap_s=float(i - 1), class_interval_s=1.0 if i > 1 else None, class_lapped=0)
           for i in (1, 2)]
    gt4 = [dict(CAR, entry=f"GT4 #{i}", number=str(10 + i), driver=f"Driver {10 + i}", pos=2 + i, lapped=1,
                gap_s=90.0 + i, interval_s=2.0, **{"class": "GT4"}, class_color="#0a0", class_pos=i,
                class_gap_s=float(2 * (i - 1)), class_interval_s=2.0 if i > 1 else None, class_lapped=0)
           for i in (1, 2)]
    classes = [{"name": "GT3", "color": "#c00", "lap": 7, "laps": 20,
                "fastest": {"entry": "GT3 #1", "driver": "Lena Hart", "team": "Team H", "ms": 101000}},
               {"name": "GT4", "color": "#0a0", "lap": 6, "laps": 18, "fastest": None}]
    return gt3 + gt4, classes


def t_the_public_page_groups_by_class_and_filters():
    entries, classes = _public_cars()
    bad = dict(classes[1], name="<b>GT4</b>", color="red;background:url(x)")
    odd = [dict(e, **{"class": "<b>GT4</b>"}) if e["class"] == "GT4" else e for e in entries]
    got = _public(f"""
      const d = {{mode: "race", entries: {json.dumps(entries)}, classes: {json.dumps(classes)}}};
      render(d);
      const out = {{all: body(), picker: document.getElementById("classpick").hidden,
                    options: document.getElementById("class-filter").children.map(o => o.textContent)}};
      pick(document.getElementById("class-filter").children[1].value);
      out.gt3 = body();
      out.stored = store["timing-class"];
      out.fastest = text(document.getElementById("fastest"));
      render({{mode: "race", entries: {json.dumps(odd)}, classes: [{json.dumps(classes[0])}, {json.dumps(bad)}]}});
      pick("all");
      out.bad = body().filter(r => r.head !== undefined)[1];
      return out;""")
    if got is None:
        return
    heads = [r for r in got["all"] if "head" in r]
    assert [h["head"] for h in heads] == ["GT3Lap 7 / 20", "GT4Lap 6 / 18"], f"one header per class: {got['all']}"
    assert [h["color"] for h in heads] == ["#c00", "#0a0"], f"each name in its class colour: {heads}"
    cars = [r for r in got["all"] if "head" not in r]
    assert [r["pos"] for r in cars] == ["1", "2", "1", "2"], f"positions within the class: {cars}"
    assert [r["gap"] for r in cars] == ["", "+1.0", "", "+2.0"], f"class gaps, never +1 L overall: {cars}"
    assert [r["int"] for r in cars] == ["", "+1.0", "", "+2.0"], f"class intervals: {cars}"
    assert got["picker"] is False and got["options"] == ["All classes", "GT3", "GT4"], got
    assert [r.get("head", r.get("drv")) for r in got["gt3"]] == ["GT3Lap 7 / 20", "Driver 1Team B", "Driver 2Team B"], \
        f"the GT3 filter leaves only GT3: {got['gt3']}"
    assert got["stored"] == '"GT3"', f"the choice is remembered: {got['stored']}"
    assert got["fastest"].startswith("GT3") and "1:41.000" in got["fastest"], \
        f"the class's fastest lap under its filter: {got['fastest']}"
    assert got["bad"]["head"] == "<b>GT4</b>Lap 6 / 18" and got["bad"]["kids"] == 0, \
        f"a class name is plain text: {got['bad']}"
    assert "url" not in got["bad"]["color"], f"a colour that is not one never reaches the style: {got['bad']}"


def t_the_public_page_remembers_the_class_and_forgets_a_gone_one():
    entries, classes = _public_cars()
    got = _public(f"""
      render({{mode: "race", entries: {json.dumps(entries)}, classes: {json.dumps(classes)}}});
      const kept = body().filter(r => r.head).map(r => r.head);
      render({{mode: "race", entries: {json.dumps(entries)},
               classes: [{json.dumps(classes[0])}, Object.assign({{}}, {json.dumps(classes[1])}, {{name: "GT2"}})]}});
      return [kept, body().filter(r => r.head).length];""", store={"timing-class": '"GT4"'})
    if got is None:
        return
    assert got[0] == ["GT4Lap 6 / 18"], f"a reload keeps the chosen class: {got}"
    assert got[1] == 2, f"a class the race no longer has shows them all: {got}"


def t_the_public_page_works_without_storage():
    entries, classes = _public_cars()
    got = _public(f"""
      localStorage.getItem = localStorage.setItem = () => {{ throw new Error("blocked"); }};
      render({{mode: "race", entries: {json.dumps(entries)}, classes: {json.dumps(classes)}}});
      pick(document.getElementById("class-filter").children[2].value);
      return body().filter(r => r.head).map(r => r.head);""")
    if got is None:
        return
    assert got == ["GT4Lap 6 / 18"], f"a blocked store costs only the memory: {got}"


QUIET = ["est", "no_data", "dc", "no_sender", "retired"]


def t_the_public_page_and_the_cockpit_show_no_fuel_for_a_quiet_car():
    cars = [dict(CAR, entry=f"E #{i}", pos=i + 1, status=s) for i, s in enumerate(["racing"] + QUIET)]
    public = _public(f"""render({{mode: "race", entries: {json.dumps(cars)}}});
      return body().map(r => r.fuel);""")
    if public is None:
        return
    cockpit = _cockpit({"mode": "race", "entries": cars})
    assert public == ["56.4 L", "", "", "", "", ""], f"public page: a quiet car's last fuel reading is stale: {public}"
    assert [r[9] for r in cockpit] == ["56.4 L", "", "", "", "", ""], \
        f"cockpit: a quiet car's last fuel reading is stale: {cockpit}"


def t_the_public_page_renders_one_class_as_today():
    entries, _classes = _public_cars()
    flat_rows = [{k: v for k, v in e.items() if not k.startswith("class")} for e in entries]
    got = _public(f"""
      render({{mode: "race", entries: {json.dumps(flat_rows)}}});
      const before = body();
      render({{mode: "race", entries: {json.dumps(flat_rows)}, classes: []}});
      return [before, body(), document.getElementById("classpick").hidden, text(document.getElementById("fastest"))];""",
                  store={"timing-class": '"GT3"'})
    if got is None:
        return
    before, after, picker, fastest = got
    assert before == after, "an empty class list is the page as before"
    assert not any("head" in r for r in after), f"no class header outside class mode: {after}"
    assert [r["pos"] for r in after] == ["1", "2", "3", "4"], f"overall positions: {after}"
    assert [r["gap"] for r in after] == ["", "+1.0", "+1 L", "+1 L"], f"overall gaps: {after}"
    assert picker is True, "no class filter outside class mode, whatever the store remembers"
    assert fastest == "", fastest



COCKPIT = os.path.join(ROOT, "src", "cockpit", "cockpit.html")


def _cockpit(payload, head=False, probe=None):
    """The cockpit's timing card after one poll of `payload`: each body row's cell texts,
    a class header as {head, color}; with `head` the header line's parts; with `probe`
    the value of that JS expression."""
    with open(COCKPIT, encoding="utf-8") as fh:
        html = fh.read()
    start = html.index("// Driver timing (#1115)")
    block = html[start:html.index("\npollTiming();", start)]
    js = (_PUBLIC_DOM.replace("STORE", "{}") + "window.RC_API = p => p; const $ = id => document.getElementById(id);\n"
          + "const j = async () => (" + json.dumps(payload) + ");\n" + block
          + """\n;(async function () {
      await pollTiming();
      if (__HASPROBE__) return (__PROBE__);
      if (__HEAD__) return els.timingHead.children.map(text);
      return els.timing.tBodies[0].children.map(function (r) {
        if (r.className === "cls") return {head: text(r), color: r.children[0].children[0].style.color || ""};
        return r.children.map(text);
      });
    })().then(r => console.log(JSON.stringify(r)));""").replace("__HEAD__", json.dumps(head)).replace(
        "__HASPROBE__", json.dumps(probe is not None)).replace("__PROBE__", probe or "null")
    return _run(js)


def t_the_cockpit_names_a_qualifying_segment_once():
    got = [_cockpit({"mode": "qualifying", "qualifying": [], "track_name": "Suzuka Circuit", "stint": s}, head=True)
           for s in ("Qualifying", "qualifying 2", "Q1")]
    assert got == [["Qualifying", "Suzuka Circuit"], ["qualifying 2", "Suzuka Circuit"],
                   ["Qualifying", "Suzuka Circuit", "Q1"]], f"a segment that names qualifying shows once: {got}"


def t_the_cockpit_shows_class_position_and_gap():
    entries, classes = _public_cars()
    got = _cockpit({"mode": "race", "entries": entries, "classes": classes})
    if got is None:
        return
    heads = [r for r in got if isinstance(r, dict)]
    assert heads == [{"head": "GT3Lap 7 / 20", "color": "#c00"}, {"head": "GT4Lap 6 / 18", "color": "#0a0"}], \
        f"one header per class in its colour: {got}"
    cars = [r for r in got if isinstance(r, list)]
    assert [r[0] for r in cars] == ["1", "2", "1", "2"], f"class positions: {cars}"
    assert [r[6] for r in cars] == ["", "+1.0", "", "+2.0"], f"class gaps, not +1 L: {cars}"
    assert [r[7] for r in cars] == ["", "+1.0", "", "+2.0"], f"class intervals: {cars}"


def t_the_cockpit_flags_a_finished_row_with_its_final_gap():
    cars = [dict(CAR, entry="A #1", pos=1, gap_s=0.0, finished=True, status="box"),
            dict(CAR, finished=True, status="no_data"), dict(CAR, entry="C #3", pos=3, finished=True, status="dc"),
            dict(CAR, entry="D #4", pos=4, gap_s=3.0, finished=True, status="est")]
    got = _cockpit({"mode": "race", "entries": cars}, probe="""els.timing.tBodies[0].children.map(r => ({
        row: r.className, gap: text(r.children[6]),
        flag: flat(r.children[6]).some(c => /chequer/.test(c.className)),
        est: flat(r.children[6]).some(c => c.className === "est")}))""")
    if got is None:
        return
    assert [g["gap"] for g in got] == ["", "+1.4", "+1.4", "+3.0EST"], f"the final gap, not the status: {got}"
    assert [g["flag"] for g in got] == [True, True, True, False], f"a finish shows the flag, an estimate its tag: {got}"
    assert got[3]["est"], f"an estimate keeps its EST tag: {got}"
    assert got[0]["row"] == "", f"a finished car in the box is not marked as boxed: {got}"


def t_the_cockpit_hides_the_timing_card_until_a_car_has_data():
    def card(payload):
        return _cockpit(payload, probe="els.timingCard.hidden")

    def race(*rows):
        return {"mode": "race", "entries": [dict(CAR, entry=f"E #{i}", pos=i + 1, **{"laps": 0, "finished": False, **r})
                                            for i, r in enumerate(rows)]}
    if card({"mode": "race", "entries": []}) is None:
        return
    quali_row = {k: v for k, v in _q("A #1", 1, 90000, 0, None, 90000).items() if k not in ("status", "finished")}
    got = {
        "empty": card({"mode": "race", "entries": []}),
        "roster only": card(race({"status": "no_sender"}, {"status": "no_sender"})),
        "never sent": card(race({"status": "no_sender"}, {"status": "no_data"})),
        "waiting": card(race({"status": "no_sender"}, {"status": "waiting"})),
        "quiet after laps": card(race({"status": "no_data", "laps": 3}, {"status": "no_sender"})),
        "finished and quiet": card(race({"status": "no_data", "finished": True}, {"status": "no_sender"})),
        "qualifying": card({"mode": "qualifying", "qualifying": [quali_row]}),
    }
    assert got == {"empty": True, "roster only": True, "never sent": True, "waiting": False,
                   "quiet after laps": False, "finished and quiet": False, "qualifying": False}, \
        f"the card shows once a car sent data, and a roster of no-data rows keeps it hidden: {got}"


def t_the_cockpit_renders_one_class_as_today():
    entries, _classes = _public_cars()
    flat_rows = [{k: v for k, v in e.items() if not k.startswith("class")} for e in entries]
    got = _cockpit({"mode": "race", "entries": flat_rows})
    if got is None:
        return
    assert _cockpit({"mode": "race", "entries": flat_rows, "classes": []}) == got, "an empty class list is as before"
    assert all(isinstance(r, list) for r in got), f"no class header outside class mode: {got}"
    assert [r[0] for r in got] == ["1", "2", "3", "4"] and [r[6] for r in got] == ["", "+1.0", "+1 L", "+1 L"], got


# Renders a qualifying payload at clock t and reads each row: a header's name, or a car's
# position, best lap cell and value cell with their colour classes.
_QUALI = """
  function quali(t, d) {
    Date.now = () => t;
    renderQualifying(Object.assign({mode: "qualifying"}, d));
    return rows.children.map(function (r) {
      if (/dd-class/.test(r.className)) return {head: r.children[0].textContent};
      const v = r.children.filter(c => /dd-val/.test(c.className))[0];
      const b = r.children.filter(c => /dd-best/.test(c.className))[0];
      return {pos: r.children[0].textContent, best: b ? b.textContent : null, bestFl: !!b && / fl$/.test(b.className),
              value: text(v), mark: v.className.replace("dd-val", "").trim(),
              pit: r.children.some(c => /dd-pit/.test(c.className))};
    });
  }
"""


def _q(entry, pos, best, gap, interval, last=None, **kw):
    return dict(CAR, entry=entry, number=str(pos), pos=pos, best_ms=best, gap_ms=gap, interval_ms=interval,
                last_lap_ms=last, **kw)


QROWS = [_q("A #1", 1, 90000, 0, None, 90000), _q("B #2", 2, 90400, 400, 400, 91000),
         _q("C #3", 3, 91250, 1250, 850, 91250), _q("D #4", 4, None, None, None)]


def t_qualifying_keeps_the_best_lap_and_cycles_gap_and_interval():
    got = _tower(_QUALI + f"""
      const d = {{qualifying: {json.dumps(QROWS)}, remaining: 754}};
      const out = [quali(1, d)];
      const timed = document.getElementById("dd-lap").textContent;
      out.push(quali(CYCLE_MS + 1, {{qualifying: d.qualifying}}));
      return out.concat([[timed, document.getElementById("dd-lap").textContent]]);""")
    if got is None:
        return
    gap, inter, lap = got
    assert [r["best"] for r in gap] == ["1:30.000", "1:30.400", "1:31.250", ""], \
        f"the best lap is a column of its own, empty without a lap: {gap}"
    assert [r["bestFl"] for r in gap] == [True, False, False, False], f"P1's best lap is purple: {gap}"
    assert [r["value"] for r in gap] == ["Pole", "+0.400", "+1.250", ""], f"P1 reads Pole, then the gap to P1: {gap}"
    assert [r["value"] for r in inter] == ["Pole", "+0.400", "+0.850", ""], f"the interval by best lap: {inter}"
    assert not any(r["pit"] for r in gap), "qualifying has no pit stop column"
    assert lap == ["12:34", "QUALIFYING"], f"the timer takes the lap line, else it names the session: {lap}"


def t_a_qualifying_lap_flashes_purple_green_or_white():
    before = [dict(r, last_lap_ms=None) for r in QROWS]
    got = _tower(_QUALI + f"""
      lastLapS = 5;
      quali(1000, {{qualifying: {json.dumps(before)}}});
      return [quali(2000, {{qualifying: {json.dumps(QROWS)}}}), quali(7001, {{qualifying: {json.dumps(QROWS)}}})];""")
    if got is None:
        return
    now, later = got
    assert [(r["value"], r["mark"]) for r in now[:3]] == [("1:30.000", "fl"), ("1:31.000", ""), ("1:31.250", "pb")], \
        f"P1's new best purple, a personal best green, another lap white: {now}"
    assert [r["value"] for r in later[:3]] == ["Pole", "+0.400", "+1.250"], f"then the gap returns: {later}"


def t_qualifying_ranks_each_class_under_its_header():
    gt3 = {"name": "GT3", "color": "#f00", "fastest": {"entry": "A #1", "driver": "Max", "team": "", "ms": 90000}}
    gt4 = {"name": "GT4", "color": "#0f0", "fastest": {"entry": "B #2", "driver": "Ann", "team": "", "ms": 95000}}
    rows = [_q("A #1", 1, 90000, 0, None, **{"class": "GT3"}, class_pos=1, class_gap_ms=0, class_interval_ms=None),
            _q("C #3", 2, 90700, 700, 700, **{"class": "GT3"}, class_pos=2, class_gap_ms=700, class_interval_ms=700),
            _q("B #2", 3, 95000, 5000, 4300, **{"class": "GT4"}, class_pos=1, class_gap_ms=0,
               class_interval_ms=None),
            _q("D #4", 5, 96000, 6000, 600, **{"class": "GT4"}, class_pos=2, class_gap_ms=1000,
               class_interval_ms=1000)]
    got = _tower(_QUALI + f"""
      const d = {{qualifying: {json.dumps(rows)}, classes: {json.dumps([gt3, gt4])}}};
      const out = quali(1, d);
      return [out, document.getElementById("dd-fl-class").textContent, document.getElementById("dd-fl-time").textContent,
              quali(CYCLE_MS + 1, d)[5].value];""")
    if got is None:
        return
    shown, label, time, interval = got
    assert interval == "+1.000", f"the interval is to the class car ahead, not the overall one: {interval}"
    assert [r.get("head") or [r["pos"], r["value"], r["bestFl"]] for r in shown] == [
        "GT3", ["1", "Pole", True], ["2", "+0.700", False], "GT4", ["1", "Pole", True], ["2", "+1.000", False]], \
        f"positions, gaps and the purple best restart per class, each class P1 on Pole: {shown}"
    assert label == "" and time == "", f"qualifying has no fastest lap box: {label} {time}"


def t_the_public_page_shows_qualifying_per_class_with_every_column():
    gt3 = {"name": "GT3", "color": "#c00", "fastest": {"entry": "A #1", "driver": "Max", "team": "", "ms": 90000}}
    gt4 = {"name": "GT4", "color": "#0a0", "fastest": {"entry": "B #2", "driver": "Ann", "team": "", "ms": 95000}}
    rows = [_q("A #1", 1, 90000, 0, None, 90000, **{"class": "GT3"}, class_pos=1, class_gap_ms=0,
               class_interval_ms=None),
            _q("C #3", 2, 90700, 700, 700, 91000, **{"class": "GT3"}, class_pos=2, class_gap_ms=700,
               class_interval_ms=700),
            _q("B #2", 3, 95000, 5000, 4300, 95000, **{"class": "GT4"}, class_pos=1, class_gap_ms=0,
               class_interval_ms=None),
            _q("D #4", 4, 96000, 6000, 1000, 96000, **{"class": "GT4"}, class_pos=2, class_gap_ms=1000,
               class_interval_ms=1000)]
    got = _public(f"""
      render({{mode: "qualifying", qualifying: {json.dumps(rows)}, classes: {json.dumps([gt3, gt4])}}});
      const marks = els.timing.tBodies[0].children.filter(r => r.className !== "cls").map(
        r => r.children.filter(td => td.dataset.col === "last")[0].className);
      return {{head: els.timing.tHead.children[0].children.map(th => th.textContent), rows: body(), marks: marks,
               fastest: text(document.getElementById("fastest"))}};""")
    if got is None:
        return
    assert got["head"] == ["Pos", "", "#", "Driver", "Tyre", "Best", "Gap", "Int.", "Last"], got["head"]
    cars = [r for r in got["rows"] if "head" not in r]
    assert [r["head"] for r in got["rows"] if "head" in r] == ["GT3", "GT4"], f"one header per class: {got['rows']}"
    assert [(r["pos"], r["best"], r["gap"], r["int"], r["last"]) for r in cars] == [
        ("1", "1:30.000", "Pole", "", "1:30.000"), ("2", "1:30.700", "+0.700", "+0.700", "1:31.000"),
        ("1", "1:35.000", "Pole", "", "1:35.000"), ("2", "1:36.000", "+1.000", "+1.000", "1:36.000")], \
        f"positions, gaps and intervals within the class: {cars}"
    assert got["marks"] == ["fl", "", "fl", "pb"], f"P1's best purple, a personal best green: {got['marks']}"
    assert got["fastest"] == "", f"qualifying lists no fastest lap: {got['fastest']}"


def t_the_cockpit_shows_every_qualifying_column_per_class():
    with open(os.path.join(ROOT, "src", "cockpit", "cockpit.html"), encoding="utf-8") as fh:
        page = fh.read()
    assert "['Pos', '', '#', 'Driver', 'Tyre', 'Best', 'Gap', 'Int.', 'Last']" in page, \
        "the cockpit's qualifying table has best lap, gap, interval and last lap"
    assert "const classes = Array.isArray(d.classes)" in page, "qualifying groups by class like the race"
    assert "gap_ms: e.class_gap_ms, interval_ms: e.class_interval_ms" in page, \
        "a class row reads its gap and interval within the class"
    assert "typeof e.best_ms === 'number' ? 'Pole'" in page, "P1 (of its class) reads Pole"
    assert "} else if (fl && !quali) {" in page, "qualifying shows no fastest lap line"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

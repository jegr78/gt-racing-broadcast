#!/usr/bin/env python3
"""The race snapshot page (src/console/race-snapshot.html): class grouping of a recorded sample.
Runs under node when it is installed (skipped otherwise). Run: python3 tests/test_race_snapshot_page.py"""
import json
import os
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(os.path.dirname(HERE), "src", "console", "race-snapshot.html")

_DOM = r"""
function node(tag) {
  return {tag: tag, className: "", textContent: "", style: {}, children: [], colSpan: 1,
          set hidden(v) {}, addEventListener() {}, append(...c) { this.children.push(...c); },
          replaceChildren(...c) { this.children = c; }};
}
const els = {};
const document = {getElementById: id => els[id] || (els[id] = node("div")), createElement: node};
const location = {pathname: "/x", search: "?time=100"};
const history = {replaceState() {}};
const SAMPLE = __SAMPLE__;
async function fetch(path) {
  const body = path.indexOf("/at") >= 0 ? SAMPLE : {span: [100, 100]};
  return {ok: true, status: 200, json: async () => body};
}
function setInterval() {}
function flat(n) { return [n].concat(...(n.children || []).map(flat)); }
"""
_TAIL = r"""
;setTimeout(() => {
  const rows = els["rows"].children;
  console.log(JSON.stringify(rows.map(tr => ({cls: tr.className,
    cells: tr.children.map(c => flat(c).map(x => x.textContent).join("|")),
    colors: tr.children.map(c => c.style.color || "")}))));
}, 20);
"""


def _render(sample):
    node = shutil.which("node")
    if not node:
        print("  (node not installed, JS check skipped)")
        return None
    with open(PAGE, encoding="utf-8") as fh:
        html = fh.read()
    i = html.rindex("<script>") + len("<script>")
    js = _DOM.replace("__SAMPLE__", json.dumps(sample)) + html[i:html.index("</script>", i)] + _TAIL
    out = subprocess.run([node, "-"], input=js, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _row(entry, pos, cls=None, class_pos=None, **kw):
    r = {"pos": pos, "entry": entry, "driver": "Driver " + entry, "laps": 5, "status": "racing",
         "gap_time_s": 99.0, "interval_time_s": 98.0}
    if cls is not None:
        r.update({"class": cls, "class_pos": class_pos, "class_gap_s": None, "class_interval_s": None,
                  "class_lapped": 0})
    r.update(kw)
    return r


def t_two_classes_group_under_coloured_headers():
    got = _render({"t": 100, "state": "racing",
                   "rows": [_row("A", 1, "GT3", 1), _row("B", 2, "GT4", 1),
                            _row("C", 3, "GT3", 2, class_gap_s=4.5, class_interval_s=4.5),
                            _row("D", 4, "GT3", 3, class_gap_s=None, class_lapped=2, class_gap_time_s=158.2,
                                 class_interval_time_s=153.7),
                            _row("E", 5, "", 1)],
                   "classes": [{"name": "GT3", "color": "#ff0000", "lap": 5, "laps": 30},
                               {"name": "GT4", "color": "#00ff00", "lap": 4, "laps": None}]})
    if got is None:
        return
    kinds = [r["cls"] for r in got]
    assert kinds == ["cls", "", "", "", "cls", "", "cls", ""], kinds
    assert got[0]["cells"] == ["GT3|LAP 5 / 30"] and got[0]["colors"] == ["#ff0000"], got[0]
    assert got[4]["cells"] == ["GT4|LAP 4"], got[4]
    assert got[6]["cells"][0].startswith("Unclassed"), got[6]
    assert [r["cells"][0] for r in got if not r["cls"]] == ["1", "2", "3", "1", "1"], "class_pos is shown"
    gap = lambda i: (got[i]["cells"][3], got[i]["cells"][4])
    assert gap(1) == ("", ""), "class leader reads like the overall leader: %r" % (gap(1),)
    assert gap(2) == ("4.5", "4.5"), "class gap, not the overall 99.0: %r" % (gap(2),)
    assert gap(3) == ("+2\u00a0L\u00a0\u00b7\u00a02:38.2", "2:33.7"), \
        "lapped in the class reads +n L, the class time and the exact class interval: %r" % (gap(3),)
    assert gap(5) == ("", ""), gap(5)
    assert "|C|" in got[2]["cells"][1], "C is GT3's second car"


def t_a_class_gap_reads_the_exact_time_like_the_overall_gap():
    got = _render({"t": 100, "state": "racing",
                   "rows": [_row("A", 1, "GT3", 1), _row("B", 2, "GT4", 1),
                            _row("C", 3, "GT3", 2, class_gap_s=4.5, class_gap_time_s=4.2),
                            _row("D", 4, "GT4", 2, class_gap_s=6.0)],
                   "classes": [{"name": "GT3", "color": "#ff0000", "lap": 5, "laps": None},
                               {"name": "GT4", "color": "#00ff00", "lap": 5, "laps": None}]})
    if got is None:
        return
    gaps = {r["cells"][1].split("|")[1]: r["cells"][3] for r in got if not r["cls"]}
    assert gaps["C"] == "4.2", "the exact class gap, not the rounded 4.5: %r" % gaps
    assert gaps["D"] == "6.0", "a recording without the exact class gap falls back to the rounded one: %r" % gaps


def t_the_lapped_gap_cell_stays_on_one_line():
    with open(PAGE, encoding="utf-8") as f:
        page = f.read()
    assert "td.num { font-family: var(--mono); font-size: 13px; text-align: right; white-space: nowrap; }" in page, \
        "the gap cell never wraps between +1 L, the dot and the time"


def t_one_class_or_none_stays_flat():
    for rows in ([_row("A", 1), _row("B", 2)], [_row("A", 1, "GT3", 1), _row("B", 2, "GT3", 2)]):
        got = _render({"t": 100, "state": "racing", "rows": rows, "classes": []})
        if got is None:
            return
        assert [r["cls"] for r in got] == ["", ""], got
        assert [r["cells"][0] for r in got] == ["1", "2"], "overall pos stays"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

"""The shared track map (src/ui/track-map.js) and its three surfaces: the driver dashboard
overlay, the Race Control desk and the cockpit's timing area. The JS checks run under node."""
import json, math, os, re, shutil, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(*rel):
    with open(os.path.join(ROOT, "src", *rel), encoding="utf-8") as fh:
        return fh.read()


def _node(js):
    node = shutil.which("node")
    if not node:
        print("  (node not installed, JS check skipped)")
        return None
    out = subprocess.run([node, "-"], input=js, capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _fn(text, name):
    """The function `name` from `text`, up to the closing brace at its own indent."""
    i = text.index("function " + name + "(")
    head = text.rfind("\n", 0, i) + 1
    indent = text[head:i].replace("async ", "")
    return text[head:text.index("\n" + indent + "}\n", i) + len(indent) + 2]


# Just enough SVG DOM for the map, and a relay that answers the line and counts the asks.
_DOM = r"""
function node(tag) {
  return {tag: tag, children: [], attrs: {}, textContent: "", classList: {toggle() {}},
          setAttribute(k, v) { this.attrs[k] = v; }, appendChild(c) { this.children.push(c); return c; },
          replaceChildren(...c) { this.children = c; }};
}
const window = globalThis;
const document = {createElementNS: (ns, t) => node(t)};
const asked = [];
async function fetch(url) {
  asked.push(url);
  return {ok: true, json: async () => ({points: [[0, 0], [100, 0], [100, 100], [0, 100]]})};
}
function dots(svg) {
  const g = svg.children[1];
  return g ? g.children.map(d => d.children[1].textContent).reverse() : [];
}
"""


def _place(spots):
    """placeDots on a circle of radius 40 around (50, 50); None where node is missing."""
    line = [[50 + 40 * math.cos(math.radians(a)),
             50 + 40 * math.sin(math.radians(a))] for a in range(360)]
    return _node(f"const CLEAR = 6;\n{_fn(_src('ui', 'track-map.js'), 'placeDots')}\n"
                 f"console.log(JSON.stringify(placeDots({json.dumps(line)}, {json.dumps(spots)})));")


def _off_line(p):
    return abs(((p[0] - 50) ** 2 + (p[1] - 50) ** 2) ** 0.5 - 40)


def t_a_dot_sits_on_the_track_line():
    got = _place([[93.0, 50.0]])
    if got is not None:
        assert _off_line(got[0]) < 0.05, f"a car 3 units off the line is drawn on it: {got}"


def t_a_dot_behind_slides_back_along_the_line():
    got = _place([[90.0, 50.0], [90.0, 50.0]])
    if got is not None:
        a, b = got
        assert _off_line(b) < 0.05, f"the second dot stays on the line: {b}"
        assert ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 >= 5.99, f"no overlap: {got}"
        assert b[1] < a[1], f"it moves back against the direction of travel (+y here): {got}"


def t_a_dense_pack_stays_on_the_line():
    got = _place([[90.0, 50.0]] * 12)
    if got is not None:
        off = [p for p in got if _off_line(p) > 0.05]
        assert not off, f"no dot of a bunched field leaves the track line: {off}"


def t_a_sliding_dot_never_passes_the_car_behind():
    got = _place([[90.0, 50.0], [90.0, 50.0], [math.cos(-0.05) * 40 + 50, math.sin(-0.05) * 40 + 50]])
    if got is not None:
        a, b, c = got
        assert b[1] > c[1] - 0.01, f"the second car stays ahead of the third: {got}"


def t_one_map_module_serves_all_three_surfaces():
    assert "function placeDots(" in _src("ui", "track-map.js")
    pages = {("obs", "drivers.html"): '<script src="/track-map.js"></script>',
             ("racecontrol", "race-control.html"): '<script src="__RC_API_BASE__/track-map.js"></script>',
             ("cockpit", "cockpit.html"): '<script src="__RC_API_BASE__/track-map.js"></script>'}
    for rel, tag in pages.items():
        page = _src(*rel)
        assert page.count(tag) == 1, f"{rel[-1]} loads the shared map through its own base"
        assert "function placeDots(" not in page and "/drivers/line" not in page, \
            f"{rel[-1]} draws with the shared module, no copy of its own"
        assert page.index(tag) < page.rindex("<script>"), f"{rel[-1]}: the module loads before the page script"
    assert "function placeDots(" not in _src("console", "driver-desk.js")
    assert "track-map.js" in _src("..", "tools", "build.py"), "build verify checks the shared map ships"


def t_the_map_draws_the_line_and_a_labelled_dot_per_placed_car():
    got = _node(_DOM + _src("ui", "track-map.js") + r"""
      (async function () {
        const svg = node("svg"), map = TrackMap.create(svg);
        await map.sync("suzuka");
        map.draw([{entry: "a", number: "7", pos: 1, x: 10, z: 0}, {entry: "b", number: "", pos: 2, x: 90, z: 0},
                  {entry: "c", number: "9", pos: 3, x: null, z: null}]);
        const line = svg.children[0].tag;
        await map.sync("suzuka");
        return {asked: asked, line: line, dots: dots(svg)};
      })().then(r => console.log(JSON.stringify(r)));""")
    if got is None:
        return
    assert got["asked"] == ["/drivers/line"], f"one line per track, fetched once: {got}"
    assert got["line"] == "polyline" and got["dots"] == ["7", "2"], \
        f"the line, then the number or else the position on each car with a spot: {got}"


def t_a_car_that_has_finished_leaves_the_map():
    got = _node(_DOM + _src("ui", "track-map.js") + r"""
      (async function () {
        const svg = node("svg"), map = TrackMap.create(svg);
        await map.sync("suzuka");
        map.draw([{entry: "a", number: "7", pos: 1, x: 10, z: 0, finished: true},
                  {entry: "b", number: "8", pos: 2, x: 90, z: 0, finished: false},
                  {entry: "c", number: "9", pos: 3, x: 50, z: 0, finished: true, status: "est"}]);
        return dots(svg);
      })().then(r => console.log(JSON.stringify(r)));""")
    if got is None:
        return
    assert sorted(got) == ["8", "9"], \
        f"a car past the chequered flag leaves the map, an estimated one stays as on the tower: {got}"


def t_the_line_fetch_goes_through_the_pages_fetch_at_call_time():
    got = _node(_DOM + _src("ui", "track-map.js") + r"""
      const inner = fetch;
      fetch = function (url, o) { return inner("/console" + url, o); };   // the page's /console shim
      (async function () {
        await TrackMap.create(node("svg")).sync("spa");
        return asked;
      })().then(r => console.log(JSON.stringify(r)));""")
    if got is not None:
        assert got == ["/console/drivers/line"], f"the Funnel prefix shim applies to the line fetch: {got}"


def t_race_control_shows_the_map_on_top_of_the_chats():
    page = _src("racecontrol", "race-control.html")
    rail = page[page.index('<aside class="rail"'):page.index("</aside>")]
    assert rail.index('class="card mapcard"') < rail.index("data-track-map") < rail.index('id="chatCard"'), \
        "the map is the right column's first card, above the crew chat"
    assert page.count("data-track-map") == 1, "one map, not also in the live column"
    frame = _src("console", "crew-frame.html")
    assert ".rail > .mapcard { flex: none; }" in frame, "the chats give up the height, the map keeps its own"
    assert re.search(r"\.mapcard svg \{[^}]*width: 100%; aspect-ratio: 1; max-height: 3\dvh;", frame), \
        "a square map as wide as the column, capped at about a third of its height"
    assert ".mapcard:has(svg:empty) { display: none; }" in frame, "no line, no card"


def t_the_cockpit_map_lies_over_the_crew_chat_on_request():
    page = _src("cockpit", "cockpit.html")
    rail = page[page.index('<aside class="rail"'):page.index("</aside>")]
    chat = rail[rail.index('id="chatCard"'):rail.index('id="bchatCard"')]
    assert 'class="card mapcard"' not in page and page.count('id="timingMap"') == 1, \
        "the cockpit's map is no card in the column's flow"
    assert re.search(r'<div id="chat">\s*<div id="mapOver" hidden><svg class="tmap" id="timingMap"', chat), \
        "the map sits in the crew chat body, hidden by default"
    assert re.search(r'id="mapToggle"[^>]*aria-expanded="false"[^>]*hidden>', chat), "its toggle starts collapsed"
    assert re.search(r"#chat \{[^}]*position: relative;", page) and \
        re.search(r"#mapOver \{ position: absolute; inset: 0;", page), "it covers the crew chat, never moves a card"
    js = _fn(page, "applyMap")
    got = _node(r"""
      const els = {timingMap: {children: []}, mapOver: {hidden: true},
                   mapToggle: {hidden: true, textContent: "", attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }}};
      const $ = id => els[id];
      let mapWanted = false;
      """ + js + r"""
      const out = [];
      function state() { return [els.mapToggle.hidden, els.mapOver.hidden, els.mapToggle.attrs["aria-expanded"]]; }
      applyMap(); out.push(state());
      els.timingMap.children = [{}]; applyMap(); out.push(state());
      mapWanted = true; applyMap(); out.push(state());
      els.timingMap.children = []; applyMap(); out.push(state());
      console.log(JSON.stringify(out));""")
    if got is not None:
        assert got == [[True, True, "false"], [False, True, "false"], [False, False, "true"], [True, True, "false"]], \
            f"no line: no toggle; a line: the toggle, the map only when asked for: {got}"
    assert "localStorage.getItem(MAP_KEY) === '1'" in page and "let mapWanted = false;" in page, \
        "collapsed unless this browser asked for it"


def t_the_race_control_map_shows_the_desks_undelayed_rows():
    desk = _src("console", "driver-desk.js")
    got = _node(_fn(desk, "mapCars") + r"""
      const r = {mode: "race", class_colors: {GT3: "#0a0"},
                 entries: [{entry: "a", class: "GT3", x: 1, z: 2}, {entry: "b", class: "GT4", x: 3, z: 4}]};
      console.log(JSON.stringify([mapCars(r), mapCars(Object.assign({}, r, {mode: "qualifying"}))]));""")
    if got is not None:
        race, quali = got
        assert [(e["entry"], e["pos"], e["class_color"]) for e in race] == [("a", 1, "#0a0"), ("b", 2, "")], \
            f"each row in running order with its class ring: {race}"
        assert quali == [], f"qualifying shows the line only, as on the overlay: {quali}"
    mount = desk[desk.index("function mount("):]
    assert 'root.querySelector("[data-track-map]")' in mount and "window.TrackMap" in mount, \
        "the desk draws a map only where its page has one"
    assert re.search(r"map\.sync\(r\.track \|\| null\)\.then\(function \(\) \{ map\.draw\(mapCars\(r\)\)", mount), \
        "the map follows the race table's own /drivers/race answer"
    assert "data-track-map" in _src("racecontrol", "race-control.html")
    assert "data-track-map" not in _src("director", "director-panel.html"), "the Director Panel has no map"


def t_the_cockpit_map_follows_the_delayed_dashboard():
    page = _src("cockpit", "cockpit.html")
    got = _node(_DOM + _src("ui", "track-map.js") + "\nconst svg = node('svg'); const $ = () => svg;\n"
                + "let timingMapView = null; function applyMap() {}\n" + _fn(page, "drawTimingMap") + r"""
      (async function () {
        const d = {mode: "race", track: "spa", entries: [{entry: "a", number: "3", pos: 1, x: 10, z: 0}]};
        drawTimingMap(d);
        await new Promise(r => setTimeout(r, 10));
        const race = dots(svg);
        drawTimingMap(Object.assign({}, d, {mode: "qualifying", qualifying: d.entries}));
        await new Promise(r => setTimeout(r, 10));
        return {race: race, quali: dots(svg), asked: asked};
      })().then(r => console.log(JSON.stringify(r)));""")
    if got is None:
        return
    assert got["race"] == ["3"] and got["asked"] == ["/drivers/line"], f"the dashboard's cars on its track: {got}"
    assert got["quali"] == [], f"qualifying shows the line only, as on the overlay: {got}"
    poll = _fn(page, "pollTiming")
    assert "drawTimingMap(d);" in poll and "j('/drivers/dashboard')" in poll, \
        "the map draws from the same delayed answer as the timing table"


def t_the_chat_input_yields_so_send_stays_in_its_card():
    for page in (("cockpit", "cockpit.html"), ("racecontrol", "race-control.html")):
        with open(os.path.join(ROOT, "src", *page), encoding="utf-8") as fh:
            css = fh.read()
        rule = css[css.index(".chatbar input {"):]
        rule = rule[:rule.index("}")]
        assert "min-width: 0" in rule, f"{page[1]}: the chat input shrinks so Send stays inside a narrow card"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

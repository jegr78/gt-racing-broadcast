#!/usr/bin/env python3
"""Record mode of the event simulation (#1310): page videos, pointer, mask, timeline,
the card look, the generated clips, the limits and the watchdog, the real OBS's
configuration and its WebSocket sequence. No browser, no OBS, nothing heavy spawned.
Run: python3 tests/test_event_sim_record.py"""
import atexit
import base64
import copy
import json
import os
import re
import shutil
import struct
import sys
import tempfile
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SIM = os.path.join(ROOT, "tools", "event-sim")
for _p in (HERE, SIM, os.path.join(SIM, "fakes"), os.path.join(ROOT, "src", "scripts"), os.path.join(ROOT, "tools")):
    sys.path.insert(0, _p)
import engine  # noqa: E402
import event_sim  # noqa: E402
import obs_real  # noqa: E402
import obs_sim  # noqa: E402
import obs_ws  # noqa: E402
import probes  # noqa: E402
import record  # noqa: E402
import rtmp_sink  # noqa: E402
import roles  # noqa: E402
import stream_stub  # noqa: E402
import test_event_sim_roles as fake  # noqa: E402


party, selectors = roles.party, roles.selectors


def _tmp():
    path = tempfile.mkdtemp(prefix="event-sim-record-")
    atexit.register(shutil.rmtree, path, True)
    return path


class Video:
    def __init__(self, path):
        self._path = path

    def path(self):
        return self._path


class Mouse:
    def __init__(self, page):
        self.page, self.moves = page, []

    def move(self, x, y, steps=1):
        self.moves.append((x, y, steps))
        self.page.clicks.append(("move", x, y))


class Browser(fake.Browser):
    """The roles test's browser, plus the options a context was created with."""

    def __init__(self, page=None):
        super().__init__(page)
        self.options = []

    def new_context(self, viewport=None, **options):
        self.options.append(options)
        return super().new_context(viewport=viewport)


def _role(cls, recorder=None, video=None):
    clock = fake.Clock()
    page = fake.Page(clock)
    page.mouse = Mouse(page)
    page.video = Video(video) if video else None
    browser = Browser(page)
    ctx = engine.RunContext(urls={"relay": fake.BASE, "ui": fake.BASE}, token=lambda who: "tok-" + who,
                            browser=browser, event_link={"invite": "inv", "timing": "tim"}.get, recorder=recorder)
    role = cls(ctx, cls.kind)
    role.open()
    role.clock = clock
    page.add(getattr(cls, "ready", "body"))
    return role, page, browser


# -- page recording, pointer and mask ----------------------------------------------
def t_a_record_run_records_each_page_at_its_viewport_with_pointer_and_mask():
    out = _tmp()
    rec = record.Recorder(out, clock=lambda: 50.0)
    video = os.path.join(out, "pages", "driver", "abc.webm")
    role, page, browser = _role(party.DriverRole, recorder=rec, video=video)
    role.do("open", {})
    opts = browser.options[0]
    assert opts["record_video_size"] == {"width": 390, "height": 844}, "the video has the page's own viewport"
    assert opts["record_video_dir"] == os.path.join(out, "pages", "driver"), opts
    scripts = page.init_scripts
    assert record.POINTER_SCRIPT in scripts and record.mask_script() in scripts, \
        "a record run draws the pointer and masks the links in the page"
    assert rec.recordings == [{"view": "driver", "kind": "page", "file": "pages/driver/abc.webm",
                               "size": {"width": 390, "height": 844}, "at": 50.0}], rec.recordings


def t_close_ends_the_page_and_its_recording_and_open_starts_both_again():
    now = [50.0]
    rec = record.Recorder(_tmp(), clock=lambda: now[0])
    role, page, browser = _role(party.DriverRole, recorder=rec, video=os.path.join(rec.dir, "pages", "driver", "a.webm"))
    role.do("open", {})
    context = role.context
    now[0] = 80.0
    role.do("close", {})
    assert context.closed and role.pg is None, "close ends the page's context, which finishes its video"
    assert rec.recordings[0]["end"] == 80.0, rec.recordings
    try:
        role.do("close", {})
    except roles.StepError as exc:
        assert "not open" in str(exc), exc
    else:
        raise AssertionError("a closed page cannot close again")
    now[0] = 90.0
    role.do("open", {})
    assert len(rec.recordings) == 2 and rec.recordings[1]["at"] == 90.0 and "end" not in rec.recordings[1], \
        "the reopened page records anew"


def t_the_standings_tab_is_its_own_recording_and_stays_open_for_its_hold():
    out = _tmp()
    clock = {"t": 50.0}
    rec = record.Recorder(out, clock=lambda: clock["t"])
    role, page, _ = _role(party.RaceControlRole, recorder=rec, video=os.path.join(out, "pages", "race_control", "a.webm"))
    role.do("open", {})
    page.add('.navbtn[data-area="race"]')
    tab = fake.Page(page.clock)
    tab.video = Video(os.path.join(out, "pages", "race_control", "b.webm"))
    tab.add("#rows tr", count=14)
    opened = {}

    def click():
        clock["t"] = 60.0
        opened["t"] = page.clock.t
        role.context.opened.append(tab)
    page.add('.dd tr:has(div:text-is("B #2")) button[data-label="Show standings"]', on_click=click)
    assert role.do("show_standings", {"entry": "B #2", "seconds": 8}) == {"entry": "B #2", "rows": 14}
    assert rec.recordings[-1] == {"view": "race_control:standings", "kind": "page", "file": "pages/race_control/b.webm",
                                  "size": dict(role.viewport), "at": 60.0, "end": 60.0}, rec.recordings
    assert tab.closed and page.clock.t - opened["t"] >= 8, "the tab stays in view for the step's hold, then closes"
    fails = []
    for bad in (0, "8", 301):
        try:
            role.do("show_standings", {"entry": "B #2", "seconds": bad})
        except roles.StepError:
            fails.append(bad)
    assert fails == [0, "8", 301], fails


def t_a_focus_may_name_the_standings_tab_of_race_control_only():
    import scenario
    reg = roles.default_registry()
    steps = [{"id": "a", "role": "race_control", "do": "show_standings", "args": {"entry": "x"},
              "focus": ["race_control:standings"]}]
    assert scenario.from_dict({"step": steps}, reg).steps[0].focus == ["race_control:standings"]
    steps[0]["focus"] = ["director:standings"]
    try:
        scenario.from_dict({"step": steps}, reg)
    except scenario.ScenarioError as exc:
        assert "no view of this run" in str(exc), exc
    else:
        raise AssertionError("only Race Control opens a standings tab")

def t_a_run_without_record_has_no_video_no_pointer_and_no_mask():
    role, page, browser = _role(party.DriverRole)
    role.do("open", {})
    assert browser.options == [{}], "no video options outside record mode"
    assert record.POINTER_SCRIPT not in page.init_scripts and record.mask_script() not in page.init_scripts, \
        "the pointer and the mask are injected in record mode only"


def t_the_mask_covers_every_link_selector_and_only_changes_paint():
    script = record.mask_script()
    for sel in selectors.LINKS:
        assert f"{sel} *" in script, f"the mask covers {sel} and its children"
    for prop in ("display", "visibility", "opacity", "content-visibility"):
        assert prop not in script, f"the mask must not use {prop}: the roles still read the masked element"
    assert "adoptedStyleSheets" in script, "the mask is adopted before the first paint"


def t_the_pointer_never_takes_a_click_and_stays_in_the_top_frame():
    assert record.POINTER_SCRIPT.count("pointer-events:none") == 2, \
        "the pointer and the click mark let every click through to the page"
    assert "window.top !== window" in record.POINTER_SCRIPT, "frames draw no second pointer"


def t_a_record_click_moves_the_pointer_to_the_control_first():
    rec = record.Recorder(_tmp())
    role, page, _ = _role(party.ViewerRole, recorder=rec)
    role.do("open", {})
    page.add("#go", box={"x": 100, "y": 40, "width": 60, "height": 20})
    role.click("#go", "go")
    assert page.mouse.moves == [(130, 50, party.POINTER_STEPS)], \
        f"a record click moves the pointer to the control centre in visible steps: {page.mouse.moves}"
    assert page.clicks[-2:] == [("move", 130, 50), "#go"], "the pointer travels to the control, then it clicks"
    plain, page2, _ = _role(party.ViewerRole)
    plain.do("open", {})
    page2.add("#go")
    plain.click("#go", "go")
    assert page2.mouse.moves == [], "without record mode the click does not move a pointer"


# -- timeline ------------------------------------------------------------------------
def t_the_timeline_lists_steps_and_recording_offsets_since_the_engine_start():
    now = [10.0]
    rec = record.Recorder(_tmp(), clock=lambda: now[0])
    rec.program_started("/elsewhere/program.mkv")
    now[0] = 25.5
    rec._add("director", "page", os.path.join(rec.dir, "pages", "director", "v.webm"), {"width": 1440, "height": 900})
    card = {"title": "Race day", "subtitle": "Three stints"}
    title = {"title": "GT Racing Broadcast", "subtitle": "An endurance event"}
    scn = SimpleNamespace(name="t", intro=title, outro=None,
                          steps=[SimpleNamespace(id="a", caption="Producer starts the event",
                                                 chapter="Start the event", focus=["producer"], card=card),
                                 SimpleNamespace(id="b")])
    steps = [{"id": "a", "role": "producer", "do": "start_event", "status": "ok", "started": 1.0, "duration": 2.5,
              "acted": 1.25},
             {"id": "b", "role": "drivers", "do": "start", "status": "failed", "started": 4.0, "duration": 0.25},
             {"id": "c", "role": "x", "do": "y", "status": "skipped", "started": None, "duration": 0.0}]
    tl = rec.timeline(scn, steps, start=20.0)
    assert tl["steps"][0] == {"id": "a", "role": "producer", "action": "start_event", "status": "ok", "start": 1.0,
                              "acted": 1.25, "end": 3.5, "caption": "Producer starts the event", "chapter": "Start the event",
                              "focus": ["producer"], "card": card}, tl["steps"][0]
    assert tl["intro"] == title and "outro" not in tl, "the film-wide cards the scenario has reach the timeline"
    assert set(tl["steps"][1]) == {"id", "role", "action", "status", "start", "acted", "end"}, \
        "a step without caption, chapter, focus or card carries none of them"
    assert tl["steps"][1]["acted"] == tl["steps"][1]["start"], "a step that never noted its action counts as acted at once"
    assert [s["id"] for s in tl["steps"]] == ["a", "b"], "a step that never started has no place on the timeline"
    by_view = {r["view"]: r for r in tl["recordings"]}
    assert by_view["program"]["start"] == -10.0, "a recording started before the engine has a negative offset"
    assert by_view["director"]["start"] == 5.5 and by_view["director"]["file"] == "pages/director/v.webm", by_view
    path = rec.write(tl)
    with open(path, encoding="utf-8") as fh:
        assert json.load(fh) == tl and os.path.basename(path) == "timeline.json"


def t_a_closed_page_s_recording_ends_and_a_reopened_one_is_a_new_recording():
    now = [10.0]
    rec = record.Recorder(_tmp(), clock=lambda: now[0])
    page = SimpleNamespace(video=SimpleNamespace(path=lambda: os.path.join(rec.dir, "pages", "producer", "a.webm")))
    rec.page_opened("producer", page, {"width": 1440, "height": 900})
    now[0] = 40.0
    rec.page_closed("producer")
    now[0] = 90.0
    page2 = SimpleNamespace(video=SimpleNamespace(path=lambda: os.path.join(rec.dir, "pages", "producer", "b.webm")))
    rec.page_opened("producer", page2, {"width": 1440, "height": 900})
    tl = rec.timeline(SimpleNamespace(name="t", steps=[]), [], start=10.0)
    got = [(r["start"], r.get("end")) for r in tl["recordings"]]
    assert got == [(0.0, 30.0), (80.0, None)], f"the first recording ends at the close, the second runs on: {got}"


def t_a_recording_file_is_named_relative_to_the_record_dir_when_it_can_be():
    import ntpath
    import posixpath
    assert record.rel_file("C:\\out\\pages\\v.webm", "C:\\out", pathmod=ntpath) == "pages/v.webm", \
        "a file under the record dir is named relative to it, with forward slashes"
    try:
        other = record.rel_file("D:\\rec\\program.mkv", "C:\\out", pathmod=ntpath)
    except ValueError as exc:
        other = f"ValueError: {exc}"
    assert other == "D:/rec/program.mkv", \
        f"a file on another drive keeps its absolute path, with forward slashes, instead of failing: {other}"
    assert record.rel_file("/out/program/p.mkv", "/out", pathmod=posixpath) == "program/p.mkv"
    assert record.rel_file(None, "/out") is None


def t_the_run_writes_its_timeline_through_the_run_s_redaction():
    rec = record.Recorder(_tmp(), clock=lambda: 3.0)
    scn = SimpleNamespace(name="t", steps=[SimpleNamespace(id="a", caption="opens /console/drive?i=abcdef1234")])
    eng = SimpleNamespace(scenario=scn, start=1.0, results={"a": engine.StepResult("a", "driver", "open", "ok", 0.5, 1.0)})
    path = event_sim.write_timeline(rec, eng, engine.redaction.scrub)
    with open(path, encoding="utf-8") as fh:
        tl = json.load(fh)
    assert "abcdef1234" not in json.dumps(tl) and tl["steps"][0]["end"] == 1.5, tl
    assert event_sim.write_timeline(rec, SimpleNamespace(start=None), None) is None, \
        "a run whose engine never started writes no timeline"
    assert event_sim.write_timeline(None, eng, None) is None, "a run without --record writes none"


def t_the_contained_run_keeps_record_and_a_record_run_is_one_run():
    args = event_sim.build_parser().parse_args(["--scenario", "smoke", "--out", "o", "--record", "rec"])
    argv = event_sim._run_argv(SimpleNamespace(path="smoke.toml"), "/o", args)
    assert argv[argv.index("--record") + 1] == os.path.abspath("rec"), \
        "the run inside the unit gets --record, with an absolute path"
    real = event_sim.build_parser().parse_args(["--scenario", "smoke", "--out", "o", "--record", "r", "--obs", "real"])
    argv = event_sim._run_argv(SimpleNamespace(path="smoke.toml"), "/o", real)
    assert argv[argv.index("--obs") + 1] == "real", "the run inside the unit keeps --obs real"
    assert event_sim.main(["--scenario", "smoke", "--out", _tmp(), "--obs", "real"]) == 2, \
        "--obs real without --record is refused: the program recording needs its directory"
    code = event_sim.main(["--scenario", "smoke", "--out", _tmp(), "--record", _tmp(), "--repeat", "2"])
    assert code == 2, "--record with --repeat is refused"


# -- generated clips ---------------------------------------------------------------------
def t_the_intro_trailer_and_outro_clips_are_generated_into_the_media_dir():
    media = os.path.join(_tmp(), "runtime", "demo", "media")
    images = {stem: f"/titles/{stem}.png" for stem in record.CLIPS}
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stderr="")
    paths = record.make_clips(media, images, run=run)
    assert [os.path.basename(p) for p in paths] == ["intro.mp4", "trailer.mp4", "outro.mp4"], paths
    assert all(os.path.dirname(p) == media for p in paths), "the clips land where the media sources read them"
    for argv, stem in zip(calls, record.CLIPS, strict=True):
        seconds = float(argv[argv.index("-t") + 1])
        assert 8 <= seconds <= 10, f"a clip runs 8 to 10 s, not {seconds}"
        assert images[stem] in argv and "drawtext" not in " ".join(argv), \
            "the clip overlays its pre-rendered title; Homebrew's ffmpeg has no drawtext"
        assert all("http" not in a for a in argv), "a clip is drawn, nothing is downloaded"
    try:
        record.make_clips(media, images, run=lambda argv, **kw: SimpleNamespace(returncode=1, stderr="no gradients"))
    except RuntimeError as exc:
        assert "intro" in str(exc) and "no gradients" in str(exc), exc
    else:
        raise AssertionError("a clip ffmpeg could not write fails the run's start")


def t_a_channel_created_during_the_run_gets_its_card_before_the_stub_serves_it():
    stub = record.stream_stub.StreamStub(_tmp()).install()
    order, rendered = [], []

    def render(jobs, browser=None):
        order.append("render" if stub.get("comms9") is None else "late")
        rendered.append((jobs, browser))
    rec = record.Recorder(_tmp(), render=render)
    ctx = engine.RunContext(stub=stub, recorder=rec)
    ctx.browser = lambda: "the run's browser"
    role = roles.builtin.StreamRole(ctx, "stream")
    role.do("channel", {"name": "Comms9", "stint": 4, "commentator": "Dana"})
    assert order == ["render"], "the card is rendered before the channel exists, so no pull sees it bare"
    jobs, browser = rendered[0]
    html = {p: h for h, p, _ in jobs}
    paths = record.stream_stub.card_paths(stub.directory, "comms9")
    assert set(paths) <= set(html) and "Stint 4" in html[paths.title] and "Dana" in html[paths.title], \
        "a run-time channel gets its title, dial and hand"
    assert browser == "the run's browser", "it renders in the run's own browser, not a second Playwright"
    role.do("channel", {"name": "comms9", "status": "live"})
    assert len(rendered) == 1, "a change that keeps stint and commentator renders nothing"
    role.do("channel", {"name": "comms9", "commentator": "Eli"})
    assert "Stint 4" in rendered[-1][0][0][0] and "Eli" in rendered[-1][0][0][0], \
        "a new commentator gets a new card with the kept stint"
    plain_ctx = engine.RunContext(stub=stub)
    roles.builtin.StreamRole(plain_ctx, "stream").do("channel", {"name": "comms10", "stint": 1})
    assert len(rendered) == 2 and plain_ctx.findings == [], \
        "outside record mode no card is rendered and no browser is asked for"


def t_a_record_stack_renders_its_titles_once_and_makes_its_clips_in_the_app_home():
    import stack
    home, stub = _tmp(), _tmp()
    rendered, made = [], []
    channels = [SimpleNamespace(channel="Comms1", stint=1, name="Anna"),
                SimpleNamespace(channel="comms2", stint=2, name="Ben <&>")]
    stack.prepare_record(home, stub, channels, render=rendered.append,
                         make=lambda d, images: made.append((d, images)))
    assert len(rendered) == 1, "one browser session renders every title"
    html = {p: h for h, p, _ in rendered[0]}
    for ch in ("comms1", "comms2"):
        assert set(record.stream_stub.card_paths(stub, ch)) <= set(html), f"the card images of {ch} are rendered"
    title = html[record.stream_stub.card_paths(stub, "comms2").title]
    assert "Stint 2" in title and "Ben &lt;&amp;&gt;" in title, "the card names stint and commentator, escaped"
    assert made[0][0] == os.path.join(home, "runtime", "demo", "media"), made
    assert set(made[0][1]) == set(record.CLIPS) and all(p in html for p in made[0][1].values()), \
        "each clip gets its rendered title"
    intro = html[made[0][1]["intro"]]
    assert "Intro" in intro and "GT Demo League" in intro, "the intro shows its title and the league name"
    graphics = os.path.join(home, "runtime", "demo", "graphics")
    with open(stack.COLLECTION, encoding="utf-8") as fh:
        labels = set(re.findall(r"__RACECAST_GRAPHICS__/([^\"/]+)\.png", fh.read()))
    assert {"Overlay", "Standby", "Standby Cover", "Flag Red", "Grid Row 8"} <= labels, labels
    sizes = {p: size for _h, p, size in rendered[0]}
    for label in labels:
        path = os.path.join(graphics, label + ".png")
        assert sizes.get(path) == record.CLIP_SIZE, f"the collection's {label} graphic is rendered full canvas"
    over = [lab for lab in labels if lab in ("Overlay", "Post Race Interviews") or lab.startswith(("Flag ", "Grid Row "))]
    for label in labels:
        page = html[os.path.join(graphics, label + ".png")]
        opaque = "background:transparent" not in page.split(">", 2)[1]
        assert opaque == (label not in over), \
            f"{label}: over the live picture it stays transparent, a full page covers it"
    overlay = html[os.path.join(graphics, "Overlay.png")]
    assert "opacity" not in overlay and "rgba" not in overlay, "the Overlay's panels are opaque: they hide the strip"
    with open(os.path.join(ROOT, "profiles", "demo", "overlay", "layout-hud.json"), encoding="utf-8") as fh:
        slots = json.load(fh)["slots"]
    for n in (1, 2, 3):
        s = slots[f"team{n}-logo"]
        plate = (f"left:{s['left'] - record.PLATE_MARGIN}px;top:{s['top'] - record.PLATE_MARGIN}px;"
                 f"width:{s['width'] + 2 * record.PLATE_MARGIN}px;height:{s['height'] + 2 * record.PLATE_MARGIN}px")
        assert plate in overlay and record.PLATE_COLOUR in overlay, \
            f"a light plate behind team {n}'s logo keeps a dark brand legible on the dark panel"
    standby = html[os.path.join(graphics, "Standby.png")]
    assert "Standby" in standby and "GT Demo League" in standby, "a full page names itself and the league"


# -- limits and the watchdog -----------------------------------------------------------------
MiB = 1024**2
START = "Wed Nov 25 09:00:00 2026"


def _rows(*procs):
    """(pid, ppid, rss, start) rows from (pid, ppid, MiB) and an optional start."""
    return [(p[0], p[1], p[2] * MiB, p[3] if len(p) > 3 else START) for p in procs]


def t_the_watchdog_counts_the_whole_tree_whatever_its_process_groups():
    rows = _rows((100, 1, 300), (101, 100, 200), (102, 101, 1500), (103, 102, 400), (900, 1, 5000))
    dog = record.Watchdog(2500 * MiB, root=100, ps=lambda: rows, interrupt=lambda: None)
    assert dog.check() == 2400 * MiB, \
        "a browser or ffmpeg in its own process group still counts: the tree is walked by ppid"
    orphan = _rows((100, 1, 300), (103, 1, 400), (900, 1, 5000))
    assert record.descendants(orphan, 100, dog.known) == {103}, \
        "a child reparented after its parent ended still counts"
    reused = _rows((100, 1, 300), (103, 1, 400, "Wed Nov 25 10:00:00 2026"))
    assert record.descendants(reused, 100, dict(dog.known)) == set(), "a reused pid of another process does not"


def t_the_watchdog_asks_the_run_to_stop_then_kills_its_tree_after_the_grace():
    now, asked, killed = [0.0], [], []
    rows = _rows((100, 1, 300), (101, 100, 3000), (102, 101, 100))
    dog = record.Watchdog(2000 * MiB, root=100, ps=lambda: rows, interrupt=lambda: asked.append(now[0]),
                          kill=lambda pid, sig: killed.append(pid), clock=lambda: now[0], grace=60)
    dog.check()
    assert asked == [0.0] and killed == [] and "over its limit" in dog.reason, \
        "past the limit the run is asked to stop first, so it quits OBS and restores what it changed"
    now[0] = 30.0
    dog.check()
    assert asked == [0.0] and killed == [], "within the grace nothing is killed and the run is asked once"
    now[0] = 61.0
    dog.check()
    assert killed == [101, 102], "after the grace every descendant is killed, never the run itself"
    late = record.Watchdog(2000 * MiB, root=100, ps=lambda: rows, interrupt=lambda: asked.append("late"),
                           kill=lambda pid, sig: None, clock=lambda: now[0])
    late.disarm()
    late.check()
    assert "late" not in asked and late.reason, "a run in its cleanup is not interrupted, so OBS still quits"
    calm = record.Watchdog(2000 * MiB, root=100, ps=lambda: _rows((100, 1, 300)), interrupt=lambda: asked.append(1))
    calm.check()
    assert calm.reason == "" and calm.peak == 300 * MiB, "a tree under its limit is left alone"


def t_record_states_its_limits_and_checks_the_machine_before_the_stack():
    assert record.LIMITS == (record.RECORD_MEMORY, record.RECORD_TASKS, record.RECORD_CPU_PERCENT)
    assert record.RECORD_MEMORY > record.e2e_safety.MAX_MEMORY, "a record run needs more than the E2E unit"
    assert 2 * 2.75e9 <= record.RECORD_MEMORY <= 8 * 1024**3, \
        "the limit holds about twice the measured tree peak of `full` (2.75 GB on macOS) and no more"
    assert "Linux or macOS" in record.admission(platform="win32"), "no watchdog and no OBS method off Linux and macOS"
    need = record.RECORD_MEMORY + record.e2e_safety.MEMORY_MARGIN
    assert record.admission(platform="darwin", total=lambda: need, tools=lambda: "") == ""
    assert "needs" in record.admission(platform="darwin", total=lambda: need - 1, tools=lambda: ""), \
        "a smaller Mac is refused"
    calm = {"available": need, "some": 0.0, "full": 0.0}
    assert record.admission(platform="linux", host=lambda: calm, tools=lambda: "") == ""
    missing = record.admission(platform="linux", host=lambda: calm, tools=lambda: "ffmpeg lacks rotate")
    assert "rotate" in missing, "a machine without the filters or the browser the pictures need is refused"
    assert "MemAvailable" in record.admission(platform="linux", host=lambda: dict(calm, available=need - 1),
                                              tools=lambda: "")


def t_a_record_run_is_admitted_before_its_unit_and_gets_its_own_limits():
    entered = []
    old_admission, old_enter = record.admission, event_sim.e2e_safety.enter
    try:
        record.admission = lambda: "the machine has 4096 MiB, record mode needs 10752 MiB"
        event_sim.e2e_safety.enter = lambda *a, **kw: entered.append(kw) or 0
        argv = ["--scenario", "smoke", "--out", _tmp(), "--record", _tmp()]
        assert event_sim.main(argv) == 2 and entered == [], "a machine below the limit starts nothing"
        record.admission = lambda: ""
        assert event_sim.main(argv) == 0 and entered[0]["limits"] == record.LIMITS, \
            "the record run's unit gets the record limits"
        assert entered[0]["stop_pipe"], "a stop signal reaches the contained run through its stop pipe"
        event_sim.main(["--scenario", "smoke", "--out", _tmp()])
        assert "limits" not in entered[1], "a run without --record keeps the E2E unit's limits"
    finally:
        record.admission, event_sim.e2e_safety.enter = old_admission, old_enter


def t_the_pictures_need_stock_ffmpeg_filters_and_playwright_but_no_drawtext():
    listing = "\n".join(f" T.. {f:<16} V->V  x" for f in record.FILTERS)
    run = lambda argv, **kw: SimpleNamespace(stdout=listing, returncode=0)  # noqa: E731
    assert record.tools_problem(run=run, playwright=lambda: True) == "", "a stock build with Playwright is enough"
    assert "drawtext" not in record.FILTERS, "record mode works with a build without drawtext (Homebrew's)"
    short = "\n".join(f" T.. {f:<16} V->V  x" for f in record.FILTERS if f != "gradients")
    problem = record.tools_problem(run=lambda argv, **kw: SimpleNamespace(stdout=short, returncode=0),
                                   playwright=lambda: True)
    assert "gradients" in problem, f"an ffmpeg without gradients is refused by name: {problem!r}"
    assert "Playwright" in record.tools_problem(run=run, playwright=lambda: False)


def t_ps_rows_reads_pid_ppid_rss_and_start():
    out = "  100     1  2048 Wed Nov 25 09:00:00 2026\n  bad line\n  101   100     4 Wed Nov 25 09:00:01 2026\n"
    rows = record.ps_rows(run=lambda argv, **kw: SimpleNamespace(stdout=out))
    assert rows == [(100, 1, 2 * MiB, "Wed Nov 25 09:00:00 2026"), (101, 100, 4096, "Wed Nov 25 09:00:01 2026")], rows


def t_signals_during_the_obs_quit_and_restore_wait_until_it_finished():
    import signal as sig
    got = []
    old = sig.signal(sig.SIGTERM, lambda n, f: got.append(("handler", n)))
    try:
        with record.deferred_signals():
            sig.raise_signal(sig.SIGTERM)
            got.append("restored")
        assert got == ["restored", ("handler", sig.SIGTERM)], \
            f"a signal in the cleanup waits until the restore finished, then takes effect: {got}"
    finally:
        sig.signal(sig.SIGTERM, old)


def t_a_restore_that_cannot_finish_names_the_recovery_steps():
    obs = obs_real.RealObs(_tmp(), _tmp(), _collection(), platform="linux", binary=sys.executable, port=1)

    def broken():
        raise OSError("disk gone")
    obs.config.restore = broken
    problems = obs.stop()
    assert any("quit OBS" in p and "event_sim.py --restore-obs" in p for p in problems), \
        f"a restore that breaks tells the maintainer how to recover: {problems}"


# -- the real OBS ---------------------------------------------------------------------------
COLLECTION = os.path.join(ROOT, "src", "obs", "GT_Racing_Endurance.json")
DEMO = "GT Racing Endurance — Demo"


def _collection(name=DEMO):
    with open(COLLECTION, encoding="utf-8") as fh:
        data = json.load(fh)
    data["name"] = name
    return data


class Proc:
    """A started OBS: ends on the signals in `obeys`."""

    def __init__(self, obeys=("SIGINT",)):
        self.pid, self.returncode, self.obeys, self.signals = 4242, None, obeys, []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise obs_real.subprocess.TimeoutExpired("obs", timeout)
        return self.returncode

    def signal(self, pid, sig):
        assert pid == self.pid, "only the PID the run started is signalled"
        self.signals.append(sig.name if hasattr(sig, "name") else sig)
        if self.signals[-1] in self.obeys or self.signals[-1] == "SIGKILL":
            self.returncode = 0


class Session:
    """The simulated OBS's WebSocket plus the profile list it does not model."""

    version = "33.0.3"

    def __init__(self, port):
        self.inner = obs_ws._open_session("127.0.0.1", port, "", 5)
        self.log = []

    def request(self, kind, data):
        self.log.append(kind)
        if kind == "GetProfileList":
            return {"currentProfileName": obs_real.PROFILE}
        if kind == "GetVersion":
            return {"obsVersion": self.version}
        return self.inner.request(kind, data)

    def close(self):
        self.inner.close()


def _real(sim, proc, work, record_dir, **kw):
    sessions = []

    def connect(host, port, password, timeout):
        sessions.append(Session(sim.port))
        sessions[-1].password = password
        return sessions[-1]
    launched = []
    obs = obs_real.RealObs(work, record_dir, _collection(sim.state.collection), platform="linux",
                           launcher=lambda argv, **o: launched.append((argv, o)) or proc, connect=connect,
                           kill=proc.signal, killpg=proc.signal, binary=sys.executable,
                           sleep=lambda s: None, port=sim.port, **kw)
    return obs, sessions, launched


def t_the_real_obs_starts_records_stops_and_quits_on_sigint():
    sim = obs_sim.ObsSim(collection=_collection()).start()
    try:
        work, rec_dir, proc = _tmp(), _tmp(), Proc()
        obs, sessions, launched = _real(sim, proc, work, rec_dir)
        obs.start()
        argv, opts = launched[0]
        assert argv[1:7] == ["--multi", "--minimize-to-tray", "--disable-missing-files-check", "--profile", "Sim",
                             "--collection"] and argv[7] == DEMO, argv
        given = dict(zip(argv[8::2], argv[9::2], strict=True))
        assert given.get("--websocket_port") == str(sim.port) and len(given.get("--websocket_password", "")) >= 20, \
            "OBS gets its WebSocket port and password on its command line (obs-websocket's overrides)"
        assert opts["env"]["XDG_CONFIG_HOME"] == os.path.join(work, "obs-config") and opts["start_new_session"], \
            "on Linux OBS reads only the run's own configuration"
        ws = os.path.join(work, "obs-config", "obs-studio", "plugin_config", "obs-websocket", "config.json")
        with open(ws, encoding="utf-8") as fh:
            cfg = json.load(fh)
        assert cfg["server_enabled"] is True and "server_password" not in cfg, \
            "the WebSocket is on, and its password is in no file"
        for base, _dirs, files in os.walk(os.path.join(work, "obs-config")):
            for f in files:
                with open(os.path.join(base, f), encoding="utf-8", errors="replace") as fh:
                    assert given["--websocket_password"] not in fh.read(), f"{f} must not hold the WebSocket password"
        assert sessions[0].password == given["--websocket_password"], "the run's own client authenticates"
        assert obs.env()["RACECAST_SIM_OBS_WS"] == f"ws://127.0.0.1:{sim.port}", "racecast talks to this OBS"
        assert obs.env()["RACECAST_SIM_OBS_WS_PASSWORD"] == given["--websocket_password"], \
            "and the relay authenticates too"
        other, _, _ = _real(sim, Proc(), _tmp(), _tmp())
        assert other.password and other.password != obs.password, "every run has its own password"
        obs.start_record()
        assert sim.state.snapshot()["record"]["active"], "the run starts OBS's recording"
        assert obs.state.snapshot()["record"]["active"] and obs.state.snapshot()["scene"] == sim.state.program, \
            "the probes read the real OBS through the mirror"
        sim.state.request("SetCurrentProgramScene", {"sceneName": "Standby"}, origin="ws")
        obs.state._synced = None
        assert obs.state.snapshot()["scene"] == "Standby", "the mirror follows OBS's program scene"
        problems = obs.stop()
        assert not sim.state.snapshot()["record"]["active"], "the recording is stopped before OBS quits"
        log = sessions[0].log
        assert log.index("StopRecord") < len(log) - 1 and log[-1] == "GetRecordStatus", \
            "the run waits until OBS reports the recording stopped"
        assert proc.signals == ["SIGINT"] and problems == [], (proc.signals, problems)
    finally:
        sim.stop()


class NotReady(Session):
    """OBS while it loads: every request answers 207 until `ready` requests were refused."""
    ready = 3

    def request(self, kind, data):
        if NotReady.ready > 0:
            NotReady.ready -= 1
            raise ValueError(f"{kind} failed: {{'code': 207, 'comment': 'OBS is not ready to perform the request.', "
                             "'result': False}")
        return super().request(kind, data)


def _strip(w, h, code, stint):
    """rgb24 bytes of a w x h picture that carries the stub's identity strip; code 0 is black."""
    rgb = bytearray(w * h * 3)
    for x, y, bw, bh, white in stream_stub.identity_boxes(w, h, code, stint) if code else ():
        for yy in range(y, y + bh):
            rgb[(yy * w + x) * 3:(yy * w + x + bw) * 3] = (b"\xff" if white else b"\x00") * (bw * 3)
    return bytes(rgb)


def _bmp(rgb, w, h, bpp=24, top_down=False, masks=None):
    """A BMP as Qt writes one: BITMAPINFOHEADER, BGR(A) rows padded to 4 bytes. `masks`
    (red, green, blue) writes a 32 bit BI_BITFIELDS picture with each channel at its mask."""
    step = bpp // 8
    pad = (-w * step) % 4
    rows = []
    for y in range(h):
        row = bytearray()
        for x in range(w):
            r, g, b = rgb[(y * w + x) * 3:(y * w + x) * 3 + 3]
            if masks:
                row += struct.pack("<I", sum(v * (m & -m) for v, m in zip((r, g, b), masks, strict=True)))
            else:
                row += bytes((b, g, r)) + (b"\xff" if step == 4 else b"")
        rows.append(bytes(row) + b"\x00" * pad)
    pixels = b"".join(rows if top_down else rows[::-1])
    extra = struct.pack("<III", *masks) if masks else b""
    info = struct.pack("<IiiHHIIiiII", 40, w, -h if top_down else h, 1, bpp, 3 if masks else 0, len(pixels),
                       2835, 2835, 0, 0)
    start = 54 + len(extra)
    return b"BM" + struct.pack("<IHHI", start + len(pixels), 0, 0, start) + info + extra + pixels


class _ShotSession:
    """The real OBS as the mirror asks it: Stint on program, and a picture per source."""

    def __init__(self, pictures):
        self.pictures, self.shots = pictures, []

    def request(self, kind, data):
        if kind == "GetCurrentProgramScene":
            return {"currentProgramSceneName": "Stint"}
        if kind == "GetSourceScreenshot":
            self.shots.append(dict(data))
            picture = self.pictures.get(data["sourceName"])
            if picture is None:
                raise ValueError("GetSourceScreenshot failed: {'code': 600}")
            return {"imageData": "data:image/bmp;base64," + base64.b64encode(picture).decode()}
        if kind in ("GetCurrentPreviewScene", "GetSceneItemList"):
            raise ValueError(f"{kind} failed: {{'code': 506}}")
        return {}


class _RelayFeed:
    """The grabber: the relay's feed shows comms1 whatever OBS plays."""

    def frames(self, urls):
        return {u: (_strip(640, 360, 1, 1), 640, 360) for u in urls}


def t_the_program_check_under_a_real_obs_reads_obs_s_own_picture_of_each_feed():
    w, h = 640, 360
    stub = SimpleNamespace(channels=lambda: {"comms1": {"code": 1}, "comms2": {"code": 2}})
    clock = fake.Clock()
    session = _ShotSession({"Feed A": _bmp(_strip(w, h, 2, 2), w, h)})
    mirror = obs_real.ObsMirror(_collection(), session, grabber=_RelayFeed(), clock=clock)
    probe = probes.ObsProbe(SimpleNamespace(state=mirror), stub, stream_stub.decode_identity)
    assert probe.program_channels == ["comms2"], \
        "the channel OBS plays in Feed A, not the one the relay serves on its port"
    assert [(s["sourceName"], s["imageFormat"]) for s in session.shots] == [("Feed A", "bmp")], \
        f"the feed source alone, under the Overlay's panel that covers the strip on program: {session.shots}"
    assert probe.program_channels == ["comms2"] and len(session.shots) == 1, "a check's polls share one picture"
    clock.t += obs_real.SNAPSHOT_S
    session.pictures["Feed A"] = _bmp(_strip(w, h, 0, 0), w, h, bpp=32, top_down=True)
    assert probe.program_channels == [], "a black picture in OBS shows no commentator, whatever the relay serves"
    clock.t += obs_real.SNAPSHOT_S
    del session.pictures["Feed A"]
    assert probe.program_channels == [], "a source OBS cannot picture shows no commentator"


def t_a_bmp_from_obs_reads_as_rgb_rows_top_down():
    w, h = 7, 3
    rgb = bytes(v for y in range(h) for x in range(w) for v in (x * 30, y * 80, x + y))
    for bpp in (24, 32):
        for top_down in (False, True):
            got = obs_real.bmp_rgb(_bmp(rgb, w, h, bpp, top_down))
            assert got == (rgb, w, h), f"{bpp} bpp, top_down={top_down}"
    for bad in (b"", b"PNG", _bmp(rgb, w, h)[:60]):
        try:
            obs_real.bmp_rgb(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad[:8]!r}: not a whole BMP")
    rgba = (0x000000FF, 0x0000FF00, 0x00FF0000)
    assert obs_real.bmp_rgb(_bmp(rgb, w, h, 32, masks=rgba)) == (rgb, w, h), "bit fields put red first here"
    assert obs_real.bmp_rgb(_bmp(rgb, w, h, 32, masks=(0x00FF0000, 0x0000FF00, 0x000000FF))) == (rgb, w, h)
    for bad, why in ((_bmp(rgb, w, h, 32, masks=(0x7C00, 0x03E0, 0x001F)), "a 5 bit channel"),
                     (_bmp(rgb, w, h, 32, masks=(0xFF, 0xFF, 0xFF00)), "two channels on one byte")):
        try:
            obs_real.bmp_rgb(bad)
        except ValueError:
            continue
        raise AssertionError(f"{why}: refused, not read as noise")


def t_an_obs_that_is_not_ready_yet_is_asked_again_until_the_start_timeout():
    sim = obs_sim.ObsSim(collection=_collection()).start()
    try:
        for ready, fails in ((3, False), (10**9, True)):
            NotReady.ready = ready
            now, proc = [0.0], Proc()
            obs = obs_real.RealObs(_tmp(), _tmp(), _collection(sim.state.collection), platform="linux",
                                   launcher=lambda argv, proc=proc, **o: proc,
                                   connect=lambda *a: NotReady(sim.port), kill=proc.signal, killpg=proc.signal,
                                   binary=sys.executable, port=sim.port, clock=lambda now=now: now[0],
                                   sleep=lambda s, now=now: now.__setitem__(0, now[0] + s))
            try:
                obs.start()
            except RuntimeError as exc:
                assert fails and "not ready" in str(exc) and f"{obs_real.START_TIMEOUT_S}s" in str(exc), \
                    f"an OBS that never gets ready fails at the start timeout with its last answer: {exc}"
                assert proc.signals == ["SIGINT"], "and the OBS the run started is quit"
                continue
            assert not fails, "an OBS that never gets ready must fail the start"
            assert obs.version == "33.0.3" and NotReady.ready == 0, \
                "a request OBS answers with 207 (not ready) is asked again until it succeeds"
            obs.stop()
        now, proc = [0.0], Proc()

        class Broken(Session):
            def request(self, kind, data):
                raise ValueError(f"{kind} failed: {{'code': 600, 'comment': 'bad', 'result': False}}")
        obs = obs_real.RealObs(_tmp(), _tmp(), _collection(), platform="linux", launcher=lambda argv, **o: proc,
                               connect=lambda *a: Broken(sim.port), kill=proc.signal, killpg=proc.signal,
                               binary=sys.executable, port=sim.port, clock=lambda: now[0],
                               sleep=lambda s: now.__setitem__(0, now[0] + s))
        try:
            obs.start()
        except ValueError as exc:
            assert "600" in str(exc) and now[0] == 0.0, f"any other refusal fails at once, without waiting: {exc}"
        else:
            raise AssertionError("an OBS that refuses GetVersion must fail the start")
    finally:
        NotReady.ready = 0
        sim.stop()


def t_the_program_recording_starts_when_obs_answers_start_record():
    now = [100.0]

    class Slow:
        def request(self, kind, data):
            if kind == "GetRecordStatus":
                now[0] += 0.5          # each poll takes its time
                return {"outputActive": now[0] >= 101.0}
            return {}
    obs = obs_real.RealObs(_tmp(), _tmp(), _collection(), platform="linux", binary=sys.executable, port=1,
                           clock=lambda: now[0], sleep=lambda s: None)
    obs.session = Slow()
    at = obs.start_record()
    assert at == 100.0 and now[0] == 101.0, f"the start is taken at StartRecord's answer, not after the polls: {at}"
    rec = record.Recorder(_tmp(), clock=lambda: 999.0)
    rec.program_started(None, size=None, at=at)
    assert rec.timeline(SimpleNamespace(name="t", steps=[]), [], start=100.5)["recordings"][0]["start"] == -0.5, \
        "the timeline carries that moment"


def t_an_obs_that_ignores_sigint_gets_sigterm_and_only_then_its_group_is_killed():
    for obeys, want, problem in ((("SIGTERM",), ["SIGINT", "SIGTERM"], False),
                                 ((), ["SIGINT", "SIGTERM", "SIGKILL"], True)):
        proc = Proc(obeys)
        obs = obs_real.RealObs(_tmp(), _tmp(), _collection(), platform="linux", kill=proc.signal,
                               killpg=lambda pid, sig, proc=proc: proc.signal(pid, "SIGKILL"),
                               binary=sys.executable, port=1)
        obs.proc = proc
        got = obs.quit()
        assert proc.signals == want, proc.signals
        assert bool(got) == problem, f"a killed OBS is a problem in the report: {got}"


def t_an_obs_that_runs_the_wrong_profile_is_quit_and_refused():
    sim = obs_sim.ObsSim(collection=_collection("Another")).start()
    try:
        proc = Proc()
        obs, _, _ = _real(sim, proc, _tmp(), _tmp())
        obs.collection = _collection()
        obs.name = DEMO
        try:
            obs.start()
        except RuntimeError as exc:
            assert "Another" in str(exc), exc
        else:
            raise AssertionError("an OBS on another collection must be refused")
        assert proc.signals == ["SIGINT"], "a refused start still quits the OBS it started"
        Session.version = "31.0.0"
        old, _, _ = _real(sim, Proc(), _tmp(), _tmp())
        old.collection, old.name = _collection("Another"), "Another"
        old.start()
        assert "quits on a signal only from 32.1" in old.stop()[0], "an older OBS's quit is reported as not graceful"
    finally:
        Session.version = "33.0.3"
        sim.stop()


def t_the_localized_collection_points_into_the_app_home_and_captures_nothing():
    home = _tmp()
    out = obs_real.localize(_collection("x"), home, DEMO, os.path.join(ROOT, "profiles", "demo"))
    text = json.dumps(out)
    assert "__RACECAST_GRAPHICS__" not in text and "__RACECAST_MEDIA__" not in text, "every path token is localized"
    files = [os.path.normpath(s["settings"]["local_file"]) for s in out["sources"] if s["settings"].get("local_file")]
    assert os.path.join(home, "runtime", "demo", "media", "intro.mp4") in files, "the intro plays the generated clip"
    kinds = {s["id"] for s in out["sources"]}
    assert not kinds & obs_real.DEVICE_KINDS, f"no capture input opens a device or a permission dialog: {kinds}"
    names = {s["name"] for s in out["sources"]}
    assert {"Commentary Mic Device", "Discord Audio Capture"} <= names, "the relay still finds the inputs by name"
    assert out["name"] == DEMO, "the collection carries the profile's OBS_COLLECTION, the one Start event expects"
    assert os.listdir(os.path.join(home, "runtime", "demo", "graphics")), "missing graphics get placeholders"


def _mac(home, running=False, ditto=None):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if argv[0] == "pgrep":
            return SimpleNamespace(returncode=0 if running else 1)
        (ditto or (lambda a, b: shutil.copytree(a, b, dirs_exist_ok=True)))(argv[1], argv[2])
        return SimpleNamespace(returncode=0)
    return obs_real.MacConfig(home, run=run), calls


def t_a_real_obs_stack_needs_a_record_dir_and_the_program_file_lands_on_the_timeline():
    import stack
    st = stack.Stack(SimpleNamespace(), _tmp(), record=True, obs="real", record_dir="/rec", evidence_dir="/out/logs")
    assert st.evidence_dir == "/out/logs", "the real OBS's evidence goes to the run's logs"
    try:
        stack.Stack(SimpleNamespace(), _tmp(), obs="real")
    except ValueError as exc:
        assert "--record" in str(exc), exc
    else:
        raise AssertionError("a real OBS without a record dir must be refused")
    rec = record.Recorder(_tmp(), clock=lambda: 5.0)
    rec.program_started(None, size={"width": 1920, "height": 1080})
    rec.program_file(os.path.join(rec.dir, "program", "2026-11-25 10-00-00.mkv"))
    tl = rec.timeline(SimpleNamespace(name="t", steps=[]), [], start=6.0)
    assert tl["recordings"] == [{"view": "program", "kind": "program", "file": "program/2026-11-25 10-00-00.mkv",
                                 "size": {"width": 1920, "height": 1080}, "start": -1.0}], tl["recordings"]


def t_the_rtmp_sink_listens_on_loopback_again_after_every_part():
    argv = rtmp_sink.sink_argv("ffmpeg", 5555)
    assert argv[argv.index("-listen") + 1] == "1" and argv[argv.index("-i") + 1].startswith("rtmp://127.0.0.1:5555/"), \
        f"the sink only listens on loopback: {argv}"
    assert argv[-3:] == ["-f", "null", "-"] and "-threads" in argv, "the stream is read and dropped, on one thread"
    turns = iter([True, True, True, False])
    procs = []

    def popen(argv, **kw):
        procs.append(SimpleNamespace(wait=lambda: 0, poll=lambda: 0, kill=lambda: None))
        return procs[-1]
    assert rtmp_sink.serve(5555, popen=popen, running=lambda: next(turns), sleep=lambda s: None) == 3, \
        "a new listener follows each publisher that ended, for the next Part"
    assert rtmp_sink.url(5555) == "rtmp://127.0.0.1:5555/live"


def t_the_sink_stops_after_the_obs_that_streams_to_it():
    import stack
    order = []
    st = stack.Stack(SimpleNamespace(), _tmp(), record=True, obs="real", record_dir=_tmp())
    st.obs = SimpleNamespace(stop=lambda: order.append("obs") or ["OBS note"])
    st.rtmp = SimpleNamespace(stop=lambda: order.append("sink") or "", name="rtmp-sink", log_path="x")
    problems = st.stop()
    assert order == ["obs", "sink"], f"OBS quits before its stream sink goes away: {order}"
    assert "OBS note" in problems, "what the real OBS met on its way out reaches the report"
    st.obs.password = "obs-pass-1234"
    assert set(st.run_secrets()) == {st.secret, "obs-pass-1234"}, \
        "the real OBS's password is a credential the report's redaction hides"


def t_the_real_obs_profile_streams_to_the_sink_and_racecast_learns_it():
    files = obs_real.config_files(4455, "/rec", _collection(), "linux", rtmp="rtmp://127.0.0.1:5555/live")
    service = json.loads(files["basic/profiles/Sim/service.json"])
    assert service == {"type": "rtmp_custom", "settings": {"server": "rtmp://127.0.0.1:5555/live", "key": "sim",
                                                           "use_auth": False}}, service
    obs = obs_real.RealObs(_tmp(), _tmp(), _collection(), platform="linux", binary=sys.executable, port=1,
                           rtmp="rtmp://127.0.0.1:5555/live")
    assert obs.env()["RACECAST_SIM_RTMP"] == "rtmp://127.0.0.1:5555/live", \
        "a Part's stream target is the sink, so the real OBS really streams and nothing leaves loopback"
    try:
        obs_real.RealObs(_tmp(), _tmp(), _collection(), platform="linux", binary=sys.executable, port=1,
                         rtmp="rtmp://a.rtmp.youtube.com/live2")
    except RuntimeError as exc:
        assert "loopback" in str(exc), exc
    else:
        raise AssertionError("a stream target off loopback must be refused")


def _urls(value):
    if isinstance(value, dict):
        return [u for v in value.values() for u in _urls(v)]
    if isinstance(value, list):
        return [u for v in value for u in _urls(v)]
    return [value] if isinstance(value, str) and value.startswith("http") else []


def t_the_real_obs_collection_reaches_the_run_s_relay_and_never_8088():
    import stack
    from urllib.parse import urlsplit
    out = obs_real.localize(stack.run_collection((50001, 50002, 50003), 50088), _tmp(), DEMO,
                            os.path.join(ROOT, "profiles", "demo"))
    urls = _urls(out)
    pages = {s["name"]: s["settings"]["url"] for s in out["sources"] if s["id"] == "browser_source"}
    assert pages and all(urlsplit(u).port == 50088 for u in pages.values()), \
        f"every relay page in OBS (HUD, splitscreen, ...) is served by the run's relay: {pages}"
    assert not [u for u in urls if urlsplit(u).port in (8088, 8089, 53001, 53002, 53003)], \
        f"nothing in the collection reaches a live relay or Control Center port: {urls}"


def t_the_real_obs_leaves_its_state_hud_picture_and_log_in_the_run_s_logs():
    sim = obs_sim.ObsSim(collection=_collection()).start()
    try:
        work, logs, proc = _tmp(), _tmp(), Proc()
        obs, _, _ = _real(sim, proc, work, _tmp(), evidence=logs)
        obs_logs = os.path.join(obs.config.root, "logs")
        os.makedirs(obs_logs)
        with open(os.path.join(obs_logs, "2026-11-24 09-00-00.txt"), "w", encoding="utf-8") as fh:
            fh.write("an earlier session of the user\n")
        obs.start()
        assert os.path.isfile(os.path.join(logs, "obs-state.json")), "the start writes OBS's state into the run's logs"
        with open(os.path.join(logs, "obs-state.json"), encoding="utf-8") as fh:
            states = json.load(fh)
        first = states[0]
        assert first.get("when") == "start" and first.get("program_scene") == sim.state.program, \
            f"each state names when it was taken and the program scene: {first}"
        assert first["items"] and {"name", "id", "enabled"} <= set(first["items"][0]), \
            "the state lists the program scene's items with their visibility"
        hud = first["browser_sources"]["HUD Overlay"]
        assert hud == {"url": "http://127.0.0.1:8088/hud", "width": 1920, "height": 1080}, \
            f"each browser source with the url, width and height OBS reports: {hud}"
        start_png = os.path.join(logs, "hud-source-start.png")
        assert os.path.isfile(start_png) and os.path.getsize(start_png) > 0, "the HUD source alone, at the start"
        sim.state.request("SetCurrentProgramScene", {"sceneName": "Stint"}, origin="ws")
        obs.state._synced = None
        obs.state.snapshot()
        obs.state._synced = None
        obs.state.snapshot()
        with open(os.path.join(logs, "obs-state.json"), encoding="utf-8") as fh:
            states = json.load(fh)
        assert [s["when"] for s in states] == ["start", "stint"] and states[1]["program_scene"] == "Stint", \
            f"a second state once the first Stint is on air, and only once: {[s['when'] for s in states]}"
        stint_png = os.path.join(logs, "hud-source.png")
        assert os.path.isfile(stint_png) and os.path.getsize(stint_png) > 0, "the HUD source alone while Stint is on air"
        with open(os.path.join(obs_logs, "2026-11-25 10-00-00.txt"), "w", encoding="utf-8") as fh:
            fh.write("browser_source: loaded\n")
        redact = obs.redact
        obs.redact = lambda text: redact(text).replace("loaded", "loaded (redacted copy)")
        obs.stop()
        obs.redact = redact
        assert os.path.isfile(os.path.join(logs, "obs.log")), "the run OBS's log is copied before the restore"
        with open(os.path.join(logs, "obs.log"), encoding="utf-8") as fh:
            text = fh.read()
        assert "browser_source: loaded" in text and "earlier session" not in text, \
            "the run OBS's own log is kept, not the user's older ones"
        assert "loaded (redacted copy)" in text, "the copy goes through the run OBS's redaction"
        line = "Command Line Arguments: --websocket_password " + obs.password
        assert obs.password not in obs.redact(line), \
            "OBS logs its command line: the copy of its log passes it through the password's redaction"
        with open(os.path.join(logs, "obs-state.json"), encoding="utf-8") as fh:
            assert obs.password not in fh.read(), "obs-state.json never carries the WebSocket password"
    finally:
        sim.stop()


def t_the_real_obs_launches_only_once_the_relay_answers_and_reloads_its_pages_once():
    sim = obs_sim.ObsSim(collection=_collection()).start()
    try:
        answers, launched = iter([False, False, True]), []
        proc = Proc()
        obs, sessions, _ = _real(sim, proc, _tmp(), _tmp(), relay_ready=lambda: next(answers), evidence=_tmp())
        obs.launcher = lambda argv, **o: launched.append(next(answers, "after")) or proc
        obs.start()
        assert launched == ["after"], "OBS starts only after the relay answered, so its pages load live"
        pages = [src["name"] for src in obs.collection["sources"] if src["id"] == "browser_source"]
        log = sessions[0].log
        assert log.count("PressInputPropertiesButton") == len(pages), \
            "after the start every relay page is reloaded once, in case one loaded an error page"
        assert sorted(obs.states[0].get("refreshed") or []) == sorted(pages), "the state notes the reload"
        obs.stop()
        never, launched = Proc(), []
        ticks = iter(range(0, 10**6, 5))
        late, _, _ = _real(sim, never, _tmp(), _tmp(), relay_ready=lambda: False, clock=lambda: next(ticks))
        late.launcher = lambda argv, **o: launched.append(1) or never
        try:
            late.start()
        except RuntimeError as exc:
            assert "relay" in str(exc) and launched == [], f"no relay, no OBS: {exc}"
        else:
            raise AssertionError("an OBS whose relay never answers must not start")
    finally:
        sim.stop()


def t_the_stack_probes_the_relay_before_it_starts_obs():
    import stack
    assert stack.relay_answers(f"http://127.0.0.1:{stack.free_port()}") is False, \
        "a port without a relay is no relay: OBS waits"


def t_an_obs_that_loaded_another_collection_of_that_name_is_refused():
    theirs = _collection()      # the user's import: its relay pages on 8088
    sim = obs_sim.ObsSim(collection=theirs).start()
    try:
        proc = Proc()
        obs, _, _ = _real(sim, proc, _tmp(), _tmp())
        ours = copy.deepcopy(theirs)
        for src in ours["sources"]:
            if src["id"] == "browser_source":
                src["settings"]["url"] = src["settings"]["url"].replace(":8088", ":50088")
        obs.collection = ours
        try:
            obs.start()
        except RuntimeError as exc:
            assert "another collection" in str(exc), exc
        else:
            raise AssertionError("an OBS that loaded another collection under the run's name must be refused")
        assert proc.signals == ["SIGINT"], "and the OBS the run started is quit"
    finally:
        sim.stop()


def t_on_macos_the_users_configuration_is_backed_up_and_restored_byte_for_byte():
    home = _tmp()
    real = obs_real.mac_config_dir(home)
    os.makedirs(os.path.join(real, "basic", "profiles", "Mine"))
    with open(os.path.join(real, "basic", "profiles", "Mine", "basic.ini"), "w", encoding="utf-8") as fh:
        fh.write("[General]\nName=Mine\n")
    before = obs_real.digest(real)
    cfg, _ = _mac(home)
    cfg.prepare()
    assert os.path.isdir(obs_real.mac_backup_dir(home)), "the backup lives outside the run's work dir"
    files = obs_real.config_files(4455, "/rec", _collection(), "darwin")
    assert "user.ini" not in files and "global.ini" not in files, "on macOS only the Sim files are written"
    for rel, text in files.items():
        path = os.path.join(real, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    assert cfg.restore() == [] and obs_real.digest(real) == before, "the folder is byte-identical afterwards"
    assert not os.path.exists(obs_real.mac_backup_dir(home)), "a verified restore removes the backup"
    assert obs_real.mac_backup_dir(home).startswith(home + "/Library/Application Support/"), \
        "the macOS paths are built with forward slashes"


def t_on_macos_a_failed_restore_keeps_the_backup_and_a_left_backup_refuses_the_run():
    home = _tmp()
    os.makedirs(obs_real.mac_config_dir(home))
    with open(os.path.join(obs_real.mac_config_dir(home), "global.ini"), "w", encoding="utf-8") as fh:
        fh.write("x")

    def lossy(a, b):
        shutil.copytree(a, b, dirs_exist_ok=True)
        if a.endswith("backup"):
            with open(os.path.join(b, "global.ini"), "w", encoding="utf-8") as fh:
                fh.write("changed")
    cfg, _ = _mac(home, ditto=lossy)
    cfg.prepare()
    problems = cfg.restore()
    assert problems and "global.ini" in problems[0] and os.path.isdir(obs_real.mac_backup_dir(home)), \
        f"a restore that differs keeps the backup and says so: {problems}"
    again, _ = _mac(home)
    try:
        again.prepare()
    except RuntimeError as exc:
        assert "earlier run" in str(exc) and "event_sim.py --restore-obs" in str(exc), \
            f"the refusal names the recovery command: {exc}"
    else:
        raise AssertionError("a left backup must refuse the next run")
    shutil.rmtree(obs_real.mac_backup_dir(home))
    busy, calls = _mac(home, running=True)
    try:
        busy.prepare()
    except RuntimeError as exc:
        assert "OBS is running" in str(exc) and not os.path.exists(obs_real.mac_backup_dir(home)), exc
    else:
        raise AssertionError("a running OBS must refuse the run before anything is copied")


def _complete(home):
    with open(obs_real.mac_backup_mark(home), "w", encoding="utf-8") as fh:
        fh.write("verified\n")


def t_restore_obs_recovers_a_left_backup_and_keeps_it_when_it_differs():
    home = _tmp()
    real, backup = obs_real.mac_config_dir(home), obs_real.mac_backup_dir(home)
    os.makedirs(os.path.join(backup, "basic"))
    with open(os.path.join(backup, "basic", "user.ini"), "w", encoding="utf-8") as fh:
        fh.write("mine")
    _complete(home)
    os.makedirs(os.path.join(real, "basic", "profiles", "Sim"))     # what a killed run left behind
    _, calls = _mac(home, running=True)
    busy = obs_real.restore_obs(home=home, run=_mac(home, running=True)[0].run, platform="darwin")
    assert busy[0] == 2 and "OBS is running" in busy[1] and os.path.isdir(backup), \
        "nothing is restored while OBS runs"
    code, text = obs_real.restore_obs(home=home, run=_mac(home)[0].run, platform="darwin")
    assert code == 0 and not os.path.exists(backup), text
    assert obs_real.digest(real) == {os.path.join("basic", "user.ini"): obs_real.hashlib.sha256(b"mine").hexdigest()}, \
        "the folder is the backup again, the Sim files of the killed run are gone"
    assert obs_real.restore_obs(home=home, run=_mac(home)[0].run, platform="darwin")[0] == 2, \
        "without a backup there is nothing to restore"
    assert not os.path.exists(obs_real.mac_backup_mark(home)), "the completion mark goes with the backup"
    os.makedirs(backup)
    with open(os.path.join(backup, "global.ini"), "w", encoding="utf-8") as fh:
        fh.write("x")
    _complete(home)

    def lossy(a, b):
        shutil.copytree(a, b, dirs_exist_ok=True)
        with open(os.path.join(b, "global.ini"), "w", encoding="utf-8") as fh:
            fh.write("changed")
    code, text = obs_real.restore_obs(home=home, run=_mac(home, ditto=lossy)[0].run, platform="darwin")
    assert code == 1 and "global.ini" in text and os.path.isdir(backup), \
        f"a restore that differs keeps the backup: {text}"
    assert os.path.isdir(os.path.join(real, "basic")), "a copy that differs is never swapped in"
    assert obs_real.restore_obs(home=home, platform="linux")[0] == 2, \
        "on Linux the run never touched the user's configuration: there is nothing to restore"


def t_an_interrupted_or_failed_backup_copy_leaves_no_backup_behind():
    home = _tmp()
    real = obs_real.mac_config_dir(home)
    os.makedirs(os.path.join(real, "basic"))
    for name in ("a.ini", "b.ini"):
        with open(os.path.join(real, "basic", name), "w", encoding="utf-8") as fh:
            fh.write(name)
    before = obs_real.digest(real)

    def interrupted(a, b):
        os.makedirs(os.path.join(b, "basic"))
        shutil.copy(os.path.join(a, "basic", "a.ini"), os.path.join(b, "basic", "a.ini"))
        raise KeyboardInterrupt
    for ditto, error in ((interrupted, KeyboardInterrupt),
                         (lambda a, b: (_ for _ in ()).throw(obs_real.subprocess.CalledProcessError(1, "ditto")),
                          obs_real.subprocess.CalledProcessError)):
        cfg, _ = _mac(home, ditto=ditto)
        try:
            cfg.prepare()
        except error:
            pass  # the copy broke off
        else:
            raise AssertionError("a broken copy must stop the run")
        left = [n for n in os.listdir(os.path.dirname(real)) if n != "obs-studio"]
        assert left == [] and not cfg.prepared, f"a partial copy is removed, never left as a backup: {left}"
        assert obs_real.digest(real) == before, "the user's folder is untouched"
    ok, _ = _mac(home)
    ok.prepare()
    assert ok.prepared and os.path.exists(obs_real.mac_backup_mark(home)), "a verified copy is marked complete"


def t_an_unmarked_backup_is_never_restored_and_a_missing_folder_is():
    home = _tmp()
    real, backup = obs_real.mac_config_dir(home), obs_real.mac_backup_dir(home)
    os.makedirs(os.path.join(real, "basic"))
    os.makedirs(backup)     # a copy that broke off: no completion mark
    code, text = obs_real.restore_obs(home=home, run=_mac(home)[0].run, platform="darwin")
    assert code == 2 and "not complete" in text and os.path.isdir(os.path.join(real, "basic")), \
        f"an unmarked backup is refused and the user's folder stays: {text}"
    try:
        _mac(home)[0].prepare()
    except RuntimeError as exc:
        assert "not complete" in str(exc), f"the next run names the unmarked backup: {exc}"
    else:
        raise AssertionError("an unmarked backup must refuse the next run")
    with open(os.path.join(backup, "user.ini"), "w", encoding="utf-8") as fh:
        fh.write("mine")
    _complete(home)
    shutil.rmtree(real)     # a run that died between removing the folder and renaming the copy
    code, text = obs_real.restore_obs(home=home, run=_mac(home)[0].run, platform="darwin")
    assert code == 0 and obs_real.digest(real) == {"user.ini": obs_real.hashlib.sha256(b"mine").hexdigest()}, \
        f"a missing folder is restored from the complete backup: {text}"
    os.makedirs(backup)
    with open(os.path.join(backup, "user.ini"), "w", encoding="utf-8") as fh:
        fh.write("mine")
    _complete(home)
    real_digest = obs_real.digest

    def unreadable(root):
        if root == real:
            raise PermissionError(root)       # the restored folder cannot be read back
        return real_digest(root)
    obs_real.digest = unreadable
    try:
        code, text = obs_real.restore_obs(home=home, run=_mac(home)[0].run, platform="darwin")
    finally:
        obs_real.digest = real_digest
    assert code == 1 and "PermissionError" in text and os.path.isdir(backup), \
        f"a folder that cannot be read back keeps the backup and says so: {text}"


def t_event_sim_restore_obs_needs_no_scenario():
    old = obs_real.restore_obs
    try:
        obs_real.restore_obs = lambda: (0, "restored")
        assert event_sim.main(["--restore-obs"]) == 0, "--restore-obs runs on its own"
        obs_real.restore_obs = lambda: (1, "differs")
        assert event_sim.main(["--restore-obs"]) == 1
    finally:
        obs_real.restore_obs = old


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")

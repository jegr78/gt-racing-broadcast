#!/usr/bin/env python3
"""The event walkthrough film's renderer (#1312): pace and focus rules, the frame plan,
the stage layout, chapters.txt and captions.vtt, and the render commands. No video, no
ffmpeg run; the stage page renders only where Playwright is installed.
Run: python3 tests/test_event_video.py"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VIDEO = os.path.join(ROOT, "tools", "event-video")
sys.path.insert(0, VIDEO)
import film_plan as fp  # noqa: E402
import film_render as fr  # noqa: E402

_spec = importlib.util.spec_from_file_location("build_event_video", os.path.join(ROOT, "tools", "build-event-video.py"))
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)

PAGE = {"width": 1440, "height": 900}
EPS = 1e-6


def _rec(view, start, size=PAGE, kind="page", file=None):
    return {"view": view, "kind": kind, "file": file or f"pages/{view.replace(':', '_')}/page@1.webm",
            "size": size, "start": start}


def _step(sid, role, start, acted, end, **extra):
    return {"id": sid, "role": role, "action": "x", "status": "ok", "start": start, "acted": acted, "end": end,
            **extra}


QUARTERS = ["director", "program", "commentator:Comms 1", "commentator:Comms 2"]


def timeline():
    """A fixed small run: a start, a long wait, a director step with focus, own-role steps,
    a four-view switch, an uncaptioned cockpit step and a focus on a view off the stage."""
    return {"scenario": "fixture", "steps": [
        _step("boot", "api", 0.0, 0.2, 1.0),
        _step("pick", "producer", 1.0, 2.0, 3.0, chapter="Start the event", caption="The producer picks the track"),
        _step("lap", "api", 3.0, 3.1, 60.0),
        _step("approve", "director", 60.0, 62.0, 65.0, caption="The director approves the link",
              focus=["director", "program"]),
        _step("desk", "race_control", 65.0, 67.0, 80.0, caption="Race Control confirms the disconnect of #33"),
        _step("wait2", "api", 80.0, 80.1, 100.0),
        _step("switch", "director", 100.0, 102.0, 104.0, chapter="Driver swap 1",
              caption="The director switches to stint 2", focus=QUARTERS),
        _step("cockpit", "commentator:Comms 1", 110.0, 111.0, 111.5),
        _step("tower", "race_control", 115.0, 116.0, 117.0, caption="Race Control checks the tower <now> & then",
              focus=["page:overlay"]),
        _step("end", "api", 117.0, 117.1, 130.0),
    ], "recordings": [
        _rec("program", -2.0, {"width": 1920, "height": 1080}, "program", "program/2026-11-25 10-00-00.mkv"),
        _rec("producer", 0.5), _rec("director", 0.5), _rec("race_control", 0.6),
        _rec("commentator:Comms 1", 0.7), _rec("commentator:Comms 2", 0.8), _rec("viewer", 0.9),
        _rec("driver", 5.0, {"width": 390, "height": 844}), _rec("page:overlay", 1.0, {"width": 1920, "height": 1080}),
        _rec("director", 50.0, file="pages/director/page@second.webm"),
    ]}


def plan(**kw):
    return fp.Plan(timeline(), **kw)


def frames(p):
    return [p.frame(i) for i in range(p.frames)]


def _captioned(p):
    return [s for s in p.steps if s.get("caption")]


# pace


def t_a_captioned_step_plays_its_action_in_real_time_frame_by_frame():
    p = plan()
    fs = frames(p)
    for s in _captioned(p):
        inside = [f for f in fs if s["start"] <= f["t"] <= s["acted"]]
        assert inside, f"{s['id']} has frames"
        assert all(f["speed"] == 1 and not f["ff"] for f in inside), f"{s['id']} must never be sped up"
        for a, b in zip(inside, inside[1:], strict=False):
            assert abs((b["t"] - a["t"]) - 1 / p.fps) < 1e-6, f"{s['id']} advances one frame of source per frame"


def t_the_lead_in_and_the_hold_play_in_real_time_and_acted_to_end_is_waiting():
    p = plan()
    lead, hold = fp.LEAD_IN_S, fp.HOLD_S
    assert p.speed_at(60.0 - lead + 0.01) == 1, "the lead-in is real time"
    assert p.speed_at(60.0 - lead - 0.01) == p.wait_speed, "before the lead-in the wait is sped up"
    assert p.speed_at(67.0 + hold - 0.01) == 1, "the hold is real time"
    assert p.speed_at(75.0) == p.wait_speed, "after the hold, a step waiting for its expects is sped up"


def t_waiting_is_sped_up_and_shows_the_fast_forward_mark():
    p = plan()
    fs = frames(p)
    waiting = [f for f in fs if 8.0 < f["t"] < 55.0]
    assert waiting and all(f["speed"] == p.wait_speed and f["ff"] for f in waiting), "the lap wait is sped up"
    out = p.out_time(55.0) - p.out_time(8.0)
    assert abs(out - 47.0 / p.wait_speed) < 1e-6, f"47 s of waiting take {out}"


def t_a_short_wait_plays_in_real_time():
    p = plan()
    assert p.speed_at(108.0) == p.wait_speed, "the gap before the cockpit step is long enough to speed up"
    q = fp.Plan(timeline(), min_wait=20.0)
    assert q.speed_at(108.0) == 1, "a gap shorter than min_wait plays in real time"


def t_no_source_time_is_skipped_and_output_time_is_monotonic():
    p = plan()
    fs = frames(p)
    ts = [f["t"] for f in fs]
    assert abs(ts[0] - p.t0) < EPS and p.t0 == 0.0, "the film starts at the first step"
    assert all(b > a for a, b in zip(ts, ts[1:], strict=False)), "source time only moves forward"
    step = p.wait_speed / p.fps + 1e-6
    assert all(b - a <= step for a, b in zip(ts, ts[1:], strict=False)), "no frame skips more than one frame of waiting"
    assert ts[-1] >= p.t1 - step, "the film reaches the last step's end"
    segs = p.segments
    assert segs[0].a == p.t0 and segs[-1].b == p.t1, "the segments cover the whole run"
    assert all(abs(x.b - y.a) < EPS for x, y in zip(segs, segs[1:], strict=False)), "the segments are contiguous"
    outs = [p.out_time(t) for t in ts]
    assert all(abs(o - i / p.fps) < 1e-6 for i, o in enumerate(outs)), "frame i stands at i/fps in the film"


def t_from_and_to_cut_a_partial_render_out_of_the_same_film():
    p = plan()
    a, b = p.frame_range(10.0, 12.0)
    assert (a, b) == (300, 360), (a, b)
    assert p.frame_range(None, None) == (0, p.frames)
    assert p.frame_range(0, 1e9) == (0, p.frames), "a range past the end stops at the last frame"
    for start, stop in ((12.0, 10.0), (10.0, 10.0)):
        try:
            p.frame_range(start, stop)
        except ValueError:
            continue
        raise AssertionError(f"the empty range {start}-{stop} is refused")


# focus


def _at(p, t):
    """The frame whose source time is first at or after t."""
    return p.frame(next(i for i in range(p.frames) if p.src_time(i / p.fps) >= t - EPS))


def t_the_running_step_s_own_role_takes_a_large_slot():
    p = plan()
    f = _at(p, 2.0)
    assert f["large"] == ["producer"], f["large"]
    f = _at(p, 66.0)
    assert f["large"][0] == "race_control", "the own role takes the first large slot"
    assert f["large"] == ["race_control", "director"], "the second slot keeps the previous first view"


def t_a_step_s_focus_overrides_its_role():
    p = plan()
    assert _at(p, 61.0)["large"] == ["director", "program"], "focus names the large views"


def t_a_focus_on_views_off_the_stage_falls_back_to_the_role():
    p = plan()
    assert "page:overlay" not in p.views, "the overlay page is not on the stage: the program shows it"
    assert _at(p, 115.5)["large"][0] == "race_control"


def t_at_most_two_views_are_large_except_four_quarters_for_a_four_view_step():
    p = plan()
    quarters = 0
    for f in frames(p):
        if f["quarters"]:
            quarters += 1
            assert f["large"] == QUARTERS, f["large"]
        else:
            assert 1 <= len(f["large"]) <= 2, f["large"]
    assert quarters, "the four-view switch lays out quarters"
    assert _at(p, 101.0)["quarters"] and not _at(p, 66.0)["quarters"]
    two = timeline()
    two["steps"][6]["focus"] = QUARTERS[:2]
    assert not any(f["quarters"] for f in frames(fp.Plan(two))), "a step naming two views never lays out quarters"
    three = timeline()
    three["steps"][6]["focus"] = ["director", "program", "viewer"]
    t3 = fp.Plan(three)
    assert not any(f["quarters"] for f in frames(t3)), "three views are no quarters"
    assert all(len(f["large"]) <= 2 for f in frames(t3)), "three views keep at most two large"


def t_an_uncaptioned_step_lights_its_ring_but_keeps_the_layout():
    p = plan()
    before = _at(p, 108.0)
    during = _at(p, 110.5)
    assert during["large"] == before["large"], "the cockpit step moves nothing"
    assert "commentator:Comms 1" in during["rings"] and "commentator:Comms 1" not in before["rings"]
    assert "director" in _at(p, 61.0)["rings"], "the acting view has its ring"


def t_layout_changes_animate_and_never_cut():
    p = plan()
    fs = frames(p)
    limit = 0
    for a, b in zip(fs, fs[1:], strict=False):
        for v in p.views:
            ra, rb = a["rects"][v], b["rects"][v]
            limit = max(limit, max(abs(x - y) for x, y in zip(ra[:4], rb[:4], strict=True)))
    assert limit < 150, f"a view moved {limit:.0f} px in one frame: a cut"
    i = next(i for i, f in enumerate(fs) if f["large"] == ["director", "program"])
    mid = fs[i + int(fp.MOVE_S * p.fps / 2)]
    done = fs[i + int(fp.MOVE_S * p.fps) + 1]
    assert mid["rects"]["program"] != done["rects"]["program"], "half way the program is still moving"
    assert done["rects"]["program"] == fs[i + int(fp.MOVE_S * p.fps) + 5]["rects"]["program"], "after 600 ms it rests"


# recordings


def t_a_view_shows_its_latest_recording_from_its_start_on():
    p = plan()
    f = _at(p, 2.0)
    assert f["sources"]["driver"] is None, "the driver page has not opened yet"
    rec, t = f["sources"]["director"]
    first = p.recordings[rec]
    assert first["file"] == "pages/director/page@1.webm"
    assert abs(t - (f["t"] - first["start"])) < EPS, "a view's time is the run time minus its start"
    rec2, t2 = _at(p, 61.0)["sources"]["director"]
    assert p.recordings[rec2]["file"] == "pages/director/page@second.webm", "the reopened page takes over"
    assert abs(t2 - (_at(p, 61.0)["t"] - 50.0)) < EPS
    prog = _at(p, 2.0)["sources"]["program"]
    assert abs(prog[1] - (_at(p, 2.0)["t"] + 2.0)) < EPS, "a recording started before the engine is ahead"


def t_a_recording_s_time_stops_at_its_known_duration():
    p = fp.Plan(timeline(), durations={"pages/driver/page@1.webm": 10.0})
    _, t = _at(p, 60.0)["sources"]["driver"]
    assert t == round(10.0 - 1 / p.fps, 4), f"time stops at the last frame of the file: {t}"


def t_a_missing_transcode_of_a_reopened_page_shows_the_earlier_recording():
    tl = timeline()
    second = len(tl["recordings"]) - 1
    p = fp.Plan(tl, available=set(range(len(tl["recordings"]))) - {second})
    rec, t = _at(p, 61.0)["sources"]["director"]
    assert p.recordings[rec]["file"] == "pages/director/page@1.webm", "the first recording stays on"
    assert _at(p, 61.0)["sources"]["driver"] is not None
    none = fp.Plan(tl, available=set())
    assert all(src is None for src in _at(none, 61.0)["sources"].values()), "nothing transcoded, nothing shown"


def t_a_recording_without_a_file_is_left_out():
    tl = timeline()
    tl["recordings"][-1]["file"] = None
    p = fp.Plan(tl)
    rec, _ = _at(p, 61.0)["sources"]["director"]
    assert p.recordings[rec]["file"] is not None


def t_every_focus_of_the_full_scenario_lands_on_the_stage():
    import tomllib
    with open(os.path.join(ROOT, "tools", "event-sim", "scenarios", "full.toml"), "rb") as fh:
        steps = tomllib.load(fh)["step"]
    named = {v for s in steps for v in s.get("focus") or ()}
    recs = [{"view": v, "file": "x.webm"} for v in named]
    assert set(fp.stage_views(recs)) == named, f"focus views off the stage: {named - set(fp.stage_views(recs))}"
    assert any(len(s.get("focus") or ()) == 4 for s in steps), "full has the four-view switch"


# stage layout


def _inside(r):
    x, y, w, h = r
    return x >= -EPS and y >= -EPS and x + w <= fp.CANVAS[0] + EPS and y + h <= fp.CANVAS[1] + EPS


def _overlap(a, b):
    return a[0] < b[0] + b[2] - EPS and b[0] < a[0] + a[2] - EPS and a[1] < b[1] + b[3] - EPS and b[1] < a[1] + a[3] - EPS


def t_the_stage_keeps_every_view_inside_the_canvas_and_off_the_caption_line():
    p = plan()
    for f in frames(p):
        for v, r in f["rects"].items():
            if r[4] <= 0:
                continue
            assert _inside(r[:4]), f"{v} leaves the canvas at {f['t']}: {r}"
            assert not _overlap(r, fp.CAPTION_RECT), f"{v} covers the caption line at {f['t']}"
            assert not _overlap(r, fp.TOPBAR_RECT), f"{v} covers the chapter and clock bar at {f['t']}"


def t_resting_views_never_overlap_each_other():
    p = plan()
    for f in frames(p):
        if f["moving"]:
            continue
        shown = [(v, r) for v, r in f["rects"].items() if r[4] > 0]
        for i, (va, ra) in enumerate(shown):
            for vb, rb in shown[i + 1:]:
                assert not _overlap(ra, rb), f"{va} and {vb} overlap at {f['t']}"


def t_a_view_s_label_is_a_tab_above_its_picture_and_covers_nothing():
    p = plan()
    sizes = set()
    for f in frames(p):
        shown = {v: r for v, r in f["rects"].items() if r[4] > 0}
        for v, r in shown.items():
            tab = fp.label_rect(r)
            sizes.add(round(tab[3]))
            assert tab[3] >= 24 - EPS, f"the tab of {v} is too small to read on a rail tile: {tab}"
            assert abs(tab[1] + tab[3] - r[1]) < EPS and not _overlap(tab, r), f"the tab of {v} sits on its picture"
            assert _inside(tab), f"the tab of {v} leaves the canvas at {f['t']}"
            assert not _overlap(tab, fp.CAPTION_RECT) and not _overlap(tab, fp.TOPBAR_RECT), f"the tab of {v} at {f['t']}"
            if f["moving"]:
                continue
            for w, o in shown.items():
                if w != v:
                    assert not _overlap(tab, o[:4]) and not _overlap(tab, fp.label_rect(o)), \
                        f"the tab of {v} covers {w} at {f['t']}"
    assert {fp.TAB_RAIL, fp.TAB_LARGE} <= sizes, f"rail and large tabs: {sizes}"


def t_a_large_view_keeps_its_aspect_and_is_larger_than_a_preview():
    p = plan()
    f = _at(p, 61.0)
    big, small = f["rects"]["director"], f["rects"]["viewer"]
    assert abs(big[2] / big[3] - 1.6) < 0.01 and abs(small[2] / small[3] - 1.6) < 0.01, "16:10 stays 16:10"
    prog = f["rects"]["program"]
    assert abs(prog[2] / prog[3] - 16 / 9) < 0.01
    assert big[2] > 3 * small[2], "a large view is readable, a preview only recognizable"
    q = _at(p, 101.0)
    sizes = {round(q["rects"][v][3]) for v in ("director", "commentator:Comms 1")}
    assert len(sizes) == 1, "the quarters are equal"
    assert all(q["rects"][v][4] == 0 for v in p.views if v not in QUARTERS), "the rail gives way to the quarters"


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  (Playwright unavailable; the stage page renders on the maintainer machine)")
        return None
    return sync_playwright


def _stage(pw, url):
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": fp.CANVAS[0], "height": fp.CANVAS[1]})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    return browser, page, errors


def t_the_stage_page_lays_out_a_frame_without_overlap():
    sync_playwright = _playwright()
    if sync_playwright is None:
        return
    p = fp.Plan(timeline(), available=set())
    states = [p.frame(0), _at(p, 61.0), _at(p, 101.0), _at(p, 66.0)]
    url = "file://" + os.path.join(VIDEO, "stage.html").replace(os.sep, "/")
    with sync_playwright() as pw:
        browser, page, errors = _stage(pw, url)
        try:
            page.evaluate("cfg => setup(cfg)", p.stage_config({}))
            for state in states:
                done = page.evaluate("s => setFrame(s)", state)
                assert done["ok"], done
                boxes = page.evaluate("""() => [...document.querySelectorAll('.view')]
                    .filter(e => +getComputedStyle(e).opacity > 0)
                    .map(e => { const r = e.getBoundingClientRect(); return [e.dataset.view, r.x, r.y, r.width, r.height]; })""")
                cap = page.evaluate("() => { const r = document.querySelector('#caption').getBoundingClientRect();"
                                    " return [r.x, r.y, r.width, r.height]; }")
                text = page.evaluate("() => document.querySelector('#caption').textContent")
                assert boxes, "the stage shows views"
                tabs = page.evaluate("""() => [...document.querySelectorAll('.view')]
                    .filter(e => +getComputedStyle(e).opacity > 0)
                    .map(e => { const r = e.querySelector('.label').getBoundingClientRect();
                                return [e.dataset.view, r.x, r.y, r.width, r.height]; })""")
                for v, *r in boxes:
                    assert _inside(r), f"{v} leaves the canvas: {r}"
                    assert not _overlap(r, cap), f"{v} covers the caption line"
                for v, *t in tabs:
                    assert _inside(t) and not _overlap(t, cap), f"the tab of {v}: {t}"
                    assert not any(_overlap(t, r) for _, *r in boxes), f"the tab of {v} covers a picture: {t}"
                assert (state["caption"] or "") in text
            assert not errors, errors
        finally:
            browser.close()


def _one_view_timeline():
    return {"scenario": "tiny", "steps": [_step("a", "director", 0.0, 1.0, 2.0, caption="The director acts")],
            "recordings": [_rec("director", 0.0, {"width": 320, "height": 200}, file="pages/director/a.webm")]}


def t_the_stage_page_seeks_a_real_video_and_reports_a_broken_one():
    sync_playwright = _playwright()
    if sync_playwright is None:
        return
    if shutil.which("ffmpeg") is None:
        print("  (no ffmpeg; the real-video stage check runs on the maintainer machine)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x200:rate=30:duration=2",
                        "-c:v", "libvpx", "-g", "1", "-b:v", "1M", os.path.join(tmp, "0.webm")], check=True,
                       errors="replace", text=True)
        p = fp.Plan(_one_view_timeline(), available={0})
        state = _at(p, 1.0)
        httpd, url = fr.serve(tmp)
        try:
            with sync_playwright() as pw:
                browser, page, errors = _stage(pw, url + "/stage/stage.html")
                try:
                    page.evaluate("cfg => setup(cfg)", p.stage_config({0: url + "/media/0.webm"}))
                    done = page.evaluate("s => setFrame(s)", state)
                    assert done["ok"], done
                    at = page.evaluate("() => document.querySelector('.view video').currentTime")
                    assert abs(at - state["sources"]["director"][1]) < 0.05, f"the video stands at {at}"
                    page2 = browser.new_page(viewport={"width": fp.CANVAS[0], "height": fp.CANVAS[1]})
                    page2.goto(url + "/stage/stage.html")
                    page2.evaluate("cfg => setup(cfg)", p.stage_config({0: url + "/media/missing.webm"}))
                    bad = page2.evaluate("s => setFrame(s)", state)
                    assert not bad["ok"] and any("director" in x for x in bad["problems"]), bad
                    assert not errors, errors
                finally:
                    browser.close()
        finally:
            fr.stop(httpd)


# render


class _Page:
    def __init__(self, bad_at=None):
        self.bad_at, self.frames = bad_at, 0

    def evaluate(self, script, state):
        n = self.frames
        self.frames += 1
        if n == self.bad_at:
            return {"ok": False, "problems": ["director: seek to 1.0 s timed out"]}
        return {"ok": True, "problems": []}

    def screenshot(self, **kw):
        return b"jpeg"


class _Proc:
    def __init__(self, argv, **kw):
        self.out = argv[-1]
        self.stdin = self

    def write(self, data):
        pass

    def close(self):
        with open(self.out, "wb") as fh:
            fh.write(b"mp4")

    def wait(self):
        return 0


def t_a_frame_whose_seek_failed_fails_the_segment_and_leaves_it_unfinished():
    p = plan()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "seg-0000000-0000010.mp4")
        try:
            fr.render_segment(_Page(bad_at=3), p, 0, 10, path, ["ffmpeg"], popen=_Proc)
        except RuntimeError as exc:
            assert "frame 3" in str(exc) and "director" in str(exc), str(exc)
        else:
            raise AssertionError("a stale frame is never encoded")
        assert not os.path.exists(path), "the segment is not final"
        assert fr.pending([(0, 10, path)]) == [(0, 10, path)], "a .part segment is rendered again"
        fr.render_segment(_Page(), p, 0, 10, path, ["ffmpeg"], popen=_Proc)
        assert os.path.isfile(path) and fr.pending([(0, 10, path)]) == [], "a finished segment is kept"


def t_a_worker_stops_once_its_main_process_is_gone():
    p = plan()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "seg.mp4")
        try:
            fr.render_segment(_Page(), p, 0, 10, path, ["ffmpeg"], popen=_Proc, alive=lambda: False)
        except RuntimeError as exc:
            assert "main process" in str(exc), str(exc)
        else:
            raise AssertionError("an orphaned worker keeps rendering")
        assert not os.path.exists(path)
    assert fr.parent_alive(42, getppid=lambda: 42) and not fr.parent_alive(42, getppid=lambda: 1)


def t_only_unfinished_segments_are_shared_out_on_resume():
    with tempfile.TemporaryDirectory() as tmp:
        segs = [(a, b, os.path.join(tmp, f"{a}.mp4")) for a, b in fr.segments(0, 9000, 1800)]
        for _, _, path in segs[:3]:
            open(path, "wb").close()
        open(segs[3][2][:-4] + ".part.mp4", "wb").close()
        todo = fr.pending(segs)
        assert todo == segs[3:], todo
        assert fr.slices(todo, 2) == [[segs[3]], [segs[4]]], "both workers get unfinished work"


def t_a_resumed_render_shares_out_only_unfinished_segments_and_joins_by_absolute_path():
    p = plan()
    calls = {"jobs": [], "final": None}

    class Worker:
        def __init__(self, argv, **kw):
            with open(argv[-1], encoding="utf-8") as fh:
                calls["jobs"].append(json.load(fh))

        def wait(self):
            return 0

    def final(argv, **kw):
        calls["final"] = argv
        return subprocess.CompletedProcess(argv, 0, "", "")

    saved = (fr.transcode_all, fr.serve, fr.stop, fr.subprocess.Popen, fr.subprocess.run)
    fr.transcode_all = lambda *a, **k: ({n: f"/media/{n}.webm" for n in range(len(p.recordings))}, {})
    fr.serve = lambda media: (None, "http://127.0.0.1:1")
    fr.stop = lambda httpd: None
    fr.subprocess.Popen, fr.subprocess.run = Worker, final
    cwd = os.getcwd()
    tmp = tempfile.mkdtemp()
    try:
        os.chdir(tmp)
        first = fr.render(p, timeline(), "rec", "out", (0, 5400), workers=2, script="x.py", log=lambda *_: None)
        assert first == 0 and len(calls["jobs"]) == 2
        done = [seg for job in calls["jobs"] for seg in job["segments"]]
        assert [tuple(seg[:2]) for seg in done] == [(0, 1800), (1800, 3600), (3600, 5400)]
        for _, _, path in done[:2]:
            assert os.path.isabs(path), path
            open(path, "wb").close()
        calls["jobs"].clear()
        fr.render(p, timeline(), "rec", "out", (0, 5400), workers=2, script="x.py", log=lambda *_: None)
        assert [tuple(seg[:2]) for job in calls["jobs"] for seg in job["segments"]] == [(3600, 5400)], \
            "a resumed render renders only the unfinished segment"
        with open(calls["final"][calls["final"].index("-i") + 1], encoding="utf-8") as fh:
            listed = [line[6:-1] for line in fh.read().splitlines()]
        assert len(listed) == 3 and all(os.path.isabs(x) for x in listed), listed
        home = os.path.normcase(os.path.realpath(tmp))
        assert all(os.path.normcase(os.path.realpath(x)).startswith(home) for x in listed), listed
    finally:
        os.chdir(cwd)    # before the removal: Windows cannot remove the working directory
        shutil.rmtree(tmp, ignore_errors=True)
        fr.transcode_all, fr.serve, fr.stop, fr.subprocess.Popen, fr.subprocess.run = saved


def t_the_concat_list_names_every_segment_by_its_absolute_path():
    text = fr.concat_text(["rel/seg-1.mp4", "/abs/it's.mp4"])
    lines = text.splitlines()
    assert lines[0] == "file '" + os.path.abspath("rel/seg-1.mp4").replace(os.sep, "/") + "'", lines
    assert lines[1] == "file '" + os.path.abspath("/abs/it's.mp4").replace(os.sep, "/").replace("'", "'\\''") + "'"


def t_the_server_serves_only_its_mounts():
    with tempfile.TemporaryDirectory() as tmp:
        media = os.path.join(tmp, "media")
        os.makedirs(media)
        for path in (os.path.join(media, "0.webm"), os.path.join(tmp, "secret")):
            open(path, "w").close()
        mounts = {"/media/": media}
        assert fr.resolve(mounts, "/media/0.webm") == os.path.join(media, "0.webm"), "a file under the mount is served"
        assert fr.resolve(mounts, "/media/1.webm") is None, "a missing file is not served"
        for bad in ("/media/../secret", "/media/%2e%2e/secret", "/other/0.webm", "/media/..%2fsecret"):
            assert fr.resolve(mounts, bad) is None, bad


def t_a_range_answer_is_bounded_so_no_video_holds_a_connection():
    big = 3 * 1024**3
    a, b = fr.byte_range("bytes=0-", big)
    assert (a, b) == (0, fr.RANGE_CHUNK - 1), (a, b)
    assert fr.byte_range("bytes=100-", 1000) == (100, 999), "a small file is answered whole"
    assert fr.byte_range(f"bytes=-{fr.RANGE_CHUNK * 2}", big)[1] - fr.byte_range(f"bytes=-{fr.RANGE_CHUNK * 2}", big)[0] \
        == fr.RANGE_CHUNK - 1


def t_a_transcode_is_named_after_its_source_and_its_parameters():
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "a.webm")
        with open(src, "wb") as fh:
            fh.write(b"x" * 10)
        name = fr.media_name(3, src, program=False)
        assert name.startswith("3-") and name.endswith(".webm") and name == fr.media_name(3, src, program=False)
        assert name != fr.media_name(3, src, program=True), "the program's parameters differ"
        before, old = fr.media_name(3, src, program=True), fr.PROGRAM_WIDTH
        fr.PROGRAM_WIDTH = old + 1
        try:
            assert fr.media_name(3, src, program=True) != before, "a changed program width transcodes again"
        finally:
            fr.PROGRAM_WIDTH = old
        stamp = os.stat(src).st_mtime_ns
        with open(src, "ab") as fh:
            fh.write(b"y")
        os.utime(src, ns=(stamp, stamp))
        assert fr.media_name(3, src, program=False) != name, "a source of another size transcodes again"
        os.utime(src, ns=(stamp + 10**9, stamp + 10**9))
        grown = fr.media_name(3, src, program=False)
        os.utime(src, ns=(stamp, stamp))
        assert grown != fr.media_name(3, src, program=False), "a newer source transcodes again"
        other = os.path.join(tmp, "b.webm")
        shutil.copy2(src, other)
        assert fr.media_name(3, other, program=False) != fr.media_name(3, src, program=False)


def t_the_segment_key_covers_the_encode_parameters():
    tl = timeline()
    key = fr.plan_key(tl, {}, {})
    old = fr.JPEG_QUALITY
    fr.JPEG_QUALITY = old - 1
    try:
        assert fr.plan_key(tl, {}, {}) != key, "another JPEG quality renders new segments"
    finally:
        fr.JPEG_QUALITY = old
    assert fr.plan_key(tl, {}, {}) == key


def t_transcoding_needs_its_disk_space_first():
    p = plan()
    jobs = [(n, r) for n, r in enumerate(p.recordings) if r["view"] in p.views]
    need = fr.transcode_bytes(p, jobs)
    least = sum(p.t1 - r["start"] for _, r in jobs) * fr.TRANSCODE_BITRATE / 8
    assert need >= least, f"{need} bytes for {least} bytes of video"
    fr.check_disk("/x", need, free=lambda _: need + 1)
    try:
        fr.check_disk("/x", need, free=lambda _: need - 1)
    except RuntimeError as exc:
        assert "GB" in str(exc) and "/x" in str(exc), str(exc)
    else:
        raise AssertionError("a disk too small is refused before any transcode")
    assert fr.transcode_workers(8, cpus=8) == 4 and fr.transcode_workers(2, cpus=8) == 2
    assert fr.transcode_workers(4, cpus=1) == 1


# chapters and captions


def t_chapters_txt_starts_at_zero_and_lists_each_chapter():
    p = plan()
    lines = fp.chapters_txt(p.chapters()).splitlines()
    assert lines[0] == "0:00 Start the event", lines
    m = re.fullmatch(r"(\d+):(\d\d) Driver swap 1", lines[1])
    assert m and int(m.group(1)) * 60 + int(m.group(2)) == int(p.out_time(100.0 - fp.LEAD_IN_S)), lines
    assert len(lines) == 2
    late = timeline()
    late["steps"][0]["end"] = 5.0
    late["steps"][1].update(start=5.0, acted=6.0, end=7.0)
    first = fp.chapters_txt(fp.Plan(late).chapters()).splitlines()[0]
    assert first == "0:00 Start the event", f"the first chapter starts the film: {first}"


def t_chapters_past_an_hour_use_hours():
    text = fp.chapters_txt([(0.0, "A"), (59.5, "B"), (3725.0, "C")])
    assert text.splitlines() == ["0:00:00 A", "0:00:59 B", "1:02:05 C"], text


def t_captions_vtt_has_one_cue_per_caption_in_order_without_overlap():
    p = plan()
    text = fp.captions_vtt(p.cues())
    assert text.startswith("WEBVTT\n\n"), text[:20]
    stamps = re.findall(r"^(\d\d:\d\d:\d\d\.\d\d\d) --> (\d\d:\d\d:\d\d\.\d\d\d)$", text, re.M)
    assert len(stamps) == len(_captioned(p)), stamps

    def sec(s):
        h, m, rest = s.split(":")
        return int(h) * 3600 + int(m) * 60 + float(rest)
    spans = [(sec(a), sec(b)) for a, b in stamps]
    assert all(a < b for a, b in spans), spans
    assert all(x[1] <= y[0] + 1e-3 for x, y in zip(spans, spans[1:], strict=False)), "cues do not overlap"
    assert "Race Control checks the tower &lt;now&gt; &amp; then" in text, "cue text is escaped"
    assert "Race Control: Race Control confirms" not in text


def t_a_step_that_follows_at_once_keeps_the_previous_caption_until_it_starts():
    tl = timeline()
    tl["steps"][3]["end"] = 62.5      # approve: its checks end 0.5 s after it acted, desk follows at once
    tl["steps"][4].update(start=62.5, acted=64.0, end=80.0)
    p = fp.Plan(tl)
    assert _at(p, 61.9)["caption"] == "The director approves the link", "the action keeps its caption until it returns"
    assert _at(p, 62.4)["caption"] == "The director approves the link", "and holds it until the next step starts"
    assert _at(p, 62.4)["large"] == ["director", "program"], "the layout stays until the next step starts"
    assert _at(p, 62.6)["caption"].startswith("Race Control confirms")


def t_the_frame_shows_the_caption_of_the_cue_it_stands_in():
    p = plan()
    f = _at(p, 66.0)
    assert f["caption"] == "Race Control confirms the disconnect of #33" and f["who"] == "Race Control"
    assert _at(p, 30.0)["caption"] is None, "waiting has no caption"
    assert _at(p, 30.0)["chapter"] == "Start the event" and _at(p, 101.0)["chapter"] == "Driver swap 1"
    assert _at(p, 30.0)["clock"] == "0:00:30"


# render commands and CLI


def t_a_recording_is_transcoded_once_to_all_keyframe_vp8_without_audio():
    argv = fr.transcode_argv(["ffmpeg"], "in.mkv", "out.webm", program=True)
    s = " ".join(argv)
    for part in ("-c:v libvpx", "-g 1", "-b:v 4M", "-deadline realtime", "-cpu-used 8", "-an"):
        assert part in s, f"{part} in {s}"
    assert "scale" in s and "scale" not in " ".join(fr.transcode_argv(["ffmpeg"], "a.webm", "b.webm")), \
        "only the 1080p program is scaled down"


def t_no_render_command_needs_drawtext():
    cmds = [fr.transcode_argv(["ffmpeg"], "a", "b", program=True), fr.segment_argv(["ffmpeg"], 30, "s.mp4"),
            fr.final_argv(["ffmpeg"], "list.txt", "out.mp4", 100.0, music="m.mp3"),
            fr.final_argv(["ffmpeg"], "list.txt", "out.mp4", 100.0)]
    assert not any("drawtext" in " ".join(c) for c in cmds), "stock Homebrew ffmpeg has no drawtext"


def t_the_music_bed_loops_quietly_and_fades_out_at_the_film_s_end():
    s = " ".join(fr.final_argv(["ffmpeg"], "list.txt", "out.mp4", 100.0, music="m.mp3"))
    assert "-stream_loop -1" in s and "volume=" in s and "afade=t=out:st=97" in s, s
    assert "-t 100.000" in s
    silent = " ".join(fr.final_argv(["ffmpeg"], "list.txt", "out.mp4", 100.0))
    assert "m.mp3" not in silent and "-c:v copy" in silent


def t_a_range_request_is_answered_with_the_asked_bytes():
    assert fr.byte_range("bytes=0-99", 1000) == (0, 99)
    assert fr.byte_range("bytes=900-", 1000) == (900, 999)
    assert fr.byte_range("bytes=-100", 1000) == (900, 999)
    assert fr.byte_range("bytes=990-2000", 1000) == (990, 999)
    assert fr.byte_range("bytes=1000-", 1000) is False, "a start past the end is unsatisfiable"
    assert fr.byte_range(None, 1000) is None and fr.byte_range("items=0-1", 1000) is None


def t_frames_are_split_into_segments_and_contiguous_worker_slices():
    segs = fr.segments(0, 4000, 1800)
    assert segs == [(0, 1800), (1800, 3600), (3600, 4000)], segs
    slices = fr.slices(segs, 2)
    assert slices == [[(0, 1800), (1800, 3600)], [(3600, 4000)]], slices
    assert fr.slices(segs, 8) == [[s] for s in segs], "never more workers than segments"


def t_the_cli_writes_into_runtime_event_video_run_and_reads_times():
    args = cli.parse_args(["/x/recordings/run-7"])
    assert cli.out_dir(args) == os.path.join(ROOT, "runtime", "event-video", "run-7")
    assert cli.parse_args(["/x/r", "--out", "/y"]).out == "/y"
    assert cli.out_dir(cli.parse_args(["/x/r", "--out", "rel"])) == os.path.abspath("rel"), "a relative --out is made absolute"
    a = cli.parse_args(["/x/r", "--from", "1:30", "--to", "95.5", "--workers", "2"])
    assert (a.start, a.stop, a.workers) == (90.0, 95.5, 2)
    assert cli.parse_args(["/x/r"]).workers == 4


def t_the_cli_writes_chapters_and_captions_before_any_capture():
    with tempfile.TemporaryDirectory() as tmp:
        rec = os.path.join(tmp, "rec")
        os.makedirs(rec)
        with open(os.path.join(rec, "timeline.json"), "w", encoding="utf-8") as fh:
            json.dump(timeline(), fh)
        out = os.path.join(tmp, "out")
        calls = []

        def render(*a, **k):
            calls.append(os.path.isfile(os.path.join(out, "chapters.txt"))
                         and os.path.isfile(os.path.join(out, "captions.vtt")))
            return 0
        code = cli.main([rec, "--out", out], render=render)
        assert code == 0 and calls == [True], f"the render runs once, after the texts: {calls}"
        with open(os.path.join(out, "chapters.txt"), encoding="utf-8") as fh:
            assert fh.read().startswith("0:00 Start the event")
        with open(os.path.join(out, "captions.vtt"), encoding="utf-8") as fh:
            assert fh.read().startswith("WEBVTT")
        assert not os.path.exists(os.path.join(rec, "work")), "nothing is written into the recording"


def t_a_timeline_that_lasts_no_time_is_refused():
    tl = timeline()
    tl["steps"] = [_step("only", "producer", 5.0, 5.0, 5.0, caption="x")]
    try:
        fp.Plan(tl)
    except Exception as exc:
        assert isinstance(exc, ValueError) and "no time" in str(exc), f"refused with a reason: {exc!r}"
    else:
        raise AssertionError("a timeline of no duration is refused")


def t_the_film_is_never_written_into_the_recording():
    with tempfile.TemporaryDirectory() as tmp:
        rec = os.path.join(tmp, "event-video", "run")
        os.makedirs(rec)
        with open(os.path.join(rec, "timeline.json"), "w", encoding="utf-8") as fh:
            json.dump(timeline(), fh)
        for out in (rec, os.path.join(rec, "film")):
            code = cli.main([rec, "--out", out], render=lambda *a, **k: 0)
            assert code == 2, f"an output dir in the recording is refused: {out}"
        assert sorted(os.listdir(rec)) == ["timeline.json"], os.listdir(rec)


def t_a_run_with_the_obs_stand_in_has_no_program_and_still_plans_a_film():
    tl = timeline()
    tl["recordings"] = [r for r in tl["recordings"] if r["kind"] != "program"]
    p = fp.Plan(tl)
    assert "program" not in p.views, "without --obs real no program view is on the stage"
    f = _at(p, 61.0)
    assert f["large"] == ["director"], f"a focus on the missing program keeps only the director: {f['large']}"
    assert set(f["rects"]) == set(p.views) and "program" not in f["sources"], "no frame places the program"
    a, b = p.frame_range(0, 5)
    assert (a, b) == (0, 5 * p.fps), f"a five-second partial film from the stand-in run: {(a, b)}"


def t_a_timeline_without_steps_is_refused():
    try:
        fp.Plan({"scenario": "x", "steps": [], "recordings": []})
    except Exception as exc:
        assert isinstance(exc, ValueError) and "step" in str(exc), f"refused with a reason: {exc!r}"
    else:
        raise AssertionError("an empty timeline is refused")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")

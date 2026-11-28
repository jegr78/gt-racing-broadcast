#!/usr/bin/env python3
"""The event walkthrough film's renderer (#1312): pace and focus rules, the frame plan,
the stage layout, chapters.txt and captions.vtt, and the render commands. No video, no
ffmpeg run; the stage page renders only where Playwright is installed.
Run: python3 tests/test_event_video.py"""
import contextlib
import importlib.util
import io
import json
import math
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
ROUNDED = 6e-5     # a source time is rounded to 0.1 ms


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


C1, C2, C3 = "commentator:Comms 1", "commentator:Comms 2", "commentator:Comms 3"


def swaps_timeline():
    """Three stints and two swaps: each commentator submits its link in its own chapter,
    the swap step's checks wait for the on-air change, the next step follows at once."""
    def swap(n, start, out, inc):
        return [
            _step(f"driver{n}", "driver", start, start + 1.0, start + 1.0, chapter=f"Driver swap {n}",
                  caption="The next driver registers", focus=["driver", "race_control"]),
            _step(f"ok{n}", "race_control", start + 1.0, start + 2.0, start + 2.0, caption="Race Control approves",
                  focus=["race_control", "driver"]),
            _step(f"next{n}", "director", start + 2.0, start + 3.0, start + 7.0, caption="The director switches",
                  focus=["director", "program", out, inc]),
            _step(f"cut{n}", "director", start + 7.0, start + 8.0, start + 8.0, caption="The director cuts back",
                  focus=["program", "director"]),
        ]
    return {"scenario": "swaps", "steps": [
        _step("pick", "producer", 0.0, 1.0, 1.0, chapter="Start the event", caption="The producer starts"),
        _step("submit1", C1, 1.0, 2.0, 2.0, chapter="First link", caption="Comms 1 submits", focus=[C1, "director"]),
        _step("open3", C3, 2.0, 2.5, 2.5),
        _step("wait1", "api", 2.5, 2.6, 100.0),
        _step("submit2", C2, 100.0, 100.0, 101.0, chapter="Next commentator", caption="Comms 2 submits",
              focus=[C2, "director"]),
        _step("wait2", "api", 101.0, 101.1, 200.0),
        *swap(1, 200.0, C1, C2),
        _step("wait3", "api", 208.0, 208.1, 300.0),
        *swap(2, 300.0, C2, C3),
        _step("end", "api", 308.0, 308.1, 400.0),
    ], "recordings": [
        _rec("program", 0.0, {"width": 1920, "height": 1080}, "program", "program/p.mkv"),
        _rec("producer", 0.0), _rec("director", 0.0), _rec("race_control", 0.0), _rec(C1, 0.0), _rec(C2, 0.0),
        _rec(C3, 0.0), _rec("viewer", 0.0), _rec("driver", 0.0, {"width": 390, "height": 844}),
    ]}


def frames(p):
    return [p.frame(i) for i in range(p.frames)]


def _captioned(p):
    return [s for s in p.steps if s.get("caption")]


# pace


def t_a_captioned_step_plays_its_action_in_real_time_frame_by_frame():
    p = plan()
    fs = frames(p)
    for s in _captioned(p):
        assert s["acted"] - s["start"] <= fp.MAX_ACTION_S, "the fixture's actions are short"
        inside = [f for f in fs if s["start"] < f["t"] <= s["acted"]]
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


def t_a_long_wait_takes_at_most_max_wait_of_film_at_a_whole_speed():
    tl = timeline()
    tl["steps"][2]["end"] = 60.0 + 400.0
    for st in tl["steps"][3:]:
        for k in ("start", "acted", "end"):
            st[k] += 400.0
    p = fp.Plan(tl)
    seg = next(g for g in p.segments if g.a <= 200.0 < g.b)
    gap = sum(g.b - g.a for g in p.segments if g.speed == seg.speed and g.a >= seg.a and g.b <= 460.0)
    speed = p.speed_at(200.0)
    assert speed == int(speed) and speed > p.wait_speed and gap / speed <= fp.MAX_WAIT_S < gap / (speed - 1), \
        f"a wait too long for {fp.MAX_WAIT_S} s at {p.wait_speed}x plays at the least whole speed that fits: {speed}"
    assert _at(p, 200.0)["ff"] == f"{speed:g}x", "the mark names that speed"
    assert plan().speed_at(30.0) == plan().wait_speed, "a wait that fits keeps the normal speed"


def t_a_short_wait_plays_in_real_time():
    p = plan()
    assert p.speed_at(108.0) == p.wait_speed, "the gap before the cockpit step is long enough to speed up"
    q = fp.Plan(timeline(), min_wait=20.0)
    assert q.speed_at(108.0) == 1, "a gap shorter than min_wait plays in real time"


def t_no_source_time_is_skipped_and_output_time_is_monotonic():
    p = plan()
    fs = frames(p)
    ts = [f["t"] for f in fs]
    assert abs(ts[0] - p.t0) < EPS and p.t0 == 0.5, "the film starts once the first caption's view has opened"
    assert all(b > a or (b == a and 0 in (f["speed"], g["speed"]))
               for a, b, f, g in zip(ts, ts[1:], fs, fs[1:], strict=False)), \
        "source time only moves forward, and stands only in a still"
    assert any(f["speed"] == 0 for f in fs), "the fixture has stills"
    step = p.wait_speed / p.fps + 1e-6
    assert all(b - a <= step for a, b in zip(ts, ts[1:], strict=False)), "no frame skips more than one frame of waiting"
    assert ts[-1] >= p.t1 - step, "the film reaches the last step's end"
    segs = p.segments
    assert segs[0].a == p.t0 and segs[-1].b == p.t1, "the segments cover the whole run"
    assert all(abs(x.b - y.a) < EPS for x, y in zip(segs, segs[1:], strict=False)), "the segments are contiguous"
    assert all(abs(x.out + x.dur - y.out) < EPS for x, y in zip(segs, segs[1:], strict=False)), "and so is film time"
    stills = [x.a for x in segs if x.speed == 0]
    for i, f in enumerate(fs):
        assert abs(p.src_time(i / p.fps) - f["t"]) < EPS, "frame i stands at i/fps in the film"
        if f["speed"] and not any(abs(f["t"] - a) < EPS for a in stills):
            assert abs(p.out_time(f["t"]) - i / p.fps) < 1e-6, f"frame {i} outside a still maps back to i/fps"


def _cue(p, sid):
    step = next(s for s in p.steps if s["id"] == sid)
    return next(c for c in p.cues() if c[3] == step["caption"])


def _first_frame_after(p, s):
    return next(i for i in range(p.frames) if p.frame(i)["t"] > s + EPS)


def t_steps_that_follow_at_once_still_get_a_lead_in_and_a_minimum_time_on_screen():
    p = fp.Plan(swaps_timeline())
    assert fp.LEAD_IN_S >= 2.0 and fp.MIN_CAPTION_S >= fp.LEAD_IN_S + fp.HOLD_S, "the opening gives time to follow"
    for s in _captioned(p):
        a, b, _who, _text = _cue(p, s["id"])
        assert b - a >= fp.MIN_CAPTION_S - EPS, f"{s['id']} is on screen {b - a:.2f} s"
        acts = _first_frame_after(p, s["start"])
        assert acts / p.fps - a >= fp.LEAD_IN_S - 1.5 / p.fps, f"{s['id']} acts {acts / p.fps - a:.2f} s after its caption"
        assert not p.frame(acts - 1)["moving"], f"{s['id']} acts only once its layout rests"


def t_a_long_action_shows_its_start_and_speeds_up_the_rest():
    tl = timeline()
    tl["steps"][4].update(start=65.0, acted=125.0, end=125.0)      # Race Control approves one request after another
    for st in tl["steps"][5:]:
        st.update({k: st[k] + 60.0 for k in ("start", "acted", "end")})
    p = fp.Plan(tl)
    assert p.speed_at(65.0 + fp.MAX_ACTION_S - 0.1) == 1, "the first approvals play in real time"
    assert p.speed_at(100.0) == p.wait_speed, "the rest of the action is sped up"
    assert p.speed_at(125.0 + fp.HOLD_S - 0.1) == 1, "the result holds in real time"
    assert _at(p, 100.0)["caption"].startswith("Race Control confirms"), "the caption stays on while it speeds"


def t_in_a_swap_chapter_only_the_switch_plays_a_long_action_in_real_time():
    tl = swaps_timeline()
    for st in tl["steps"]:
        if st["id"] == "ok1":
            st.update(acted=236.0, end=236.0)                      # Race Control works through a long list
        elif st["id"] == "next1":
            st.update(start=236.0, acted=250.0, end=254.0)         # the switch waits for the on-air change
        elif st["id"] == "cut1":
            st.update({k: st[k] + 47.0 for k in ("start", "acted", "end")})
    p = fp.Plan(tl)
    assert p.speed_at(201.0 + fp.MAX_ACTION_S - 0.5) == 1, "a swap step's action starts in real time"
    assert p.speed_at(225.0) == p.wait_speed, "its long rest is sped up, as elsewhere"
    assert all(p.speed_at(x) == 1 for x in (238.0, 245.0, 249.9)), "the four-view switch plays in real time"


def _run_timeline(actions, chapter=None, swap=False):
    steps = [_step("boot", "api", 0.0, 0.1, 1.0)]
    for n, action in enumerate(actions):
        extra = {"chapter": chapter} if chapter and n == 0 else {}
        steps.append(_step(f"lap{n}", "race_control", 1.0 + 6 * n, 6.0 + 6 * n, 7.0 + 6 * n, caption=f"Lap {n}",
                           focus=["race_control", "viewer"], action=action, **extra))
    if swap:
        steps.append(_step("switch", "director", 37.0, 38.0, 39.0, caption="The director switches", focus=QUARTERS))
    else:
        steps.append(_step("other", "director", 37.0, 38.0, 39.0, caption="The director acts"))
    recs = [_rec(v, 0.0) for v in ("race_control", "viewer", "director", "program", "commentator:Comms 1",
                                   "commentator:Comms 2")]
    return {"scenario": "run", "steps": steps, "recordings": recs}


def t_a_run_of_like_steps_shows_its_first_ones_and_speeds_up_the_rest_under_one_caption():
    shown = fp.RUN_SHOWN
    assert 2 <= shown < 6
    p = fp.Plan(_run_timeline(["add_lap"] * 6))
    for n in range(6):
        mid = 3.5 + 6 * n
        want = 1 if n < shown else p.wait_speed
        assert p.speed_at(mid) == want, f"lap {n} plays at {p.speed_at(mid)}"
    cues = p.cues()
    assert [c[3] for c in cues] == [f"Lap {n}" for n in range(shown + 1)] + ["The director acts"], \
        f"the sped-up tail of the run shows one caption: {[c[3] for c in cues]}"
    assert all(b - a >= fp.MIN_CAPTION_S - EPS for a, b, _w, _t in cues), \
        f"no caption flashes: {[round(b - a, 2) for a, b, _w, _t in cues]}"
    assert p.speed_at(37.5) == 1, "a different step after the run plays in real time"
    mixed = fp.Plan(_run_timeline([f"act{n}" for n in range(6)]))
    assert all(mixed.speed_at(3.5 + 6 * n) == 1 for n in range(6)), "steps of one role with other actions are no run"
    swap = fp.Plan(_run_timeline(["add_lap"] * 6, chapter="Driver swap 1", swap=True))
    assert all(swap.speed_at(3.5 + 6 * n) == 1 for n in range(6)), "a run in a swap chapter plays in full"


def t_a_swap_holds_its_beats_and_the_quarters_until_both_cockpits_changed():
    p = fp.Plan(swaps_timeline())
    for sid in ("driver1", "ok1", "next1", "cut1"):
        step = next(s for s in p.steps if s["id"] == sid)
        _a, b, _who, _text = _cue(p, sid)
        assert b - p.out_time(step["acted"]) >= fp.SWAP_HOLD_S - EPS, f"{sid} holds its result {fp.SWAP_HOLD_S} s"
    assert p.speed_at(206.9) == 1, "the swap step's wait for the on-air change plays in real time"
    still = next(x for x in p.segments if x.speed == 0 and x.a == 201.0)
    f = p.frame(math.ceil(still.out * p.fps))
    assert f["caption"] == "The next driver registers" and "race_control" not in f["rings"], \
        "the next step's ring lights only once its action runs"
    lead = next(x for x in p.segments if x.speed == 0 and x.a == 201.0 and x.out > still.out)
    g = p.frame(math.ceil(lead.out * p.fps) + 1)
    assert g["caption"] == "Race Control approves" and not {"driver", "race_control"} & set(g["rings"]), \
        "during a lead-in no ring lights: the previous step is done, this one has not acted"
    assert "race_control" in _at(p, 201.5)["rings"], "the step's ring lights while it acts"
    assert "director" in _at(p, 206.5)["rings"], "a swap step's ring lasts its swap hold"
    acted = p.out_time(203.0)
    assert p.frame(round(acted * p.fps))["quarters"]
    gone = next(i for i in range(round(acted * p.fps), p.frames) if not p.frame(i)["quarters"])
    assert gone / p.fps - acted >= (207.0 - 203.0) + fp.HOLD_S - 0.1, \
        "the quarters stay until the cockpits changed, and a hold longer"


def _present(f, v):
    return f["rects"][v][4] > 0


def t_a_cockpit_is_on_the_stage_only_around_its_own_stint():
    p = fp.Plan(swaps_timeline())
    assert _present(_at(p, 1.5), C1) and not _present(_at(p, 0.5), C1), "Comms 1 comes on with its first chapter"
    assert not _present(_at(p, 50.0), C2) and not _present(_at(p, 50.0), C3)
    assert _present(_at(p, 150.0), C2), "Comms 2 comes on with its link, before the swap"
    assert _present(_at(p, 204.0), C1) and not _present(_at(p, 250.0), C1), "Comms 1 leaves after the swap"
    assert not _present(_at(p, 250.0), C3) and _present(_at(p, 300.5), C3), "Comms 3 comes on with swap 2"
    assert not _present(_at(p, 350.0), C2), "Comms 2 leaves after swap 2"
    assert _present(_at(p, 399.0), C3), "the last commentator stays to the end"
    fade = [f["rects"][C2][:4] for f in frames(p) if 0 < f["rects"][C2][4] < 1 and f["t"] < 150]
    assert fade and all(r == fade[0] for r in fade), "a cockpit fades in where it will stand"


def t_the_cockpit_on_air_hands_over_and_a_returning_one_stays():
    tl = swaps_timeline()
    for st in tl["steps"]:
        if st["id"] == "next2":
            st["focus"] = ["director", "program", C1, C2]           # Comms 2 hands back to Comms 1
    p = fp.Plan(tl)
    assert not _present(_at(p, 250.0), C1) and _present(_at(p, 250.0), C2), "Comms 1 left after swap 1"
    assert _present(_at(p, 300.5), C1), "Comms 1 comes back with swap 2"
    assert _present(_at(p, 304.0), C1) and _present(_at(p, 304.0), C2), "both stand in the quarters"
    assert not _present(_at(p, 350.0), C2), "Comms 2, on air before swap 2, hands over and leaves"
    assert _present(_at(p, 399.0), C1), "the returning Comms 1 stays to the end"


def t_a_cockpit_never_named_by_a_caption_stays_on_the_stage():
    p = plan()
    two = timeline()
    two["steps"][6]["focus"] = QUARTERS[:2]
    q = fp.Plan(two)
    assert all(_present(f, "commentator:Comms 2") for f in frames(q) if f["t"] >= 1.0), \
        "no caption places it, so it stays from its recording on"
    assert _present(_at(p, 101.0), "commentator:Comms 2")
    assert not _present(_at(p, 115.5), "commentator:Comms 1") and _present(_at(p, 115.5), "commentator:Comms 2"), \
        "of two cockpits that came on together, the one named first hands over"


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


def t_a_step_without_focus_stands_its_own_role_beside_the_previous_view():
    p = plan()
    f = _at(p, 2.0)
    assert f["large"] == ["producer"] and f["front"] == "producer", "with nothing large before, it stands alone"
    f = _at(p, 66.0)
    assert f["large"] == ["race_control", "program"] and f["front"] is None, \
        f"the own role stands beside the program that was large before, covering nothing: {f['large']}"
    tl = timeline()
    tl["steps"][3]["focus"] = ["director", "viewer"]
    assert _at(fp.Plan(tl), 66.0)["large"] == ["race_control", "director"], \
        "without the program, beside the first view that was large"
    tl = timeline()
    tl["steps"].insert(5, _step("desk2", "race_control", 80.0, 81.0, 82.0, caption="Race Control acts again"))
    tl["steps"][6].update(start=82.0, acted=82.1)
    q = fp.Plan(tl)
    span = [i for i in range(q.frames) if 66.0 <= q.frame(i)["t"] <= 81.0]
    assert all(q.frame(i)["large"] == ["race_control", "program"] and not q.frame(i)["moving"] for i in span[q.fps:]), \
        "consecutive steps of the same role keep the same layout"


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
    limit = fade = 0
    for p in (fp.Plan(swaps_timeline()), plan()):
        fs = frames(p)
        for a, b in zip(fs, fs[1:], strict=False):
            for v in p.views:
                ra, rb = a["rects"][v], b["rects"][v]
                if ra[4] > 0 and rb[4] > 0:
                    limit = max(limit, max(abs(x - y) for x, y in zip(ra[:4], rb[:4], strict=True)))
                fade = max(fade, abs(ra[4] - rb[4]))
    assert limit < 150, f"a visible view moved {limit:.0f} px in one frame: a cut"
    assert fade < 0.1, f"a view's opacity changed by {fade:.2f} in one frame: a cut"
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
    assert abs(t - (f["t"] - first["start"])) < ROUNDED, "a view's time is the run time minus its start"
    rec2, t2 = _at(p, 61.0)["sources"]["director"]
    assert p.recordings[rec2]["file"] == "pages/director/page@second.webm", "the reopened page takes over"
    assert abs(t2 - (_at(p, 61.0)["t"] - 50.0)) < ROUNDED
    prog = _at(p, 2.0)["sources"]["program"]
    assert abs(prog[1] - (_at(p, 2.0)["t"] + 2.0)) < ROUNDED, "a recording started before the engine is ahead"


def t_a_recording_s_time_stops_at_its_known_duration():
    p = fp.Plan(timeline(), durations={"pages/driver/page@1.webm": 10.0})
    _, t = _at(p, 60.0)["sources"]["driver"]
    assert t == round(10.0 - 1 / p.fps, 4), f"time stops at the last frame of the file: {t}"


def t_a_closed_page_leaves_the_stage_until_it_opens_again():
    tl = swaps_timeline()
    for r in tl["recordings"]:
        if r["view"] == "producer":
            r["end"] = 50.0
    tl["recordings"].append(_rec("producer", 350.0, file="pages/producer/page@2.webm"))
    tl["steps"][-1].update(caption="The producer stops", chapter="Stop", focus=["producer"])
    p = fp.Plan(tl)
    assert _present(_at(p, 1.5), "producer"), "the producer is on the stage while its page is open"
    assert not _present(_at(p, 150.0), "producer") and _at(p, 150.0)["sources"]["producer"] is None, \
        "a closed page shows nothing and gives its place up"
    assert _present(_at(p, 360.0), "producer"), "it comes back with its next recording"
    rec, _t = _at(p, 360.0)["sources"]["producer"]
    assert p.recordings[rec]["file"] == "pages/producer/page@2.webm"
    missing = fp.Plan(tl, available=set(range(len(tl["recordings"]))) - {len(tl["recordings"]) - 1})
    assert not _present(_at(missing, 360.0), "producer"), "a reopened page without its transcode takes no place"


def t_a_screenshot_s_window_shows_the_frame_before_it():
    tl = timeline()
    tl["shots"] = [{"view": "race_control", "start": 70.0, "end": 70.5}]
    p = fp.Plan(tl)
    held = round(70.0 - fp.SHOT_LEAD_S - 0.6, 4)
    for s in (70.0, 70.5, 70.5 + fp.SHOT_SETTLE_S - 0.1):
        assert p._source("race_control", s) == [3, held], \
            f"at {s} the desk shows its frame from before the shot: {p._source('race_control', s)}"
    assert p._source("race_control", 70.5 + fp.SHOT_SETTLE_S + 0.1)[1] > held, "after the settle it plays on"
    assert p._source("director", 70.2)[1] == 20.2, "another view plays through the shot"


def t_a_missing_transcode_of_a_reopened_page_shows_the_earlier_recording():
    tl = timeline()
    second = len(tl["recordings"]) - 1
    p = fp.Plan(tl, available=set(range(len(tl["recordings"]))) - {second})
    rec, t = _at(p, 61.0)["sources"]["director"]
    assert p.recordings[rec]["file"] == "pages/director/page@1.webm", "the first recording stays on"
    assert _at(p, 61.0)["sources"]["driver"] is not None
    none = fp.Plan(tl, available=set())
    assert all(src is None for src in _at(none, 61.0)["sources"].values()), "nothing transcoded, nothing shown"


TAB = "race_control:standings"


def tab_timeline():
    """Race Control opens the standings tab for 8 s during one step; it is recorded on its own."""
    tl = timeline()
    tl["steps"][4]["focus"] = [TAB]
    tl["recordings"].append(_rec(TAB, 65.5, file="pages/race_control/tab.webm"))
    return tl


def t_a_tab_is_a_view_of_its_own_with_its_label():
    p = fp.Plan(tab_timeline())
    assert TAB in p.views and p.views.index(TAB) == p.views.index("race_control") + 1, p.views
    assert fp.label(TAB) == "Race Control: standings"
    assert fp.stage_views([{"view": "race_control:other", "file": "x.webm"}]) == ["race_control:other"]


def t_a_tab_is_on_the_stage_only_around_its_step():
    p = fp.Plan(tab_timeline())
    assert not _present(_at(p, 61.0), TAB) and not _present(_at(p, 101.0), TAB), "the tab is off the stage"
    before = _at(p, 65.3)
    assert not _present(before, TAB) and before["large"][0] == "race_control", \
        "until the tab opens, the desk that opens it stays large"
    f = _at(p, 66.2)
    assert f["large"] == [TAB, "program"] and f["front"] is None, \
        f"once open, the tab stands large beside the program, covering nothing: {f['large']}"


def t_a_recording_that_starts_mid_run_counts_its_time_from_its_start():
    p = fp.Plan(tab_timeline())
    f = _at(p, 66.0)
    rec, t = f["sources"][TAB]
    assert p.recordings[rec]["file"] == "pages/race_control/tab.webm"
    assert abs(t - (f["t"] - 65.5)) < ROUNDED, f"the tab's video stands at source minus its start: {t}"
    assert _at(p, 65.0)["sources"][TAB] is None and not _present(_at(p, 65.0), TAB), "before it opens, no tab"


def t_the_film_opens_on_the_first_caption_s_own_view():
    p = plan()
    f = p.frame(0)
    assert f["large"] == ["producer"] and f["sources"]["producer"] is not None, \
        f"the film opens on the Control Center, not a stand-in: {f['large']}"
    assert f["caption"] == "The producer picks the track", f["caption"]
    q = fp.Plan(swaps_timeline())
    assert q.t0 == 0.0, "a view open from the start moves nothing"


def t_a_view_takes_no_place_before_its_recording_starts():
    p = plan()
    f = _at(p, 2.0)
    assert not _present(f, "driver"), "the driver page opens at 5 s"
    assert _present(_at(p, 30.0), "driver"), "and is on the stage from then on"
    fade = [g["rects"]["driver"][:4] for g in frames(p) if 0 < g["rects"]["driver"][4] < 1 and g["t"] < 30]
    assert fade and all(r == fade[0] for r in fade), "it fades in where it will stand"
    for q in (plan(), fp.Plan(swaps_timeline()), fp.Plan(tab_timeline())):
        for g in frames(q):
            bare = [v for v, r in g["rects"].items() if r[4] > 0 and g["sources"][v] is None]
            assert not bare, f"{bare} on the stage without a picture at {g['t']}"


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


# title cards

INTRO = {"title": "GT Racing Broadcast", "subtitle": "An endurance event"}
OUTRO = {"title": "GT Racing Broadcast", "subtitle": "The project wiki"}
SWAP_CARD = {"title": "Driver swap", "subtitle": "The next stint"}


def cards_timeline():
    """The fixed run with an intro, an outro and a card on each of its two chapters."""
    tl = timeline()
    tl.update(intro=INTRO, outro=OUTRO)
    steps = {s["id"]: s for s in tl["steps"]}
    steps["pick"]["card"] = {"title": "Start", "subtitle": "The day before"}
    steps["switch"]["card"] = SWAP_CARD
    return tl


def _card_spans(p):
    """[(first frame, frame count, title)] of each card in the film."""
    spans = []
    for i in range(p.frames):
        card = p.frame(i)["card"]
        if card and (not spans or spans[-1][2] != card["title"] or spans[-1][0] + spans[-1][1] != i):
            spans.append([i, 0, card["title"]])
        if card:
            spans[-1][1] += 1
    return [tuple(x) for x in spans]


def t_the_cards_add_film_time_and_change_no_other_frame():
    p, q = fp.Plan(cards_timeline()), plan()
    spans = _card_spans(p)
    want = [(0, fp.INTRO_S, "GT Racing Broadcast"), (None, fp.CHAPTER_CARD_S, "Start"),
            (None, fp.CHAPTER_CARD_S, "Driver swap"), (None, fp.OUTRO_S, "GT Racing Broadcast")]
    assert [(n, t) for _, n, t in spans] == [(round(d * p.fps), t) for _, d, t in want], \
        f"intro, the two chapter cards and the outro, each for its own duration: {spans}"
    assert spans[0][0] == 0 and spans[1][0] == spans[0][1], "the film opens on the intro, then the first chapter's card"
    assert spans[-1][0] + spans[-1][1] == p.frames, "the film closes on the outro"
    added = sum(n for _, n, _ in spans)
    assert p.frames == q.frames + added, f"the cards add {added} frames and nothing else: {p.frames} vs {q.frames}"
    body = [f for f in frames(p) if not f["card"]]
    keys = ("t", "speed", "sources", "caption", "who", "chapter", "chapter_no", "large", "rings")
    for n, (f, g) in enumerate(zip(body, frames(q), strict=True)):
        assert {k: f[k] for k in keys} == {k: g[k] for k in keys}, f"frame {n} of the film without cards differs"
    assert [(x.a, x.b, x.speed) for x in p.segments if x.speed] == [(x.a, x.b, x.speed) for x in q.segments if x.speed], \
        "the source time plays exactly as before"


def t_the_film_opens_on_the_intro_even_when_the_first_step_starts_before_its_view():
    tl = cards_timeline()
    for r in tl["recordings"]:
        if r["view"] == "producer":
            r["start"] = 2.0       # after the first step started at 1.0
    p = fp.Plan(tl)
    assert p.t0 == 2.0, p.t0
    intro, start = round(fp.INTRO_S * p.fps), round(fp.CHAPTER_CARD_S * p.fps)
    titles = [(p.frame(i)["card"] or {}).get("title") for i in range(intro + start + 1)]
    assert titles[:intro] == ["GT Racing Broadcast"] * intro, f"frame 0 to the intro's end is the intro: {titles[:5]}"
    assert titles[intro:intro + start] == ["Start"] * start, "then the first chapter's card"
    first = p.frame(intro + start)
    assert first["card"] is None and first["caption"] == "The producer picks the track", \
        f"the first view comes after the cards, under its caption: {first['caption']}"


def t_a_card_stands_still_in_source_time_and_hides_the_stage():
    p = fp.Plan(cards_timeline())
    fs = frames(p)
    card_segs = [x for x in p.segments if x.card]
    for (a0, n0, title), seg in zip(_card_spans(p), card_segs, strict=True):
        assert seg.card["title"] == title and seg.dur * p.fps == n0, f"{title} is its own still segment"
        for f in fs[a0:a0 + n0]:
            assert f["t"] == seg.a and f["speed"] == 0 and not f["ff"], f"{title} stands at {seg.a}, not {f['t']}"
    ts = [f["t"] for f in fs]
    assert all(b >= a for a, b in zip(ts, ts[1:], strict=False)), "source time never runs back across a card"
    a, n, _ = _card_spans(p)[2]
    assert fs[a - 1]["t"] <= fs[a]["t"] == fs[a + n - 1]["t"] <= fs[a + n]["t"], "the card sits between two frames of the run"
    late = a + n + round((fp.CHAPTER_FRESH_S - 0.2) * p.fps)
    assert fs[a + n]["chapter"] == "Driver swap 1" and fs[a + n]["chapter_fresh"] and fs[late]["chapter_fresh"], \
        "the chapter bar lights up once the card has gone, for its full time"


def t_a_card_fades_in_and_out():
    p = fp.Plan(cards_timeline())
    fade = round(fp.CARD_FADE_S * p.fps)
    for a, n, title in _card_spans(p):
        ops = [p.frame(i)["card"]["opacity"] for i in range(a, a + n)]
        assert ops[0] < 0.1 and ops[-1] < 0.1, f"{title} fades in and out: {ops[0]}, {ops[-1]}"
        assert all(o == 1.0 for o in ops[fade:-fade]), f"{title} stands at full opacity between its fades"
        assert ops[:fade] == sorted(ops[:fade]) and ops[-fade:] == sorted(ops[-fade:], reverse=True)


def t_the_cards_shift_later_chapter_marks_and_captions_by_their_duration():
    p, q = fp.Plan(cards_timeline()), plan()
    spans = [(a / p.fps, (a + n) / p.fps, t) for a, n, t in _card_spans(p)]
    assert p.chapters()[0] == (0.0, "Start the event"), p.chapters()
    swap = spans[2][0]
    assert abs(p.chapters()[1][0] - swap) < 1 / p.fps, f"a chapter with a card starts at its card: {p.chapters()} {spans}"
    before = fp.INTRO_S + fp.CHAPTER_CARD_S
    assert abs(p.chapters()[1][0] - (q.chapters()[1][0] + before)) < 1 / p.fps, "shifted by the cards before it"
    for (a, b, _who, text), (c, d, _w, _t) in zip(p.cues(), q.cues(), strict=True):
        shift = sum(e - s for s, e, _ in spans if e <= a + EPS)
        assert abs(a - c - shift) < 1 / p.fps and abs(b - d - shift) < 1 / p.fps, f"{text} moves by the cards before it"
        assert not any(a < e - EPS and s < b - EPS for s, e, _ in spans), f"{text} overlaps a card"
    lines = fp.chapters_txt(p.chapters()).splitlines()
    assert lines[0] == "0:00 Start the event" and len(lines) == 2, lines


def t_a_card_must_have_a_title_and_a_subtitle():
    for bad in ({"title": "x"}, {"title": "", "subtitle": "y"}, "x", {"title": "x", "subtitle": 3}):
        for where in ("intro", "step"):
            tl = cards_timeline()
            if where == "intro":
                tl["intro"] = bad
            else:
                tl["steps"][6]["card"] = bad
            try:
                fp.Plan(tl)
            except ValueError as exc:
                assert "card" in str(exc), exc
            else:
                raise AssertionError(f"a {where} card {bad!r} is refused")


def t_a_card_on_a_step_without_a_caption_is_refused():
    tl = cards_timeline()
    tl["steps"][2]["card"] = SWAP_CARD
    try:
        fp.Plan(tl)
    except ValueError as exc:
        assert "caption" in str(exc), exc
    else:
        raise AssertionError("a card on an uncaptioned step is refused")
    tl = cards_timeline()
    tl["steps"][3]["card"] = SWAP_CARD
    try:
        fp.Plan(tl)
    except ValueError as exc:
        assert "chapter" in str(exc), exc
    else:
        raise AssertionError("a card on a step that opens no chapter is refused")


def t_a_caption_still_acting_stops_at_a_card():
    tl = cards_timeline()
    steps = {s["id"]: s for s in tl["steps"]}
    steps["desk"].update(start=61.0, acted=63.0, end=80.0, chapter="Desk", card={"title": "Desk", "subtitle": "x"})
    p = fp.Plan(tl)
    spans = [(x.out, x.out + x.dur) for x in p.segments if x.card]
    approve = next(c for c in p.cues() if c[3] == "The director approves the link")
    assert approve[1] - approve[0] > 1.0, f"the cue before the card still shows: {approve}"
    for a, b, _who, text in p.cues():
        assert not any(a < e - EPS and s < b - EPS for s, e in spans), f"{text!r} runs across a card: {(a, b)} {spans}"


def t_the_first_chapter_lights_up_after_the_intro():
    tl = timeline()
    tl["intro"] = INTRO
    p = fp.Plan(tl)
    intro = round(fp.INTRO_S * p.fps)
    assert p.chapters()[0] == (0.0, "Start the event"), "chapters.txt still starts at 0:00"
    assert p.frame(intro - 1)["chapter"] is None, "no chapter under the intro"
    late = intro + round((fp.CHAPTER_FRESH_S - 0.2) * p.fps)
    assert p.frame(intro)["chapter_fresh"] and p.frame(late)["chapter_fresh"], \
        "the first chapter's highlight runs its full time after the intro"


def t_a_first_chapter_with_a_card_later_in_the_film_shows_after_its_card():
    tl = timeline()
    steps = {s["id"]: s for s in tl["steps"]}
    del steps["pick"]["chapter"]
    steps["switch"]["card"] = SWAP_CARD
    p = fp.Plan(tl)
    (start, n, _title), = [x for x in _card_spans(p) if x[2] == SWAP_CARD["title"]]
    assert start > 0, "the film opens on the recording, not on the card"
    assert p.frame(start - 1)["chapter"] is None, "the first chapter's title waits for its card"
    after = start + n
    assert p.frame(after)["chapter"] == "Driver swap 1" and p.frame(after)["chapter_fresh"], \
        "the title lights up as new once its card has gone"
    assert p.chapters()[0] == (0.0, "Driver swap 1"), "chapters.txt still starts at 0:00"


def t_a_first_step_that_started_before_its_view_shows_its_caption_from_frame_0():
    tl = timeline()
    for r in tl["recordings"]:
        if r["view"] == "producer":
            r["start"] = 2.0
    f = fp.Plan(tl).frame(0)
    assert f["speed"] == 0 and f["caption"] == "The producer picks the track", \
        f"the opening still already carries the first caption: {f['caption']}"


def t_a_partial_render_across_a_card_matches_the_whole_film():
    p = fp.Plan(cards_timeline())
    spans = _card_spans(p)
    for a0, n0, _ in spans[1:3]:
        start, stop = (a0 + n0 - 15) / p.fps, (a0 + n0 + 15) / p.fps
        q = fp.Plan(json.loads(json.dumps(cards_timeline())))
        a, b = q.frame_range(start, stop)
        assert any(q.frame(i)["card"] for i in range(a, b)) and not all(q.frame(i)["card"] for i in range(a, b))
        assert [q.frame(i) for i in range(a, b)] == [p.frame(i) for i in range(a, b)], \
            f"frames {a}-{b} of a partial render equal the whole film's"


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


SCENARIO_CARDS = """
intro = { title = "Intro", subtitle = "From the scenario" }
outro = { title = "Outro", subtitle = "From the scenario" }
[[step]]
id = "switch"
chapter = "Driver swap 1"
card = { title = "Swap", subtitle = "From the scenario" }
[[step]]
do = "pick"
chapter = "Start the event"
card = { title = "Start", subtitle = "An id that defaults to its action" }
[[step]]
do = "desk"
chapter = "Desk"
card = { title = "Desk", subtitle = "A chapter the recording does not have" }
"""


def t_a_recording_without_cards_takes_them_from_its_scenario_file():
    with tempfile.TemporaryDirectory() as tmp:
        rec, scenarios = os.path.join(tmp, "rec"), os.path.join(tmp, "scenarios")
        _write(os.path.join(scenarios, "fixture.toml"), SCENARIO_CARDS)
        _write(os.path.join(rec, "timeline.json"), json.dumps(timeline()))
        tl = fp.load_timeline(rec, scenarios=scenarios)
        assert tl["intro"]["title"] == "Intro" and tl["outro"]["title"] == "Outro", "the scenario's intro and outro"
        cards = {s["id"]: s.get("card") for s in tl["steps"]}
        assert cards["switch"] == {"title": "Swap", "subtitle": "From the scenario"}, "a card by step id"
        assert cards["pick"]["title"] == "Start", "a step's id defaults to its action, as in the loader"
        assert not cards["desk"], "a card whose chapter the recorded step does not open stays out"
        assert sum(1 for c in cards.values() if c) == 2, cards
        p = fp.Plan(json.loads(json.dumps(tl)))
        assert [p.frame(i) for i in range(0, p.frames, 7)] == [fp.Plan(tl).frame(i) for i in range(0, p.frames, 7)], \
            "a worker that reads the merged timeline back plans the same frames"
        _write(os.path.join(rec, "timeline.json"), json.dumps(cards_timeline()))
        assert fp.load_timeline(rec, scenarios=scenarios) == cards_timeline(), \
            "a recording that has cards keeps them and reads no scenario"
        for name in ("missing", "../scenarios/fixture", ""):
            _write(os.path.join(rec, "timeline.json"), json.dumps(dict(timeline(), scenario=name)))
            tl = fp.load_timeline(rec, scenarios=scenarios)
            assert "intro" not in tl and not any(s.get("card") for s in tl["steps"]), f"no cards for {name!r}"


def t_the_full_scenario_carries_the_film_s_cards():
    with tempfile.TemporaryDirectory() as rec:
        steps = [_step("track", "producer", 0, 1, 2, chapter="Qualifying (the day before)"),
                 _step("start_event", "producer", 3, 4, 5, chapter="Race day: start the event")]
        _write(os.path.join(rec, "timeline.json"), json.dumps({"scenario": "full", "steps": steps, "recordings": []}))
        tl = fp.load_timeline(rec)
    assert tl["intro"] == {"title": "GT Racing Broadcast", "subtitle": "An endurance event from qualifying to the flag"}
    assert tl["outro"] == {"title": "GT Racing Broadcast", "subtitle": "Setup and handbooks: the project wiki on GitHub"}
    assert [s["card"] for s in tl["steps"]] == [{"title": "Qualifying", "subtitle": "The day before the race"},
                                                 {"title": "Race day", "subtitle": "Three stints, two driver swaps"}]


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


def t_resting_views_never_overlap_each_other_except_the_one_in_front():
    for p in (plan(), fp.Plan(swaps_timeline())):
        for f in frames(p):
            if f["moving"]:
                continue
            shown = [(v, r) for v, r in f["rects"].items() if r[4] > 0 and v != f["front"]]
            for i, (va, ra) in enumerate(shown):
                for vb, rb in shown[i + 1:]:
                    assert not _overlap(ra, rb), f"{va} and {vb} overlap at {f['t']}"


def t_a_view_s_label_is_a_tab_above_its_picture_and_covers_nothing():
    sizes = set()
    for f in frames(plan()) + frames(fp.Plan(swaps_timeline())):
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
                if w != v and f["front"] not in (v, w):
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


def t_a_view_off_the_stage_gets_no_rect_and_leaves_the_layout():
    prev = fp.Layout(("director",), behind=("program", C1))
    kept = fp.focus_layout(_step("x", "api", 0, 0, 0, caption="c"), prev, ["director", "program"])
    assert kept.large == ("director",) and C1 not in kept.behind, f"a departed view leaves the layout: {kept}"
    sizes = {v: (1440, 900) for v in ("director", "program", C1)}
    rects = fp.target_rects(["director", "program"], prev, sizes)
    assert set(rects) == {"director", "program"}, f"no rect for a view off the stage: {set(rects)}"
    rects = fp.target_rects(["director"], fp.Layout(("director", "program")), sizes)
    assert set(rects) == {"director"}, set(rects)


def t_a_single_view_comes_forward_over_the_dimmed_stage():
    p = plan()
    f = _at(p, 2.0)
    assert f["large"] == ["producer"] and f["front"] == "producer" and not f["moving"]
    x, y, w, h = f["rects"]["producer"][:4]
    assert abs(x + w / 2 - fp.CANVAS[0] / 2) < 1, "the view in front stands in the centre"
    assert h >= 0.78 * fp.CANVAS[1], f"it takes about 80 % of the canvas: {h:.0f} px high"
    others = [r for v, r in f["rects"].items() if v != "producer" and r[4] > 0]
    assert others and all(r[4] <= fp.DIM + EPS for r in others), "the rest of the stage stays visible behind it, dimmed"
    two = _at(p, 61.0)
    assert two["front"] is None and two["large"] == ["director", "program"], "two views keep the side-by-side layout"
    assert all(r[4] == 1.0 for v, r in two["rects"].items() if r[4] > 0), "nothing is dimmed beside two views"
    fs = frames(p)
    i = next(i for i, f in enumerate(fs) if f["large"] == ["director", "program"])
    assert fs[i]["moving"] and fs[i]["front"] == "producer", "the view in front stays on top while it eases out"


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
    states = [p.frame(0), _at(p, 2.0), _at(p, 61.0), _at(p, 101.0), _at(p, 66.0)]
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
                    assert not any(_overlap(t, r) for w, *r in boxes if state["front"] not in (v, w)), \
                        f"the tab of {v} covers a picture: {t}"
                if state["front"]:
                    x, y, w, h = state["rects"][state["front"]][:4]
                    top = page.evaluate("([x, y]) => document.elementFromPoint(x, y).closest('.view').dataset.view",
                                        [x + w / 2, y + h / 2])
                    assert top == state["front"], f"{state['front']} lies over the stage, not {top}"
                assert (state["caption"] or "") in text
            assert not errors, errors
        finally:
            browser.close()


def t_the_stage_page_lays_a_card_out_inside_the_canvas_over_everything():
    sync_playwright = _playwright()
    if sync_playwright is None:
        return
    tl = cards_timeline()
    tl.update(intro={"title": "GT Racing Broadcast", "subtitle": "An endurance event from qualifying to the flag"},
              outro={"title": "GT Racing Broadcast", "subtitle": "Setup and handbooks: the project wiki on GitHub"})
    p = fp.Plan(tl, available=set())
    a, n, _ = _card_spans(p)[-1]
    url = "file://" + os.path.join(VIDEO, "stage.html").replace(os.sep, "/")
    with sync_playwright() as pw:
        browser, page, errors = _stage(pw, url)
        try:
            page.evaluate("cfg => setup(cfg)", p.stage_config({}))
            for i in (a, a + n // 2):
                state = p.frame(i)
                done = page.evaluate("s => setFrame(s)", state)
                assert done["ok"], done
                texts = page.evaluate("""() => ['#cardTitle', '#cardSub'].map(sel => {
                    const e = document.querySelector(sel), r = e.getBoundingClientRect();
                    return [e.textContent, +getComputedStyle(e).opacity, r.x, r.y, r.width, r.height]; })""")
                for text, opacity, *rect in texts:
                    assert _inside(rect) and rect[3] < 300, f"{text!r} lies inside the canvas on one line: {rect}"
                    assert abs(opacity - state["card"]["opacity"]) < 1e-3, f"{text!r} fades with the card: {opacity}"
                assert [t[0] for t in texts] == [state["card"]["title"], state["card"]["subtitle"]], texts
                hit = page.evaluate("() => [[40, 40], [1280, 1380], [2520, 720]].map(([x, y]) =>"
                                    " document.elementFromPoint(x, y).closest('#card') !== null)")
                assert all(hit), f"the card covers the whole stage, no picture behind it: {hit}"
            page.evaluate("s => setFrame(s)", p.frame(a - 1))
            assert page.evaluate("() => getComputedStyle(document.querySelector('#card')).display") == "none", \
                "the card leaves with its last frame"
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


def t_the_playlist_repeats_the_tracks_in_order_until_the_film_is_covered():
    durs = {"a.mp3": 100.0, "b.mp3": 50.0}
    assert fr.playlist(["a.mp3", "b.mp3"], durs, 140.0, xfade=3.0) == ["a.mp3", "b.mp3"], \
        "two tracks with one crossfade cover 147 s"
    assert fr.playlist(["a.mp3", "b.mp3"], durs, 150.0, xfade=3.0) == ["a.mp3", "b.mp3", "a.mp3"], \
        "the crossfade eats into the length, so 150 s needs a third track"
    assert fr.playlist(["a.mp3"], durs, 30.0, xfade=3.0) == ["a.mp3"]


def t_the_music_bed_crossfades_each_track_into_the_next():
    s = " ".join(fr.bed_argv(["ffmpeg"], ["a.mp3", "b.mp3", "a.mp3"], "bed.flac", xfade=3.0))
    assert s.count("-i a.mp3") == 2 and s.count("-i b.mp3") == 1, s
    assert "[0:a][1:a]acrossfade=d=3.000[x1];[x1][2:a]acrossfade=d=3.000[x2]" in s, s
    assert "-map [x2]" in s and s.endswith("bed.flac"), s


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


def t_the_cli_takes_several_music_tracks_and_refuses_a_missing_one():
    assert cli.parse_args(["--music", "a.mp3", "--music", "b.mp3", "/x/r"]).music == ["a.mp3", "b.mp3"], \
        "the option repeats per track, so a record dir after it stays the record dir"
    with tempfile.TemporaryDirectory() as tmp:
        rec = os.path.join(tmp, "rec")
        os.makedirs(rec)
        with open(os.path.join(rec, "timeline.json"), "w", encoding="utf-8") as fh:
            json.dump(timeline(), fh)
        track = os.path.join(tmp, "a.mp3")
        open(track, "w").close()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = cli.main([rec, "--out", os.path.join(tmp, "out"), "--music", track, "--music", os.path.join(tmp, "gone.mp3")],
                            render=lambda *a, **k: 0)
        assert code == 2 and "gone.mp3" in err.getvalue() and "a.mp3" not in err.getvalue(), err.getvalue()


def t_an_unusable_music_track_stops_the_render_before_any_capture():
    calls = []
    saved = fr.music_bed, fr.transcode_all
    fr.music_bed = lambda *a, **k: None
    fr.transcode_all = lambda *a, **k: calls.append("transcode") or ({}, {})
    try:
        with tempfile.TemporaryDirectory() as tmp:
            code = fr.render(plan(), timeline(), tmp, os.path.join(tmp, "out"), (0, 10), music=["a.mp3", "b.mp3"],
                             log=lambda *_: None)
    finally:
        fr.music_bed, fr.transcode_all = saved
    assert code == 1 and calls == [], f"the bed is checked before the recording is transcoded: {code} {calls}"


def t_an_unreadable_music_track_stops_the_bed_with_its_name():
    logs = []

    def run(argv, **k):
        out = "" if argv[-1] == "bad.mp3" else "200.0"
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")
    assert fr.music_bed(["good.mp3", "bad.mp3"], 300.0, "/w", ["ffmpeg"], ["ffprobe"], run=run, log=logs.append) is None
    assert logs and "bad.mp3" in logs[0], logs


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
    assert f["large"][0] == "director" and "program" not in f["large"], \
        f"a focus on the missing program drops it: {f['large']}"
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

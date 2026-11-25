#!/usr/bin/env python3
"""Stdlib unit checks for the relay race timer. Run: python3 tests/test_timer.py"""
import importlib.util, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "irofeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def t_parse_duration():
    assert m.parse_duration("6:00:00") == 21600
    assert m.parse_duration("06:00:00") == 21600
    assert m.parse_duration("0:00:30") == 30
    assert m.parse_duration("24:00:00") == 86400
    assert m.parse_duration(" 1:02:03 ") == 3723
    assert m.parse_duration("100:00:00") is None   # 3-digit hours rejected
    for bad in ("", None, "90", "1:2", "1:60:00", "1:00:60", "abc", "-1:00:00"):
        assert m.parse_duration(bad) is None, bad


def t_format_duration():
    assert m.format_duration(21600) == "6:00:00"
    assert m.format_duration(3723) == "1:02:03"
    assert m.format_duration(0) == "0:00:00"
    assert m.format_duration(86400) == "24:00:00"
    assert m.format_duration(3723.9) == "1:02:03"   # truncates float


def t_parse_utc_ts():
    # Canonical ISO, Apps Script toISOString with a fraction, and gviz-reformatted.
    assert m.parse_utc_ts("2026-06-13T20:00:00Z") == 1781380800.0
    assert m.parse_utc_ts("2026-06-13T20:00:00.000Z") == 1781380800.0
    assert m.parse_utc_ts("2026-06-13 20:00:00") == 1781380800.0
    for bad in ("", None, "tomorrow", "2026-06-13", "20:00:00"):
        assert m.parse_utc_ts(bad) is None, bad


def t_iso_utc_roundtrip():
    assert m.iso_utc(1781380800.0) == "2026-06-13T20:00:00Z"
    assert m.parse_utc_ts(m.iso_utc(1781380800.0)) == 1781380800.0


TIMER_CSV = (
    "Race End (UTC),2026-06-13T20:00:00Z\n"
    "Duration,6:00:00\n"
    "Visible,FALSE\n"
    "Updated (UTC),2026-06-13T13:59:58Z\n"
)


def t_parse_timer_tab():
    st = m.parse_timer_tab(TIMER_CSV)
    assert st["end"] == 1781380800.0
    assert st["duration"] == 21600
    assert st["visible"] is False
    assert st["updated"] == 1781359198.0
    assert st["remaining"] is None


def t_parse_timer_tab_remaining():
    st = m.parse_timer_tab("Duration,6:00:00\nRemaining,1:30:00\n")
    assert st["end"] is None and st["remaining"] == 5400
    # end and remaining are mutually exclusive, and a set anchor wins.
    st = m.parse_timer_tab(
        "Race End (UTC),2026-06-13T20:00:00Z\nRemaining,1:30:00\n")
    assert st["end"] == 1781380800.0 and st["remaining"] is None


def t_parse_timer_tab_defaults_and_garbage():
    # Empty or missing values fall back to the default state fields.
    st = m.parse_timer_tab("Race End (UTC),\nDuration,\nVisible,\n")
    assert st["end"] is None and st["duration"] == 21600
    assert st["visible"] is True and st["updated"] == 0.0
    st = m.parse_timer_tab("")
    assert st["end"] is None
    st = m.parse_timer_tab("garbage,x\nmore,y\n")
    assert st["end"] is None and st["visible"] is True
    # The label match is case-insensitive, and gviz may reformat the timestamp.
    st = m.parse_timer_tab("race end (utc),2026-06-13 20:00:00\n")
    assert st["end"] == 1781380800.0


def t_timer_mode():
    base = {"end": 1000.0, "duration": 21600, "remaining": None,
            "visible": True, "updated": 0.0}
    assert m.timer_mode(dict(base, visible=False), 500.0) == "hidden"
    assert m.timer_mode(dict(base, end=None), 500.0) == "prestart"
    assert m.timer_mode(base, 500.0) == "running"
    assert m.timer_mode(base, 1000.0) == "finished"
    assert m.timer_mode(base, 2000.0) == "finished"
    assert m.timer_mode(dict(base, end=None, remaining=300), 500.0) == "paused"
    # hidden wins over everything
    assert m.timer_mode(dict(base, end=None, visible=False), 0.0) == "hidden"
    assert m.timer_mode(dict(base, end=None, remaining=300, visible=False),
                        0.0) == "hidden"


def t_merge_timer_states_newest_wins():
    local = {"end": 1.0, "duration": 60, "visible": True, "updated": 100.0}
    sheet = {"end": 2.0, "duration": 90, "visible": False, "updated": 200.0}
    assert m.merge_timer_states(local, sheet) == sheet
    assert m.merge_timer_states(sheet, local) == sheet     # order-insensitive
    # On a tie the first argument wins, and a None sheet keeps local.
    tie = dict(sheet, updated=100.0)
    assert m.merge_timer_states(local, tie) == local
    assert m.merge_timer_states(local, None) == local


def _store(tmp=None, push_url=None, csv_url="http://sheet"):
    import tempfile, os as _os
    tmp = tmp or tempfile.mkdtemp()
    return m.TimerStore(csv_url, push_url, _os.path.join(tmp, "timer.json")), tmp


def t_timerstore_actions_update_state_and_stamp():
    ts, _ = _store()
    ts.set_duration(7200, now=900.0)
    assert ts.state["duration"] == 7200 and ts.state["updated"] == 900.0
    ts.start(now=1000.0)
    assert ts.state["end"] == 1000.0 + 7200
    assert ts.state["updated"] == 1000.0
    ts.adjust(-60, now=1000.0)
    assert ts.state["end"] == 1000.0 + 7200 - 60
    ts.hide(); assert ts.state["visible"] is False
    ts.show(); assert ts.state["visible"] is True
    ts.reset(); assert ts.state["end"] is None and ts.state["remaining"] is None


def t_timerstore_stop_pauses_and_start_resumes():
    ts, _ = _store()
    ts.set_duration(7200, now=900.0)
    ts.start(now=1000.0)                        # end = 8200
    ts.stop(now=2000.0)                         # pause with 6200 s left
    assert ts.state["end"] is None
    assert ts.state["remaining"] == 6200
    assert m.timer_mode(ts.state, 2000.0) == "paused"
    ts.start(now=5000.0)                        # resume: end = now + remaining
    assert ts.state["end"] == 5000.0 + 6200
    assert ts.state["remaining"] is None
    # start while already running -> no-op (anchor untouched)
    r = ts.start(now=6000.0)
    assert ts.state["end"] == 5000.0 + 6200 and "note" in r
    # stop while not running -> no-op note, state untouched
    ts.reset()
    r = ts.stop(now=7000.0)
    assert "note" in r and ts.state["remaining"] is None


def t_timerstore_adjust_is_context_sensitive():
    ts, _ = _store()
    ts.set_duration(3600, now=10.0)
    ts.adjust(60, now=11.0)                     # prestart -> duration
    assert ts.state["duration"] == 3660 and ts.state["end"] is None
    ts.adjust(-7200, now=12.0)                  # clamped at 0
    assert ts.state["duration"] == 0
    ts.set_duration(3600, now=13.0)
    ts.start(now=100.0)
    ts.adjust(-60, now=100.0)                   # running -> shifts the anchor
    assert ts.state["end"] == 100.0 + 3600 - 60
    ts.stop(now=200.0)                          # paused: 3640 - 200 = 3440 left
    ts.adjust(60, now=201.0)                    # paused -> shifts the remainder
    assert ts.state["remaining"] == 3500
    ts.adjust(-9999, now=202.0)                 # clamped at 0
    assert ts.state["remaining"] == 0


def t_timerstore_persists_and_reloads():
    import os as _os
    ts, tmp = _store()
    ts.set_duration(3600); ts.start(now=5000.0); ts.hide()
    ts2, _ = _store(tmp=tmp)
    assert ts2.state["end"] == 5000.0 + 3600
    assert ts2.state["duration"] == 3600
    assert ts2.state["visible"] is False
    assert _os.path.exists(_os.path.join(tmp, "timer.json"))
    ts2.stop(now=6000.0)                        # paused remainder survives reload too
    ts3, _ = _store(tmp=tmp)
    assert ts3.state["remaining"] == 2600 and ts3.state["end"] is None


def t_timerstore_refresh_adopts_newer_sheet():
    ts, _ = _store()
    ts.set_duration(3600)                       # local write, updated = now
    newer = m.iso_utc(ts.state["updated"] + 100)
    ts._fetch = lambda url, timeout=10: (
        f"Race End (UTC),2026-06-13T20:00:00Z\nDuration,4:00:00\n"
        f"Visible,TRUE\nUpdated (UTC),{newer}\n")
    assert ts.refresh() is True
    assert ts.state["end"] == 1781380800.0 and ts.state["duration"] == 14400


def t_timerstore_refresh_keeps_newer_local():
    ts, _ = _store()
    ts._fetch = lambda url, timeout=10: (
        "Race End (UTC),2026-06-13T20:00:00Z\nDuration,4:00:00\n"
        "Visible,TRUE\nUpdated (UTC),2000-01-01T00:00:00Z\n")
    ts.refresh()
    ts.set_duration(3600)                       # local now newer than sheet
    ts.refresh()
    assert ts.state["duration"] == 3600         # stale sheet did not revert it


def t_timerstore_refresh_failure_keeps_state():
    ts, _ = _store()
    ts.set_duration(3600)
    def boom(url, timeout=10):
        raise RuntimeError("sheet down")
    ts._fetch = boom
    assert ts.refresh() is False
    assert ts.state["duration"] == 3600 and ts.last_error


def t_timerstore_push_payload_and_status():
    ts, _ = _store(push_url="http://push?key=k")
    sent = []
    orig = m.post_webhook
    _base, m.WEBHOOK_RETRY_BASE_S = m.WEBHOOK_RETRY_BASE_S, 0.0
    try:
        def post_ok(url, payload, timeout=10):
            sent.append((url, payload)); return b'{"ok":true}'
        m.post_webhook = post_ok
        ts._spawn_push = ts._push                   # synchronous for the test
        ts.set_duration(7200); ts.start(now=1000.0)
        url, payload = sent[-1]
        assert url == "http://push?key=k"
        assert payload == {"end": m.iso_utc(8200.0), "duration": "2:00:00",
                           "visible": "TRUE", "remaining": ""}
        assert ts.push_status == "ok"
        ts.stop(now=2000.0)                         # paused -> remaining travels too
        p = sent[-1][1]
        assert p["end"] == "" and p["remaining"] == "1:43:20"   # 6200 s
        def fail(url, payload, timeout=10):
            raise RuntimeError("403")
        m.post_webhook = fail
        ts.hide()
        assert ts.push_status == "failed"
    finally:
        m.post_webhook = orig
        m.WEBHOOK_RETRY_BASE_S = _base


def t_timerstore_push_unconfirmed_is_failed():
    # Apps Script answers HTTP 200 even for errors, so only {"ok": true} counts.
    ts, _ = _store(push_url="http://push?key=k")
    ts._spawn_push = ts._push
    orig = m.post_webhook
    _base, m.WEBHOOK_RETRY_BASE_S = m.WEBHOOK_RETRY_BASE_S, 0.0
    try:
        for resp in (b'{"error":"bad key"}', b"<html>exception</html>", b"", None):
            m.post_webhook = lambda url, payload, timeout=10, r=resp: r
            ts.set_duration(7200)
            assert ts.push_status == "failed", resp
            assert "did not confirm" in ts.last_error
    finally:
        m.post_webhook = orig
        m.WEBHOOK_RETRY_BASE_S = _base


def t_timerstore_push_disabled_without_url():
    ts, _ = _store(push_url=None)
    ts.set_duration(7200)
    assert ts.push_status == "disabled"


def t_timerstore_data_contract():
    ts, _ = _store()
    d = ts.data(now=123.0)
    assert d["mode"] == "prestart" and d["visible"] is True
    assert d["end"] is None and d["duration_s"] == m.TIMER_DEFAULT_DURATION
    assert d["remaining_s"] is None
    assert d["server_now"] == 123.0
    assert d["sync"]["push"] == "disabled"
    assert set(d["sync"]) == {"push", "sheet_last_ok_age_s", "last_error"}
    ts.set_duration(600, now=10.0); ts.start(now=10.0); ts.stop(now=70.0)
    d = ts.data(now=80.0)
    assert d["mode"] == "paused" and d["remaining_s"] == 540


def t_timerstore_summary():
    ts, _ = _store()
    assert ts.summary() == {"mode": "prestart", "visible": True,
                            "remaining_s": None, "push": "disabled"}
    ts.set_duration(60, now=10.0); ts.start(now=10.0)
    # The anchor is long past against the real clock, so it clamps to 0.
    s = ts.summary()
    assert s["mode"] == "finished" and s["remaining_s"] == 0
    ts.start(now=10.0)                          # still running per state -> note
    ts.stop(now=40.0)                           # but pause math still works
    assert ts.summary() == {"mode": "paused", "visible": True,
                            "remaining_s": 30, "push": "disabled"}


def t_autostart_starts_only_a_timer_that_never_started():
    ts, _ = _store()
    ts.set_duration(3600, now=10.0)
    ts.hide(now=11.0)
    assert ts.autostart(at=90.0, now=100.0) is True
    assert ts.state["end"] == 3690.0 and ts.state["remaining"] is None, \
        f"the countdown runs from the first race lap: {ts.state}"
    assert ts.state["updated"] == 100.0 and ts.state["visible"] is False, ts.state
    assert ts.autostart(now=200.0) is False and ts.state["end"] == 3690.0, \
        "a running timer is never restarted"
    ts.stop(now=300.0)
    assert ts.autostart(now=400.0) is False and ts.state["end"] is None \
        and ts.state["remaining"] == 3390, f"a paused timer stays paused: {ts.state}"
    ts.reset(now=500.0)
    ts.start(now=500.0)
    assert ts.autostart(now=10_000.0) is False and ts.state["end"] == 4100.0, \
        f"a finished timer stays finished: {ts.state}"


def t_autostart_pushes_like_start():
    ts, _ = _store(push_url="http://push")
    pushed = []
    ts._spawn_push = pushed.append
    ts.autostart(now=100.0)
    ts.autostart(now=200.0)
    assert len(pushed) == 1 and pushed[0]["end"] == m.iso_utc(100.0 + m.TIMER_DEFAULT_DURATION), pushed


def t_the_sheet_state_is_known_only_after_a_read():
    ts, _ = _store()
    assert ts.sheet_known() is False, "a Sheet-synced timer is unknown before its first read"
    ts._fetch = lambda url, timeout=10: ""
    ts.refresh()
    assert ts.sheet_known() is True
    local, _ = _store(csv_url=None)
    assert local.sheet_known() is True, "a local-only timer has no Sheet to wait for"


class _Race:
    def __init__(self, starts=()):
        self.starts = list(starts)
        self.gen = 1

    def take_race_start(self):
        return (True, self.gen) if self.starts and self.starts.pop(0) else None

    def start_generation(self):
        return self.gen


def t_timer_autostart_starts_only_on_an_armed_race_start():
    ts, _ = _store(csv_url=None)
    auto = m.TimerAutostart(ts, _Race([False, False]))
    for now in (1.0, 2.0, 3.0):
        auto.observe(now=now)
    assert ts.state["end"] is None, "without Race start nothing starts the timer"
    auto = m.TimerAutostart(ts, _Race([False, True, False]))
    for now in (10.0, 11.0, 12.0):
        auto.observe(now=now)
    assert ts.state["end"] == 11.0 + m.TIMER_DEFAULT_DURATION, ts.state


def t_timer_autostart_waits_for_the_sheet_and_keeps_its_running_anchor():
    ts, _ = _store()
    auto = m.TimerAutostart(ts, _Race([True]))
    auto.observe(now=1000.0)
    auto.observe(now=1001.0)
    assert ts.state["end"] is None, f"no start while the Sheet state is unknown: {ts.state}"
    ts._fetch = lambda url, timeout=10: TIMER_CSV            # director A started the race
    ts.refresh()
    auto.observe(now=1002.0)
    assert ts.state["end"] == 1781380800.0, f"the Sheet's running anchor wins: {ts.state}"
    fresh, _ = _store()
    auto = m.TimerAutostart(fresh, _Race([True]))
    auto.observe(now=1000.0)
    fresh._fetch = lambda url, timeout=10: ""
    fresh.refresh()
    auto.observe(now=1030.0)
    assert fresh.state["end"] == 1000.0 + m.TIMER_DEFAULT_DURATION, \
        f"a late start still counts from the first race lap: {fresh.state}"


def t_a_waiting_start_is_visible_and_named_after_a_while():
    ts, _ = _store()
    auto = m.TimerAutostart(ts, _Race([True]))
    assert auto.pending() is False and auto.waiting_text(now=0.0) is None
    auto.observe(now=1000.0)
    assert auto.pending() is True, "a start waiting for the Sheet is visible"
    assert auto.waiting_text(now=1000.0 + m.TIMER_START_WAIT_S - 1) is None, "a short wait stays quiet"
    text = auto.waiting_text(now=1000.0 + m.TIMER_START_WAIT_S + 1)
    assert text and "Timer tab" in text, f"a long wait becomes a health line: {text}"
    auto.observe(now=5000.0)
    assert auto.pending() is True and ts.state["end"] is None, "it keeps waiting, never gives up"


def t_a_waiting_start_goes_with_a_new_event_or_a_new_race():
    ts, _ = _store()
    race = _Race([True])
    auto = m.TimerAutostart(ts, race)
    auto.observe(now=1000.0)
    race.gen = 2
    auto.observe(now=1001.0)
    assert auto.pending() is False, "a new event or race reset drops the waiting start"
    ts._fetch = lambda url, timeout=10: ""
    ts.refresh()
    auto.observe(now=1002.0)
    assert ts.state["end"] is None, f"the dropped start never fires: {ts.state}"


def t_a_reset_right_after_the_take_drops_the_start():
    import test_driver_race as tdr
    svc, clock, cars = tdr._practice_then_grid()
    for car, grid in zip(cars, (-20.0, -40.0), strict=True):
        car.along = grid
    svc.race_start("tailnet")
    take = svc.take_race_start

    def take_then_reset():
        got = take()
        if got is not None:
            svc.reset("tailnet")          # a race reset lands between the take and the next read
        return got
    svc.take_race_start = take_then_reset
    ts, _ = _store(csv_url=None)
    auto = m.TimerAutostart(ts, svc)
    for _ in range(80):
        clock[0] += 0.1
        for car in cars:
            car.along += 5.0
            svc.update(car.reg, tdr._sample(clock[0], car.along, car.lap(), 50.0))
        svc.tick()
        auto.observe(now=clock[0])
    assert ts.state["end"] is None, f"a start taken before a race reset never fires: {ts.state}"


def t_after_a_keep_unlock_the_next_race_start_leaves_the_timer_alone():
    import test_driver_race as tdr
    svc, clock, cars = tdr._armed_race_under_way()
    svc.unlock_start("director:bob", "keep")
    svc.race_start("race_control:dave")
    ts, _ = _store(csv_url=None)
    auto = m.TimerAutostart(ts, svc)
    for _ in range(30):
        clock[0] += 0.1
        for car in cars:
            car.along += 5.0
            svc.update(car.reg, tdr._sample(clock[0], car.along, car.lap(), 50.0))
        svc.tick()
        auto.observe(now=clock[0])
    assert svc.race_started() and ts.state["end"] is None, \
        f"keep: the restarted race does not start a timer: {ts.state}"


def t_a_fresh_unlock_drops_a_start_waiting_for_the_sheet():
    import test_driver_race as tdr
    svc, clock, cars = tdr._practice_then_grid()
    for car, grid in zip(cars, (-20.0, -40.0), strict=True):
        car.along = grid
    svc.race_start("tailnet")
    ts, _ = _store()
    auto = m.TimerAutostart(ts, svc)

    def drive(steps):
        for _ in range(steps):
            clock[0] += 0.1
            for car in cars:
                car.along += 5.0
                svc.update(car.reg, tdr._sample(clock[0], car.along, car.lap(), 50.0))
            svc.tick()
            auto.observe(now=clock[0])
    drive(30)
    assert auto.pending(), "the first lap's start waits for the Sheet"
    svc.unlock_start("director:bob", "fresh")
    ts.reset(now=clock[0])
    ts._fetch = lambda url, timeout=10: ""
    ts.refresh()
    auto.observe(now=clock[0])
    assert ts.state["end"] is None and not auto.pending(), \
        f"a fresh unlock drops the old race's waiting start: {ts.state}"


def t_the_lock_names_the_timer_state():
    unknown, _ = _store()
    assert "not known yet" in (unknown.start_lock(now=0.0) or ""), \
        "a Sheet-synced timer that never read the Sheet locks Race start"
    ts, _ = _store(csv_url=None)
    assert ts.start_lock(now=0.0) is None
    ts.start(now=100.0)
    assert "runs" in ts.start_lock(now=200.0), ts.start_lock(now=200.0)
    assert "finished" in ts.start_lock(now=100.0 + m.TIMER_DEFAULT_DURATION + 1), \
        "a stale timer from the last event reads as finished"
    ts.stop(now=300.0)
    assert "paused" in (ts.start_lock(now=400.0) or ""), f"a paused timer locks: {ts.start_lock(now=400.0)}"
    ts.reset(now=500.0)
    assert ts.start_lock(now=600.0) is None, "RESET unlocks"


def t_dashboard_ticks_hands_the_tick_to_the_autostart():
    class _Relay:
        def _tick_ok(self, _name):
            pass

        def _warn_tick_error(self, name, e):
            warned.append((name, str(e)))

    class _Svc:
        def tick(self):
            pass

        def race_state(self):
            return None

    class _Stop:
        n = 3

        def wait(self, _s):
            self.n -= 1
            return self.n < 0

    class _Seen:
        def observe(self):
            seen.append(1)

    class _Boom:
        def observe(self):
            raise ValueError("boom")
    warned, seen = [], []
    m.dashboard_ticks(_Relay(), _Svc(), _Stop(), 0, None, None, _Seen())
    assert len(seen) == 3, seen
    _Stop.n = 1
    m.dashboard_ticks(_Relay(), _Svc(), _Stop(), 0, None, None, _Boom())
    assert warned == [("race timer autostart", "boom")], f"a failing autostart is warned: {warned}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

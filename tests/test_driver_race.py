#!/usr/bin/env python3
"""The relay's live race service: settings, layout pick and stint switch (#1112).
Run: python3 tests/test_driver_race.py"""
import os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "scripts"))
import driver_race as dr  # noqa: E402
from test_race_distance import TRACE, _pos  # noqa: E402


def _sample(t, along, lap, speed=50.0):
    x, z = _pos(along)
    return {"mono": t, "lap": lap, "x": x, "z": z, "speed_kmh": speed * 3.6, "race_position": None}


def t_settings_validate_persist_and_fall_back():
    path = os.path.join(tempfile.mkdtemp(), "driver-race.json")
    assert dr.read_settings(path) == {"formation": True, "finish_mode": "laps", "lap_count": None}
    saved = dr.write_settings(path, {"formation": False, "lap_count": 12})
    assert saved == {"formation": False, "finish_mode": "laps", "lap_count": 12} == dr.read_settings(path)
    for bad in ({"formation": "no"}, {"finish_mode": "sprint"}, {"lap_count": 0},
                {"lap_count": 2.5}, {"lap_count": True}):
        try:
            dr.write_settings(path, bad)
        except ValueError:
            pass  # expected
        else:
            raise AssertionError(f"{bad} must be refused")
    assert dr.read_settings(path)["lap_count"] == 12, "a refused change writes nothing"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"lap_count": "many"}')
    assert dr.read_settings(path) == dr.DEFAULTS, "a broken file keeps the defaults"


def _service(pick, multi=False):
    path = os.path.join(tempfile.mkdtemp(), "driver-race.json")
    dr.write_settings(path, {"formation": False})
    clock = [0.0]
    svc = dr.Service(path, pick=lambda: pick[0], trace_for=lambda i: TRACE if i == "good" else None,
                     multi_lobby=lambda: multi, clock=lambda: clock[0])
    return svc, clock


def t_the_picked_line_drives_the_race_and_a_new_pick_starts_over():
    pick = [None]
    svc, clock = _service(pick)
    svc.update({"entry": "A", "conn": "a", "name": "Max"}, _sample(0.1, -5, 1))
    assert svc.standings()["state"] == "no_line", "no pick: no distance, and it says so"
    pick[0] = {"id": "good"}
    for i in range(1, 30):
        clock[0] = i / 10
        svc.update({"entry": "A", "conn": "a", "name": "Max"}, _sample(i / 10, i * 5, 2))
    snap = svc.standings()
    assert snap["state"] == "racing" and snap["track"] == "good" and snap["entries"][0]["distance_m"] > 100, snap
    pick[0] = {"id": "other"}
    assert svc.standings()["state"] == "no_line" and svc.standings()["entries"] == [], \
        "distances along another line mean nothing: the race starts over"


def t_settings_apply_live_and_the_final_stint_brings_its_lap_count():
    pick = [{"id": "good"}]
    svc, clock = _service(pick)
    svc.set_settings({"finish_mode": "time"})
    assert svc.standings()["settings"]["finish_mode"] == "time" and svc._race.finish_mode == "time"
    svc.set_settings({"lap_count": 5})
    assert svc._race.lap_count == 5, "a single lobby takes the lap count from the settings"
    team, _clock = _service([{"id": "good"}], multi=True)
    team.set_settings({"lap_count": 5})
    assert team._race.lap_count is None, "endurance counts laps only in the final stint lobby"
    team.stint_switch()
    assert team._race.lap_count is None and team._race._window_until is not None
    team.stint_switch(final_laps=17)
    assert team._race.lap_count == 17


def t_a_line_downloaded_after_the_pick_is_picked_up():
    path = os.path.join(tempfile.mkdtemp(), "driver-race.json")
    clock, lines = [0.0], {}
    svc = dr.Service(path, pick=lambda: {"id": "late"}, trace_for=lines.get,
                     multi_lobby=lambda: False, clock=lambda: clock[0])
    assert svc.standings()["state"] == "no_line"
    lines["late"] = TRACE
    clock[0] = dr.LINE_RETRY_S - 1
    assert svc.standings()["state"] == "no_line", "the lookup is rate-limited"
    clock[0] = dr.LINE_RETRY_S + 1
    assert svc.standings()["state"] == "waiting", "the downloaded line is used without a new pick"


def t_final_lap_finish_and_reset():
    pick = [{"id": "good"}]
    svc, clock = _service(pick)
    svc.update({"entry": "A", "conn": "a", "name": "Max"}, _sample(0.1, 5, 2))
    svc.final_lap()
    assert svc.standings()["state"] == "final_lap"
    svc.finish()
    assert svc.standings()["state"] == "finished"
    svc.reset()
    assert svc.standings()["state"] == "waiting" and svc.standings()["entries"] == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

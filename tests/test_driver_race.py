#!/usr/bin/env python3
"""The relay's live race service: settings, layout pick and stint switch (#1112).
Run: python3 tests/test_driver_race.py"""
import json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "scripts"))
import driver_race as dr  # noqa: E402
from test_race_distance import TRACE, L, _pos  # noqa: E402


def _sample(t, along, lap, speed=50.0):
    x, z = _pos(along)
    return {"mono": t, "lap": lap, "x": x, "z": z, "speed_kmh": speed * 3.6, "race_position": None}


def t_settings_validate_persist_and_fall_back():
    path = os.path.join(tempfile.mkdtemp(), "driver-race.json")
    assert dr.read_settings(path) == {"formation": True, "finish_mode": "laps", "lap_count": None,
                                      "delay_s": 0}
    saved = dr.write_settings(path, {"formation": False, "lap_count": 12})
    assert saved == {"formation": False, "finish_mode": "laps", "lap_count": 12, "delay_s": 0} == dr.read_settings(path)
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
    for i in range(1, 34):                       # from the grid across the line
        clock[0] = i / 10
        along = i * 5 - 20
        svc.update({"entry": "A", "conn": "a", "name": "Max"},
                   _sample(i / 10, along, 1 if along >= 0 else 0))
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
    restarted, _c = _service([{"id": "good"}], multi=True)
    restarted.stint_switch(final_laps=9, window=False)
    assert restarted._race.lap_count == 9 and restarted._race._window_until is None, \
        "a relay start on the last row brings its lap count without a change window"


def t_qualifying_mode_feeds_no_race():
    path = os.path.join(tempfile.mkdtemp(), "driver-race.json")
    dr.write_settings(path, {"formation": False})
    mode = ["qualifying"]
    svc = dr.Service(path, pick=lambda: {"id": "good"}, trace_for=lambda _i: TRACE,
                     multi_lobby=lambda: False, clock=lambda: 1.0, enabled=lambda: mode[0] == "race")
    svc.update({"entry": "A", "conn": "a", "name": "Max"}, _sample(0.1, 5, 1))
    assert svc.standings()["entries"] == [], "qualifying laps are no race distance"
    mode[0] = "race"
    svc.update({"entry": "A", "conn": "a", "name": "Max"}, _sample(0.2, 10, 1))
    assert [r["entry"] for r in svc.standings()["entries"]] == ["A"]


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


def _paths():
    d = tempfile.mkdtemp()
    return (os.path.join(d, "runtime", "driver-race.json"), os.path.join(d, "league", "driver-race.json"))


def _ctl(multi=False, wall=None):
    path, league = _paths()
    dr.write_settings(path, {"formation": False})
    clock = [0.0]
    svc = dr.Service(path, pick=lambda: {"id": "good"}, trace_for=lambda _i: TRACE,
                     multi_lobby=lambda: multi, clock=lambda: clock[0], league_path=league,
                     wall=wall or (lambda: 1_800_000_000.0))
    return svc, clock, path, league


def _audit(path):
    import json
    with open(dr.audit_path_for(path), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def t_the_league_restart_default_and_its_override_per_restart():
    svc, _clock, path, league = _ctl()
    assert dr.read_league(league) == {"restart": "keep"}
    dr.write_league(league, {"restart": "neutralise"})
    assert svc.red_flag("rc:Anna")["red_flag"]["mode"] == "neutralise", "the league default applies"
    assert svc.set_restart_mode("director:Bo", "keep")["red_flag"]["mode"] == "keep"
    assert svc.release("rc:Anna")["red_flag"] is None
    for bad in ({"restart": "fast"}, {"restart": None}):
        try:
            dr.write_league(league, bad)
        except ValueError:
            pass  # expected
        else:
            raise AssertionError(f"{bad} must be refused")
    actions = [r["action"] for r in _audit(path)]
    assert actions == ["red_flag", "restart_mode", "release"], actions
    assert _audit(path)[1]["who"] == "director:Bo" and _audit(path)[1]["mode"] == "keep"


def t_corrections_and_rejoins_survive_a_relay_restart():
    svc, _clock, path, league = _ctl(multi=True)
    svc.correct("rc:Anna", "A", metres=-250.0, pit_stops=1, reason="missed a cut")
    svc.rejoin("rc:Anna", "B", True)
    again = dr.Service(path, pick=lambda: {"id": "good"}, trace_for=lambda _i: TRACE,
                       multi_lobby=lambda: True, clock=lambda: 5.0, league_path=league)
    again.update({"entry": "A", "conn": "a", "name": "Max"}, _sample(0.1, -20, 0))
    again.update({"entry": "B", "conn": "b", "name": "Ann"}, _sample(0.1, -20, 0))
    rows = {r["entry"]: r for r in again.standings()["entries"]}
    assert rows["A"]["correction_m"] == -250.0 and rows["A"]["pit_stops"] == 1, rows["A"]
    assert rows["B"]["rejoin"] is True
    rec = _audit(path)[0]
    assert rec == {"ts": 1_800_000_000.0, "who": "rc:Anna", "action": "correct", "entry": "A",
                   "metres": -250.0, "pit_stops": 1, "reason": "missed a cut"}, rec
    last = again.standings()["last_action"]
    assert last is not None and last["who"] == "rc:Anna", f"the last action survives a restart: {last}"


def t_actions_validate_their_input():
    svc, _clock, path, _league = _ctl()
    for call in (lambda: svc.correct("rc:A", "A", metres=10.0, reason=""),
                 lambda: svc.correct("rc:A", "A", metres=float("nan"), reason="x"),
                 lambda: svc.correct("rc:A", "A", metres=1e9, reason="x"),
                 lambda: svc.correct("rc:A", "A", pit_stops=1.5, reason="x"),
                 lambda: svc.correct("rc:A", "", metres=1.0, reason="x"),
                 lambda: svc.correct("rc:A", "A", metres=1.0, reason="x" * 201),
                 lambda: svc.red_flag("rc:A", mode="maybe"),
                 lambda: svc.rejoin("rc:A", "A", "yes")):
        try:
            call()
        except ValueError:
            pass  # expected
        else:
            raise AssertionError("an invalid action must be refused")
    assert not os.path.exists(dr.audit_path_for(path)), "a refused action logs nothing"


def t_reset_and_a_fresh_event_drop_corrections_and_rejoins():
    svc, _clock, path, _league = _ctl(multi=True)
    svc.correct("rc:Anna", "A", metres=-250.0, reason="missed a cut")
    svc.rejoin("rc:Anna", "B", True)
    svc.reset("rc:Anna")
    svc.update({"entry": "A", "conn": "a", "name": "Max"}, _sample(0.1, -20, 0))
    assert svc.standings()["entries"][0]["correction_m"] == 0.0, "a reset race starts without corrections"
    svc.correct("rc:Anna", "A", metres=10.0, reason="x")
    dr.clear_actions(os.path.dirname(path))
    fresh = dr.Service(path, pick=lambda: {"id": "good"}, trace_for=lambda _i: TRACE,
                       multi_lobby=lambda: True, clock=lambda: 1.0)
    fresh.update({"entry": "A", "conn": "a", "name": "Max"}, _sample(0.1, -20, 0))
    assert fresh.standings()["entries"][0]["correction_m"] == 0.0, "the next event starts clean"
    assert [r["action"] for r in _audit(path)][-2:] == ["reset", "correct"], "the audit log stays"


def t_a_damaged_actions_file_never_breaks_the_race():
    svc, _clock, path, _league = _ctl()
    with open(os.path.join(os.path.dirname(path), dr.ACTIONS), "w", encoding="utf-8") as fh:
        fh.write('{"corrections": {"A": [1], "B": ["x", 1], "C": [5.0, 2]}, "rejoin": "D"}')
    svc.update({"entry": "C", "conn": "c", "name": "Max"}, _sample(0.1, -20, 0))
    rows = svc.standings()["entries"]
    assert rows[0]["correction_m"] == 5.0 and rows[0]["pit_stops"] == 2 and not rows[0]["rejoin"], rows


ROSTER = {"A": {"number": "1", "bgColor": "#c00", "textColor": "#fff", "cupTeam": "Red Bulls"},
          "B": {"number": "2", "bgColor": "#00c", "textColor": "#fff", "cupTeam": ""}}


def _dash_service(ctx, path=None):
    path = path or os.path.join(tempfile.mkdtemp(), "driver-race.json")
    dr.write_settings(path, {"formation": False})
    clock = [0.0]
    svc = dr.Service(path, pick=lambda: {"id": "good"}, trace_for=lambda _i: TRACE,
                     multi_lobby=lambda: False, clock=lambda: clock[0], context=lambda: ctx)
    return svc, clock, path


def _feed(svc, clock, entry, conn, start, until, speed=50.0):
    t = clock[0]
    along = start
    while t < until:
        t = round(t + 0.1, 3)
        along += speed * 0.1
        clock[0] = t
        svc.update({"entry": entry, "conn": conn, "name": "Max " + entry},
                   _sample(t, along, max(0, int(along // L) + 1), speed))
    return along


def t_the_dashboard_payload_is_an_allowlist_with_roster_details():
    ctx = {"roster": ROSTER, "stint": "Stint 3", "remaining": "1:02:03", "mode": "race"}
    svc, clock, _path = _dash_service(ctx)
    _feed(svc, clock, "A", "a", -20.0, 3.0)
    svc.correct("rc:Anna", "A", metres=5.0, reason="secret reason")
    svc.tick()
    d = svc.dashboard()
    assert d["stint"] == "Stint 3" and d["remaining"] == "1:02:03" and d["mode"] == "race", d
    row = d["entries"][0]
    assert row["number"] == "1" and row["color"] == "#c00" and row["team"] == "Red Bulls", row
    flat = json.dumps(d)
    for leak in ("conn", "secret reason", "rc:Anna", "last_action", "correction"):
        assert leak not in flat, f"{leak} must not reach viewers"


def t_the_delay_holds_the_dashboard_back():
    ctx = {"roster": ROSTER, "stint": "Stint 1", "remaining": None, "mode": "race"}
    svc, clock, _path = _dash_service(ctx)
    svc.set_settings({"delay_s": 5})
    _feed(svc, clock, "A", "a", -20.0, 1.0)
    svc.tick()
    first = svc.dashboard()["entries"][0]["laps"]
    ctx["stint"] = "Stint 2"
    clock[0] = 3.0
    svc.tick()
    assert svc.dashboard()["stint"] == "Stint 1", "the stint switch waits for the delay too"
    clock[0] = 7.5
    svc.tick()
    assert svc.dashboard()["stint"] == "Stint 1", "a switch at 3 s shows at 8 s"
    clock[0] = 8.5
    svc.tick()
    assert svc.dashboard()["stint"] == "Stint 2" and first == 0
    assert dr.read_settings(svc._path)["delay_s"] == 5


def t_the_fastest_lap_survives_a_relay_restart_and_carries_the_team():
    ctx = {"roster": ROSTER, "stint": None, "remaining": None, "mode": "race"}
    svc, clock, path = _dash_service(ctx)
    svc._current().fastest = {"entry": "A", "driver": "Max A", "ms": 61234}
    svc.save_fastest()
    again, _clock, _p = _dash_service(ctx, path)
    again.tick()
    fl = again.dashboard()["fastest"]
    assert fl == {"entry": "A", "driver": "Max A", "team": "Red Bulls", "ms": 61234}, fl
    svc.set_fastest("rc:Anna")
    voided, _c, _p = _dash_service(ctx, path)
    voided.tick()
    assert voided.dashboard()["fastest"] is None, "Race Control can void a fastest lap"
    voided.set_fastest("rc:Anna", "A", "Max A", 61234)
    other = {"id": "other"}
    moved = dr.Service(path, pick=lambda: other, trace_for=lambda _i: TRACE,
                       multi_lobby=lambda: False, clock=lambda: 0.0, context=lambda: ctx)
    moved.tick()
    assert moved.dashboard()["fastest"] is None, "a lap on another layout is not this race's"
    dr.clear_actions(os.path.dirname(path))
    fresh, _c, _p = _dash_service(ctx, path)
    fresh.tick()
    assert fresh.dashboard()["fastest"] is None, "a new event starts without a fastest lap"


def t_qualifying_ranks_the_running_segment_by_best_lap():
    conns = [
        {"entry": "A", "segment": "Q1", "name": "Max", "latest": {"best_ms": 90500}},
        {"entry": "B", "segment": "Q1", "name": "Ann", "latest": {"best_ms": 90100}},
        {"entry": "A", "segment": "Q2", "name": "Lea", "latest": {"best_ms": 80000}},
        {"entry": "C", "segment": "Q1", "name": "Bo", "latest": {"best_ms": -1}},
    ]
    rows = dr.qualifying(conns, "Q1", ROSTER)
    assert [(r["entry"], r["best_ms"], r["gap_ms"]) for r in rows] == [
        ("B", 90100, 0), ("A", 90500, 400), ("C", None, None)], rows
    assert rows[1]["number"] == "1" and rows[1]["driver"] == "Max"
    forged = conns + [{"entry": "A", "segment": "Q1", "name": "Mallory", "latest": {"best_ms": 1}},
                      {"entry": "B", "segment": "Q1", "name": "Ghost", "live": False,
                       "latest": {"best_ms": 85000}}]
    rows = dr.qualifying(forged, "Q1", ROSTER, min_ms=30000)
    assert [(r["entry"], r["driver"], r["best_ms"]) for r in rows][:2] == [
        ("B", "Ann", 90100), ("A", "Max", 90500)], f"impossible and quiet senders are ignored: {rows}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

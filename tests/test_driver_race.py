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
                                      "delay_s": 0, "visible": False,
                                      "show_compound": True, "show_fuel": True}
    saved = dr.write_settings(path, {"formation": False, "lap_count": 12})
    assert saved == {"formation": False, "finish_mode": "laps", "lap_count": 12, "delay_s": 0,
                     "visible": False, "show_compound": True, "show_fuel": True} == dr.read_settings(path)
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
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"lap_count": "many", "visible": true, "formation": false}')
    got = dr.read_settings(path)
    assert got["visible"] is True and got["formation"] is False and got["lap_count"] is None, \
        f"one bad value falls back alone, the others stay: {got}"


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


def t_a_lap_count_entered_after_the_final_switch_reaches_the_final_lobby():
    team, clock = _service([{"id": "good"}], multi=True)
    clock[0] = 100.0
    team.stint_switch(final_laps=None, final_row=True)      # no Laps cell, no event setting
    assert team._race.lap_count is None and team._race._final_at == 100.0
    clock[0] = 160.0
    team.set_settings({"lap_count": 12})
    assert team._race.lap_count == 12, "the count entered now applies to the final lobby on air"
    assert team._race._final_at == 100.0, "the cars that took over since the switch stay final cars"
    team.set_settings({"delay_s": 5})
    assert team._race.lap_count == 12, "another setting leaves the count alone"
    team._race = None
    assert team._current().lap_count == 12, "a rebuilt race keeps it"
    early, _c = _service([{"id": "good"}], multi=True)
    early.set_settings({"lap_count": 12})
    assert early._race.lap_count is None, "before the final lobby the count waits"


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
    with open(dr.audit_path_for(path), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def t_the_league_restart_default_and_its_override_per_restart():
    svc, _clock, path, league = _ctl()
    assert dr.read_league(league)["restart"] == "keep"
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


def t_the_league_lap_time_seconds_default_to_five_and_are_checked():
    _path, league = _paths()
    assert dr.read_league(league) == {"restart": "keep", "last_lap_s": 5}
    assert dr.write_league(league, {"last_lap_s": 12}) == {"restart": "keep", "last_lap_s": 12}
    assert dr.write_league(league, {"restart": "neutralise"}) == {"restart": "neutralise", "last_lap_s": 12}, \
        "one league key changes, the other stays"
    for bad in ({"last_lap_s": 0}, {"last_lap_s": 31}, {"last_lap_s": 5.0}, {"last_lap_s": True},
                {"last_lap_s": "5"}, {"last_lap_s": None}, {}):
        try:
            dr.write_league(league, bad)
        except ValueError:
            pass  # expected
        else:
            raise AssertionError(f"{bad} must be refused")
    assert dr.read_league(league)["last_lap_s"] == 12, "a refused change writes nothing"
    with open(league, "w", encoding="utf-8") as fh:
        json.dump({"last_lap_s": 99}, fh)
    assert dr.read_league(league)["last_lap_s"] == 5, "a hand-edited value out of range reads as the default"


def t_the_dashboard_carries_the_league_lap_time_seconds_live():
    path, league = _paths()
    dr.write_settings(path, {"formation": False, "delay_s": 30})
    clock = [0.0]
    svc = dr.Service(path, pick=lambda: {"id": "good"}, trace_for=lambda _i: TRACE,
                     multi_lobby=lambda: False, clock=lambda: clock[0], league_path=league,
                     context=lambda: {"mode": "race"})
    svc.tick()
    assert svc.dashboard()["show"]["last_lap_s"] == 5, svc.dashboard()["show"]
    dr.write_league(league, {"last_lap_s": 8})
    assert svc.dashboard()["show"]["last_lap_s"] == 8, \
        "a league change reaches the tower without a relay restart and without the delay"
    assert "last_lap_s" not in json.dumps(dr.public_view(svc.dashboard())), "the public page has its Last column"


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


def t_a_consumed_rejoin_does_not_come_back_after_a_restart():
    svc, _clock, path, league = _ctl(multi=True)
    svc.rejoin("rc:Anna", "B", True)
    svc._current()._used_rejoins.append("B")                  # the team rejoined at the change
    svc.update({"entry": "A", "conn": "a", "name": "Max"}, _sample(0.1, -20, 0))
    again = dr.Service(path, pick=lambda: {"id": "good"}, trace_for=lambda _i: TRACE,
                       multi_lobby=lambda: True, clock=lambda: 5.0, league_path=league)
    again.update({"entry": "B", "conn": "b", "name": "Ann"}, _sample(0.1, -20, 0))
    rows = {r["entry"]: r for r in again.standings()["entries"]}
    assert rows["B"]["rejoin"] is False, "a used rejoin mark never lifts the team a second time"
    assert _audit(path)[-1]["action"] == "rejoin_used"


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


def t_a_race_reset_keeps_tyre_compounds_and_qualifying_corrections():
    ctx = {"roster": ROSTER, "stint": "Q1", "remaining": None, "mode": "race", "segment": "Q1"}
    svc, _clock, path = _dash_service(ctx)
    svc.set_compound("rc:Anna", "A", "RS")
    svc.set_quali("rc:Anna", "B", 90000)
    svc.correct("rc:Anna", "A", metres=10.0, reason="x")
    svc.rejoin("rc:Anna", "B", True)
    svc.set_fastest("rc:Anna", "A", "Max", 61000)
    svc.reset("rc:Anna")
    st = svc.standings()
    assert st["compounds"]["A"]["code"] == "RS" and st["quali_overrides"] == {"B": 90000}, \
        f"a race reset is no new event: tyres and qualifying stay: {st['compounds']} {st['quali_overrides']}"
    doc = dr._read_json(dr._actions_path_for(path))
    assert "corrections" not in doc and "rejoin" not in doc, doc
    assert st["fastest"] is None and not os.path.exists(svc._fastest_path()), "the fastest lap goes"
    dr.clear_actions(os.path.dirname(path))
    st = svc.standings()
    assert st["compounds"] == {} and st["quali_overrides"] == {}, "a fresh event clears everything"


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
    assert d["visible"] is False and d["track"] == "good", "the dashboard starts off air"
    svc.set_settings({"visible": True})
    svc.tick()
    assert svc.dashboard()["visible"] is True, "the director puts the dashboard on air"
    row = d["entries"][0]
    assert row["number"] == "1" and row["color"] == "#c00" and row["team"] == "Red Bulls", row
    flat = json.dumps(d)
    for leak in ("conn", "secret reason", "rc:Anna", "last_action", "correction"):
        assert leak not in flat, f"{leak} must not reach viewers"



def t_the_dashboard_says_whether_the_clock_runs_and_how_much_it_is_delayed():
    ctx = {"roster": ROSTER, "stint": "Stint 1", "remaining": 600, "remaining_running": True, "mode": "race"}
    svc, clock, _path = _dash_service(ctx)
    svc.set_settings({"delay_s": 5})
    svc.tick()
    d = svc.dashboard()
    assert d["remaining_running"] is True and d["delay_s"] == 5, d
    pub = dr.public_view(d)
    assert pub["remaining_running"] is True and pub["delay_s"] == 5, "the public page counts down and names the delay"
    ctx["remaining_running"] = False
    clock[0] = 10.0
    svc.tick()
    clock[0] = 16.0
    svc.tick()
    assert svc.dashboard()["remaining_running"] is False, "a paused timer stops the page's clock"

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


def t_the_display_switches_skip_the_delay():
    ctx = {"roster": ROSTER, "stint": "Stint 1", "remaining": None, "mode": "race"}
    svc, clock, _path = _dash_service(ctx)
    svc.set_settings({"delay_s": 60})
    _feed(svc, clock, "A", "a", -20.0, 1.0)
    svc.tick()
    clock[0] = 2.0
    svc.set_settings({"visible": True, "show_compound": False, "show_fuel": False})
    svc.tick()
    d = svc.dashboard()
    assert d["visible"] is True and d["show"] == {"compound": False, "fuel": False, "last_lap_s": 5}, \
        f"Hide/Show and the display switches apply at once, not after the delay: {d}"
    pub = dr.public_view(d)
    assert "fuel_l" in pub["entries"][0] and "compound" in pub["entries"][0], \
        f"the switches are for OBS only, the public page keeps every column: {pub}"
    assert svc._snapshots[0][1]["visible"] is False, "the delayed snapshot itself stays untouched"


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


def t_a_qualifying_time_stays_when_its_sender_goes_quiet():
    conns = [{"entry": "A", "segment": "Q1", "name": "Max", "latest": {"best_ms": 90500}},
             {"entry": "B", "segment": "Q1", "name": "Ann", "latest": {"best_ms": 90100}}]
    ctx = {"roster": ROSTER, "stint": "Q1", "remaining": None, "mode": "qualifying",
           "segment": "Q1", "connections": conns}
    svc, _clock, _path = _dash_service(ctx)
    svc.tick()
    conns[1]["live"] = False                                # Ann closed the sender
    conns[0]["latest"] = {"best_ms": -1}                    # Max's new GT7 session
    svc.tick()
    rows = svc.dashboard()["qualifying"]
    assert [(r["entry"], r["driver"], r["best_ms"]) for r in rows] == [
        ("B", "Ann", 90100), ("A", "Max", 90500)], f"a set lap stays on the tower: {rows}"
    ctx["segment"] = "Q2"
    svc.tick()
    assert svc.dashboard()["qualifying"] == [], "another segment starts its own ranking"


def t_race_control_corrects_a_qualifying_time():
    conns = [{"entry": "A", "segment": "Q1", "name": "Max", "latest": {"best_ms": 90500}},
             {"entry": "B", "segment": "Q1", "name": "Ann", "latest": {"best_ms": 90100}}]
    ctx = {"roster": ROSTER, "stint": "Q1", "remaining": None, "mode": "qualifying",
           "segment": "Q1", "connections": conns}
    svc, _clock, path = _dash_service(ctx)
    svc.set_quali("rc:Anna", "B", None)                     # B's lap deleted for track limits
    svc.tick()
    rows = svc.dashboard()["qualifying"]
    assert [(r["entry"], r["best_ms"]) for r in rows] == [("A", 90500), ("B", None)], rows
    svc.set_quali("rc:Anna", "A", 91000)
    svc.tick()
    assert svc.dashboard()["qualifying"][0]["best_ms"] == 91000, "a set time replaces GT7's"
    again, _c, _p = _dash_service(ctx, path)
    again.tick()
    assert [r["best_ms"] for r in again.dashboard()["qualifying"]] == [91000, None], "it survives a restart"
    assert again.standings()["quali_overrides"] == {"A": 91000, "B": None}
    again.set_quali("rc:Anna", "B", clear=True)
    again.tick()
    assert again.dashboard()["qualifying"][0]["entry"] == "B", "clearing gives GT7's time back"
    ctx["segment"] = "Q2"
    assert again.standings()["quali_overrides"] == {}, "a correction belongs to its segment"
    again.set_quali("rc:Anna", "B", 80000)
    ctx["segment"] = "Q1"
    assert again.standings()["quali_overrides"] == {"A": 91000}, again.standings()["quali_overrides"]
    for bad in ({"ms": 0}, {"ms": 10**8}, {"ms": "1:30"}, {"ms": True}):
        try:
            again.set_quali("rc:Anna", "A", **bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad} must be refused")
    full = {"qualifying": {"Q1": {f"E{i}": 90000 for i in range(dr.MAX_QUALI)}}}
    dr._write_json(dr._actions_path_for(path), full)
    try:
        again.set_quali("rc:Anna", "A-new", 90000)
    except ValueError:
        pass  # refused, as it must be
    else:
        raise AssertionError("a full list refuses instead of claiming success")
    dr.clear_actions(os.path.dirname(path))
    assert dr.Service(path, pick=lambda: {"id": "good"}, trace_for=lambda _i: TRACE,
                      multi_lobby=lambda: False).standings()["quali_overrides"] == {}


def t_the_crew_sets_a_tyre_compound_that_outlives_a_driver_change():
    ctx = {"roster": ROSTER, "stint": "Stint 1", "remaining": None, "mode": "race"}
    svc, clock, path = _dash_service(ctx)
    _feed(svc, clock, "A", "a", -20.0, 2.0)
    svc.set_compound("rc:Anna", "A", "RS")
    clock[0] = 2.5
    svc.set_compound("director:Ben", "A", "RM")                  # the later change wins
    svc.tick()
    row = svc.dashboard()["entries"][0]
    assert row["compound"] == "RM", row
    assert svc.standings()["compounds"]["A"]["who"] == "director:Ben", svc.standings()["compounds"]
    _feed(svc, clock, "A", "a2", 100.0, 4.0)                     # another sender drives on
    svc.tick()
    assert svc.dashboard()["entries"][0]["compound"] == "RM", "a driver change keeps the tyres"
    ctx.update(mode="qualifying", segment="Q1",
               connections=[{"entry": "A", "segment": "Q1", "name": "Max", "latest": {"best_ms": 90000}}])
    svc.tick()
    assert svc.dashboard()["qualifying"][0]["compound"] == "RM", "qualifying shows it too"
    svc.set_compound("rc:Anna", " A ", "RH")
    assert set(svc.standings()["compounds"]) == {"A"}, "entry names are stripped"
    svc.set_compound("rc:Anna", "A", None)
    svc.tick()
    assert svc.dashboard()["qualifying"][0]["compound"] is None, "no compound shows none"
    for bad in ("XX", "rs", 3):
        try:
            svc.set_compound("rc:Anna", "A", bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} is no compound")
    svc.set_settings({"show_compound": False, "show_fuel": False})
    svc.tick()
    assert svc.dashboard()["show"] == {"compound": False, "fuel": False, "last_lap_s": 5}, svc.dashboard()["show"]
    again, _c, _p = _dash_service(ctx, path)
    svc.set_compound("rc:Anna", "A", "W")
    assert again.standings()["compounds"]["A"]["code"] == "W", "it survives a restart"
    dr.clear_actions(os.path.dirname(path))
    assert again.standings()["compounds"] == {}, "the next event starts without compounds"


def t_the_public_view_is_timing_only_and_ignores_the_obs_switches():
    snap = {"v": 1, "mode": "race", "stint": "Stint 2", "remaining": 600, "track": "good",
            "track_name": "Suzuka", "visible": False, "show": {"compound": False, "fuel": True},
            "state": "racing", "red_flag": False, "lap": {"leader": 3, "count": 10},
            "fastest": {"entry": "A", "driver": "Max", "team": "", "ms": 61000},
            "entries": [{"pos": 1, "entry": "A", "number": "1", "driver": "Max", "team": "",
                         "color": "#c00", "text_color": "#fff", "laps": 2, "stint_laps": 2,
                         "gap_s": 0, "interval_s": None, "lapped": 0, "status": "racing",
                         "pit_stops": 0, "last_lap_ms": 61000, "fuel_l": 40.0, "finished": False,
                         "x": 1.0, "z": 2.0, "compound": "RS"}]}
    pub = dr.public_view(snap)
    row = pub["entries"][0]
    assert row["compound"] == "RS" and row["fuel_l"] == 40.0, "the OBS switches never hide public columns"
    assert "x" not in row and "z" not in row and "visible" not in pub and "track" not in pub, pub
    assert "show" not in pub, "the public page gets no OBS switches to follow"
    assert pub["fastest"] == {"entry": "A", "driver": "Max", "team": "", "ms": 61000}
    quali = dr.public_view({"v": 1, "mode": "qualifying", "show": {"compound": True, "fuel": True},
                            "qualifying": [{"pos": 1, "entry": "A", "number": "1", "driver": "Max",
                                            "team": "", "color": "", "text_color": "", "best_ms": 90000,
                                            "gap_ms": 0, "compound": None, "conn": "leak"}]})
    assert quali["qualifying"][0] == {"pos": 1, "entry": "A", "number": "1", "driver": "Max", "team": "",
                                      "color": "", "text_color": "", "best_ms": 90000, "gap_ms": 0,
                                      "compound": None}, quali
    assert dr.public_view(None) == {"v": 1, "mode": "race", "entries": []}


def t_viewer_rows_carry_the_brand_key_and_name():
    roster = {"A": {"number": "1", "brandKey": "bmw", "brandName": "BMW M4 GT3"},
              "B": {"number": "2", "brandKey": "../etc", "brandName": "Odd"},
              "C": {"number": "3"}}
    rows = [dr._viewer_entry({"entry": e, "driver": "Max"}, roster) for e in ("A", "B", "C", "Z")]
    assert (rows[0]["brand"], rows[0]["brand_name"]) == ("bmw", "BMW M4 GT3"), rows[0]
    assert rows[1]["brand"] == "", f"a key outside the asset-key charset never reaches a page: {rows[1]}"
    assert rows[2]["brand"] == "" and rows[2]["brand_name"] == "", rows[2]
    assert rows[3]["brand"] == "", "an entry missing from the roster has no brand"
    pub = dr.public_view({"v": 1, "mode": "race", "entries": [dict(rows[0], pos=1)]})
    assert pub["entries"][0]["brand"] == "bmw" and pub["entries"][0]["brand_name"] == "BMW M4 GT3", \
        f"the public page shows the logo too: {pub}"


def t_the_public_view_names_a_brand_only_with_a_logo():
    rows = [{"entry": "A", "brand": "bmw", "brand_name": "BMW"}, {"entry": "B", "brand": "ghost", "brand_name": "G"}]
    for key in ("entries", "qualifying"):
        view = {"v": 1, key: [dict(r) for r in rows]}
        out = dr.public_brands(view, lambda k: k == "bmw")
        assert [r["brand"] for r in out[key]] == ["bmw", ""], \
            f"a brand without a logo would make every viewer ask for it again: {out}"
        assert out[key][1]["brand_name"] == "G" and view[key][1]["brand"] == "ghost", out


def t_the_desk_standings_carry_each_entrys_brand():
    st = {"state": "racing", "entries": [{"entry": "A", "laps": 1}, {"entry": "Z", "laps": 0}]}
    out = dr.with_brands(st, {"A": {"brandKey": "bmw", "brandName": "BMW"}})
    assert [(e["brand"], e["brand_name"]) for e in out["entries"]] == [("bmw", "BMW"), ("", "")], out
    assert "brand" not in st["entries"][0], "the standings themselves stay untouched"


def t_public_view_has_no_exact_gaps():
    snap = {"v": 1, "mode": "race", "show": {"compound": True, "fuel": True},
            "entries": [{"entry": "A", "gap_s": 1.0, "gap_time_s": 61.0, "interval_time_s": 61.0,
                         "quiet_s": 0.2, "in_box": False}]}
    row = dr.public_view(snap)["entries"][0]
    assert row["gap_s"] == 1.0 and not {"gap_time_s", "interval_time_s", "quiet_s", "in_box"} & set(row), row


def _recording_service(enabled=lambda: True, context=None):
    d = tempfile.mkdtemp()
    wall = [1000.0]
    svc = dr.Service(os.path.join(d, "driver-race.json"), pick=lambda: {"id": "good"},
                     trace_for=lambda i: TRACE, multi_lobby=lambda: False, clock=lambda: wall[0],
                     enabled=enabled, wall=lambda: wall[0], recording_dir=d, context=context)
    svc.update({"entry": "A", "conn": "a", "name": "Max"}, _sample(0.1, -5, 1))
    svc.open_recording()
    return svc, wall, d


def _flush(svc):
    svc._recorder()._writer.flush()


def t_tick_hands_the_viewer_classes_to_the_recorder():
    svc, _wall, _d = _recording_service()
    seen = []
    svc._viewer = lambda ctx: {"classes": [{"name": "GT3"}], "entries": []}
    svc._observe = lambda standings, classes=None: seen.append(classes)
    svc.tick()
    assert seen == [[{"name": "GT3"}]], seen


def t_tick_records_in_race_mode_only():
    mode = ["race"]
    svc, wall, _d = _recording_service(enabled=lambda: mode[0] == "race")
    svc.recording_start("pit")
    svc.tick()
    _flush(svc)
    assert svc.recording_status()["span"] is not None, "race mode: tick records a sample"
    mode[0] = "qualifying"
    wall[0] += 5
    svc.tick()
    _flush(svc)
    span = svc.recording_status()["span"]
    assert span[0] == span[1] == 1000.0, f"qualifying mode records nothing: {span}"
    assert svc.recording_at(1000.0)["rows"][0]["entry"] == "A"
    assert svc.recording_csv()[0][0] == "time_utc"



def t_a_confirmed_disconnect_reads_dc_on_the_dashboard_until_data_returns():
    svc, wall, _d = _recording_service()
    svc.recording_start("pit")
    rec = svc._recorder()
    rec._events[1] = {"id": 1, "entry": "A", "state": "suspected"}
    rec._silent["A"] = 1
    svc.tick()
    assert svc.dashboard()["entries"][0]["status"] == "no_data", "a suspicion alone is still No data"
    svc.recording_decide("Cy", 1, "confirm")
    wall[0] += 1
    svc.tick()
    assert svc.dashboard()["entries"][0]["status"] == "dc", svc.dashboard()["entries"][0]
    assert dr.public_view(svc.dashboard())["entries"][0]["status"] == "dc", "the public page names it too"
    assert svc.standings()["entries"][0]["status"] == "dc", "the desks name it too"
    _flush(svc)
    assert svc.recording_at(wall[0])["rows"][0]["status"] == "dc", "the recording keeps DC for the snapshot and CSV"
    del rec._silent["A"]
    wall[0] += 1
    svc.tick()
    assert svc.dashboard()["entries"][0]["status"] != "dc", "a returned car is no longer a DC"

def _two_cars(svc, wall, until, quiet_b=False):
    """C ahead of B at 50 m/s until `until`; B sends nothing with quiet_b."""
    while wall[0] < until:
        wall[0] = round(wall[0] + 0.1, 3)
        t = wall[0] - 1000.0
        for entry, along in (("C", 50 * t - 25), ("B", 50 * t - 125)):
            if entry == "B" and quiet_b:
                continue
            svc.update({"entry": entry, "conn": entry.lower(), "name": "Max " + entry},
                       _sample(wall[0], along, max(0, int(along // L) + 1)))


def t_an_estimated_car_reads_est_everywhere_and_is_audited():
    svc, wall, d = _recording_service()
    svc.set_settings({"formation": False})
    svc.recording_start("pit")
    _two_cars(svc, wall, 1010.0)
    _two_cars(svc, wall, 1010.0 + dr.race_distance.NO_DATA_S + 1, quiet_b=True)
    rec = svc._recorder()
    rec._events[9] = {"id": 9, "entry": "B", "state": "confirmed"}
    rec._silent["B"] = 9
    race = svc.estimate("rc:Cy", "B", "C", 2.0)
    row = race["entries"][1]
    assert row["entry"] == "B" and row["status"] == "est" and row["gap_s"] == 2.0, row
    assert row["estimate"] == {"ref": "C", "seconds": 2.0}, "the desks see the estimate"
    svc.tick()
    dash = svc.dashboard()["entries"][1]
    assert dash["status"] == "est" and dash["gap_s"] == 2.0, f"EST wins over DC on the tower: {dash}"
    assert "estimate" not in dash, "viewers never see the reference"
    assert dr.public_view(svc.dashboard())["entries"][1]["status"] == "est"
    _flush(svc)
    sample = svc.recording_at(wall[0])["rows"][1]
    assert sample["status"] == "est" and sample["gap_time_s"] is not None, sample
    svc.estimate("rc:Cy", "B", clear=True)
    assert svc.standings()["entries"][1]["status"] == "dc", "a cleared estimate shows the DC again"
    svc.estimate("director:Bo", "B", "C", 1.5)
    del rec._silent["B"]
    _two_cars(svc, wall, wall[0] + 1)
    assert svc.standings()["entries"][1]["estimate"] is None, "returning data ends the estimate"
    got = [(r["who"], r["action"], r.get("entry"), r.get("ref"), r.get("seconds"), r.get("cleared"))
           for r in _audit(os.path.join(d, "driver-race.json")) if r["action"].startswith("estimate")]
    assert got == [("rc:Cy", "estimate", "B", "C", 2.0, None), ("rc:Cy", "estimate", "B", None, None, True),
                   ("director:Bo", "estimate", "B", "C", 1.5, None),
                   ("race", "estimate_end", "B", None, None, None)], got


def t_an_estimate_validates_its_input():
    svc, clock, path, _league = _ctl()
    svc.update({"entry": "A", "conn": "a", "name": "Max"}, _sample(0.1, -5, 1))
    svc.update({"entry": "B", "conn": "b", "name": "Ann"}, _sample(0.1, -50, 0))
    for call in (lambda: svc.estimate("rc:A", "B", "A", -1),
                 lambda: svc.estimate("rc:A", "B", "A", float("inf")),
                 lambda: svc.estimate("rc:A", "B", "A", float("nan")),
                 lambda: svc.estimate("rc:A", "B", "A", True),
                 lambda: svc.estimate("rc:A", "B", "A", "5"),
                 lambda: svc.estimate("rc:A", "B", "A", dr.MAX_ESTIMATE_S + 1),
                 lambda: svc.estimate("rc:A", "B", "B", 1.0),
                 lambda: svc.estimate("rc:A", "B", "", 1.0),
                 lambda: svc.estimate("rc:A", "Nobody", "A", 1.0),
                 lambda: svc.estimate("rc:A", "B", clear=True)):
        try:
            call()
        except ValueError:
            pass  # expected
        else:
            raise AssertionError("an invalid estimate must be refused")
    assert not os.path.exists(dr.audit_path_for(path)), "a refused estimate logs nothing"


def t_recording_start_is_audited():
    svc, _wall, d = _recording_service()
    svc.recording_start("Anna")
    svc.recording_stop("Ben")
    svc._recorder()._events[1] = {"id": 1, "entry": "A", "state": "suspected"}
    svc._recorder()._store.add_event({"id": 1, "entry": "A", "state": "suspected"})
    svc.recording_decide("Cy", "1", "confirm")
    svc.recording_decide("Cy", 1, "dismiss")
    try:
        svc.recording_decide("Cy", "x", "confirm")
        raise AssertionError("a bad id must raise")
    except ValueError:
        pass
    with open(dr.audit_path_for(os.path.join(d, "driver-race.json")), encoding="utf-8") as fh:
        recs = [json.loads(line) for line in fh]
    got = [(r["who"], r["action"]) for r in recs]
    assert got == [("Anna", "recording_start"), ("Ben", "recording_stop"),
                   ("Cy", "dc_confirm"), ("Cy", "dc_dismiss")], got


def t_the_recording_carries_the_compound_code():
    svc, wall, _d = _recording_service()
    svc.set_compound("pit", "A", "RS")
    svc.recording_start("pit")
    svc.tick()
    _flush(svc)
    assert svc.recording_at(1000.0)["rows"][0]["compound"] == "RS", svc.recording_at(1000.0)


def t_voiding_the_fastest_lap_removes_it_from_its_class():
    ctx = {"roster": ROSTER, "stint": None, "remaining": None, "mode": "race"}
    svc, _clock, _path = _dash_service(ctx)
    race = svc._current()
    race.fastest = {"entry": "B", "driver": "Max B", "ms": 61000}
    race.fastest_by_class = {"G": dict(race.fastest), "H": {"entry": "A", "driver": "Max A", "ms": 62000}}
    svc.set_fastest("rc:Anna")
    assert svc._current().fastest_by_class == {"H": {"entry": "A", "driver": "Max A", "ms": 62000}}, \
        f"the voided lap leaves its class, the other class keeps its lap: {svc._current().fastest_by_class}"


def t_a_failing_recording_does_not_stop_the_fastest_lap_save():
    d = tempfile.mkdtemp()
    blocker = os.path.join(d, "file")
    open(blocker, "w").close()
    wall = [1000.0]
    svc = dr.Service(os.path.join(d, "driver-race.json"), pick=lambda: {"id": "good"},
                     trace_for=lambda i: TRACE, multi_lobby=lambda: False, clock=lambda: wall[0],
                     wall=lambda: wall[0], recording_dir=os.path.join(blocker, "sub"))
    svc.open_recording()
    svc.set_fastest("pit", "A", "Max", 90000)
    svc.tick()
    assert os.path.exists(svc._fastest_path()), "the fastest lap is saved although recording failed"
    opened = []
    real = dr.race_recording.open_current
    dr.race_recording.open_current = lambda *a: opened.append(1) or real(*a)
    try:
        svc.tick()
        assert not opened, "a failed open is not retried within the backoff"
        wall[0] += dr.RECORDING_RETRY_S + 1
        svc.tick()
        assert opened, "it retries after the backoff"
    finally:
        dr.race_recording.open_current = real


def t_recording_without_a_directory_and_unknown_events_raise():
    svc = dr.Service(os.path.join(tempfile.mkdtemp(), "driver-race.json"), pick=lambda: None,
                     trace_for=lambda i: None, multi_lobby=lambda: False)
    try:
        svc.recording_status()
        raise AssertionError("no recording_dir must raise")
    except RuntimeError as e:
        assert "unavailable" in str(e)
    svc, _wall, _d = _recording_service()
    try:
        svc.recording_decide("Cy", "99", "confirm")
        raise AssertionError("an unknown id must raise")
    except KeyError:
        pass  # the route maps this to 404


def t_a_qualifying_relay_neither_records_nor_creates_a_file():
    d = tempfile.mkdtemp()
    svc = dr.Service(os.path.join(d, "driver-race.json"), pick=lambda: {"id": "good"},
                     trace_for=lambda i: TRACE, multi_lobby=lambda: False,
                     enabled=lambda: False, recording_dir=d)
    st = svc.recording_status()
    assert st["running"] is False and st["available"] is True, st
    assert st["span"] is None and st["events"] == [], st
    try:
        svc.recording_start("pit")
        raise AssertionError("a manual start in qualifying must be refused")
    except dr.NotRaceMode:
        pass
    svc.tick()
    assert not [f for f in os.listdir(d) if f.startswith("race-recording")], os.listdir(d)


def t_nothing_opens_a_recording_before_open_recording():
    d = tempfile.mkdtemp()
    wall = [1000.0]
    svc = dr.Service(os.path.join(d, "driver-race.json"), pick=lambda: {"id": "good"},
                     trace_for=lambda i: TRACE, multi_lobby=lambda: False, clock=lambda: wall[0],
                     wall=lambda: wall[0], recording_dir=d)
    svc.update({"entry": "A", "conn": "a", "name": "Max"}, _sample(0.1, -5, 1))

    def files():
        return [f for f in os.listdir(d) if f.startswith("race-recording")]
    svc.tick()
    st = svc.recording_status()
    assert st["running"] is False and st["span"] is None, st
    for act in (lambda: svc.recording_start("pit"), lambda: svc.recording_stop("pit")):
        try:
            act()
        except RuntimeError:
            pass  # the route answers 503 until the relay owns its port
    assert files() == [], f"a relay that may still lose its port touches no recording: {files()}"
    svc.open_recording()
    assert "race-recording.json" in files() and len(files()) > 1, files()
    q = dr.Service(os.path.join(d, "q.json"), pick=lambda: None, trace_for=lambda i: None,
                   multi_lobby=lambda: False, enabled=lambda: False, recording_dir=tempfile.mkdtemp())
    q.open_recording()
    assert q._rec is None, "qualifying opens nothing"


def _failing_service():
    d = tempfile.mkdtemp()
    blocker = os.path.join(d, "file")
    open(blocker, "w").close()
    wall = [1000.0]
    svc = dr.Service(os.path.join(d, "driver-race.json"), pick=lambda: {"id": "good"},
                     trace_for=lambda i: TRACE, multi_lobby=lambda: False, clock=lambda: wall[0],
                     wall=lambda: wall[0], recording_dir=os.path.join(blocker, "sub"))
    svc.open_recording()
    return svc


def t_store_errors_turn_into_recording_unavailable():
    svc = _failing_service()
    for call in (svc.recording_status, lambda: svc.recording_start("pit"),
                 lambda: svc.recording_at(1.0), svc.recording_csv):
        try:
            call()
            raise AssertionError("an unopenable recording must raise RuntimeError")
        except RuntimeError as e:
            assert "unavailable" in str(e), e
    svc, _wall, _d = _recording_service()
    rec = svc._recorder()

    class _Broken:
        def sample_at(self, t):
            raise dr.sqlite3.OperationalError("disk I/O error")

        def csv_rows(self):
            yield ["time_utc"]
            raise dr.sqlite3.OperationalError("disk I/O error")
    rec._store = _Broken()
    for call in (lambda: svc.recording_at(1.0), svc.recording_csv):
        try:
            call()
            raise AssertionError("a store error must raise RuntimeError")
        except RuntimeError as e:
            assert "unavailable" in str(e), e


def t_recording_decide_rejects_bad_types():
    svc, _wall, _d = _recording_service()
    for event_id, decision in ((1, []), (1, None), (1.5, "confirm"), (float("inf"), "confirm"),
                               (float("nan"), "confirm"), ([1], "confirm")):
        try:
            svc.recording_decide("Cy", event_id, decision)
            raise AssertionError(f"{event_id!r}/{decision!r} accepted")
        except ValueError:
            pass  # the route maps this to 400
    try:
        svc.recording_decide("Cy", 1.0, "confirm")
        raise AssertionError("an unknown integral float id must reach the lookup")
    except KeyError:
        pass


def t_any_recorder_error_keeps_the_fastest_lap_save():
    svc, wall, _d = _recording_service()

    class _Boom:
        def observe(self, *a):
            raise TypeError("corrupt meta")
    svc._rec = _Boom()
    svc.set_fastest("pit", "A", "Max", 90000)
    svc.tick()
    assert os.path.exists(svc._fastest_path()), "a recorder bug never skips save_fastest"


def t_a_recording_failure_reaches_the_relay_log():
    import logging
    seen = []

    class _H(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())
    log = logging.getLogger("racecast.relay")
    h = _H()
    log.addHandler(h)
    try:
        _failing_service()
    finally:
        log.removeHandler(h)
    assert any("race recording unavailable" in m for m in seen), seen


ROSTER_X = dict(ROSTER, X={"number": "9", "bgColor": "#999", "textColor": "#000", "cupTeam": "Grey Team"})


def _roster_service():
    ctx = {"roster": ROSTER_X, "stint": None, "remaining": None, "mode": "race"}
    svc, clock, path = _dash_service(ctx)
    _feed(svc, clock, "A", "a", -20.0, 6.0)
    _feed(svc, clock, "B", "b", -60.0, 6.0)
    return svc, clock, path, ctx


def _rows(st):
    return {r["entry"]: r for r in st["entries"]}


def t_every_roster_entry_stands_in_the_payload_without_a_sender():
    svc, clock, _path, _ctx = _roster_service()
    svc.tick()
    rows = svc.dashboard()["entries"]
    x = _rows(svc.standings())["X"]
    assert x["status"] == "no_sender" and x["gap_s"] is None and x["driver"] == "", x
    assert [r["entry"] for r in rows][-1] == "X" and rows[-1]["status"] == "no_sender", rows
    assert rows[-1]["pos"] == 3 and rows[-1]["number"] == "9" and rows[-1]["x"] is None, rows[-1]
    pub = dr.public_view(svc.dashboard())["entries"][-1]
    assert pub["status"] == "no_sender" and set(pub) <= set(dr._PUBLIC_ROW), pub


def t_an_estimate_places_a_roster_entry_that_never_sent():
    svc, clock, path, _ctx = _roster_service()
    st = svc.estimate("rc:Cy", "X", "A", 1.0)
    x = _rows(st)["X"]
    assert x["status"] == "est" and x["estimate"] == {"ref": "A", "seconds": 1.0}, x


def t_race_control_overrides_driver_names():
    svc, clock, path, ctx = _roster_service()
    st = svc.set_driver_name("rc:Anna", "X", "  Kim Berg ")
    assert _rows(st)["X"]["driver"] == "Kim Berg", _rows(st)["X"]
    st = svc.set_driver_name("director:Bo", "A", "Max H", driver="Max A")
    assert _rows(st)["A"]["driver"] == "Max H" and _rows(st)["A"]["driver_registered"] == "Max A", _rows(st)["A"]
    assert st["driver_overrides"] == [{"entry": "A", "driver": "Max A", "name": "Max H"},
                                      {"entry": "X", "driver": "", "name": "Kim Berg"}], st["driver_overrides"]
    svc._current().fastest = {"entry": "A", "driver": "Max A", "ms": 61000}
    assert svc.standings()["fastest"]["driver"] == "Max H", "the desks' fastest lap shows the override"
    svc.tick()
    d = svc.dashboard()
    assert _rows(d)["X"]["driver"] == "Kim Berg" and _rows(d)["A"]["driver"] == "Max H", d["entries"]
    assert d["fastest"]["driver"] == "Max H" and dr.public_view(d)["fastest"]["driver"] == "Max H", d["fastest"]
    assert "driver_registered" not in _rows(d)["A"], "viewers never see the registered name"
    rec = _audit(path)[-1]
    assert (rec["who"], rec["action"], rec["entry"], rec["driver"], rec["name"]) == \
        ("director:Bo", "driver_name", "A", "Max A", "Max H"), rec
    again, _c, _p = _dash_service(ctx, path=path)
    _feed(again, _c, "A", "a", -20.0, 2.0)
    assert _rows(again.standings())["A"]["driver"] == "Max H", "the override survives a relay restart"
    again.reset("rc:Anna")
    assert _rows(again.standings())["X"]["driver"] == "Kim Berg", "a race reset keeps the overrides"
    st = svc.set_driver_name("rc:Anna", "A", "", driver="Max A")
    assert _rows(st)["A"]["driver"] == "Max A" and len(st["driver_overrides"]) == 1, "an empty name removes it"
    dr.clear_actions(os.path.dirname(path))
    fresh, _c, _p = _dash_service(ctx, path=path)
    assert _rows(fresh.standings())["X"]["driver"] == "", "a fresh event starts without overrides"


def t_each_driver_of_an_entry_keeps_their_own_override():
    svc, clock, path, _ctx = _roster_service()
    svc.set_driver_name("rc:Anna", "A", "Max H", driver="Max A")
    svc.set_driver_name("rc:Anna", "A", "Old Name", driver="Alexander Fitzgerald-Smith")
    st = svc.set_driver_name("rc:Anna", "A", "Lea W", driver="Lea Weber")
    assert [o["driver"] for o in st["driver_overrides"]] == ["Alexander Fitzgerald-Smith", "Lea Weber", "Max A"], \
        f"a driver registered with up to 32 characters can be renamed: {st['driver_overrides']}"
    assert _rows(st)["A"]["driver"] == "Max H", "the driver in the car keeps their override"


def t_a_desk_name_follows_the_sender_name_rule():
    svc, clock, path, _ctx = _roster_service()
    bad = (None, 5, "x\ud800", "Ann\u202eBerg", "Ann\u200bBerg", "Ann\nBerg", "x" * 25, "Ann;rm",
           "Ana Ru\u00edz")
    for name in bad:
        try:
            svc.set_driver_name("rc:Anna", "X", name)
        except ValueError:
            continue
        raise AssertionError(f"{name!r} must be refused")
    for driver in ("x\ud800", "a\nb", 5):
        try:
            svc.set_driver_name("rc:Anna", "A", "Ann", driver=driver)
        except ValueError:
            continue
        raise AssertionError(f"registered name {driver!r} must be refused")
    for name in ("x\ud800", "Ann\u202e", "a\nb", "x" * 25):
        try:
            svc.set_fastest("rc:Anna", "A", name, 61000)
        except ValueError:
            continue
        raise AssertionError(f"fastest lap driver {name!r} must be refused")
    assert not os.path.exists(dr.audit_path_for(path)), "a refused name logs nothing"
    assert dr._saved_overrides(dr._actions_path_for(path)) == []
    json.dumps(svc.standings(), ensure_ascii=False).encode("utf-8")


def t_a_bad_name_in_the_actions_file_is_dropped():
    svc, clock, path, ctx = _roster_service()
    with open(dr._actions_path_for(path), "w", encoding="utf-8") as fh:
        json.dump({"driver_names": [["X", "", "Kim Berg"], ["C", "x" * 32, "Alexander Fitzgerald-Smith"],
                                    ["D", "", "x" * 33], ["B", "", "x\ud800"], ["A", "a\nb", "Ann"],
                                    ["A", "Max A", "Ann\u202e"], "junk", ["A"]]}, fh)
    fresh, _c, _p = _dash_service(ctx, path=path)
    st = fresh.standings()
    assert st["driver_overrides"] == [{"entry": "C", "driver": "x" * 32, "name": "Alexander Fitzgerald-Smith"},
                                      {"entry": "X", "driver": "", "name": "Kim Berg"}], \
        f"names saved under the old 32 character rule stay: {st['driver_overrides']}"
    json.dumps(st, ensure_ascii=False).encode("utf-8")



def t_an_entry_with_a_lone_surrogate_is_refused_by_every_action():
    svc, clock, path, _ctx = _roster_service()
    for act in (lambda: svc.retire("rc:Anna", "A\ud800", True),
                lambda: svc.set_compound("rc:Anna", "A\ud800", "RM")):
        try:
            act()
            raise AssertionError("an entry that cannot be written as UTF-8 must be refused")
        except ValueError:
            pass  # expected
    doc = dr._read_json(dr._actions_path_for(path))
    assert not doc.get("retired") and not doc.get("compounds"), f"nothing is stored: {doc}"
    json.dumps(svc.standings(), ensure_ascii=False).encode("utf-8")


def t_a_fastest_lap_with_a_bad_driver_name_is_not_loaded():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "fastest.json")
        for driver in ("M\ud800", "Ann\u202e"):
            with open(f, "w", encoding="utf-8") as fh:
                json.dump({"track": "t", "entry": "A", "driver": driver, "ms": 90000}, fh)
            assert dr._saved_fastest(f, "t") is None, f"a saved fastest lap with {driver!r} is dropped"
        with open(f, "w", encoding="utf-8") as fh:
            json.dump({"track": "t", "entry": "A", "driver": "x" * 32, "ms": 90000}, fh)
        assert dr._saved_fastest(f, "t") is not None, "an old 32 character name still loads"

def t_the_audit_is_written_before_the_name_changes():
    svc, clock, path, _ctx = _roster_service()
    svc._log = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("audit failed"))
    try:
        svc.set_driver_name("rc:Anna", "X", "Kim Berg")
    except RuntimeError:
        pass  # expected
    assert dr._saved_overrides(dr._actions_path_for(path)) == [], "a failed audit leaves no name behind"
    assert _rows(svc.standings())["X"]["driver"] == ""


def t_a_retirement_is_audited_persisted_and_can_be_undone():
    svc, clock, path, ctx = _roster_service()
    st = svc.retire("director:Bo", "A", True)
    assert [r["entry"] for r in st["entries"]] == ["B", "X", "A"] and _rows(st)["A"]["status"] == "retired", st
    rec = _audit(path)[-1]
    assert (rec["who"], rec["action"], rec["entry"], rec["on"]) == ("director:Bo", "retire", "A", True), rec
    svc.tick()
    assert svc.dashboard()["entries"][-1]["status"] == "retired"
    assert dr.public_view(svc.dashboard())["entries"][-1]["status"] == "retired"
    again, _c, _p = _dash_service(ctx, path=path)
    assert _rows(again.standings())["A"]["status"] == "retired", "a retirement survives a relay restart"
    st = again.retire("rc:Anna", "A", False)
    assert _rows(st)["A"]["status"] != "retired" and _audit(path)[-1]["on"] is False
    again.retire("rc:Anna", "B", True)
    again.reset("rc:Anna")
    assert _rows(again.standings())["B"]["status"] != "retired", "a race reset drops retirements"
    again.retire("rc:Anna", "B", True)
    dr.clear_actions(os.path.dirname(path))
    fresh, _c, _p = _dash_service(ctx, path=path)
    assert _rows(fresh.standings())["B"]["status"] != "retired", "a fresh event drops retirements"
    for bad in (("A", "yes"), ("", True)):
        try:
            svc.retire("rc:Anna", *bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad} must be refused")


def t_a_retired_car_raises_no_disconnect_and_records_as_retired():
    ctx = {"roster": {"C": {}, "B": {}}, "mode": "race"}
    svc, wall, d = _recording_service(context=lambda: ctx)
    svc.set_settings({"formation": False})
    svc.recording_start("pit")
    _two_cars(svc, wall, 1010.0)
    svc.retire("rc:Cy", "B", True)
    _two_cars(svc, wall, 1010.0 + dr.race_distance.NO_DATA_S + 2, quiet_b=True)
    svc.tick()
    _flush(svc)
    assert [e["entry"] for e in svc.recording_status()["events"]] == [], \
        f"a retired car raises no disconnect: {svc.recording_status()['events']}"
    sample = {r["entry"]: r for r in svc.recording_at(wall[0])["rows"]}
    assert sample["B"]["status"] == "retired", sample


def t_a_reference_retirement_ends_the_estimate_in_the_audit():
    svc, clock, path, _ctx = _roster_service()
    svc.estimate("rc:Cy", "X", "A", 1.0)
    st = svc.retire("rc:Anna", "A", True)
    assert _rows(st)["X"]["status"] == "no_sender" and _rows(st)["X"]["estimate"] is None, _rows(st)["X"]
    ends = [r for r in _audit(path) if r["action"] == "estimate_end"]
    assert [(r["who"], r["entry"], r["reason"]) for r in ends] == [("rc:Anna", "X", "reference_retired")], ends



CLASS_ROSTER = {"A": {"number": "1", "cupTeam": "Red Bulls", "carClass": "H", "classColor": "#c00"},
                "B": {"number": "2", "carClass": "G", "classColor": "#0c0"},
                "C": {"number": "3", "carClass": "G", "classColor": ""}}


def _class_service(path=None, along=None, until=6.0):
    ctx = {"roster": {k: dict(v) for k, v in CLASS_ROSTER.items()}, "stint": None, "remaining": None,
           "mode": "race"}
    svc, clock, path = _dash_service(ctx, path)
    along = along or {"A": -20.0, "B": -60.0, "C": -100.0}
    while clock[0] < until:
        clock[0] = round(clock[0] + 0.1, 3)
        for entry in along:
            along[entry] += 5.0
            svc.update({"entry": entry, "conn": entry.lower(), "name": "Max " + entry},
                       _sample(clock[0], along[entry], max(0, int(along[entry] // L) + 1)))
    return svc, clock, path, ctx


def t_the_desk_standings_carry_the_class_colours():
    svc, _clock, _path, ctx = _class_service()
    st = svc.standings()
    assert st["classes"] == ["H", "G"], st["classes"]
    assert st.get("class_colors") == {"H": "#c00", "G": "#0c0"}, \
        f"each class in the colour the viewers see: {st.get('class_colors')}"
    for info in ctx["roster"].values():
        info["carClass"] = "H"
    st = svc.standings()
    assert st["classes"] == [] and "class_colors" not in st, "one class: the desk payload as before"


def t_the_dashboard_carries_classes_in_order():
    svc, _clock, _path, _ctx = _class_service()
    svc._current().fastest_by_class = {"G": {"entry": "C", "driver": "Max C", "ms": 63000}}
    svc.set_settings({"finish_mode": "laps", "lap_count": 10})
    svc.tick()
    d = svc.dashboard()
    assert [c["name"] for c in d["classes"]] == ["H", "G"], d["classes"]
    assert [c["color"] for c in d["classes"]] == ["#c00", "#0c0"], \
        f"a class takes the first colour set for it: {d['classes']}"
    rows = _rows(d)
    assert [(rows[e]["class"], rows[e]["class_pos"]) for e in "ABC"] == [("H", 1), ("G", 1), ("G", 2)], rows
    assert rows["C"]["class_color"] == "#0c0", f"a row shows its class colour: {rows['C']}"
    assert rows["C"]["class_gap_s"] is not None and rows["B"]["class_gap_s"] == 0, rows
    g = d["classes"][1]
    assert g["laps"] is None, g
    assert d["classes"][0]["laps"] == 10, f"the leader's class runs the race lap count: {d['classes'][0]}"
    assert g["fastest"] == {"entry": "C", "driver": "Max C", "team": "", "ms": 63000}, g
    assert d["classes"][0]["fastest"] is None, d["classes"][0]


def t_a_class_header_lap_follows_the_overall_lap_rule():
    svc, _clock, _path, _ctx = _class_service(
        along={"A": -20.0, "B": -20.0 - 0.9 * L, "C": -40.0 - 0.9 * L}, until=70.0)
    svc.set_settings({"finish_mode": "laps", "lap_count": 10})
    svc.tick()
    d = svc.dashboard()
    rows = _rows(d)
    assert rows["A"]["laps"] == rows["B"]["laps"] + 1, rows
    h, g = d["classes"]
    assert h["lap"] == d["lap"]["leader"], f"the leader's class reads the overall LAP: {h} {d['lap']}"
    assert g["lap"] == d["lap"]["leader"] - 1, f"a class leader one lap down reads one less: {g} {d['lap']}"


def t_a_class_change_in_the_roster_reaches_the_next_tick():
    svc, _clock, _path, ctx = _class_service()
    svc.tick()
    ctx["roster"]["B"]["carClass"] = "H"
    svc.tick()
    rows = _rows(svc._snapshots[-1][1])
    assert (rows["B"]["class"], rows["B"]["class_pos"]) == ("H", 2), rows["B"]
    assert (rows["C"]["class"], rows["C"]["class_pos"]) == ("G", 1), rows["C"]


def t_a_rebuilt_race_knows_the_classes_before_its_first_lap():
    svc, _clock, _path, _ctx = _class_service()
    svc.tick()
    svc._race = None
    svc.update({"entry": "A", "conn": "a", "name": "Max A"}, _sample(9.0, 10.0, 1))
    assert svc._current()._classes == {"A": "H", "B": "G", "C": "G"}, \
        f"a lap timed before the next tick is filed under its class: {svc._current()._classes}"


def t_the_public_view_keeps_the_class_fields():
    svc, _clock, _path, _ctx = _class_service()
    svc._current().fastest_by_class = {"H": {"entry": "A", "driver": "Max A", "ms": 61000}}
    svc.tick()
    d = svc.dashboard()
    d["classes"][0]["secret"] = "x"
    d["classes"][0]["fastest"]["conn"] = "leak"
    d["entries"][0]["class_secret"] = "x"
    pub = dr.public_view(d)
    for key in ("class", "class_color", "class_pos", "class_gap_s", "class_interval_s", "class_lapped"):
        assert key in pub["entries"][0], f"{key} reaches the public page: {pub['entries'][0]}"
    assert "class_secret" not in pub["entries"][0], pub["entries"][0]
    assert pub["classes"][0] == {"name": "H", "color": "#c00", "lap": d["classes"][0]["lap"],
                                 "laps": d["classes"][0]["laps"],
                                 "fastest": {"entry": "A", "driver": "Max A", "team": "Red Bulls",
                                             "ms": 61000}}, pub["classes"][0]
    assert pub["classes"][1]["fastest"] is None, pub["classes"][1]


def t_a_single_class_payload_is_unchanged():
    svc, _clock, _path, ctx = _class_service()
    for info in ctx["roster"].values():
        info["carClass"] = "G"
    ctx["roster"]["C"]["carClass"] = ""
    svc.tick()
    d = svc.dashboard()
    assert d["classes"] == [], d["classes"]
    assert all(r.get("class_pos") is None and r.get("class_color") is None for r in d["entries"]), d["entries"]
    plain, plain_clock, _p = _dash_service({"roster": ROSTER, "stint": None, "remaining": None, "mode": "race"})
    _feed(plain, plain_clock, "A", "a", -20.0, 6.0)
    plain.tick()
    assert set(d["entries"][0]) == set(plain.dashboard()["entries"][0]), \
        "one class adds no row fields to the payload"
    pub = dr.public_view(d)
    assert "classes" not in pub and not any(k.startswith("class") for r in pub["entries"] for k in r), pub


def t_class_fastest_laps_survive_a_restart():
    svc, _clock, path, ctx = _class_service()
    race = svc._current()
    race.fastest = {"entry": "A", "driver": "Max A", "ms": 61000}
    race.fastest_by_class = {"H": dict(race.fastest), "G": {"entry": "C", "driver": "Max C", "ms": 63000}}
    svc.save_fastest()
    race.fastest_by_class["G"] = {"entry": "B", "driver": "Max B", "ms": 62500}
    svc.save_fastest()
    again, _c, _p = _dash_service(ctx, path)
    assert again._current().fastest_by_class == {
        "H": {"entry": "A", "driver": "Max A", "ms": 61000},
        "G": {"entry": "B", "driver": "Max B", "ms": 62500}}, \
        f"a new class lap is saved even when the overall lap stays: {again._current().fastest_by_class}"
    again.set_fastest("rc:Anna")
    voided, _c, _p = _dash_service(ctx, path)
    assert voided._current().fastest is None and voided._current().fastest_by_class == {
        "G": {"entry": "B", "driver": "Max B", "ms": 62500}}, \
        f"voiding the overall lap keeps the other class laps: {voided._current().fastest_by_class}"
    moved = dr.Service(path, pick=lambda: {"id": "other"}, trace_for=lambda _i: TRACE,
                       multi_lobby=lambda: False, clock=lambda: 0.0, context=lambda: ctx)
    assert moved._current().fastest_by_class == {}, "class laps on another layout are not this race's"


def t_a_damaged_class_lap_is_not_loaded():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "fastest.json")
        by_class = {"G": {"entry": "B", "driver": "Max B", "ms": 62000},
                    "H": {"entry": "A", "driver": "Ann\u202e", "ms": 61000},
                    "x" * 25: {"entry": "C", "driver": "Max C", "ms": 61000},
                    "": {"entry": "C", "driver": "Max C", "ms": -1}}
        with open(f, "w", encoding="utf-8") as fh:
            json.dump({"track": "t", "by_class": by_class}, fh)
        assert dr._saved_fastest_by_class(f, "t") == {"G": {"entry": "B", "driver": "Max B", "ms": 62000}}, \
            "only valid class laps load, without an overall lap"
        assert dr._saved_fastest_by_class(f, "u") == {}, "another layout loads none"


def t_a_hand_set_fastest_lap_moves_between_classes():
    svc, _clock, path, ctx = _class_service()
    svc.tick()
    race = svc._current()
    race.fastest = {"entry": "B", "driver": "Max B", "ms": 60000}
    race.fastest_by_class = {"G": dict(race.fastest), "H": {"entry": "A", "driver": "Max A", "ms": 62000}}
    svc.set_fastest("rc:Anna", "A", "Max A", 61000)
    assert svc._current().fastest_by_class == {"H": {"entry": "A", "driver": "Max A", "ms": 61000}}, \
        f"the replaced lap leaves its class, the set lap is its class's: {svc._current().fastest_by_class}"
    again, _c, _p = _dash_service(ctx, path)
    assert again._current().fastest_by_class == svc._current().fastest_by_class, "the hand-set lap is saved"


def _fastest_service():
    svc, _clock, path, ctx = _class_service()
    svc.tick()
    race = svc._current()
    race.fastest = {"entry": "A", "driver": "Max A", "ms": 60000}
    race.fastest_by_class = {"H": dict(race.fastest), "G": {"entry": "B", "driver": "Max B", "ms": 62000}}
    return svc, path, ctx


def t_race_control_voids_one_class_fastest_lap():
    svc, path, ctx = _fastest_service()
    svc.set_fastest("rc:Anna", cls="G")
    race = svc._current()
    assert race.fastest_by_class == {"H": {"entry": "A", "driver": "Max A", "ms": 60000}}, \
        f"only the class's lap goes: {race.fastest_by_class}"
    assert race.fastest == {"entry": "A", "driver": "Max A", "ms": 60000}, "the overall lap stays"
    rec = _audit(path)[-1]
    assert (rec["who"], rec["action"], rec["class"], rec.get("cleared")) == ("rc:Anna", "fastest", "G", True), rec
    svc.set_fastest("rc:Anna", cls="H")
    assert svc._current().fastest_by_class == {} and svc._current().fastest is not None, \
        "voiding the class of the overall lap keeps the overall lap"
    again, _c, _p = _dash_service(ctx, path)
    assert again._current().fastest_by_class == {} and again._current().fastest["ms"] == 60000, "the void is saved"


def t_race_control_sets_one_class_fastest_lap():
    svc, path, _ctx = _fastest_service()
    st = svc.set_fastest("rc:Anna", "C", "Max C", 61500, cls="G")
    race = svc._current()
    assert race.fastest_by_class["G"] == {"entry": "C", "driver": "Max C", "ms": 61500}, race.fastest_by_class
    assert race.fastest == {"entry": "A", "driver": "Max A", "ms": 60000} and \
        race.fastest_by_class["H"]["ms"] == 60000, "the overall lap and the other class stay"
    rec = _audit(path)[-1]
    assert (rec["action"], rec["class"], rec["entry"], rec["ms"]) == ("fastest", "G", "C", 61500), rec
    assert st["class_fastest"]["G"]["entry"] == "C" and st["class_fastest"]["H"]["entry"] == "A", \
        f"the desk sees each class's lap: {st.get('class_fastest')}"
    for bad in ({"entry": "A", "driver": "Max A", "ms": 61000, "cls": "G"},
                {"cls": "X"}, {"cls": ""}, {"cls": "G\ud800"}, {"cls": ["G"]}, {"cls": "x" * 25}):
        try:
            svc.set_fastest("rc:Anna", **bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} must be refused")
    assert svc._current().fastest_by_class["G"]["entry"] == "C", "a refused change leaves the lap"


def t_a_class_fastest_lap_audit_is_written_first():
    svc, _path, _ctx = _fastest_service()
    svc._log = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("audit failed"))
    try:
        svc.set_fastest("rc:Anna", cls="G")
    except RuntimeError:
        pass  # expected
    assert "G" in svc._current().fastest_by_class, "a failed audit leaves the class lap"


def t_the_desk_sees_no_class_fastest_laps_outside_class_mode():
    ctx = {"roster": ROSTER, "stint": None, "remaining": None, "mode": "race"}
    svc, _clock, _path = _dash_service(ctx)
    assert "class_fastest" not in svc.standings(), "one class: the desk payload as before"
    try:
        svc.set_fastest("rc:Anna", cls="H")
    except ValueError:
        return
    raise AssertionError("a class outside class mode is refused")


def _class_of(d, name):
    return [c for c in d["classes"] if c["name"] == name][0]


def t_race_control_sets_a_class_lap_count():
    svc, _clock, path, ctx = _class_service()
    st = svc.set_class_laps("race_control:rc", "G", 54)
    assert st.get("class_laps") == {"G": 54}, f"the desks see the count: {st.get('class_laps')}"
    rec = _audit(path)[-1]
    assert (rec["who"], rec["action"], rec["class"], rec["laps"], rec["stint"]) == \
        ("race_control:rc", "class_laps", "G", 54, ""), rec
    svc.tick()
    assert _class_of(svc.dashboard(), "G")["laps"] == 54, svc.dashboard()["classes"]
    again, _c, _p = _dash_service(ctx, path)
    assert again.class_laps() == {"G": 54}, "a class lap count survives a relay restart"
    again.reset("rc:Anna")
    assert again.class_laps() == {"G": 54}, "a race reset keeps the class lap counts"
    st = again.set_class_laps("director:Bo", "G", None)
    assert st["class_laps"] == {} and _audit(path)[-1]["laps"] is None, "null clears the count"
    again.set_class_laps("director:Bo", "G", 30)
    dr.clear_actions(os.path.dirname(path))
    assert again.class_laps() == {}, "a fresh event drops the class lap counts"
    for cls, laps in (("X", 5), ("", 5), ("G\ud800", 5), (["G"], 5), (None, 5), ("G", 0), ("G", 1000),
                      ("G", "x"), ("G", True), ("G", 5.0)):
        try:
            svc.set_class_laps("rc:Anna", cls, laps)
        except ValueError:
            continue
        raise AssertionError(f"{cls!r} {laps!r} must be refused")


def t_a_class_lap_count_below_the_leader_lap_is_kept():
    svc, _clock, path, _ctx = _class_service()
    svc.correct("rc:Anna", "A", 27 * L, 0, "test")
    svc.correct("rc:Anna", "A", 28 * L, 0, "test")
    svc.correct("rc:Anna", "B", 27 * L, 0, "test")
    svc.correct("rc:Anna", "B", 27 * L, 0, "test")
    svc.tick()
    before = svc.dashboard()
    assert _class_of(before, "G")["lap"] >= 55, before["classes"]
    st = svc.set_class_laps("race_control:rc", "G", 54)
    assert st["state"] == before["state"] and st.get("class_laps") == {"G": 54}, st["state"]
    svc.tick()
    after = svc.dashboard()
    assert _class_of(after, "G")["laps"] == 54 and after["state"] == before["state"], after["classes"]
    assert not any(r["finished"] for r in after["entries"]), "a class lap count never finishes a car"


def t_the_schedule_laps_belong_to_the_first_named_class():
    roster = {"A": {"number": "1", "carClass": ""}, "B": {"number": "2", "carClass": "H"},
              "C": {"number": "3", "carClass": "G"}}
    svc, clock, _path = _dash_service({"roster": roster, "stint": None, "remaining": None, "mode": "race"})
    along = {"A": -20.0, "B": -60.0, "C": -100.0}
    while clock[0] < 6.0:
        clock[0] = round(clock[0] + 0.1, 3)
        for entry in along:
            along[entry] += 5.0
            svc.update({"entry": entry, "conn": entry.lower(), "name": "Max " + entry},
                       _sample(clock[0], along[entry], max(0, int(along[entry] // L) + 1)))
    svc.set_settings({"finish_mode": "laps", "lap_count": 10})
    svc.tick()
    d = svc.dashboard()
    assert [(c["name"], c["laps"]) for c in d["classes"]] == [("H", 10), ("G", None), ("", None)], \
        f"the Schedule laps go to the first named class, never to the unclassed group: {d['classes']}"


def t_a_class_lap_count_waits_for_the_final_lobby():
    ctx = {"roster": {k: dict(v) for k, v in CLASS_ROSTER.items()}, "stint": None, "remaining": None,
           "mode": "race"}
    path = os.path.join(tempfile.mkdtemp(), "driver-race.json")
    dr.write_settings(path, {"formation": False})
    clock = [0.0]
    svc = dr.Service(path, pick=lambda: {"id": "good"}, trace_for=lambda _i: TRACE,
                     multi_lobby=lambda: True, clock=lambda: clock[0], context=lambda: ctx)
    along = {"A": -20.0, "B": -60.0, "C": -100.0}
    while clock[0] < 6.0:
        clock[0] = round(clock[0] + 0.1, 3)
        for entry in along:
            along[entry] += 5.0
            svc.update({"entry": entry, "conn": entry.lower(), "name": "Max " + entry},
                       _sample(clock[0], along[entry], max(0, int(along[entry] // L) + 1)))
    svc.set_class_laps("rc:Anna", "G", 26)
    svc.tick()
    assert _class_of(svc.dashboard(), "G")["laps"] is None, \
        f"outside the final lobby no class shows a count: {svc.dashboard()['classes']}"
    assert svc.standings()["class_laps"] == {"G": 26}, "the desk input keeps the value"


def t_the_class_lap_audit_is_written_before_the_count_changes():
    svc, _clock, path, _ctx = _class_service()
    svc._log = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("audit failed"))
    try:
        svc.set_class_laps("rc:Anna", "G", 54)
    except RuntimeError:
        pass  # expected
    assert svc.class_laps() == {}, "a failed audit leaves no class lap count behind"


def t_schedule_class_laps_prefill_the_desk():
    svc, _clock, path, ctx = _class_service()
    ctx["stint"] = "S1"
    svc.set_class_laps("rc:Anna", "G", 40)
    ctx["stint"] = "S2"
    svc.stint_switch(class_laps={"g": 54, "Z": 9})
    assert svc.class_laps() == {"G": 54}, f"a Laps G column pre-fills G, an unknown class is skipped: {svc.class_laps()}"
    rec = _audit(path)[-1]
    assert (rec["who"], rec["action"], rec["class"], rec["laps"], rec["stint"]) == \
        ("schedule", "class_laps", "G", 54, "S2"), rec
    svc.set_class_laps("rc:Anna", "G", 50)
    svc.stint_switch(class_laps={"G": 54})
    assert svc.class_laps() == {"G": 50}, "a desk value set earlier stays"
    ctx["stint"] = "S1"
    svc.stint_switch(class_laps={"G": 54})
    assert svc.class_laps() == {"G": 40}, "each stint keeps its own desk value"
    svc.tick()
    assert _class_of(svc.dashboard(), "G")["laps"] == 40, svc.dashboard()["classes"]


def t_a_bad_class_lap_count_in_the_actions_file_is_dropped():
    svc, _clock, path, _ctx = _class_service()
    with open(dr._actions_path_for(path), "w", encoding="utf-8") as fh:
        json.dump({"class_laps": {"": {"G": 5, "H": 0, "X": True, "Y": 1000, "x" * 25: 3, "Z": "4"},
                                  "S9": [1], "S8": {"G": 2.0}}}, fh)
    assert svc.class_laps() == {"G": 5}, svc.class_laps()
    assert svc.class_laps("S9") == {} and svc.class_laps("S8") == {}


def t_class_fastest_laps_show_the_driver_name_override():
    svc, _clock, path, _ctx = _class_service()
    svc.set_driver_name("director:Bo", "C", "Kim Berg", driver="Max C")
    race = svc._current()
    race.fastest_by_class = {"G": {"entry": "C", "driver": "Max C", "ms": 63000}}
    svc.tick()
    g = [c for c in svc.dashboard()["classes"] if c["name"] == "G"][0]
    assert g["fastest"]["driver"] == "Kim Berg", g
    assert dr._read_json(svc._fastest_path())["by_class"]["G"]["driver"] == "Max C", \
        "the file keeps the registered name"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

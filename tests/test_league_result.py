#!/usr/bin/env python3
"""League result export (#1254) against the simulated league service.
Run: python3 tests/test_league_result.py"""
import json, os, sys, tempfile, threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "event-sim", "fakes"))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import driver_race as dr  # noqa: E402
import league_result as lr  # noqa: E402
import league_service as ls  # noqa: E402
import league_sim  # noqa: E402
import race_recording  # noqa: E402
from test_race_distance import TRACE  # noqa: E402
from test_driver_race import _feed  # noqa: E402

KEY = "fixture-league-key-2"
REF = league_sim.DEFAULT_REF
ON = {"RACECAST_SIMULATION": "1"}
ROSTER = {"A": {"number": "11", "cupTeam": "Red Bulls"}, "B": {"number": "22", "cupTeam": "Blue Cats"}}


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class _Source:
    """A race service stand-in with a settable state and classification."""

    def __init__(self, audit_path=None):
        self.state = None
        self.entries = [{"pos": 1, "entry": "A", "number": "11", "driver": "Ann", "team": "Red Bulls",
                         "laps": 10, "gap_s": 0, "interval_s": None, "lapped": 0, "status": "racing",
                         "pit_stops": 1, "last_lap_ms": 61000, "best_ms": 60500, "finished": True,
                         "fuel_l": 3.0, "conn": "secret-conn"}]
        self.fastest = {"entry": "A", "driver": "Ann", "team": "Red Bulls", "ms": 60500}
        self.stored = []
        self.audit_path = audit_path
        self.disconnects = []

    def race_state(self):
        return self.state

    def result_source(self):
        return {"state": self.state, "track_name": "Spa", "entries": [dict(e) for e in self.entries],
                "fastest": self.fastest, "started_at": 900.0, "disconnects": self.disconnects,
                "audit_path": self.audit_path}

    def store_result(self, doc):
        self.stored = [d for d in self.stored if (d["ref"], d["race"]) != (doc["ref"], doc["race"])
                       or not doc["race"]] + [doc]
        return True

    def recording_results(self):
        return list(self.stored)

    def mark_race_start(self):
        self.starts = getattr(self, "starts", 0) + 1
        if self.audit_path:
            with open(self.audit_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": self.clock(), "who": "race", "action": "race_start"}) + "\n")


def _setup(d, sim, src=None, wall=None, **kw):
    path = ls.path_in(d)
    ls.store(path, REF, ls.fetch_event(_cfg(sim), REF))
    src = src or _Source()
    wall = wall or _Clock()
    src.clock = wall
    exp = lr.Exporter(d, lambda: (_cfg(sim), ""), src, wall=wall, clock=wall, **kw)
    return exp, src, wall, path


def _cfg(sim):
    cfg, problem = ls.settings(sim.url, KEY, ON)
    assert cfg is not None, problem
    return cfg


def _run_race(exp, src, wall, states=("waiting", "racing", "final_lap", "finished")):
    for s in states:
        src.state = s
        wall.t += 1
        exp.observe(s)


def t_a_finish_sends_the_result_of_the_current_race_without_waiting():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        exp, src, wall, path = _setup(d, sim)
        _run_race(exp, src, wall)
        assert not sim.calls(call="result"), "observe only queues; the sender thread posts"
        assert ls.read(path)["finished"] is True
        exp.step()
        (call,) = sim.calls(call="result")
        body = call["body"]
        assert (body["ref"], body["race"], body["track_name"]) == (REF, "leg-1", "Spa"), body
        assert body["fastest"] == {"entry": "A", "driver": "Ann", "team": "Red Bulls", "ms": 60500}
        row = body["entries"][0]
        assert (row["team"], row["number"], row["best_ms"], row["driver_ref"]) == ("Red Bulls", "11", 60500, "")
        assert "fuel_l" not in row and "conn" not in row, f"the row is an allowlist: {row}"
        assert body["finished_at"].endswith("Z") and "samples" not in body and "rows" not in body
        st = lr.status_of(lr.results_path(d), REF, "leg-1")
        assert (st["state"], st["status"], st["link"]) == ("sent", "pending", league_sim.RESULT_LINK.format(
            ref=REF, race="leg-1")), st
        assert src.stored[0]["race"] == "leg-1" and src.stored[0]["body"] == body, \
            "the recording keeps exactly what was sent"


def t_a_relay_restart_in_a_finished_race_sends_nothing_again():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        exp, src, wall, _path = _setup(d, sim)
        exp.observe("finished")
        exp.observe("finished")
        exp.step()
        assert not sim.calls(call="result") and not src.stored, "the first state seen is the baseline"


def t_a_failed_send_is_retried_with_backoff_until_it_lands():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        exp, src, wall, _path = _setup(d, sim)
        sim.answer("result", status=503, body={"error": "down"})
        _run_race(exp, src, wall)
        wait = exp.step()
        st = lr.status_of(lr.results_path(d), REF, "leg-1")
        assert st["state"] == "failed" and "503" in st["error"] and st["attempts"] == 1, st
        assert wait == lr.RETRY_MIN_S, wait
        assert exp.status()["queued"] == [{"race": "leg-1", "tries": 1}]
        wall.t += 1
        exp.step()
        assert lr.status_of(lr.results_path(d), REF, "leg-1")["attempts"] == 1, "no retry before its time"
        wall.t += lr.RETRY_MIN_S
        assert exp.step() == 2 * lr.RETRY_MIN_S, "the wait doubles"
        sim.clear_answers()
        wall.t += 2 * lr.RETRY_MIN_S
        exp.step()
        st = lr.status_of(lr.results_path(d), REF, "leg-1")
        assert st["state"] == "sent" and st["status"] == "pending" and st["error"] == "", st
        assert exp.status()["queued"] == [] and len(sim.calls(call="result")) == 1


def t_a_refusal_is_final_and_names_the_services_reason():
    for code in (400, 401, 403, 404, 409, 422):
        with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
            exp, src, wall, _path = _setup(d, sim)
            sim.answer("result", status=code, body={"error": "race is locked\u0007 on the service"})
            _run_race(exp, src, wall)
            exp.step()
            st = lr.status_of(lr.results_path(d), REF, "leg-1")
            assert st["state"] == "failed" and st["final"] is True and f"HTTP {code}" in st["error"], (code, st)
            assert exp.status()["queued"] == [], f"HTTP {code} is final: no retry"
            if code not in (401, 403):
                assert "race is locked" not in st["error"], "a reason with a control character is dropped"
            sim.answer("result", status=code, body={"error": "race is locked"})
            assert exp.resend()["ok"]
            exp.step()
            st = lr.status_of(lr.results_path(d), REF, "leg-1")
            assert "race is locked" in st["error"], f"the service's own reason reaches the operator: {st}"
            sim.clear_answers()
            assert exp.resend()["ok"], "a manual resend is always possible"
            exp.step()
            st = lr.status_of(lr.results_path(d), REF, "leg-1")
            assert st["state"] == "sent" and st["final"] is False, st


def t_overload_and_timeouts_are_retried():
    for code in (408, 429, 500, 503):
        with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
            exp, src, wall, _path = _setup(d, sim)
            sim.answer("result", status=code, body={})
            _run_race(exp, src, wall)
            exp.step()
            st = lr.status_of(lr.results_path(d), REF, "leg-1")
            assert st["state"] == "failed" and not st["final"], (code, st)
            assert exp.status()["queued"] == [{"race": "leg-1", "tries": 1}], f"HTTP {code} is retried"


def t_a_missing_service_keeps_the_result_queued():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        exp, src, wall, _path = _setup(d, sim)
        exp._settings_fn = lambda: (None, "")
        _run_race(exp, src, wall)
        exp.step()
        st = lr.status_of(lr.results_path(d), REF, "leg-1")
        assert st["state"] == "failed" and "no league service" in st["error"], st
        assert exp.status()["queued"][0]["race"] == "leg-1"


def t_two_races_in_one_event_each_get_their_result_and_then_none():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        exp, src, wall, path = _setup(d, sim)
        _run_race(exp, src, wall)
        exp.step()
        exp.observe("waiting")
        assert ls.read(path)["current"] == "leg-1", "a reset alone is no new race"
        src.entries[0]["laps"] = 12
        _run_race(exp, src, wall, ("racing", "finished"))
        assert ls.read(path)["current"] == "leg-2"
        exp.step()
        bodies = {c["race"]: c["body"] for c in sim.calls(call="result")}
        assert set(bodies) == {"leg-1", "leg-2"} and bodies["leg-2"]["entries"][0]["laps"] == 12, bodies
        _run_race(exp, src, wall, ("waiting", "racing"))
        ev = ls.read(path)
        assert ev["done"] and ev["current"] == "leg-2", ev
        assert "races are over" in exp.status()["note"]
        _run_race(exp, src, wall, ("finished",))
        exp.step()
        assert len(sim.calls(call="result")) == 2, "after the last listed race no result is sent"
        assert src.stored[-1]["race"] is None, "the recording still keeps that race's classification"
        assert exp.set_race("leg-1") == {"ok": True, "current": "leg-1"}
        ev = ls.read(path)
        assert not ev["done"] and not ev["finished"], "the Director's pick starts that race afresh"
        assert exp.set_race("leg-9")["ok"] is False


def t_a_resend_rebuilds_the_finished_race_and_replaces_the_draft():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        audit = os.path.join(d, dr.AUDIT)
        exp, src, wall, _path = _setup(d, sim, src=_Source(audit))
        _run_race(exp, src, wall)
        exp.step()
        with open(audit, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": wall.t + 5, "who": "rc:Anna", "action": "correct", "entry": "A",
                                 "metres": -120.0, "pit_stops": 1, "reason": "cut at T1"}) + "\n")
        wall.t += 10
        src.entries[0]["laps"] = 9
        assert exp.resend() == {"ok": True, "ref": REF, "race": "leg-1", "rebuilt": True}
        exp.step()
        first, second = [c["body"] for c in sim.calls(call="result")]
        assert second["entries"][0]["laps"] == 9 and second["finished_at"] == first["finished_at"], second
        assert [e["kind"] for e in second["events"]] == ["correction"], \
            f"a correction after the finish is in the resend: {second['events']}"
        ev = second["events"][0]
        assert ev["entry"] == "A" and "cut at T1" in ev["detail"], ev
        assert "rc:Anna" not in json.dumps(second), "who acted stays on the producer's machine"
        assert sim.results[(REF, "leg-1")]["entries"][0]["laps"] == 9, "the resend replaced the draft"
        assert src.stored[-1]["body"] == second, "the recording keeps the corrected result"


def t_a_resend_after_the_next_race_began_uses_the_recording():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        exp, src, wall, _path = _setup(d, sim)
        _run_race(exp, src, wall)
        exp.step()
        _run_race(exp, src, wall, ("waiting", "racing"))
        src.entries = []
        assert exp.resend("leg-1")["rebuilt"] is False
        exp.step()
        assert sim.calls(call="result")[-1]["body"]["entries"][0]["entry"] == "A", \
            "the live race moved on, the recording's copy goes"
        r = exp.resend("leg-2")
        assert r["ok"] is False and "no result" in r["error"], r


def t_resend_errors_name_the_problem():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        exp, src, wall, path = _setup(d, sim)
        assert exp.resend("leg-9")["error"] == "the event does not list race leg-9"
        ls.clear(path)
        assert exp.resend() == {"ok": False, "error": "no league event loaded"}
        assert exp.set_race("leg-1") == {"ok": False, "error": "no league event loaded"}


def t_steward_events_carry_red_flags_corrections_and_confirmed_disconnects_of_this_race():
    records = [{"ts": 50.0, "action": "red_flag", "mode": "keep", "who": "old race"},
               {"ts": 100.0, "action": "reset", "who": "x"},
               {"ts": 120.0, "action": "red_flag", "mode": "neutralise", "who": "rc:Bo"},
               {"ts": 150.0, "action": "release", "who": "rc:Bo"},
               {"ts": 160.0, "action": "correct", "entry": "B", "metres": 0.0, "pit_stops": -1, "reason": "",
                "who": "rc:Bo"},
               {"ts": 170.0, "action": "compound", "entry": "B", "code": "RS"}]
    since = lr.race_start(records, started_at=90.0)
    assert lr.race_start([], started_at=90.0) == 90.0 and lr.race_start([]) == 0.0
    assert since == 100.0, "the last reset starts the race"
    dc = [{"entry": "A", "state": "confirmed", "last_packet": 130.0, "opened": 140.0, "dropout_s": 12.4},
          {"entry": "B", "state": "confirmed", "last_packet": 10.0, "opened": 20.0}]
    events = lr.steward_events(records, dc, since, 200.0)
    assert [(e["kind"], e.get("entry")) for e in events] == [
        ("red_flag", None), ("disconnect", "A"), ("correction", "B")], \
        f"a release is no second red flag and a compound change no steward event: {events}"
    assert events[0]["detail"] == "red flag, restart neutralise" and events[2]["detail"] == "-1 pit stop"
    assert events[1]["at"] == lr.iso(130.0) and "12 s" in events[1]["detail"]
    long = lr.steward_events([{"ts": 1.0, "action": "correct", "entry": "A", "metres": 1.0,
                               "reason": "x" * 400}], [], 0, 2)
    assert len(long[0]["detail"]) == lr.MAX_DETAIL, "a detail stays within the contract"


def t_a_graphic_from_the_service_is_filed_for_the_director():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY, graphic=True) as sim:
        exp, src, wall, _path = _setup(d, sim)
        _run_race(exp, src, wall)
        exp.step()
        st = lr.status_of(lr.results_path(d), REF, "leg-1")
        assert st["graphic"] == lr.graphic_name(REF, "leg-1") and st["graphic_error"] == "", st
        staged = os.path.join(d, lr.GRAPHICS, st["graphic"])
        with open(staged, "rb") as fh:
            assert fh.read() == sim.graphic_body
        gfx = os.path.join(d, "graphics")
        assert not os.path.exists(gfx), "a fetched graphic never reaches OBS by itself"
        dest = lr.install_graphic(staged, gfx)
        with open(dest, "rb") as fh:
            assert os.path.basename(dest) == "Race Results.png" and fh.read() == sim.graphic_body


def t_a_refused_graphic_is_named_and_the_result_still_counts():
    cases = [("raw", b"<html>not a png</html>", "image/png", "not a PNG"),
             ("raw", league_sim.png_bytes(), "text/plain", "not a PNG"),
             ("big", None, "image/png", "larger than 4 MB")]
    for kind, payload, ctype, words in cases:
        with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY, graphic=True) as sim:
            if kind == "big":
                sim.graphic_body = lr.PNG_MAGIC + b"\0" * lr.MAX_GRAPHIC
            else:
                sim.graphic_body = payload
            orig = sim.route

            def route(method, path, query, headers, body, orig=orig, ctype=ctype):
                status, _t, data, *rest = orig(method, path, query, headers, body)
                return (status, ctype if path.endswith(".png") else _t, data, *rest)
            sim.route = route
            exp, src, wall, _path = _setup(d, sim)
            _run_race(exp, src, wall)
            exp.step()
            st = lr.status_of(lr.results_path(d), REF, "leg-1")
            assert st["state"] == "sent" and st["graphic"] is None and words in st["graphic_error"], (kind, st)
            assert not os.listdir(os.path.join(d, lr.GRAPHICS)) if os.path.isdir(os.path.join(d, lr.GRAPHICS)) \
                else True, "a refused graphic leaves no file"


def t_a_graphic_on_another_port_is_never_fetched():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim, \
            league_sim.LeagueSim(key=KEY, graphic=True) as other:
        exp, src, wall, _path = _setup(d, sim)
        sim.answer("result", body={"v": 1, "status": "stored", "graphic_url": other.url + "/v1/graphic/leg-1.png"})
        _run_race(exp, src, wall)
        exp.step()
        st = lr.status_of(lr.results_path(d), REF, "leg-1")
        assert st["state"] == "sent" and st["graphic"] is None, st
        assert not other.calls(call="graphic"), "the other port's service never hears from the relay"
        try:
            lr.fetch_graphic(_cfg(sim), other.url + "/v1/graphic/leg-1.png", os.path.join(d, "x.png"))
        except lr.GraphicError as e:
            assert "own host and port" in str(e)
        else:
            raise AssertionError("fetch_graphic itself refuses another origin")


def t_refresh_files_the_current_status():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        exp, src, wall, _path = _setup(d, sim)
        _run_race(exp, src, wall)
        exp.step()
        sim.result_status = "stored"
        st = lr.refresh(_cfg(sim), REF, "leg-1", d)
        assert st["status"] == "stored" and st["state"] == "sent", st
        try:
            lr.refresh(_cfg(sim), REF, "leg-2", d)
        except ls.ServiceError as e:
            assert "404" in str(e)
        else:
            raise AssertionError("an unknown result is an error")


def t_the_exporter_with_the_real_race_service_and_recording():
    d = tempfile.mkdtemp()      # the recorder keeps its SQLite file open, which Windows will not delete
    with league_sim.LeagueSim(key=KEY) as sim:
        path = os.path.join(d, "driver-race.json")
        dr.write_settings(path, {"formation": False})
        clock = [0.0]
        ctx = {"roster": ROSTER, "mode": "race"}
        svc = dr.Service(path, pick=lambda: {"id": "good", "track": "Spa"}, trace_for=lambda _i: TRACE,
                         multi_lobby=lambda: False, clock=lambda: clock[0],
                         wall=lambda: 5000.0 + clock[0], context=lambda: ctx, recording_dir=d)
        svc.open_recording()
        ls.store(ls.path_in(d), REF, ls.fetch_event(_cfg(sim), REF))
        exp = lr.Exporter(d, lambda: (_cfg(sim), ""), svc, wall=lambda: 5000.0 + clock[0])

        def tick():
            svc.tick()
            exp.observe(svc.race_state())
        tick()
        _feed(svc, clock, "A", "a", -20.0, 6.0)
        tick()
        _feed(svc, clock, "B", "b", -60.0, 9.0)
        tick()
        svc.finish()
        tick()
        exp.step()
        first = sim.calls(call="result")[-1]["body"]
        assert [(r["entry"], r["team"]) for r in first["entries"]] == [("A", "Red Bulls"), ("B", "Blue Cats")]
        svc.correct("rc:Anna", "B", metres=500.0, reason="tow")
        clock[0] += 1
        tick()
        assert exp.resend()["rebuilt"] is True
        exp.step()
        second = sim.calls(call="result")[-1]["body"]
        assert [r["entry"] for r in second["entries"]] == ["B", "A"], "the correction moved B ahead"
        assert [e["kind"] for e in second["events"]] == ["correction"], second["events"]
        svc._recorder()._writer.flush()
        kept = race_recording.stored_results(svc._recorder()._store.path)
        assert kept[-1]["body"] == second and kept[-1]["race"] == "leg-1"


def t_the_sender_thread_stops_with_the_relay():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        exp, src, wall, _path = _setup(d, sim)
        stop = threading.Event()
        t = threading.Thread(target=exp.run, args=(stop,), daemon=True)
        t.start()
        _run_race(exp, src, wall)
        for _ in range(100):
            if sim.calls(call="result"):
                break
            threading.Event().wait(0.05)
        stop.set()
        exp._wake.set()
        t.join(5)
        assert not t.is_alive() and sim.calls(call="result"), "a queued result goes out on the thread"


def t_an_unarmed_exporter_ignores_the_race_until_the_relay_owns_its_port():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        exp, src, wall, path = _setup(d, sim, armed=False)
        _run_race(exp, src, wall)
        assert not ls.read(path)["finished"] and not src.stored and exp.status()["watching"] is False, \
            "a relay that does not own its port touches no league file"
        exp.arm()
        _run_race(exp, src, wall, ("racing", "finished"))
        assert ls.read(path)["finished"] and exp.status()["watching"], "armed, the next finish exports"


def t_a_status_file_that_cannot_be_written_still_backs_off():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        exp, src, wall, _path = _setup(d, sim)
        _run_race(exp, src, wall)

        def broken(*_a, **_k):
            raise OSError("disk full")
        exp._post = broken
        exp.step()
        assert exp.status()["queued"] == [{"race": "leg-1", "tries": 1}], "an OSError counts as a failed try"
        assert exp.step() > 1.0, "and waits before the next one instead of posting every tick"


def t_an_event_loaded_after_the_finish_points_to_the_recording():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        src, wall = _Source(), _Clock()
        exp = lr.Exporter(d, lambda: (_cfg(sim), ""), src, wall=wall, clock=wall)
        _run_race(exp, src, wall)
        assert src.stored[-1]["race"] is None, "no event, the recording keeps a result without a race"
        ls.store(ls.path_in(d), REF, ls.fetch_event(_cfg(sim), REF))
        r = exp.resend()
        assert not r["ok"] and "goes from Recording" in r["error"], r


def t_an_earlier_races_steward_events_stay_out_after_a_track_change_and_a_restart():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        audit = os.path.join(d, dr.AUDIT)
        exp, src, wall, path = _setup(d, sim, src=_Source(audit))
        wall.t = 1050
        exp.observe("racing")
        with open(audit, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": 1100, "who": "rc", "action": "red_flag", "mode": "keep"}) + "\n")
        wall.t = 1200
        src.state = "finished"
        exp.observe("finished")
        for t, state in ((1300, "waiting"), (1400, "racing"), (1500, "finished")):   # a new pick, no reset
            wall.t = t
            src.state = state
            exp.observe(state)
        exp.step()
        bodies = {c["race"]: c["body"] for c in sim.calls(call="result")}
        assert [e["kind"] for e in bodies["leg-1"]["events"]] == ["red_flag"], bodies["leg-1"]["events"]
        assert bodies["leg-2"]["events"] == [], f"race 1's red flag is not race 2's: {bodies['leg-2']['events']}"
        restarted = lr.Exporter(d, lambda: (_cfg(sim), ""), src, wall=wall, clock=wall)
        wall.t = 1600
        assert restarted.resend("leg-2")["rebuilt"] is True
        restarted.step()
        assert sim.calls(call="result")[-1]["body"]["events"] == [], "a relay restart keeps the window"


def t_a_held_red_flag_raised_again_is_one_event():
    records = [{"ts": 10.0, "action": "red_flag", "mode": "keep"}, {"ts": 12.0, "action": "red_flag", "mode": "keep"},
               {"ts": 20.0, "action": "release"}, {"ts": 30.0, "action": "red_flag", "mode": ""}]
    events = lr.steward_events(records, [], 0, 40)
    assert [(e["at"], e["detail"]) for e in events] == [(lr.iso(10.0), "red flag, restart keep"),
                                                        (lr.iso(30.0), "red flag")], events


def t_a_failed_file_update_does_not_lose_the_finish():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        exp, src, wall, path = _setup(d, sim)
        _run_race(exp, src, wall, ("waiting", "racing"))
        real = ls.update
        ls.update = lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk full"))
        try:
            src.state = "finished"
            try:
                exp.observe("finished")
            except OSError:
                pass  # the relay's tick logs it
        finally:
            ls.update = real
        exp.observe("finished")
        assert ls.read(path)["finished"], "the next tick finishes what the failed one could not"
        exp.step()
        assert len(sim.calls(call="result")) == 1


def t_a_send_the_restart_interrupted_is_marked_not_resumed():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        path = lr.results_path(d)
        lr.record_status(path, REF, "leg-1", state="sending")
        lr.record_status(path, REF, "leg-2", state="failed", error="HTTP 503", final=False)
        lr.record_status(path, "OLD", "leg-1", state="failed", error="HTTP 404", final=True)
        exp, src, wall, _path = _setup(d, sim, armed=False)
        exp.arm()
        for race in ("leg-1", "leg-2"):
            st = lr.status_of(path, REF, race)
            assert st["state"] == "failed" and st["final"] and "relay restarted" in st["error"], st
        assert lr.status_of(path, "OLD", "leg-1")["error"] == "HTTP 404", "a final refusal keeps its reason"
        exp.step()
        assert not sim.calls(call="result"), "nothing is resent by itself"


def t_a_status_file_lock_serializes_two_processes():
    import subprocess
    with tempfile.TemporaryDirectory() as d:
        path = lr.results_path(d)
        code = ("import sys; sys.path.insert(0, %r); import league_result as lr\n"
                "for i in range(25): lr.record_status(%r, 'R', sys.argv[1] + str(i), state='sent')") % (
                    os.path.join(ROOT, "src", "scripts"), path)
        procs = [subprocess.Popen([sys.executable, "-c", code, tag]) for tag in ("a", "b")]
        assert all(p.wait(60) == 0 for p in procs)
        races = {i["race"] for i in lr.read_statuses(path)}
        assert len(races) == 50, f"no write lost another process's status: {len(races)}"
        assert not os.path.exists(path + ".lock")


def t_an_unsafe_race_never_names_a_graphic_file():
    for race in ("a/../../../graphics/Race Results", "..", "x y"):
        try:
            lr.graphic_name("REF", race)
        except ValueError:
            continue
        raise AssertionError(f"{race!r} must not name a file")
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY, graphic=True) as sim:
        answer = {"status": "stored", "message": "", "link": None, "graphic_url": sim.url + "/v1/graphic/x.png"}
        st = lr._file_answer(_cfg(sim), REF, "a/../../x", answer, d, None)
        assert st["graphic"] is None and "race id" in st["graphic_error"], st
        assert not os.path.exists(os.path.join(d, "x.png"))


def t_an_answer_that_cannot_be_saved_here_is_not_posted_again():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        exp, src, wall, _path = _setup(d, sim)
        _run_race(exp, src, wall)
        real = lr._file_answer
        lr._file_answer = lambda *_a, **_k: (_ for _ in ()).throw(OSError("read-only"))
        try:
            exp.step()
            wall.t += 120
            exp.step()
        finally:
            lr._file_answer = real
        assert len(sim.calls(call="result")) == 1, "the service has it: no second post"
        (res,) = exp.status()["results"]
        assert res["state"] == "sent" and "not saved here" in res["local_error"] and res["status"] == "pending", res


def t_a_resend_from_the_recording_is_cut_to_the_allowlist():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        exp, src, wall, _path = _setup(d, sim)
        src.stored = [{"ref": REF, "race": "leg-1", "finished_at": 1.0, "body": {
            "v": 1, "ref": REF, "race": "leg-1", "finished_at": "2026-01-01T00:00:00Z", "secret": "x",
            "entries": [{"pos": 1, "entry": "A", "driver": "Ann", "fuel_l": 3.0, "conn": "c1"}],
            "fastest": {"entry": "A", "ms": 60000, "conn": "c1"},
            "events": [{"kind": "red_flag", "at": "2026-01-01T00:00:00Z", "who": "rc:Bo"},
                       {"kind": "chat", "at": "2026-01-01T00:00:00Z"}]}}]
        assert exp.resend("leg-1")["ok"]
        exp.step()
        body = sim.calls(call="result")[-1]["body"]
        flat = json.dumps(body)
        for leak in ("secret", "fuel_l", "conn", "rc:Bo", "chat"):
            assert leak not in flat, f"{leak} reached the service: {body}"
        assert body["entries"][0]["driver_ref"] == "" and body["fastest"] == {"entry": "A", "ms": 60000}


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

#!/usr/bin/env python3
"""`racecast event league`, its Control Center data and the LEAGUE_SERVICE_* profile keys
(#1253). Run: python3 tests/test_league_event_cli.py"""
import contextlib, importlib.util, io, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "event-sim", "fakes"))
spec = importlib.util.spec_from_file_location("racecast", os.path.join(ROOT, "src", "racecast.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
import league_service as ls  # noqa: E402
import league_sim  # noqa: E402

KEY = "fixture-league-key-3"


def _rc(url, key=KEY):
    return m.pcfg.ResolvedConfig(profile="demo", name="Demo", sheet_id="S",
                                 league_service_url=url, league_service_key=key)


@contextlib.contextmanager
def _simulation():
    saved = os.environ.get("RACECAST_SIMULATION")
    os.environ["RACECAST_SIMULATION"] = "1"
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("RACECAST_SIMULATION", None)
        else:
            os.environ["RACECAST_SIMULATION"] = saved


def t_load_stores_the_event_and_the_status_never_names_the_key():
    with tempfile.TemporaryDirectory() as d, _simulation(), league_sim.LeagueSim(key=KEY) as sim:
        path = ls.path_in(d)
        rc = _rc(sim.url)
        r = m.league_event_load_data(league_sim.DEFAULT_REF, rc=rc, path=path)
        assert r["ok"], r
        assert r["event"]["current"] == "leg-1" and [x["race"] for x in r["event"]["races"]] == ["leg-1", "leg-2"]
        with open(path, encoding="utf-8") as fh:
            stored = json.load(fh)
        assert stored["ref"] == league_sim.DEFAULT_REF and [w["id"] for w in stored["widgets"]] == \
            ["match-score", "standings"]
        status = m.league_event_data(rc=rc, path=path)
        assert status["configured"] and status["key_set"] and status["event"]["ref"] == league_sim.DEFAULT_REF
        assert KEY not in json.dumps(status) and KEY not in json.dumps(r), "the key is never in a status"
        lines = "\n".join(m._league_event_lines(status))
        assert "leg-1" in lines and "<- current" in lines and KEY not in lines


def t_load_errors_leave_no_file():
    with tempfile.TemporaryDirectory() as d, _simulation(), league_sim.LeagueSim(key=KEY) as sim:
        path = ls.path_in(d)
        for rc, ref, needle in ((_rc("", ""), "M", "No league service"),
                                (_rc("http://league.example/r"), "M", "https"),
                                (_rc(sim.url), "UNKNOWN", "404"),
                                (_rc(sim.url, key="other-key"), league_sim.DEFAULT_REF, "LEAGUE_SERVICE_KEY"),
                                (_rc(sim.url), "  ", "empty")):
            r = m.league_event_load_data(ref, rc=rc, path=path)
            assert not r["ok"] and needle in r["error"], (needle, r)
            assert "other-key" not in r["error"]
        assert not os.path.exists(path), "a failed load writes nothing"
        status = m.league_event_data(rc=_rc("", ""), path=path)
        assert status["configured"] is False and status["event"] is None
        assert m._league_event_lines(status) == [m.NO_LEAGUE_SERVICE]


def t_without_a_service_nothing_is_called():
    calls = []
    r = m.league_event_load_data("M", rc=_rc("", ""), path=os.devnull,
                                 fetch=lambda *a: calls.append(a))
    assert not r["ok"] and calls == [], "no configured service, no call"


def t_event_stop_clears_the_league_event():
    with tempfile.TemporaryDirectory() as d:
        path = ls.path_in(d)
        ls.store(path, "REF", ls.parse_event({"v": 1, "races": [{"race": "r1"}], "widgets": []}))
        m._stop_driver_invite(os.path.join(d, "driver-invite.json"))
        assert not os.path.exists(path), "event stop clears league-event.json"


def _recording_with(d, *docs):
    import race_recording
    store = race_recording.open_current(d, 1700000000.0)
    for doc in docs:
        store.set_meta(race_recording.RESULTS_KEY, race_recording.merged_results(
            store.meta(race_recording.RESULTS_KEY), doc))
    path = store.path
    store.close()
    return path


def _kept(ref, race, laps=10, at=1700000100.0):
    return {"ref": ref, "race": race, "finished_at": at,
            "body": {"v": 1, "ref": ref, "race": race, "finished_at": "2023-11-14T22:15:00Z",
                     "entries": [{"pos": 1, "entry": "A", "driver": "Ann", "team": "Red Bulls", "laps": laps}],
                     "events": [], "fastest": None}}


def _no_relay():
    return False


def t_send_after_the_event_from_a_recording_replaces_the_draft():
    REF = league_sim.DEFAULT_REF
    with tempfile.TemporaryDirectory() as d, _simulation(), league_sim.LeagueSim(key=KEY) as sim:
        rec = _recording_with(d, _kept(REF, "leg-1"), _kept(None, None, laps=7, at=1700000200.0))
        rc = _rc(sim.url)
        r = m.league_result_send_data(recording=os.path.basename(rec), index=1, rc=rc, runtime=d,
                                      relay_running=_no_relay)
        assert r["ok"] and r["status"]["status"] == "pending" and r["via"] == os.path.basename(rec), r
        assert sim.results[(REF, "leg-1")]["entries"][0]["team"] == "Red Bulls"
        r = m.league_result_send_data("leg-1", recording=rec, index=2, rc=rc, runtime=d, relay_running=_no_relay)
        assert not r["ok"] and "give the race and the reference" in r["error"], \
            "a race run without a loaded event names no reference"
        r = m.league_result_send_data("leg-1", recording=rec, index=2, ref=REF, rc=rc, runtime=d,
                                      relay_running=_no_relay)
        assert r["ok"] and sim.results[(REF, "leg-1")]["entries"][0]["laps"] == 7, "the resend replaced the draft"
        data = m.league_event_data(rc=rc, path=ls.path_in(d))
        assert [x["race"] for x in data["results"]] == ["leg-1"] and data["results"][0]["state"] == "sent"
        assert data["recordings"] == [{"file": os.path.basename(rec), "results": [
            {"index": 1, "ref": REF, "race": "leg-1", "finished_at": 1700000100.0},
            {"index": 2, "ref": None, "race": None, "finished_at": 1700000200.0}]}], data["recordings"]
        lines = "\n".join(m._league_result_lines(data))
        assert "leg-1: pending, Draft waits for a steward." in lines and "2. (no league race)" in lines, lines
        assert KEY not in json.dumps(data) and KEY not in lines


def t_send_errors_name_the_problem():
    REF = league_sim.DEFAULT_REF
    with tempfile.TemporaryDirectory() as d, _simulation(), league_sim.LeagueSim(key=KEY) as sim:
        rc = _rc(sim.url)
        r = m.league_result_send_data(rc=_rc("", ""), runtime=d, relay_running=_no_relay)
        assert r == {"ok": False, "error": m.NO_LEAGUE_SERVICE}
        r = m.league_result_send_data(rc=rc, runtime=d, relay_running=_no_relay)
        assert r == {"ok": False, "error": "no current race recording"}
        r = m.league_result_send_data(recording="nope.sqlite", rc=rc, runtime=d, relay_running=_no_relay)
        assert r == {"ok": False, "error": "no such recording: nope.sqlite"}
        empty = _recording_with(d)
        r = m.league_result_send_data(rc=rc, runtime=d, relay_running=_no_relay)
        assert r == {"ok": False, "error": "this recording holds no finished race result"}, r
        os.remove(empty)
        rec = _recording_with(d, _kept(REF, "leg-1"))
        r = m.league_result_send_data(recording=rec, index=3, rc=rc, runtime=d, relay_running=_no_relay)
        assert "pick 1 to 1" in r["error"], r
        sim.answer("result", status=503, body={})
        r = m.league_result_send_data(recording=rec, rc=rc, runtime=d, relay_running=_no_relay)
        assert not r["ok"] and "503" in r["error"], r
        import league_result
        st = league_result.status_of(league_result.results_path(d), REF, "leg-1")
        assert st["state"] == "failed" and "503" in st["error"], st


def t_send_for_the_current_event_goes_through_the_running_relay():
    with tempfile.TemporaryDirectory() as d, _simulation(), league_sim.LeagueSim(key=KEY) as sim:
        asked = []
        r = m.league_result_send_data("leg-2", rc=_rc(sim.url), runtime=d, relay_running=lambda: True,
                                      relay_send=lambda race: asked.append(race) or {"ok": True, "race": race,
                                                                                    "rebuilt": False})
        assert r == {"ok": True, "race": "leg-2", "rebuilt": False, "via": "relay"} and asked == ["leg-2"], r
        r = m.league_result_send_data(rc=_rc(sim.url), runtime=d, relay_running=lambda: True,
                                      relay_send=lambda race: {"ok": False, "error": "race leg-1 has no result"})
        assert r == {"ok": False, "error": "race leg-1 has no result"}
        boom = m.league_result_send_data(rc=_rc(sim.url), runtime=d, relay_running=lambda: True,
                                         relay_send=lambda race: (_ for _ in ()).throw(OSError("refused")))
        assert not boom["ok"] and "refused" in boom["error"], boom


def t_refresh_asks_the_service_for_the_current_race():
    REF = league_sim.DEFAULT_REF
    with tempfile.TemporaryDirectory() as d, _simulation(), league_sim.LeagueSim(key=KEY) as sim:
        rc = _rc(sim.url)
        r = m.league_result_refresh_data(rc=rc, runtime=d)
        assert not r["ok"] and "no league event loaded" in r["error"], r
        m.league_event_load_data(REF, rc=rc, path=ls.path_in(d))
        r = m.league_result_refresh_data(rc=rc, runtime=d)
        assert not r["ok"] and "404" in r["error"], "nothing sent yet"
        rec = _recording_with(d, _kept(REF, "leg-1"))
        m.league_result_send_data(recording=rec, rc=rc, runtime=d, relay_running=_no_relay)
        sim.result_status = "stored"
        r = m.league_result_refresh_data(rc=rc, runtime=d)
        assert r["ok"] and r["status"]["status"] == "stored", r


def t_event_result_verb_sends_and_prints():
    REF = league_sim.DEFAULT_REF
    with tempfile.TemporaryDirectory() as d, _simulation(), league_sim.LeagueSim(key=KEY) as sim:
        rec = _recording_with(d, _kept(REF, "leg-2"))
        rc = _rc(sim.url)
        orig = (m._active_config, m._runtime_dir, m._apply_active_profile_env,
                m._relay_already_running_for_active_profile)
        m._active_config, m._runtime_dir = (lambda: rc), (lambda: d)
        m._apply_active_profile_env = lambda: None
        m._relay_already_running_for_active_profile = _no_relay
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                m.event_result_cmd(["send", "--recording", rec])
            with contextlib.redirect_stdout(io.StringIO()) as err:
                try:
                    m.event_result_cmd(["send", "--recording", rec, "--index", "4"])
                except SystemExit as e:
                    assert e.code == 1
                else:
                    raise AssertionError("a failed send exits 1")
        finally:
            (m._active_config, m._runtime_dir, m._apply_active_profile_env,
             m._relay_already_running_for_active_profile) = orig
        assert "result" in m.EVENT_VERBS and m.DISPATCH[("event", "result")] is m.event_result_cmd, \
            "racecast event result dispatches to the verb"
        text = out.getvalue()
        assert f"Sent leg-2 of {REF}" in text and "leg-2: pending" in text, text
        assert "pick 1 to 1" in err.getvalue()


def t_a_result_without_a_race_goes_to_the_loaded_events_current_race():
    REF = league_sim.DEFAULT_REF
    with tempfile.TemporaryDirectory() as d, _simulation(), league_sim.LeagueSim(key=KEY) as sim:
        rc = _rc(sim.url)
        m.league_event_load_data(REF, rc=rc, path=ls.path_in(d))
        rec = _recording_with(d, _kept(None, None))
        r = m.league_result_send_data(recording=rec, index=1, rc=rc, runtime=d, relay_running=_no_relay)
        assert r["ok"] and (r["ref"], r["race"]) == (REF, "leg-1"), \
            f"a race finished before the event was loaded goes out as the current race: {r}"


def t_the_relays_refusal_reaches_the_operator_with_its_reason():
    sys.path.insert(0, HERE)
    import test_league_routes as lrt
    with tempfile.TemporaryDirectory() as d, _simulation(), league_sim.LeagueSim(key=lrt.KEY) as sim:
        srv, port, exp, src, wall, _obs, _gfx = lrt._stack(d, sim)
        orig = m._relay_port
        m._relay_port = lambda: port
        try:
            r = m.league_result_send_data(rc=_rc(sim.url, lrt.KEY), runtime=d, relay_running=lambda: True)
            assert r == {"ok": False, "error": "race leg-1 has no result in the race recording yet; "
                                               "a race finished before the event was loaded goes from Recording"}, r
            lrt._run_race(exp, src, wall)
            r = m.league_result_send_data(rc=_rc(sim.url, lrt.KEY), runtime=d, relay_running=lambda: True)
            assert r["ok"] and r["via"] == "relay" and r["rebuilt"] is True, r
        finally:
            m._relay_port = orig
            srv.shutdown()


def _profile_home(td, text):
    prof = os.path.join(td, "profiles", "demo")
    os.makedirs(prof)
    open(os.path.join(td, ".env.example"), "w").close()
    with open(os.path.join(prof, "profile.env"), "w") as fh:
        fh.write(text)
    os.makedirs(os.path.join(td, "runtime"))
    with open(os.path.join(td, "runtime", "active-profile"), "w") as fh:
        fh.write("demo\n")
    return os.path.join(prof, "profile.env")


def t_profile_save_refuses_a_league_url_that_is_not_https():
    with tempfile.TemporaryDirectory() as td:
        env_path = _profile_home(td, "SHEET_ID=s\n")
        orig = m._env_base, m._runtime_base_dir
        m._env_base = lambda *a, **k: td
        m._runtime_base_dir = lambda: os.path.join(td, "runtime")
        try:
            with _simulation():
                bad = m.profile_env_write_data([{"key": "SHEET_ID", "value": "s"},
                                                {"key": "LEAGUE_SERVICE_URL", "value": "http://127.0.0.1:9/r"}])
            good = m.profile_env_write_data([{"key": "SHEET_ID", "value": "s"},
                                             {"key": "LEAGUE_SERVICE_URL", "value": "https://league.example/r"},
                                             {"key": "LEAGUE_SERVICE_KEY", "value": KEY}])
            rc = m._active_config()
        finally:
            m._env_base, m._runtime_base_dir = orig
        assert not bad["ok"] and "https" in bad["error"], "http is refused, even loopback in simulation"
        assert good["ok"], good
        with open(env_path, encoding="utf-8") as fh:
            assert "LEAGUE_SERVICE_URL=https://league.example/r" in fh.read()
        assert (rc.league_service_url, rc.league_service_key) == ("https://league.example/r", KEY)


def t_profile_save_reads_a_quoted_url_like_the_env_parser():
    for value, ok in (('"http://league.example/r"', False), ("'https://league.example/r'", True),
                      ('"https://league.example/r"', True)):
        problem = m._league_url_problem([{"key": "LEAGUE_SERVICE_URL", "value": value}])
        assert (problem == "") is ok, (value, problem)


def t_event_stop_names_each_file_it_could_not_remove():
    import driver_invite
    with tempfile.TemporaryDirectory() as d:
        path = ls.path_in(d)
        ls.store(path, "REF", ls.parse_event({"v": 1, "races": [{"race": "r1"}], "widgets": []}))
        saved = driver_invite.clear
        driver_invite.clear = lambda p: (_ for _ in ()).throw(PermissionError("locked"))
        try:
            m._stop_driver_invite(os.path.join(d, "driver-invite.json"))
            raise AssertionError("a file that could not be removed must be reported")
        except OSError as exc:
            assert "driver invite" in str(exc) and "locked" in str(exc), str(exc)
        finally:
            driver_invite.clear = saved
        assert not os.path.exists(path), "the other files go even when one could not be removed"


def t_the_key_is_never_handed_to_child_processes():
    pairs = dict(m._profile_env_pairs(_rc("https://league.example/r")))
    assert KEY not in pairs.values(), "the league key stays in profile.env"


def t_event_league_verb_parses():
    assert m.route(["event", "league"])["verb"] == "league"
    assert m.route(["event", "league", "M1"])["rest"] == ["M1"]
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):   # --help exits
        m.event_league_cmd(["--help"])
    assert out.getvalue().isascii() and "REF" in out.getvalue()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

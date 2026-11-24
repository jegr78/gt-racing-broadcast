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
    with contextlib.redirect_stdout(out):
        try:
            m.event_league_cmd(["--help"])
        except SystemExit:
            pass
    assert out.getvalue().isascii() and "REF" in out.getvalue()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

#!/usr/bin/env python3
"""The simulated league service (#1253). Run: python3 tests/test_league_sim.py"""
import json, os, subprocess, sys, tempfile, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "event-sim", "fakes"))
import league_sim  # noqa: E402

KEY = "fixture-league-key-2"
REF = league_sim.DEFAULT_REF


def _req(sim, method, path, body=None, key=KEY, base=None):
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request((base or sim.url) + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def _live(race="leg-1", **over):
    return dict({"v": 1, "ref": REF, "race": race, "mode": "race",
                 "entries": [{"pos": 1, "entry": "e1", "driver": "Ann", "team": "A", "laps": 3},
                             {"pos": 2, "entry": "e2", "driver": "Ben", "team": "B", "laps": 3}]}, **over)


def t_every_call_of_the_contract_answers():
    with league_sim.LeagueSim(key=KEY) as sim:
        st, event = _req(sim, "GET", f"/v1/event?ref={REF}")
        assert st == 200 and [r["race"] for r in event["races"]] == ["leg-1", "leg-2"]
        st, values = _req(sim, "POST", "/v1/live", _live())
        assert st == 200 and values["widgets"]["match-score"]["values"] == [
            {"label": "A", "value": "1"}, {"label": "B", "value": "0"}], values
        st, _ = _req(sim, "GET", f"/v1/result?ref={REF}&race=leg-1")
        assert st == 404, "no result before one was sent"
        result = {"v": 1, "ref": REF, "race": "leg-1", "entries": [{"pos": 1}]}
        st, status = _req(sim, "POST", "/v1/result", result)
        assert st == 200 and status["status"] == "pending"
        st, _ = _req(sim, "POST", "/v1/result", dict(result, entries=[{"pos": 1}, {"pos": 2}]))
        assert len(sim.results[(REF, "leg-1")]["entries"]) == 2, "a resend replaces the draft"
        st, status = _req(sim, "GET", f"/v1/result?ref={REF}&race=leg-1")
        assert st == 200 and status["link"].startswith("https://")
        assert [c["call"] for c in sim.calls()] == ["event", "live", "result", "result", "result-status"]


def t_a_missing_or_wrong_key_is_refused_and_never_recorded():
    with league_sim.LeagueSim(key=KEY) as sim:
        assert _req(sim, "GET", f"/v1/event?ref={REF}", key=None)[0] == 401
        assert _req(sim, "GET", f"/v1/event?ref={REF}", key="other")[0] == 401
        assert sim.unauthorized == 2 and sim.calls() == []
        st, state = _req(sim, "GET", "/_sim/state", base=sim.http_url)
        assert st == 200 and state["unauthorized"] == 2
        assert KEY not in json.dumps(state) and KEY not in json.dumps(sim.calls())


def t_live_state_off_the_contract_gets_400():
    with league_sim.LeagueSim(key=KEY) as sim:
        for body, reason in ((_live(v=2), "v must be 1"), (_live(race="leg-9"), "race"),
                             (_live(ref="X"), "unknown ref"), (_live(mode="practice"), "mode"),
                             (_live(entries=[{"pos": "1"}]), "pos")):
            st, err = _req(sim, "POST", "/v1/live", body)
            assert st == 400 and reason in err["error"], (body, err)
        assert sim.rejected == 5 and sim.calls() == []


def t_canned_answers_and_faults():
    with league_sim.LeagueSim(key=KEY) as sim:
        st, _ = _req(sim, "POST", "/_sim/answer", {"call": "event", "status": 302, "body": {},
                                                   "location": "http://127.0.0.1:1/x"}, base=sim.http_url)
        assert st == 200
        req = urllib.request.Request(sim.url + f"/v1/event?ref={REF}",
                                     headers={"Authorization": f"Bearer {KEY}"})

        class NoFollow(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a):
                return None
        try:
            urllib.request.build_opener(NoFollow()).open(req, timeout=5)
            raise AssertionError("the canned redirect must answer 302")
        except urllib.error.HTTPError as e:
            assert e.code == 302 and e.headers["Location"] == "http://127.0.0.1:1/x"
        sim.clear_answers()
        sim.fail("call:live", status=503, times=1)
        assert _req(sim, "POST", "/v1/live", _live())[0] == 503
        assert _req(sim, "POST", "/v1/live", _live())[0] == 200, "the fault is spent after one hit"


def t_cli_writes_a_ready_file_and_removes_it():
    with tempfile.TemporaryDirectory() as d:
        ready = os.path.join(d, "league.json")
        p = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "event-sim", "fakes", "league_sim.py"),
                              "--port", "0", "--key", KEY, "--ready-file", ready],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 10
            while not os.path.exists(ready) and time.monotonic() < deadline:
                time.sleep(0.05)
            with open(ready, encoding="utf-8") as fh:
                info = json.load(fh)
            assert info["env"]["RACECAST_SIM_LEAGUE_SERVICE_URL"] == info["url"]
            assert info["env"]["RACECAST_SIM_LEAGUE_SERVICE_KEY"] == KEY
        finally:
            p.terminate()
            p.communicate(timeout=10)
        assert not os.path.exists(ready), "the ready-file goes with the fake"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

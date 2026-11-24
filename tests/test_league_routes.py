#!/usr/bin/env python3
"""The relay's league result routes (#1254): status, current race, resend and the
graphic take, on the tailnet and director-gated under /console.
Run: python3 tests/test_league_routes.py"""
import json, os, sys, tempfile, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "event-sim", "fakes"))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import league_result as lr  # noqa: E402
import league_service as ls  # noqa: E402
import league_sim  # noqa: E402
import test_console_gate as g  # noqa: E402
from test_league_result import KEY, REF, _Clock, _Source, _cfg, _run_race  # noqa: E402


class _Obs:
    def __init__(self):
        self.files = []

    def set_input_file(self, name, path):
        self.files.append((name, path))
        return True, ""


def _get_raw(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return r.status, r.read(), r.headers.get("Content-Type")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type")


def _stack(d, sim):
    ls.store(ls.path_in(d), REF, ls.fetch_event(_cfg(sim), REF))
    src, wall = _Source(), _Clock()
    exp = lr.Exporter(d, lambda: (_cfg(sim), ""), src, wall=wall, clock=wall)
    gfx = os.path.join(d, "graphics")
    srv = g._serve(graphics_dir=gfx, league_exporter=exp)
    obs = _Obs()
    srv.relay._obs = obs
    return srv, srv.server_address[1], exp, src, wall, obs, gfx


def t_status_and_the_current_race_on_the_tailnet():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        srv, port, exp, src, wall, _obs, _gfx = _stack(d, sim)
        try:
            code, body = g._get(port, "/league/status")
            st = json.loads(body)
            assert code == 200 and st["event"]["current"] == "leg-1" and st["configured"], (code, st)
            assert KEY not in body, "the status never carries the key"
            code, body = g._post(port, "/league/race", body={"race": "leg-2"})
            assert code == 200 and json.loads(body)["current"] == "leg-2", body
            code, body = g._post(port, "/league/race", body={"race": "leg-9"})
            assert code == 400 and "does not list" in json.loads(body)["error"], body
            code, body = g._post(port, "/league/race", body={"race": 5})
            assert code == 400 and json.loads(body)["error"] == "race must be text", body
        finally:
            srv.shutdown()


def t_send_again_from_the_panel_and_its_errors():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        srv, port, exp, src, wall, _obs, _gfx = _stack(d, sim)
        try:
            code, body = g._post(port, "/league/result/send", body={})
            assert code == 400 and "no result" in json.loads(body)["error"], "nothing finished yet"
            _run_race(exp, src, wall)
            exp.step()
            code, body = g._post(port, "/league/result/send", body={})
            assert code == 200 and json.loads(body)["rebuilt"] is True, body
            exp.step()
            assert len(sim.calls(call="result")) == 2
        finally:
            srv.shutdown()


def t_a_graphic_is_shown_and_taken_only_by_the_directors_action():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY, graphic=True) as sim:
        srv, port, exp, src, wall, obs, gfx = _stack(d, sim)
        try:
            code, _b = g._get(port, "/league/graphic?race=leg-1")
            assert code == 404, "no graphic before a result"
            _run_race(exp, src, wall)
            exp.step()
            assert obs.files == [] and not os.path.exists(gfx), "a fetched graphic never goes on air by itself"
            code, raw, ctype = _get_raw(port, "/league/graphic?race=leg-1")
            assert code == 200 and ctype == "image/png" and raw == sim.graphic_body, (code, ctype)
            code, _b = g._get(port, "/league/graphic?race=../../x")
            assert code == 404
            code, body = g._post(port, "/league/graphic/take", body={"race": "leg-2"})
            assert code == 404, body
            code, body = g._post(port, "/league/graphic/take", body={"race": "leg-1"})
            assert code == 200 and json.loads(body)["obs"] is True, body
            dest = os.path.join(gfx, "Race Results.png")
            assert obs.files == [("Race Results", dest)], obs.files
            with open(dest, "rb") as fh:
                assert fh.read() == sim.graphic_body
        finally:
            srv.shutdown()


def t_the_panel_loads_the_graphic_through_the_console_mount():
    with open(os.path.join(ROOT, "src", "director", "director-panel.html"), encoding="utf-8") as fh:
        html = fh.read()
    assert 'RC_API("/league/graphic")' in html and '"/league/graphic?race=' not in html, \
        "behind Funnel the graphic preview resolves under /console like every other panel request"


def t_a_taken_graphic_logs_no_line_break_from_the_request():
    import logging
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY, graphic=True) as sim:
        srv, port, exp, src, wall, _obs, _gfx = _stack(d, sim)
        lines = []
        handler = logging.Handler()
        handler.emit = lambda rec: lines.append(rec.getMessage())
        g.m.LOG.addHandler(handler)
        level = g.m.LOG.level
        g.m.LOG.setLevel(logging.INFO)
        try:
            _run_race(exp, src, wall)
            exp.step()
            staged = exp.staged_graphic("leg-1")
            exp.staged_graphic = lambda race: staged      # a lookup that let any text through
            code, _b = g._post(port, "/league/graphic/take", body={"race": "leg-1\nFAKE relay line"})
            assert code == 200
        finally:
            g.m.LOG.removeHandler(handler)
            g.m.LOG.setLevel(level)
            srv.shutdown()
        taken = [ln for ln in lines if "Race Results graphic now" in ln]
        assert taken and all("\n" not in ln and "\r" not in ln for ln in taken), taken


def t_the_league_routes_are_director_only_under_console():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        srv, port, *_rest = _stack(d, sim)
        try:
            assert g._get(port, "/console/league/status", g._tok("alice"))[0] == 403, "a commentator"
            assert g._get(port, "/console/league/status", g._tok("bob"))[0] == 200, "the director"
            code, _b = g._post(port, "/console/league/race", g._tok("dave"), body={"race": "leg-2"})
            assert code == 403, "Race Control sets no race"
        finally:
            srv.shutdown()


def t_without_an_exporter_the_routes_say_so():
    srv = g._serve()
    port = srv.server_address[1]
    try:
        assert g._get(port, "/league/status")[0] == 404
        assert g._post(port, "/league/result/send", body={})[0] == 404
    finally:
        srv.shutdown()


def t_the_dashboard_tick_hands_each_race_state_to_the_export():
    class _Relay:
        _warn_tick_error = g.m.Relay._warn_tick_error
        _tick_ok = g.m.Relay._tick_ok

    class _Svc:
        states = ["racing", "finished", None]
        ticks = 0

        def tick(self):
            self.ticks += 1

        def race_state(self):
            return self.states[self.ticks - 1]

    class _League:
        seen = []

        def observe(self, state):
            self.seen.append(state)
            if state is None:
                raise OSError("disk full")

    class _Stop:
        n = 3

        def wait(self, _s):
            self.n -= 1
            return self.n < 0
    svc, league = _Svc(), _League()
    g.m.dashboard_ticks(_Relay(), svc, _Stop(), 0, league)
    assert league.seen == ["racing", "finished", None] and svc.ticks == 3, \
        f"every tick reaches the export and its failure never stops the tick: {league.seen}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

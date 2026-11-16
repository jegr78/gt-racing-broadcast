#!/usr/bin/env python3
"""The event's GT7 layout over HTTP (#1129): /event/track for the tailnet and the
Director Panel, /console/race-control/track for the Race Control desk, the
/status block and the health line. Run: python3 tests/test_track_pick_routes.py"""
import json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "scripts"))
import test_console_gate as g  # noqa: E402  the shared relay + console-token harness
import gt7_track_pick as tp  # noqa: E402

m = g.m


class _Tracks:
    CAT = {"aaa001": {"id": "aaa001", "track": "Oval", "layout": "Full", "reverse": False},
           "bbb001": {"id": "bbb001", "track": "Ring", "layout": "Full", "reverse": False}}

    def name(self, oid):
        return dict(self.CAT[oid]) if oid in self.CAT else None

    def has_downloaded_line(self, oid):
        return oid == "aaa001"

    def layouts(self):
        return [dict(v) for v in self.CAT.values()]


class _Telemetry:
    def __init__(self):
        self.picks = []
        self.live = {"track": None, "recognised": None, "mismatch": False}

    def set_track_pick(self, info):
        self.picks.append(info)

    def track_status(self):
        return self.live


def _serve():
    tmp = tempfile.TemporaryDirectory()
    tele = _Telemetry()
    ctl = tp.Controller(tp.Store(tp.path_in(tmp.name), _Tracks()), tele)
    srv = g._serve(track_pick=ctl)
    return srv, srv.server_address[1], ctl, tele, tmp


def t_tailnet_route_lists_sets_and_clears_the_pick():
    srv, port, ctl, tele, tmp = _serve()
    try:
        code, body = g._get(port, "/event/track")
        assert code == 200 and "layouts" not in json.loads(body), "the poll answer stays small"
        code, body = g._get(port, "/event/track?layouts=1")
        data = json.loads(body)
        assert code == 200 and data["pick"] is None, (code, body)
        assert [r["id"] for r in data["layouts"]] == ["aaa001", "bbb001"], data["layouts"]
        code, body = g._post(port, "/event/track", body={"id": "bbb001"})
        data = json.loads(body)
        assert code == 200 and data["ok"] is True and data["pick"]["id"] == "bbb001", body
        assert data["pick"]["warning"], "a layout without a line must warn"
        assert tele.picks[-1]["id"] == "bbb001", "the pick must reach the live telemetry"
        assert json.loads(g._get(port, "/status")[1])["track_pick"]["pick"]["id"] == "bbb001"
        code, body = g._post(port, "/event/track", body={"id": "nope00"})
        assert code == 400 and "unknown track layout" in body, (code, body)
        code, body = g._post(port, "/event/track", body={"id": 7})
        assert code == 400 and "layout id or null" in body, (code, body)
        code, body = g._post(port, "/event/track", body={"id": None})
        assert code == 200 and json.loads(body)["pick"] is None, body
        assert ctl.store.current() is None
    finally:
        srv.shutdown(); tmp.cleanup()


def t_console_routes_are_role_gated():
    srv, port, ctl, _tele, tmp = _serve()
    try:
        # dave is race_control only, bob is director, alice is a commentator.
        code, body = g._post(port, "/console/race-control/track", g._tok("dave"), body={"id": "aaa001"})
        assert code == 200 and json.loads(body)["pick"]["id"] == "aaa001", (code, body)
        code, body = g._get(port, "/console/race-control/track?layouts=1", g._tok("dave"))
        assert code == 200 and len(json.loads(body)["layouts"]) == 2, (code, body)
        code, body = g._get(port, "/console/race-control/data", g._tok("dave"))
        assert json.loads(body)["track_pick"]["pick"]["id"] == "aaa001", body
        code, body = g._post(port, "/console/event/track", g._tok("bob"), body={"id": "bbb001"})
        assert code == 200 and json.loads(body)["pick"]["id"] == "bbb001", (code, body)
        for path in ("/console/race-control/track", "/console/event/track"):
            code, body = g._post(port, path, g._tok("alice"), body={"id": "aaa001"})
            assert code == 403 and "forbidden" in body, (path, code, body)
        code, _body = g._post(port, "/console/event/track", g._tok("dave"), body={"id": "aaa001"})
        assert code == 403, "race control reaches the layout only through its own desk route"
        assert ctl.store.current()["id"] == "bbb001", "a refused request must not change the pick"
    finally:
        srv.shutdown(); tmp.cleanup()


def t_mismatch_is_a_quiet_health_yellow():
    reason = "GT7 track: the lap matches Ring Full, not the picked Oval Full."
    h = m.aggregate_health({"track_mismatch": reason})
    assert h["level"] == "yellow" and reason in h["reasons"], h
    assert m.aggregate_health({"track_mismatch": None})["level"] == "green"
    tmp = tempfile.TemporaryDirectory()
    try:
        tele = _Telemetry()
        ctl = tp.Controller(tp.Store(tp.path_in(tmp.name), _Tracks()), tele)
        ctl.set("aaa001")
        tele.live = {"track": None, "mismatch": True,
                     "recognised": {"id": "bbb001", "track": "Ring", "layout": "Full", "reverse": False}}
        relay = m.Relay(g._FakeSource(g._URLS8, [("https://youtu.be/a", "Alice", "1", 2)]),
                        [53001, 53002], g.LOGDIR)
        relay.track_pick = ctl
        detect = m.detect_tailscale_ip
        m.detect_tailscale_ip = lambda: "100.64.0.1"
        try:
            assert "Ring Full" in relay._health_facts(0)["track_mismatch"]
            h = relay._refresh_health(0)
        finally:
            m.detect_tailscale_ip = detect
        assert h["level"] == "yellow" and any("Ring Full" in r for r in h["reasons"]), h
        assert h["notify_level"] == "green", f"the mismatch must not page Discord: {h}"
    finally:
        tmp.cleanup()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

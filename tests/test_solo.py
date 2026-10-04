#!/usr/bin/env python3
"""Stdlib unit checks for solo relay mode (#302). Run: python3 tests/test_solo.py"""
import importlib.util, os, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOGDIR = tempfile.mkdtemp(prefix="racecast-test-solo-")
spec = importlib.util.spec_from_file_location(
    "irofeeds_solo", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def _solo_relay():
    return m.Relay(None, [], LOGDIR, solo=True, sheet_id="abc", league_name="Solo")


def t_solo_relay_has_no_feeds():
    r = _solo_relay()
    assert r.solo is True
    assert r.feeds == {}
    assert r.race_source is None and r.qual_source is None
    assert r.pov is None                      # no pov_source passed


def t_endurance_relay_still_has_ab_and_solo_false():
    class _Src:
        def get(self): return ["u1", "u2"]
        def get_rows(self): return [("u1", "", "", 1), ("u2", "", "", 2)]
        def refresh(self, timeout=None): pass
        def health(self): return {"ok": True}
    r = m.Relay(_Src(), [53001, 53002], LOGDIR)
    assert r.solo is False
    assert set(r.feeds) == {"A", "B"}


def t_solo_status_is_feedless_and_shaped():
    r = _solo_relay()
    s = r.status()
    assert s["mode"] == "solo" and s["solo"] is True
    assert s["feeds"] == {}
    assert s["live"] == {"feed": None, "stint": None, "mode": "solo"}
    assert s["league"]["sheet_id"] == "abc"
    assert "health" in s and "obs" in s


def t_solo_feed_controls_are_guarded_not_crashing():
    r = _solo_relay()
    assert r.live_feed() is None
    assert r.on_air_row_idx() == 0
    assert r.live_row_map() == {}
    assert r.live_schedule_row() is None
    for call in (r.next_auto, r.reload, lambda: r.set_stint(2),
                 lambda: r.set_mode("qualifying")):
        out = call()
        assert out.get("solo") is True and "error" in out


def t_solo_heartbeat_paths_never_crash():
    import time as _t
    r = _solo_relay()
    now = _t.time()
    # the heartbeat body constituents must not raise in solo, where there are no A/B feeds
    r._sample_connectivity()
    r._refresh_health(now)
    snap = r._health_snapshot(now)          # feed fields NULL, POV/system fields present
    assert snap["feed_a_state"] is None and snap["feed_b_state"] is None
    assert snap["live_feed"] is None
    r.auto_failover = True                   # even opted-in, solo must early-return
    r._maybe_auto_failover(now)              # must not KeyError on feeds[None]


def _get_json(srv, path, body=None):
    import json, urllib.error, urllib.request
    url = "http://127.0.0.1:%d%s" % (srv.server_address[1], path)
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _solo_server(**kw):
    import threading
    srv = m.ThreadingHTTPServer(("127.0.0.1", 0), m.make_handler(_solo_relay(), **kw))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def t_solo_schedule_rows_are_empty():
    assert m.schedule_rows(_solo_relay()) == [], "solo has no schedule source, so no rows"


def t_solo_schedule_data_is_an_empty_schedule_not_a_500():
    srv = _solo_server()
    try:
        status, body = _get_json(srv, "/schedule/data")
    finally:
        srv.shutdown()
    assert status == 200, (status, body)
    assert body["rows"] == [] and body["source"] is None, body


def t_solo_cue_send_reaches_the_store_not_a_500():
    store = m.CueStore(os.path.join(tempfile.mkdtemp(), "cues.json"))
    srv = _solo_server(cue_store=store)
    try:
        status, body = _get_json(srv, "/cues/send",
                                 {"target": "all", "level": "info", "text": "wrap up"})
    finally:
        srv.shutdown()
    assert status == 200, (status, body)
    assert "error" not in body, body


def t_handlers_read_the_schedule_through_the_solo_safe_accessor():
    with open(os.path.join(ROOT, "src", "relay", "racecast-feeds.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert "relay.source.get_rows()" not in src, \
        "relay.source is None in solo; handlers must call schedule_rows(relay)"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

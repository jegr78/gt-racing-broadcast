#!/usr/bin/env python3
"""Endpoint checks for the GT7 telemetry routes. Run: python3 tests/test_telemetry_endpoints.py

Exercises the store contract behind /telemetry/data and /telemetry/trace: when
telemetry_store is None the routes 404, and when a store exists they return its
data()/trace() shape. The t_telemetry_* tests check the store directly, which is
the shape the do_GET routes hand back via self._send. The t_route_* tests exercise
the HTTP route dispatch through make_handler over a real ThreadingHTTPServer, so a
route typo or a missing None-guard fails here, not just a store-shape check."""
import importlib.util, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "irofeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

# Never reach the machine's OBS: tests/_obs_guard.py answers every OBS call as
# unreachable and fails any real connection attempt.
import _obs_guard  # noqa: E402
_obs_guard.install(m)


def t_telemetry_data_shape():
    store = m.gt7_telemetry.TelemetryStore(None, units="metric")
    payload = store.data()
    assert set(payload) >= {"speed", "tyres", "fuel", "units", "has_reference"}
    assert len(payload["tyres"]) == 4


def t_telemetry_trace_shape():
    store = m.gt7_telemetry.TelemetryStore(None)
    assert store.trace(10) == []          # empty before any packet


def _serve(telemetry_store, relay=None):
    """Stand up make_handler over a real ThreadingHTTPServer on an ephemeral port,
    mirroring tests/test_cockpit.py's harness. Returns (server, get); caller must
    srv.shutdown() in a finally block."""
    import threading as _t
    import urllib.error
    from urllib.request import Request, urlopen

    class _Relay:
        pass

    handler = m.make_handler(relay or _Relay(), telemetry_store=telemetry_store)
    srv = m.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    _t.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def _read(req):
        try:
            with urlopen(req, timeout=5) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def get(path, headers=None):
        return _read(Request(base + path, headers=dict(headers or {})))

    return srv, get


def t_route_data_404_without_store():
    # Endurance, meaning non-solo, has no telemetry_store, so the route must 404.
    srv, get = _serve(None)
    try:
        status, _, body = get("/telemetry/data")
        assert status == 404, status
    finally:
        srv.shutdown()


def t_route_data_and_trace_200_with_store():
    store = m.gt7_telemetry.TelemetryStore(None, units="metric")
    srv, get = _serve(store)
    try:
        import json
        s1, _, b1 = get("/telemetry/data")
        assert s1 == 200, s1
        d = json.loads(b1)
        assert {"speed", "tyres", "fuel", "units", "has_reference"} <= set(d)
        assert len(d["tyres"]) == 4
        s2, _, b2 = get("/telemetry/trace")
        assert s2 == 200, s2
        assert "samples" in json.loads(b2)
    finally:
        srv.shutdown()


def t_telemetry_visibility_defaults_on_and_persists():
    # The producer hides the block for the lobby or a replay; that choice lives in
    # its own file so it survives a relay restart (the reference-lap file does not).
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        view = os.path.join(d, "telemetry-view.json")
        store = m.gt7_telemetry.TelemetryStore(None, view_path=view)
        assert store.visible() is True and store.data()["visible"] is True
        assert store.set_visible(False) == {"visible": False}
        again = m.gt7_telemetry.TelemetryStore(None, view_path=view, reset=True)
        assert again.visible() is False, "hidden must survive a relay restart"
        assert again.toggle() == {"visible": True}
        assert m.gt7_telemetry.TelemetryStore(None, view_path=view).visible() is True


def t_telemetry_visibility_bad_file_means_visible():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        view = os.path.join(d, "telemetry-view.json")
        with open(view, "w", encoding="utf-8") as fh:
            fh.write("not json")
        assert m.gt7_telemetry.TelemetryStore(None, view_path=view).visible() is True


def t_route_visibility_show_hide_toggle():
    import json, tempfile
    with tempfile.TemporaryDirectory() as d:
        store = m.gt7_telemetry.TelemetryStore(
            None, view_path=os.path.join(d, "telemetry-view.json"))
        srv, get = _serve(store)
        try:
            assert json.loads(get("/telemetry/hide")[2]) == {"visible": False}
            assert json.loads(get("/telemetry/data")[2])["visible"] is False
            assert json.loads(get("/telemetry/toggle")[2]) == {"visible": True}
            assert json.loads(get("/telemetry/hide")[2]) == {"visible": False}
            assert json.loads(get("/telemetry/show")[2]) == {"visible": True}
        finally:
            srv.shutdown()


def t_route_visibility_404_without_store():
    srv, get = _serve(None)
    try:
        for verb in ("show", "hide", "toggle"):
            assert get("/telemetry/" + verb)[0] == 404, verb
    finally:
        srv.shutdown()


def t_status_reports_telemetry_visibility():
    # The Director Panel lights its TELEMETRY toggle from /status.
    import json

    class _StatusRelay:
        def status(self):
            return {}

    store = m.gt7_telemetry.TelemetryStore(None)
    srv, get = _serve(store, relay=_StatusRelay())
    try:
        assert json.loads(get("/status")[2])["telemetry"] == {"visible": True, "car": None}
    finally:
        srv.shutdown()
    srv, get = _serve(None, relay=_StatusRelay())
    try:
        assert "telemetry" not in json.loads(get("/status")[2])
    finally:
        srv.shutdown()


def t_telemetry_toggle_is_atomic_under_concurrency():
    # The panel key and the Companion button can fire at once: an even number of
    # toggles from many threads must land back on the starting state.
    import threading
    store = m.gt7_telemetry.TelemetryStore(None)
    def worker():
        for _ in range(250):
            store.toggle()
    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert store.visible() is True


def t_status_and_data_name_the_car():
    """The Director Panel shows the current car from /status and the HUD from
    /telemetry/data, both resolved through the shipped car tables. (#713)"""
    import json
    from test_gt7_fixture import EXT_TCS_HEX

    class _StatusRelay:
        def status(self):
            return {}

    store = m.gt7_telemetry.TelemetryStore(None, cars=m.gt7_cars.CarDB())
    plain = m.gt7_crypto.decrypt_packet(bytes.fromhex(EXT_TCS_HEX))
    store.update(m.gt7_telemetry.parse_packet(plain), 1.0)
    srv, get = _serve(store, relay=_StatusRelay())
    try:
        car = json.loads(get("/status")[2])["telemetry"]["car"]
        assert (car["id"], car["maker"], car["group"]) == (365, "Alfa Romeo", "Gr.4"), car
        assert json.loads(get("/telemetry/data")[2])["car"] == car
    finally:
        srv.shutdown()


def t_telemetry_loop_requests_extended_format_and_switches_once():
    """The relay loop sends the '~' heartbeat. Fed a base 'A' stream it goes silent
    until the stream lapses, then requests '~' again, exactly one pause. (#711)"""
    import socket as _socket
    import threading as _t
    from test_gt7_fixture import PKT_THROTTLE_HEX
    ps_ip = "100.64.0.7"                      # test constant, never a real console
    clock = [0.0]
    stop = _t.Event()
    packets = [bytes.fromhex(PKT_THROTTLE_HEX)] * 40      # 4 s of 'A' at the fake rate

    class FakeSock:
        sent = []

        def __init__(self, *a, **kw):
            pass

        def setsockopt(self, *a):
            pass

        def bind(self, addr):
            pass

        def settimeout(self, s):
            pass

        def close(self):
            pass

        def sendto(self, data, addr):
            FakeSock.sent.append((round(clock[0], 1), data, addr))

        def recvfrom(self, n):
            if clock[0] >= 12.0:
                stop.set()
            if packets:
                clock[0] += 0.1
                return packets.pop(), (ps_ip, 33740)
            clock[0] += 1.0                   # a recv timeout: the stream is quiet
            raise _socket.timeout()

    store = m.gt7_telemetry.TelemetryStore(None)
    real = m.socket.socket
    m.socket.socket = FakeSock
    try:
        m._telemetry_loop(store, ps_ip, stop, clock=lambda: clock[0])
    finally:
        m.socket.socket = real
    beats = [(t, d) for t, d, addr in FakeSock.sent]
    assert all(addr == (ps_ip, m.GT7_SEND_PORT) for _t0, _d, addr in FakeSock.sent)
    assert [d for _t0, d in beats] == [b"~", b"~"], beats
    assert beats[0][0] == 0.0
    assert beats[1][0] >= 4.0 + m.gt7_telemetry.HEARTBEAT_LAPSE_GAP_S   # after the lapse
    assert store.trace(10)                    # the 'A' packets still fed the HUD


def _recorder(d, default=False):
    return m.gt7_recording.RecordControl(
        os.path.join(d, "rec"), os.path.join(d, "telemetry-record.json"), default)


def t_route_record_start_stop_toggle():
    import json, tempfile
    with tempfile.TemporaryDirectory() as d:
        store = m.gt7_telemetry.TelemetryStore(None, recorder=_recorder(d))
        srv, get = _serve(store)
        try:
            assert json.loads(get("/telemetry/record/start")[2])["active"] is True
            assert json.loads(get("/telemetry/record/toggle")[2])["active"] is False
            assert json.loads(get("/telemetry/record/stop")[2])["active"] is False
            assert get("/telemetry/record/bogus")[0] == 404
        finally:
            srv.shutdown()
            store.recorder.close()


def t_route_record_404_without_store_or_recorder():
    srv, get = _serve(None)
    try:
        assert get("/telemetry/record/start")[0] == 404
    finally:
        srv.shutdown()
    srv, get = _serve(m.gt7_telemetry.TelemetryStore(None))
    try:
        assert get("/telemetry/record/start")[0] == 404
    finally:
        srv.shutdown()


def t_status_reports_record_block():
    import json, tempfile

    class _StatusRelay:
        def status(self):
            return {}

    with tempfile.TemporaryDirectory() as d:
        store = m.gt7_telemetry.TelemetryStore(None, recorder=_recorder(d, default=True))
        srv, get = _serve(store, relay=_StatusRelay())
        try:
            rec = json.loads(get("/status")[2])["telemetry"]["record"]
            assert rec["active"] is True and rec["file"] is None and rec["error"] is None, rec
            assert rec["elapsed_s"] is None, "no open file yet -> no elapsed time"
        finally:
            srv.shutdown()
            store.recorder.close()


def t_telemetry_loop_feeds_the_recorder():
    import socket as _socket
    import threading as _t
    from test_gt7_fixture import EXT_TCS_HEX
    ps_ip = "100.64.0.7"
    stop = _t.Event()
    packets = [bytes.fromhex(EXT_TCS_HEX)] * 3
    got = []

    class FakeRecorder:
        def put(self, wall_ts, kind, plain):
            got.append((kind, len(plain)))

    class FakeSock:
        def __init__(self, *a, **kw): pass
        def setsockopt(self, *a): pass
        def bind(self, addr): pass
        def settimeout(self, s): pass
        def close(self): pass
        def sendto(self, data, addr): pass
        def recvfrom(self, n):
            if packets:
                return packets.pop(), (ps_ip, 33740)
            stop.set()
            raise _socket.timeout()

    store = m.gt7_telemetry.TelemetryStore(None, recorder=FakeRecorder())
    real = m.socket.socket
    m.socket.socket = FakeSock
    try:
        m._telemetry_loop(store, ps_ip, stop)
    finally:
        m.socket.socket = real
    assert got == [("~", 0x158)] * 3, got


def t_telemetry_store_record_swallows_a_raising_recorder():
    # A bad recorder (its put() raises) must never stop the UDP telemetry loop.
    class BoomRecorder:
        def put(self, wall_ts, kind, plain):
            raise RuntimeError("disk full")

    store = m.gt7_telemetry.TelemetryStore(None, recorder=BoomRecorder())
    store.record(1.0, "A", b"x")          # must not raise


def t_status_reports_track_only_with_track_db():
    import json

    class _StatusRelay:
        def status(self):
            return {}

    class _Tracks:
        def match(self, points, length_m):
            return None

    store = m.gt7_telemetry.TelemetryStore(None, tracks=_Tracks())
    srv, get = _serve(store, relay=_StatusRelay())
    try:
        tel = json.loads(get("/status")[2])["telemetry"]
        assert "track" in tel and tel["track"] is None, tel
        assert json.loads(get("/telemetry/data")[2])["track"] is None
    finally:
        srv.shutdown()


def t_store_reload_data_swaps_cars_and_tracks():
    class _Cars:
        def lookup(self, car_id):
            return {"id": car_id, "maker": "New", "name": "Car", "group": None}
    store = m.gt7_telemetry.TelemetryStore(None)
    assert not store.has_tracks()
    store.reload_data(_Cars(), object())
    assert store.has_tracks() and store._lookup_car(5)["maker"] == "New"


def t_gt7_data_refresh_reloads_store_only_when_changed():
    import tempfile
    calls = []

    class _Store:
        def reload_data(self, cars, tracks):
            calls.append((cars, tracks))

    with tempfile.TemporaryDirectory() as d:
        bundled = os.path.join(ROOT, "src", "assets", "gt7")
        m._gt7_data_refresh(_Store(), d, bundled,
                            update=lambda base: {"checked": True, "changed": False, "files": {}})
        assert calls == [], "an unchanged update must not reload the store"
        m._gt7_data_refresh(_Store(), d, bundled,
                            update=lambda base: {"checked": True, "changed": True,
                                                 "files": {"cars.csv": "updated"}})
        assert len(calls) == 1 and len(calls[0][0]) > 400, "reloaded with the bundled car tables"
        assert isinstance(calls[0][1], m.gt7_tracks.TrackDB), calls[0][1]

        def boom(base):
            raise RuntimeError("offline")
        m._gt7_data_refresh(_Store(), d, bundled, update=boom)    # never raises
        assert len(calls) == 1, "a failed update keeps the loaded data"


def t_zz_no_test_reached_a_real_obs():
    # Sorted last: no test in this file may have attempted a real OBS connection.
    assert _obs_guard.CALLS == [], _obs_guard.CALLS[:3]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

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


def t_telemetry_data_shape():
    store = m.gt7_telemetry.TelemetryStore(None, units="metric")
    payload = store.data()
    assert set(payload) >= {"speed", "tyres", "fuel", "units", "has_reference"}
    assert len(payload["tyres"]) == 4


def t_telemetry_trace_shape():
    store = m.gt7_telemetry.TelemetryStore(None)
    assert store.trace(10) == []          # empty before any packet


def _serve(telemetry_store):
    """Stand up make_handler over a real ThreadingHTTPServer on an ephemeral port,
    mirroring tests/test_cockpit.py's harness. Returns (server, get); caller must
    srv.shutdown() in a finally block."""
    import threading as _t
    import urllib.error
    from urllib.request import Request, urlopen

    class _Relay:
        pass

    handler = m.make_handler(_Relay(), telemetry_store=telemetry_store)
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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

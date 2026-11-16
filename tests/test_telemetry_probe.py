#!/usr/bin/env python3
"""The driver telemetry probe ingest (#1110). Run: python3 tests/test_telemetry_probe.py"""
import os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "scripts"))
import gt7_crypto  # noqa: E402
import telemetry_probe as tp  # noqa: E402
from test_gt7_telemetry import _packet  # noqa: E402


def _wire(pid, **kw):
    plain = bytearray(_packet(**kw))
    struct.pack_into("<i", plain, 0x70, pid)
    return gt7_crypto.encrypt(bytes(plain), "A", iv=pid)


class _Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _probe(**kw):
    clock = _Clock()
    return tp.Probe("k" * 22, clock=clock, wall=lambda: 1_700_000_000.0 + clock.t, **kw), clock


def t_batch_round_trip_and_limits():
    packets = [b"x" * 296, b"y" * 344]
    assert tp.decode_batch(tp.encode_batch(packets)) == packets
    for bad, why in ((b"XXXX", "magic"), (tp.MAGIC + b"\x01", "truncated length"),
                     (tp.MAGIC + struct.pack(">H", 10) + b"abc", "truncated packet"),
                     (tp.MAGIC + struct.pack(">H", tp.MAX_PACKET + 1) + bytes(tp.MAX_PACKET + 1), "oversized"),
                     (tp.encode_batch([b"p"] * (tp.MAX_BATCH + 1)), "too many")):
        try:
            tp.decode_batch(bad)
        except tp.ProbeError as exc:
            assert exc.status == 400, (why, exc.status)
        else:
            raise AssertionError(f"{why} must be refused")


def t_ingest_counts_loss_decode_failures_and_keeps_samples():
    probe, clock = _probe()
    batch = [_wire(1, lap=2, speed_mps=50.0), _wire(2, lap=2), _wire(5, lap=2), b"\0" * 296]
    reply = probe.ingest("Driver A", tp.encode_batch(batch), {"received": 4, "unknown": "x"})
    assert reply == {"ok": True, "rate_hz": 20, "accepted": 3}, reply
    clock.t += 0.05
    probe.ingest("Driver A", tp.encode_batch([_wire(6, lap=3)]), None)
    snap = probe.snapshot()["senders"]["Driver A"]
    assert snap["packets"]["received"] == 4 and snap["packets"]["lost"] == 2, snap["packets"]
    assert snap["packets"]["decrypt_failed"] == 1, snap["packets"]
    assert snap["batches"] == 2 and snap["kinds"] == {"A": 4}, snap
    assert snap["sender_stats"] == {"received": 4}, "only numeric sender counters are kept"
    assert snap["samples"][0]["pid"] == 1 and snap["samples"][0]["lap"] == 2, snap["samples"][0]
    assert round(snap["samples"][0]["speed_kmh"]) == 180, snap["samples"][0]
    assert abs(snap["max_batch_gap_s"] - 0.05) < 1e-9, snap["max_batch_gap_s"]


def t_totals_survive_an_upload_stall_and_a_replay_loop():
    probe, clock = _probe()
    probe.ingest("one", tp.encode_batch([_wire(101), _wire(102), _wire(104)]), None)
    clock.t += 6.0                       # an upload stall longer than a stream's idle limit
    probe.ingest("one", tp.encode_batch([_wire(110), _wire(111)]), None)
    clock.t += 1.0                       # a looping replay: the packet ids jump far back
    probe.ingest("one", tp.encode_batch([_wire(1), _wire(3)]), None)
    snap = probe.snapshot()["senders"]["one"]
    assert snap["stream"] == 3, snap["stream"]
    assert snap["packets"]["received"] == 7 and snap["packets"]["lost"] == 2, \
        f"totals must cover every stream: {snap['packets']}"
    assert snap["current_stream"]["received"] == 2 and snap["current_stream"]["lost"] == 1, snap
    assert snap["packets_per_s"] == 1.0, f"the rate uses the totals: {snap['packets_per_s']}"


def t_sample_ring_is_bounded_and_senders_are_capped():
    probe, _clock = _probe(samples=3, max_senders=2)
    probe.ingest("one", tp.encode_batch([_wire(i) for i in range(1, 31)]), None)
    one = probe.snapshot()["senders"]["one"]
    assert one["packets"]["received"] == 30, "every packet is counted"
    assert [s["pid"] for s in one["samples"]] == [18, 24, 30], \
        f"every {tp.SAMPLE_EVERY}th packet is a sample, the ring keeps the newest: {one['samples']}"
    probe.ingest("two", tp.encode_batch([_wire(1)]), None)
    try:
        probe.ingest("three", tp.encode_batch([_wire(1)]), None)
    except tp.ProbeError as exc:
        assert exc.status == 429 and "senders" in str(exc), (exc.status, exc)
    else:
        raise AssertionError("a sender beyond the cap must be refused")


def t_sender_names_and_keys_are_checked():
    probe, _clock = _probe()
    assert probe.check_key("k" * 22) and not probe.check_key("k" * 21) and not probe.check_key(None)
    for bad in ("", "x" * 33, "a/b", None, "Fahreré"):
        try:
            probe.ingest(bad, tp.encode_batch([_wire(1)]), None)
        except tp.ProbeError as exc:
            assert exc.status == 400 and "sender name" in str(exc), (bad, exc)
        else:
            raise AssertionError(f"{bad!r} must be refused")
    probe.reset()
    assert probe.snapshot()["senders"] == {}


def _post(port, path, body, headers):
    import urllib.request, urllib.error
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, method="POST",
                                 headers=dict({"Content-Type": "application/octet-stream"}, **headers))
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def t_relay_ingest_needs_the_probe_key_and_reports_on_the_tailnet():
    import json
    import test_console_gate as g
    probe = tp.Probe("probe-key-0123456789")
    srv = g._serve(telemetry_probe_obj=probe)
    port = srv.server_address[1]
    try:
        body = tp.encode_batch([_wire(1), _wire(2)])
        code, text = _post(port, "/console/probe/ingest?sender=Car%207", body,
                           {"X-Probe-Key": probe.key, "X-Sender-Stats": json.dumps({"received": 2})})
        assert code == 200 and json.loads(text) == {"ok": True, "rate_hz": 20, "accepted": 2}, (code, text)
        code, text = _post(port, "/console/probe/ingest?sender=x", body, {"X-Probe-Key": "wrong"})
        assert code == 403 and "forbidden" in text, (code, text)
        code, text = _post(port, "/console/probe/ingest?sender=x", b"nope", {"X-Probe-Key": probe.key})
        assert code == 400 and "not a telemetry batch" in text, (code, text)
        code, text = g._get(port, "/telemetry/probe")
        doc = json.loads(text)
        assert code == 200 and doc["key"] == probe.key and doc["path"] == "/console/probe/ingest", doc
        assert doc["senders"]["Car 7"]["packets"]["received"] == 2, doc["senders"]
        assert doc["senders"]["Car 7"]["sender_stats"] == {"received": 2}
        code, _text = _post(port, "/telemetry/probe/reset", b"{}", {"Content-Type": "application/json"})
        assert code == 200 and probe.snapshot()["senders"] == {}
    finally:
        srv.shutdown()
    off = g._serve()
    try:
        code, text = _post(off.server_address[1], "/console/probe/ingest?sender=x", b"RCT1", {"X-Probe-Key": "k"})
        assert code == 404, "without the probe the ingest does not exist"
        assert g._get(off.server_address[1], "/telemetry/probe")[0] == 404
    finally:
        off.shutdown()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

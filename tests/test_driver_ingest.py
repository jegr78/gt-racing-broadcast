#!/usr/bin/env python3
"""The driver telemetry ingest: connections, limits and counting (#1111).
Run: python3 tests/test_driver_ingest.py"""
import os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "scripts"))
import driver_ingest as ing  # noqa: E402
import gt7_crypto  # noqa: E402
import telemetry_probe as tp  # noqa: E402
from test_gt7_telemetry import _packet  # noqa: E402


def _wire(pid, **kw):
    plain = bytearray(_packet(**kw))
    struct.pack_into("<i", plain, 0x70, pid)
    return gt7_crypto.encrypt(bytes(plain), "A", iv=pid)


def _batch(*pids):
    return tp.encode_batch([_wire(p, lap=2) for p in pids])


def _reg(conn, entry="Team A #1", name="Max", segment=None):
    return {"conn": conn, "entry": entry, "name": name, "segment": segment}


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _hub(multi_lobby=False):
    clock = _Clock()
    return ing.Hub(multi_lobby=lambda: multi_lobby, clock=clock), clock


def _refused(fn, status, text):
    try:
        fn()
    except ing.IngestError as exc:
        assert exc.status == status and text in str(exc), (exc.status, str(exc))
    else:
        raise AssertionError(f"expected {status} {text!r}")


def t_ingest_counts_per_connection_and_sets_the_rate():
    hub, clock = _hub()
    reply = hub.ingest(_reg("c1"), _batch(1, 2, 4), {"received": 3})
    assert reply == {"ok": True, "v": 1, "rate_hz": 20, "accepted": 3}, reply
    clock.t += 0.05
    hub.ingest(_reg("c1"), _batch(5), None)
    (conn,) = hub.connections()
    assert conn["conn"] == "c1" and conn["entry"] == "Team A #1" and conn["name"] == "Max", conn
    assert conn["packets"]["received"] == 4 and conn["packets"]["lost"] == 1, conn["packets"]
    assert conn["live"] is True and conn["latest"]["lap"] == 2, conn


def t_one_sender_per_entry_in_one_lobby_two_per_team_in_endurance():
    hub, clock = _hub()
    hub.ingest(_reg("c1"), _batch(1), None)
    _refused(lambda: hub.ingest(_reg("c2", name="Ann"), _batch(1), None), 409, "already has a sender")
    hub.ingest(_reg("c1"), _batch(2), None)          # the same code reconnecting is the same sender
    hub.ingest(_reg("c3", entry="Team B #2"), _batch(1), None)
    clock.t += ing.LIVE_S + 1                          # the first sender went quiet
    hub.ingest(_reg("c2", name="Ann"), _batch(1), None)
    team, clock = _hub(multi_lobby=True)
    team.ingest(_reg("d1", name="Max", segment="Stint 1"), _batch(1), None)
    team.ingest(_reg("d2", name="Ann", segment="Stint 2"), _batch(1), None)
    _refused(lambda: team.ingest(_reg("d3", name="Lea"), _batch(1), None), 409, "two senders")


def t_a_disconnected_sender_stays_out_until_it_registers_again():
    hub, _clock = _hub()
    hub.ingest(_reg("c1"), _batch(1), None)
    assert hub.disconnect("c1") is True and hub.disconnect("nope") is False
    _refused(lambda: hub.ingest(_reg("c1"), _batch(2), None), 410, "disconnected")
    assert hub.connections() == [], "a disconnected sender frees its entry"
    hub.ingest(_reg("c9"), _batch(1), None)            # a new registration for the entry works


def t_bad_batches_and_too_many_connections_are_refused():
    hub, _clock = _hub()
    _refused(lambda: hub.ingest(_reg("c1"), b"nope", None), 400, "not a telemetry batch")
    small = ing.Hub(multi_lobby=lambda: False, clock=_Clock(), max_connections=2)
    small.ingest(_reg("a", entry="E1"), _batch(1), None)
    small.ingest(_reg("b", entry="E2"), _batch(1), None)
    _refused(lambda: small.ingest(_reg("c", entry="E3"), _batch(1), None), 429, "too many")
    small._clock.t += ing.LIVE_S + 1
    small.ingest(_reg("c", entry="E3"), _batch(1), None)
    assert [c["conn"] for c in small.connections()] == ["c"], "quiet connections make room"


def t_reset_drops_every_connection():
    hub, _clock = _hub()
    hub.ingest(_reg("c1"), _batch(1), None)
    hub.reset()
    assert hub.connections() == []
    hub.ingest(_reg("c2", name="Ann"), _batch(1), None)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

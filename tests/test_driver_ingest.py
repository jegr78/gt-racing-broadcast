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
    _refused(lambda: hub.ingest(_reg("c1"), _batch(3), None), 409, "already has a sender")
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


def t_a_disconnect_during_a_batch_leaves_no_ghost():
    hub, _clock = _hub()
    real = hub._counts.ingest

    def slow(conn, body, stats):
        reply = real(conn, body, stats)
        hub.disconnect(conn)           # the producer clicks while this batch decrypts
        return reply
    hub._counts.ingest = slow
    _refused(lambda: hub.ingest(_reg("c1"), _batch(1), None), 410, "disconnected")
    assert hub.connections() == [] and "c1" not in hub._counts.snapshot()["senders"], \
        "a sender disconnected mid-batch must not come back in the counters"


def t_a_refused_first_batch_frees_its_slot():
    hub = ing.Hub(multi_lobby=lambda: False, clock=_Clock(), max_connections=4)
    hub._counts._max_senders = 0      # the counter refuses: the reservation must roll back
    _refused(lambda: hub.ingest(_reg("c1"), _batch(1), None), 429, "too many")
    assert hub.connections() == [], "no slot stays held by a refused first batch"


def t_every_sample_reaches_the_race_dated_by_its_packet_id():
    got = []
    clock = _Clock()
    hub = ing.Hub(multi_lobby=lambda: False, clock=clock,
                  on_sample=lambda reg, sample: got.append((reg, sample)))
    hub.ingest(_reg("c1", name="Max"), _batch(*range(1, 13)), None)   # a catch-up batch of 12
    assert [s["pid"] for _r, s in got] == [1, 6, 12], [s["pid"] for _r, s in got]
    assert got[0][0] == {"conn": "c1", "entry": "Team A #1", "segment": None, "name": "Max"}, got[0][0]
    ages = [round(clock.t - s["mono"], 3) for _r, s in got]
    assert ages == [round(11 / 60, 3), round(6 / 60, 3), 0.0], f"older packets are dated earlier: {ages}"
    got.clear()
    hub.ingest(_reg("c1", name="Max"), _batch(*([600] * 20)), None)
    assert len(got) == 1, f"a repeated packet id is one sample: {len(got)}"
    got.clear()
    hub.ingest(_reg("c1", name="Max"), _batch(6, 1_000_001), None)     # a forged far-ahead id
    age = clock.t - got[0][1]["mono"]
    assert age <= tp.MAX_BATCH / tp.PACKET_HZ + 1e-9, f"back-dating stays within one batch: {age}"


def t_a_failing_race_hook_never_fails_the_upload():
    def boom(_reg, _sample):
        raise ValueError("bad sample")
    hub = ing.Hub(multi_lobby=lambda: False, on_sample=boom)
    import logging
    seen = []

    class Grab(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())
    handler = Grab()
    logging.getLogger("racecast.relay").addHandler(handler)
    try:
        for start in (1, 7, 13):
            res = hub.ingest(_reg("c1"), _batch(*range(start, start + 6)), None)
            assert res["ok"] and res["accepted"] == 6, res
    finally:
        logging.getLogger("racecast.relay").removeHandler(handler)
    assert len(seen) == 1 and "c1" in seen[0], f"one warning per connection, not one per sample: {seen}"
    assert "bad sample" not in seen[0], "the exception text can carry client data: only its type is logged"


def t_the_producer_reassigns_a_sender_to_another_entry():
    got = []
    hub = ing.Hub(multi_lobby=lambda: False, on_sample=lambda reg, s: got.append(reg["entry"]))
    hub.ingest(_reg("c1", entry="Team A #1"), _batch(6), None)
    assert hub.reassign("c1", "Team B #2")
    hub.ingest(_reg("c1", entry="Team A #1"), _batch(12), None)
    assert got == ["Team A #1", "Team B #2"], got
    assert hub.connections()[0]["entry"] == "Team B #2" and hub.connections()[0]["reassigned"]
    try:
        hub.ingest(_reg("c2", entry="Team B #2"), _batch(18), None)
    except ing.IngestError as exc:
        assert exc.status == 409, exc
    else:
        raise AssertionError("the reassigned sender holds Team B's slot")
    assert not hub.reassign("nobody", "Team B #2")


def t_readiness_lists_every_entry_and_stint_with_its_senders():
    conns = [{"conn": "c1", "entry": "A", "segment": "S1", "name": "Max", "live": True},
             {"conn": "c2", "entry": "B", "segment": "S2", "name": "Ann", "live": False},
             {"conn": "c3", "entry": "Z", "segment": "S1", "name": "Odd", "live": True}]
    rows = ing.readiness(["A", "B"], ["S1", "S2"], conns, multi_lobby=True)
    got = {(r["entry"], r["segment"]): (r["ready"], [s["name"] for s in r["senders"]]) for r in rows}
    assert got == {("A", "S1"): (True, ["Max"]), ("A", "S2"): (False, []),
                   ("B", "S1"): (False, []), ("B", "S2"): (False, ["Ann"]),
                   ("Z", "S1"): (True, ["Odd"])}, got
    single = ing.readiness(["A", "B"], ["S1"], conns[:1], multi_lobby=False)
    assert [(r["entry"], r["segment"], r["ready"]) for r in single] == [("A", None, True), ("B", None, False)]


def t_reset_drops_every_connection():
    hub, _clock = _hub()
    hub.ingest(_reg("c1"), _batch(1), None)
    hub.reset()
    assert hub.connections() == []
    hub.ingest(_reg("c2", name="Ann"), _batch(1), None)



def t_long_quiet_connections_are_forgotten_with_their_side_state():
    hub, clock = _hub()
    hub.ingest(_reg("old"), _batch(1), None)
    hub.reassign("old", "Team B #2")
    hub.ingest(_reg("new", entry="Team C #3"), _batch(1), None)
    clock.t += ing.FORGET_S - 1
    hub.ingest(_reg("new", entry="Team C #3"), _batch(2), None)
    assert {c["conn"] for c in hub.connections()} == {"old", "new"}, "a recent sender stays listed"
    clock.t += 2
    assert [c["conn"] for c in hub.connections()] == ["new"], \
        "a sender quiet for FORGET_S leaves the list, the dashboard and readiness"
    hub.ingest(_reg("old"), _batch(3), None)
    back = [c for c in hub.connections() if c["conn"] == "old"]
    assert back and back[0]["entry"] == "Team B #2", \
        ("a sender coming back after the sweep must keep the producer's correction", back)


def t_reassignment_records_are_bounded():
    clock = _Clock()
    hub = ing.Hub(multi_lobby=lambda: False, clock=clock, max_connections=2)
    for i in range(20):
        hub.ingest(_reg(f"c{i}", entry=f"E{i}"), _batch(1), None)
        hub.reassign(f"c{i}", f"F{i}")
        clock.t += ing.FORGET_S + 1
        hub.connections()
    assert len(hub._reassigned) <= 4 * 2, f"reassignments must not grow without bound: {len(hub._reassigned)}"


def t_disconnect_records_are_bounded():
    hub = ing.Hub(multi_lobby=lambda: False, clock=_Clock(), max_connections=2)
    for i in range(20):
        hub.ingest(_reg(f"c{i}", entry=f"E{i}"), _batch(1), None)
        hub.disconnect(f"c{i}")
    assert len(hub._dropped) <= 4 * 2, f"disconnect records must not grow without bound: {len(hub._dropped)}"
    _refused(lambda: hub.ingest(_reg("c19", entry="E19"), _batch(2), None), 410, "disconnected")


def t_a_new_invite_resets_the_hub():
    current = {"id": "inv1"}
    hub = ing.Hub(multi_lobby=lambda: False, clock=_Clock(), invite_id=lambda: current["id"])
    hub.ingest(_reg("c1"), _batch(1), None)
    hub.disconnect("c1")
    hub.ingest(_reg("c2", entry="Team B #2"), _batch(1), None)
    assert [c["conn"] for c in hub.connections()] == ["c2"]
    current["id"] = "inv2"
    assert hub.connections() == [], "a regenerated invite revokes every code, so its senders go too"
    assert not hub._dropped, "and so do the old disconnect records"
    hub.ingest(_reg("c3"), _batch(1), None)
    assert [c["conn"] for c in hub.connections()] == ["c3"]


def t_a_full_entry_is_reported_before_registration():
    hub, clock = _hub()
    assert not hub.entry_full("Team A #1")
    hub.ingest(_reg("c1"), _batch(1), None)
    assert hub.entry_full("Team A #1") and not hub.entry_full("Team B #2")
    clock.t += ing.LIVE_S + 1
    assert not hub.entry_full("Team A #1"), "a quiet sender frees the entry"
    team, _clock = _hub(multi_lobby=True)
    team.ingest(_reg("d1"), _batch(1), None)
    assert not team.entry_full("Team A #1"), "endurance allows a second sender per team"
    team.ingest(_reg("d2", name="Ann"), _batch(1), None)
    assert team.entry_full("Team A #1")


def t_entry_for_follows_a_reassignment():
    hub, _clock = _hub()
    hub.ingest(_reg("c1"), _batch(1), None)
    assert hub.entry_for("c1", "Team A #1") == "Team A #1"
    hub.reassign("c1", "Team B #2")
    assert hub.entry_for("c1", "Team A #1") == "Team B #2"
    assert hub.entry_for("unknown", "Team A #1") == "Team A #1"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

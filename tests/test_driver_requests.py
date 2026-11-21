#!/usr/bin/env python3
"""Driver registrations waiting for Race Control's approval.
Run: python3 tests/test_driver_requests.py"""
import json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "scripts"))
import driver_requests as dr  # noqa: E402


def _reg(conn="conn0001", entry="Team A #1", segment=None, name="Max"):
    return {"conn": conn, "entry": entry, "segment": segment, "name": name}


def _store(invite=None, now=None):
    d = tempfile.mkdtemp()
    current = invite if invite is not None else ["inv-1"]
    clock = now if now is not None else [1000.0]
    return dr.Store(os.path.join(d, dr.FILENAME), lambda: current[0], wall=lambda: clock[0]), current, clock


def t_a_registration_lands_pending_and_carries_no_telemetry():
    store, _inv, _clock = _store()
    rec = store.add(_reg())
    assert rec["status"] == "pending" and rec["entry"] == "Team A #1" and rec["name"] == "Max", rec
    assert rec["registered"] == {"entry": "Team A #1", "segment": None, "name": "Max"}, rec
    assert set(rec) == {"conn", "entry", "segment", "name", "status", "ts", "registered", "who", "decided",
                        "client"}, \
        f"a request carries the registration only: {sorted(rec)}"
    assert store.add(_reg(name="Other"))["name"] == "Max", "the same code is the same request"
    assert [r["conn"] for r in store.all()] == ["conn0001"]


def t_approve_with_corrections_and_correct_again():
    store, _inv, clock = _store()
    store.add(_reg(segment="Stint 1"))
    clock[0] = 1010.0
    before, after = store.approve("conn0001", "race_control:dave", entry="Team B #2", segment="Stint 2",
                                  name="Max Power")
    assert before["status"] == "pending" and after["status"] == "approved", (before, after)
    assert (after["entry"], after["segment"], after["name"]) == ("Team B #2", "Stint 2", "Max Power")
    assert after["registered"]["entry"] == "Team A #1", "the registration stays visible after a correction"
    assert after["who"] == "race_control:dave" and after["decided"] == 1010.0, after
    _b, again = store.correct("conn0001", "director:bob", entry="Team A #1", segment="Stint 2", name="Max")
    assert again["entry"] == "Team A #1" and again["status"] == "approved" and again["who"] == "director:bob"
    try:
        store.approve("conn0001", "x", entry="Team A #1", segment=None, name="Max")
        raise AssertionError("an approved request is corrected, not approved twice")
    except ValueError:
        pass


def t_reject_revokes_for_good():
    store, _inv, _clock = _store()
    store.add(_reg())
    store.approve("conn0001", "director:bob", entry="Team A #1", segment=None, name="Max")
    before = store.reject("conn0001", "race_control:dave")
    assert before["status"] == "approved" and store.get("conn0001")["status"] == "rejected"
    for fn in (lambda: store.approve("conn0001", "x", entry="Team A #1", segment=None, name="Max"),
               lambda: store.correct("conn0001", "x", entry="Team A #1", segment=None, name="Max"),
               lambda: store.reject("conn0001", "x")):
        try:
            fn()
            raise AssertionError("a rejected request stays rejected")
        except ValueError:
            pass
    assert store.add(_reg())["status"] == "rejected", "the rejected code never requests again"
    try:
        store.approve("nobody00", "x", entry="Team A #1", segment=None, name="Max")
        raise AssertionError("an unknown request is a KeyError")
    except KeyError:
        pass


def t_decisions_survive_a_restart_and_a_new_invite_resets_them():
    store, inv, _clock = _store()
    store.add(_reg())
    store.add(_reg(conn="conn0002", name="Ann"))
    store.approve("conn0001", "director:bob", entry="Team B #2", segment=None, name="Max")
    again = dr.Store(store.path, lambda: inv[0])
    assert [(r["conn"], r["status"], r["entry"]) for r in again.all()] == \
        [("conn0001", "approved", "Team B #2"), ("conn0002", "pending", "Team A #1")], again.all()
    inv[0] = "inv-2"
    assert again.all() == [] and again.get("conn0001") is None, "a renewed invite resets every request"
    inv[0] = None
    assert store.all() == [], "no invite, no requests"


def t_a_corrupt_or_foreign_file_is_no_request():
    store, inv, _clock = _store()
    with open(store.path, "w", encoding="utf-8") as fh:
        fh.write("{broken")
    assert store.all() == []
    with open(store.path, "w", encoding="utf-8") as fh:
        json.dump({"invite": "inv-1", "requests": [
            {"conn": "conn0001", "entry": "Team A #1", "segment": None, "name": "bad;name", "status": "approved",
             "ts": 1, "registered": {}, "who": None, "decided": None},
            {"conn": "conn0002", "entry": "Team A #1", "segment": None, "name": "Ann", "status": "approved",
             "ts": 1, "registered": {"entry": "Team A #1", "segment": None, "name": "Ann"},
             "who": "director:bob", "decided": 2}]}, fh)
    fresh = dr.Store(store.path, lambda: inv[0])
    assert [r["conn"] for r in fresh.all()] == ["conn0002"], "an invalid saved record is dropped"


def t_open_requests_are_capped_and_approvals_never_evicted():
    store, _inv, _clock = _store()
    for i in range(dr.MAX_PENDING):
        store.add(_reg(conn=f"conn{i:04d}", entry=f"Team {i}"), client=f"10.0.{i // 200}.{i % 200}")
    assert store.add(_reg(conn="connfull", entry="Team X"), client="10.9.9.9")["status"] == "pending", \
        "a newcomer always lands"
    assert store.get("conn0000") is None and store.get("conn0001") is not None, \
        "with one request per client the oldest overall makes room"
    store.approve("conn0001", "x", entry="Team A #1", segment=None, name="Max")
    heavy = [f"conn{i:04d}" for i in (10, 11, 12)]
    for c in heavy:
        store.reject(c, "x")
    for c in heavy:      # one client with several waiting requests among clients with one each
        store.add(_reg(conn=c.replace("conn", "hvy_"), entry=c), client="198.51.100.1")
    store.add(_reg(conn="connfill", entry="Team F"), client="10.9.9.7")
    assert len([r for r in store.all() if r["status"] == "pending"]) == dr.MAX_PENDING
    assert store.add(_reg(conn="connnew2", entry="Team Z"), client="10.9.9.8")["status"] == "pending"
    assert store.get("hvy_0010") is None and store.get("hvy_0011") is not None \
        and store.get("conn0002") is not None, "the client holding the most loses its oldest"
    assert store.get("conn0001")["status"] == "approved", "an approval is never evicted"
    small = dr.Store(store.path + ".2", lambda: "inv-1")
    small.MAX_KEPT = 3
    for c in ("connaaaa", "connbbbb", "conncccc"):
        small.add(_reg(conn=c, name=c))
    small.approve("connaaaa", "x", entry="Team A #1", segment=None, name="Max")
    small.reject("connbbbb", "x")
    small.reject("conncccc", "x")
    small.add(_reg(conn="conndddd", name="dddd"))
    assert [r["conn"] for r in small.all()] == ["connaaaa", "conncccc", "conndddd"], \
        "the oldest rejected request makes room, never an approved one"


def t_a_second_registration_with_the_same_name_leaves_the_first_intact():
    store, _inv, _clock = _store()
    store.add(_reg(conn="conn0001", segment="Stint 1"))
    store.add(_reg(conn="conn0002", segment="Stint 1"))
    assert [(r["conn"], r["status"]) for r in store.all()] == [("conn0001", "pending"), ("conn0002", "pending")], \
        "a public registration never removes another code's request"


def t_a_flooding_client_only_ever_evicts_its_own_requests():
    store, _inv, _clock = _store()
    store.add(_reg(conn="realdrvr", name="Max", segment="Stint 1"), client="203.0.113.7")
    for i in range(dr.MAX_PENDING_PER_SLOT + 2):
        store.add(_reg(conn=f"flood{i:03d}", name=f"N{i}", segment="Stint 1"), client="198.51.100.1")
    mine = [r["conn"] for r in store.all() if r["conn"].startswith("flood")]
    assert mine == [f"flood{i:03d}" for i in range(2, dr.MAX_PENDING_PER_SLOT + 2)], \
        ("a client over its cap for an entry and stint loses its own oldest", mine)
    assert store.get("realdrvr")["status"] == "pending", "never another client's request"
    assert store.add(_reg(conn="realdrv2", name="Ann", segment="Stint 1"), client="203.0.113.8")["status"] \
        == "pending", "a flood never blocks another client's registration for the same slot"
    for i in range(dr.MAX_PENDING_PER_CLIENT + 3):
        store.add(_reg(conn=f"wide{i:04d}", name="W", entry=f"Team {i}"), client="192.0.2.1")
    wide = [r["conn"] for r in store.all() if r["conn"].startswith("wide")]
    assert len(wide) == dr.MAX_PENDING_PER_CLIENT and wide[0] == "wide0003", \
        ("a client over its own total loses its oldest", wide)
    assert {r["client"] for r in store.all() if r["conn"] == "realdrvr"} == {"203.0.113.7"}, \
        "each request records its client"


def t_reject_all_waiting_requests_at_once():
    store, _inv, _clock = _store()
    store.add(_reg(conn="conn0001"))
    store.add(_reg(conn="conn0002", name="Ann"))
    store.approve("conn0002", "x", entry="Team A #1", segment=None, name="Ann")
    store.add(_reg(conn="conn0003", name="Lea", entry="Team B #2"))
    gone = store.reject_pending("race_control:dave")
    assert sorted(r["conn"] for r in gone) == ["conn0001", "conn0003"], gone
    assert {r["conn"]: r["status"] for r in store.all()} == \
        {"conn0001": "rejected", "conn0002": "approved", "conn0003": "rejected"}, store.all()
    assert store.reject_pending("x") == []


def t_a_failed_write_leaves_memory_as_it_was():
    store, _inv, _clock = _store()
    store.add(_reg())
    real = store._save

    def broken():
        raise OSError("disk full")
    store._save = broken
    for fn in (lambda: store.approve("conn0001", "x", entry="Team B #2", segment=None, name="Max"),
               lambda: store.reject("conn0001", "x"), lambda: store.reject_pending("x"),
               lambda: store.add(_reg(conn="conn0002", name="Ann"))):
        try:
            fn()
            raise AssertionError("the write error reaches the caller")
        except OSError:
            pass
    store._save = real
    assert [(r["conn"], r["status"], r["entry"]) for r in store.all()] == [("conn0001", "pending", "Team A #1")], \
        store.all()


def t_an_evicted_request_is_remembered_for_its_invite():
    store, inv, _clock = _store()
    for i in range(dr.MAX_PENDING_PER_SLOT + 1):
        store.add(_reg(conn=f"flood{i:03d}", name=f"N{i}"), client="198.51.100.1")
    assert store.get("flood000") is None and store.was_evicted("flood000"), "the dropped code is known"
    assert not store.was_evicted("flood001") and not store.was_evicted("nobody00")
    again = dr.Store(store.path, lambda: inv[0])
    assert again.was_evicted("flood000"), "it survives a restart"
    inv[0] = "inv-2"
    assert not again.was_evicted("flood000"), "a renewed invite forgets it"
    small = dr.Store(store.path + ".3", lambda: "inv-1")
    for i in range(dr.MAX_EVICTED + dr.MAX_PENDING_PER_SLOT + 5):
        small.add(_reg(conn=f"bulk{i:05d}", name=f"N{i}"), client="198.51.100.1")
    assert len(small._evicted) == dr.MAX_EVICTED and not small.was_evicted("bulk00000"), "the memory is bounded"



def t_a_disconnected_request_is_remembered_for_its_invite():
    store, inv, _clock = _store()
    store.add(_reg())
    store.approve("conn0001", "race_control:dave", entry="Team A #1", segment=None, name="Max")
    assert not store.disconnect("nobody00"), "only a known request is disconnected"
    assert store.disconnect("conn0001") and store.was_disconnected("conn0001")
    assert [r.get("disconnected") for r in store.all()] == [True], "the listing marks it for the desks"
    again = dr.Store(store.path, lambda: inv[0])
    assert again.was_disconnected("conn0001") and not again.was_disconnected("nobody00"), "it survives a restart"
    inv[0] = "inv-2"
    assert not again.was_disconnected("conn0001"), "a renewed invite forgets it"
    small, _inv, _clock = _store()
    for i in range(dr.MAX_EVICTED + 2):
        small.add(_reg(conn=f"bulk{i:05d}", name=f"N{i}"), client=f"198.51.100.{i % 200}")
        small.disconnect(f"bulk{i:05d}")
    assert len(small._disconnected) == dr.MAX_EVICTED and not small.was_disconnected("bulk00000"), \
        "the memory is bounded"
    broken, _inv, _clock = _store()
    broken.add(_reg())
    broken._save = lambda: (_ for _ in ()).throw(OSError("disk full"))
    try:
        broken.disconnect("conn0001")
        raise AssertionError("a failed write raises")
    except OSError:
        pass  # the route answers it with 500
    assert not broken.was_disconnected("conn0001"), "a failed write leaves memory as it was"

if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

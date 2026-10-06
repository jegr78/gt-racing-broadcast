#!/usr/bin/env python3
"""Stdlib unit checks for the pending panel-save store.
Run: python3 tests/test_pending_saves.py"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "pending_saves", os.path.join(ROOT, "src", "scripts", "pending_saves.py"))
ps = importlib.util.module_from_spec(spec); spec.loader.exec_module(ps)


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


def _store():
    c = Clock()
    return ps.PendingSaves(now=c), c


def t_put_queues_a_saving_entry():
    s, _c = _store()
    rev = s.put("schedule", 3, {"url": "https://youtu.be/a"})
    assert rev == 1
    assert s.state("schedule", 3) == ("saving", None)
    assert s.next_job() == ("schedule", 3, {"url": "https://youtu.be/a"}, 1)
    assert s.next_job() is None            # in flight, nothing else queued


def t_put_merges_fields_newest_wins():
    s, _c = _store()
    s.put("schedule", 3, {"url": "https://youtu.be/a", "name": "JeGr"})
    s.put("schedule", 3, {"url": ""})
    assert s.next_job() == ("schedule", 3, {"url": "", "name": "JeGr"}, 2)


def t_done_ok_confirms_latest_revision():
    s, _c = _store()
    s.put("pov", 2, {"url": "https://youtu.be/p"})
    _t, _r, _f, rev = s.next_job()
    assert s.done("pov", 2, rev, ok=True) == "confirmed"
    assert s.state("pov", 2) == (None, None)
    assert len(s) == 1                      # kept until the sheet shows it


def t_put_during_flight_keeps_newer_revision():
    s, _c = _store()
    s.put("schedule", 3, {"url": "https://youtu.be/a"})
    _t, _r, _f, rev1 = s.next_job()
    s.put("schedule", 3, {"url": "https://youtu.be/b"})        # arrives mid-flight
    assert s.done("schedule", 3, rev1, ok=True) == "saving"     # older confirm
    assert s.next_job() == ("schedule", 3, {"url": "https://youtu.be/b"}, 2)
    assert s.overlay("schedule") == {3: {"url": "https://youtu.be/b"}}


def t_done_failure_goes_local_with_error():
    s, _c = _store()
    s.put("schedule", 3, {"url": "https://youtu.be/a"})
    _t, _r, _f, rev = s.next_job()
    assert s.done("schedule", 3, rev, ok=False, err="TimeoutError: x") == "local"
    assert s.state("schedule", 3) == ("local", "TimeoutError: x")
    assert s.unsynced() == (1, "TimeoutError: x")
    assert s.next_job() is None             # local is not queued


def t_requeue_local_skips_held_entries():
    s, _c = _store()
    s.put("schedule", 3, {"url": "https://youtu.be/a"})
    s.put("schedule", 4, {"url": "https://youtu.be/b"})
    j = s.next_job(); s.done(j[0], j[1], j[3], ok=False, err="timeout")
    j = s.next_job(); s.done(j[0], j[1], j[3], ok=False, err="outdated", hold=True)
    assert s.requeue_local() == 1
    assert s.state("schedule", 3) == ("saving", None)
    assert s.state("schedule", 4) == ("local", "outdated")
    assert s.requeue("schedule", 4) is True                    # SYNC NOW forces it
    assert s.state("schedule", 4) == ("saving", None)
    assert s.requeue("schedule", 9) is False


def t_reconcile_releases_confirmed_when_sheet_matches():
    s, _c = _store()
    s.put("schedule", 3, {"url": "https://youtu.be/a", "name": "JeGr"})
    j = s.next_job(); s.done(j[0], j[1], j[3], ok=True)
    assert s.reconcile("schedule", {3: ("https://youtu.be/old", "JeGr", "")}) == []
    assert s.reconcile("schedule", {3: ("https://youtu.be/a", "JeGr", "Stint 1")}) == [3]
    assert len(s) == 0


def t_reconcile_never_releases_saving_or_local():
    s, _c = _store()
    s.put("schedule", 3, {"url": "https://youtu.be/a"})
    assert s.reconcile("schedule", {3: ("https://youtu.be/a", "", "")}) == []
    j = s.next_job(); s.done(j[0], j[1], j[3], ok=False, err="x")
    assert s.reconcile("schedule", {3: ("https://youtu.be/a", "", "")}) == []


def t_reconcile_missing_row_counts_as_empty():
    s, _c = _store()
    s.put("schedule", 7, {"url": "", "name": "", "stint": ""})
    j = s.next_job(); s.done(j[0], j[1], j[3], ok=True)
    assert s.reconcile("schedule", {}) == [7]


def t_reconcile_grace_expiry():
    s, c = _store()
    s.put("schedule", 3, {"url": "https://youtu.be/a"})
    j = s.next_job(); s.done(j[0], j[1], j[3], ok=True)
    c.t += ps.CONFIRM_GRACE_S - 1
    assert s.reconcile("schedule", {3: ("https://youtu.be/x", "", "")}) == []
    c.t += 2
    assert s.reconcile("schedule", {3: ("https://youtu.be/x", "", "")}) == [3]


def t_targets_are_separate():
    s, _c = _store()
    s.put("schedule", 2, {"url": "https://youtu.be/a"})
    s.put("qualifying", 2, {"url": "https://youtu.be/q"})
    assert s.overlay("schedule") == {2: {"url": "https://youtu.be/a"}}
    assert s.overlay("qualifying") == {2: {"url": "https://youtu.be/q"}}


def t_unconfirmed_counts_saving_and_local_only():
    s, _c = _store()
    s.put("schedule", 2, {"url": ""})
    s.put("schedule", 3, {"url": ""})
    s.put("pov", 2, {"url": ""})
    j = s.next_job(); s.done(j[0], j[1], j[3], ok=True)          # confirmed
    j = s.next_job(); s.done(j[0], j[1], j[3], ok=False, err="x")  # local
    assert s.unconfirmed() == 2                                     # local + saving


def t_states_lists_every_unconfirmed_row_of_a_target():
    s, _c = _store()
    s.put("qualifying", 2, {"url": "", "name": "", "stint": ""})
    j = s.next_job(); s.done(j[0], j[1], j[3], ok=False, err="slow")
    s.put("schedule", 5, {"url": ""})
    assert s.states("qualifying") == {2: ("local", "slow")}
    assert s.states("schedule") == {5: ("saving", None)}


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

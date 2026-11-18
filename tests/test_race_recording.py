#!/usr/bin/env python3
"""Stdlib unit checks for the race recording store. Run: python3 tests/test_race_recording.py"""
import os
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "scripts"))

import race_recording as rr  # noqa: E402


def _row(entry, pos, **kw):
    r = {"pos": pos, "entry": entry, "driver": "D" + entry, "laps": 3,
         "gap_time_s": 1.5, "interval_time_s": 0.5, "lapped": False,
         "status": "running", "finished": False, "compound": "RH",
         "pit_stops": 1, "correction_m": 0.0, "rejoin": None,
         "gt7_position": pos, "position_mismatch": False}
    r.update(kw)
    return r


def _doc(*entries):
    return {"state": "racing", "red_flag": False,
            "rows": [_row(e, i + 1) for i, e in enumerate(entries)]}


def t_samples_round_trip_and_nearest_lookup():
    with tempfile.TemporaryDirectory() as d:
        s = rr.open_current(d, 100.0)
        try:
            assert s.sample_at(100) is None and s.span() is None
            for t in (100, 101, 102):
                s.add_sample(t, _doc("1"))
            assert s.sample_at(101.4)["t"] == 101
            assert s.sample_at(50)["t"] == 100
            assert s.sample_at(500)["t"] == 102
            assert s.sample_at(101)["state"] == "racing"
            assert s.span() == (100, 102), s.span()
        finally:
            s.close()


def t_reopen_after_restart_appends_to_the_same_file():
    with tempfile.TemporaryDirectory() as d:
        a = rr.open_current(d, 1000.0)
        a.add_sample(1, _doc("1"))
        path = a.path
        a.close()
        b = rr.open_current(d, 2000.0)
        try:
            b.add_sample(2, _doc("1"))
            assert b.path == path
            assert b.span() == (1, 2)
        finally:
            b.close()


def t_rotate_starts_a_new_file():
    with tempfile.TemporaryDirectory() as d:
        rr.rotate(d)
        a = rr.open_current(d, 1000.0)
        old = a.path
        a.close()
        rr.rotate(d)
        b = rr.open_current(d, 2000.0)
        try:
            assert b.path != old
            assert os.path.exists(old)
        finally:
            b.close()


def t_events_update_and_list_newest_first():
    with tempfile.TemporaryDirectory() as d:
        s = rr.open_current(d, 1.0)
        try:
            i1 = s.add_event({"kind": "disconnect", "entry": "1"})
            i2 = s.add_event({"kind": "disconnect", "entry": "2"})
            assert i2 > i1
            s.update_event(i1, resolved=True)
            evs = s.events()
            assert [e["id"] for e in evs] == [i2, i1], evs
            assert evs[1]["resolved"] is True and evs[1]["entry"] == "1"
            s.set_meta("a", "x")
            s.set_meta("a", "y")
            assert s.meta("a") == "y" and s.meta("b") is None
        finally:
            s.close()


def t_csv_has_header_and_one_row_per_car():
    with tempfile.TemporaryDirectory() as d:
        s = rr.open_current(d, 1.0)
        try:
            s.add_sample(1700000000, _doc("1", "2"))
            s.add_sample(1700000001, _doc("1", "2"))
            rows = list(s.csv_rows())
        finally:
            s.close()
        assert rows[0][:3] == ["time_utc", "pos", "entry"], rows[0]
        assert len(rows[0]) == 14
        assert len(rows) == 5, rows
        assert rows[1][0].startswith("2023-11-14T22:13:20"), rows[1][0]
        assert rows[1][2] == "1" and rows[2][2] == "2"


class _Blocking:
    def __init__(self):
        self.gate = threading.Event()
        self.started = threading.Event()

    def add_sample(self, t, doc):
        self.started.set()
        self.gate.wait(5)


def t_full_writer_queue_drops_and_counts():
    store = _Blocking()
    w = rr.Writer(store, maxsize=2)
    try:
        job = lambda s: s.add_sample(0, {})  # noqa: E731
        assert w.put(job)
        assert store.started.wait(5)
        assert w.put(job) and w.put(job)
        assert w.put(job) is False
        assert w.dropped == 1, w.dropped
    finally:
        store.gate.set()
        w.stop()


def t_writer_runs_jobs_with_the_store():
    seen = []
    w = rr.Writer("store", maxsize=4)
    try:
        w.put(seen.append)
        assert w.flush(5)
        assert seen == ["store"]
    finally:
        w.stop()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

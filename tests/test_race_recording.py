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


def t_csv_neutralises_formula_cells():
    with tempfile.TemporaryDirectory() as d:
        s = rr.open_current(d, 1.0)
        try:
            doc = _doc("1")
            doc["rows"][0].update(driver="=HYPERLINK(\"x\")", entry="+1", status="@a",
                                  gap_time_s=-1.5, compound="-x", rejoin="\tx")
            s.add_sample(1700000000, doc)
            row = list(s.csv_rows())[1]
        finally:
            s.close()
        assert row[2] == "'+1" and row[3] == "'=HYPERLINK(\"x\")", row
        assert row[8] == "'@a", row
        assert row[9] == "'-x" and row[12] == "'\tx", row
        assert row[5] == -1.5, "numbers stay numbers: %r" % (row[5],)


def _srow(entry, quiet_s=0.5, distance_m=100.0, in_box=False, **kw):
    r = {"entry": entry, "driver": "D" + entry, "conn": "ok", "distance_m": distance_m,
         "laps": 2, "gap_time_s": 0.0, "interval_time_s": None, "lapped": False,
         "status": "running", "finished": False, "pit_stops": 0, "correction_m": 0.0,
         "rejoin": None, "gt7_position": 1, "position_mismatch": False,
         "quiet_s": quiet_s, "in_box": in_box}
    r.update(kw)
    return r


def _st(state="racing", rows=None, red_flag=None, change_window=False):
    return {"state": state, "red_flag": red_flag, "change_window": change_window,
            "entries": rows if rows is not None else [_srow("7")]}


class _Rec:
    def __init__(self, d):
        self.store = rr.open_current(d, 1.0)
        self.writer = rr.Writer(self.store)
        self.rec = rr.Recorder(self.store, self.writer, lambda: 1.0)

    def flush(self):
        assert self.writer.flush(5)

    def reopen(self):
        self.flush()
        return rr.Recorder(self.store, self.writer, lambda: 2.0)

    def close(self):
        self.writer.stop()
        self.store.close()


def t_auto_starts_on_the_first_counting_state_and_stops_at_finish():
    with tempfile.TemporaryDirectory() as d:
        h = _Rec(d)
        try:
            h.rec.observe(_st("waiting"), {}, 100.0)
            assert h.rec.status()["running"] is False
            h.rec.observe(_st("racing"), {"7": "RM"}, 101.0)
            st = h.rec.status()
            assert st["running"] is True and st["started_by"] == "auto", st
            assert st["started_at"] == 101.0, st
            h.rec.observe(_st("finishing"), {}, 102.0)
            h.rec.observe(_st("finished"), {}, 103.0)
            h.flush()
            assert h.rec.status()["running"] is False
            assert h.store.span() == (101.0, 102.0), h.store.span()
            doc = h.store.sample_at(101.0)
            assert doc["rows"][0]["pos"] == 1 and doc["rows"][0]["compound"] == "RM", doc
            assert h.store.meta("running") == "0"
        finally:
            h.close()


def t_manual_stop_is_not_undone_by_auto_start():
    with tempfile.TemporaryDirectory() as d:
        h = _Rec(d)
        try:
            h.rec.observe(_st("racing"), {}, 100.0)
            h.rec.stop("rc")
            h.rec.observe(_st("racing"), {}, 101.0)
            assert h.rec.status()["running"] is False, "auto start runs once per event"
        finally:
            h.close()


def t_manual_start_before_the_green_flag_records_the_grid():
    with tempfile.TemporaryDirectory() as d:
        h = _Rec(d)
        try:
            grid = [_srow("7", quiet_s=None, distance_m=None)]
            res = h.rec.start("alice")
            assert res["running"] is True and res["started_by"] == "alice", res
            assert res["started_at"] == 1.0, "manual start uses the wall clock: %r" % res
            h.rec.observe(_st("waiting", grid), {}, 50.0)
            h.rec.observe(_st("racing"), {}, 51.0)
            h.flush()
            assert h.rec.status()["started_by"] == "alice"
            assert h.store.sample_at(50.0)["state"] == "waiting"
            assert h.store.meta("started_by") == "alice"
        finally:
            h.close()


def t_samples_once_per_second_not_per_tick():
    with tempfile.TemporaryDirectory() as d:
        h = _Rec(d)
        try:
            for i in range(4):
                h.rec.observe(_st(), {}, 100.0 + 0.5 * i)
            h.flush()
            n = sum(1 for _ in h.store.csv_rows()) - 1
            assert n == 2, "one car, two samples expected, got %d rows" % n
        finally:
            h.close()


def _events(h):
    return h.rec.status()["events"]


def t_quiet_car_becomes_a_suspicion_dated_by_its_last_packet():
    with tempfile.TemporaryDirectory() as d:
        h = _Rec(d)
        try:
            h.rec.observe(_st(rows=[_srow("7", quiet_s=6.2)]), {}, 1000.0)
            h.rec.observe(_st(rows=[_srow("7", quiet_s=7.2)]), {}, 1001.0)
            evs = _events(h)
            assert len(evs) == 1, evs
            ev = evs[0]
            assert ev["state"] == "suspected" and ev["entry"] == "7", ev
            assert abs(ev["last_packet"] - 993.8) < 1e-6, ev
            assert ev["opened"] == 1000.0 and ev["driver"] == "D7", ev
            h.flush()
            assert h.store.events()[0]["state"] == "suspected"
        finally:
            h.close()


def t_short_silence_raises_nothing():
    with tempfile.TemporaryDirectory() as d:
        h = _Rec(d)
        try:
            h.rec.observe(_st(rows=[_srow("7", quiet_s=4.9)]), {}, 1000.0)
            assert _events(h) == []
        finally:
            h.close()


def t_unstarted_car_raises_nothing():
    with tempfile.TemporaryDirectory() as d:
        h = _Rec(d)
        try:
            h.rec.observe(_st(rows=[_srow("7", quiet_s=60.0, distance_m=None)]), {}, 1000.0)
            assert _events(h) == []
        finally:
            h.close()


def t_return_before_decision_is_a_dropout_and_stays_confirmable():
    with tempfile.TemporaryDirectory() as d:
        h = _Rec(d)
        try:
            h.rec.observe(_st(rows=[_srow("7", quiet_s=6.0)]), {}, 1000.0)
            h.rec.observe(_st(rows=[_srow("7", quiet_s=0.2)]), {}, 1003.0)
            ev = _events(h)[0]
            assert ev["state"] == "dropout", ev
            assert ev["returned"] == 1003.0 and abs(ev["dropout_s"] - 9.0) < 1e-6, ev
            out = h.rec.decide("rc", ev["id"], "confirm")
            assert out["state"] == "confirmed" and out["decided_by"] == "rc", out
            assert out["dropout_s"] is not None
            h.flush()
            assert h.store.events()[0]["state"] == "confirmed"
            h.rec.observe(_st(rows=[_srow("7", quiet_s=5.5)]), {}, 1020.0)
            assert len(_events(h)) == 2, "a new silence after the return is a new event"
        finally:
            h.close()


def t_decided_silence_is_not_raised_again_while_quiet():
    with tempfile.TemporaryDirectory() as d:
        h = _Rec(d)
        try:
            h.rec.observe(_st(rows=[_srow("7", quiet_s=6.0)]), {}, 1000.0)
            h.rec.decide("rc", _events(h)[0]["id"], "dismiss")
            h.rec.observe(_st(rows=[_srow("7", quiet_s=7.0)]), {}, 1001.0)
            evs = _events(h)
            assert len(evs) == 1 and evs[0]["state"] == "dismissed", evs
            h.rec.observe(_st(rows=[_srow("7", quiet_s=0.1)]), {}, 1002.0)
            assert _events(h)[0]["state"] == "dismissed"
            assert _events(h)[0]["returned"] == 1002.0
        finally:
            h.close()


def t_box_in_change_window_raises_nothing_but_track_does():
    with tempfile.TemporaryDirectory() as d:
        h = _Rec(d)
        try:
            rows = [_srow("1", quiet_s=9.0, in_box=True), _srow("2", quiet_s=9.0)]
            h.rec.observe(_st(rows=rows, change_window=True), {}, 1000.0)
            assert [e["entry"] for e in _events(h)] == ["2"], _events(h)
            h.rec.observe(_st(rows=rows, change_window=False), {}, 1001.0)
            assert sorted(e["entry"] for e in _events(h)) == ["1", "2"], _events(h)
        finally:
            h.close()


def t_finished_car_raises_nothing():
    with tempfile.TemporaryDirectory() as d:
        h = _Rec(d)
        try:
            rows = [_srow("1", quiet_s=9.0, finished=True), _srow("2", quiet_s=9.0)]
            h.rec.observe(_st("finishing", rows), {}, 1000.0)
            assert [e["entry"] for e in _events(h)] == ["2"], _events(h)
        finally:
            h.close()

def t_red_flag_raises_nothing():
    with tempfile.TemporaryDirectory() as d:
        h = _Rec(d)
        try:
            h.rec.observe(_st(rows=[_srow("7", quiet_s=9.0)], red_flag={"at": 1}), {}, 1000.0)
            assert _events(h) == []
        finally:
            h.close()


def t_no_events_without_a_recording():
    with tempfile.TemporaryDirectory() as d:
        h = _Rec(d)
        try:
            h.rec.observe(_st("waiting", [_srow("7", quiet_s=9.0)]), {}, 1000.0)
            assert _events(h) == []
        finally:
            h.close()


def t_open_suspicion_survives_a_restart():
    with tempfile.TemporaryDirectory() as d:
        h = _Rec(d)
        try:
            h.rec.observe(_st(rows=[_srow("7", quiet_s=6.0)]), {}, 1000.0)
            rec2 = h.reopen()
            st = rec2.status()
            assert st["running"] is True and st["started_by"] == "auto", st
            assert [e["state"] for e in st["events"]] == ["suspected"], st
            rec2.observe(_st(rows=[_srow("7", quiet_s=8.0)]), {}, 1002.0)
            assert len(rec2.status()["events"]) == 1, "no duplicate after restart"
            rec2.observe(_st(rows=[_srow("7", quiet_s=0.1)]), {}, 1003.0)
            assert rec2.status()["events"][0]["state"] == "dropout"
            rec2.observe(_st(rows=[_srow("8", quiet_s=6.0)]), {}, 1004.0)
            ids = [e["id"] for e in rec2.status()["events"]]
            assert len(set(ids)) == 2, "new ids continue after the stored ones: %r" % ids
        finally:
            h.close()


def t_decide_rejects_unknown_decision_and_id():
    with tempfile.TemporaryDirectory() as d:
        h = _Rec(d)
        try:
            h.rec.observe(_st(rows=[_srow("7", quiet_s=6.0)]), {}, 1000.0)
            eid = _events(h)[0]["id"]
            for bad in ("approve", "", None):
                try:
                    h.rec.decide("rc", eid, bad)
                except ValueError:
                    pass  # expected
                else:
                    raise AssertionError("decision %r accepted" % (bad,))
            try:
                h.rec.decide("rc", eid + 99, "confirm")
            except KeyError:
                pass  # expected
            else:
                raise AssertionError("unknown event id accepted")
            assert _events(h)[0]["state"] == "suspected"
        finally:
            h.close()


def t_status_reports_writer_counters():
    with tempfile.TemporaryDirectory() as d:
        h = _Rec(d)
        try:
            st = h.rec.status()
            assert st["dropped"] == 0 and st["failed"] == 0 and st["span"] is None, st
        finally:
            h.close()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

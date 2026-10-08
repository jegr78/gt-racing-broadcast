#!/usr/bin/env python3
"""GT7 telemetry recording: file format, writer, reader, switch and CSV export.
Run: python3 tests/test_gt7_recording.py"""
import importlib.util, json, os, struct, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))   # gt7_recording imports gt7_telemetry


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


tm = _load("gt7_telemetry", ("src", "scripts", "gt7_telemetry.py"))
rec = _load("gt7_recording", ("src", "scripts", "gt7_recording.py"))


def _plain(lap=1, size=0x128, fill=0):
    b = bytearray([fill]) * size
    struct.pack_into("<I", b, 0, 0x47375330)
    struct.pack_into("<h", b, tm.OFF_LAP, lap)
    return bytes(b)


def _write(d, items, **kw):
    w = rec.RecordingWriter(d, "Demo", "dev", flush_s=0.05, **kw)
    for ts, kind, plain in items:
        w.put(ts, kind, plain)
    w.close()
    return w


def t_roundtrip_header_and_packets():
    with tempfile.TemporaryDirectory() as d:
        items = [(1000.0 + i / 60, "~", _plain(lap=1, size=0x158, fill=i)) for i in range(5)]
        w = _write(d, items)
        assert w.error is None and w.path.endswith(".gt7rec"), w.path
        assert not os.path.exists(w.path + ".part"), "a clean close drops .part"
        r = rec.Recording(w.path)
        assert r.header["format"] == "racecast-gt7rec" and r.header["version"] == 1
        assert r.header["profile"] == "Demo" and r.header["relay_version"] == "dev"
        assert list(r.packets()) == items
        assert r.dropped == 0


def t_writer_opens_no_file_without_packets():
    with tempfile.TemporaryDirectory() as d:
        w = _write(d, [])
        assert w.path is None and os.listdir(d) == [], os.listdir(d)


def t_name_is_local_start_time_and_never_collides():
    with tempfile.TemporaryDirectory() as d:
        ts = time.mktime((2026, 10, 7, 20, 15, 3, 0, 0, -1))
        a = _write(d, [(ts, "A", _plain())]).path
        b = _write(d, [(ts, "A", _plain())]).path
        assert os.path.basename(a) == "20261007-201503.gt7rec", a
        assert os.path.basename(b) == "20261007-201503-2.gt7rec", b


def t_truncated_tail_is_skipped():
    with tempfile.TemporaryDirectory() as d:
        w = _write(d, [(1.0, "A", _plain()), (2.0, "A", _plain())])
        with open(w.path, "r+b") as fh:
            fh.truncate(os.path.getsize(w.path) - 10)
        assert [p[0] for p in rec.Recording(w.path).packets()] == [1.0]


def t_drop_count_is_persisted():
    with tempfile.TemporaryDirectory() as d:
        w = rec.RecordingWriter(d, "Demo", "dev", flush_s=0.05)
        w.put(1.0, "A", _plain())
        w.dropped = 3                           # as if put() had met a full queue three times
        w.close()
        r = rec.Recording(w.path)
        assert [p[0] for p in r.packets()] == [1.0], "the meta record is not a packet"
        assert r.dropped == 3, r.dropped


def t_put_never_blocks_when_full():
    import queue as _queue
    with tempfile.TemporaryDirectory() as d:
        w = rec.RecordingWriter(d, "Demo", "dev", flush_s=0.05)
        w.close()                               # writer thread gone, nothing drains the queue
        w._stop.clear()
        w._q = _queue.Queue(maxsize=1)
        w.put(1.0, "A", _plain())
        t0 = time.monotonic()
        w.put(2.0, "A", _plain())               # full -> dropped, returns at once
        assert time.monotonic() - t0 < 0.5 and w.dropped == 1


def t_write_error_stops_and_reports():
    with tempfile.TemporaryDirectory() as d:
        blocker = os.path.join(d, "file")
        with open(blocker, "w") as fh:
            fh.write("x")
        w = _write(os.path.join(blocker, "sub"), [(1.0, "A", _plain())])   # dir under a file
        assert w.error, "an unwritable directory must surface as error, not raise"
        w.put(2.0, "A", _plain())               # ignored after an error


def t_reader_rejects_foreign_and_newer_files():
    with tempfile.TemporaryDirectory() as d:
        bad = os.path.join(d, "x.gt7rec")
        with open(bad, "wb") as fh:
            fh.write(b"hello\n")
        try:
            rec.Recording(bad); raise AssertionError("foreign file accepted")
        except rec.RecordingError as e:
            assert "not a racecast telemetry recording" in str(e)
        with open(bad, "wb") as fh:
            fh.write((json.dumps({"format": "racecast-gt7rec", "version": 99}) + "\n").encode())
        try:
            rec.Recording(bad); raise AssertionError("newer version accepted")
        except rec.RecordingError as e:
            assert "version 99" in str(e) and "update racecast" in str(e)


def t_finalize_partials_renames_leftovers():
    with tempfile.TemporaryDirectory() as d:
        w = _write(d, [(1.0, "A", _plain())])
        os.replace(w.path, w.path + ".part")
        assert rec.finalize_partials(d) == [os.path.basename(w.path)]
        assert os.path.exists(w.path) and rec.finalize_partials(d) == []
        assert rec.finalize_partials(os.path.join(d, "missing")) == []


def t_list_recordings_reports_duration_laps_partial():
    with tempfile.TemporaryDirectory() as d:
        items = [(10.0, "A", _plain(lap=1)), (20.0, "A", _plain(lap=2)),
                 (30.0, "A", _plain(lap=3))]
        w = _write(d, items)
        os.replace(w.path, w.path + ".part")
        (row,) = rec.list_recordings(d, count_laps=True)
        assert row["name"] == os.path.basename(w.path) + ".part"
        assert row["duration_s"] == 20.0 and row["laps"] == 2 and row["partial"] is True
        (fast,) = rec.list_recordings(d)
        assert fast["laps"] is None and fast["partial"] is True, "the default reads only the header"
        assert fast["duration_s"] >= 0.0
        assert rec.recording_stem(row["path"]) == os.path.basename(w.path)[:-len(".gt7rec")]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

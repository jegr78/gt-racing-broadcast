#!/usr/bin/env python3
"""GT7 telemetry recording: file format, writer, reader, switch and CSV export.
Run: python3 tests/test_gt7_recording.py"""
import importlib.util, json, logging, os, struct, sys, tempfile, threading, time

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
        sub = os.path.join(blocker, "sub")
        w = _write(sub, [(1.0, "A", _plain())])   # dir under a file
        assert w.error, "an unwritable directory must surface as error, not raise"
        assert sub not in w.error and d not in w.error, \
            f"an OSError message must not leak the recording path: {w.error}"
        before = w.error
        w.put(2.0, "A", _plain())               # ignored after an error
        assert w.error == before and w._q.empty(), \
            "a packet after an error must not change the error or be queued"


def t_non_os_error_in_writer_stops_recording_and_reports():
    # ord() on a multi-char kind raises TypeError, not OSError; _run must still
    # stop the recording and report it, not die silently with error=None (#786).
    with tempfile.TemporaryDirectory() as d:
        w = rec.RecordingWriter(d, "Demo", "dev", flush_s=0.05)
        w.put(1.0, "AB", _plain())
        w.close()
        assert w.error == "TypeError", w.error
        assert os.path.exists(w.path + rec.PART), "a non-OSError failure must not be renamed clean"
        assert not os.path.exists(w.path)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def _close_with_flaky_rename(d, failures):
    """Write one packet and close while os.replace on the .part fails `failures` times."""
    real_replace, real_wait = rec.os.replace, rec.RENAME_WAIT_S
    calls = []

    def flaky(src, dst):
        if src.endswith(rec.PART):
            calls.append(src)
            if len(calls) <= failures:
                raise PermissionError(13, "The process cannot access the file")
        return real_replace(src, dst)
    cap = _Capture()
    rec.LOG.addHandler(cap)
    rec.os.replace, rec.RENAME_WAIT_S = flaky, 0.01
    try:
        w = rec.RecordingWriter(d, "Demo", "dev", flush_s=0.05)
        w.put(1.0, "A", _plain())
        w.close()
    finally:
        rec.os.replace, rec.RENAME_WAIT_S = real_replace, real_wait
        rec.LOG.removeHandler(cap)
    return w, calls, cap.messages


def t_rename_retries_a_transient_windows_lock():
    with tempfile.TemporaryDirectory() as d:
        w, calls, warnings = _close_with_flaky_rename(d, failures=2)
        assert len(calls) == 3, f"two failed renames must be retried: {calls}"
        assert w.error is None and os.path.exists(w.path), "the third attempt finishes the file"
        assert not os.path.exists(w.path + rec.PART) and warnings == [], warnings


def t_rename_that_keeps_failing_is_logged_and_left_for_finalize():
    with tempfile.TemporaryDirectory() as d:
        w, calls, warnings = _close_with_flaky_rename(d, failures=99)
        assert len(calls) == rec.RENAME_TRIES, calls
        assert os.path.exists(w.path + rec.PART) and not os.path.exists(w.path), \
            "a failed rename leaves the .part for finalize_partials"
        assert any(os.path.basename(w.path) in msg for msg in warnings), \
            f"a failed rename must be logged: {warnings}"


def t_close_wakes_an_idle_writer_at_once():
    with tempfile.TemporaryDirectory() as d:
        w = rec.RecordingWriter(d, "Demo", "dev", flush_s=5.0)
        time.sleep(0.05)                        # let the writer block in its queue wait
        t0 = time.monotonic()
        w.close()
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, f"close() must not wait for the {5.0} s flush timeout: {elapsed:.2f} s"
        assert not w._thread.is_alive(), "the writer thread must have finished"


def t_close_writes_queued_packets_before_finishing():
    with tempfile.TemporaryDirectory() as d:
        w = rec.RecordingWriter(d, "Demo", "dev", flush_s=5.0)
        items = [(1.0 + i, "A", _plain(fill=i)) for i in range(20)]
        for item in items:
            w.put(*item)
        w.close()
        assert list(rec.Recording(w.path).packets()) == items, "close() must drain every queued packet"
        w.close()                               # a second close is a no-op


def t_header_and_stem_survive_the_windows_near_epoch_localtime_bug():
    # On Windows, datetime.fromtimestamp(ts).astimezone() (no tz) raises OSError
    # (Errno 22) for small ts; the fix passes tz=utc explicitly. Simulate the
    # Windows CRT behaviour here (this machine's libc tolerates small ts fine) so
    # the regression is caught on any platform.
    import datetime as _dt
    _real = _dt.datetime

    class StrictDatetime(_real):
        @classmethod
        def fromtimestamp(cls, ts, tz=None):
            if tz is None:
                raise OSError(22, "Invalid argument")
            return _real.fromtimestamp(ts, tz)

    orig = rec.datetime.datetime
    rec.datetime.datetime = StrictDatetime
    try:
        rec._header("Demo", "dev", 1.0)          # must not raise
        with tempfile.TemporaryDirectory() as d:
            w = rec.RecordingWriter(d, "Demo", "dev", flush_s=0.05)
            w.put(1.0, "A", _plain())
            w.close()
            assert w.error is None, w.error
    finally:
        rec.datetime.datetime = orig


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


import csv  # noqa: E402


def _tpkt(lap, speed=50.0, last_ms=-1, flags=None, throttle=255, steer=None):
    size = 0x158 if steer is not None else 0x128
    b = bytearray(size)
    struct.pack_into("<I", b, 0, 0x47375330)
    struct.pack_into("<f", b, tm.OFF_SPEED, speed)
    struct.pack_into("<f", b, tm.OFF_FUEL_LEVEL, 50.0)
    struct.pack_into("<f", b, tm.OFF_FUEL_CAP, 100.0)
    for off in (tm.OFF_TYRE_FL, tm.OFF_TYRE_FR, tm.OFF_TYRE_RL, tm.OFF_TYRE_RR):
        struct.pack_into("<f", b, off, 80.0)
    struct.pack_into("<h", b, tm.OFF_LAP, lap)
    struct.pack_into("<i", b, tm.OFF_BEST_MS, -1)
    struct.pack_into("<i", b, tm.OFF_LAST_MS, last_ms)
    struct.pack_into("<H", b, tm.OFF_FLAGS, tm.FLAG_ON_TRACK if flags is None else flags)
    b[tm.OFF_THROTTLE] = throttle
    struct.pack_into("<f", b, tm.OFF_RPM, 7000.0)
    b[tm.OFF_GEAR] = 4
    struct.pack_into("<i", b, tm.OFF_CAR_ID, 999999)
    if steer is not None:
        struct.pack_into("<f", b, tm.OFF_STEER, steer)
    return bytes(b)


def _session(d):
    """Lap 0 (mid-lap connect), lap 1 of 10 s, lap 2 of 11 s, a paused packet, then
    lap 3 starts; GT7 reports lap 1's time 0.5 s into lap 2."""
    items = [(1000.0, "A", _tpkt(0))]
    t = 1000.1
    for lap, secs in ((1, 10.0), (2, 11.0)):
        for i in range(int(secs * 10)):
            last = 10000 if (lap == 2 and i >= 5) else -1
            items.append((t, "A", _tpkt(lap, last_ms=last)))
            t += 0.1
    items.append((t, "A", _tpkt(2, last_ms=10000, flags=tm.FLAG_ON_TRACK | tm.FLAG_PAUSED)))
    items.append((t + 0.1, "A", _tpkt(3, last_ms=10000)))
    return _write(d, items).path


def _rows(path, delimiter=","):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh, delimiter=delimiter))


def t_export_samples_and_laps():
    with tempfile.TemporaryDirectory() as d:
        src = _session(d)
        out = rec.export_csv(src, os.path.join(d, "out"))
        assert out["laps"] == 3 and out["dropped"] == 0, out
        samples = _rows(os.path.join(d, "out", "samples.csv"))
        assert list(samples[0]) == list(rec.SAMPLE_COLUMNS)
        assert out["samples"] == len(samples) and all(s["paused"] == "0" for s in samples)
        first = samples[0]
        assert first["t_s"] == "0.000" and first["throttle_pct"] == "100.0"
        assert first["gear"] == "4" and first["rpm"] == "7000" and first["steer_deg"] == ""
        lap1 = [s for s in samples if s["lap"] == "1"]
        assert lap1[0]["lap_dist_m"] == "0.0" and lap1[-1]["lap_dist_m"] == "495.0", lap1[-1]
        laps = _rows(os.path.join(d, "out", "laps.csv"))
        assert list(laps[0]) == list(rec.LAP_COLUMNS)
        by_lap = {r["lap"]: r for r in laps}
        assert by_lap["1"]["status"] == "reference" and by_lap["1"]["gt7_time_s"] == "10.000"
        assert by_lap["0"]["status"] == "not counted" and by_lap["0"]["gt7_time_s"] == ""
        assert by_lap["1"]["start_t_s"] == "0.100" and by_lap["2"]["start_t_s"] == "10.100", laps
        assert by_lap["1"]["car"] == "Car #999999", "an id the tables do not know keeps its number"


def t_export_all_keeps_paused_packets():
    with tempfile.TemporaryDirectory() as d:
        src = _session(d)
        rec.export_csv(src, os.path.join(d, "out"), include_all=True)
        samples = _rows(os.path.join(d, "out", "samples.csv"))
        assert any(s["paused"] == "1" for s in samples)


def t_export_excel_uses_semicolon_and_decimal_comma():
    with tempfile.TemporaryDirectory() as d:
        src = _session(d)
        rec.export_csv(src, os.path.join(d, "out"), excel=True)
        with open(os.path.join(d, "out", "samples.csv"), "rb") as fh:
            raw = fh.read()
        assert raw.startswith(b"\xef\xbb\xbf"), "Excel needs the UTF-8 BOM"
        row = _rows(os.path.join(d, "out", "samples.csv"), delimiter=";")[1]
        assert row["t_s"] == "0,100" and row["throttle_pct"] == "100,0", row


def t_export_car_name_from_tables():
    class Cars:
        def lookup(self, car_id):
            return {"id": car_id, "maker": "Porsche", "name": "911 RSR", "group": "Gr.3"}
    with tempfile.TemporaryDirectory() as d:
        src = _session(d)
        rec.export_csv(src, os.path.join(d, "out"), cars=Cars())
        laps = _rows(os.path.join(d, "out", "laps.csv"))
        assert {r["car"] for r in laps} == {"Porsche 911 RSR"}, laps


def t_export_steering_in_degrees_positive_left():
    with tempfile.TemporaryDirectory() as d:
        w = _write(d, [(1.0, "~", _tpkt(1, steer=0.5))])
        rec.export_csv(w.path, os.path.join(d, "out"))
        assert _rows(os.path.join(d, "out", "samples.csv"))[0]["steer_deg"] == "28.6"


def _control(d, default=False, **kw):
    return rec.RecordControl(os.path.join(d, "rec"), os.path.join(d, "telemetry-record.json"),
                             default, profile="Demo", relay_version="dev", **kw)


def t_record_default_tokens():
    for v in ("1", "true", "YES", " on "):
        assert rec.record_default({"RACECAST_TELEMETRY_RECORD": v}) is True, v
    for v in ("", "0", "off", "no", "maybe"):
        assert rec.record_default({"RACECAST_TELEMETRY_RECORD": v}) is False, v
    assert rec.record_default({}) is False


def t_control_state_file_beats_profile_default():
    with tempfile.TemporaryDirectory() as d:
        c = _control(d, default=True)
        assert c.status()["active"] is True, "no state file -> profile default"
        c.set_active(False)
        c.close()
        assert _control(d, default=True).status()["active"] is False, "live toggle survives a restart"
        with open(os.path.join(d, "telemetry-record.json"), "w") as fh:
            fh.write("garbage")
        assert _control(d, default=True).status()["active"] is True, "bad file -> default"


def t_control_opens_file_on_first_packet_only():
    with tempfile.TemporaryDirectory() as d:
        c = _control(d, default=True)
        assert c.status()["file"] is None and not os.path.exists(os.path.join(d, "rec"))
        c.put(time.time(), "A", _plain())
        for _ in range(50):
            if c.status()["file"]:
                break
            time.sleep(0.02)
        st = c.status()
        assert st["file"].endswith(".gt7rec") and st["since"] is not None, st
        c.close()
        assert len(rec.list_recordings(os.path.join(d, "rec"))) == 1


def t_control_off_ignores_packets_and_toggle_starts_new_file():
    with tempfile.TemporaryDirectory() as d:
        c = _control(d, default=False)
        c.put(1.0, "A", _plain())
        assert c.status()["file"] is None
        assert c.toggle()["active"] is True
        c.put(time.time(), "A", _plain())
        c.toggle()                                   # off: closes the file
        c.toggle()                                   # on again: next packet opens a new one
        c.put(time.time() + 1, "A", _plain())
        c.close()
        assert len(rec.list_recordings(os.path.join(d, "rec"))) == 2


def t_control_write_error_reported_until_next_set():
    with tempfile.TemporaryDirectory() as d:
        class Broken:
            path, started, bytes, dropped, error = None, None, 0, 0, "disk full"
            def put(self, *a): pass
            def close(self, timeout=5.0): pass
        c = _control(d, default=True, writer_factory=Broken)
        c.put(1.0, "A", _plain())
        st = c.status()
        assert st["error"] == "disk full" and st["active"] is True, st
        c.set_active(True)
        assert c.status()["error"] is None, "a fresh start clears the error"


def t_control_put_does_not_block_on_slow_close():
    class Slow:
        path, started, bytes, dropped, error = None, 1.0, 0, 0, None
        def put(self, *a): pass
        def close(self, timeout=5.0):
            time.sleep(1.0)
    with tempfile.TemporaryDirectory() as d:
        c = _control(d, default=True, writer_factory=Slow)
        c.put(1.0, "A", _plain())              # opens the (fake) writer
        th = threading.Thread(target=c.set_active, args=(False,))
        th.start()
        time.sleep(0.05)                        # let set_active grab the lock and start closing
        t0 = time.monotonic()
        c.put(2.0, "A", _plain())               # must not wait for the slow close()
        elapsed = time.monotonic() - t0
        th.join(timeout=2.0)
        c.close()
        assert elapsed < 0.2, elapsed


def t_control_put_does_not_recreate_writer_after_error():
    calls = []
    class Broken:
        path, started, bytes, dropped, error = None, None, 0, 0, "disk full"
        def put(self, *a): pass
        def close(self, timeout=5.0): pass
    def factory():
        calls.append(1)
        return Broken()
    with tempfile.TemporaryDirectory() as d:
        c = _control(d, default=True, writer_factory=factory)
        c.put(1.0, "A", _plain())
        c.put(2.0, "A", _plain())
        c.close()
        assert calls == [1], calls


def t_control_put_after_close_never_recreates_writer():
    # The relay's shutdown() calls close() while the telemetry thread may still be
    # in flight with one more packet (#786).
    calls = []
    class Fake:
        path, started, bytes, dropped, error = None, 1.0, 0, 0, None
        def put(self, *a): pass
        def close(self, timeout=5.0): pass
    def factory():
        calls.append(1)
        return Fake()
    with tempfile.TemporaryDirectory() as d:
        c = _control(d, default=True, writer_factory=factory)
        c.put(1.0, "A", _plain())        # opens the first (fake) writer
        c.close()
        c.put(2.0, "A", _plain())        # arrives after close(): must stay a no-op
        assert calls == [1], "a packet arriving after close() must not open a new file"


def t_status_elapsed_s_is_relay_clock_minus_since():
    with tempfile.TemporaryDirectory() as d:
        c = _control(d, default=True)
        c.put(1000.0, "A", _plain())
        for _ in range(50):
            if c.status()["file"]:
                break
            time.sleep(0.02)
        since = c.status()["since"]
        assert since is not None
        assert abs(c.status(now=since + 12.3)["elapsed_s"] - 12.3) < 1e-6
        c.close()
        assert c.status()["elapsed_s"] is None, "no open writer after close() -> no elapsed time"


def t_status_elapsed_s_is_none_while_idle():
    with tempfile.TemporaryDirectory() as d:
        c = _control(d, default=False)
        assert c.status()["elapsed_s"] is None


def t_control_state_file_true_beats_default_false():
    with tempfile.TemporaryDirectory() as d:
        state = os.path.join(d, "telemetry-record.json")
        with open(state, "w", encoding="utf-8") as fh:
            json.dump({"active": True}, fh)
        c = rec.RecordControl(os.path.join(d, "rec"), state, False,
                               profile="Demo", relay_version="dev")
        assert c.status()["active"] is True, "a valid state file beats default=False too"
        c.close()


def t_control_set_active_true_while_on_keeps_writer():
    with tempfile.TemporaryDirectory() as d:
        c = _control(d, default=True)
        c.put(time.time(), "A", _plain())
        for _ in range(50):
            if c.status()["file"]:
                break
            time.sleep(0.02)
        before = c.status()["file"]
        assert before
        c.set_active(True)
        assert c.status()["file"] == before, "set_active(True) while on must not open a new file"
        c.close()


def t_control_factory_exception_never_escapes_put():
    def boom():
        raise RuntimeError()
    with tempfile.TemporaryDirectory() as d:
        c = _control(d, default=True, writer_factory=boom)
        c.put(1.0, "A", _plain())              # must not raise
        assert c.status()["error"] == "RuntimeError", c.status()
        c.close()


def t_control_save_creates_missing_state_dir():
    with tempfile.TemporaryDirectory() as d:
        state = os.path.join(d, "nested", "sub", "telemetry-record.json")
        c = rec.RecordControl(os.path.join(d, "rec"), state, False,
                               profile="Demo", relay_version="dev")
        c.toggle()
        c.close()
        assert os.path.exists(state), "a toggle must persist even into a missing directory"
        with open(state, encoding="utf-8") as fh:
            assert json.load(fh)["active"] is True


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

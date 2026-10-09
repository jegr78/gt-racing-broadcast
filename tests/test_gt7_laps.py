#!/usr/bin/env python3
"""GT7 lap index: sector math, traces, the cache and the comparison pool.
Run: python3 tests/test_gt7_laps.py"""
import contextlib, math, os, struct, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import gt7_laps as gl
import gt7_recording
import gt7_telemetry as tm

R = 1000.0 / (2 * math.pi)        # a 1000 m circle around the origin
CAR = 3424


class FakeTracks:
    """Recognises any lap of 900 to 1100 m as 'ring01', the 1000 m circle."""
    def __init__(self, assigned=None, known=True):
        self.assigned, self.known, self.keys, self.learned = assigned, known, [], []

    def match(self, points, length_m):
        if self.known and 900.0 < length_m < 1100.0:
            return {"id": "ring01", "track": "Test Ring", "layout": "Full",
                    "reverse": False, "score_m": 0.4}
        return None

    def name(self, oid):
        return {"id": oid, "track": "Test Ring" if oid == "ring01" else "Other Ring",
                "layout": "Full", "reverse": False, "country": "", "length_m": 1000.0,
                "official_name": "Test Ring"}

    def layouts(self):
        return [self.name("ring01")]

    def line_length(self, oid):
        return 1000.0

    def project(self, points, oid):
        return [(math.atan2(z, x) % (2 * math.pi)) * R for x, z in points]

    def assignment(self, key):
        self.keys.append(key)
        return self.assigned

    def learn(self, oid, points, length_m, key=None):
        self.learned.append((oid, len(points), round(length_m), key))


class FakeCars:
    def lookup(self, car_id):
        return {"id": car_id, "maker": "Mitsubishi", "name": "Lancer Evolution IX",
                "group": "N"}


def _pkt(lap, speed, angle, last_ms=-1):
    b = bytearray(0x158)
    struct.pack_into("<I", b, 0, 0x47375330)
    struct.pack_into("<3f", b, tm.OFF_POS, R * math.cos(angle), 0.0, R * math.sin(angle))
    struct.pack_into("<f", b, tm.OFF_SPEED, speed)
    struct.pack_into("<f", b, tm.OFF_FUEL_LEVEL, 50.0)
    struct.pack_into("<f", b, tm.OFF_FUEL_CAP, 100.0)
    for i, off in enumerate((tm.OFF_TYRE_FL, tm.OFF_TYRE_FR, tm.OFF_TYRE_RL, tm.OFF_TYRE_RR)):
        struct.pack_into("<f", b, off, 80.0 + i)
    struct.pack_into("<h", b, tm.OFF_LAP, lap)
    struct.pack_into("<i", b, tm.OFF_BEST_MS, -1)
    struct.pack_into("<i", b, tm.OFF_LAST_MS, last_ms)
    struct.pack_into("<H", b, tm.OFF_FLAGS, tm.FLAG_ON_TRACK)
    b[tm.OFF_THROTTLE] = 255
    struct.pack_into("<f", b, tm.OFF_RPM, 7000.0)
    b[tm.OFF_GEAR] = 4
    struct.pack_into("<i", b, tm.OFF_CAR_ID, CAR)
    struct.pack_into("<f", b, tm.OFF_STEER, 0.1)
    return bytes(b)


def write_circle_recording(rec_dir, lap_secs=(20.0, 20.0, 16.0, 20.0), t0=1_700_000_000.0,
                           n=400, ns=None):
    """One lap per entry of lap_secs around the circle, `n` packets each (or ns[i] for
    lap i); lap 1 is the engine's partial first lap. From the 6th packet of a lap GT7's
    last_ms reports the previous timed lap; 10 packets of one more lap close the last one."""
    w = gt7_recording.RecordingWriter(rec_dir, "Solo", "dev", flush_s=0.05, queue_max=0)
    t, last_ms = t0, -1
    for li, secs in enumerate(list(lap_secs) + [lap_secs[-1]]):
        n = ns[li] if ns and li < len(ns) else n
        dt, speed = secs / n, 1000.0 / secs
        for k in range(n if li < len(lap_secs) else 10):
            if li >= 2 and k == 5:
                last_ms = round(lap_secs[li - 1] * 1000)
            w.put(t, "~", _pkt(li + 1, speed, 2 * math.pi * k / n, last_ms))
            t += dt
    w.close()
    return w.path


@contextlib.contextmanager
def _data_version(version):
    real = gl.gt7_data.data_version
    gl.gt7_data.data_version = lambda base, bundled=None: version
    try:
        yield
    finally:
        gl.gt7_data.data_version = real


def _index(path, tracks=None, version="v1"):
    with _data_version(version):
        return gl.index(path, tracks or FakeTracks(), FakeCars(), os.path.dirname(path),
                        key="solo/x")


def _lap(idx, n):
    return [lap for lap in idx["laps"] if lap["lap"] == n][0]


def t_index_laps_status_time_car_and_track():
    with tempfile.TemporaryDirectory() as d:
        path = write_circle_recording(d)
        idx = _index(path)
        assert [(lap["lap"], lap["status"]) for lap in idx["laps"]] == [
            (1, "not counted"), (2, "reference"), (3, "reference"), (4, "counted")], \
            "a partial first lap, two new references, then a slower counted lap"
        assert _lap(idx, 1)["gt7_time_s"] is None, "GT7 sends no time for the partial lap"
        assert (_lap(idx, 2)["gt7_time_s"], _lap(idx, 3)["time_s"]) == (20.0, 16.0), \
            "GT7's last_ms is the lap time"
        assert abs(_lap(idx, 2)["relay_time_s"] - 19.95) < 1e-6, \
            "the gap to the edge packet belongs to neither lap"
        lap2 = _lap(idx, 2)
        assert lap2["car"] == "Mitsubishi Lancer Evolution IX" and lap2["car_id"] == CAR, \
            "the car is named from the car tables"
        assert (lap2["track_id"], lap2["track"], lap2["layout"]) == \
            ("ring01", "Test Ring", "Full"), "the matched layout labels the lap"
        assert lap2["tyre_avg_c"] == [80.0, 81.0, 82.0, 83.0], "the mean of each tyre's samples"
        assert len(lap2["points"]) >= 10, "the learn flow needs the 20 m positions"
        assert idx["rec"] == gt7_recording.recording_stem(path), "rec is the recording stem"
        assert idx["start_ts"] == 1_700_000_000.0 and idx["end_ts"] > idx["start_ts"], \
            "start_ts and end_ts are the first and last packet"
        assert lap2["start_t_s"] == 20.0, "counted from the first packet"
        assert idx["track"] == {"id": "ring01", "track": "Test Ring", "layout": "Full",
                                "reverse": False}, "the display track is the session's layout"
        assert idx["sessions"] == {"1": idx["track"]}, "one session, keyed as a string"


def t_trace_follows_the_racing_line_every_5_m():
    with tempfile.TemporaryDirectory() as d:
        idx = _index(write_circle_recording(d))
        lap2 = _lap(idx, 2)
        tr = lap2["trace"]
        assert [p["d"] for p in tr[:3]] == [0.0, 5.0, 10.0] and tr[-2]["d"] == 995.0, tr[-2]
        assert (tr[-1]["d"], tr[-1]["t"]) == (1000.0, 20.0), \
            f"a counted lap's trace closes at the line length with the lap time: {tr[-1]}"
        mid = tr[100]
        assert abs(mid["t"] - 10.0) < 0.002 and mid["speed_kmh"] == 180.0, mid
        assert (mid["throttle"], mid["brake"], mid["gear"]) == (100.0, 0.0, 4), mid
        assert mid["steer_deg"] == 5.7, "0.1 rad of steering, positive to the left"
        assert abs(math.hypot(mid["x"], mid["z"]) - R) < 0.2, "positions stay on the circle"
        assert lap2["sectors"] == [4.0, 4.0, 4.0, 4.0, 4.0], lap2["sectors"]
        assert _lap(idx, 1)["trace"][-1]["d"] == 995.0, \
            "a lap that is not counted keeps its own trace end"


def t_trace_uses_driven_distance_without_track():
    with tempfile.TemporaryDirectory() as d:
        idx = _index(write_circle_recording(d), tracks=FakeTracks(known=False))
        lap2 = _lap(idx, 2)
        assert lap2["track_id"] is None and lap2["track"] == "" and idx["track"] is None
        assert abs(lap2["trace"][100]["t"] - 10.0) < 0.002, lap2["trace"][100]
        assert abs(lap2["distance_m"] - 997.5) < 0.1, lap2["distance_m"]
        assert lap2["trace"][-1]["d"] == lap2["distance_m"] and len(lap2["sectors"]) == 5, \
            "without a track the trace closes at the driven lap distance"


def t_counted_laps_close_at_the_line_so_sectors_add_up():
    with tempfile.TemporaryDirectory() as d:
        idx = _index(write_circle_recording(d, lap_secs=(20.0, 20.0, 19.0, 18.0),
                                            ns=(400, 400, 200, 100)))
        laps = [_lap(idx, n) for n in (2, 3, 4)]
        assert len({lap["trace"][-2]["d"] for lap in laps}) > 1, \
            "the fixture's laps end their 5 m grid at different distances"
        for lap in laps:
            tr = lap["trace"]
            assert tr[-1]["d"] == 1000.0 and gl.lap_length_m(lap) == 1000.0, tr[-1]
            ds, ts = [p["d"] for p in tr], [p["t"] for p in tr]
            assert ds == sorted(set(ds)) and ts == sorted(ts), "d strictly rises, t never falls"
            assert abs(sum(lap["sectors"]) - lap["time_s"]) <= 0.001, \
                f"lap {lap['lap']}: sectors {lap['sectors']} must add up to {lap['time_s']}"
        assert {len(lap["sectors"]) for lap in laps} == {5}, "same boundaries on every lap"
        best = gl.best_sectors(laps)
        assert best == [3.6, 3.6, 3.6, 3.6, 3.6], f"the 18 s lap owns every sector: {best}"
        assert gl.theoretical_best(laps) == round(sum(best), 3) == 18.0, \
            "the theoretical best equals the fastest lap when it owns every sector"


def t_assignment_for_the_recording_wins():
    with tempfile.TemporaryDirectory() as d:
        ft = FakeTracks(assigned="ring02")
        idx = _index(write_circle_recording(d), tracks=ft)
        assert "solo/x" in ft.keys, "the learned assignment is looked up by <profile>/<stem>"
        assert {lap["track_id"] for lap in idx["laps"]} == {"ring02"}, "the assignment beats matching"


def t_index_raises_recording_error_for_an_unreadable_file():
    with tempfile.TemporaryDirectory() as d:
        junk = os.path.join(d, "junk.gt7rec")
        with open(junk, "w", encoding="utf-8") as fh:
            fh.write("junk\n")
        for path in (os.path.join(d, "missing.gt7rec"), junk):
            try:
                _index(path)
            except gt7_recording.RecordingError:
                continue
            raise AssertionError(f"{path} must raise RecordingError")


def t_index_cache_is_reused_until_something_changes():
    with tempfile.TemporaryDirectory() as d:
        path = write_circle_recording(d)
        builds = []
        real = gl._build

        def counting(*args):
            builds.append(1)
            return real(*args)
        gl._build = counting
        try:
            _index(path)
            assert os.path.basename(gl.cache_path(path)) == \
                gt7_recording.recording_stem(path) + ".laps.json", "<stem>.laps.json"
            assert os.path.exists(gl.cache_path(path)), "the build writes the cache"
            _index(path)
            assert len(builds) == 1, "an unchanged recording reads the cache"
            _index(path, version="v2")
            assert len(builds) == 2, "new track data or a learned track rebuilds"
            with open(path, "ab") as fh:
                fh.write(b"\x00")                  # a truncated record the reader skips
            _index(path, version="v2")
            assert len(builds) == 3, "a grown recording rebuilds"
            st = os.stat(path)
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
            _index(path, version="v2")
            assert len(builds) == 4, "a rewritten recording of the same size rebuilds"
            with open(gl.cache_path(path), "w", encoding="utf-8") as fh:
                fh.write("{broken")
            assert _index(path, version="v2")["laps"] and len(builds) == 5, \
                "a broken cache file rebuilds"
            with _data_version("v2"):
                assert gl.cached(path, d) is not None, "cached() reads a valid cache"
        finally:
            gl._build = real


def t_index_computes_the_data_version_once():
    with tempfile.TemporaryDirectory() as d:
        path = write_circle_recording(d)
        calls = []
        real = gl.gt7_data.data_version

        def counting(base, bundled=None):
            calls.append(1)
            return "v1"
        gl.gt7_data.data_version = counting
        try:
            for _ in range(2):        # a miss, then a hit
                calls.clear()
                gl.index(path, FakeTracks(), FakeCars(), d)
                assert len(calls) == 1, f"one data_version per index() call, got {len(calls)}"
        finally:
            gl.gt7_data.data_version = real


def t_write_cache_leaves_no_temp_file_on_failure():
    with tempfile.TemporaryDirectory() as d:
        target = os.path.join(d, "x.laps.json")
        try:
            gl._write_cache(target, {"bad": object()})
        except TypeError:
            pass  # expected: the dump rejects it
        else:
            raise AssertionError("a non-JSON value must not be written silently")
        assert os.listdir(d) == [], f"the temp file is removed: {os.listdir(d)}"


def t_pool_compares_counted_laps_of_the_same_track_and_car():
    with tempfile.TemporaryDirectory() as d:
        a = _index(write_circle_recording(d))
        b = _index(write_circle_recording(d, lap_secs=(20.0, 19.0, 18.0), t0=1_700_007_200.0))
        laps = gl.pool([a, b], "ring01", CAR)
        assert [lap["time_s"] for lap in laps] == [16.0, 18.0, 19.0, 20.0, 20.0], laps
        assert all(lap["status"] in gl.COUNTED for lap in laps), "only counted laps pool"
        assert gl.pool([a, b], "ring01", 1) == [], "another car never pools"
        unknown = _index(write_circle_recording(d, t0=1_700_014_400.0),
                         tracks=FakeTracks(known=False))
        assert gl.pool([a, unknown], None, CAR) == [], "an unknown track needs its session"
        mine = gl.pool([a, unknown], None, CAR, rec=unknown["rec"], session=1)
        assert {lap["rec"] for lap in mine} == {unknown["rec"]} and len(mine) == 3, \
            "the unknown track pools its own session's counted laps"
        brief = gl.summary(mine[0])
        assert "trace" not in brief and "points" not in brief and "sectors" in brief, \
            "a summary drops the trace and the points"


class _NoneTracks(FakeTracks):
    """Projects onto the circle but returns None for the sample indexes in `missing`."""
    def __init__(self, missing):
        super().__init__()
        self.missing = missing

    def project(self, points, oid):
        out = super().project(points, oid)
        return [None if i in self.missing else v for i, v in enumerate(out)]


def _samples(ds, dt=0.1, at=None):
    """Trace samples (t, d, kmh, thr, brk, steer, gear, x, z) at driven ds, placed on the
    circle at the arc lengths `at` (default ds)."""
    at = ds if at is None else at
    return [(i * dt, d, 100.0, 50.0, 0.0, 0.0, 3, R * math.cos(a / R), R * math.sin(a / R))
            for i, (d, a) in enumerate(zip(ds, at, strict=True))]


def t_trace_distance_strictly_rises_for_a_stop_and_a_step_back():
    stopped = _samples([0.0, 3.0, 6.0, 6.0, 6.0, 6.0, 9.0, 12.0, 15.0, 18.0, 21.0])
    for tracks, oid, length in ((FakeTracks(), "ring01", 1000.0),
                                (FakeTracks(known=False), None, None)):
        tr = gl._trace(stopped, tracks, oid, length)
        ds = [p["d"] for p in tr]
        assert ds == [0.0, 5.0, 10.0, 15.0, 20.0], ds
        assert all(b["t"] >= a["t"] for a, b in zip(tr, tr[1:], strict=False)), tr
    back = list(stopped)
    x, z = R * math.cos(1.0 / R), R * math.sin(1.0 / R)
    back[7] = back[7][:7] + (x, z)                 # a projection that falls back to 1 m
    tr = gl._trace(back, FakeTracks(), "ring01", 1000.0)
    ds = [p["d"] for p in tr]
    assert ds == sorted(set(ds)) and ds[-1] == 20.0, ds
    assert tr[2]["t"] == 0.633, f"10 m lies between the 9 m and 15 m samples, the step back is dropped: {tr[2]}"
    assert gl.sectors(tr, gl.lap_length_m({"trace": tr}), step_m=10.0), "sector math accepts it"


def t_trace_closes_off_grid_below_and_beyond_its_samples():
    drv = [0.0, 3.0, 6.0, 9.0, 12.0, 15.0, 18.0, 21.0]
    short = gl._trace(_samples(drv), FakeTracks(known=False), None, None, close=(12.34, 9.9))
    assert [p["d"] for p in short] == [0.0, 5.0, 10.0, 12.3] and short[-1]["t"] == 9.9, \
        f"stations past the lap length go, the closing point sits at it: {short}"
    long = gl._trace(_samples(drv), FakeTracks(known=False), None, None, close=(30.0, 9.9))
    assert [p["d"] for p in long][-2:] == [20.0, 30.0] and long[-1]["speed_kmh"] == 100.0, \
        f"a closing point past the last sample keeps that sample's values: {long[-1]}"
    assert gl._trace(_samples(drv), FakeTracks(known=False), None, None, close=(0.0, 1.0)) \
        == gl._trace(_samples(drv), FakeTracks(known=False), None, None), "no length, no closing"


def t_trace_none_projection_continues_from_the_last_projected_distance():
    drv = [0.0, 10.0, 20.0, 30.0, 40.0, 50.0]
    tr = gl._trace(_samples(drv, at=[1.1 * d for d in drv]), _NoneTracks({3}), "ring01", 1000.0)
    assert tr[6]["t"] == 0.28, \
        f"22 m projected plus 10 m driven puts the unprojected sample at 32 m: {tr[6]}"


def t_trace_ignores_a_projection_onto_another_branch_of_the_line():
    ds = [i * 10.0 for i in range(100)]
    for jump in (400.0, -300.0):
        at = list(ds)
        at[20] += jump
        tr = gl._trace(_samples(ds, dt=0.2, at=at), FakeTracks(), "ring01", 1000.0)
        got = gl.sectors(tr, gl.lap_length_m({"trace": tr}))
        assert got == [4.0, 4.0, 4.0, 4.0, 3.8], \
            f"one sample projected {jump:+} m away must not distort the lap: {got}"


def _trace(*sector_secs, sector=200.0, step=5.0):
    """Stations every 5 m; sector i of 200 m takes sector_secs[i] s at constant speed."""
    pts, t = [], 0.0
    for i, secs in enumerate(sector_secs):
        v = sector / secs
        for k in range(0 if i == 0 else 1, int(sector / step) + 1):
            pts.append({"d": i * sector + k * step, "t": round(t + k * step / v, 3),
                        "speed_kmh": round(v * 3.6, 1), "throttle": 100.0, "brake": 0.0,
                        "steer_deg": 0.0, "gear": 4, "x": 0.0, "z": 0.0})
        t += secs
    return pts


def t_sectors_every_200_m_from_the_line():
    tr = _trace(4.0, 4.0, 4.0, 4.0, 4.0)
    assert tr[0]["d"] == 0.0 and tr[-1]["d"] == 1000.0
    assert gl.lap_length_m({"trace": tr}) == 1000.0
    assert gl.sectors(tr, 1000.0) == [4.0, 4.0, 4.0, 4.0, 4.0]


def t_sectors_short_last_sector():
    tr = _trace(4.0, 4.0, 4.0, 4.0, 4.0)
    assert gl.sectors(tr, 1000.0, step_m=300.0) == [6.0, 6.0, 6.0, 2.0], \
        "boundaries 0/300/600/900 and the line at 1000"
    short = tr[:-1]                                # the trace ends at 995 m
    assert gl.sectors(short, gl.lap_length_m({"trace": short})) == [4.0, 4.0, 4.0, 4.0, 3.9]


def t_sectors_interpolates_between_stations():
    tr = _trace(4.0, 4.0, 4.0, 4.0, 4.0)
    assert gl.sectors(tr, 1000.0, step_m=203.0) == [4.06, 4.06, 4.06, 4.06, 3.76], \
        "boundaries at 203/406/609/812 fall between 5 m stations and must be interpolated"


def t_sectors_beyond_the_trace_have_no_time():
    tr = _trace(4.0, 4.0, 4.0, 4.0, 4.0)[:-4]     # ends at 980 m
    assert gl.sectors(tr, 1000.0)[-1] is None, "no time is invented past the trace"
    assert gl.sectors([], 1000.0) == [] and gl.sectors(tr, 0.0) == []
    assert gl.lap_length_m({"trace": []}) == 0.0 and gl.lap_length_m({}) == 0.0


def t_best_sectors_and_theoretical_best():
    a = {"trace": _trace(4.0, 4.0, 4.0, 4.0, 4.0)}
    b = {"trace": _trace(3.5, 4.5, 4.0, 4.2, 3.8)}
    assert gl.best_sectors([a, b]) == [3.5, 4.0, 4.0, 4.0, 3.8]
    assert gl.theoretical_best([a, b]) == 19.3
    assert gl.best_sectors([{"trace": []}]) == [] and gl.theoretical_best([]) is None
    assert gl.theoretical_best([{"trace": []}]) is None, "a lap without trace adds nothing"
    short = {"trace": _trace(4.0, 4.0, 4.0, 4.0, 4.0)[:-4]}      # ends at 980 m
    assert gl.best_sectors([short])[-1] == 3.6, "its own trace end closes the last sector"


def t_theoretical_best_clamped_to_the_fastest_lap_time():
    """Rounding each sector to the millisecond can sum above the lap that set every
    one of them; the theoretical best must never beat the actual fastest lap."""
    lap = {"sectors": [4.445] * 10, "time_s": 44.446}
    assert gl.theoretical_best([lap]) == 44.446, \
        "the rounded sector sum (44.450) must clamp to the lap's own time"
    assert gl.theoretical_best([{"trace": []}]) is None, "a lap without a time still clamps nothing"
    assert gl.theoretical_best([{"sectors": [4.445] * 10}]) == 44.45, \
        "a lap without time_s is tolerated and left unclamped"


def t_best_sectors_reads_the_sectors_of_a_summary():
    a = {"trace": _trace(4.0, 4.0, 4.0, 4.0, 4.0)}
    b = {"trace": _trace(3.5, 4.5, 4.0, 4.2, 3.8)}
    for lap in (a, b):
        lap["sectors"] = gl.sectors(lap["trace"], gl.lap_length_m(lap))
    brief = [gl.summary(a), gl.summary(b)]
    assert gl.best_sectors(brief) == gl.best_sectors([a, b]) == [3.5, 4.0, 4.0, 4.0, 3.8], \
        "a lap without its trace still has its sector times"
    assert gl.theoretical_best(brief) == 19.3


def t_write_cache_temp_file_carries_the_recording_stem():
    with tempfile.TemporaryDirectory() as d:
        target = os.path.join(d, "rec1.laps.json")
        seen = []
        real = gl.os.replace
        gl.os.replace = lambda src, dst: seen.append(os.path.basename(src)) or real(src, dst)
        try:
            gl._write_cache(target, {"a": 1})
        finally:
            gl.os.replace = real
        assert seen and seen[0].startswith("rec1.laps-") and seen[0].endswith(".tmp"), \
            f"a leftover temp file names its recording: {seen}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

#!/usr/bin/env python3
"""Stdlib unit checks for GT7 telemetry parsing + engine. Run: python3 tests/test_gt7_telemetry.py"""
import importlib.util, os, struct

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


tm = _load("gt7_telemetry", ("src", "scripts", "gt7_telemetry.py"))


def _packet(**kw):
    """Build a decrypted packet-'A' buffer with the given field values."""
    b = bytearray(0x128)
    struct.pack_into("<I", b, tm.OFF_MAGIC, 0x47375330)
    struct.pack_into("<f", b, tm.OFF_FUEL_LEVEL, kw.get("fuel_level", 60.0))
    struct.pack_into("<f", b, tm.OFF_FUEL_CAP, kw.get("fuel_capacity", 60.0))
    struct.pack_into("<f", b, tm.OFF_SPEED, kw.get("speed_mps", 0.0))
    fl, fr, rl, rr = kw.get("tyre_temp", (80.0, 80.0, 80.0, 80.0))
    struct.pack_into("<f", b, tm.OFF_TYRE_FL, fl)
    struct.pack_into("<f", b, tm.OFF_TYRE_FR, fr)
    struct.pack_into("<f", b, tm.OFF_TYRE_RL, rl)
    struct.pack_into("<f", b, tm.OFF_TYRE_RR, rr)
    struct.pack_into("<h", b, tm.OFF_LAP, kw.get("lap", 1))
    struct.pack_into("<i", b, tm.OFF_BEST_MS, kw.get("best_ms", -1))
    struct.pack_into("<i", b, tm.OFF_LAST_MS, kw.get("last_ms", -1))
    struct.pack_into("<i", b, tm.OFF_DAY_PROGRESSION, kw.get("day_ms", 0))
    struct.pack_into("<H", b, tm.OFF_FLAGS, kw.get("flags", tm.FLAG_ON_TRACK))
    b[tm.OFF_THROTTLE] = kw.get("throttle", 0)
    b[tm.OFF_BRAKE] = kw.get("brake", 0)
    return bytes(b)


def t_parse_fields():
    p = tm.parse_packet(_packet(speed_mps=50.0, throttle=255, brake=0,
                                tyre_temp=(70.0, 85.0, 60.0, 100.0), lap=3,
                                fuel_level=42.5, flags=tm.FLAG_ON_TRACK))
    assert abs(p.speed_mps - 50.0) < 1e-3
    assert p.throttle == 255 and p.brake == 0
    assert p.tyre_temp == (70.0, 85.0, 60.0, 100.0)
    assert p.lap == 3
    assert abs(p.fuel_level - 42.5) < 1e-3
    assert p.on_track is True and p.paused is False


def t_parse_flags():
    p = tm.parse_packet(_packet(flags=tm.FLAG_PAUSED | tm.FLAG_LOADING))
    assert p.on_track is False and p.paused is True and p.loading is True


def _feed_lap(eng, t0, lap, *, duration=10.0, dt=0.1, speed=50.0,
              flags=None, fuel_start=None):
    """Drive one synthetic lap of constant speed; returns the end timestamp.
    Emits packets across [t0, t0+duration) with the given lap number, then one
    packet at the end carrying lap+1 (the lap-change edge)."""
    flags = tm.FLAG_ON_TRACK if flags is None else flags
    t = t0
    n = int(duration / dt)
    for _ in range(n):
        kw = dict(speed_mps=speed, lap=lap, flags=flags)
        if fuel_start is not None:
            kw["fuel_level"] = fuel_start
        eng.update(tm.parse_packet(_packet(**kw)), t)
        t += dt
    # lap-change edge:
    eng.update(tm.parse_packet(_packet(speed_mps=speed, lap=lap + 1, flags=flags)), t)
    return t


def t_engine_no_reference_before_first_lap():
    eng = tm.TelemetryEngine()
    eng.update(tm.parse_packet(_packet(speed_mps=40.0, lap=1)), 100.0)
    s = eng.snapshot()
    assert s["has_reference"] is False
    assert s["delta_s"] is None and s["predicted_s"] is None
    assert abs(s["speed_mps"] - 40.0) < 1e-3


def t_engine_reference_after_clean_lap():
    eng = tm.TelemetryEngine()
    eng.update(tm.parse_packet(_packet(lap=0)), 99.0)     # mid-connect partial (discarded)
    _feed_lap(eng, 100.0, 1, duration=10.0, speed=50.0)   # lap 1 opens at a boundary -> ~500 m/10 s
    s = eng.snapshot()
    assert s["has_reference"] is True
    assert s["best_s"] is not None and 9.0 < s["best_s"] < 11.0


def t_engine_midlap_connect_partial_not_reference():
    """The FIRST lap after the relay connects is a mid-lap partial (not opened at
    the start/finish line) and must NEVER become the reference, or a 3-second
    partial locks best/delta/predicted for the whole broadcast. The first FULL
    lap opened at a boundary becomes the reference. (#324)"""
    eng = tm.TelemetryEngine()
    # Connect ~3 s before the line, then cross it: a short partial lap.
    t = 100.0
    for _ in range(30):
        eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=7)), t); t += 0.1
    eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=8)), t)   # line crossing
    assert eng.snapshot()["has_reference"] is False   # partial rejected, not a reference
    # Now a full clean boundary lap (lap 8 -> 9) sets the reference:
    _feed_lap(eng, t + 0.1, 8, duration=10.0, speed=50.0)
    s = eng.snapshot()
    assert s["has_reference"] is True and 9.0 < s["best_s"] < 11.0


def t_engine_delta_negative_when_faster():
    eng = tm.TelemetryEngine()
    eng.update(tm.parse_packet(_packet(lap=0)), 99.0)          # mid-connect partial (discarded)
    _feed_lap(eng, 100.0, 1, duration=10.0, speed=50.0)         # reference ~10 s / 500 m (boundary)
    # Lap 2, faster (higher speed -> same distance reached earlier -> negative delta):
    t = 120.0
    for _ in range(30):                                          # 3 s in, well ahead on distance
        eng.update(tm.parse_packet(_packet(speed_mps=100.0, lap=2)), t)
        t += 0.1
    s = eng.snapshot()
    assert s["delta_s"] is not None and s["delta_s"] < 0
    assert s["predicted_s"] is not None


def _ref_then_partial(speed2, secs=3.0):
    """Set a 50 m/s reference lap, then drive `secs` of lap 2 at speed2 m/s.
    Returns (engine, next_free_timestamp)."""
    eng = tm.TelemetryEngine()
    eng.update(tm.parse_packet(_packet(lap=0)), 99.0)          # mid-connect partial (discarded)
    _feed_lap(eng, 100.0, 1, duration=10.0, speed=50.0)         # reference ~10 s / 500 m
    t = 120.0
    for _ in range(int(secs / 0.1)):
        eng.update(tm.parse_packet(_packet(speed_mps=speed2, lap=2)), t); t += 0.1
    return eng, t


def t_engine_delta_dir_down_when_gaining():
    eng, _ = _ref_then_partial(100.0)      # faster than the 50 m/s reference -> gap shrinking
    assert eng.snapshot()["delta_dir"] == "down"


def t_engine_delta_dir_up_when_losing():
    eng, _ = _ref_then_partial(40.0)       # slower than reference -> gap growing
    assert eng.snapshot()["delta_dir"] == "up"


def t_engine_delta_dir_flat_when_matching():
    eng, _ = _ref_then_partial(50.0)       # matching reference pace -> within deadband
    assert eng.snapshot()["delta_dir"] == "flat"


def t_engine_delta_dir_none_without_reference():
    eng = tm.TelemetryEngine()
    eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=1)), 100.0)
    assert eng.snapshot()["delta_dir"] is None


def t_engine_delta_dir_cleared_on_lap_edge():
    """The trend history must not carry across the start/finish line: right after a
    lap-change edge there are <2 samples, so delta_dir is None (no phantom trend)."""
    eng, t = _ref_then_partial(40.0)       # building an "up" trend on lap 2
    assert eng.snapshot()["delta_dir"] == "up"
    eng.update(tm.parse_packet(_packet(speed_mps=40.0, lap=3)), t)   # lap edge -> history cleared
    assert eng.snapshot()["delta_dir"] is None


def t_engine_delta_dir_deadband_boundary():
    """Classification uses strict > / < DEADBAND: exactly at the boundary reads 'flat'."""
    eng = tm.TelemetryEngine()
    eng._ref = {"time": 10.0, "samples": [(0.0, 0.0), (100.0, 10.0)]}   # makes has_reference true

    def dir_for(diff):
        eng._delta_hist.clear()
        eng._delta_hist.append((0.0, 0.0))
        eng._delta_hist.append((1.0, diff))
        return eng.snapshot()["delta_dir"]

    assert dir_for(tm.DELTA_TREND_DEADBAND) == "flat"            # exactly at +boundary (not > )
    assert dir_for(tm.DELTA_TREND_DEADBAND + 0.001) == "up"      # just above -> losing
    assert dir_for(-tm.DELTA_TREND_DEADBAND) == "flat"          # exactly at -boundary (not < )
    assert dir_for(-tm.DELTA_TREND_DEADBAND - 0.001) == "down"   # just below -> gaining


def t_engine_replay_makes_no_phantom_lap():
    eng = tm.TelemetryEngine()
    # A "lap change" while paused/loading (menu/replay) must NOT set a reference.
    eng.update(tm.parse_packet(_packet(lap=1, flags=tm.FLAG_PAUSED)), 100.0)
    eng.update(tm.parse_packet(_packet(lap=2, flags=tm.FLAG_PAUSED)), 101.0)
    assert eng.snapshot()["has_reference"] is False


def t_engine_pause_midlap_marks_unclean():
    # A mid-lap pause (nonzero dt, paused flag) must prevent the lap becoming a reference.
    eng = tm.TelemetryEngine()
    t = 100.0
    for _ in range(50):
        eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=1)), t); t += 0.1
    t += 0.5
    eng.update(tm.parse_packet(_packet(speed_mps=0.0, lap=1, flags=tm.FLAG_PAUSED)), t)
    t += 0.1
    for _ in range(50):
        eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=1)), t); t += 0.1
    eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=2)), t)
    assert eng.snapshot()["has_reference"] is False


def t_engine_long_gap_midlap_marks_unclean():
    # A >2s stall WITHOUT a pause flag (network hiccup) also invalidates the lap.
    eng = tm.TelemetryEngine()
    t = 100.0
    for _ in range(50):
        eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=1)), t); t += 0.1
    t += 5.0
    for _ in range(50):
        eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=1)), t); t += 0.1
    eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=2)), t)
    assert eng.snapshot()["has_reference"] is False


def t_engine_fuel_after_two_laps():
    eng = tm.TelemetryEngine()
    # Lap 1: start 60 L. Lap 2: start 57 L (3 L/lap). Lap 3: start 54 L.
    t = _feed_lap(eng, 100.0, 1, duration=10.0, speed=50.0, fuel_start=60.0)
    t = _feed_lap(eng, t, 2, duration=10.0, speed=50.0, fuel_start=57.0)
    _feed_lap(eng, t, 3, duration=10.0, speed=50.0, fuel_start=54.0)
    f = eng.snapshot()["fuel"]
    assert f["per_lap"] is not None and abs(f["per_lap"] - 3.0) < 0.5
    # 54 L left / 3 L per lap ~ 18 laps; each lap ~10 s -> ~180 s.
    assert 15 < f["laps_remaining"] < 21
    assert 150 < f["time_remaining_s"] < 210


def t_engine_fuel_none_before_two_laps():
    eng = tm.TelemetryEngine()
    eng.update(tm.parse_packet(_packet(fuel_level=60.0, lap=1)), 100.0)
    f = eng.snapshot()["fuel"]
    assert f["per_lap"] is None and f["laps_remaining"] is None


def t_engine_stall_at_lap_start_marks_unclean():
    # A >2s stall right at the start of a lap (before any elapsed accumulates) must
    # still invalidate the lap; it must NOT become the reference.
    eng = tm.TelemetryEngine()
    t = 100.0
    eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=1)), t)   # opens the accumulator
    t += 3.0                                                          # stall, elapsed still 0
    for _ in range(80):
        eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=1)), t); t += 0.1
    eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=2)), t)   # finalise lap 1
    assert eng.snapshot()["has_reference"] is False


def t_engine_fuel_continuous_decay():
    # Realistic: fuel drains continuously within each lap (~2 L/lap), not stepwise.
    eng = tm.TelemetryEngine()
    t = 100.0
    fuel = 50.0
    for lap in (1, 2, 3):
        for _ in range(100):
            eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=lap, fuel_level=fuel)), t)
            fuel -= 2.0 / 100
            t += 0.1
    eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=4, fuel_level=fuel)), t)
    f = eng.snapshot()["fuel"]
    assert f["per_lap"] is not None and abs(f["per_lap"] - 2.0) < 0.3


def t_engine_fuel_per_lap_is_whole_session_not_last3():
    # Four completed fuel laps with burns 6/2/2/2 L: the whole-session mean is
    # 3.0 L, while a rolling last-3 window would give 2.0 L. fuel_start drops
    # 60->54 (6 L burn), then 54->52->50->48 (2 L each); the 5th feed only closes
    # lap 4 (its own burn is non-positive and excluded).
    eng = tm.TelemetryEngine()
    t = 100.0
    for lap, fs in ((1, 60.0), (2, 54.0), (3, 52.0), (4, 50.0), (5, 48.0)):
        t = _feed_lap(eng, t, lap, duration=10.0, speed=50.0, fuel_start=fs)
    per_lap = eng.snapshot()["fuel"]["per_lap"]
    assert per_lap is not None
    assert abs(per_lap - 3.0) < 0.5, per_lap   # whole-session 3.0, not last-3 (~2.0)


def t_engine_trace_decimates_and_windows():
    eng = tm.TelemetryEngine()
    t = 100.0
    # 60 Hz for 20 s: raw 1200 samples, decimated to ~30 Hz, windowed to 15 s.
    for i in range(1200):
        thr = 255 if i % 2 == 0 else 0
        eng.update(tm.parse_packet(_packet(throttle=thr, brake=0, lap=1)), t)
        t += 1.0 / 60
    tr = eng.trace_batch(limit=10_000)
    assert tr, "trace should not be empty"
    # decimated to ~30 Hz over 15 s window -> ~450 samples, well under raw 1200:
    assert len(tr) < 700
    # window bound: oldest sample within ~15 s of the newest:
    assert tr[-1]["t"] - tr[0]["t"] <= tm.TRACE_WINDOW_S + 0.5
    # normalised 0-1:
    assert all(0.0 <= s["throttle"] <= 1.0 for s in tr)


def t_engine_trace_batch_limit():
    eng = tm.TelemetryEngine()
    t = 100.0
    for _ in range(300):
        eng.update(tm.parse_packet(_packet(throttle=128, lap=1)), t)
        t += 1.0 / 30
    assert len(eng.trace_batch(limit=50)) == 50


def t_format_metric_and_bands():
    snap = {"speed_mps": 50.0, "tyre_temp": (65.0, 78.0, 90.0, 99.0),
            "tyre_temp_avg": (65.0, 78.0, 90.0, 99.0), "top_speed_mps": 55.0,
            "lap": 4, "current_lap_s": 12.3, "best_s": 95.4,
            "delta_s": -0.42, "predicted_s": 94.98, "has_reference": True,
            "time_of_day_ms": 45000000,
            "fuel": {"level": 40.0, "per_lap": 2.5, "laps_remaining": 16.0,
                     "time_remaining_s": 1600.0}}
    out = tm.format_snapshot(snap, "metric", (70, 85, 95))
    assert out["speed"] == 180          # 50 m/s = 180 km/h
    assert out["units"]["speed"] == "km/h"
    assert [t["band"] for t in out["tyres"]] == ["cold", "optimal", "hot", "critical"]
    assert out["tyres"][0]["value"] == 65    # °C
    assert out["delta"] == -0.42
    assert out["has_reference"] is True


def t_format_imperial_converts_tyres():
    snap = {"speed_mps": 50.0, "tyre_temp": (70.0, 70.0, 70.0, 70.0),
            "tyre_temp_avg": (70.0, 70.0, 70.0, 70.0), "top_speed_mps": 50.0,
            "lap": 1, "current_lap_s": 0.0, "best_s": None,
            "delta_s": None, "predicted_s": None, "has_reference": False,
            "time_of_day_ms": None,
            "fuel": {"level": 10.0, "per_lap": None,
                     "laps_remaining": None, "time_remaining_s": None}}
    out = tm.format_snapshot(snap, "imperial", (70, 85, 95))
    assert out["units"]["speed"] == "mph" and out["units"]["temp"] == "°F"
    assert out["tyres"][0]["value"] == 158     # 70°C -> 158°F
    assert out["tyres"][0]["band"] == "optimal"  # band still computed in °C
    assert out["speed"] == 112                 # 50 m/s -> 111.8 mph -> 112


def t_engine_top_speed_tracks_onair_max():
    eng = tm.TelemetryEngine()
    eng.update(tm.parse_packet(_packet(speed_mps=40.0, lap=1)), 100.0)
    eng.update(tm.parse_packet(_packet(speed_mps=80.0, lap=1)), 100.1)
    eng.update(tm.parse_packet(_packet(speed_mps=55.0, lap=1)), 100.2)
    # a higher speed while paused/off-track must NOT count (menu/replay artefact):
    eng.update(tm.parse_packet(_packet(speed_mps=200.0, lap=1, flags=tm.FLAG_PAUSED)), 100.3)
    assert abs(eng.snapshot()["top_speed_mps"] - 80.0) < 1e-6


def t_engine_tyre_avg_windowed():
    eng = tm.TelemetryEngine()
    t = 100.0
    # 40 s of FL=60, then 10 s of FL=100 -> the 30 s average should be pulled
    # toward 100 (the >30 s-old 60s samples fall out of the window).
    for _ in range(400):
        eng.update(tm.parse_packet(_packet(tyre_temp=(60.0, 60.0, 60.0, 60.0), lap=1)), t); t += 0.1
    for _ in range(100):
        eng.update(tm.parse_packet(_packet(tyre_temp=(100.0, 100.0, 100.0, 100.0), lap=1)), t); t += 0.1
    avg_fl = eng.snapshot()["tyre_temp_avg"][0]
    # With a 30s window, only the trailing 20s of the 60C block and the 10s of the
    # 100C block remain (~73.3C), above the naive full-history average (68.0C).
    assert avg_fl > 70.0, avg_fl          # window no longer contains the old 60s block fully


def t_format_includes_top_speed_and_tyre_avg():
    snap = {"speed_mps": 50.0, "tyre_temp": (70.0, 70.0, 70.0, 70.0),
            "tyre_temp_avg": (68.0, 69.0, 71.0, 72.0), "top_speed_mps": 90.0,
            "lap": 1, "current_lap_s": 0.0, "best_s": None, "delta_s": None,
            "predicted_s": None, "has_reference": False,
            "time_of_day_ms": None,
            "fuel": {"level": 10.0, "per_lap": None, "laps_remaining": None,
                     "time_remaining_s": None}}
    out = tm.format_snapshot(snap, "metric", (70, 85, 95))
    assert out["top_speed"] == 324             # 90 m/s -> 324 km/h
    assert out["tyres"][0]["avg"] == 68 and out["tyres"][0]["value"] == 70
    imp = tm.format_snapshot(snap, "imperial", (70, 85, 95))
    assert imp["top_speed"] == 201             # 90 m/s -> 201 mph
    assert imp["tyres"][0]["avg"] == 154       # 68 C -> 154 F


def _feed_lap_store(st, t0, lap, *, duration, speed, dt=0.1):
    t = t0
    for _ in range(int(duration / dt)):
        st.update(tm.parse_packet(_packet(speed_mps=speed, lap=lap)), t)
        t += dt
    st.update(tm.parse_packet(_packet(speed_mps=speed, lap=lap + 1)), t)


def t_store_roundtrips_reference(tmp_path=None):
    import tempfile
    d = tempfile.mkdtemp()
    path = os.path.join(d, "telemetry.json")
    st = tm.TelemetryStore(path, units="metric")
    st.update(tm.parse_packet(_packet(lap=0)), 99.0)      # mid-connect partial (discarded)
    _feed_lap_store(st, 100.0, 1, duration=10.0, speed=50.0)   # boundary lap -> reference
    assert st.data()["has_reference"] is True
    # A new store on the same path recovers the reference lap (default reset=False):
    st2 = tm.TelemetryStore(path, units="metric")
    assert st2.data()["has_reference"] is True


def t_store_reset_drops_persisted_reference():
    """The relay constructs the store with reset=True: a fresh session must NOT
    load a stale reference from a previous (possibly different track) run, and
    the stale file is removed. (#324)"""
    import tempfile, json as _json
    d = tempfile.mkdtemp()
    path = os.path.join(d, "telemetry.json")
    with open(path, "w", encoding="utf-8") as fh:
        _json.dump({"time": 42.0, "samples": [[0.0, 0.0], [100.0, 42.0]]}, fh)
    st = tm.TelemetryStore(path, units="metric", reset=True)
    assert st.data()["has_reference"] is False        # stale reference not loaded
    assert not os.path.exists(path)                    # and the stale file was cleared


def t_engine_samples_capped_under_flood():
    """A same-lap packet flood (lap held constant, distance forced up) must not
    grow _LapAccumulator.samples without bound. The cap marks the lap unclean so
    it cannot become a reference and memory stays bounded. (#324)"""
    eng = tm.TelemetryEngine()
    t = 100.0
    eng.update(tm.parse_packet(_packet(speed_mps=90.0, lap=1)), t); t += 0.1
    for _ in range(tm.MAX_SAMPLES + 500):     # flood, lap never changes
        eng.update(tm.parse_packet(_packet(speed_mps=90.0, lap=1)), t); t += 0.1
    assert len(eng._acc.samples) <= tm.MAX_SAMPLES     # bounded
    assert eng._acc.clean is False                     # flooded lap invalidated
    eng.update(tm.parse_packet(_packet(speed_mps=90.0, lap=2)), t)  # lap edge
    assert eng.snapshot()["has_reference"] is False    # the bogus lap never became a reference


def t_band_critical_strictly_above_threshold():
    """critical is >crit, so exactly at the threshold reads 'hot'. (#324)"""
    assert tm._band(95.0, (70, 85, 95)) == "hot"
    assert tm._band(95.01, (70, 85, 95)) == "critical"
    assert tm._band(85.0, (70, 85, 95)) == "optimal"


def t_engine_session_reset_on_lap_backwards():
    eng = tm.TelemetryEngine()
    eng.update(tm.parse_packet(_packet(lap=0)), 99.0)         # mid-connect partial
    _feed_lap(eng, 100.0, 1, duration=10.0, speed=50.0)       # sets a reference; now on lap 2
    assert eng.snapshot()["has_reference"] is True
    assert eng.snapshot()["top_speed_mps"] > 0.0
    # a packet whose lap counter dropped => session boundary => full reset
    eng.update(tm.parse_packet(_packet(lap=0, speed_mps=0.0)), 130.0)
    s = eng.snapshot()
    assert s["has_reference"] is False
    assert s["top_speed_mps"] == 0.0
    # a fresh clean lap re-establishes a reference
    _feed_lap(eng, 131.0, 1, duration=10.0, speed=40.0)
    assert eng.snapshot()["has_reference"] is True


def t_engine_session_reset_on_best_cleared():
    eng = tm.TelemetryEngine()
    eng.update(tm.parse_packet(_packet(lap=0)), 99.0)
    _feed_lap(eng, 100.0, 1, duration=10.0, speed=50.0)
    assert eng.snapshot()["has_reference"] is True
    # best carries a real value, then clears to -1 (GT7 wipes it on a session change)
    eng.update(tm.parse_packet(_packet(lap=2, best_ms=95000)), 130.0)
    eng.update(tm.parse_packet(_packet(lap=2, best_ms=-1)), 130.1)
    assert eng.snapshot()["has_reference"] is False


def t_engine_no_reset_on_normal_lap_increment():
    eng = tm.TelemetryEngine()
    eng.update(tm.parse_packet(_packet(lap=0)), 99.0)
    _feed_lap(eng, 100.0, 1, duration=10.0, speed=50.0)       # ref from lap 1, now on lap 2
    assert eng.snapshot()["has_reference"] is True
    _feed_lap(eng, 120.0, 2, duration=10.0, speed=50.0)       # forward 2 -> 3: NO reset
    assert eng.snapshot()["has_reference"] is True


def t_engine_time_of_day_survives_session_reset():
    """The on-track clock keeps ticking through a session reset (it reads _last,
    which the reset deliberately does not clear) even though the reference drops."""
    eng = tm.TelemetryEngine()
    eng.update(tm.parse_packet(_packet(lap=0)), 99.0)
    _feed_lap(eng, 100.0, 1, duration=10.0, speed=50.0)       # ref set, now on lap 2
    assert eng.snapshot()["has_reference"] is True
    # a session-boundary packet (lap backwards) carrying a real time-of-day
    eng.update(tm.parse_packet(_packet(lap=0, speed_mps=0.0, day_ms=45000000)), 130.0)
    s = eng.snapshot()
    assert s["has_reference"] is False                        # reset happened
    assert s["time_of_day_ms"] == 45000000                    # clock kept, not blanked


def t_engine_avg_lap_is_whole_session_not_last3():
    # Four clean laps of different durations: the average must be the mean of ALL
    # four, not just the last three (proves the rolling-3 window is gone).
    eng = tm.TelemetryEngine()
    eng.update(tm.parse_packet(_packet(lap=0)), 99.0)
    t = 100.0
    for lap, dur in ((1, 10.0), (2, 20.0), (3, 12.0), (4, 18.0)):
        _feed_lap(eng, t, lap, duration=dur, speed=50.0)
        t += dur
    # lap 5 edge already closed lap 4 inside _feed_lap's next call chain; read avg
    avg = eng.snapshot()["avg_lap_s"]
    assert avg is not None
    assert abs(avg - (10.0 + 20.0 + 12.0 + 18.0) / 4) < 0.5, avg   # ~15.0, not last-3 (~16.67)


def t_engine_avg_lap_none_without_laps():
    eng = tm.TelemetryEngine()
    eng.update(tm.parse_packet(_packet(lap=1, speed_mps=50.0)), 100.0)
    assert eng.snapshot()["avg_lap_s"] is None


def t_format_surfaces_avg_lap():
    snap = {"speed_mps": 0.0, "tyre_temp": (70, 70, 70, 70), "lap": 3,
            "current_lap_s": 5.0, "best_s": 90.0, "delta_s": None, "predicted_s": None,
            "has_reference": True, "tyre_temp_avg": (70.0, 70.0, 70.0, 70.0),
            "top_speed_mps": 0.0, "time_of_day_ms": None, "avg_lap_s": 92.5,
            "session_dist_m": 0.0,
            "fuel": {"level": 40.0, "per_lap": 2.5, "laps_remaining": 16.0,
                     "time_remaining_s": 1600.0}}
    out = tm.format_snapshot(snap, "metric", (70, 85, 95))
    assert out["avg_lap"] == "1:32.500"
    snap["avg_lap_s"] = None
    assert tm.format_snapshot(snap, "metric", (70, 85, 95))["avg_lap"] is None


def t_engine_pit_lap_via_standstill_excluded():
    eng = tm.TelemetryEngine()
    eng.update(tm.parse_packet(_packet(lap=0)), 99.0)
    t = 100.0
    for _ in range(80):                                   # ~8 s driving
        eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=1)), t); t += 0.1
    for _ in range(30):                                   # ~3 s stationary (pit box)
        eng.update(tm.parse_packet(_packet(speed_mps=0.0, lap=1)), t); t += 0.1
    eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=2)), t)   # lap edge
    assert eng.snapshot()["has_reference"] is False       # pit lap never became reference
    assert eng._lap_time_n == 0 and eng._lap_fuel_n == 0
    assert eng.snapshot()["avg_lap_s"] is None


def t_engine_pit_lap_via_fuel_rise_excluded():
    eng = tm.TelemetryEngine()
    eng.update(tm.parse_packet(_packet(lap=0, fuel_level=20.0)), 99.0)
    t = 100.0
    for i in range(100):                                  # fuel jumps up mid-lap = refuel
        fuel = 20.0 + (10.0 if i > 50 else 0.0)
        eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=1, fuel_level=fuel)), t); t += 0.1
    eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=2, fuel_level=30.0)), t)
    assert eng.snapshot()["has_reference"] is False


def t_engine_brief_slowdown_not_pit():
    """False-positive guard: a short (<PIT_STOP_MIN_S) slow section is not a pit lap."""
    eng = tm.TelemetryEngine()
    eng.update(tm.parse_packet(_packet(lap=0)), 99.0)
    t = 100.0
    for _ in range(80):
        eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=1)), t); t += 0.1
    for _ in range(10):                                   # ~1 s slow (hairpin), below threshold
        eng.update(tm.parse_packet(_packet(speed_mps=0.0, lap=1)), t); t += 0.1
    eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=2)), t)
    assert eng.snapshot()["has_reference"] is True        # brief stop still a valid lap


def t_store_removes_file_on_session_reset():
    import tempfile
    d = tempfile.mkdtemp()
    path = os.path.join(d, "telemetry.json")
    st = tm.TelemetryStore(path, units="metric", reset=True)
    st.update(tm.parse_packet(_packet(lap=0)), 99.0)
    t = 100.0
    for _ in range(100):
        st.update(tm.parse_packet(_packet(speed_mps=50.0, lap=1)), t); t += 0.1
    st.update(tm.parse_packet(_packet(speed_mps=50.0, lap=2)), t)   # lap edge -> ref saved
    assert os.path.exists(path)
    st.update(tm.parse_packet(_packet(lap=0, speed_mps=0.0)), t + 1)  # session boundary
    assert not os.path.exists(path)


def t_parse_day_ms():
    p = tm.parse_packet(_packet(day_ms=65438716))
    assert p.day_ms == 65438716


def t_fmt_clock():
    assert tm._fmt_clock(None) is None
    assert tm._fmt_clock(0) == "00:00:00"
    assert tm._fmt_clock(65438716) == "18:10:38"                  # 65438.716 s
    assert tm._fmt_clock(90061000) == "01:01:01"                  # wraps past 24 h


def t_format_snapshot_time_of_day():
    eng = tm.TelemetryEngine()
    eng.update(tm.parse_packet(_packet(day_ms=45000000, speed_mps=10.0, lap=1)), 100.0)
    out = tm.format_snapshot(eng.snapshot(), "metric", (70, 85, 95))
    assert out["time_of_day"] == "12:30:00"                       # 45000 s


def t_format_snapshot_time_of_day_none_before_packet():
    eng = tm.TelemetryEngine()
    out = tm.format_snapshot(eng.snapshot(), "metric", (70, 85, 95))
    assert out["time_of_day"] is None


def t_format_surfaces_fuel_per_lap_and_delta_dir():
    snap = {"speed_mps": 0.0, "tyre_temp": (70.0, 70.0, 70.0, 70.0),
            "tyre_temp_avg": (70.0, 70.0, 70.0, 70.0), "top_speed_mps": 0.0,
            "lap": 1, "current_lap_s": 0.0, "best_s": 90.0,
            "delta_s": 0.5, "predicted_s": 90.5, "has_reference": True,
            "time_of_day_ms": None, "delta_dir": "up",
            "fuel": {"level": 40.0, "per_lap": 2.5, "laps_remaining": 16.0,
                     "time_remaining_s": 1600.0}}
    out = tm.format_snapshot(snap, "metric", (70, 85, 95))
    assert out["fuel"]["per_lap"] == 2.5
    assert out["delta_dir"] == "up"
    imp = tm.format_snapshot(snap, "imperial", (70, 85, 95))
    assert imp["fuel"]["per_lap"] == 0.7        # 2.5 L -> 0.66 gal -> 0.7
    assert imp["delta_dir"] == "up"


def t_format_delta_dir_and_per_lap_default_none():
    """format_snapshot tolerates a snapshot with no delta_dir and null per_lap."""
    snap = {"speed_mps": 0.0, "tyre_temp": (70.0, 70.0, 70.0, 70.0),
            "tyre_temp_avg": (70.0, 70.0, 70.0, 70.0), "top_speed_mps": 0.0,
            "lap": 1, "current_lap_s": 0.0, "best_s": None,
            "delta_s": None, "predicted_s": None, "has_reference": False,
            "time_of_day_ms": None,
            "fuel": {"level": 10.0, "per_lap": None, "laps_remaining": None,
                     "time_remaining_s": None}}
    out = tm.format_snapshot(snap, "metric", (70, 85, 95))
    assert out["delta_dir"] is None
    assert out["fuel"]["per_lap"] is None


def t_store_source_roundtrip():
    store = tm.TelemetryStore()
    assert store.data()["source"] is None      # unset by default
    store.set_source("192.168.1.42")
    assert store.data()["source"] == "192.168.1.42"
    store.set_source(None)                       # clearable (e.g. relaunch)
    assert store.data()["source"] is None


def t_engine_session_distance_accumulates_incl_pit():
    eng = tm.TelemetryEngine()
    eng.update(tm.parse_packet(_packet(lap=0)), 99.0)
    # lap 1: ~10 s @ 50 m/s ≈ 500 m
    _feed_lap(eng, 100.0, 1, duration=10.0, speed=50.0)
    d1 = eng.snapshot()["session_dist_m"]
    assert 400 < d1 < 600, d1
    # lap 2 also ~500 m -> ~1000 m total
    _feed_lap(eng, 110.0, 2, duration=10.0, speed=50.0)
    d2 = eng.snapshot()["session_dist_m"]
    assert 900 < d2 < 1100, d2
    assert d2 > d1
    assert eng._lap_time_n == 2   # both laps 1+2 are clean and admitted to the average

    # lap 3: a genuine PIT lap, ~8 s driving (~400 m) then a sustained standstill
    # past PIT_STOP_MIN_S. It must be EXCLUDED from the lap-time/fuel averages
    # (acc.pit), while its driven distance is STILL banked into session_dist_m.
    t = 120.0
    for _ in range(80):                                   # ~8 s driving
        eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=3)), t); t += 0.1
    for _ in range(30):                                   # ~3 s stationary (pit box)
        eng.update(tm.parse_packet(_packet(speed_mps=0.0, lap=3)), t); t += 0.1
    eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=4)), t)   # lap edge -> finalises lap 3
    # the pit lap was excluded from the average (proves it really was classified as pit):
    assert eng._lap_time_n == 2 and eng._lap_fuel_n == 0
    d3 = eng.snapshot()["session_dist_m"]
    assert d3 > d2, d3                        # pit lap's driven distance WAS banked
    pit_dist = d3 - d2
    assert 300 < pit_dist < 500, pit_dist     # ~400 m driven before the standstill


def t_engine_session_distance_resets_on_session_boundary():
    eng = tm.TelemetryEngine()
    eng.update(tm.parse_packet(_packet(lap=0)), 99.0)
    t = _feed_lap(eng, 100.0, 1, duration=10.0, speed=50.0)   # banks lap 1 (~500 m); now on lap 2
    d_after_lap1 = eng.snapshot()["session_dist_m"]
    assert d_after_lap1 > 100
    # Drive several more packets on the now-LIVE (unfinished) lap 2 so it
    # accumulates clearly non-zero distance. An empty live lap (0 m) cannot tell a
    # real reset apart from a coincidentally-empty one, so the total has to grow
    # visibly first for the reset below to mean anything.
    for _ in range(50):                                        # ~5 s @ 50 m/s -> ~250 m live
        eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=2)), t); t += 0.1
    d_with_live = eng.snapshot()["session_dist_m"]
    assert d_with_live > d_after_lap1 + 100, d_with_live       # live lap distance is counted
    # lap counter backwards = new session -> full reset, including the live accumulator
    eng.update(tm.parse_packet(_packet(lap=0, speed_mps=0.0)), t + 1.0)
    assert eng.snapshot()["session_dist_m"] == 0.0


def t_engine_session_distance_includes_live_lap():
    eng = tm.TelemetryEngine()
    t = 100.0
    for _ in range(100):    # ~10 s @ 50 m/s in the CURRENT (unfinished) lap
        eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=1)), t); t += 0.1
    assert eng.snapshot()["session_dist_m"] > 400   # live lap counted, no edge yet


def t_format_surfaces_session_distance():
    snap = {"speed_mps": 0.0, "tyre_temp": (70, 70, 70, 70), "lap": 2,
            "current_lap_s": 0.0, "best_s": None, "delta_s": None, "predicted_s": None,
            "has_reference": False, "tyre_temp_avg": (70.0, 70.0, 70.0, 70.0),
            "top_speed_mps": 0.0, "time_of_day_ms": None, "avg_lap_s": None,
            "session_dist_m": 2500.0,
            "fuel": {"level": 10.0, "per_lap": None, "laps_remaining": None,
                     "time_remaining_s": None}}
    out = tm.format_snapshot(snap, "metric", (70, 85, 95))
    assert out["session_distance"] == 2.5 and out["units"]["distance"] == "km"
    imp = tm.format_snapshot(snap, "imperial", (70, 85, 95))
    assert abs(imp["session_distance"] - 1.6) < 0.1 and imp["units"]["distance"] == "mi"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

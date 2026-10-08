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
    struct.pack_into("<i", b, tm.OFF_CAR_ID, kw.get("car_id", 0))
    struct.pack_into("<3f", b, tm.OFF_POS, *kw.get("pos", (0.0, 0.0, 0.0)))
    struct.pack_into("<f", b, tm.OFF_RPM, kw.get("rpm", 0.0))
    b[tm.OFF_GEAR] = kw.get("gear_byte", 0)
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


def _ext_packet(**kw):
    """An extended '~' buffer (0x158 bytes): the base fields plus the extras."""
    b = bytearray(_packet(**kw)) + bytearray(0x158 - 0x128)
    struct.pack_into("<f", b, tm.OFF_STEER, kw.get("steer_rad", 0.0))
    struct.pack_into("<f", b, tm.OFF_SWAY, kw.get("sway", 0.0))
    struct.pack_into("<f", b, tm.OFF_HEAVE, kw.get("heave", 0.0))
    struct.pack_into("<f", b, tm.OFF_SURGE, kw.get("surge", 0.0))
    b[tm.OFF_THROTTLE_INPUT] = kw.get("throttle_input", 0)
    b[tm.OFF_BRAKE_INPUT] = kw.get("brake_input", 0)
    return bytes(b)


def t_parse_car_id():
    """The car id sits in the base packet too, so it needs no extended format."""
    assert tm.parse_packet(_packet(car_id=3424)).car_id == 3424


def t_parse_gear_rpm_position():
    p = tm.parse_packet(_packet(gear_byte=0x24, rpm=7350.5, pos=(1.5, -2.0, 300.25)))
    assert p.gear == 4, "gear is the low nibble of 0x90; the high nibble is ignored"
    assert abs(p.rpm - 7350.5) < 1e-3
    assert (p.pos_x, p.pos_y, p.pos_z) == (1.5, -2.0, 300.25)


def t_sanitize_keeps_last_rpm_and_position_on_nan():
    eng = tm.TelemetryEngine()
    eng.update(tm.parse_packet(_packet(rpm=5000.0, pos=(1.0, 2.0, 3.0))), 1.0)
    nan = float("nan")
    eng.update(tm.parse_packet(_packet(rpm=nan, pos=(nan, nan, nan))), 1.1)
    assert eng._last.rpm == 5000.0, "a non-finite rpm keeps the previous reading"
    assert (eng._last.pos_x, eng._last.pos_y, eng._last.pos_z) == (1.0, 2.0, 3.0)


class _Cars:
    def lookup(self, car_id):
        return None if not car_id else {"id": car_id, "maker": "M", "name": "N", "group": None}


def t_store_data_names_the_car():
    store = tm.TelemetryStore(None, cars=_Cars())
    assert store.data()["car"] is None and store.car() is None       # no packet yet
    store.update(tm.parse_packet(_packet(car_id=365)), 1.0)
    assert store.data()["car"] == {"id": 365, "maker": "M", "name": "N", "group": None}
    assert store.car()["id"] == 365


def t_store_follows_a_car_change():
    store = tm.TelemetryStore(None, cars=_Cars())
    store.update(tm.parse_packet(_packet(car_id=3424)), 1.0)
    store.update(tm.parse_packet(_packet(car_id=365, lap=0)), 2.0)
    assert store.car()["id"] == 365


def t_store_without_car_table_has_no_car():
    store = tm.TelemetryStore(None)
    store.update(tm.parse_packet(_packet(car_id=365)), 1.0)
    assert store.data()["car"] is None


def t_parse_extended_fields():
    p = tm.parse_packet(_ext_packet(steer_rad=0.75, sway=-3.5, heave=0.25, surge=-12.0,
                                    throttle_input=200, brake_input=17, throttle=90))
    assert abs(p.steer_rad - 0.75) < 1e-6
    assert abs(p.sway + 3.5) < 1e-6 and abs(p.heave - 0.25) < 1e-6 and abs(p.surge + 12.0) < 1e-6
    assert p.throttle_input == 200 and p.brake_input == 17 and p.throttle == 90


def t_parse_base_packet_leaves_extended_fields_none():
    p = tm.parse_packet(_packet(speed_mps=10.0))
    assert (p.steer_rad, p.sway, p.heave, p.surge, p.throttle_input, p.brake_input) == (None,) * 6


def t_parse_type_b_packet_has_motion_but_no_driver_input():
    """Packet 'B' (0x13C bytes) ends before the driver-input bytes at 0x13C."""
    p = tm.parse_packet(_ext_packet(steer_rad=-0.5, surge=2.0, throttle_input=99)[:0x13C])
    assert abs(p.steer_rad + 0.5) < 1e-6 and abs(p.surge - 2.0) < 1e-6
    assert p.throttle_input is None and p.brake_input is None


# ---- heartbeat: request the extended format, force one switch (#711) ----

def t_heartbeat_requests_extended_format_every_interval():
    hb = tm.HeartbeatPolicy()
    assert hb.due(0.0) == b"~"
    assert hb.due(5.0) is None
    assert hb.due(10.0) == b"~"


def t_heartbeat_extended_stream_never_pauses():
    hb = tm.HeartbeatPolicy()
    hb.due(0.0)
    for i in range(1, 250):
        hb.on_packet("~", i * 0.1)
    assert hb.due(25.0) == b"~" and hb.state == "request"


def t_heartbeat_pauses_once_to_switch_a_base_stream():
    """GT7 keeps a running stream in its format while heartbeats continue, so a base
    'A' stream only switches after it lapses (~8 s after the last heartbeat). The
    policy goes silent until the stream stops, then requests '~' at once."""
    hb = tm.HeartbeatPolicy()
    hb.due(0.0)
    t = 0.0
    while t < 18.0:                               # 'A' keeps flowing past two intervals
        t += 0.1
        hb.on_packet("A", t)
        assert hb.due(t) is None                  # silent: no heartbeat keeps it alive
    assert hb.state == "lapse"
    assert hb.due(t + 1.0) is None                # a short gap is not a lapse yet
    assert hb.due(t + tm.HEARTBEAT_LAPSE_GAP_S) == b"~"   # stream stopped: ask for '~'
    assert hb.state == "request"


def t_heartbeat_switch_is_attempted_only_once():
    hb = tm.HeartbeatPolicy()
    hb.due(0.0)
    hb.on_packet("A", 0.1)
    hb.due(0.1 + tm.HEARTBEAT_LAPSE_GAP_S)       # lapse over, '~' requested
    t = 3.0
    while t < 30.0:                               # console still answers with 'A'
        hb.on_packet("A", t)
        t += 0.1
    assert hb.state == "request"                  # no second pause: stay on 'A'
    assert hb.due(t) == b"~"                      # heartbeats keep the stream alive


def t_heartbeat_gives_up_waiting_when_the_stream_never_lapses():
    """If the base stream keeps flowing without our heartbeat (something else on this
    host keeps it alive), the pause is bounded and heartbeats resume."""
    hb = tm.HeartbeatPolicy()
    hb.due(0.0)
    t = 0.0
    while t < tm.HEARTBEAT_LAPSE_MAX_S:
        t += 0.1
        hb.on_packet("A", t)
        hb.due(t)
    assert hb.due(t + 0.1) == b"~" and hb.state == "request"


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


def t_format_snapshot_steering_degrees():
    """/telemetry/data carries the steering angle in degrees, positive = left, for
    the HUD wheel (#712)."""
    eng = tm.TelemetryEngine()
    eng.update(tm.parse_packet(_ext_packet(steer_rad=0.5236, lap=1)), 100.0)
    assert tm.format_snapshot(eng.snapshot(), "metric", (70, 85, 95))["steer_deg"] == 30.0
    eng.update(tm.parse_packet(_ext_packet(steer_rad=-1.5708, lap=1)), 100.1)
    assert tm.format_snapshot(eng.snapshot(), "metric", (70, 85, 95))["steer_deg"] == -90.0


def t_format_snapshot_steering_none_on_base_packets():
    """A base 'A' stream has no steering: the HUD hides the wheel."""
    eng = tm.TelemetryEngine()
    assert tm.format_snapshot(eng.snapshot(), "metric", (70, 85, 95))["steer_deg"] is None
    eng.update(tm.parse_packet(_packet(lap=1)), 100.0)
    assert tm.format_snapshot(eng.snapshot(), "metric", (70, 85, 95))["steer_deg"] is None


def t_format_snapshot_steering_non_finite_is_none():
    """A NaN/inf steering float must not reach the JSON: json.dumps would emit a bare
    NaN that the HUD's JSON.parse rejects, freezing the whole block. (#716 review)"""
    eng = tm.TelemetryEngine()
    for bad in (float("nan"), float("inf"), float("-inf")):
        eng.update(tm.parse_packet(_ext_packet(steer_rad=bad, lap=1)), 100.0)
        assert tm.format_snapshot(eng.snapshot(), "metric", (70, 85, 95))["steer_deg"] is None


_BAD = (float("nan"), float("inf"), float("-inf"))


def t_engine_non_finite_floats_keep_the_last_good_value():
    """A NaN/inf float in any packet field is replaced by the last good reading, so
    the accumulated distance, fuel and tyre averages stay finite. (#717)"""
    for bad in _BAD:
        eng = tm.TelemetryEngine()
        eng.update(tm.parse_packet(_ext_packet(speed_mps=50.0, fuel_level=40.0, lap=1,
                                               tyre_temp=(80.0, 81.0, 82.0, 83.0),
                                               steer_rad=0.5, sway=1.0)), 100.0)
        eng.update(tm.parse_packet(_ext_packet(speed_mps=bad, fuel_level=bad, lap=1,
                                               fuel_capacity=bad,
                                               tyre_temp=(bad, bad, bad, bad),
                                               steer_rad=bad, sway=bad, heave=bad,
                                               surge=bad)), 101.0)
        last = eng._last
        assert (last.speed_mps, last.fuel_level, last.fuel_capacity) == (50.0, 40.0, 60.0), last
        assert last.tyre_temp == (80.0, 81.0, 82.0, 83.0), last.tyre_temp
        assert (last.steer_rad, last.sway, last.heave, last.surge) == (
            _f32(0.5), 1.0, 0.0, 0.0), last
        snap = eng.snapshot()
        assert snap["session_dist_m"] == 50.0, snap["session_dist_m"]
        assert snap["top_speed_mps"] == 50.0, snap["top_speed_mps"]


def t_engine_non_finite_first_packet_falls_back_to_defaults():
    """Without an earlier reading a base float becomes 0.0 and an extended one None."""
    eng = tm.TelemetryEngine()
    nan = float("nan")
    eng.update(tm.parse_packet(_ext_packet(speed_mps=nan, fuel_level=nan, lap=1,
                                           tyre_temp=(nan, 70.0, nan, 70.0),
                                           steer_rad=nan)), 100.0)
    last = eng._last
    assert (last.speed_mps, last.fuel_level) == (0.0, 0.0), last
    assert last.tyre_temp == (0.0, 70.0, 0.0, 70.0), last.tyre_temp
    assert last.steer_rad is None, last.steer_rad


def t_store_data_is_strict_json_after_non_finite_packets():
    """/telemetry/data must neither raise (round(nan)) nor emit NaN/Infinity, or the
    HUD's r.json() fails and the whole telemetry block freezes. (#717)"""
    import json
    store = tm.TelemetryStore()
    t = 100.0
    store.update(tm.parse_packet(_ext_packet(speed_mps=50.0, lap=1)), t)
    for bad in _BAD:
        t += 0.1
        store.update(tm.parse_packet(_ext_packet(
            speed_mps=bad, fuel_level=bad, fuel_capacity=bad, lap=1,
            tyre_temp=(bad, bad, bad, bad), steer_rad=bad)), t)
        json.dumps(store.data(), allow_nan=False)


def _f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]

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


class _LapLog:
    """Capture the engine's per-lap verdict lines on racecast.relay.telemetry."""
    def __enter__(self):
        import logging
        self.lines = []
        cap = self
        class H(logging.Handler):
            def emit(self, rec): cap.lines.append(rec.getMessage())
        self.h = H(level=logging.INFO)
        self.log = logging.getLogger("racecast.relay.telemetry")
        self.prev = self.log.level
        self.log.setLevel(logging.INFO); self.log.addHandler(self.h)
        return self

    def __exit__(self, *exc):
        self.log.removeHandler(self.h); self.log.setLevel(self.prev)


def _verdict(lines, lap):
    """The one verdict line logged for `lap` ("GT7 lap N <time>: ..."), as the text
    after the colon. Fails unless exactly one such line exists."""
    hits = [s.split(": ", 1)[1] for s in lines if s.startswith(f"GT7 lap {lap} ")]
    assert len(hits) == 1, (lap, lines)
    return hits[0]


def _drive(eng, t, lap, secs, speed=50.0, **kw):
    for _ in range(int(secs / 0.1)):
        eng.update(tm.parse_packet(_packet(speed_mps=speed, lap=lap, **kw)), t)
        t += 0.1
    return t


def t_laplog_reference_then_counted():
    with _LapLog() as cap:
        eng = tm.TelemetryEngine()
        eng.update(tm.parse_packet(_packet(lap=0)), 99.0)
        t = _feed_lap(eng, 100.0, 1, duration=10.0, speed=50.0)
        _feed_lap(eng, t, 2, duration=11.0, speed=50.0)
    assert _verdict(cap.lines, 0) == "not counted (partial lap (relay connected mid-lap))"
    assert _verdict(cap.lines, 1) == "new reference, delta vs this lap from now on"
    assert _verdict(cap.lines, 2) == "counted"


def t_laplog_names_why_a_lap_is_rejected():
    with _LapLog() as cap:                                 # standstill pit lap
        eng = tm.TelemetryEngine()
        eng.update(tm.parse_packet(_packet(lap=0)), 99.0)
        t = _drive(eng, 100.0, 1, 8.0)
        t = _drive(eng, t, 1, 3.0, speed=0.0)
        eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=2)), t)
    assert _verdict(cap.lines, 1) == "not counted (pit lap: standstill)"

    with _LapLog() as cap:                                 # refuel pit lap
        eng = tm.TelemetryEngine()
        eng.update(tm.parse_packet(_packet(lap=0, fuel_level=20.0)), 99.0)
        t = _drive(eng, 100.0, 1, 5.0, fuel_level=20.0)
        t = _drive(eng, t, 1, 5.0, fuel_level=30.0)
        eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=2, fuel_level=30.0)), t)
    assert _verdict(cap.lines, 1) == "not counted (pit lap: refuel)"

    with _LapLog() as cap:                                 # pause mid-lap
        eng = tm.TelemetryEngine()
        eng.update(tm.parse_packet(_packet(lap=0)), 99.0)
        t = _drive(eng, 100.0, 1, 5.0)
        eng.update(tm.parse_packet(_packet(speed_mps=0.0, lap=1, flags=tm.FLAG_PAUSED)), t)
        t = _drive(eng, t + 0.1, 1, 5.0)
        eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=2)), t)
    assert _verdict(cap.lines, 1) == "not counted (paused, loading or off track)"

    with _LapLog() as cap:                                 # >2 s data gap
        eng = tm.TelemetryEngine()
        eng.update(tm.parse_packet(_packet(lap=0)), 99.0)
        t = _drive(eng, 100.0, 1, 5.0)
        t = _drive(eng, t + 5.0, 1, 5.0)
        eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=2)), t)
    assert _verdict(cap.lines, 1) == "not counted (data gap over 2 s)"

    with _LapLog() as cap:                                 # boundary lap under MIN_LAP_S
        eng = tm.TelemetryEngine()
        eng.update(tm.parse_packet(_packet(lap=0)), 99.0)
        t = _drive(eng, 100.0, 1, tm.MIN_LAP_S - 2.0)
        eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=2)), t)
    assert _verdict(cap.lines, 1) == "not counted (too short)"


def t_laplog_reports_the_first_reason():
    # A lap that stood in the pit box and was then paused names the standstill,
    # which happened first, not the pause.
    with _LapLog() as cap:
        eng = tm.TelemetryEngine()
        eng.update(tm.parse_packet(_packet(lap=0)), 99.0)
        t = _drive(eng, 100.0, 1, 3.0)
        t = _drive(eng, t, 1, tm.PIT_STOP_MIN_S + 1.0, speed=0.0)
        eng.update(tm.parse_packet(_packet(speed_mps=0.0, lap=1, flags=tm.FLAG_PAUSED)), t)
        t = _drive(eng, t + 0.1, 1, 5.0)
        eng.update(tm.parse_packet(_packet(speed_mps=50.0, lap=2)), t)
    assert _verdict(cap.lines, 1) == "not counted (pit lap: standstill)"


def t_laplog_session_change_clears_reference():
    with _LapLog() as cap:
        eng = tm.TelemetryEngine()
        eng.update(tm.parse_packet(_packet(lap=0)), 99.0)
        t = _feed_lap(eng, 100.0, 1, duration=10.0, speed=50.0)   # lap 1 -> reference
        t = _drive(eng, t, 2, 3.0)                                # lap 2 in progress
        eng.update(tm.parse_packet(_packet(lap=0, speed_mps=0.0)), t)
    # the unfinished lap gets its own verdict, so no lap is missing from the log
    assert _verdict(cap.lines, 2) == "not counted (session change)"
    assert "GT7 session change (new lap counter 0): reference cleared" in cap.lines, cap.lines


def t_laplog_session_change_without_reference():
    with _LapLog() as cap:
        eng = tm.TelemetryEngine()
        eng.update(tm.parse_packet(_packet(lap=3)), 99.0)         # mid-lap connect
        eng.update(tm.parse_packet(_packet(lap=1, speed_mps=0.0)), 100.0)
    assert "GT7 session change (new lap counter 1): no reference yet" in cap.lines, cap.lines


def t_engine_emits_lap_records():
    eng = tm.TelemetryEngine()
    laps = []
    eng.on_lap = laps.append
    eng.update(tm.parse_packet(_packet(lap=0)), 99.0)
    t = _feed_lap(eng, 100.0, 1, duration=10.0, speed=50.0)
    _feed_lap(eng, t, 2, duration=11.0, speed=60.0)   # its last packet opens lap 3
    assert [r["lap"] for r in laps] == [0, 1, 2], laps
    assert laps[0]["status"] == "not counted" and "partial" in laps[0]["reason"]
    assert laps[1]["status"] == "reference" and laps[1]["reason"] == ""
    assert laps[2]["status"] == "counted"
    assert abs(laps[2]["top_speed_mps"] - 60.0) < 1e-6
    assert laps[1]["start"] == 100.0 and laps[1]["end"] < laps[2]["start"]
    assert all(r["session"] == 1 for r in laps)


def t_engine_session_change_emits_abandoned_lap_and_counts_session():
    eng = tm.TelemetryEngine()
    laps = []
    eng.on_lap = laps.append
    eng.update(tm.parse_packet(_packet(lap=1)), 99.0)
    t = _feed_lap(eng, 100.0, 2, duration=5.0)   # ends with lap 3's first packet at t
    assert eng.session == 1
    eng.update(tm.parse_packet(_packet(lap=0, speed_mps=0.0)), t)
    assert eng.session == 2
    last = laps[-1]
    assert (last["lap"], last["status"], last["reason"], last["session"]) == \
        (3, "not counted", "session change", 1), last
    assert eng.lap_started_at() == t


def t_engine_without_on_lap_is_unchanged():
    eng = tm.TelemetryEngine()
    assert eng.on_lap is None and eng.lap_started_at() is None and eng.lap_distance() is None
    eng.update(tm.parse_packet(_packet(lap=1)), 1.0)
    assert eng.lap_started_at() == 1.0


def t_engine_lap_distance_and_car_in_record():
    eng = tm.TelemetryEngine()
    laps = []
    eng.on_lap = laps.append
    eng.update(tm.parse_packet(_packet(lap=1, car_id=3424)), 1.0)
    for i in range(1, 11):                                    # 1 s at 50 m/s
        eng.update(tm.parse_packet(_packet(lap=1, speed_mps=50.0, car_id=3424)), 1.0 + i / 10)
    assert abs(eng.lap_distance() - 50.0) < 1e-6, eng.lap_distance()
    eng.update(tm.parse_packet(_packet(lap=2, car_id=3424)), 2.1)
    assert laps[-1]["car_id"] == 3424 and eng.lap_distance() == 0.0


def t_engine_on_lap_failure_does_not_raise():
    eng = tm.TelemetryEngine()

    def boom(_record):
        raise RuntimeError("consumer exploded")

    eng.on_lap = boom
    eng.update(tm.parse_packet(_packet(lap=0)), 99.0)
    t = _feed_lap(eng, 100.0, 1, duration=10.0, speed=50.0)
    assert eng.lap_started_at() == t
    assert eng.session == 1


class _FakeTracks:
    def __init__(self, result):
        self.result, self.calls = result, []

    def match(self, points, length_m):
        self.calls.append((len(points), round(length_m)))
        return self.result


def _drive_xy(eng, t, lap, secs, speed=50.0):
    """Drive `secs` along +x at `speed`, positions following the distance."""
    x = 0.0
    for _ in range(int(secs / 0.1)):
        eng.update(tm.parse_packet(_packet(speed_mps=speed, lap=lap, pos=(x, 0.0, 0.0))), t)
        t += 0.1
        x += speed * 0.1
    return t


def t_lap_record_carries_points_every_20m():
    eng = tm.TelemetryEngine()
    laps = []
    eng.on_lap = laps.append
    eng.update(tm.parse_packet(_packet(lap=1)), 0.0)
    t = _drive_xy(eng, 0.1, 2, 10.0)                       # 500 m
    eng.update(tm.parse_packet(_packet(lap=3)), t)
    pts = laps[-1]["points"]
    assert 24 <= len(pts) <= 26, len(pts)
    assert all(15.0 <= b[0] - a[0] <= 25.0 for a, b in zip(pts, pts[1:], strict=False)), pts[:4]
    assert abs(laps[-1]["distance_m"] - 495.0) < 1e-6


def t_engine_sets_track_after_a_matching_lap_and_resets_on_session_change():
    eng = tm.TelemetryEngine()
    found = {"id": "2066d9", "track": "Nürburgring", "layout": "Grand Prix",
             "reverse": False, "score_m": 4.2}
    eng.track_db = _FakeTracks(found)
    eng.update(tm.parse_packet(_packet(lap=1)), 0.0)
    t = _drive_xy(eng, 0.1, 2, 10.0)
    eng.update(tm.parse_packet(_packet(lap=3)), t)
    assert eng.track == found
    n = len(eng.track_db.calls)
    t = _drive_xy(eng, t + 0.1, 3, 10.0)
    eng.update(tm.parse_packet(_packet(lap=4)), t)
    assert len(eng.track_db.calls) == n, "a recognised track is not matched again"
    eng.update(tm.parse_packet(_packet(lap=0)), t + 0.1)
    assert eng.track is None


def t_engine_keeps_trying_while_ambiguous_and_survives_matcher_errors():
    eng = tm.TelemetryEngine()
    fake = _FakeTracks({"candidates": ["a", "b"]})
    eng.track_db = fake
    eng.update(tm.parse_packet(_packet(lap=1)), 0.0)
    t = _drive_xy(eng, 0.1, 2, 10.0)
    eng.update(tm.parse_packet(_packet(lap=3)), t)
    assert eng.track == {"candidates": ["a", "b"]}
    n = len(fake.calls)

    t = _drive_xy(eng, t + 0.1, 3, 10.0)
    eng.update(tm.parse_packet(_packet(lap=4)), t)
    assert len(fake.calls) == n + 1, "an ambiguous result keeps trying on the next lap"
    assert eng.track == {"candidates": ["a", "b"]}

    class Boom:
        def match(self, *a):
            raise RuntimeError("bad data")
    eng.track_db = Boom()
    t = _drive_xy(eng, t + 0.1, 4, 10.0)
    eng.update(tm.parse_packet(_packet(lap=5)), t)        # must not raise
    assert eng.track == {"candidates": ["a", "b"]}


def t_engine_survives_an_incomplete_or_non_dict_match_result():
    """A match result with an 'id' but missing keys, or a non-dict result, must
    never raise into update(): the lap counter has to keep advancing (#787 review)."""
    class _Incomplete:
        def match(self, *a):
            return {"id": "x"}        # missing track/layout/reverse

    eng = tm.TelemetryEngine()
    eng.track_db = _Incomplete()
    eng.update(tm.parse_packet(_packet(lap=1)), 0.0)
    t = _drive_xy(eng, 0.1, 2, 10.0)
    eng.update(tm.parse_packet(_packet(lap=3)), t)        # must not raise
    assert eng.track == {"id": "x"}
    assert eng._lap_num == 3

    class _NonDict:
        def match(self, *a):
            return ["not", "a", "dict"]

    eng2 = tm.TelemetryEngine()
    eng2.track_db = _NonDict()
    eng2.update(tm.parse_packet(_packet(lap=1)), 0.0)
    t2 = _drive_xy(eng2, 0.1, 2, 10.0)
    eng2.update(tm.parse_packet(_packet(lap=3)), t2)      # must not raise
    assert eng2.track is None
    assert eng2._lap_num == 3

    # the lap counter must keep advancing normally on the following lap too
    t3 = _drive_xy(eng2, t2 + 0.1, 3, 10.0)
    eng2.update(tm.parse_packet(_packet(lap=4)), t3)
    assert eng2._lap_num == 4


def t_lap_points_capped_under_flood():
    """A same-lap packet flood must not grow _LapAccumulator.points without bound,
    mirroring the existing samples cap (#787 review)."""
    eng = tm.TelemetryEngine()
    t = 100.0
    eng.update(tm.parse_packet(_packet(speed_mps=90.0, lap=1)), t); t += 0.1
    for _ in range(tm.MAX_SAMPLES + 500):     # flood, lap never changes
        eng.update(tm.parse_packet(_packet(speed_mps=90.0, lap=1)), t); t += 0.1
    assert len(eng._acc.points) == tm.MAX_POINTS     # capped, not just bounded


def t_short_lap_never_reaches_match():
    """A lap under MIN_TRACK_POINTS points must never call TrackDB.match (#787 review)."""
    eng = tm.TelemetryEngine()
    fake = _FakeTracks({"id": "x", "track": "T", "layout": "L",
                         "reverse": False, "score_m": 1.0})
    eng.track_db = fake
    eng.update(tm.parse_packet(_packet(lap=1)), 0.0)
    t = _drive_xy(eng, 0.1, 2, 1.0)            # a few metres: well under 10 points
    eng.update(tm.parse_packet(_packet(lap=3)), t)
    assert fake.calls == []
    assert eng.track is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

#!/usr/bin/env python3
"""Telemetry block of the post-event report: figures, groups, mini-sector map, SVG.
Run: python3 tests/test_report_telemetry.py"""
import math
import os
import statistics
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))

import gt7_laps
import report_telemetry as rtel

LENGTH = 2000.0
RADIUS = LENGTH / (2 * math.pi)
REC = "20261007-200000"
WINDOW = (1000.0, 2000.0)


def _trace(first_mps, second_mps):
    """A lap on a 2000 m circle, one speed per half, a point every 5 m."""
    pts, t = [], 0.0
    for i in range(int(LENGTH // 5) + 1):
        d = i * 5.0
        a = d / RADIUS
        v = first_mps if d < LENGTH / 2 else second_mps
        pts.append({"d": d, "t": t, "speed_kmh": v * 3.6, "throttle": 100, "brake": 0,
                    "steer_deg": 0.0, "gear": 5, "x": RADIUS * math.cos(a),
                    "z": RADIUS * math.sin(a)})
        t += 5.0 / v
    return pts


def _straight_trace(length_m, speed_mps, step=5.0):
    """A straight-line trace of length_m at a constant speed, ending exactly at
    length_m like a counted lap's trace (not necessarily on the step grid)."""
    pts, d = [], 0.0
    while d < length_m:
        pts.append({"d": d, "t": d / speed_mps, "speed_kmh": speed_mps * 3.6,
                    "throttle": 100, "brake": 0, "steer_deg": 0.0, "gear": 5,
                    "x": d, "z": 0.0})
        d += step
    pts.append({"d": length_m, "t": length_m / speed_mps, "speed_kmh": speed_mps * 3.6,
                "throttle": 100, "brake": 0, "steer_deg": 0.0, "gear": 5,
                "x": length_m, "z": 0.0})
    return pts


def _lap(n, start, trace=None, status="counted", reason="", fuel=2.0,
         tyres=(80.0, 81.0, 70.0, 71.0), track_id=101, car_id=3424,
         car="Porsche 911 RSR", relay=None, rec=REC, session=1):
    """One lap in the shape gt7_laps.index writes."""
    trace = trace or []
    gt7 = round(trace[-1]["t"], 3) if trace else None
    relay = relay if relay is not None else (gt7 or 0.0) + 0.02
    time_s = gt7 if gt7 is not None else relay
    known = track_id is not None
    return {"rec": rec, "session": session, "lap": n, "start_t_s": start,
            "end_t_s": start + time_s, "gt7_time_s": gt7, "relay_time_s": relay,
            "time_s": time_s, "status": status, "reason": reason, "fuel_used_l": fuel,
            "top_speed_kmh": 180.0, "car_id": car_id, "car": car, "track_id": track_id,
            "track": "Suzuka Circuit" if known else "",
            "layout": "Full Course" if known else "",
            "distance_m": trace[-1]["d"] if trace else 0.0, "tyre_avg_c": list(tyres),
            "points": [[p["x"], p["z"]] for p in trace],
            "sectors": gt7_laps.sectors(trace, gt7_laps.lap_length_m({"trace": trace})),
            "trace": trace}


def session_index():
    """Three counted laps and a pit lap. Lap 3 is the best (43.810 s); laps 1 and 2
    hold the fast halves, so the theoretical best is 40.000 s."""
    laps = [_lap(1, 10.0, _trace(50.0, 40.0), status="reference", fuel=2.0,
                 tyres=(80.0, 81.0, 70.0, 71.0)),
            _lap(2, 55.0, _trace(40.0, 50.0), fuel=2.2, tyres=(82.0, 83.0, 72.0, 73.0)),
            _lap(3, 100.0, _trace(50.0, 42.0), status="reference", fuel=2.1,
                 tyres=(84.0, 85.0, 74.0, 75.0)),
            _lap(4, 143.81, status="not counted", reason="pit", fuel=0.5,
                 tyres=(60.0, 60.0, 60.0, 60.0), relay=95.0)]
    return {"rec": REC, "started": "2026-10-07T20:00:00+02:00",
            "start_ts": 1000.0, "end_ts": 1240.0, "track": None, "laps": laps}


def block():
    return rtel.telemetry_block([session_index()], WINDOW)


def t_fmt_lap():
    assert rtel.fmt_lap(118.432) == "1:58.432"
    assert rtel.fmt_lap(43.81) == "0:43.810"
    assert rtel.fmt_lap(59.9996) == "1:00.000", "rounding to the millisecond carries into the minute"
    assert rtel.fmt_lap(None) == "—"


def t_track_label_as_in_the_control_center():
    assert rtel.track_label({"track": "Suzuka Circuit", "layout": "Full Course"}) == \
        "Suzuka Circuit - Full Course"
    assert rtel.track_label({"track": "Nürburgring", "layout": ""}) == "Nürburgring"
    assert rtel.track_label({"track": "", "layout": ""}) == "Unknown track"


def t_block_figures():
    b = block()
    assert b["laps_total"] == 4 and b["laps_counted"] == 3, b
    assert len(b["groups"]) == 1
    g = b["groups"][0]
    assert abs(g["best_s"] - 43.81) < 1e-9, g["best_s"]
    assert g["best_lap"]["lap"] == 3 and g["best_lap"]["n"] == 3, g["best_lap"]
    assert abs(g["theoretical_s"] - 40.0) < 1e-6, \
        "the theoretical best joins lap 1's first half and lap 2's second half"
    assert abs(g["consistency_s"] - statistics.pstdev([45.0, 45.0, 43.81])) < 1e-9, \
        g["consistency_s"]
    assert abs(g["fuel_per_lap_l"] - 2.1) < 1e-9, "the pit lap's fuel stays out"
    assert g["tyre_avg_c"] == [82.0, 83.0, 72.0, 73.0], g["tyre_avg_c"]
    assert g["track"] == "Suzuka Circuit - Full Course" and g["track_name"] == "Suzuka Circuit"
    assert g["car"] == "Porsche 911 RSR"
    assert [p["best"] for p in g["trend"]] == [False, False, True, False]


def t_tyre_average_skips_laps_without_tyre_data():
    idx = session_index()
    idx["laps"][1]["tyre_avg_c"] = [0.0, 0.0, 0.0, 0.0]
    g = rtel.telemetry_block([idx], WINDOW)["groups"][0]
    assert g["tyre_avg_c"] == [82.0, 83.0, 72.0, 73.0], \
        f"a lap without a tyre sample must not count as 0 °C: {g['tyre_avg_c']}"


def t_lap_rows_in_driving_order():
    rows = block()["laps"]
    assert [(r["n"], r["lap"]) for r in rows] == [(1, 1), (2, 2), (3, 3), (4, 4)]
    assert rows[0]["ts"] == 1010.0 and rows[0]["counted"], rows[0]
    pit = rows[3]
    assert (pit["status"], pit["reason"], pit["time_s"], pit["counted"]) == \
        ("not counted", "pit", 95.0, False), pit
    assert rows[0]["track"] == "Suzuka Circuit - Full Course"


def t_consistency_needs_two_counted_laps():
    idx = session_index()
    idx["laps"] = idx["laps"][2:]
    g = rtel.telemetry_block([idx], WINDOW)["groups"][0]
    assert g["laps_counted"] == 1 and g["consistency_s"] is None, "one lap has no spread"


def t_laps_outside_the_window_are_ignored():
    assert rtel.telemetry_block([session_index()], (5000.0, 6000.0)) is None
    assert rtel.telemetry_block([], WINDOW) is None
    b = rtel.telemetry_block([session_index()], (1050.0, 2000.0))
    assert [r["lap"] for r in b["laps"]] == [2, 3, 4], \
        "a lap is in the report when it starts inside the window"


def t_groups_by_track_and_car_most_counted_first():
    other = {"rec": "20261007-210000", "started": "2026-10-07T20:08:20+02:00",
             "start_ts": 1500.0, "end_ts": 1600.0, "track": None,
             "laps": [_lap(1, 5.0, _trace(45.0, 45.0), track_id=None, car_id=1234,
                           car="Mazda Roadster", rec="20261007-210000")]}
    b = rtel.telemetry_block([other, session_index()], WINDOW)
    assert [g["car"] for g in b["groups"]] == ["Porsche 911 RSR", "Mazda Roadster"]
    unknown = b["groups"][1]
    assert (unknown["track"], unknown["track_name"]) == ("Unknown track", "")
    assert (unknown["rec"], unknown["session"]) == ("20261007-210000", 1)
    assert (b["groups"][0]["rec"], b["groups"][0]["session"]) == (None, None), \
        "a known track groups across recordings"
    assert b["laps"][-1]["car"] == "Mazda Roadster", "rows stay chronological across recordings"


def t_unknown_track_groups_per_session():
    idx = session_index()
    idx["laps"] = [_lap(1, 10.0, _trace(50.0, 40.0), track_id=None, session=1),
                   _lap(1, 100.0, _trace(40.0, 50.0), track_id=None, session=2)]
    groups = rtel.telemetry_block([idx], WINDOW)["groups"]
    assert [g["session"] for g in groups] == [1, 2], groups
    assert all(abs(g["theoretical_s"] - 45.0) < 1e-6 for g in groups), \
        "two GT7 sessions on an unknown track may be two circuits and share no theoretical best"


def t_summary_line():
    assert rtel.summary_line(block()) == \
        "Best lap 0:43.810 (theoretical 0:40.000), 3 laps, Suzuka Circuit"
    idx = session_index()
    idx["laps"] = idx["laps"][3:]
    assert rtel.summary_line(rtel.telemetry_block([idx], WINDOW)) == \
        "1 lap, none counted, Suzuka Circuit"


def t_partial_flag_from_an_open_recording():
    assert rtel.telemetry_block([dict(session_index(), partial=True)], WINDOW)["partial"] is True
    assert block()["partial"] is False


def t_theoretical_best_none_when_sector_counts_differ():
    """An unknown track closes each counted lap at its own distance; lengths straddling
    a 200 m boundary give the laps a different sector count, so no theoretical best."""
    idx = {"rec": REC, "started": "2026-10-07T20:00:00+02:00",
           "start_ts": 1000.0, "end_ts": 1100.0, "track": None,
           "laps": [_lap(1, 10.0, _straight_trace(1998.0, 50.0), track_id=None),
                    _lap(2, 60.0, _straight_trace(2000.2, 49.0), track_id=None)]}
    g = rtel.telemetry_block([idx], WINDOW)["groups"][0]
    assert g["theoretical_s"] is None, \
        "differing sector counts must not produce a theoretical best below the best lap"


def t_fuel_per_lap_none_when_consumption_is_off():
    idx = session_index()
    for lap in idx["laps"][:3]:
        lap["fuel_used_l"] = 0.0
    g = rtel.telemetry_block([idx], WINDOW)["groups"][0]
    assert g["fuel_per_lap_l"] is None, "all-zero fuel means consumption is off, not 0.00 L/lap"


def t_fuel_per_lap_none_without_any_fuel_samples():
    idx = session_index()
    for lap in idx["laps"]:
        lap["fuel_used_l"] = None
    g = rtel.telemetry_block([idx], WINDOW)["groups"][0]
    assert g["fuel_per_lap_l"] is None, "no fuel samples at all means no fuel figure either"


def t_best_lap_tiebreak_keeps_the_earliest():
    idx = {"rec": REC, "started": "2026-10-07T20:00:00+02:00",
           "start_ts": 1000.0, "end_ts": 1200.0, "track": None,
           "laps": [_lap(1, 10.0, _trace(50.0, 45.0)), _lap(2, 70.0, _trace(50.0, 45.0))]}
    g = rtel.telemetry_block([idx], WINDOW)["groups"][0]
    assert g["best_lap"]["n"] == 1, "a tie on best time keeps the earliest lap in driving order"


def t_theoretical_best_skips_a_counted_lap_without_sectors():
    idx = {"rec": REC, "started": "2026-10-07T20:00:00+02:00",
           "start_ts": 1000.0, "end_ts": 1200.0, "track": None,
           "laps": [_lap(1, 10.0, _trace(50.0, 50.0)), _lap(2, 70.0, _trace(45.0, 45.0))]}
    idx["laps"][1]["sectors"] = []
    g = rtel.telemetry_block([idx], WINDOW)["groups"][0]
    assert abs(g["theoretical_s"] - 40.0) < 1e-6, \
        "a counted lap without sectors drops out, the theoretical best still comes from the rest"


def t_gap_color_scale():
    assert rtel.gap_color(0.0, 0.5) == "#2e7d32"
    assert rtel.gap_color(0.5, 0.5) == "#c62828"
    assert rtel.gap_color(0.25, 0.5) == "#7a532d", "halfway is the RGB midpoint, rounded half up"
    assert rtel.gap_color(0.3, 0.0) == "#2e7d32", "a lap without any loss is all green"


def t_map_colours_sectors_by_gap_to_best():
    m = block()["groups"][0]["map"]
    assert len(m["sectors"]) == 10, len(m["sectors"])
    assert abs(m["worst_gap_s"] - (round(200 / 42, 3) - 4.0)) < 1e-9, m["worst_gap_s"]
    assert [s["color"] for s in m["sectors"][:5]] == ["#2e7d32"] * 5, "no loss is green"
    assert [s["color"] for s in m["sectors"][5:]] == ["#c62828"] * 5, "the largest loss is red"
    assert all(len(s["points"]) >= 2 for s in m["sectors"])


def t_map_greys_a_sector_without_time():
    idx = session_index()
    idx["laps"][2]["sectors"][0] = None
    m = rtel.telemetry_block([idx], WINDOW)["groups"][0]["map"]
    assert (m["sectors"][0]["gap_s"], m["sectors"][0]["color"]) == (None, "#bdbdbd"), m["sectors"][0]
    assert m["sectors"][9]["color"] == "#c62828", "the other sectors keep their colours"


def t_map_absent_when_the_best_lap_has_no_trace():
    idx = session_index()
    idx["laps"][2]["trace"] = []
    g = rtel.telemetry_block([idx], WINDOW)["groups"][0]
    assert g["map"] is None, "the map needs the best lap's x/z"
    assert abs(g["theoretical_s"] - 40.0) < 1e-6, "the sector times still give the theoretical best"


def t_svgs_are_well_formed_xml():
    g = block()["groups"][0]
    trend = ET.fromstring(rtel.svg_lap_trend(g["trend"]))
    tmap = ET.fromstring(rtel.svg_track_map(g["map"]))
    assert trend.tag == "svg" and tmap.tag == "svg"
    fills = [c.get("fill") for c in trend.iter("circle")]
    assert fills.count("#bdbdbd") == 1, "the pit lap is greyed"
    assert fills.count("#2e7d32") == 1, "the best lap is green"
    assert "#3 (lap 3): 0:43.810" in [t.text for t in trend.iter("title")], \
        "the tooltip names the driving-order number next to GT7's lap number"
    assert len(list(tmap.iter("polyline"))) == 10


def t_trend_pins_a_slow_lap_to_the_top_edge():
    trend = ET.fromstring(rtel.svg_lap_trend(block()["groups"][0]["trend"]))
    circles = list(trend.iter("circle"))
    pit = [c for c in circles if c.get("fill") == "#bdbdbd"][0]
    assert float(pit.get("cy")) == min(float(c.get("cy")) for c in circles), \
        "the 95 s pit lap sits on the top edge instead of stretching the scale"
    assert all(12.0 <= float(c.get("cy")) <= 138.0 for c in circles)


def t_svg_empty_inputs():
    assert rtel.svg_lap_trend([]) == ""
    assert rtel.svg_track_map(None) == ""


def run():
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")


if __name__ == "__main__":
    run()

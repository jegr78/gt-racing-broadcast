#!/usr/bin/env python3
"""Write a synthetic GT7 telemetry recording that follows a real racing line.

Maintainer tool for the Control Center Telemetry view (screenshots, visual checks): an
out-lap and N timed laps on one layout from the downloaded signatures.json (run
`racecast gt7-data update` once). Every lap has its own pace pattern, so two laps trade
mini-sectors.

    python3 tools/make-demo-recording.py --out runtime/solo-pov/telemetry-recordings
"""
import argparse
import bisect
import json
import math
import os
import struct
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import gt7_data
import gt7_recording
import gt7_telemetry as tm
import logsetup

A_LAT = 14.0          # m/s^2 cornering grip
A_BRAKE = 11.0
A_ACCEL = 5.5
V_MAX = 80.0
V_MIN = 12.0
CAR_ID = 2158         # 458 Italia GT3 '13 in the bundled car table
PACE_AMP = 0.035
PACE_WAVES = 3
LAP_BASE = (1.0, 0.992, 1.006, 0.997, 1.003, 0.994, 1.001, 0.996)
GEARS = (16.0, 24.0, 32.0, 41.0, 51.0, 62.0)   # upshift speeds, m/s


def load_rows(path):
    with open(path, encoding="utf-8") as fh:
        return sorted(json.load(fh)["signatures"], key=lambda r: r["official_name"])


def pick_row(rows, query):
    q = (query or "").lower()
    for r in rows:
        if not q or r["official_id"] == query or q in r["official_name"].lower():
            return r
    raise SystemExit(f"no layout in signatures.json matches {query!r}")


def _line(path):
    """Segment lengths, cumulative distance and signed curvature of the closed line."""
    n = len(path)
    seg = [math.dist(path[i], path[(i + 1) % n]) for i in range(n)]
    cum = [0.0]
    for s in seg:
        cum.append(cum[-1] + s)
    curv = []
    for i in range(n):
        a, b, c = path[i - 2], path[i], path[(i + 2) % n]
        turn = math.atan2(c[1] - b[1], c[0] - b[0]) - math.atan2(b[1] - a[1], b[0] - a[0])
        turn = (turn + math.pi) % (2 * math.pi) - math.pi
        span = math.dist(a, b) + math.dist(b, c)
        curv.append(turn / span if span else 0.0)
    return seg, cum, curv


def _speeds(seg, curv, lap):
    """Target speed per line point for one lap: grip in corners, braking and traction."""
    n, total = len(seg), sum(seg)
    base, phase = LAP_BASE[lap % len(LAP_BASE)], 1.9 * lap
    v, pos = [], 0.0
    for i in range(n):
        pace = base * (1 + PACE_AMP * math.sin(2 * math.pi * PACE_WAVES * pos / total + phase))
        grip = math.sqrt(A_LAT * pace / abs(curv[i])) if abs(curv[i]) > 1e-6 else V_MAX
        v.append(max(V_MIN, min(V_MAX * pace, grip)))
        pos += seg[i]
    for _ in range(2):            # twice, so the line's start sees the end's limits
        for i in range(n):
            v[i] = min(v[i], math.sqrt(v[i - 1] ** 2 + 2 * A_ACCEL * seg[i - 1]))
        for i in range(n - 1, -1, -1):
            v[i] = min(v[i], math.sqrt(v[(i + 1) % n] ** 2 + 2 * A_BRAKE * seg[i]))
    return v


def _at(cum, values, s):
    """Linear interpolation of per-point values at distance s along the closed line."""
    s %= cum[-1]
    i = min(bisect.bisect_right(cum, s) - 1, len(values) - 1)
    f = (s - cum[i]) / (cum[i + 1] - cum[i]) if cum[i + 1] > cum[i] else 0.0
    return values[i] + (values[(i + 1) % len(values)] - values[i]) * f


def _packet(x, z, v, lap, last_ms, fuel, throttle, brake, steer, gear, rpm, tyres, car_id):
    b = bytearray(0x158)
    struct.pack_into("<I", b, tm.OFF_MAGIC, 0x47375330)
    struct.pack_into("<3f", b, tm.OFF_POS, x, 0.0, z)
    struct.pack_into("<f", b, tm.OFF_RPM, rpm)
    struct.pack_into("<f", b, tm.OFF_FUEL_LEVEL, fuel)
    struct.pack_into("<f", b, tm.OFF_FUEL_CAP, 100.0)
    struct.pack_into("<f", b, tm.OFF_SPEED, v)
    struct.pack_into("<4f", b, tm.OFF_TYRE_FL, *tyres)
    struct.pack_into("<h", b, tm.OFF_LAP, lap)
    struct.pack_into("<i", b, tm.OFF_BEST_MS, -1)
    struct.pack_into("<i", b, tm.OFF_LAST_MS, last_ms)
    struct.pack_into("<H", b, tm.OFF_FLAGS, tm.FLAG_ON_TRACK)
    b[tm.OFF_GEAR] = gear
    b[tm.OFF_THROTTLE] = throttle
    b[tm.OFF_BRAKE] = brake
    struct.pack_into("<i", b, tm.OFF_CAR_ID, car_id)
    struct.pack_into("<f", b, tm.OFF_STEER, steer)
    b[tm.OFF_THROTTLE_INPUT] = throttle
    b[tm.OFF_BRAKE_INPUT] = brake
    return bytes(b)


def build(out_dir, row, laps=6, hz=60, start=None, car_id=CAR_ID, profile="demo",
          mirror=False):
    """Write one recording into out_dir; returns its path, layout and timed lap times."""
    path = [(-p[0] if mirror else p[0], p[1]) for p in row["path"]]
    seg, cum, curv = _line(path)
    total = cum[-1]
    xs, zs = [p[0] for p in path], [p[1] for p in path]
    w = gt7_recording.RecordingWriter(out_dir, profile, "demo", queue_max=0)
    ts = start if start is not None else time.time() - 3600
    dt = 1.0 / hz
    s = 0.6 * total               # the out-lap starts mid-lap, as after leaving the pits
    lap, lap_t, last_ms, fuel = 0, 0.0, -1, 60.0
    times, report_at, tail = [], None, None
    v_lap = _speeds(seg, curv, lap)
    while tail is None or ts < tail:
        v = _at(cum, v_lap, s)
        acc = (_at(cum, v_lap, s + v * dt) - v) / dt
        throttle = 0 if acc < -1.0 else 255 if acc > 0.3 or v >= V_MAX * 0.97 else 140
        brake = min(255, int(-acc / A_BRAKE * 255)) if acc < -1.0 else 0
        steer = max(-2.6, min(2.6, _at(cum, curv, s) * 2.7 * 14.0))
        gear = 1 + sum(v > g for g in GEARS)
        lo = GEARS[gear - 2] if gear > 1 else 0.0
        hi = GEARS[gear - 1] if gear <= len(GEARS) else V_MAX
        rpm = 4200.0 + 4300.0 * (v - lo) / max(1.0, hi - lo)
        tyres = tuple(78.0 + 6.0 * math.sin(s / 900.0 + i) + 0.4 * lap for i in range(4))
        if report_at is not None and lap_t >= report_at:
            last_ms, report_at = round(times[-1] * 1000), None
        w.put(ts, "~", _packet(_at(cum, xs, s), _at(cum, zs, s), v, lap, last_ms, fuel,
                               throttle, brake, steer, gear, rpm, tyres, car_id))
        ts += dt
        lap_t += dt
        fuel -= 0.00045 * v * dt
        s_next = s + v * dt
        if int(s_next // total) > int(s // total):
            if lap >= 1:
                times.append(lap_t)
                report_at = 0.5          # GT7 shows the lap time shortly after the line
            lap, lap_t = lap + 1, 0.0
            v_lap = _speeds(seg, curv, lap)
            if lap > laps:
                tail = ts + 4.0
        s = s_next
    w.close()
    return {"path": w.path, "official_id": row["official_id"],
            "official_name": row["official_name"], "lap_times": times}


def main():
    logsetup.harden_stdio()
    ap = argparse.ArgumentParser(
        description="Write a synthetic GT7 telemetry recording along a real racing line.")
    ap.add_argument("--out", required=True,
                    help="recordings dir, e.g. runtime/solo-pov/telemetry-recordings")
    ap.add_argument("--track", default="Suzuka",
                    help="part of the layout name or its official id (default: Suzuka)")
    ap.add_argument("--laps", type=int, default=6, help="timed laps after the out-lap")
    ap.add_argument("--hz", type=int, default=60, help="packets per second")
    ap.add_argument("--start", type=float,
                    help="wall time of the first packet (default: one hour ago)")
    ap.add_argument("--signatures",
                    default=gt7_data.resolve("signatures.json", os.path.join(ROOT, "runtime")),
                    help="signatures.json with the racing lines (default: the downloaded one)")
    ap.add_argument("--mirror", action="store_true",
                    help="mirror the line so no layout matches (shows Set track)")
    args = ap.parse_args()
    if not os.path.isfile(args.signatures):
        sys.exit("no racing lines yet: run 'racecast gt7-data update'")
    row = pick_row(load_rows(args.signatures), args.track)
    out = build(args.out, row, laps=args.laps, hz=args.hz, start=args.start,
                mirror=args.mirror)
    laps = ", ".join(f"{t:.3f}" for t in out["lap_times"])
    print(f"wrote {out['path']}: {out['official_name']} ({out['official_id']}), laps {laps}")


if __name__ == "__main__":
    main()

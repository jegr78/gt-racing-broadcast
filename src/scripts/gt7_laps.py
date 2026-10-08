#!/usr/bin/env python3
"""GT7 lap index: sector math over a lap trace.

Stdlib only and no relay imports. A trace is one point every STEP_M metres of lap
distance, starting at 0.0 on the line.
"""
import bisect
import math

STEP_M = 5.0
SECTOR_M = 200.0
COUNTED = ("reference", "counted")


def _time_at(trace, d):
    """Lap time at distance d, interpolated between stations; None outside the trace."""
    if not trace or d < trace[0]["d"] or d > trace[-1]["d"]:
        return None
    ds = [p["d"] for p in trace]
    k = bisect.bisect_left(ds, d)
    if ds[k] == d:
        return trace[k]["t"]
    a, b = trace[k - 1], trace[k]
    return a["t"] + (b["t"] - a["t"]) * (d - a["d"]) / (b["d"] - a["d"])


def lap_length_m(lap):
    """A lap's length for sectors: where its trace ends."""
    trace = lap.get("trace") or []
    return trace[-1]["d"] if trace else 0.0


def sectors(trace, length_m, step_m=SECTOR_M):
    """Sector times with boundaries every step_m from the line; the last sector ends at
    length_m and may be shorter. None for a sector the trace does not cover."""
    if not trace or not length_m or length_m <= 0:
        return []
    n = max(1, math.ceil(length_m / step_m - 1e-9))
    bounds = [k * step_m for k in range(n)] + [length_m]
    times = [_time_at(trace, b) for b in bounds]
    return [round(t1 - t0, 3) if t0 is not None and t1 is not None else None
            for t0, t1 in zip(times, times[1:], strict=False)]


def best_sectors(laps):
    """Per sector the fastest time over the given laps that carry a trace."""
    per_lap = [sectors(lap["trace"], lap_length_m(lap)) for lap in laps if lap.get("trace")]
    n = max((len(s) for s in per_lap), default=0)
    best = []
    for i in range(n):
        vals = [s[i] for s in per_lap if i < len(s) and s[i] is not None]
        best.append(min(vals) if vals else None)
    return best


def theoretical_best(laps):
    """Sum of the best sectors over the given laps, or None when a sector has no time."""
    best = best_sectors(laps)
    if not best or any(v is None for v in best):
        return None
    return round(sum(best), 3)

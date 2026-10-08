#!/usr/bin/env python3
"""GT7 lap index: sector math, traces, the cache and the comparison pool.
Run: python3 tests/test_gt7_laps.py"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import gt7_laps as gl


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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

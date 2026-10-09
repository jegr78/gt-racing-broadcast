#!/usr/bin/env python3
"""tools/make-demo-recording.py writes a recording the lap index recognises.
Run: python3 tests/test_make_demo_recording.py"""
import importlib.util, json, math, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import gt7_laps
import gt7_tracks

_spec = importlib.util.spec_from_file_location(
    "make_demo_recording", os.path.join(ROOT, "tools", "make-demo-recording.py"))
demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo)


def _circuit(step=20.0):
    """A closed line with corners of different radii, counter-clockwise, a point every
    ~step metres; not mirror-symmetric, so a mirrored lap matches nothing."""
    dense = []
    for i in range(3600):
        a = 2 * math.pi * i / 3600
        r = 300.0 * (1 + 0.35 * math.cos(2 * a) + 0.12 * math.cos(3 * a))
        dense.append((r * math.cos(a), r * math.sin(a)))
    path, run = [dense[0]], 0.0
    for p, q in zip(dense, dense[1:], strict=False):
        run += math.dist(p, q)
        if run >= step:
            path.append(q)
            run = 0.0
    return [[round(x, 1), round(z, 1)] for x, z in path]


def _row():
    path = _circuit()
    xs, zs = [p[0] for p in path], [p[1] for p in path]
    length = sum(math.dist(path[i], path[(i + 1) % len(path)]) for i in range(len(path)))
    return {"official_id": "demo01", "official_name": "Demo Circuit",
            "length_m": round(length, 1), "min_x": min(xs), "max_x": max(xs),
            "min_z": min(zs), "max_z": max(zs), "path": path, "reverse": None,
            "ambiguous_with": [], "flags": []}


def _db(d, row):
    """A track database that knows only `row`."""
    ip, sp = os.path.join(d, "index.json"), os.path.join(d, "signatures.json")
    with open(ip, "w", encoding="utf-8") as fh:
        json.dump({"format": "gt7-datalogger-track-index", "version": 1, "configurations": [
            {"official_id": row["official_id"], "track": "Demo Circuit", "layout": "Full",
             "reverse": False, "official_name": row["official_name"], "country": "X",
             "turns": 6, "length_m": row["length_m"]}]}, fh)
    with open(sp, "w", encoding="utf-8") as fh:
        json.dump({"format": "gt7-datalogger-track-signatures", "version": 1,
                   "signatures": [row]}, fh)
    return gt7_tracks.TrackDB(ip, sp, os.path.join(d, "learned-tracks.json"))


def _index(d, row, **kw):
    out = demo.build(os.path.join(d, "rec"), row, hz=20, start=1_700_000_000.0, **kw)
    return out, gt7_laps.index(out["path"], _db(d, row), None, d, key="demo/x")


def t_demo_recording_is_recognised_and_laps_trade_sectors():
    row = _row()
    with tempfile.TemporaryDirectory() as d:
        out, idx = _index(d, row, laps=3)
        assert len(out["lap_times"]) == 3 and out["official_id"] == row["official_id"]
        timed = [lap for lap in idx["laps"] if lap["status"] in gt7_laps.COUNTED]
        assert len(timed) == 3, [(lap["lap"], lap["status"], lap["reason"]) for lap in idx["laps"]]
        assert {lap["track_id"] for lap in timed} == {row["official_id"]}, idx["sessions"]
        assert len({lap["time_s"] for lap in timed}) == 3, "every lap has its own time"
        a, b = timed[0]["sectors"], timed[1]["sectors"]
        diffs = [y - x for x, y in zip(a, b, strict=False) if x is not None and y is not None]
        assert any(v < 0 for v in diffs) and any(v > 0 for v in diffs), \
            "lap B gains in some mini-sectors and loses in others"
        assert all(lap["trace"][10]["steer_deg"] is not None for lap in timed)


def t_recordings_started_apart_get_different_laps():
    row = _row()
    with tempfile.TemporaryDirectory() as d:
        times = [demo.build(os.path.join(d, str(i)), row, laps=2, hz=20,
                            start=1_700_000_000.0 + 56 * i)["lap_times"] for i in range(2)]
        assert times[0] != times[1], f"two demo recordings repeat the same laps: {times}"


def t_mirrored_demo_is_not_recognised():
    with tempfile.TemporaryDirectory() as d:
        _out, idx = _index(d, _row(), laps=2, mirror=True)
        assert idx["track"] is None or "candidates" in idx["track"], idx["track"]


def t_pick_row_by_name_or_id():
    rows = [{"official_id": "a1", "official_name": "Alpha Ring"},
            {"official_id": "b2", "official_name": "Beta Park"}]
    assert demo.pick_row(rows, "beta")["official_id"] == "b2"
    assert demo.pick_row(rows, "a1")["official_name"] == "Alpha Ring"
    assert demo.pick_row(rows, "")["official_id"] == "a1"
    try:
        demo.pick_row(rows, "gamma"); raise AssertionError("unknown layout accepted")
    except SystemExit as e:
        assert "gamma" in str(e)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

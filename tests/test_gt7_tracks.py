#!/usr/bin/env python3
"""GT7 track recognition from lap positions. Run: python3 tests/test_gt7_tracks.py"""
import importlib.util, json, math, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


gt = _load("gt7_tracks", ("src", "scripts", "gt7_tracks.py"))


def _loop(cx, cz, rx, rz, step=20.0):
    """An elliptic racing line, counter-clockwise, a point every ~step metres."""
    n = int(2 * math.pi * math.sqrt((rx * rx + rz * rz) / 2) / step)
    return [[cx + rx * math.cos(2 * math.pi * i / n), cz + rz * math.sin(2 * math.pi * i / n)]
            for i in range(n)]


def _length(path):
    return sum(math.dist(path[i], path[(i + 1) % len(path)]) for i in range(len(path)))


def _row(oid, name, path, reverse=None):
    xs, zs = [p[0] for p in path], [p[1] for p in path]
    return {"official_id": oid, "official_name": name, "length_m": round(_length(path), 1),
            "min_x": min(xs), "max_x": max(xs), "min_z": min(zs), "max_z": max(zs),
            "path": path, "reverse": reverse, "ambiguous_with": [], "flags": []}


OVAL = _loop(0, 0, 600, 300)
TWIN = _loop(5, 0, 600, 300)                 # nearly the same line: an ambiguous pair
OTHER = _loop(5000, 5000, 400, 400)


def _db(d, rows, catalog=None):
    cat = catalog or [
        {"official_id": "aaa001", "track": "Oval", "layout": "Full", "reverse": False,
         "official_name": "Oval", "country": "X", "turns": 4, "length_m": 2900},
        {"official_id": "aaa002", "track": "Oval", "layout": "Full", "reverse": True,
         "official_name": "Oval (Reverse)", "country": "X", "turns": 4, "length_m": 2900},
        {"official_id": "bbb001", "track": "Ring", "layout": "Full", "reverse": False,
         "official_name": "Ring", "country": "Y", "turns": 6, "length_m": 2500},
        {"official_id": "ccc001", "track": "Oval", "layout": "Twin", "reverse": False,
         "official_name": "Oval Twin", "country": "X", "turns": 4, "length_m": 2900}]
    ip, sp = os.path.join(d, "index.json"), os.path.join(d, "signatures.json")
    with open(ip, "w", encoding="utf-8") as fh:
        json.dump({"format": "gt7-datalogger-track-index", "version": 1,
                   "configurations": cat}, fh)
    with open(sp, "w", encoding="utf-8") as fh:
        json.dump({"format": "gt7-datalogger-track-signatures", "version": 1,
                   "signatures": rows}, fh)
    return gt.TrackDB(ip, sp, os.path.join(d, "learned-tracks.json"))


def _lap(path, offset=0.0, start=7):
    """Driven points: the line from index `start` once round, shifted sideways by `offset` m."""
    n = len(path)
    return [(path[(start + i) % n][0] + offset, path[(start + i) % n][1]) for i in range(n)]


def t_match_forward_and_reverse():
    with tempfile.TemporaryDirectory() as d:
        db = _db(d, [_row("aaa001", "Oval", OVAL, {"official_id": "aaa002",
                                                   "official_name": "Oval (Reverse)"}),
                     _row("bbb001", "Ring", OTHER)])
        L = _length(OVAL)
        m = db.match(_lap(OVAL, offset=5.0), L * 1.01)
        assert m["id"] == "aaa001" and m["layout"] == "Full" and m["reverse"] is False, m
        assert m["score_m"] < 6.0, m
        m = db.match(list(reversed(_lap(OVAL))), L)
        assert m["id"] == "aaa002" and m["reverse"] is True, m


def t_no_match_on_wrong_length_far_line_or_few_points():
    with tempfile.TemporaryDirectory() as d:
        db = _db(d, [_row("aaa001", "Oval", OVAL)])
        L = _length(OVAL)
        assert db.match(_lap(OVAL), L * 1.2) is None, "length outside 3 %"
        assert db.match(_lap(OVAL, offset=40.0), L) is None, "40 m off the line"
        assert db.match(_lap(OVAL)[:5], L) is None, "too few points"
        assert db.match(list(reversed(_lap(OVAL))), L) is None, "reverse without a twin row"


def t_ambiguous_pair_yields_candidates():
    with tempfile.TemporaryDirectory() as d:
        db = _db(d, [_row("aaa001", "Oval", OVAL), _row("ccc001", "Oval Twin", TWIN)])
        m = db.match(_lap(OVAL, offset=2.5), _length(OVAL))
        assert set(m["candidates"]) == {"aaa001", "ccc001"}, m


def t_rows_without_path_are_skipped_and_bad_files_load_empty():
    with tempfile.TemporaryDirectory() as d:
        row = _row("aaa001", "Oval", OVAL)
        del row["path"]
        db = _db(d, [row])
        assert db.match(_lap(OVAL), _length(OVAL)) is None
        bad = gt.TrackDB(os.path.join(d, "missing.json"), os.path.join(d, "missing2.json"))
        assert bad.match(_lap(OVAL), 1000.0) is None and bad.layouts() == []


def t_name_and_layouts():
    with tempfile.TemporaryDirectory() as d:
        db = _db(d, [])
        assert db.name("aaa002")["reverse"] is True and db.name("zzz") is None
        assert [c["id"] for c in db.layouts()] == ["aaa001", "aaa002", "ccc001", "bbb001"]


def t_missing_signatures_file_still_loads_the_catalogue():
    with tempfile.TemporaryDirectory() as d:
        _db(d, [])
        db = gt.TrackDB(os.path.join(d, "index.json"), os.path.join(d, "absent.json"))
        assert db.name("aaa001") is not None, "catalogue loads without signatures"
        assert db.match([(float(i), 0.0) for i in range(20)], 400.0) is None


def t_distance_m_projects_onto_the_line_not_the_nearest_vertex():
    with tempfile.TemporaryDirectory() as d:
        straight = [[20.0 * i, 0.0] for i in range(20)]       # vertices 20 m apart on z=0
        db = _db(d, [_row("aaa001", "Straight", straight)])
        # (10, 2) is abeam the midpoint of the vertices at x=0 and x=20: 2 m from the
        # line, ~10.2 m from the nearest vertex.
        d_line = db.distance_m("aaa001", 10.0, 2.0)
        assert d_line is not None and abs(d_line - 2.0) < 0.2, d_line


def t_distance_m_returns_none_for_non_finite_positions():
    with tempfile.TemporaryDirectory() as d:
        db = _db(d, [_row("aaa001", "Oval", OVAL)])
        assert db.distance_m("aaa001", float("nan"), 0.0) is None
        assert db.distance_m("aaa001", float("inf"), 0.0) is None


def _midpoint_lap(path, start=7):
    """Driven points exactly on the polyline, each one a segment midpoint."""
    n = len(path)
    out = []
    for i in range(n):
        a, b = path[(start + i) % n], path[(start + i + 1) % n]
        out.append(((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0))
    return out


def t_match_scores_the_line_not_the_vertex_phase():
    with tempfile.TemporaryDirectory() as d:
        db = _db(d, [_row("aaa001", "Oval", OVAL)])
        lap = _midpoint_lap(OVAL)
        m = db.match(lap, _length(OVAL))
        assert m is not None and m["id"] == "aaa001", m
        assert m["score_m"] < 1.0, m


def t_match_never_lists_the_same_id_twice():
    with tempfile.TemporaryDirectory() as d:
        _db(d, [_row("aaa001", "Oval", OVAL, {"official_id": "aaa002",
                                              "official_name": "Oval (Reverse)"})])
        learned = {"format": "racecast-gt7-learned", "version": 1,
                   "signatures": [_row("aaa002", "Oval (Reverse)", list(reversed(OVAL)))],
                   "assignments": {}}
        lp = os.path.join(d, "learned-tracks.json")
        with open(lp, "w", encoding="utf-8") as fh:
            json.dump(learned, fh)
        db = gt.TrackDB(os.path.join(d, "index.json"), os.path.join(d, "signatures.json"), lp)
        m = db.match(list(reversed(_lap(OVAL))), _length(OVAL))
        assert m is not None and "candidates" not in m, m
        assert m["id"] == "aaa002" and m["reverse"] is True, m


def t_match_reverse_without_catalogue_still_reports_reverse():
    with tempfile.TemporaryDirectory() as d:
        row = _row("aaa001", "Oval", OVAL, {"official_id": "aaa002",
                                            "official_name": "Oval (Reverse)"})
        sp = os.path.join(d, "signatures.json")
        with open(sp, "w", encoding="utf-8") as fh:
            json.dump({"format": "gt7-datalogger-track-signatures", "version": 1,
                       "signatures": [row]}, fh)
        db = gt.TrackDB(os.path.join(d, "missing-index.json"), sp)
        m = db.match(list(reversed(_lap(OVAL))), _length(OVAL))
        assert m is not None and "candidates" not in m, m
        assert m["id"] == "aaa002" and m["reverse"] is True, m


def t_trackdb_never_raises_on_malformed_data():
    with tempfile.TemporaryDirectory() as d:
        ip = os.path.join(d, "index.json")
        sp = os.path.join(d, "signatures.json")
        lp = os.path.join(d, "learned-tracks.json")

        with open(ip, "w", encoding="utf-8") as fh:
            json.dump([1, 2, 3], fh)                       # top-level list, not a dict
        with open(sp, "w", encoding="utf-8") as fh:
            json.dump({"format": "gt7-datalogger-track-signatures", "version": 1,
                       "signatures": "not-a-list"}, fh)
        db = gt.TrackDB(ip, sp, lp)
        assert db.layouts() == [] and db.match([(0.0, 0.0)] * 12, 100.0) is None

        with open(ip, "w", encoding="utf-8") as fh:
            json.dump({"format": "gt7-datalogger-track-index", "version": 1,
                       "configurations": "not-a-list"}, fh)
        good_path = [[20.0 * i, 0.0] for i in range(12)]
        bad_row = {"official_id": "aaa001", "official_name": "Bad", "length_m": 240.0,
                   "min_x": 0.0, "max_x": 220.0, "min_z": 0.0, "max_z": 0.0,
                   "path": [[1], *good_path], "reverse": "not-a-dict",
                   "ambiguous_with": [], "flags": []}
        with open(sp, "w", encoding="utf-8") as fh:
            json.dump({"format": "gt7-datalogger-track-signatures", "version": 1,
                       "signatures": [bad_row]}, fh)
        with open(lp, "w", encoding="utf-8") as fh:
            json.dump({"format": "racecast-gt7-learned", "version": 1,
                       "signatures": "nope", "assignments": [1, 2]}, fh)
        db = gt.TrackDB(ip, sp, lp)                        # must not raise
        assert db.layouts() == []                         # "configurations" wasn't a list
        assert db.name("aaa001") is not None               # the short point was skipped


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

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


def t_locate_measures_the_distance_to_the_line_not_the_nearest_vertex():
    line = gt._Line([[20.0 * i, 0.0] for i in range(20)])   # vertices 20 m apart on z=0
    # (10, 2) is abeam the midpoint of the vertices at x=0 and x=20: 2 m from the
    # line, ~10.2 m from the nearest vertex.
    d_line = line.locate(10.0, 2.0)[1]
    assert abs(d_line - 2.0) < 0.2, d_line


def t_project_returns_none_slots_for_non_finite_points():
    with tempfile.TemporaryDirectory() as d:
        db = _db(d, [_row("aaa001", "Oval", OVAL)])
        s = db.project([(float("nan"), 0.0), (float("inf"), 0.0), tuple(OVAL[0])], "aaa001")
        assert s[0] is None and s[1] is None and s[2] is not None and s[2] < 1.0, s


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


def t_project_runs_along_the_line_and_reverses():
    with tempfile.TemporaryDirectory() as d:
        db = _db(d, [_row("aaa001", "Oval", OVAL, {"official_id": "aaa002",
                                                   "official_name": "Oval (Reverse)"})])
        L = db.line_length("aaa001")
        s = db.project([tuple(OVAL[0]), tuple(OVAL[10]), (OVAL[10][0] + 3, OVAL[10][1])],
                       "aaa001")
        assert s[0] < 1.0 and abs(s[1] - s[2]) < 3.5, s
        expected = sum(math.dist(OVAL[i], OVAL[i + 1]) for i in range(10))
        assert abs(s[1] - expected) < 1e-6, (s[1], expected)
        r = db.project([tuple(OVAL[10])], "aaa002")[0]
        assert abs(r - (L - s[1])) < 1e-6, (r, L - s[1])
        assert db.project([(0, 0)], "nope") is None and db.line_length("nope") is None


def t_learn_adds_row_and_assignment_that_survive_reload():
    with tempfile.TemporaryDirectory() as d:
        db = _db(d, [])
        lap = _lap(OTHER)
        assert db.match(lap, _length(OTHER)) is None
        db.learn("bbb001", lap, _length(OTHER), key="solo-pov/20261007-201503")
        again = _db(d, [])
        m = again.match(lap, _length(OTHER))
        assert m["id"] == "bbb001" and m["track"] == "Ring", m
        assert again.assignment("solo-pov/20261007-201503") == "bbb001"
        assert again.assignment("other/x") is None
        try:
            gt.TrackDB(os.path.join(d, "index.json"), os.path.join(d, "signatures.json")).learn(
                "bbb001", lap, 1.0)
            raise AssertionError("learn without a learned path accepted")
        except ValueError:
            pass


def t_learn_onto_a_downloaded_line_only_assigns_the_recording():
    with tempfile.TemporaryDirectory() as d:
        db = _db(d, [_row("aaa001", "Oval", OVAL), _row("bbb001", "Ring", OTHER)])
        mirrored = [(-x, z) for x, z in _lap(OVAL)]
        assert db.learn("aaa001", mirrored, _length(OVAL), key="solo-pov/m") is False, \
            "a downloaded line is kept, only the recording is assigned"
        again = _db(d, [_row("aaa001", "Oval", OVAL), _row("bbb001", "Ring", OTHER)])
        m = again.match(_lap(OVAL, offset=5.0), _length(OVAL))
        assert m["id"] == "aaa001" and m["score_m"] < 6.0, f"a correct lap still matches: {m}"
        assert again.assignment("solo-pov/m") == "aaa001"
        with open(os.path.join(d, "learned-tracks.json"), encoding="utf-8") as fh:
            assert json.load(fh)["signatures"] == [], "no learned row for a downloaded id"
        assert again.learn("bbb001", [(0.0, 0.0)], 1.0, key="solo-pov/n") is False, \
            "an assignment needs no usable lap"
        try:
            again.learn("aaa001", _lap(OVAL), _length(OVAL))
            raise AssertionError("an assignment without a key was accepted")
        except ValueError:
            pass
        assert again.learn("ccc001", _lap(TWIN, offset=900.0), _length(TWIN),
                           key="solo-pov/t") is True, "an id without a downloaded line learns it"


def t_learn_onto_the_reverse_of_a_downloaded_line_only_assigns():
    with tempfile.TemporaryDirectory() as d:
        rows = [_row("aaa001", "Oval", OVAL, {"official_id": "aaa002",
                                              "official_name": "Oval (Reverse)"})]
        db = _db(d, rows)
        before = db.project([tuple(OVAL[10])], "aaa002")[0]
        assert db.learn("aaa002", [(-x, z) for x, z in OVAL], _length(OVAL),
                        key="solo-pov/r") is False, "the reverse twin keeps the downloaded line"
        again = _db(d, rows)
        assert again.assignment("solo-pov/r") == "aaa002"
        assert abs(again.project([tuple(OVAL[10])], "aaa002")[0] - before) < 1e-6
        with open(os.path.join(d, "learned-tracks.json"), encoding="utf-8") as fh:
            assert json.load(fh)["signatures"] == [], "no learned row for the reverse twin"


def t_a_downloaded_line_wins_over_an_earlier_learned_row():
    with tempfile.TemporaryDirectory() as d:
        mirrored = [(-x, z) for x, z in _lap(OVAL)]
        assert _db(d, []).learn("aaa001", mirrored, _length(OVAL), key="solo-pov/a") is True
        assert _db(d, []).learn("aaa002", mirrored, _length(OVAL), key="solo-pov/b") is True
        shipped = _db(d, [_row("aaa001", "Oval", OVAL, {"official_id": "aaa002",
                                                        "official_name": "Oval (Reverse)"})])
        m = shipped.match(_lap(OVAL, offset=5.0), _length(OVAL))
        assert m and m["id"] == "aaa001" and m["score_m"] < 6.0, f"the downloaded line wins: {m}"
        m = shipped.match(list(reversed(_lap(OVAL))), _length(OVAL))
        assert m and m["id"] == "aaa002", f"and its reverse twin too: {m}"
        assert shipped.project([tuple(OVAL[0])], "aaa001")[0] < 1.0
        assert shipped.assignment("solo-pov/a") == "aaa001"
        assert shipped.assignment("solo-pov/b") == "aaa002"
        with open(os.path.join(d, "learned-tracks.json"), encoding="utf-8") as fh:
            assert len(json.load(fh)["signatures"]) == 2, "the learned rows stay in the file"
        assert _db(d, []).project([mirrored[1]], "aaa001")[0] < 20.0, \
            "without the download the learned rows apply again"


def t_learn_on_unwritable_path_raises_and_leaves_no_tmp_file():
    with tempfile.TemporaryDirectory() as d:
        _db(d, [])
        blocker = os.path.join(d, "blocker")
        with open(blocker, "w", encoding="utf-8") as fh:
            fh.write("not a directory")
        lp = os.path.join(blocker, "learned-tracks.json")       # makedirs must fail: not a dir
        db = gt.TrackDB(os.path.join(d, "index.json"), os.path.join(d, "signatures.json"), lp)
        try:
            db.learn("bbb001", _lap(OTHER), _length(OTHER), key="solo-pov/x")
            raise AssertionError("learn on an unwritable path did not raise")
        except OSError:
            pass
        assert db.assignment("solo-pov/x") is None, "write failed, nothing should be assigned"
        assert not [f for f in os.listdir(d) if f.endswith(".tmp")]


def t_learn_drops_non_finite_points_and_raises_when_too_few_remain():
    with tempfile.TemporaryDirectory() as d:
        db = _db(d, [])
        lap = _lap(OTHER)
        db.learn("bbb001", lap, _length(OTHER), key="solo-pov/good")
        bad = [(float("nan"), 0.0)] * (gt.MIN_POINTS - 1) + lap[:gt.MIN_POINTS - 1]
        try:
            db.learn("bbb001", bad, _length(OTHER), key="solo-pov/bad")
            raise AssertionError("learn with too few finite points accepted")
        except ValueError:
            pass
        assert db.assignment("solo-pov/bad") is None, "the failed learn must not write anything"
        again = _db(d, [])
        assert again.assignment("solo-pov/good") == "bbb001", "the earlier good row must survive"


def t_learn_keeps_the_existing_row_reverse_twin():
    with tempfile.TemporaryDirectory() as d:
        db = _db(d, [_row("aaa001", "Oval", OVAL, {"official_id": "aaa002",
                                                   "official_name": "Oval (Reverse)"})])
        before = db.line_length("aaa002")
        db.learn("aaa001", _lap(OVAL), _length(OVAL), key="solo-pov/x")
        after = db.line_length("aaa002")
        assert after is not None and abs(after - before) < 1.0, (before, after)
        m = db.match(list(reversed(_lap(OVAL))), _length(OVAL))
        assert m is not None and m.get("id") == "aaa002" and m.get("reverse") is True, m


def t_relearning_same_id_replaces_its_row():
    with tempfile.TemporaryDirectory() as d:
        db = _db(d, [])
        lp = os.path.join(d, "learned-tracks.json")
        db.learn("bbb001", _lap(OTHER), _length(OTHER))
        newer = _lap(OTHER, offset=5.0)
        db.learn("bbb001", newer, _length(OTHER))
        with open(lp, encoding="utf-8") as fh:
            doc = json.load(fh)
        rows = [r for r in doc["signatures"] if r.get("official_id") == "bbb001"]
        assert len(rows) == 1, rows
        assert rows[0]["path"][0] == [round(newer[0][0], 1), round(newer[0][1], 1)], rows[0]


def t_learn_of_a_reverse_id_uses_its_own_path_for_project():
    with tempfile.TemporaryDirectory() as d:
        db = _db(d, [])
        rev_path = list(reversed(OVAL))
        db.learn("aaa002", rev_path, _length(OVAL))
        s = db.project([tuple(rev_path[0])], "aaa002")[0]
        assert s is not None and s < 1.0, s


def t_assignment_on_corrupt_learned_file_returns_none():
    with tempfile.TemporaryDirectory() as d:
        lp = os.path.join(d, "learned-tracks.json")
        with open(lp, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        db = gt.TrackDB(os.path.join(d, "index.json"), os.path.join(d, "signatures.json"), lp)
        assert db.assignment("solo-pov/x") is None


def t_non_string_reverse_official_id_tolerated_and_int_converted():
    """A malformed reverse.official_id (list, dict, int) must not raise out of
    TrackDB.__init__; the int case still yields a usable, string row id."""
    with tempfile.TemporaryDirectory() as d:
        third = _loop(-5000, -5000, 900, 450)   # a third length/box, clear of OVAL and OTHER
        rows = [_row("list001", "List", OVAL, {"official_id": ["x"]}),
                _row("dict001", "Dict", third, {"official_id": {"y": 1}}),
                _row("int001", "Int", OTHER, {"official_id": 5})]
        db = _db(d, rows)                                       # must not raise
        assert db.match(list(reversed(_lap(OVAL))), _length(OVAL)) is None, \
            "a list reverse id yields no usable twin"
        m = db.match(list(reversed(_lap(OTHER))), _length(OTHER))
        assert m is not None and m["id"] == "5" and isinstance(m["id"], str), m


def t_row_with_too_few_parsed_points_is_skipped():
    """A path whose raw length clears MIN_POINTS but where most entries fail to
    parse must be skipped, not loaded as a near-degenerate line."""
    with tempfile.TemporaryDirectory() as d:
        few = {"official_id": "few001", "official_name": "Few", "length_m": 1.0,
               "min_x": 0.0, "max_x": 100.0, "min_z": 0.0, "max_z": 100.0,
               "path": [[1]] * 10 + [[0.0, 0.0], [100.0, 100.0]],   # only 2 points parse
               "reverse": None, "ambiguous_with": [], "flags": []}
        db = _db(d, [few])
        assert db.name("few001") is None
        assert db.match([(0.0, 0.0)] * gt.MIN_POINTS, 1.0) is None
        assert db.project([(0.0, 0.0)], "few001") is None
        assert db.line_length("few001") is None


def t_zero_length_line_skipped_without_raising_in_match_and_project():
    """A path of identical points parses fine but has zero line length; _row
    must drop it so match/project/line_length never hit ZeroDivisionError."""
    with tempfile.TemporaryDirectory() as d:
        xs, zs = [p[0] for p in OVAL], [p[1] for p in OVAL]
        flat = {"official_id": "flat001", "official_name": "Flat", "length_m": _length(OVAL),
                "min_x": min(xs), "max_x": max(xs), "min_z": min(zs), "max_z": max(zs),
                "path": [[100.0, 200.0]] * 12, "reverse": None,
                "ambiguous_with": [], "flags": []}
        db = _db(d, [flat])
        assert db.name("flat001") is None
        assert db.match(_lap(OVAL), _length(OVAL)) is None    # must not raise ZeroDivisionError
        assert db.project([(0.0, 0.0)], "flat001") is None
        assert db.line_length("flat001") is None


def t_layouts_tolerates_non_string_track_and_layout_fields():
    """An upstream index.json row with a non-string track/layout must not make
    layouts()'s sort raise."""
    with tempfile.TemporaryDirectory() as d:
        cat = [{"official_id": "aaa001", "track": ["bad"], "layout": {"x": 1},
                "reverse": False, "official_name": "Bad", "country": "X",
                "turns": 1, "length_m": 100},
               {"official_id": "bbb001", "track": "Ring", "layout": "Full",
                "reverse": False, "official_name": "Ring", "country": "Y",
                "turns": 2, "length_m": 200}]
        db = _db(d, [], catalog=cat)
        ids = [c["id"] for c in db.layouts()]              # must not raise in sorted()
        assert set(ids) == {"aaa001", "bbb001"}, ids


def t_assignment_ignores_non_string_values():
    """A hand-edited learned file may hold a non-string assignment value;
    assignment(key) must treat it as unset rather than handing back a bad type."""
    with tempfile.TemporaryDirectory() as d:
        lp = os.path.join(d, "learned-tracks.json")
        with open(lp, "w", encoding="utf-8") as fh:
            json.dump({"format": "racecast-gt7-learned", "version": 1,
                       "signatures": [], "assignments": {"p/x": ["bad"], "p/y": "bbb001"}}, fh)
        db = gt.TrackDB(os.path.join(d, "index.json"), os.path.join(d, "signatures.json"), lp)
        assert db.assignment("p/x") is None
        assert db.assignment("p/y") == "bbb001"



def t_concurrent_learn_keeps_every_assignment():
    import threading, time
    with tempfile.TemporaryDirectory() as d:
        dbs = [_db(d, [_row("aaa001", "Oval", OVAL)]) for _ in range(8)]
        real = gt.TrackDB._read_learned

        def slow(self):
            doc = real(self)
            time.sleep(0.02)
            return doc
        gt.TrackDB._read_learned = slow
        try:
            threads = [threading.Thread(target=db.learn, args=("aaa001", [], 0.0),
                                        kwargs={"key": f"p/rec{i}"})
                       for i, db in enumerate(dbs)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            gt.TrackDB._read_learned = real
        fresh = _db(d, [_row("aaa001", "Oval", OVAL)])
        got = [fresh.assignment(f"p/rec{i}") for i in range(8)]
        assert got == ["aaa001"] * 8, f"a concurrent learn must not drop another's assignment: {got}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

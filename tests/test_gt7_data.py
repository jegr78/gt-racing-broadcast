#!/usr/bin/env python3
"""GT7 reference data: validation, runtime-over-bundled resolution, the update gate.
Run: python3 tests/test_gt7_data.py"""
import importlib.util, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


gd = _load("gt7_data", ("src", "scripts", "gt7_data.py"))


def _cars(n=400):
    return ("ID,ShortName,Maker\n" + "".join(f"{i},Car {i},1\n" for i in range(1, n + 1))).encode()


def _index(n=100):
    cfg = [{"official_id": f"{i:06x}", "track": f"T{i}", "layout": "Full", "reverse": False,
            "official_name": f"T{i}", "country": "X", "turns": 5, "length_m": 3000}
           for i in range(n)]
    return json.dumps({"format": "gt7-datalogger-track-index", "version": 1,
                       "configurations": cfg}).encode()


def _sigs(n=50):
    rows = [{"official_id": f"{i:06x}", "official_name": f"T{i}", "length_m": 3000.0,
             "min_x": 0, "max_x": 1, "min_z": 0, "max_z": 1, "path": [[0, 0], [1, 1]],
             "reverse": None} for i in range(n)]
    return json.dumps({"format": "gt7-datalogger-track-signatures", "version": 1,
                       "signatures": rows}).encode()


GOOD = {"cars.csv": _cars(), "maker.csv": b"ID,Name\n1,Maker\n",
        "cargrp.csv": b"ID,Group\n1,N\n", "index.json": _index(), "signatures.json": _sigs()}


def _fetch(files):
    def fetch(url):
        name = url.rsplit("/", 1)[1]
        v = files[name]
        if isinstance(v, Exception):
            raise v
        return v
    return fetch


def _bundled(d):
    b = os.path.join(d, "bundled")
    os.makedirs(b)
    for name, data in GOOD.items():
        with open(os.path.join(b, name), "wb") as fh:
            fh.write(data)
    return b


def t_validators_accept_good_and_reject_bad():
    for name, data in GOOD.items():
        assert gd.validate(name, data) >= 1, name
    bad = {"cars.csv": _cars(10), "maker.csv": b"ID,Label\n1,X\n", "cargrp.csv": b"<html>",
           "index.json": _index(5), "signatures.json": b"{\"format\": \"other\"}"}
    for name, data in bad.items():
        try:
            gd.validate(name, data)
        except ValueError:
            continue
        raise AssertionError(f"{name} accepted")


def t_resolve_prefers_valid_runtime_copy():
    with tempfile.TemporaryDirectory() as d:
        b = _bundled(d)
        base = os.path.join(d, "runtime")
        assert gd.resolve("index.json", base, b) == os.path.join(b, "index.json")
        os.makedirs(gd.data_dir(base))
        with open(os.path.join(gd.data_dir(base), "index.json"), "wb") as fh:
            fh.write(b"broken")
        assert gd.resolve("index.json", base, b) == os.path.join(b, "index.json"), \
            "a broken runtime copy falls back to the bundled one"
        with open(os.path.join(gd.data_dir(base), "index.json"), "wb") as fh:
            fh.write(_index(120))
        assert gd.resolve("index.json", base, b) == os.path.join(gd.data_dir(base), "index.json")
        assert gd.cars_dir(base, b) == b, "car tables resolve as a set"


def t_update_writes_validated_files_and_gates_24h():
    with tempfile.TemporaryDirectory() as d:
        base = os.path.join(d, "runtime")
        res = gd.update(base, fetch=_fetch(GOOD), now=1000.0)
        assert res["checked"] and res["changed"], res
        assert set(res["files"].values()) == {"updated"}, res
        assert gd.update(base, fetch=_fetch(GOOD), now=2000.0) == \
            {"checked": False, "changed": False, "files": {}}, "inside 24 h nothing is fetched"
        res = gd.update(base, fetch=_fetch(GOOD), now=1000.0 + gd.UPDATE_EVERY_S + 1)
        assert set(res["files"].values()) == {"unchanged"} and not res["changed"], res
        st = gd.status(base)
        assert st["files"]["cars.csv"]["source"] == "runtime"
        assert st["files"]["cars.csv"]["rows"] == 400


def t_update_accepts_a_str_fetch_result_without_raising():
    with tempfile.TemporaryDirectory() as d:
        base = os.path.join(d, "runtime")
        as_str = dict(GOOD, **{"index.json": _index().decode("utf-8")})
        res = gd.update(base, fetch=_fetch(as_str), now=1000.0)
        assert res["files"]["index.json"] == "updated", res
        with open(os.path.join(gd.data_dir(base), "index.json"), "rb") as fh:
            assert fh.read() == _index(), "a str fetch result is encoded to utf-8 before writing"


def t_cars_dir_falls_back_to_bundled_when_one_runtime_table_is_broken():
    with tempfile.TemporaryDirectory() as d:
        b = _bundled(d)
        base = os.path.join(d, "runtime")
        os.makedirs(gd.data_dir(base))
        with open(os.path.join(gd.data_dir(base), "cars.csv"), "wb") as fh:
            fh.write(GOOD["cars.csv"])
        with open(os.path.join(gd.data_dir(base), "maker.csv"), "wb") as fh:
            fh.write(GOOD["maker.csv"])
        with open(os.path.join(gd.data_dir(base), "cargrp.csv"), "wb") as fh:
            fh.write(b"<html>broken</html>")
        assert gd.cars_dir(base, b) == b, "one broken car table falls the whole set back to bundled"


def t_update_failure_keeps_old_file_and_retries():
    with tempfile.TemporaryDirectory() as d:
        base = os.path.join(d, "runtime")
        gd.update(base, fetch=_fetch(GOOD), now=1000.0)
        broken = dict(GOOD, **{"signatures.json": b"<html>rate limited</html>",
                               "index.json": OSError("offline")})
        res = gd.update(base, force=True, fetch=_fetch(broken), now=5000.0)
        assert res["files"]["signatures.json"].startswith("error")
        assert res["files"]["index.json"] == "error: offline"
        with open(os.path.join(gd.data_dir(base), "signatures.json"), "rb") as fh:
            assert fh.read() == GOOD["signatures.json"], "a failed download keeps the old file"
        offline = {k: OSError("offline") for k in GOOD}
        res = gd.update(base, force=True, fetch=_fetch(offline), now=9000.0)
        assert not res["changed"]
        assert gd.status(base)["checked"] == 5000.0, "an all-failed run does not move the gate"


def t_data_version_changes_with_learned_file():
    with tempfile.TemporaryDirectory() as d:
        b = _bundled(d)
        base = os.path.join(d, "runtime")
        v1 = gd.data_version(base, b)
        os.makedirs(gd.data_dir(base))
        with open(gd.learned_path(base), "w", encoding="utf-8") as fh:
            fh.write("{}")
        assert gd.data_version(base, b) != v1 and len(v1) == 16


def t_update_tolerates_a_corrupt_stamp_file():
    with tempfile.TemporaryDirectory() as d:
        base = os.path.join(d, "runtime")
        os.makedirs(gd.data_dir(base))
        with open(os.path.join(gd.data_dir(base), gd.STAMP), "w", encoding="utf-8") as fh:
            json.dump({"checked": "not-a-number", "sha256": [], "updated": 5}, fh)
        res = gd.update(base, force=True, fetch=_fetch(GOOD), now=1000.0)
        assert set(res["files"].values()) == {"updated"}, res


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

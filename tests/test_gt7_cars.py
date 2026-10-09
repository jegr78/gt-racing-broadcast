#!/usr/bin/env python3
"""GT7 car id -> car lookup (#713). Run: python3 tests/test_gt7_cars.py"""
import importlib.util, os, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


gc = _load("gt7_cars", ("src", "scripts", "gt7_cars.py"))
SHIPPED = os.path.join(ROOT, "src", "assets", "gt7")


def _db(cars, makers="ID,Name,Country\n7,Chevrolet,1\n25,Mitsubishi,2\n",
        groups="ID,Group\n3424,N\n365,4\n"):
    d = tempfile.mkdtemp()
    for name, body in (("cars.csv", cars), ("maker.csv", makers), ("cargrp.csv", groups)):
        if body is not None:
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                f.write(body)
    return d


def t_lookup_known_car():
    db = gc.CarDB(_db("ID,ShortName,Maker\n3424,Lancer Evolution IX MR GSR '06,25\n"))
    assert db.lookup(3424) == {"id": 3424, "maker": "Mitsubishi",
                               "name": "Lancer Evolution IX MR GSR '06", "group": "N"}


def t_race_group_label():
    db = gc.CarDB(_db("ID,ShortName,Maker\n365,155 2.5 V6 TI '93,7\n"))
    assert db.lookup(365)["group"] == "Gr.4"


def t_unknown_id_falls_back():
    """A car a GT7 update added after the tables were refreshed."""
    db = gc.CarDB(_db("ID,ShortName,Maker\n"))
    assert db.lookup(9999) == {"id": 9999, "maker": None, "name": "Car #9999", "group": None}


def t_no_car_is_none():
    db = gc.CarDB(_db("ID,ShortName,Maker\n"))
    assert db.lookup(None) is None and db.lookup(0) is None and db.lookup(-1) is None


def t_missing_tables_never_raise():
    db = gc.CarDB(os.path.join(tempfile.mkdtemp(), "absent"))
    assert db.lookup(3424)["name"] == "Car #3424"


def t_malformed_rows_are_skipped():
    db = gc.CarDB(_db("ID,ShortName,Maker\nnot-a-number,X,25\n3424,Evo,25\n", groups=None))
    assert db.lookup(3424) == {"id": 3424, "maker": "Mitsubishi", "name": "Evo", "group": None}


def t_shipped_tables_name_the_verified_cars():
    """The vendored tables resolve the two cars verified in a live session."""
    db = gc.CarDB(SHIPPED)
    evo = db.lookup(3424)
    assert (evo["maker"], evo["name"], evo["group"]) == (
        "Mitsubishi", "Lancer Evolution IX MR GSR '06", "N")
    alfa = db.lookup(365)
    assert (alfa["maker"], alfa["group"]) == ("Alfa Romeo", "Gr.4")
    assert len(db) > 500


def t_default_dir_is_the_shipped_tables():
    assert os.path.normpath(gc.default_dir()) == os.path.normpath(SHIPPED)


def t_fetch_tool_uses_the_shared_validator():
    tool = _load("fetch_gt7_data", ("tools", "fetch-gt7-data.py"))
    assert tool.gt7_data.SOURCES["cars.csv"].endswith("/cars.csv")
    for name in tool.BUNDLED:
        with open(os.path.join(SHIPPED, name), "rb") as f:
            assert tool.gt7_data.validate(name, f.read()) > 0, f"bundled {name} must validate"
    assert "signatures.json" not in tool.BUNDLED, "signatures.json is downloaded at runtime only"


def t_fetch_tool_writes_through_gt7_data():
    import shutil
    import sys
    tool = _load("fetch_gt7_data", ("tools", "fetch-gt7-data.py"))
    with tempfile.TemporaryDirectory() as d:
        for name in tool.BUNDLED:
            shutil.copy(os.path.join(SHIPPED, name), d)

        def get_bytes(url, timeout=None):
            with open(os.path.join(SHIPPED, url.rsplit("/", 1)[1]), "rb") as f:
                return f.read()
        written = []
        real_get, real_write, real_dir = tool.http_util.get_bytes, tool.gt7_data.write_file, tool.GT7_DIR
        real_argv = sys.argv
        tool.http_util.get_bytes, tool.GT7_DIR, sys.argv = get_bytes, d, ["fetch-gt7-data.py"]
        tool.gt7_data.write_file = lambda dir_, name, data: (written.append(name),
                                                             real_write(dir_, name, data))
        try:
            assert tool.main() == 0
        finally:
            tool.http_util.get_bytes, tool.gt7_data.write_file = real_get, real_write
            tool.GT7_DIR, sys.argv = real_dir, real_argv
        assert sorted(written) == sorted(tool.BUNDLED), f"every file goes through write_file: {written}"
        assert sorted(os.listdir(d)) == sorted(tool.BUNDLED), f"no temp file is left: {os.listdir(d)}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

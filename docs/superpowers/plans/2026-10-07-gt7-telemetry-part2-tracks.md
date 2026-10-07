# GT7 Telemetry Part 2: Data Updates and Track Recognition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep GT7 car and track reference data current at runtime, and name the track and layout of every lap from the car's positions, live in the Director Panel and in the CSV export.

**Architecture:** `src/scripts/gt7_data.py` owns the five reference files: bundled copies in `src/assets/gt7/`, newer validated copies in `<runtime>/gt7/`, a 24 h update gate and a manual update. `src/scripts/gt7_tracks.py` loads the track catalogue and racing-line signatures and matches a lap's 20 m position samples against them (length, bounding box, mean distance to the line, driving direction). The telemetry engine collects the positions and asks the matcher after each closed lap; the exporter decides the track per GT7 session and projects samples onto the racing line.

**Tech Stack:** Python 3 stdlib only. Tests are runnable stdlib scripts under `tests/` (no pytest).

**Spec:** `docs/superpowers/specs/2026-10-07-gt7-telemetry-recording-design.md`, section "Part 2". Issue #787, part 2 of epic #785. Requires part 1 (#786) merged: `gt7_recording`, `TelemetryEngine.on_lap`, `lap_distance()`, `export_csv(..., cars=None)`, `racecast telemetry export`.

## Global Constraints

- Edit only under `src/`, `tests/`, `tools/`, `docs/`. Never touch `dist/` or `runtime/`.
- Code, comments, docs, help text: English only. argparse/usage help strings ASCII only.
- Comments minimal: one reason, one sentence.
- Outbound HTTP from `src/scripts/*`, `racecast.py` and `ui_server.py` goes through `http_util` (`tests/test_http_util.py` enforces it).
- Nothing in the telemetry path may raise into `_telemetry_loop`, a request handler or the relay start; data problems degrade to "unknown track" / "Car #<id>".
- Tests run on any machine and on Windows CI: `tempfile`, no network (inject `fetch`), `os.path.join` only for local paths.
- Data sources, verbatim:
  - `GT7INFO_URL = "https://raw.githubusercontent.com/ddm999/gt7info/web-new/_data/db/"` for `cars.csv`, `maker.csv`, `cargrp.csv` (MIT-0).
  - `TRACKDATA_URL = "https://raw.githubusercontent.com/jbhoorasingh/gt7-datalogger-track-data/main/"` for `index.json` (`format` `gt7-datalogger-track-index`, version 1, key `configurations`) and `signatures.json` (`format` `gt7-datalogger-track-signatures`, version 1, key `signatures`).
- Match constants, verbatim: length tolerance 3 %, box margin 50 m, max mean distance 15 m, ambiguity window 3 m, position step 20 m, grid cell 50 m, at least 10 points.
- Learned file `<runtime>/gt7/learned-tracks.json`: `{"format": "racecast-gt7-learned", "version": 1, "signatures": [...], "assignments": {"<profile>/<stem>": "<official_id>"}}`. Updates never write it.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Run `python3 tools/lint.py` after every Python change.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/scripts/gt7_data.py` | create | sources, validators, `resolve`, `cars_dir`, `update`, `status`, `data_version`, `learned_path` |
| `src/scripts/gt7_tracks.py` | create | `TrackDB`: catalogue, signatures, learned rows, `match`, `project`, `learn`, `assignment` |
| `src/assets/gt7/index.json`, `signatures.json`, `LICENSE-track-data`, `README.md` | create/modify | bundled track data and its licence |
| `tools/fetch-gt7-cars.py` -> `tools/fetch-gt7-data.py` | rename/rewrite | refresh the bundled copies through `gt7_data` |
| `src/scripts/gt7_telemetry.py` | modify | lap points, `distance_m`, live track detection, store `track()` and `reload_data` |
| `src/scripts/gt7_recording.py` | modify | `replay_laps`, `session_tracks`, `track`/`layout` columns, projected `lap_dist_m` |
| `src/relay/racecast-feeds.py` | modify | `--runtime-base`, load data via `gt7_data`, background update, `/status` `telemetry.track` |
| `src/racecast.py` | modify | `--runtime-base` for the relay, `racecast gt7-data update|status`, export with tracks, Control Center data functions |
| `src/ui/ui_server.py`, `src/ui/control-center.html` | modify | `/api/gt7-data`, `/api/gt7-data/update`, Settings row |
| `src/director/director-panel.html` | modify | `stTrack` in the status strip |
| `tools/build-binary.py` | modify | hidden imports `gt7_data`, `gt7_tracks` |
| tests: `test_gt7_data.py` (new), `test_gt7_tracks.py` (new), `test_gt7_cars.py`, `test_gt7_telemetry.py`, `test_gt7_recording.py`, `test_telemetry_endpoints.py`, `test_racecast.py`, `test_ui_server.py` | | |
| docs: `src/relay/CLAUDE.md`, `tools/CLAUDE.md`, wiki `Relay-Mode.md`, `Director.md`, `Control-Center.md`, images `director-panel.png`, `cc-settings.png` | | |

---

### Task 1: `gt7_data`: sources, validation, resolve, update

**Files:**
- Create: `src/scripts/gt7_data.py`
- Create: `tests/test_gt7_data.py`

**Interfaces:**
- Produces:
  - `CAR_TABLES` (dict name -> required columns), `SOURCES` (dict name -> (url, validator)), `UPDATE_EVERY_S = 86400`, `LEARNED = "learned-tracks.json"`.
  - `validate(name, data: bytes) -> int` (row count) or raises `ValueError`.
  - `bundled_dir() -> str` (`src/assets/gt7` next to `src/scripts`).
  - `data_dir(runtime_base) -> str` = `<runtime_base>/gt7`.
  - `resolve(name, runtime_base, bundled=None) -> str`: runtime copy if it validates, else bundled path.
  - `cars_dir(runtime_base, bundled=None) -> str`: runtime dir when all three car tables there validate, else bundled dir.
  - `learned_path(runtime_base) -> str`.
  - `update(runtime_base, force=False, fetch=None, now=None) -> {"checked": bool, "changed": bool, "files": {name: "updated"|"unchanged"|"error: ..."}}`. Never raises.
  - `status(runtime_base, bundled=None) -> {"checked": float|None, "files": {name: {"source": "runtime"|"bundled", "updated": float|None, "rows": int|None}}}`.
  - `data_version(runtime_base, bundled=None) -> str` (16 hex chars; changes when `index.json`, `signatures.json` or the learned file change).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gt7_data.py`:

```python
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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_gt7_data.py`
Expected: FAIL with `FileNotFoundError` for `gt7_data.py`.

- [ ] **Step 3: Implement `src/scripts/gt7_data.py`**

```python
#!/usr/bin/env python3
"""GT7 reference data: car tables plus the track catalogue and signatures.

The bundled copy in src/assets/gt7/ ships with racecast. update() fetches newer
copies into <runtime>/gt7/, and resolve() prefers a runtime copy once it validates.
"""
import csv
import hashlib
import io
import json
import os
import time

import http_util

GT7INFO_URL = "https://raw.githubusercontent.com/ddm999/gt7info/web-new/_data/db/"
TRACKDATA_URL = ("https://raw.githubusercontent.com/jbhoorasingh/"
                 "gt7-datalogger-track-data/main/")
CAR_TABLES = {
    "cars.csv": ("ID", "ShortName", "Maker"),
    "maker.csv": ("ID", "Name"),
    "cargrp.csv": ("ID", "Group"),
}
MIN_CARS = 400            # far fewer rows means a truncated or wrong download
MIN_LAYOUTS = 100
MIN_SIGNATURES = 50
UPDATE_EVERY_S = 24 * 3600
STAMP = "updated.json"
LEARNED = "learned-tracks.json"


def _validate_csv(name, data):
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
    missing = [c for c in CAR_TABLES[name] if not rows or c not in rows[0]]
    if missing:
        raise ValueError(f"{name}: missing column(s) {', '.join(missing)}")
    if name == "cars.csv" and len(rows) < MIN_CARS:
        raise ValueError(f"{name}: only {len(rows)} rows, expected at least {MIN_CARS}")
    return len(rows)


def _validate_json(data, fmt, key, minimum, fields):
    doc = json.loads(data)
    if not isinstance(doc, dict) or doc.get("format") != fmt or doc.get("version") != 1:
        raise ValueError(f"not a {fmt} v1 file")
    rows = doc.get(key)
    if not isinstance(rows, list) or len(rows) < minimum:
        raise ValueError(f"{key}: expected at least {minimum} rows")
    if not all(isinstance(r, dict) and all(f in r for f in fields) for r in rows):
        raise ValueError(f"{key}: rows lack {', '.join(fields)}")
    return len(rows)


def validate(name, data):
    """Row count of a usable copy of `name`; ValueError otherwise."""
    try:
        if name in CAR_TABLES:
            return _validate_csv(name, data)
        if name == "index.json":
            return _validate_json(data, "gt7-datalogger-track-index", "configurations",
                                  MIN_LAYOUTS, ("official_id", "track", "layout"))
        if name == "signatures.json":
            return _validate_json(data, "gt7-datalogger-track-signatures", "signatures",
                                  MIN_SIGNATURES, ("official_id", "length_m", "min_x",
                                                   "max_x", "min_z", "max_z"))
    except (UnicodeDecodeError, csv.Error) as e:
        raise ValueError(f"{name}: {e}") from e
    raise ValueError(f"unknown GT7 data file {name}")


SOURCES = {name: GT7INFO_URL + name for name in CAR_TABLES}
SOURCES.update({name: TRACKDATA_URL + name for name in ("index.json", "signatures.json")})


def bundled_dir():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "assets", "gt7")


def data_dir(runtime_base):
    return os.path.join(runtime_base, "gt7")


def learned_path(runtime_base):
    return os.path.join(data_dir(runtime_base), LEARNED)


_VALID = {}


def _is_valid(path, name):
    try:
        st = os.stat(path)
    except OSError:
        return False
    key = (path, st.st_mtime_ns, st.st_size)
    if key not in _VALID:
        try:
            with open(path, "rb") as fh:
                validate(name, fh.read())
            _VALID[key] = True
        except (OSError, ValueError):
            _VALID[key] = False
    return _VALID[key]


def resolve(name, runtime_base, bundled=None):
    path = os.path.join(data_dir(runtime_base), name) if runtime_base else None
    if path and _is_valid(path, name):
        return path
    return os.path.join(bundled or bundled_dir(), name)


def cars_dir(runtime_base, bundled=None):
    d = data_dir(runtime_base) if runtime_base else None
    if d and all(_is_valid(os.path.join(d, n), n) for n in CAR_TABLES):
        return d
    return bundled or bundled_dir()


def _read_stamp(d):
    try:
        with open(os.path.join(d, STAMP), encoding="utf-8") as fh:
            stamp = json.load(fh)
        return stamp if isinstance(stamp, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(d, name, data):
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, os.path.join(d, name))


def update(runtime_base, force=False, fetch=None, now=None):
    """Fetch every source, keep only validated files; at most once per 24 h unless forced."""
    fetch = fetch or (lambda url: http_util.get_bytes(url, timeout=20))
    now = time.time() if now is None else now
    d = data_dir(runtime_base)
    stamp = _read_stamp(d)
    if not force and now - float(stamp.get("checked") or 0) < UPDATE_EVERY_S:
        return {"checked": False, "changed": False, "files": {}}
    shas = dict(stamp.get("sha256") or {})
    times = dict(stamp.get("updated") or {})
    files = {}
    for name, url in SOURCES.items():
        try:
            data = fetch(url)
            validate(name, data)
        except Exception as e:  # noqa: BLE001  offline or a bad download keeps the old file
            files[name] = f"error: {e}"
            continue
        sha = hashlib.sha256(data).hexdigest()
        if shas.get(name) == sha and os.path.exists(os.path.join(d, name)):
            files[name] = "unchanged"
            continue
        try:
            _write(d, name, data)
        except OSError as e:
            files[name] = f"error: {e}"
            continue
        shas[name], times[name] = sha, now
        files[name] = "updated"
    fetched = any(not v.startswith("error") for v in files.values())
    if fetched:
        try:
            _write(d, STAMP, json.dumps({"checked": now, "sha256": shas,
                                         "updated": times}).encode("utf-8"))
        except OSError:
            pass  # the next start simply checks again
    return {"checked": True, "changed": "updated" in files.values(), "files": files}


def status(runtime_base, bundled=None):
    stamp = _read_stamp(data_dir(runtime_base)) if runtime_base else {}
    out = {}
    for name in SOURCES:
        path = resolve(name, runtime_base, bundled)
        runtime = bool(runtime_base) and path == os.path.join(data_dir(runtime_base), name)
        try:
            with open(path, "rb") as fh:
                rows = validate(name, fh.read())
        except (OSError, ValueError):
            rows = None
        out[name] = {"source": "runtime" if runtime else "bundled",
                     "updated": (stamp.get("updated") or {}).get(name) if runtime else None,
                     "rows": rows}
    return {"checked": stamp.get("checked"), "files": out}


def data_version(runtime_base, bundled=None):
    """Changes whenever the track data or the learned tracks change (lap-index caches key on it)."""
    h = hashlib.sha1()
    paths = [resolve(n, runtime_base, bundled) for n in ("index.json", "signatures.json")]
    if runtime_base:
        paths.append(learned_path(runtime_base))
    for p in paths:
        try:
            st = os.stat(p)
            h.update(f"{p}|{st.st_mtime_ns}|{st.st_size};".encode("utf-8"))
        except OSError:
            h.update(f"{p}|-;".encode("utf-8"))
    return h.hexdigest()[:16]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_gt7_data.py && python3 tests/test_http_util.py && python3 tools/lint.py`
Expected: ALL PASS (the http_util guard accepts `gt7_data.py` because it only uses `http_util`), lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/scripts/gt7_data.py tests/test_gt7_data.py
git commit -m "feat(gt7): reference data with validated runtime updates (#787)"
```

---

### Task 2: Bundled track data, fetch tool, car tables through `gt7_data`

**Files:**
- Rename: `tools/fetch-gt7-cars.py` -> `tools/fetch-gt7-data.py` (rewrite)
- Create: `src/assets/gt7/index.json`, `src/assets/gt7/signatures.json` (generated by the tool), `src/assets/gt7/LICENSE-track-data`
- Modify: `src/assets/gt7/README.md`, `src/scripts/gt7_cars.py` (module docstring line 6), `tools/CLAUDE.md` (line ~155)
- Test: `tests/test_gt7_cars.py` (replace `t_fetch_tool_validates_a_download`), `tests/test_gt7_data.py`

**Interfaces:**
- Consumes: Task 1 `SOURCES`, `validate`, `CAR_TABLES`.
- Produces: bundled `index.json` + `signatures.json` that pass `validate`; `tools/fetch-gt7-data.py [--dry-run]`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_gt7_cars.py`, replace `t_fetch_tool_validates_a_download` with:

```python
def t_fetch_tool_uses_the_shared_validator():
    tool = _load("fetch_gt7_data", ("tools", "fetch-gt7-data.py"))
    assert tool.gt7_data.SOURCES["cars.csv"].endswith("/cars.csv")
    for name in tool.gt7_data.SOURCES:
        with open(os.path.join(SHIPPED, name), "rb") as f:
            assert tool.gt7_data.validate(name, f.read()) > 0, f"bundled {name} must validate"
```

Add to `tests/test_gt7_data.py`:

```python
def t_bundled_files_validate_and_carry_licence():
    b = gd.bundled_dir()
    for name in gd.SOURCES:
        with open(os.path.join(b, name), "rb") as fh:
            assert gd.validate(name, fh.read()) > 0, name
    with open(os.path.join(b, "LICENSE-track-data"), encoding="utf-8") as fh:
        text = fh.read()
    assert "CC0" in text and "MIT License" in text and "zetetos" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_gt7_cars.py; python3 tests/test_gt7_data.py`
Expected: FAIL (`fetch-gt7-data.py` missing; bundled `index.json` missing).

- [ ] **Step 3: Implement**

```bash
git mv tools/fetch-gt7-cars.py tools/fetch-gt7-data.py
```

Replace the content of `tools/fetch-gt7-data.py`:

```python
#!/usr/bin/env python3
"""Refresh the bundled GT7 reference data (src/assets/gt7/) from its two sources.

Car tables from ddm999/gt7info (MIT-0); the track catalogue (index.json, CC0) and
track signatures (signatures.json) from jbhoorasingh/gt7-datalogger-track-data.
Every file is validated by src/scripts/gt7_data.py before it replaces the local
copy. Installs refresh the same files at runtime (racecast gt7-data update); this
tool keeps the copy that ships current.

Usage:
  python3 tools/fetch-gt7-data.py              # refresh the bundled files
  python3 tools/fetch-gt7-data.py --dry-run    # show the changes, write nothing

Maintainer tool, not shipped in the distributable package.
"""
import argparse, csv, io, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import gt7_data  # noqa: E402
import http_util  # noqa: E402

GT7_DIR = os.path.join(ROOT, "src", "assets", "gt7")


def _ids(name, data):
    if name == "cars.csv":
        return {r["ID"]: r["ShortName"] for r in csv.DictReader(io.StringIO(data.decode()))}
    if name == "index.json":
        return {c["official_id"]: c["official_name"] for c in json.loads(data)["configurations"]}
    if name == "signatures.json":
        return {r["official_id"]: r["official_name"] for r in json.loads(data)["signatures"]}
    return {}


def main():
    ap = argparse.ArgumentParser(description="Refresh the bundled GT7 reference data.")
    ap.add_argument("--dry-run", action="store_true", help="show the changes, write nothing")
    args = ap.parse_args()
    fetched = {}
    for name, url in gt7_data.SOURCES.items():
        data = http_util.get_bytes(url, timeout=30)
        rows = gt7_data.validate(name, data)
        fetched[name] = data
        old = {}
        path = os.path.join(GT7_DIR, name)
        if os.path.exists(path):
            with open(path, "rb") as fh:
                old = _ids(name, fh.read())
        new = _ids(name, data)
        for key in sorted(set(new) - set(old)):
            print(f"+ {name} {key} {new[key]}")
        for key in sorted(set(old) - set(new)):
            print(f"- {name} {key} {old[key]}")
        print(f"{name}: {rows} rows")
    if args.dry_run:
        return 0
    os.makedirs(GT7_DIR, exist_ok=True)
    for name, data in fetched.items():
        tmp = os.path.join(GT7_DIR, name + ".tmp")
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, os.path.join(GT7_DIR, name))
    print("updated", GT7_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Run it once to vendor the track files (needs network): `python3 tools/fetch-gt7-data.py`. Check `git status`: `index.json` and `signatures.json` appear in `src/assets/gt7/`, the car tables change only if gt7info moved.

Create `src/assets/gt7/LICENSE-track-data` with the CC0 dedication text and the MIT notice. Copy them verbatim from the source repository:

```bash
curl -sL https://raw.githubusercontent.com/jbhoorasingh/gt7-datalogger-track-data/main/LICENSE > /tmp/cc0.txt
curl -sL https://raw.githubusercontent.com/jbhoorasingh/gt7-datalogger-track-data/main/vendor/gt-telemetry-LICENSE > /tmp/mit.txt
```

and write the file as:

```
index.json and signatures.json come from
https://github.com/jbhoorasingh/gt7-datalogger-track-data

<contents of /tmp/cc0.txt>

signatures.json also builds on recordings from https://github.com/zetetos/gt-telemetry,
under this licence:

<contents of /tmp/mit.txt>
```

Replace `src/assets/gt7/README.md`:

```markdown
# GT7 reference data

- `cars.csv`, `maker.csv`, `cargrp.csv` come from the community database
  [ddm999/gt7info](https://github.com/ddm999/gt7info) (`_data/db/`, licence MIT-0). The
  relay names the car in each telemetry packet from them (`src/scripts/gt7_cars.py`).
- `index.json` (every GT7 layout) and `signatures.json` (length, bounding box and racing
  line of the layouts that can be recognised) come from
  [jbhoorasingh/gt7-datalogger-track-data](https://github.com/jbhoorasingh/gt7-datalogger-track-data).
  Licences: `LICENSE-track-data`. `src/scripts/gt7_tracks.py` names the track from them.

Do not edit these files by hand. `python3 tools/fetch-gt7-data.py` refreshes the copy
that ships; an install keeps newer copies in `runtime/gt7/` (`racecast gt7-data update`).
```

In `src/scripts/gt7_cars.py` line 6, change `refreshed with tools/fetch-gt7-cars.py` to `refreshed with tools/fetch-gt7-data.py`. In `tools/CLAUDE.md` line ~155 change the command to `python3 tools/fetch-gt7-data.py         # --dry-run lists added/removed cars and layouts`.

Run `grep -rn "fetch-gt7-cars" . --include=*.py --include=*.md --include=*.yml` (exclude `runtime/`, `dist/`) and update every hit.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_gt7_cars.py && python3 tests/test_gt7_data.py && python3 tools/lint.py`
Expected: ALL PASS, lint clean.

- [ ] **Step 5: Commit**

```bash
git add tools/fetch-gt7-data.py src/assets/gt7 src/scripts/gt7_cars.py tools/CLAUDE.md tests/test_gt7_cars.py tests/test_gt7_data.py
git commit -m "feat(gt7): bundle the track catalogue and signatures, one fetch tool (#787)"
```

---

### Task 3: `TrackDB`: loading and matching

**Files:**
- Create: `src/scripts/gt7_tracks.py`
- Create: `tests/test_gt7_tracks.py`

**Interfaces:**
- Consumes: Task 1 `gt7_data.resolve`, `gt7_data.learned_path`.
- Produces (`gt7_tracks`):
  - Constants `LENGTH_TOL = 0.03`, `BOX_MARGIN_M = 50.0`, `MAX_SCORE_M = 15.0`, `AMBIGUOUS_M = 3.0`, `GRID_M = 50.0`, `MIN_POINTS = 10`, `POINT_STEP_M = 20.0`.
  - `TrackDB(index_path, signatures_path, learned_path=None)`; `TrackDB.load(runtime_base, bundled=None)`.
  - `name(official_id) -> {"id", "track", "layout", "reverse", "country", "length_m", "official_name"}` or None.
  - `layouts() -> list` of `name()` dicts sorted by `(track, layout, reverse)`.
  - `match(points, length_m) -> {"id", "track", "layout", "reverse", "score_m"} | {"candidates": [ids]} | None`.
  - `distance_m(official_id, x, z) -> float | None`: distance of a point to the layout's racing line.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gt7_tracks.py`:

```python
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


def t_real_fixture_points_lie_on_nurburgring_gp():
    """The captured '~' packets (#711) were driven on the Nürburgring GP."""
    sys.path.insert(0, HERE)
    from test_gt7_fixture import EXT_LEFT_HEX, EXT_RIGHT_HEX, EXT_TCS_HEX, EXT_BRAKE_HEX, gc, tm
    db = gt.TrackDB.load(None)
    gp = [c["id"] for c in db.layouts() if c["official_name"] == "Nürburgring GP"]
    assert gp, "bundled index lists the Nürburgring GP"
    for hexpkt in (EXT_LEFT_HEX, EXT_RIGHT_HEX, EXT_TCS_HEX, EXT_BRAKE_HEX):
        p = tm.parse_packet(gc.decrypt_packet(bytes.fromhex(hexpkt)))
        assert db.distance_m(gp[0], p.pos_x, p.pos_z) < 10.0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_gt7_tracks.py`
Expected: FAIL with `FileNotFoundError` for `gt7_tracks.py`.

- [ ] **Step 3: Implement `src/scripts/gt7_tracks.py`**

```python
#!/usr/bin/env python3
"""GT7 track recognition: name the layout from one lap of car positions.

A lap's (x, z) points every 20 m are compared with the racing lines in
signatures.json (plus learned rows): length within 3 %, inside the bounding box,
close to the line, and the driving direction separates a layout from its reverse.
"""
import json
import logging
import math
import os

import gt7_data

LOG = logging.getLogger("racecast.relay.telemetry")

LENGTH_TOL = 0.03
BOX_MARGIN_M = 50.0
MAX_SCORE_M = 15.0
AMBIGUOUS_M = 3.0
GRID_M = 50.0
MIN_POINTS = 10
POINT_STEP_M = 20.0
LEARNED_FORMAT = "racecast-gt7-learned"


class _Line:
    """A closed racing line with cumulative distance and a grid for nearest-point lookups."""

    def __init__(self, path):
        self.pts = [(float(p[0]), float(p[1])) for p in path]
        self.cum = [0.0]
        for (x0, z0), (x1, z1) in zip(self.pts, self.pts[1:]):
            self.cum.append(self.cum[-1] + math.hypot(x1 - x0, z1 - z0))
        (xa, za), (xb, zb) = self.pts[-1], self.pts[0]
        self.length = self.cum[-1] + math.hypot(xb - xa, zb - za)
        self.grid = {}
        for i, (x, z) in enumerate(self.pts):
            self.grid.setdefault((int(x // GRID_M), int(z // GRID_M)), []).append(i)

    def nearest(self, x, z):
        """(index, distance) of the closest line point."""
        cx, cz = int(x // GRID_M), int(z // GRID_M)
        for r in (1, 2, 4, 8):
            best = None
            for gx in range(cx - r, cx + r + 1):
                for gz in range(cz - r, cz + r + 1):
                    for i in self.grid.get((gx, gz), ()):
                        d = math.hypot(self.pts[i][0] - x, self.pts[i][1] - z)
                        if best is None or d < best[1]:
                            best = (i, d)
            if best is not None and best[1] <= r * GRID_M:   # nothing closer outside the ring
                return best
        return min(((i, math.hypot(px - x, pz - z)) for i, (px, pz) in enumerate(self.pts)),
                   key=lambda t: t[1])

    def station(self, x, z):
        """Distance along the line from its first point to the projection of (x, z)."""
        i, _ = self.nearest(x, z)
        n = len(self.pts)
        best = None
        for a, b in (((i - 1) % n, i), (i, (i + 1) % n)):
            (ax, az), (bx, bz) = self.pts[a], self.pts[b]
            dx, dz = bx - ax, bz - az
            seg2 = dx * dx + dz * dz
            u = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((x - ax) * dx + (z - az) * dz) / seg2))
            d = math.hypot(x - (ax + u * dx), z - (az + u * dz))
            s = self.cum[a] + u * math.sqrt(seg2)
            if best is None or d < best[0]:
                best = (d, s % self.length)
        return best[1]


def _direction(indices, n):
    """Positive when the indices mostly rise along the line (forward), negative when they fall."""
    total = 0
    for a, b in zip(indices, indices[1:]):
        step = (b - a) % n
        total += step - n if step > n / 2 else step
    return total


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, TypeError):
        return None


def _row(raw, provenance=None):
    path = raw.get("path")
    if not isinstance(path, list) or len(path) < MIN_POINTS:
        return None
    try:
        return {"id": str(raw["official_id"]), "length_m": float(raw["length_m"]),
                "box": (float(raw["min_x"]), float(raw["max_x"]),
                        float(raw["min_z"]), float(raw["max_z"])),
                "line": _Line(path),
                "reverse": (raw.get("reverse") or {}).get("official_id"),
                "official_name": raw.get("official_name") or "",
                "provenance": provenance or raw.get("provenance") or ""}
    except (KeyError, TypeError, ValueError):
        return None


class TrackDB:
    """Track catalogue plus recognisable signatures; never raises on bad data files."""

    def __init__(self, index_path, signatures_path, learned_path=None):
        self._learned_path = learned_path
        self._catalog = {}
        doc = _read_json(index_path) or {}
        for c in doc.get("configurations") or []:
            if isinstance(c, dict) and c.get("official_id"):
                self._catalog[str(c["official_id"])] = {
                    "id": str(c["official_id"]), "track": c.get("track") or "",
                    "layout": c.get("layout") or "", "reverse": bool(c.get("reverse")),
                    "country": c.get("country") or "", "length_m": c.get("length_m"),
                    "official_name": c.get("official_name") or ""}
        self._shipped = {}
        doc = _read_json(signatures_path) or {}
        for raw in doc.get("signatures") or []:
            row = _row(raw) if isinstance(raw, dict) else None
            if row:
                self._shipped[row["id"]] = row
        self._reload_learned()

    @classmethod
    def load(cls, runtime_base, bundled=None):
        return cls(gt7_data.resolve("index.json", runtime_base, bundled),
                   gt7_data.resolve("signatures.json", runtime_base, bundled),
                   gt7_data.learned_path(runtime_base) if runtime_base else None)

    def _read_learned(self):
        doc = _read_json(self._learned_path) if self._learned_path else None
        if not isinstance(doc, dict) or doc.get("format") != LEARNED_FORMAT:
            return {"format": LEARNED_FORMAT, "version": 1, "signatures": [],
                    "assignments": {}}
        doc.setdefault("signatures", [])
        doc.setdefault("assignments", {})
        return doc

    def _reload_learned(self):
        doc = self._read_learned()
        learned = {}
        for raw in doc["signatures"]:
            row = _row(raw, "learned") if isinstance(raw, dict) else None
            if row:
                learned[row["id"]] = row
        self._assign = dict(doc["assignments"]) if isinstance(doc["assignments"], dict) else {}
        self._rows = dict(self._shipped)
        self._rows.update(learned)                 # a learned row wins over the shipped one
        self._twins = {r["reverse"]: r["id"] for r in self._rows.values() if r["reverse"]}

    def name(self, official_id):
        info = self._catalog.get(official_id)
        if info is not None:
            return dict(info)
        row = self._rows.get(official_id)
        if row is None:
            return None
        return {"id": official_id, "track": row["official_name"], "layout": "",
                "reverse": False, "country": "", "length_m": row["length_m"],
                "official_name": row["official_name"]}

    def layouts(self):
        return sorted((dict(c) for c in self._catalog.values()),
                      key=lambda c: (c["track"], c["layout"], c["reverse"]))

    def _line_for(self, official_id):
        """(line, reversed) for a layout id, or (None, False)."""
        row = self._rows.get(official_id)
        if row is not None:
            return row["line"], False
        fwd = self._twins.get(official_id)
        if fwd is not None:
            return self._rows[fwd]["line"], True
        return None, False

    def distance_m(self, official_id, x, z):
        line, _rev = self._line_for(official_id)
        return line.nearest(x, z)[1] if line is not None else None

    def match(self, points, length_m):
        if not points or len(points) < MIN_POINTS or not length_m:
            return None
        scored = []
        for row in self._rows.values():
            ref = row["length_m"]
            if abs(ref - length_m) > LENGTH_TOL * ref:
                continue
            x0, x1, z0, z1 = row["box"]
            m = BOX_MARGIN_M
            if any(not (x0 - m <= x <= x1 + m and z0 - m <= z <= z1 + m) for x, z in points):
                continue
            near = [row["line"].nearest(x, z) for x, z in points]
            score = sum(d for _i, d in near) / len(near)
            if score > MAX_SCORE_M:
                continue
            if _direction([i for i, _d in near], len(row["line"].pts)) >= 0:
                scored.append((score, row["id"]))
            elif row["reverse"]:
                scored.append((score, row["reverse"]))
        if not scored:
            return None
        scored.sort()
        best_score, best_id = scored[0]
        close = [oid for s, oid in scored if s - best_score <= AMBIGUOUS_M]
        if len(close) > 1:
            return {"candidates": close}
        info = self.name(best_id) or {"track": best_id, "layout": "", "reverse": False}
        return {"id": best_id, "track": info["track"], "layout": info["layout"],
                "reverse": info["reverse"], "score_m": round(best_score, 2)}
```

The catalogue order in `t_name_and_layouts` sorts `("Oval","Full",False)`, `("Oval","Full",True)`, `("Oval","Twin",False)`, `("Ring","Full",False)`, which is the expected list.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_gt7_tracks.py && python3 tools/lint.py`
Expected: ALL PASS, lint clean. If `t_real_fixture_points_lie_on_nurburgring_gp` fails, print the distances: the four points measured 2.5 to 6.6 m from the bundled line when the plan was written; a large jump means the bundled `signatures.json` changed shape.

- [ ] **Step 5: Commit**

```bash
git add src/scripts/gt7_tracks.py tests/test_gt7_tracks.py
git commit -m "feat(gt7): recognise the track layout from one lap of positions (#787)"
```

---

### Task 4: `TrackDB`: projection, learning, assignments

**Files:**
- Modify: `src/scripts/gt7_tracks.py`
- Test: `tests/test_gt7_tracks.py`

**Interfaces:**
- Produces:
  - `project(points, official_id) -> list[float] | None`: per point the distance along the layout's line from the line's start, in `[0, L)`; for a reverse twin, measured in its own driving direction.
  - `line_length(official_id) -> float | None`.
  - `learn(official_id, points, length_m, key=None) -> None`: writes the learned row and, with `key`, the assignment; raises `ValueError` when no learned path is configured or fewer than `MIN_POINTS` points.
  - `assignment(key) -> str | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gt7_tracks.py` (above `__main__`):

```python
def t_project_runs_along_the_line_and_reverses():
    with tempfile.TemporaryDirectory() as d:
        db = _db(d, [_row("aaa001", "Oval", OVAL, {"official_id": "aaa002",
                                                   "official_name": "Oval (Reverse)"})])
        L = db.line_length("aaa001")
        s = db.project([tuple(OVAL[0]), tuple(OVAL[10]), (OVAL[10][0] + 3, OVAL[10][1])],
                       "aaa001")
        assert s[0] < 1.0 and abs(s[1] - s[2]) < 3.5, s
        assert 150 < s[1] < 250, "OVAL[10] lies about 10 x 20 m along the line"
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_gt7_tracks.py`
Expected: FAIL with `AttributeError: 'TrackDB' object has no attribute 'line_length'`.

- [ ] **Step 3: Implement**

Add to `TrackDB`:

```python
    def line_length(self, official_id):
        line, _rev = self._line_for(official_id)
        return line.length if line is not None else None

    def project(self, points, official_id):
        line, rev = self._line_for(official_id)
        if line is None:
            return None
        out = []
        for x, z in points:
            s = line.station(x, z)
            out.append((line.length - s) % line.length if rev else s)
        return out

    def assignment(self, key):
        return self._assign.get(key)

    def learn(self, official_id, points, length_m, key=None):
        """Store a learned signature for `official_id` (and the recording assignment `key`)."""
        if not self._learned_path:
            raise ValueError("no learned-tracks file configured")
        if len(points) < MIN_POINTS:
            raise ValueError("a lap needs at least %d position points" % MIN_POINTS)
        xs, zs = [p[0] for p in points], [p[1] for p in points]
        info = self.name(official_id) or {}
        row = {"official_id": official_id,
               "official_name": info.get("official_name") or official_id,
               "length_m": round(float(length_m), 1),
               "min_x": min(xs), "max_x": max(xs), "min_z": min(zs), "max_z": max(zs),
               "provenance": "learned", "reverse": None, "ambiguous_with": [], "flags": [],
               "path": [[round(x, 1), round(z, 1)] for x, z in points]}
        doc = self._read_learned()
        doc["signatures"] = [r for r in doc["signatures"]
                             if not (isinstance(r, dict) and r.get("official_id") == official_id)]
        doc["signatures"].append(row)
        if key:
            doc["assignments"][key] = official_id
        os.makedirs(os.path.dirname(self._learned_path), exist_ok=True)
        tmp = self._learned_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        os.replace(tmp, self._learned_path)
        self._reload_learned()
```

A learned row for a reverse layout has `reverse: None` and its own `path` in driving order, so `project` uses it directly (rows win over twins in `_line_for`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_gt7_tracks.py && python3 tools/lint.py`
Expected: ALL PASS, lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/scripts/gt7_tracks.py tests/test_gt7_tracks.py
git commit -m "feat(gt7): project laps onto the racing line and learn unknown tracks (#787)"
```

---

### Task 5: Engine collects positions and detects the track live

**Files:**
- Modify: `src/scripts/gt7_telemetry.py` (`_LapAccumulator`, `TelemetryEngine`, `TelemetryStore`)
- Test: `tests/test_gt7_telemetry.py`, `tests/test_telemetry_endpoints.py`

**Interfaces:**
- Consumes: Task 3 `TrackDB.match` (any object with `match(points, length_m)`).
- Produces:
  - Lap records (`on_lap`) gain `points` (list of `(x, z)` every 20 m of driven distance, starting at the lap's first moving sample) and `distance_m` (float).
  - `TelemetryEngine.track_db` (None by default), `TelemetryEngine.track` (None, a match dict with `id`, or `{"candidates": [...]}`); reset to None at a session boundary.
  - `TelemetryStore(..., tracks=None)`, `TelemetryStore.track() -> dict | None`, `TelemetryStore.has_tracks() -> bool`, `TelemetryStore.reload_data(cars, tracks)`; `data()["track"]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_gt7_telemetry.py`: extend `_packet` so tests can set positions (it already packs `pos` since part 1; check `grep -n "OFF_POS" tests/test_gt7_telemetry.py`). Add:

```python
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
    assert all(15.0 <= b[0] - a[0] <= 25.0 for a, b in zip(pts, pts[1:])), pts[:4]
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
    eng.track_db = _FakeTracks({"candidates": ["a", "b"]})
    eng.update(tm.parse_packet(_packet(lap=1)), 0.0)
    t = _drive_xy(eng, 0.1, 2, 10.0)
    eng.update(tm.parse_packet(_packet(lap=3)), t)
    assert eng.track == {"candidates": ["a", "b"]}

    class Boom:
        def match(self, *a):
            raise RuntimeError("bad data")
    eng.track_db = Boom()
    t = _drive_xy(eng, t + 0.1, 3, 10.0)
    eng.update(tm.parse_packet(_packet(lap=4)), t)        # must not raise
    assert eng.track == {"candidates": ["a", "b"]}
```

`tests/test_telemetry_endpoints.py`, before `t_zz_no_test_reached_a_real_obs`:

```python
def t_status_reports_track_only_with_track_db():
    import json

    class _StatusRelay:
        def status(self):
            return {}

    class _Tracks:
        def match(self, points, length_m):
            return None

    store = m.gt7_telemetry.TelemetryStore(None, tracks=_Tracks())
    srv, get = _serve(store, relay=_StatusRelay())
    try:
        tel = json.loads(get("/status")[2])["telemetry"]
        assert "track" in tel and tel["track"] is None, tel
        assert json.loads(get("/telemetry/data")[2])["track"] is None
    finally:
        srv.shutdown()


def t_store_reload_data_swaps_cars_and_tracks():
    class _Cars:
        def lookup(self, car_id):
            return {"id": car_id, "maker": "New", "name": "Car", "group": None}
    store = m.gt7_telemetry.TelemetryStore(None)
    assert not store.has_tracks()
    store.reload_data(_Cars(), object())
    assert store.has_tracks() and store._lookup_car(5)["maker"] == "New"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_gt7_telemetry.py; python3 tests/test_telemetry_endpoints.py`
Expected: FAIL (`KeyError: 'points'`, `TypeError: unexpected keyword argument 'tracks'`).

- [ ] **Step 3: Implement**

`_LapAccumulator`: add `"points", "next_point_m"` to `__slots__`; in `__init__`: `self.points = []` and `self.next_point_m = 0.0`. In `add()`, directly after `self.distance += max(0.0, pkt.speed_mps) * dt`:

```python
        if pkt.pos_x is not None and self.distance >= self.next_point_m:
            self.points.append((pkt.pos_x, pkt.pos_z))
            self.next_point_m = self.distance + POINT_STEP_M
```

Add the constants next to `SAMPLE_MIN_DIST`:

```python
POINT_STEP_M = 20.0       # metres between kept positions (track recognition)
MIN_TRACK_POINTS = 10     # same floor as gt7_tracks.MIN_POINTS
```

`TelemetryEngine.__init__`: add `self.track_db = None` and `self.track = None   # recognised layout, or {"candidates": [...]}`.

`_emit_lap`: add `"points": list(acc.points), "distance_m": acc.distance` to the record dict.

New method:

```python
    def _detect_track(self, acc):
        if self.track_db is None or (self.track is not None and "id" in self.track):
            return
        if len(acc.points) < MIN_TRACK_POINTS:        # a partial or stationary lap names nothing
            return
        try:
            found = self.track_db.match(acc.points, acc.distance)
        except Exception as e:  # noqa: BLE001  bad track data must not stop the telemetry
            LOG.warning("GT7 track recognition failed: %s", e)
            return
        if found is None:
            return
        if "id" in found:
            LOG.info("GT7 track: %s %s%s", found["track"], found["layout"],
                     " (reverse)" if found["reverse"] else "")
        self.track = found
```

Call `self._detect_track(acc)` in `_finalise_lap` right after `if acc is None: return`. In `_reset_session` add `self.track = None`.

`TelemetryStore.__init__`: add `tracks=None` and `self._eng.track_db = tracks`. Add:

```python
    def has_tracks(self):
        return self._eng.track_db is not None

    def track(self):
        with self._lock:
            return dict(self._eng.track) if self._eng.track else None

    def reload_data(self, cars, tracks):
        """Swap in refreshed car and track data (after a GT7 data update)."""
        with self._lock:
            self._cars = cars
            self._eng.track_db = tracks
```

In `data()`, after `out["car"] = ...`: `out["track"] = self.track()` (call outside the earlier `with self._lock` block, as `visible()` is).

Relay `/status` (Part 1 put the `telemetry` block at `racecast-feeds.py` ~line 11180): after the `record` addition:

```python
                    if telemetry_store.has_tracks():
                        base["telemetry"]["track"] = telemetry_store.track()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_gt7_telemetry.py && python3 tests/test_telemetry_endpoints.py && python3 tests/test_gt7_recording.py && python3 tools/lint.py`
Expected: ALL PASS, lint clean; `t_status_reports_telemetry_visibility` still passes (no tracks -> no key).

- [ ] **Step 5: Commit**

```bash
git add src/scripts/gt7_telemetry.py src/relay/racecast-feeds.py tests/test_gt7_telemetry.py tests/test_telemetry_endpoints.py
git commit -m "feat(telemetry): recognise the track after each lap and report it in /status (#787)"
```

---

### Task 6: Relay wiring, background update, Director Panel track

**Files:**
- Modify: `src/relay/racecast-feeds.py` (imports ~line 165; argparse near `--gt7-ps-ip` ~line 12562; telemetry startup ~line 12958)
- Modify: `src/racecast.py` (`_relay_runtime_args`, line ~922)
- Modify: `src/director/director-panel.html` (status strip line ~666, CSS line ~101, `relayPoll` ~line 2086, relay-down reset ~line 2182)
- Modify: `tools/build-binary.py` (hidden imports)
- Test: `tests/test_telemetry_endpoints.py`, `tests/test_racecast.py`

**Interfaces:**
- Consumes: Tasks 1, 3, 5.
- Produces: relay flag `--runtime-base DIR` (default: parent of `--runtime-dir`); env opt-out `RACECAST_GT7_DATA_UPDATE=0`; function `_gt7_data_refresh(store, runtime_base, bundled, update=gt7_data.update)` in the relay.

- [ ] **Step 1: Write the failing tests**

`tests/test_telemetry_endpoints.py`:

```python
def t_gt7_data_refresh_reloads_store_only_when_changed():
    import tempfile
    calls = []

    class _Store:
        def reload_data(self, cars, tracks):
            calls.append((cars, tracks))

    with tempfile.TemporaryDirectory() as d:
        bundled = os.path.join(ROOT, "src", "assets", "gt7")
        m._gt7_data_refresh(_Store(), d, bundled,
                            update=lambda base: {"checked": True, "changed": False, "files": {}})
        assert calls == []
        m._gt7_data_refresh(_Store(), d, bundled,
                            update=lambda base: {"checked": True, "changed": True,
                                                 "files": {"cars.csv": "updated"}})
        assert len(calls) == 1 and len(calls[0][0]) > 400, "reloaded with the bundled car tables"

        def boom(base):
            raise RuntimeError("offline")
        m._gt7_data_refresh(_Store(), d, bundled, update=boom)    # never raises
```

`tests/test_racecast.py`, near the existing `_relay_runtime_args` test (line ~608):

```python
def t_relay_runtime_args_pass_the_runtime_base():
    args = m._relay_runtime_args()
    i = args.index("--runtime-base")
    assert args[i + 1] == m._runtime_base_dir()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_telemetry_endpoints.py; python3 tests/test_racecast.py`
Expected: FAIL (`AttributeError: ... '_gt7_data_refresh'`, `ValueError: '--runtime-base' is not in list`).

- [ ] **Step 3: Implement**

Relay imports, after `import gt7_cars`:

```python
import gt7_data        # GT7 reference data: bundled + runtime updates (#787)
import gt7_tracks      # GT7 track recognition from lap positions (#787)
```

Helper next to `_telemetry_loop`:

```python
def _gt7_data_refresh(store, runtime_base, bundled, update=gt7_data.update):
    """Fetch newer GT7 car/track data (at most daily) and swap it into the running store."""
    try:
        res = update(runtime_base)
        if res.get("changed"):
            store.reload_data(gt7_cars.CarDB(gt7_data.cars_dir(runtime_base, bundled)),
                              gt7_tracks.TrackDB.load(runtime_base, bundled))
            LOG.info("GT7 data updated: %s", ", ".join(
                f"{k} {v}" for k, v in sorted(res["files"].items())))
        elif any(str(v).startswith("error") for v in res.get("files", {}).values()):
            LOG.info("GT7 data update incomplete: %s", res["files"])
    except Exception as e:  # noqa: BLE001  data refresh is best-effort
        LOG.info("GT7 data update skipped: %s", e)
```

Argparse, next to `--gt7-ps-ip`:

```python
    ap.add_argument("--runtime-base", default=None,
                    help="machine runtime dir (shared GT7 data); default: parent of --runtime-dir")
```

Telemetry startup: replace `cars=gt7_cars.CarDB(os.path.join(assets_dir, "gt7"))` and wire tracks:

```python
        gt7_bundled = os.path.join(assets_dir, "gt7")
        runtime_base = args.runtime_base or os.path.dirname(runtime)
```

and in the `TelemetryStore(...)` call:

```python
            cars=gt7_cars.CarDB(gt7_data.cars_dir(runtime_base, gt7_bundled)),
            tracks=gt7_tracks.TrackDB.load(runtime_base, gt7_bundled))
```

After the listener thread starts:

```python
        if str(os.environ.get("RACECAST_GT7_DATA_UPDATE", "1")).strip().lower() not in ("0", "false", "no", "off"):
            threading.Thread(target=_gt7_data_refresh,
                             args=(telemetry_store, runtime_base, gt7_bundled),
                             daemon=True).start()
```

Keep that `if` line under the lint line length by splitting the condition into a local `_update_on = ...`.

`src/racecast.py` `_relay_runtime_args`:

```python
    return (["--runtime-dir", _runtime_dir(), "--runtime-base", _runtime_base_dir(),
             "--cookies", _cookies_path()]
            + _overlay_relay_args(_active_overlay_dir()))
```

Check the existing test around `tests/test_racecast.py:608` still passes (it asserts `--runtime-dir` presence and the last element).

`tools/build-binary.py`, after the hidden imports added in part 1:

```python
           "--hidden-import", "gt7_data", "--hidden-import", "gt7_tracks",
```

Director Panel:
- CSS after the `#stCar b` rule: `#stTrack{max-width:100%;min-width:0}` and `#stTrack b{text-transform:none;letter-spacing:0;min-width:0;overflow:hidden;text-overflow:ellipsis}`.
- Markup after the `stCar` span: `<span class="st" id="stTrack" hidden>TRACK <b></b></span>`.
- In `relayPoll`, after the `stCar` block:

```js
    const trk = d.telemetry && d.telemetry.track;   // recognised after the first full lap (#787)
    $("#stTrack").hidden = !trk;
    $("#stTrack b").textContent = trackLabel(trk);
    $("#stTrack").title = trk && trk.candidates ? "Possible: " + trk.candidates.join(", ")
                                                : trackLabel(trk);
```

- Next to `carLabel`:

```js
function trackLabel(t){               // "Nürburgring - Grand Prix (reverse)"; text only
  if (!t) return "";
  if (t.candidates) return "?";
  return [t.track, t.layout].filter(Boolean).join(" - ") + (t.reverse ? " (reverse)" : "");
}
```

- In the relay-down branch next to `$("#stCar").hidden = true;` add `$("#stTrack").hidden = true;`.

The candidates tooltip shows ids; part 3 has the catalogue for names. Keep ids here: the panel has no catalogue.

- [ ] **Step 4: Run tests and verify visually**

Run: `python3 tests/test_telemetry_endpoints.py && python3 tests/test_racecast.py && python3 tools/lint.py`
Expected: ALL PASS, lint clean.

Invoke `ui-visual-verification` for the Director Panel with a solo POV profile. Without a console `/status` carries no track, so intercept it in Playwright before loading the panel:

```js
await page.route('**/status', async route => {
  const res = await route.fetch(); const d = await res.json();
  d.telemetry = Object.assign(d.telemetry || {visible: true}, {
    car: {id: 365, maker: "Alfa Romeo", name: "155 2.5 V6 TI '93", group: "Gr.4"},
    track: {id: "2066d9", track: "Nürburgring", layout: "Grand Prix", reverse: false}});
  await route.fulfill({response: res, json: d});
});
```

Check desktop and mobile width: `TRACK Nürburgring - Grand Prix` sits next to the car and long names end in an ellipsis. Repeat with `track: {candidates: ["2066d9", "0f1e2d"]}`: the strip shows `TRACK ?` with the ids as tooltip. Then refresh `src/docs/wiki/images/director-panel.png` with `wiki-screenshots` using the same interception, showing car and track.

- [ ] **Step 5: Commit**

```bash
git add src/relay/racecast-feeds.py src/racecast.py src/director/director-panel.html tools/build-binary.py tests/test_telemetry_endpoints.py tests/test_racecast.py src/docs/wiki/images/director-panel.png
git commit -m "feat(relay): load and refresh GT7 data, show the track in the Director Panel (#787)"
```

---

### Task 7: Export decides the track per session and projects the distance

**Files:**
- Modify: `src/scripts/gt7_recording.py`
- Modify: `src/racecast.py` (`telemetry_export_cmd`)
- Test: `tests/test_gt7_recording.py`, `tests/test_racecast.py`

**Interfaces:**
- Consumes: Task 3/4 `TrackDB.match`, `name`, `project`, `line_length`, `assignment`; part 1 `export_csv`.
- Produces:
  - `replay_laps(path) -> (header, laps, dropped)`: every lap record the engine closes (part 1 keys plus `points`, `distance_m`).
  - `session_tracks(laps, tracks, key=None) -> {session: {"id","track","layout","reverse"} | {"candidates": [...]} | None}`.
  - `LAP_COLUMNS` gains `"track", "layout"` at the end.
  - `export_csv(path, out_dir, include_all=False, excel=False, cars=None, tracks=None, key=None)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gt7_recording.py`. The fake `TrackDB` keeps the test independent of the real data:

```python
class _Tracks:
    """Recognises any lap longer than 300 m as layout 'aaa001', a 600 m straight line."""
    def __init__(self, assigned=None):
        self.assigned = assigned

    def match(self, points, length_m):
        return ({"id": "aaa001", "track": "Oval", "layout": "Full", "reverse": False,
                 "score_m": 1.0} if length_m > 300 else None)

    def name(self, oid):
        return {"id": oid, "track": "Assigned", "layout": "X", "reverse": False}

    def assignment(self, key):
        return self.assigned

    def line_length(self, oid):
        return 600.0

    def project(self, points, oid):
        return [x % 600.0 for x, _z in points]


def _xy_session(d):
    """Lap 1 of 10 s at 50 m/s along +x from x=590 (just behind the line), lap 2 starts."""
    items, t, x = [(1000.0, "A", _tpkt(0))], 1000.1, 590.0
    for _ in range(100):
        b = bytearray(_tpkt(1))
        struct.pack_into("<3f", b, tm.OFF_POS, x, 0.0, 0.0)
        items.append((t, "A", bytes(b)))
        t += 0.1
        x += 5.0
    items.append((t, "A", _tpkt(2)))
    return _write(d, items).path


def t_session_tracks_and_columns():
    with tempfile.TemporaryDirectory() as d:
        src = _xy_session(d)
        _h, laps, _dropped = rec.replay_laps(src)
        assert rec.session_tracks(laps, _Tracks()) == {1: {"id": "aaa001", "track": "Oval",
                                                         "layout": "Full", "reverse": False}}
        assert rec.session_tracks(laps, _Tracks(assigned="zzz"), key="p/s")[1]["track"] == \
            "Assigned", "a learned assignment wins"
        assert rec.session_tracks(laps, None) == {}
        rec.export_csv(src, os.path.join(d, "out"), tracks=_Tracks())
        lap1 = [r for r in _rows(os.path.join(d, "out", "laps.csv")) if r["lap"] == "1"][0]
        assert (lap1["track"], lap1["layout"]) == ("Oval", "Full")


def t_projected_distance_stays_continuous_across_the_line():
    with tempfile.TemporaryDirectory() as d:
        src = _xy_session(d)
        rec.export_csv(src, os.path.join(d, "out"), tracks=_Tracks())
        lap1 = [r for r in _rows(os.path.join(d, "out", "samples.csv")) if r["lap"] == "1"]
        first = float(lap1[0]["lap_dist_m"])
        assert first == -10.0, "590 m on a 600 m line right after the edge reads as -10 m"
        assert float(lap1[-1]["lap_dist_m"]) == 485.0
```

`tests/test_racecast.py`: extend `t_telemetry_export_writes_next_to_recording` (part 1) so the export call stays green with tracks wired in; add a check that `laps.csv` has the `track` column:

```python
        with open(os.path.join(out, "laps.csv"), encoding="utf-8") as fh:
            assert fh.readline().rstrip().endswith(",track,layout")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_gt7_recording.py; python3 tests/test_racecast.py`
Expected: FAIL (`AttributeError: ... 'replay_laps'`; header lacks `track`).

- [ ] **Step 3: Implement**

In `gt7_recording.py`, append `"track", "layout"` to `LAP_COLUMNS`, then add:

```python
def replay_laps(path):
    """(header, laps, dropped): every lap the engine closes in one recording."""
    r = Recording(path)
    eng = gt7_telemetry.TelemetryEngine()
    laps = []
    eng.on_lap = laps.append
    for wall_ts, _kind, plain in r.packets():
        eng.update(gt7_telemetry.parse_packet(plain), wall_ts)
    return r.header, laps, r.dropped


def _brief(found):
    return {k: found[k] for k in ("id", "track", "layout", "reverse")}


def session_tracks(laps, tracks, key=None):
    """The track of each GT7 session: a learned assignment, else the longest lap that matches."""
    if tracks is None:
        return {}
    assigned = tracks.assignment(key) if key else None
    out = {}
    for session in sorted({lap["session"] for lap in laps}):
        if assigned:
            info = tracks.name(assigned)
            out[session] = _brief(info) if info else None
            continue
        found = None
        ranked = sorted((lap for lap in laps if lap["session"] == session and lap["points"]),
                        key=lambda lap: lap["distance_m"], reverse=True)
        for lap in ranked:
            m = tracks.match(lap["points"], lap["distance_m"])
            if m and "id" in m:
                found = _brief(m)
                break
            found = found or m
        out[session] = found
    return out
```

Change `export_csv`'s signature to `export_csv(path, out_dir, include_all=False, excel=False, cars=None, tracks=None, key=None)` and, at its top after `cars = ...`:

```python
    _h, closed, _d = replay_laps(path) if tracks is not None else (None, [], 0)
    by_session = session_tracks(closed, tracks, key)
```

In the sample loop, replace the `num(eng.lap_distance(), 1)` argument with `num(_lap_dist(eng, pkt, by_session, tracks), 1)` and add the helper:

```python
def _lap_dist(eng, pkt, by_session, tracks):
    """Distance along the racing line when the session's track is known, else integrated."""
    dist = eng.lap_distance()
    found = by_session.get(eng.session)
    if not found or "id" not in found or pkt.pos_x is None:
        return dist
    s = tracks.project([(pkt.pos_x, pkt.pos_z)], found["id"])
    length = tracks.line_length(found["id"])
    if not s or not length:
        return dist
    ref = dist or 0.0
    return min((s[0], s[0] - length, s[0] + length), key=lambda v: abs(v - ref))
```

In the laps writer loop, compute the session's track first and append two cells to the row:

```python
        for lap in laps:
            found = by_session.get(lap["session"])
            known = bool(found and "id" in found)
            w.writerow([
                ...the part 1 cells unchanged...,
                _car_name(cars, lap["car_id"]),
                found["track"] if known else "", found["layout"] if known else ""])
```

In `src/racecast.py` `telemetry_export_cmd`, build the track database and key and pass them:

```python
    import gt7_data
    import gt7_tracks
    base = _runtime_base_dir()
    bundled = resource_path("assets/gt7")
    key = f"{_active_profile_name()}/{gr.recording_stem(path)}"
    res = gr.export_csv(path, out_dir, include_all=args.all, excel=args.excel,
                        cars=gt7_cars.CarDB(gt7_data.cars_dir(base, bundled)),
                        tracks=gt7_tracks.TrackDB.load(base, bundled), key=key)
```

(replacing the part 1 call, still inside its `try`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_gt7_recording.py && python3 tests/test_racecast.py && python3 tools/lint.py`
Expected: ALL PASS, lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/scripts/gt7_recording.py src/racecast.py tests/test_gt7_recording.py tests/test_racecast.py
git commit -m "feat(telemetry): track per session and racing-line distance in the CSV export (#787)"
```

---

### Task 8: `racecast gt7-data` and the Control Center Settings row

**Files:**
- Modify: `src/racecast.py` (USAGE, verb tuple, `route()`, dispatch table, command and data functions, `ctx` dict near line 7051)
- Modify: `src/ui/ui_server.py` (GET near line 469, POST near line 860)
- Modify: `src/ui/control-center.html` (Settings view after the Cookies section ~line 1000; `loadSettings` ~line 2537)
- Test: `tests/test_racecast.py`, `tests/test_ui_server.py`

**Interfaces:**
- Consumes: Task 1 `update`, `status`.
- Produces: `GT7DATA_VERBS = ("update", "status")`; `gt7_data_update_cmd(rest)`, `gt7_data_status_cmd(rest)`; `gt7_data_status_data() -> {"ok": True, "checked", "files"} | {"ok": False, "error"}`; `gt7_data_update_data() -> {"ok": bool, "changed", "files"}`; ctx keys `"gt7_data_status"`, `"gt7_data_update"`; routes `GET /api/gt7-data`, `POST /api/gt7-data/update`.

- [ ] **Step 1: Write the failing tests**

`tests/test_racecast.py`:

```python
def t_route_gt7_data_verbs():
    for verb in ("update", "status"):
        assert m.route(["gt7-data", verb]) == \
            {"kind": "service", "command": "gt7-data", "verb": verb, "rest": []}
    _raises(lambda: m.route(["gt7-data"]))


def t_gt7_data_update_data_forces_and_never_raises():
    real = m._gt7_data_module
    seen = []

    class Fake:
        @staticmethod
        def update(base, force=False):
            seen.append(force)
            return {"checked": True, "changed": True, "files": {"cars.csv": "updated"}}

        @staticmethod
        def status(base, bundled=None):
            raise RuntimeError("disk")

    m._gt7_data_module = lambda: Fake
    try:
        assert m.gt7_data_update_data()["ok"] is True and seen == [True]
        assert m.gt7_data_status_data() == {"ok": False, "error": "could not read GT7 data: disk"}
    finally:
        m._gt7_data_module = real
```

`tests/test_ui_server.py`: add the two ctx keys to `_ctx` (line ~198 lists defaults):

```python
            "gt7_data_status": lambda: {"ok": True, "checked": None, "files": {}},
            "gt7_data_update": lambda: {"ok": True, "changed": False, "files": {}},
```

and a route test modelled on `t_fonts_restore_route_passes_only_a_literal_true_force` (line ~1586), reusing its server helpers (`_post_json`, the GET helper used nearby):

```python
def t_gt7_data_routes():
    ctx = _ctx()
    ctx["gt7_data_update"] = lambda: {"ok": True, "changed": True, "files": {"cars.csv": "updated"}}
    srv, port = _serve(ctx)
    try:
        code, body = _get_json(port, "/api/gt7-data")
        assert code == 200 and body["ok"] is True
        code, body = _post_json(port, "/api/gt7-data/update", {})
        assert code == 200 and body["files"] == {"cars.csv": "updated"}
    finally:
        srv.shutdown()
```

Use the exact server/GET helper names the file already has (`grep -n "def _serve\|def _get_json\|def _post_json" tests/test_ui_server.py`) and adapt the call shapes to them.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_racecast.py; python3 tests/test_ui_server.py`
Expected: FAIL (`ValueError` on route, missing functions, 404 on the routes).

- [ ] **Step 3: Implement**

`src/racecast.py`:

USAGE, after the `racecast telemetry` lines:

```
  racecast gt7-data  update [--force] | status   # GT7 car names + track recognition data (checked daily by the relay)
```

```python
GT7DATA_VERBS = ("update", "status")      # GT7 reference data (#787)
```

`route()`, after the `telemetry` branch:

```python
    if cmd == "gt7-data":
        verb = rest[0] if rest else None
        if verb not in GT7DATA_VERBS:
            raise ValueError(f"usage: racecast gt7-data {{{'|'.join(GT7DATA_VERBS)}}}")
        return {"kind": "service", "command": "gt7-data", "verb": verb, "rest": rest[1:]}
```

Dispatch table: `("gt7-data", "update"): gt7_data_update_cmd, ("gt7-data", "status"): gt7_data_status_cmd,`.

Functions (after the telemetry commands):

```python
def _gt7_data_module():
    import gt7_data
    return gt7_data


def gt7_data_update_data():
    """Force a GT7 data update for the Control Center; never raises."""
    try:
        res = _gt7_data_module().update(_runtime_base_dir(), force=True)
    except Exception as exc:
        return {"ok": False, "error": f"could not update GT7 data: {exc}"}
    ok = any(not str(v).startswith("error") for v in res["files"].values())
    return {"ok": ok, "changed": res["changed"], "files": res["files"]}


def gt7_data_status_data():
    try:
        st = _gt7_data_module().status(_runtime_base_dir(), resource_path("assets/gt7"))
    except Exception as exc:
        return {"ok": False, "error": f"could not read GT7 data: {exc}"}
    return {"ok": True, "checked": st["checked"], "files": st["files"]}


def gt7_data_update_cmd(rest):
    """Fetch the latest GT7 car and track data now."""
    if rest not in ([], ["--force"]):
        sys.exit("usage: racecast gt7-data update [--force]")
    res = _gt7_data_module().update(_runtime_base_dir(), force=True)
    for name, state in sorted(res["files"].items()):
        print(f"{name}: {state}")
    if not any(not str(v).startswith("error") for v in res["files"].values()):
        sys.exit("GT7 data update failed (offline?)")


def gt7_data_status_cmd(_rest):
    """Show where each GT7 data file comes from and how old it is."""
    st = gt7_data_status_data()
    if not st["ok"]:
        sys.exit(st["error"])
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(st["checked"])) if st["checked"] else "never"
    print(f"last check: {when}")
    for name, f in sorted(st["files"].items()):
        print(f"{name}: {f['source']}, {f['rows'] if f['rows'] is not None else '?'} rows")
```

`gt7-data update` always forces: an operator who types it wants a check now; `--force` is accepted for symmetry with the spec and has no extra effect. Mention that in the help line only if a reviewer asks; do not add a comment.

ctx dict (line ~7051): `"gt7_data_status": gt7_data_status_data, "gt7_data_update": gt7_data_update_data,`.

`src/ui/ui_server.py`: in the GET block next to `/api/profile/env` (line ~469):

```python
            if path == "/api/gt7-data":
                return self._json(ctx["gt7_data_status"]())
```

In the POST block next to `/api/fonts/restore` (line ~860):

```python
            if path == "/api/gt7-data/update":
                try:
                    result = ctx["gt7_data_update"]()
                except Exception as exc:
                    return self._json({"ok": False, "error": f"could not update GT7 data: {exc}"},
                                      code=500)
                return self._json(result, code=200 if result.get("ok") else 502)
```

`src/ui/control-center.html`, after the Cookies `</section>`:

```html
        <div class="viewhead"><h3>GT7 data</h3><span class="sub">car names and track recognition for solo POV telemetry. Shared across all leagues</span></div>
        <section>
          <div class="row"><span class="name">GT7 data</span>
            <span class="dim grow" id="d-gt7data">…</span>
            <button id="gt7data-update" onclick="updateGt7Data()" title="Fetch the latest car and track data now">
              <svg viewBox="0 0 24 24"><path d="M23 4v6h-6"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>Update now</button></div>
        </section>
```

JS, next to `restoreBundledFonts`:

```js
function fmtAgeS(ts) {
  if (!ts) return 'never';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 3600) return Math.round(s / 60) + ' min ago';
  if (s < 86400) return Math.round(s / 3600) + ' h ago';
  return Math.round(s / 86400) + ' days ago';
}

async function loadGt7Data() {
  let d;
  try { d = await (await fetch('/api/gt7-data', {cache: 'no-store'})).json(); }
  catch (e) { $('d-gt7data').textContent = 'Control Center not reachable.'; return; }
  if (!d.ok) { $('d-gt7data').textContent = d.error || 'unavailable'; return; }
  const f = d.files || {};
  const rows = n => (f[n] && f[n].rows != null) ? f[n].rows : '?';
  const src = Object.values(f).some(x => x.source === 'runtime') ? 'updated' : 'bundled';
  $('d-gt7data').textContent = 'checked ' + fmtAgeS(d.checked) + ' · ' + rows('cars.csv') +
      ' cars · ' + rows('index.json') + ' layouts · ' + rows('signatures.json') +
      ' recognisable · ' + src;
}

async function updateGt7Data() {
  const b = $('gt7data-update'); b.disabled = true;
  let d;
  try {
    d = await (await fetch('/api/gt7-data/update', {method: 'POST',
        headers: {'Content-Type': 'application/json'}, body: '{}'})).json();
  } catch (e) { d = {ok: false, error: 'Control Center not reachable.'}; }
  b.disabled = false;
  const errs = Object.entries(d.files || {}).filter(([, v]) => String(v).startsWith('error'));
  if (!d.ok || errs.length) {
    await alertModal((d.error ? d.error + '\n' : '') +
        errs.map(([k, v]) => k + ': ' + v).join('\n'), {title: 'GT7 data'});
  }
  loadGt7Data();
}
```

Call `loadGt7Data();` at the end of `loadSettings` (line ~2537). Before adding `fmtAgeS`, check `grep -n "ago'" src/ui/control-center.html`: if a relative-age helper exists, use it instead and drop `fmtAgeS`.

- [ ] **Step 4: Run tests and verify visually**

Run: `python3 tests/test_racecast.py && python3 tests/test_ui_server.py && python3 tools/lint.py && python3 src/racecast.py gt7-data --help`
Expected: ALL PASS, lint clean, ASCII help.

Invoke `ui-visual-verification` for the Settings view (desktop and narrow width), then `wiki-screenshots` to refresh `src/docs/wiki/images/cc-settings.png` from a local dev build (`racecast ui` from `src/`). If the Settings screenshot does not exist under that name, use the file the skill lists for the Settings view.

- [ ] **Step 5: Commit**

```bash
git add src/racecast.py src/ui/ui_server.py src/ui/control-center.html tests/test_racecast.py tests/test_ui_server.py src/docs/wiki/images/
git commit -m "feat(cli,ui): racecast gt7-data and a GT7 data row in Settings (#787)"
```

---

### Task 9: Docs and final gates

**Files:**
- Modify: `src/relay/CLAUDE.md` (telemetry recording paragraph from part 1)
- Modify: `src/docs/wiki/Relay-Mode.md` (telemetry recording paragraph from part 1), `src/docs/wiki/Director.md` (status strip), `src/docs/wiki/Control-Center.md` (Settings view)

- [ ] **Step 1: Relay CLAUDE.md**

Append to the part 1 telemetry recording paragraph:

```markdown
**GT7 data and track recognition (#787).** `gt7_data` resolves each reference file (car tables from gt7info, `index.json`/`signatures.json` from gt7-datalogger-track-data) from `runtime/gt7/` when that copy validates, else from `src/assets/gt7/`; a relay start with telemetry runs `gt7_data.update` once per 24 h in a thread and swaps the new `CarDB`/`TrackDB` into the store (`RACECAST_GT7_DATA_UPDATE=0` turns it off). The lap accumulator keeps `(x, z)` every 20 m; after each closed lap `TrackDB.match` checks length (3 %), box, mean distance to the racing line (15 m) and driving direction, so a reverse layout is told apart from its forward twin. `/status` `telemetry.track` reports it; a session boundary clears it. The exporter decides the track per GT7 session and projects `lap_dist_m` onto the line. Learned signatures and recording assignments live in `runtime/gt7/learned-tracks.json`, which no update touches. Tests: `tests/test_gt7_data.py`, `tests/test_gt7_tracks.py`.
```

- [ ] **Step 2: Wiki**

`Relay-Mode.md`, after the part 1 recording paragraphs:

```markdown
**Track recognition (solo POV).** After the first full lap the relay names the track and
layout, including reverse layouts, from the car's positions, and the Director Panel shows
it next to the car. It recognises the layouts that the community dataset
[gt7-datalogger-track-data](https://github.com/jbhoorasingh/gt7-datalogger-track-data)
has a racing line for (78 of 121 when this was written). The CSV export adds `track` and
`layout` to `laps.csv` and measures `lap_dist_m` along the racing line, so laps line up
corner for corner. A track it cannot name can be taught once in the Control Center's
Telemetry view.

The relay refreshes the car names and track data once a day when it starts with
telemetry. `racecast gt7-data update` or **Settings -> GT7 data -> Update now** does it
on demand; `racecast gt7-data status` shows what is in use.
```

`Director.md`, next to the part 1 `REC` sentence: `` `TRACK` in the status strip names the recognised track and layout after the first full lap; `?` means several layouts fit, hover for the candidates. ``

`Control-Center.md`, in the General Settings section: `**GT7 data** shows when the car and track data were last checked and how many cars and layouts it knows; **Update now** fetches the latest copy.`

- [ ] **Step 3: Final gates**

```bash
python3 tools/lint.py
python3 tools/run-tests.py
python3 tools/build.py
git fetch origin && git rebase origin/main
python3 tools/run-tests.py
```

Expected: all green. `tools/build.py` verify must still pass with the new files in `src/assets/gt7/`.

- [ ] **Step 4: Commit and PR**

```bash
git add src/relay/CLAUDE.md src/docs/wiki/Relay-Mode.md src/docs/wiki/Director.md src/docs/wiki/Control-Center.md
git commit -m "docs: GT7 data updates and track recognition (#787)"
```

Open the PR with the `ship-feature` skill: body `Closes #787` and `Part of #785`.

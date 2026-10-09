#!/usr/bin/env python3
"""GT7 reference data: car tables plus the track catalogue and signatures.

The bundled copy in src/assets/gt7/ ships with racecast. update() fetches newer
copies into <runtime>/gt7/, and resolve() prefers a runtime copy once it validates.
RUNTIME_ONLY files never ship and exist only after a successful update().
"""
import csv
import hashlib
import io
import json
import os
import tempfile
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
RUNTIME_ONLY = ("signatures.json",)  # upstream licenses only index.json for redistribution


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
    """A dict with well-typed `checked`/`sha256`/`updated`; a corrupt or wrong-typed
    stamp file degrades to an empty one instead of raising into update()."""
    try:
        with open(os.path.join(d, STAMP), encoding="utf-8") as fh:
            stamp = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(stamp, dict):
        return {}
    if not isinstance(stamp.get("checked"), (int, float)):
        stamp.pop("checked", None)
    if not isinstance(stamp.get("sha256"), dict):
        stamp.pop("sha256", None)
    if not isinstance(stamp.get("updated"), dict):
        stamp.pop("updated", None)
    return stamp


def write_file(d, name, data):
    """Replace d/name atomically through a unique temp file, removed on failure."""
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, os.path.join(d, name))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass  # already gone
        raise


def error_reason(e):
    """A per-file error text without the machine path an OSError carries: the OS
    message, our own validation text, else the exception's type name."""
    if isinstance(e, http_util.HTTPError):
        return f"HTTP {e.code}"
    if isinstance(getattr(e, "reason", None), OSError):
        e = e.reason              # urllib wraps the socket error
    if isinstance(e, OSError):
        return e.strerror or type(e).__name__
    if isinstance(e, ValueError):
        return str(e)
    return type(e).__name__


def update(runtime_base, force=False, fetch=None, now=None):
    """Fetch every source, keep only validated files; at most once per 24 h unless forced."""
    fetch = fetch or (lambda url: http_util.get_bytes(url, timeout=20))
    now = time.time() if now is None else now
    d = data_dir(runtime_base)
    stamp = _read_stamp(d)
    checked_before = stamp.get("checked")
    complete = all(os.path.exists(os.path.join(d, n)) for n in RUNTIME_ONLY)
    if (not force and complete and checked_before is not None
            and now - float(checked_before) < UPDATE_EVERY_S):
        return {"checked": False, "changed": False, "files": {}}
    shas = dict(stamp.get("sha256") or {})
    times = dict(stamp.get("updated") or {})
    files = {}
    for name, url in SOURCES.items():
        try:
            data = fetch(url)
            if isinstance(data, str):
                data = data.encode("utf-8")
            validate(name, data)
            sha = hashlib.sha256(data).hexdigest()
            if shas.get(name) == sha and os.path.exists(os.path.join(d, name)):
                files[name] = "unchanged"
                continue
            write_file(d, name, data)
            shas[name], times[name] = sha, now
            files[name] = "updated"
        except Exception as e:  # noqa: BLE001  offline, a bad download or an unexpected
            files[name] = f"error: {error_reason(e)}"  # fetch/validate/write failure keeps the old file
    fetched = any(not v.startswith("error") for v in files.values())
    if fetched:
        try:
            write_file(d, STAMP, json.dumps({"checked": now, "sha256": shas,
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
        source = "runtime" if runtime else "bundled" if os.path.exists(path) else "missing"
        out[name] = {"source": source,
                     "updated": (stamp.get("updated") or {}).get(name) if runtime else None,
                     "rows": rows}
    return {"checked": stamp.get("checked"), "files": out}


def _stat_entry(path):
    try:
        st = os.stat(path)
        return f"{path}|{st.st_mtime_ns}|{st.st_size};"
    except OSError:
        return f"{path}|-;"


def _stat_hash(paths):
    h = hashlib.sha1()
    for p in paths:
        h.update(_stat_entry(p).encode("utf-8"))
    return h.hexdigest()[:16]


_CONTENT = {}


def _bundled_entry(name, path):
    """A bundled file by name and content: a onefile binary unpacks it to a new temp dir
    on every launch, so its path and mtime change while the content does not."""
    try:
        st = os.stat(path)
        key = (path, st.st_size, st.st_mtime_ns)
        if key not in _CONTENT:
            with open(path, "rb") as fh:
                _CONTENT[key] = hashlib.sha1(fh.read()).hexdigest()
        return f"bundled/{name}|{_CONTENT[key]};"
    except OSError:
        return f"bundled/{name}|-;"


def _data_files(runtime_base, bundled):
    """(name, path) of every reference file a CarDB or TrackDB loads."""
    cars = cars_dir(runtime_base, bundled)
    files = [(n, resolve(n, runtime_base, bundled)) for n in ("index.json", "signatures.json")]
    return files + [(n, os.path.join(cars, n)) for n in sorted(CAR_TABLES)]


def data_version(runtime_base, bundled=None):
    """Changes whenever the track data, the car tables or the learned tracks change
    (lap-index caches key on it)."""
    h = hashlib.sha1()
    runtime = data_dir(runtime_base) if runtime_base else None
    for name, path in _data_files(runtime_base, bundled):
        if runtime and path == os.path.join(runtime, name):
            h.update(_stat_entry(path).encode("utf-8"))
        else:
            h.update(_bundled_entry(name, path).encode("utf-8"))
    if runtime_base:
        h.update(_stat_entry(learned_path(runtime_base)).encode("utf-8"))
    return h.hexdigest()[:16]


def fingerprint(runtime_base, bundled=None):
    """Changes whenever any file a CarDB or TrackDB would load changes; stat calls only,
    except that a changed runtime file is validated once."""
    paths = [path for _name, path in _data_files(runtime_base, bundled)]
    if runtime_base:
        paths.append(learned_path(runtime_base))
    return _stat_hash(paths)

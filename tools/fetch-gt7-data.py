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

#!/usr/bin/env python3
"""Refresh the vendored GT7 car tables (src/assets/gt7/) from ddm999/gt7info.

The relay names the car in each telemetry packet from these tables (#713). A GT7
update adds cars; until the tables are refreshed such a car shows as "Car #<id>".
Downloads cars.csv, maker.csv and cargrp.csv, checks each one has the expected
columns and parses, and only then replaces the local copy. Prints the car ids
added and removed.

Usage:
  python3 tools/fetch-gt7-cars.py              # refresh the tables
  python3 tools/fetch-gt7-cars.py --dry-run    # show the changes, write nothing

Source: https://github.com/ddm999/gt7info (_data/db/, licence MIT-0).
Maintainer tool, not shipped in the distributable package.
"""
import argparse, csv, io, os, sys
from urllib.request import Request, urlopen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GT7_DIR = os.path.join(ROOT, "src", "assets", "gt7")
BASE_URL = "https://raw.githubusercontent.com/ddm999/gt7info/web-new/_data/db/"
# Each table and the columns gt7_cars.CarDB reads from it.
TABLES = {
    "cars.csv": ("ID", "ShortName", "Maker"),
    "maker.csv": ("ID", "Name"),
    "cargrp.csv": ("ID", "Group"),
}
MIN_CARS = 400        # far fewer rows means a truncated or wrong download


def _get(url, timeout=20):
    return urlopen(Request(url, headers={"User-Agent": "racecast-feeds/1.0"}),
                   timeout=timeout).read()


def validate(name, text):
    """Raise ValueError unless `text` is a usable copy of table `name`. Returns rows."""
    rows = list(csv.DictReader(io.StringIO(text)))
    missing = [c for c in TABLES[name] if not rows or c not in rows[0]]
    if missing:
        raise ValueError(f"{name}: missing column(s) {', '.join(missing)}")
    if name == "cars.csv" and len(rows) < MIN_CARS:
        raise ValueError(f"{name}: only {len(rows)} rows, expected at least {MIN_CARS}")
    return rows


def _ids(rows):
    return {r["ID"] for r in rows}


def main():
    ap = argparse.ArgumentParser(description="Refresh the vendored GT7 car tables.")
    ap.add_argument("--dry-run", action="store_true", help="show the changes, write nothing")
    args = ap.parse_args()
    fetched = {}
    for name in TABLES:
        text = _get(BASE_URL + name).decode("utf-8")
        validate(name, text)
        fetched[name] = text
    old_path = os.path.join(GT7_DIR, "cars.csv")
    old = set()
    if os.path.exists(old_path):
        with open(old_path, encoding="utf-8", newline="") as f:
            old = _ids(csv.DictReader(f))
    new_rows = validate("cars.csv", fetched["cars.csv"])
    names = {r["ID"]: r["ShortName"] for r in new_rows}
    added, removed = sorted(_ids(new_rows) - old, key=int), sorted(old - _ids(new_rows), key=int)
    for car_id in added:
        print(f"+ {car_id} {names[car_id]}")
    for car_id in removed:
        print(f"- {car_id}")
    print(f"{len(new_rows)} cars ({len(added)} added, {len(removed)} removed)")
    if args.dry_run:
        return 0
    os.makedirs(GT7_DIR, exist_ok=True)
    for name, text in fetched.items():
        tmp = os.path.join(GT7_DIR, name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, os.path.join(GT7_DIR, name))
    print("updated", GT7_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())

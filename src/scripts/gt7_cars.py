#!/usr/bin/env python3
"""GT7 car id -> readable car, for the telemetry HUD and the Director Panel (#713).

Every GT7 telemetry packet carries the car id (gt7_telemetry.OFF_CAR_ID). The tables
that name it come from the community database ddm999/gt7info (MIT-0), vendored under
src/assets/gt7/ and refreshed with tools/fetch-gt7-data.py. Stdlib only; loading
never raises, so a missing or broken table only degrades the name to "Car #<id>".
"""
import csv
import logging
import os

LOG = logging.getLogger("racecast.relay.telemetry")

# gt7info's car group column: race categories plus N (road cars).
GROUP_LABELS = {"1": "Gr.1", "2": "Gr.2", "3": "Gr.3", "4": "Gr.4",
                "B": "Gr.B", "X": "Gr.X", "N": "N"}


def default_dir():
    """src/assets/gt7 next to this file's src/scripts, in the repo and the package."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "assets", "gt7")


def _rows(directory, name):
    try:
        with open(os.path.join(directory, name), encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except (OSError, csv.Error, UnicodeDecodeError) as e:
        LOG.warning("GT7 car table %s unreadable (%s), car names degrade to ids", name, e)
        return []


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class CarDB:
    """Immutable lookup built once at relay start."""

    def __init__(self, directory=None):
        directory = directory or default_dir()
        makers = {_int(r.get("ID")): (r.get("Name") or "").strip()
                  for r in _rows(directory, "maker.csv")}
        groups = {_int(r.get("ID")): (r.get("Group") or "").strip()
                  for r in _rows(directory, "cargrp.csv")}
        self._cars = {}
        for r in _rows(directory, "cars.csv"):
            car_id = _int(r.get("ID"))
            if car_id is None:
                continue
            self._cars[car_id] = {
                "id": car_id,
                "maker": makers.get(_int(r.get("Maker"))) or None,
                "name": (r.get("ShortName") or "").strip() or f"Car #{car_id}",
                "group": GROUP_LABELS.get(groups.get(car_id)),
            }

    def __len__(self):
        return len(self._cars)

    def lookup(self, car_id):
        """The car as {id, maker, name, group}; None when the packet names no car."""
        if car_id is None or car_id <= 0:
            return None
        car = self._cars.get(car_id)
        if car is None:
            return {"id": car_id, "maker": None, "name": f"Car #{car_id}", "group": None}
        return dict(car)

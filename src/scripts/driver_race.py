"""The relay's live race from driver telemetry (#1112): settings, the picked layout's
line and one `race_distance.Race`, behind a lock.

Event settings live in `runtime/<profile>/driver-race.json`: `formation` (default
on), `finish_mode` (`laps` or `time`) and `lap_count`. In endurance the lap count
applies to the final stint lobby only; the relay passes it with the stint switch
that puts the last schedule row on air. A changed layout pick starts a new race,
because distances along another line mean nothing. Stdlib only.
"""
import json
import os
import tempfile
import threading
import time

import atomic_replace
import race_distance

FILENAME = "driver-race.json"
DEFAULTS = {"formation": True, "finish_mode": "laps", "lap_count": None}
MAX_LAPS = 999
LINE_RETRY_S = 10.0       # a pick without a downloaded line is looked up again this often


def path_in(runtime_dir):
    return os.path.join(runtime_dir, FILENAME)


def read_settings(path):
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        doc = {}
    doc = doc if isinstance(doc, dict) else {}
    out = dict(DEFAULTS)
    try:
        out.update(validate(doc))
    except ValueError:
        pass  # a hand-edited bad file keeps the defaults
    return out


def validate(changes):
    """The recognised, checked settings of `changes`; ValueError on a bad value."""
    out = {}
    if "formation" in changes:
        if not isinstance(changes["formation"], bool):
            raise ValueError("formation is true or false")
        out["formation"] = changes["formation"]
    if "finish_mode" in changes:
        if changes["finish_mode"] not in ("laps", "time"):
            raise ValueError("finish_mode is laps or time")
        out["finish_mode"] = changes["finish_mode"]
    if "lap_count" in changes:
        n = changes["lap_count"]
        if n is not None and (type(n) is not int or not 1 <= n <= MAX_LAPS):
            raise ValueError(f"lap_count is 1 to {MAX_LAPS}, or none")
        out["lap_count"] = n
    return out


def write_settings(path, changes):
    current = read_settings(path)
    current.update(validate(changes))
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".driver-race-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(current, fh)
        atomic_replace.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return current


class Service:
    """Thread-safe holder of the live race."""

    def __init__(self, settings_path, pick, trace_for, multi_lobby, clock=time.monotonic,
                 enabled=lambda: True):
        """`enabled()` False (the relay in qualifying mode) ignores samples."""
        self._path = settings_path
        self._pick, self._trace_for = pick, trace_for
        self._multi_lobby = multi_lobby
        self._clock = clock
        self._enabled = enabled
        self._lock = threading.Lock()
        self._race = None
        self._race_pick = object()
        self._line_tried = None
        self._final_lobby_laps = None

    def _current(self):
        pick = self._pick()
        pick_id = pick["id"] if pick else None
        if self._race is None or pick_id != self._race_pick:
            self._race = self._build(pick_id)
        elif pick_id and self._race.length is None \
                and self._clock() - self._line_tried >= LINE_RETRY_S:
            self._race = self._build(pick_id)     # the line may have been downloaded since
        return self._race

    def _build(self, pick_id):
        s = read_settings(self._path)
        multi = bool(self._multi_lobby())
        self._race_pick = pick_id
        self._line_tried = self._clock()
        trace = self._trace_for(pick_id) if pick_id else None
        race = race_distance.Race(
            trace, formation=s["formation"], multi_lobby=multi, finish_mode=s["finish_mode"],
            lap_count=None if multi else s["lap_count"])
        if multi and self._final_lobby_laps is not None:
            race.final_lobby(self._final_lobby_laps, self._clock())
        return race

    def update(self, reg, sample):
        if not self._enabled():
            return
        with self._lock:
            self._current().update(reg["entry"], reg["conn"], reg["name"],
                                   dict(sample, t=sample.get("mono", self._clock())))

    def standings(self):
        with self._lock:
            race = self._current()
            out = race.standings(self._clock())
            pick_id = self._race_pick
        out.update(settings=read_settings(self._path), track=pick_id)
        return out

    def set_settings(self, changes):
        """Persist and apply; formation only matters before a car starts counting."""
        saved = write_settings(self._path, changes)
        with self._lock:
            race = self._current()
            race.formation = saved["formation"]
            race.finish_mode = saved["finish_mode"]
            if not race.multi_lobby:
                race.lap_count = saved["lap_count"]
        return saved

    def stint_switch(self, final_laps=None, window=True):
        """The broadcast switched stints: open the change window (unless `window` is
        False, a relay start that finds the last row on air); `final_laps` is the lap
        count of the final stint lobby when the last row is on air."""
        with self._lock:
            race = self._current()
            if window:
                race.open_change_window(self._clock())
            if final_laps is not None and race.multi_lobby:
                self._final_lobby_laps = final_laps
                race.final_lobby(final_laps, self._clock())

    def final_lap(self):
        with self._lock:
            self._current().final_lap(self._clock())

    def finish(self):
        with self._lock:
            self._current().finish(self._clock())

    def reset(self):
        with self._lock:
            self._final_lobby_laps = None
            self._race = self._build(self._pick()["id"] if self._pick() else None)

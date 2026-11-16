#!/usr/bin/env python3
"""The GT7 layout the producer picks for an event (#1129).

Stdlib only, no relay imports: the relay, the CLI and the Control Center share it.
The pick lives in runtime/<profile>/event-track.json as {"id": "<official_id>"} and
stays until it is changed or cleared.
"""
import json
import os
import tempfile
import threading

import atomic_replace

FILENAME = "event-track.json"
NO_LINE = ("has no downloaded racing line. Without it there is no map and no "
           "recognition cross-check until a recording teaches the line with Set track. "
           "`racecast gt7-data update` downloads the lines; a few layouts have none upstream.")


def path_in(runtime_dir):
    return os.path.join(runtime_dir, FILENAME)


def read_id(path):
    """The stored layout id, or None for no pick or an unreadable file."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    value = doc.get("id") if isinstance(doc, dict) else None
    return value if isinstance(value, str) and value else None


def write_id(path, official_id):
    """Store `official_id`, or remove the pick with None."""
    if official_id is None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass  # no pick stored
        return
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=FILENAME + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"id": official_id}, fh)
        atomic_replace.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass  # already gone
        raise


def describe(tracks, official_id):
    """{id, track, layout, reverse, line, warning} for a known layout, else None."""
    if not official_id or tracks is None:
        return None
    info = tracks.name(official_id)
    if info is None:
        return None
    line = bool(tracks.has_downloaded_line(official_id))
    return {"id": info["id"], "track": info["track"], "layout": info["layout"],
            "reverse": bool(info["reverse"]), "line": line,
            "warning": None if line else f"{label(info)} {NO_LINE}"}


def label(info):
    text = " ".join(part for part in (info.get("track"), info.get("layout")) if part)
    return text + (" (reverse)" if info.get("reverse") else "")


def layouts(tracks):
    """Every catalogue layout with whether it has a downloaded line."""
    if tracks is None:
        return []
    return [{"id": t["id"], "track": t["track"], "layout": t["layout"],
             "reverse": bool(t["reverse"]), "line": bool(tracks.has_downloaded_line(t["id"]))}
            for t in tracks.layouts()]


class Store:
    """The relay's pick: validated against the current TrackDB, persisted on change."""

    def __init__(self, path, tracks=None):
        self.path = path
        self.tracks = tracks
        self._lock = threading.Lock()
        self._id = read_id(path)

    def current(self):
        """The described pick, or None. A stored id the catalogue does not know is no pick."""
        with self._lock:
            return describe(self.tracks, self._id)

    def set(self, official_id):
        """Pick a known layout, or clear with None/"". Returns the described pick."""
        official_id = official_id or None
        with self._lock:
            if official_id is not None and describe(self.tracks, official_id) is None:
                raise ValueError("unknown track layout")
            write_id(self.path, official_id)
            self._id = official_id
            return describe(self.tracks, official_id)


def recording_key(profile, filename):
    """The learned-assignment key "<profile>/<stem>" of a recording file, or None."""
    if not profile or not filename:
        return None
    name = os.path.basename(filename)
    for suffix in (".part", ".gt7rec"):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    return f"{profile}/{name}"


class Controller:
    """Applies the pick to the live telemetry and assigns the open recording to it.
    `telemetry` is a gt7_telemetry.TelemetryStore or None; `recording` returns the
    open recording's key or None."""

    def __init__(self, store, telemetry=None, recording=None, log=None):
        self.store = store
        self.telemetry = telemetry
        self.recording = recording or (lambda: None)
        self.log = log

    def apply(self):
        info = self.store.current()
        if self.telemetry is not None:
            self.telemetry.set_track_pick(info)
        self.assign(self.recording())
        return info

    def set(self, official_id):
        self.store.set(official_id)
        self.apply()
        return self.status()

    def assign(self, key):
        """Assign recording `key` to the current pick, or drop its assignment without
        one, so recognition names its tracks again; never raises."""
        tracks = self.store.tracks
        if not key or tracks is None or not hasattr(tracks, "assign"):
            return
        info = self.store.current()
        try:
            tracks.assign(key, info["id"] if info else None)
        except (OSError, ValueError) as exc:
            if self.log is not None:
                self.log.warning("could not assign recording %s to the picked layout: %s",
                                 key, exc)

    def status(self, with_layouts=False):
        """{pick, recognised, mismatch[, layouts]} for /status, the desks and the CLI."""
        live = None
        if self.telemetry is not None:
            try:
                live = self.telemetry.track_status()
            except Exception:  # noqa: BLE001  status must not fail on a telemetry fault
                live = None
        out = {"pick": self.store.current(),
               "recognised": live["recognised"] if live else None,
               "mismatch": bool(live and live["mismatch"])}
        if with_layouts:
            out["layouts"] = layouts(self.store.tracks)
        return out

    def mismatch_text(self):
        """The health line while recognition contradicts the pick, else None."""
        st = self.status()
        if not st["mismatch"] or st["pick"] is None or st["recognised"] is None:
            return None
        return (f"GT7 track: the lap matches {label(st['recognised'])}, "
                f"not the picked {label(st['pick'])}. Change the layout if the pick is wrong")

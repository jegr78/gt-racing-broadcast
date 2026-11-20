"""The relay's live race from driver telemetry (#1112): settings, the picked layout's
line and one `race_distance.Race`, behind a lock.

Event settings live in `runtime/<profile>/driver-race.json`: `formation` (default
on), `finish_mode` (`laps` or `time`) and `lap_count`. In endurance the lap count
applies to the final stint lobby only; the relay passes it with the stint switch
that puts the last schedule row on air. A changed layout pick starts a new race,
because distances along another line mean nothing.

Race Control actions (#1113): red flag, restart mode and release, rejoin marks,
corrections, change overrides, estimates for quiet cars (in memory only),
retirements, driver names for entries without a sender and class lap counts.
Corrections, rejoin marks, retirements, driver names and class lap counts persist in
`driver-race-actions.json` next to the settings and come back after a relay
restart; every action appends a line with identity and time to
`driver-race-audit.jsonl`. The red flag restart default is a league rule and lives
in the profile (`profiles/<name>/driver-race.json`, `restart`: `keep` or
`neutralise`; `last_lap_s`: how long the OBS tower shows a new lap), so it travels
with a profile export. Stdlib only.
"""
import collections
import json
import logging
import math
import os
import re
import sqlite3
import tempfile
import threading
import time

import atomic_replace
import driver_invite
import race_distance
import race_recording

FILENAME = "driver-race.json"
ACTIONS = "driver-race-actions.json"
AUDIT = "driver-race-audit.jsonl"
RESTART_MODES = ("keep", "neutralise")
LAST_LAP_S = 5                 # how long the OBS tower shows a car's new lap time
LAST_LAP_S_RANGE = (1, 30)
MAX_REASON = 200
MAX_ENTRY = 120
MAX_CORRECTION_M = 100_000.0
MAX_PIT_CORRECTION = 50
MAX_ESTIMATE_S = 3600.0
DEFAULTS = {"formation": True, "finish_mode": "laps", "lap_count": None, "delay_s": 0,
            "visible": False, "show_compound": True, "show_fuel": True}
COMPOUNDS = ("RS", "RM", "RH", "I", "W")   # Racing Soft/Medium/Hard, Intermediate, Wet
MAX_DELAY_S = 120
FASTEST = "driver-race-fastest.json"
SNAPSHOT_EVERY_S = 0.5
MAX_LAP_MS = 3_600_000
MAX_QUALI = 200             # qualifying corrections kept
MAX_CLASS = 24              # the roster caps a class name
MAX_CLASSES = 64            # classes with a stored lap count or fastest lap
LINE_POINTS = 600
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
    for key, value in doc.items():
        try:
            out.update(validate({key: value}))
        except ValueError:
            pass  # a hand-edited bad value keeps its default, the others stay
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
    for key in ("show_compound", "show_fuel"):
        if key in changes:
            if not isinstance(changes[key], bool):
                raise ValueError(f"{key} is true or false")
            out[key] = changes[key]
    if "visible" in changes:
        if not isinstance(changes["visible"], bool):
            raise ValueError("visible is true or false")
        out["visible"] = changes["visible"]
    if "delay_s" in changes:
        n = changes["delay_s"]
        if type(n) is not int or not 0 <= n <= MAX_DELAY_S:
            raise ValueError(f"delay_s is 0 to {MAX_DELAY_S} seconds")
        out["delay_s"] = n
    if "lap_count" in changes:
        n = changes["lap_count"]
        if n is not None and (type(n) is not int or not 1 <= n <= MAX_LAPS):
            raise ValueError(f"lap_count is 1 to {MAX_LAPS}, or none")
        out["lap_count"] = n
    return out


def _write_json(path, doc):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".driver-race-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        atomic_replace.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def write_settings(path, changes):
    current = read_settings(path)
    current.update(validate(changes))
    _write_json(path, current)
    return current


def _lap_seconds(n):
    return type(n) is int and LAST_LAP_S_RANGE[0] <= n <= LAST_LAP_S_RANGE[1]


def read_league(path):
    """The league's red flag restart default and how long the tower shows a new lap;
    the defaults when unset or unreadable."""
    doc = _read_json(path) if path else {}
    restart, seconds = doc.get("restart"), doc.get("last_lap_s")
    return {"restart": restart if restart in RESTART_MODES else "keep",
            "last_lap_s": seconds if _lap_seconds(seconds) else LAST_LAP_S}


def validate_league(changes):
    """The league keys of `changes` (`restart`, `last_lap_s`); ValueError on a bad value."""
    out = {k: changes[k] for k in ("restart", "last_lap_s") if k in changes}
    if "restart" in out and out["restart"] not in RESTART_MODES:
        raise ValueError("restart is keep or neutralise")
    if "last_lap_s" in out and not _lap_seconds(out["last_lap_s"]):
        raise ValueError("last_lap_s is %d to %d seconds" % LAST_LAP_S_RANGE)
    return out


def write_league(path, changes):
    """Change `restart` and/or `last_lap_s`; ValueError on a bad or missing value."""
    changes = validate_league(changes)
    if not changes:
        raise ValueError("nothing to change")
    doc = _read_json(path)
    doc.update(changes)
    _write_json(path, doc)
    return read_league(path)


def audit_path_for(settings_path):
    return os.path.join(os.path.dirname(os.path.abspath(settings_path)), AUDIT)


def _actions_path_for(settings_path):
    return os.path.join(os.path.dirname(os.path.abspath(settings_path)), ACTIONS)


def _last_audited(path):
    """{ts, who, action} of the audit log's last line, or None."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 4096))
            lines = fh.read().decode("utf-8", "replace").splitlines()
        record = json.loads(lines[-1]) if lines else None
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or not {"ts", "who", "action"} <= record.keys():
        return None
    return {k: record[k] for k in ("ts", "who", "action")}


def clear_actions(runtime_dir):
    """A fresh event: drop every action (corrections, rejoin marks, retirements, driver
    names, tyre compounds, qualifying corrections) and the fastest lap; the audit log stays."""
    for name in (ACTIONS, FASTEST):
        path = os.path.join(runtime_dir, name)
        try:
            atomic_replace.retry_windows_file_operation(lambda p=path: os.remove(p))
        except FileNotFoundError:
            pass  # nothing recorded


def _text(value, most, empty=False):
    """A stored string of at most `most` characters that encodes as UTF-8."""
    if not isinstance(value, str) or len(value) > most or not (value or empty):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _saved_lap(doc):
    """{entry, driver, ms} of a stored lap, or None when any part is invalid."""
    if not isinstance(doc, dict):
        return None
    entry, driver, ms = doc.get("entry"), doc.get("driver"), doc.get("ms")
    if _text(entry, MAX_ENTRY) and _text(driver, MAX_ENTRY) and type(ms) is int and ms > 0 \
            and _valid_name(driver, rule=driver_invite.KNOWN_NAME):
        return {"entry": entry, "driver": driver, "ms": ms}
    return None


def _saved_fastest(doc, track):
    """The fastest lap of this track layout in the fastest-lap file's `doc`, or None."""
    return _saved_lap(doc) if doc.get("track") == track else None


def _saved_fastest_by_class(doc, track):
    """The fastest lap of each class on this track layout in the fastest-lap file's `doc`."""
    saved = doc.get("by_class") if doc.get("track") == track else None
    out = {}
    for cls, item in (saved.items() if isinstance(saved, dict) else ()):
        lap = _saved_lap(item)
        if lap and _text(cls, MAX_CLASS, empty=True) and len(out) < MAX_CLASSES:
            out[cls] = lap
    return out


# The relay's ASSET_KEY_RE, capped: a brand key names a logo file, never a path.
BRAND_KEY_RE = re.compile(r"[a-z0-9-]{1,64}")


def _brand(info):
    """(key, name) of a roster entry's brand; a key outside the asset-key charset is ""."""
    key = info.get("brandKey")
    key = key if isinstance(key, str) and BRAND_KEY_RE.fullmatch(key) else ""
    name = info.get("brandName")
    return key, name[:MAX_ENTRY] if isinstance(name, str) else ""


def _viewer_entry(row, roster):
    info = roster.get(row["entry"]) or {}
    brand, brand_name = _brand(info)
    return {"entry": row["entry"], "number": info.get("number") or "",
            "driver": row["driver"], "team": info.get("cupTeam") or "",
            "color": info.get("bgColor") or "", "text_color": info.get("textColor") or "",
            "brand": brand, "brand_name": brand_name}


def public_brands(view, has_logo):
    """A copy of the public view whose rows name a brand only when `has_logo(key)`:
    a viewer's page asks for every named logo, and a missing one again later."""
    out = dict(view)
    for key in ("entries", "qualifying"):
        if key in out:
            out[key] = [r if not r.get("brand") or has_logo(r["brand"]) else dict(r, brand="")
                        for r in out[key] or ()]
    return out


def with_brands(standings, roster):
    """A copy of `standings` whose entries carry their roster brand, for the desks."""
    def one(e):
        brand, name = _brand(roster.get(e.get("entry")) or {})
        return dict(e, brand=brand, brand_name=name)
    return dict(standings, entries=[one(e) for e in standings.get("entries") or ()])


_PUBLIC_TOP = ("v", "mode", "stint", "remaining", "remaining_running", "delay_s", "track_name", "state",
               "red_flag", "lap")
_CLASS_ROW = ("class", "class_pos", "class_gap_s", "class_interval_s", "class_lapped")
_PUBLIC_ROW = ("pos", "entry", "number", "driver", "team", "color", "text_color", "laps",
               "stint_laps", "gap_s", "interval_s", "lapped", "status", "pit_stops",
               "last_lap_ms", "finished", "best_ms", "gap_ms", "compound", "fuel_l", "brand",
               "brand_name", "class_color") + _CLASS_ROW
_PUBLIC_LAP = ("entry", "driver", "team", "ms")


def _class_colors(roster):
    """Each class's colour: the first one set for it in roster order."""
    out = {}
    for info in roster.values():
        if isinstance(info, dict) and not out.get(info.get("carClass") or ""):
            out[info.get("carClass") or ""] = info.get("classColor") or ""
    return out


def public_view(snap):
    """The public live timing page's data (#1128): a dashboard snapshot cut to an
    allowlist. The display switches hide compound and fuel on OBS only."""
    if not snap:
        return {"v": 1, "mode": "race", "entries": []}

    def rows(items):
        return [{k: r[k] for k in _PUBLIC_ROW if k in r} for r in items or ()]
    out = {k: snap[k] for k in _PUBLIC_TOP if k in snap}
    fl = snap.get("fastest")
    if fl:
        out["fastest"] = {k: fl.get(k) for k in _PUBLIC_LAP}
    elif "fastest" in snap:
        out["fastest"] = None
    if snap.get("classes"):
        out["classes"] = [{"name": c.get("name"), "color": c.get("color"), "lap": c.get("lap"),
                           "laps": c.get("laps"),
                           "fastest": {k: c["fastest"].get(k) for k in _PUBLIC_LAP} if c.get("fastest") else None}
                          for c in snap["classes"]]
    if "qualifying" in snap:
        out["qualifying"] = rows(snap["qualifying"])
    else:
        out["entries"] = rows(snap.get("entries"))
    return out


def qualifying(connections, segment, roster, min_ms=0, overrides=None, seen=None):
    """Rank the running segment's entries by GT7 best lap: [{pos, entry, number,
    driver, team, color, text_color, best_ms, gap_ms}]; entries without a lap last.
    Only live senders count, and a best lap under `min_ms` (faster than the line
    allows) is ignored. `overrides` {entry: ms or None} are Race Control's
    corrections: a set time replaces GT7's, None deletes it. `seen` {entry: (ms,
    name)} carries the bests of earlier calls and is updated in place, so a lap stays
    ranked after its sender goes quiet."""
    best = seen if seen is not None else {}
    for c in connections:
        if not c.get("live", True):
            continue
        if segment is not None and c.get("segment") not in (segment, None):
            continue
        ms = (c.get("latest") or {}).get("best_ms")
        ms = ms if type(ms) is int and ms > max(0, min_ms) else None
        cur = best.get(c["entry"])
        if cur is None and len(best) >= MAX_QUALI:
            continue
        if cur is None or (ms is not None and (cur[0] is None or ms < cur[0])):
            best[c["entry"]] = (ms, c.get("name") or "")
    best = {e: (ms if ms is None or ms > max(0, min_ms) else None, name)
            for e, (ms, name) in best.items()}
    for entry, ms in (overrides or {}).items():
        best[entry] = (ms, best.get(entry, (None, ""))[1])
    order = sorted(best.items(), key=lambda kv: (kv[1][0] is None, kv[1][0] or 0, kv[0]))
    pole = order[0][1][0] if order else None
    return [dict(_viewer_entry({"entry": e, "driver": name}, roster), pos=i + 1, best_ms=ms,
                 gap_ms=None if ms is None or pole is None else ms - pole)
            for i, (e, (ms, name)) in enumerate(order)]


def _saved_actions(path):
    """(corrections {entry: (metres, pit stops)}, rejoin entries) from the actions file;
    anything malformed is skipped, never raised."""
    doc = _read_json(path)
    corrections, rejoins = {}, []
    raw = doc.get("corrections")
    for entry, value in (raw.items() if isinstance(raw, dict) else ()):
        if _text(entry, MAX_ENTRY) and isinstance(value, list) \
                and len(value) == 2 and isinstance(value[0], (int, float)) \
                and not isinstance(value[0], bool) and math.isfinite(value[0]) \
                and type(value[1]) is int:
            corrections[entry] = (float(value[0]), value[1])
    raw = doc.get("rejoin")
    for entry in (raw if isinstance(raw, list) else ()):
        if _text(entry, MAX_ENTRY):
            rejoins.append(entry)
    return corrections, rejoins


def _saved_retired(path):
    raw = _read_json(path).get("retired")
    return [e for e in (raw if isinstance(raw, list) else ())
            if _text(e, MAX_ENTRY)][:MAX_QUALI]


def _valid_name(name, empty=False, rule=driver_invite.NAME):
    """A desk-entered driver name follows the sender registration rule; `rule`
    KNOWN_NAME also takes names stored before the 24 character cap."""
    return isinstance(name, str) and ((empty and name == "") or bool(rule.match(name)))


def _saved_overrides(path):
    """[[entry, registered driver or "", shown name]] from the actions file; anything
    malformed is dropped."""
    raw = _read_json(path).get("driver_names")
    out = []
    for item in (raw if isinstance(raw, list) else ()):
        if isinstance(item, list) and len(item) == 3 and _text(item[0], MAX_ENTRY) \
                and _valid_name(item[1], True, driver_invite.KNOWN_NAME) \
                and _valid_name(item[2], rule=driver_invite.KNOWN_NAME) and len(out) < MAX_QUALI:
            out.append(item)
    return out


def _override_list(saved):
    return sorted(({"entry": e, "driver": d, "name": n} for e, d, n in saved),
                  key=lambda o: (o["entry"], o["driver"]))


def _saved_compounds(path):
    """{entry: {code, who, ts}} from the actions file; anything malformed is skipped."""
    raw = _read_json(path).get("compounds")
    out = {}
    for entry, value in (raw.items() if isinstance(raw, dict) else ()):
        if _text(entry, MAX_ENTRY) and isinstance(value, dict) \
                and value.get("code") in COMPOUNDS and _text(value.get("who"), math.inf, empty=True) \
                and isinstance(value.get("ts"), (int, float)) and math.isfinite(value["ts"]) \
                and len(out) < MAX_QUALI:
            out[entry] = {"code": value["code"], "who": value["who"][:80], "ts": float(value["ts"])}
    return out


def _segment_key(segment):
    return segment if isinstance(segment, str) else ""


def _saved_quali(path, segment):
    """{entry: best lap ms, or None for a deleted time} of one qualifying segment."""
    raw = _read_json(path).get("qualifying")
    raw = raw.get(_segment_key(segment)) if isinstance(raw, dict) else None
    out = {}
    for entry, ms in (raw.items() if isinstance(raw, dict) else ()):
        if _text(entry, MAX_ENTRY) and (
                ms is None or (type(ms) is int and 0 < ms < MAX_LAP_MS)) and len(out) < MAX_QUALI:
            out[entry] = ms
    return out


def _stint_class_laps(path, stint):
    raw = _read_json(path).get("class_laps")
    raw = raw.get(stint) if isinstance(raw, dict) else None
    return raw if isinstance(raw, dict) else {}


def _cleared_class_laps(path, stint):
    """The classes whose count Race Control cleared in one stint."""
    return {cls for cls, laps in _stint_class_laps(path, stint).items() if laps is None}


def _saved_class_laps(path, stint):
    """{class: lap count} of one stint; anything malformed is dropped."""
    raw = _stint_class_laps(path, stint)
    out = {}
    for cls, laps in (raw.items() if isinstance(raw, dict) else ()):
        if _text(cls, MAX_CLASS) and type(laps) is int \
                and 0 < laps <= MAX_LAPS and len(out) < MAX_CLASSES:
            out[cls] = laps
    return out


def _store_class_laps(doc, stint, cls, laps):
    raw = doc.get("class_laps") if isinstance(doc.get("class_laps"), dict) else {}
    done = raw.get(stint) if isinstance(raw.get(stint), dict) else {}
    if stint not in raw and len(raw) >= MAX_QUALI:
        raise ValueError(f"at most {MAX_QUALI} stints carry class lap counts")
    if laps is None:
        if cls in done or len(done) < MAX_CLASSES:
            done[cls] = None   # cleared: the Schedule never brings it back in this stint
    elif cls in done or len(done) < MAX_CLASSES:
        done[cls] = laps
    else:
        raise ValueError(f"at most {MAX_CLASSES} classes per stint")
    if done:
        raw[stint] = done
    else:
        raw.pop(stint, None)
    doc["class_laps"] = raw


def _named_classes(roster):
    return {info.get("carClass") for info in roster.values()
            if isinstance(info, dict) and isinstance(info.get("carClass"), str) and info.get("carClass")}


def _class_name(cls):
    if not isinstance(cls, str) or not cls.strip() or len(cls) > MAX_CLASS:
        raise ValueError(f"class is 1 to {MAX_CLASS} characters")
    try:
        cls.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("class is not valid text") from None
    return cls.strip()


def _entry_name(entry):
    if not isinstance(entry, str) or not entry.strip() or len(entry) > MAX_ENTRY:
        raise ValueError(f"entry is 1 to {MAX_ENTRY} characters")
    try:
        entry.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("entry is not valid text") from None
    return entry.strip()


def _mode(mode):
    if mode not in RESTART_MODES:
        raise ValueError("mode is keep or neutralise")
    return mode


LOG = logging.getLogger("racecast.relay")   # the relay attaches its log file to this logger
RECORDING_RETRY_S = 30.0
_STORE_ERRORS = (OSError, sqlite3.Error)


class NotRaceMode(Exception):
    """A manual recording start while the relay is not in race mode."""


class Service:
    """Thread-safe holder of the live race."""

    def __init__(self, settings_path, pick, trace_for, multi_lobby, clock=time.monotonic,
                 enabled=lambda: True, league_path=None, wall=time.time, context=None, recording_dir=None):
        """`enabled()` False (the relay in qualifying mode) ignores samples.
        `league_path` holds the profile's restart default; `wall` dates the audit log.
        `context()` gives the dashboard {roster, stint, remaining, mode, segment,
        connections}; every key optional. `recording_dir` holds the per-event
        race recording; nothing opens it before open_recording(), which the relay
        calls once it owns its control port."""
        self._path = settings_path
        self._pick, self._trace_for = pick, trace_for
        self._multi_lobby = multi_lobby
        self._clock = clock
        self._wall = wall
        self._context = context or (lambda: {})
        self._snapshots = collections.deque(maxlen=int(MAX_DELAY_S / SNAPSHOT_EVERY_S) + 4)
        self._saved_fastest = None
        self._saved_by_class = {}
        self._class_map = {}       # entry -> class, from the last roster seen
        self._line_cache = None
        self._settings_cache = None
        self._league = league_path
        self._last_action = _last_audited(audit_path_for(settings_path))
        self._enabled = enabled
        self._lock = threading.Lock()
        self._recording_dir = recording_dir
        self._rec_lock = threading.Lock()
        self._rec = None
        self._rec_retry = 0.0
        self._rec_failing = False
        self._rec_allowed = False   # set by open_recording()
        self._race = None
        self._race_pick = object()
        self._line_tried = None
        self._final_lobby_laps = None
        self._final_on_air = False  # endurance: the last schedule row is on air
        self._quali_seen = {}      # (pick, segment) -> {entry: (best ms, driver)}

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
        if multi and self._final_on_air:
            race.final_lobby(self._final_lobby_laps, self._clock())
        corrections, rejoins = _saved_actions(_actions_path_for(self._path))
        for entry, (metres, pits) in corrections.items():
            race.correct(entry, metres, pits)
        for entry in rejoins:
            race.rejoin(entry, True)
        for entry in _saved_retired(_actions_path_for(self._path)):
            race.retire(entry, True, self._clock())
        for entry, driver, name in _saved_overrides(_actions_path_for(self._path)):
            race.set_driver_name(entry, driver, name)
        race.set_classes(self._class_map)
        saved = _read_json(self._fastest_path())
        race.fastest = _saved_fastest(saved, pick_id)
        race.fastest_by_class = _saved_fastest_by_class(saved, pick_id)
        self._saved_fastest = race.fastest
        self._saved_by_class = {c: dict(f) for c, f in race.fastest_by_class.items()}
        return race

    def _set_roster(self, race, roster):
        """Give the race the Configuration entries and their classes."""
        self._class_map = {e: info.get("carClass") or "" for e, info in roster.items()
                           if isinstance(info, dict)}
        race.set_roster(list(roster))
        race.set_classes(self._class_map)

    def _fastest_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(self._path)), FASTEST)

    def save_fastest(self):
        """Persist the race's fastest lap and each class's with its layout when they
        changed (they survive a relay restart); none at all removes the file. Runs
        outside the race lock."""
        with self._lock:
            race = self._race
            fastest = race.fastest if race is not None else None
            by_class = {c: dict(f) for c, f in race.fastest_by_class.items()} if race is not None else {}
            track = self._race_pick
            if fastest == self._saved_fastest and by_class == self._saved_by_class:
                return
            self._saved_fastest = None if fastest is None else dict(fastest)
            self._saved_by_class = by_class
        try:
            if fastest is None and not by_class:
                os.remove(self._fastest_path())
            else:
                _write_json(self._fastest_path(), dict(fastest or {}, track=track,
                                                       **({"by_class": by_class} if by_class else {})))
        except OSError:
            pass  # the race keeps it in memory; the next change writes it again

    def set_fastest(self, who, entry=None, driver=None, ms=None, cls=None):
        """Race Control's correction: clear the fastest lap (no entry) or set it; with
        `cls` only that class's fastest lap, never the overall one."""
        if entry is not None:
            entry = _entry_name(entry)
            if not _valid_name(driver):
                raise ValueError("driver is 1 to 24 letters, digits, spaces, _ . or -")
            if type(ms) is not int or not 0 < ms < 3_600_000:
                raise ValueError("ms is a lap time in milliseconds")
        roster = self._context().get("roster") or {}
        if cls is not None:
            cls = _class_name(cls)
            if cls not in _named_classes(roster):
                raise ValueError("unknown class")
        with self._lock:
            race = self._current()
            self._set_roster(race, roster)
            if cls is not None and entry is not None and race.class_of(entry) != cls:
                raise ValueError("the entry is not in that class")
            void = entry is None and (race.fastest_by_class.get(cls) if cls is not None else race.fastest) is None
            if not void:
                self._apply_fastest(who, race, cls, entry, driver, ms)
        if not void:
            self.save_fastest()
        return self.standings()

    def _apply_fastest(self, who, race, cls, entry, driver, ms):
        """Audit, then change the fastest lap; the caller holds the lock."""
        self._log(who, "fastest", **({"class": cls} if cls is not None else {}),
                  **({"entry": entry, "ms": ms} if entry else {"cleared": True}))
        lap = None if entry is None else {"entry": entry, "driver": driver, "ms": ms}
        if cls is not None:
            race.fastest_by_class.pop(cls, None)
            if lap is not None:
                race.fastest_by_class[cls] = lap
        else:
            race.fastest_by_class = {c: f for c, f in race.fastest_by_class.items() if f != race.fastest}
            race.fastest = lap
            if lap is not None:
                race.fastest_by_class[race.class_of(entry)] = dict(lap)

    def _viewer(self, ctx):
        """The dashboard payload: an allowlist, safe for viewers and the public page."""
        roster = ctx.get("roster") or {}
        pick = self._pick() or {}
        out = {"v": 1, "mode": ctx.get("mode") or "race", "stint": ctx.get("stint"),
               "remaining": ctx.get("remaining"), "remaining_running": bool(ctx.get("remaining_running")),
               "track": pick.get("id"), "track_name": pick.get("track") or None,
               "visible": self._settings()["visible"]}
        race = self._current()
        self._set_roster(race, roster)
        settings = self._settings()
        out["delay_s"] = settings["delay_s"]
        out["show"] = self._show(settings)
        compounds = _saved_compounds(_actions_path_for(self._path))
        if out["mode"] == "qualifying":
            out["qualifying"] = qualifying(
                ctx.get("connections") or [], ctx.get("segment"), roster,
                min_ms=0 if race.length is None else int(race.min_lap_s() * 1000),
                overrides=_saved_quali(_actions_path_for(self._path), ctx.get("segment")),
                seen=self._quali_seen.setdefault((pick.get("id"), _segment_key(ctx.get("segment"))), {}))
            for row in out["qualifying"]:
                row["compound"] = (compounds.get(row["entry"]) or {}).get("code")
                row["driver"] = race.display_name(row["entry"], row["driver"])
            return out
        class_laps = _saved_class_laps(_actions_path_for(self._path), _segment_key(ctx.get("stint"))) \
            if len(_named_classes(roster)) >= 2 else {}
        race.set_class_laps(class_laps)
        st = race.standings(self._clock())
        colors = _class_colors(roster)
        rows = []
        for i, r in enumerate(st["entries"]):
            row = _viewer_entry(r, roster)
            row.update({k: r[k] for k in ("laps", "stint_laps", "gap_s", "interval_s", "lapped",
                                          "status", "pit_stops", "last_lap_ms", "fuel_l",
                                          "finished", "x", "z")})
            if st["classes"]:
                row.update({k: r[k] for k in _CLASS_ROW})
                row["class_color"] = colors.get(r["class"], "")
            row["pos"] = i + 1
            row["compound"] = (compounds.get(r["entry"]) or {}).get("code")
            rows.append(row)
        self._mark_dc(rows)
        fl = race.fastest
        first = next((name for name in st["classes"] if name), None)
        out.update({
            "state": st["state"], "red_flag": st["red_flag"] is not None, "lap": st["lap"],
            "fastest": self._shown_lap(race, fl, roster),
            "classes": [{"name": name, "color": colors.get(name, ""),
                         "lap": st["class_lap"][name],
                         "laps": st["lap"]["count"] if name == first else
                                 class_laps.get(name) if name and st["class_counts"][name] else None,
                         "fastest": self._shown_lap(race, race.fastest_by_class.get(name), roster)}
                        for name in st["classes"]],
            "entries": rows})
        return out

    @staticmethod
    def _shown_lap(race, fl, roster):
        """A fastest lap as viewers see it: with the team and the driver name override."""
        return None if fl is None else dict(
            fl, team=(roster.get(fl["entry"]) or {}).get("cupTeam") or "",
            driver=race.display_name(fl["entry"], fl["driver"]))

    def _mark_dc(self, rows):
        """A quiet car whose disconnect Race Control confirmed reads "dc" instead of
        "no_data". A recorder failure only costs the label, never the tick."""
        try:
            dc = self._rec.confirmed_silent() if self._rec is not None else set()
        except Exception:
            return
        for row in rows:
            if row.get("status") == "no_data" and row.get("entry") in dc:
                row["status"] = "dc"

    def line(self):
        """The picked layout's line for the track map, at most LINE_POINTS points;
        None without a pick or a downloaded line."""
        with self._lock:
            race, pick_id = self._current(), self._race_pick
            if not isinstance(pick_id, str) or race.length is None:
                return None
            if self._line_cache is None or self._line_cache["track"] != pick_id:
                pts = race._line.pts
                step = max(1, math.ceil(len(pts) / LINE_POINTS))
                self._line_cache = {"track": pick_id, "length_m": round(race.length, 1),
                                    "points": [[round(x, 1), round(z, 1)] for x, z in pts[::step]]}
            return self._line_cache

    def _settings(self):
        """read_settings, re-read only when the file changed."""
        try:
            stamp = os.stat(self._path).st_mtime_ns
        except OSError:
            stamp = None
        if self._settings_cache is None or self._settings_cache[0] != stamp:
            self._settings_cache = (stamp, read_settings(self._path))
        return self._settings_cache[1]

    def tick(self):
        """Take a dashboard snapshot (the relay calls this every SNAPSHOT_EVERY_S)."""
        ctx = self._context()
        self._settings()
        with self._lock:
            snap = self._viewer(ctx)
            self._snapshots.append((self._clock(), snap))
            recording = self._recording_dir and self._enabled()
            standings = self._current().standings(self._clock()) if recording else None
            rec = self._rec
            generation = rec.generation if rec is not None else None
        if recording:
            self._mark_dc(standings["entries"])
            self._observe(standings, snap.get("classes"), generation=generation)
        self.save_fastest()

    def open_recording(self):
        """Open the recording off the tick path, in race mode only; a failure backs off."""
        self._rec_allowed = True
        if self._recording_dir and self._enabled():
            self._guarded(self._recorder)

    def _observe(self, standings, classes=None, generation=None):
        """Feed the recorder; a recording failure never stops the tick."""
        if self._rec is None and (not self._rec_allowed or self._clock() < self._rec_retry):
            return
        self._guarded(lambda: self._recorder().observe(
            standings, self._compound_codes, self._wall(), classes, generation))

    def _guarded(self, fn):
        """Run fn; any failure is logged once and backs off RECORDING_RETRY_S."""
        try:
            fn()
            self._rec_failing = False
        except Exception as e:
            self._rec_retry = self._clock() + RECORDING_RETRY_S
            if not self._rec_failing:
                self._rec_failing = True
                LOG.warning("race recording unavailable: %s", e.__cause__ or e)

    def _compound_codes(self):
        saved = _saved_compounds(_actions_path_for(self._path))
        return {e: v.get("code") for e, v in saved.items() if isinstance(v, dict)}

    def _recorder(self):
        """The open recorder, opening it when needed; RuntimeError when that fails."""
        if not self._recording_dir or not self._rec_allowed:
            raise RuntimeError("recording unavailable")
        with self._rec_lock:
            if self._rec is None:
                try:
                    store = race_recording.open_current(self._recording_dir, self._wall())
                    self._rec = race_recording.Recorder(
                        store, race_recording.Writer(store), self._wall)
                except _STORE_ERRORS as e:
                    raise RuntimeError("recording unavailable") from e
            return self._rec

    def _open_recorder(self):
        """The recorder for a read: None while none is open (never opens one here);
        RuntimeError while opening it fails."""
        if not self._recording_dir:
            raise RuntimeError("recording unavailable")
        if self._rec is None and self._rec_failing:
            raise RuntimeError("recording unavailable")
        return self._rec

    def _acting_recorder(self):
        """The recorder for an action: opened in race mode, else only when already open."""
        rec = self._open_recorder()
        if rec is None and self._enabled():
            rec = self._recorder()
        return rec

    def recording_status(self):
        rec = self._open_recorder()
        if rec is None:
            return {"running": False, "available": True, "started_by": None,
                    "started_at": None, "span": None, "events": [], "dropped": 0, "failed": 0}
        return rec.status()

    def recording_start(self, who):
        """Start by hand; NotRaceMode outside race mode."""
        if not self._recording_dir:
            raise RuntimeError("recording unavailable")
        if not self._enabled():
            raise NotRaceMode("recording runs in race mode only")
        out = self._recorder().start(who)
        self._audit(who, "recording_start")
        return out

    def recording_stop(self, who):
        rec = self._acting_recorder()
        out = rec.stop(who) if rec else {"running": False, "started_by": None, "started_at": None}
        self._audit(who, "recording_stop")
        return out

    def recording_decide(self, who, event_id, decision):
        """Confirm or dismiss a disconnect event; ValueError for a bad id or decision."""
        if isinstance(event_id, bool) or (isinstance(event_id, float) and not event_id.is_integer()):
            raise ValueError("event id must be a whole number")
        try:
            event_id = int(event_id)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("event id must be a whole number") from None
        if not isinstance(decision, str):
            raise ValueError("decision must be confirm or dismiss")
        rec = self._acting_recorder()
        if rec is None:
            raise KeyError(event_id)
        ev = rec.decide(who, event_id, decision)
        self._audit(who, "dc_" + decision, event=event_id, entry=ev.get("entry"))
        return ev

    def _audit(self, who, action, **fields):
        with self._lock:
            self._log(who, action, **fields)

    def recording_at(self, t_wall):
        rec = self._open_recorder()
        try:
            return rec.sample_at(t_wall) if rec else None
        except _STORE_ERRORS as e:
            raise RuntimeError("recording unavailable") from e

    def recording_csv(self):
        """The whole CSV as a list of rows (read here, so a store error is a RuntimeError)."""
        rec = self._open_recorder()
        try:
            return list(rec.csv_rows()) if rec else [list(race_recording.CSV_HEADER)]
        except _STORE_ERRORS as e:
            raise RuntimeError("recording unavailable") from e

    def dashboard(self):
        """The newest snapshot at least the delay setting old; the oldest one while the
        delay has not passed yet. The display settings are the current ones."""
        settings = self._settings()
        delay = settings["delay_s"]
        with self._lock:
            if not self._snapshots:
                return None
            cutoff = self._clock() - delay
            chosen = self._snapshots[0][1]
            for t, snap in self._snapshots:
                if t <= cutoff:
                    chosen = snap
                else:
                    break
        return dict(chosen, visible=settings["visible"], delay_s=delay, show=self._show(settings))

    def _show(self, settings):
        return {"compound": settings["show_compound"], "fuel": settings["show_fuel"],
                "last_lap_s": read_league(self._league)["last_lap_s"]}

    def log(self, who, action, **fields):
        """Audit an action taken outside the race (a disconnect, a cover change)."""
        with self._lock:
            self._log(who, action, **fields)
        return self.standings()

    def _log(self, who, action, **fields):
        """Audit one action (best-effort) and remember it for the desks."""
        record = dict({"ts": self._wall(), "who": str(who)[:80], "action": action}, **fields)
        self._last_action = {k: record[k] for k in ("ts", "who", "action")}
        try:
            with open(audit_path_for(self._path), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass  # the audit log never blocks a race action

    def _save_actions(self, update):
        path = _actions_path_for(self._path)
        doc = _read_json(path)
        update(doc)
        _write_json(path, doc)

    def update(self, reg, sample):
        if not self._enabled():
            return
        with self._lock:
            race = self._current()
            race.update(reg["entry"], reg["conn"], reg["name"],
                        dict(sample, t=sample.get("mono", self._clock())))
            used = race.take_used_rejoins()
            if used:
                def update(doc):
                    doc["rejoin"] = sorted(set(doc.get("rejoin") or []) - set(used))
                self._save_actions(update)
                for entry in used:
                    self._log("race", "rejoin_used", entry=entry)
            for entry in race.take_ended_estimates():
                self._log("race", "estimate_end", entry=entry)

    def standings(self):
        ctx = self._context()
        with self._lock:
            race = self._current()
            self._set_roster(race, ctx.get("roster") or {})
            class_laps = _saved_class_laps(_actions_path_for(self._path), _segment_key(ctx.get("stint")))
            race.set_class_laps(class_laps)
            out = race.standings(self._clock())
            if out["classes"]:
                colors = _class_colors(ctx.get("roster") or {})
                out["class_colors"] = {name: colors.get(name, "") for name in out["classes"]}
                out["class_fastest"] = {name: self._shown_lap(race, race.fastest_by_class.get(name), ctx.get("roster") or {})
                                        for name in out["classes"]}
            if out["fastest"]:
                fl = out["fastest"]
                out["fastest"] = dict(fl, driver=race.display_name(fl["entry"], fl["driver"]))
            pick_id = self._race_pick
        self._mark_dc(out["entries"])
        out.update(settings=read_settings(self._path), track=pick_id,
                   compounds=_saved_compounds(_actions_path_for(self._path)),
                   driver_overrides=_override_list(_saved_overrides(_actions_path_for(self._path))),
                   quali_overrides=_saved_quali(_actions_path_for(self._path), ctx.get("segment")),
                   class_laps=class_laps,
                   league=read_league(self._league), last_action=self._last_action)
        return out

    def red_flag(self, who, mode=None):
        mode = _mode(mode) if mode is not None else read_league(self._league)["restart"]
        with self._lock:
            self._current().red_flag(self._clock(), mode)
            self._log(who, "red_flag", mode=mode)
        return self.standings()

    def set_restart_mode(self, who, mode):
        mode = _mode(mode)
        with self._lock:
            self._current().set_restart_mode(mode)
            self._log(who, "restart_mode", mode=mode)
        return self.standings()

    def release(self, who):
        with self._lock:
            self._current().release(self._clock())
            self._log(who, "release")
        return self.standings()

    def set_compound(self, who, entry, code):
        """Set an entry's tyre compound (one of COMPOUNDS) or clear it with None. It
        holds through driver changes; the last change wins and carries its author."""
        entry = _entry_name(entry)
        if code is not None and code not in COMPOUNDS:
            raise ValueError("compound is one of " + ", ".join(COMPOUNDS) + " or null")
        now = self._wall()

        def update(doc):
            done = doc.get("compounds") if isinstance(doc.get("compounds"), dict) else {}
            if code is None:
                done.pop(entry, None)
            elif entry in done or len(done) < MAX_QUALI:
                done[entry] = {"code": code, "who": str(who)[:80], "ts": now}
            else:
                raise ValueError(f"at most {MAX_QUALI} entries carry a compound")
            doc["compounds"] = done
        with self._lock:
            self._save_actions(update)
            self._log(who, "compound", entry=entry, code=code)
        return self.standings()

    def set_quali(self, who, entry, ms=None, clear=False):
        """Correct an entry's time in the running qualifying segment: `ms` replaces
        GT7's best lap, None deletes it, `clear` gives GT7's time back."""
        entry = _entry_name(entry)
        if not clear and ms is not None and (type(ms) is not int or not 0 < ms < MAX_LAP_MS):
            raise ValueError("ms is a lap time in milliseconds, or null to delete it")
        segment = _segment_key(self._context().get("segment"))

        def update(doc):
            raw = doc.get("qualifying") if isinstance(doc.get("qualifying"), dict) else {}
            done = raw.get(segment) if isinstance(raw.get(segment), dict) else {}
            if clear:
                done.pop(entry, None)
            elif entry in done or len(done) < MAX_QUALI:
                done[entry] = ms
            else:
                raise ValueError(f"at most {MAX_QUALI} qualifying corrections per segment")
            raw[segment] = done
            doc["qualifying"] = raw
        with self._lock:
            self._save_actions(update)
            self._log(who, "quali", entry=entry, **({"cleared": True} if clear else {"ms": ms}))
        return self.standings()

    def rejoin(self, who, entry, on):
        entry = _entry_name(entry)
        if not isinstance(on, bool):
            raise ValueError("on is true or false")

        def update(doc):
            marks = set(doc.get("rejoin") or [])
            (marks.add if on else marks.discard)(entry)
            doc["rejoin"] = sorted(marks)
        with self._lock:
            self._save_actions(update)
            self._current().rejoin(entry, on)
            self._log(who, "rejoin", entry=entry, on=on)
        return self.standings()

    def correct(self, who, entry, metres=0.0, pit_stops=0, reason=""):
        entry = _entry_name(entry)
        if isinstance(metres, bool) or not isinstance(metres, (int, float)) \
                or not math.isfinite(metres) or abs(metres) > MAX_CORRECTION_M:
            raise ValueError(f"metres is a number up to {int(MAX_CORRECTION_M)} either way")
        if type(pit_stops) is not int or abs(pit_stops) > MAX_PIT_CORRECTION:
            raise ValueError(f"pit_stops is a whole number up to {MAX_PIT_CORRECTION} either way")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > MAX_REASON:
            raise ValueError(f"a correction needs a reason of 1 to {MAX_REASON} characters")
        metres = float(metres)

        def update(doc):
            done = doc.setdefault("corrections", {})
            prev = done.get(entry, [0.0, 0])
            done[entry] = [prev[0] + metres, prev[1] + pit_stops]
        with self._lock:
            self._save_actions(update)
            self._current().correct(entry, metres, pit_stops)
            self._log(who, "correct", entry=entry, metres=metres, pit_stops=pit_stops,
                      reason=reason.strip())
        return self.standings()

    def estimate(self, who, entry, ref=None, seconds=None, clear=False, ahead=False):
        """Show the quiet `entry` `seconds` behind `ref`, or `ahead` of it, until its
        data returns, or `clear` it. Not persisted: a relay restart drops it."""
        entry = _entry_name(entry)
        if clear:
            with self._lock:
                if not self._current().clear_estimate(entry):
                    raise ValueError("that entry has no estimate")
                self._log(who, "estimate", entry=entry, cleared=True)
            return self.standings()
        ref = _entry_name(ref)
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) \
                or not math.isfinite(seconds) or not 0 <= seconds <= MAX_ESTIMATE_S:
            raise ValueError(f"seconds is a number from 0 to {int(MAX_ESTIMATE_S)}")
        if not isinstance(ahead, bool):
            raise ValueError("ahead is true or false")
        seconds = float(seconds)
        roster = self._context().get("roster") or {}
        with self._lock:
            race = self._current()
            self._set_roster(race, roster)
            race.estimate(entry, ref, seconds, self._clock(), ahead=ahead)
            self._log(who, "estimate", entry=entry, ref=ref, seconds=seconds, ahead=ahead)
        return self.standings()

    def retire(self, who, entry, on):
        """Mark a car retired (DNF) or undo it; persisted until a race reset."""
        entry = _entry_name(entry)
        if not isinstance(on, bool):
            raise ValueError("on is true or false")

        def update(doc):
            marks = set(_saved_retired(_actions_path_for(self._path)))
            if on and entry not in marks and len(marks) >= MAX_QUALI:
                raise ValueError(f"at most {MAX_QUALI} retirements")
            (marks.add if on else marks.discard)(entry)
            doc["retired"] = sorted(marks)
        with self._lock:
            self._log(who, "retire", entry=entry, on=on)
            self._save_actions(update)
            ended = self._current().retire(entry, on, self._clock())
            for other in ended:
                self._log(who, "estimate_end", entry=other, reason="reference_retired")
        return self.standings()

    def set_driver_name(self, who, entry, name, driver=""):
        """Show `name` for the driver of `entry` registered as `driver` ("" for an entry
        without a sender); "" removes it. Names follow the sender registration rule."""
        entry = _entry_name(entry)
        if not _valid_name(driver, True, driver_invite.KNOWN_NAME):
            raise ValueError("driver is the registered name, or empty for an entry without a sender")
        name = name.strip() if isinstance(name, str) else name
        if not _valid_name(name, empty=True):
            raise ValueError("name is up to 24 letters, digits, spaces, _ . or -")
        saved = [o for o in _saved_overrides(_actions_path_for(self._path)) if o[:2] != [entry, driver]]
        if name and len(saved) >= MAX_QUALI:
            raise ValueError(f"at most {MAX_QUALI} driver names")

        def update(doc):
            doc["driver_names"] = saved + ([[entry, driver, name]] if name else [])
        with self._lock:
            self._log(who, "driver_name", entry=entry, driver=driver, name=name)
            self._save_actions(update)
            self._current().set_driver_name(entry, driver, name)
        return self.standings()

    def override_change(self, who, entry, conn):
        entry = _entry_name(entry)
        with self._lock:
            done = self._current().override_change(entry, conn, self._clock())
            if not done:
                raise ValueError("that connection is not a sender of this entry, "
                                 "or no finish line is known for it yet")
            self._log(who, "change", entry=entry, conn=conn)
        return self.standings()

    def set_settings(self, changes, who="tailnet"):
        """Persist and apply; formation only matters before a car starts counting."""
        saved = write_settings(self._path, changes)
        with self._lock:
            self._log(who, "settings", **validate(changes))
            race = self._current()
            race.formation = saved["formation"]
            race.finish_mode = saved["finish_mode"]
            if not race.multi_lobby:
                race.lap_count = saved["lap_count"]
            elif self._final_on_air and "lap_count" in changes:
                self._final_lobby_laps = race.lap_count = saved["lap_count"]
        return saved

    def class_laps(self, stint=None):
        """{class: lap count} Race Control set for `stint` (default: the one on air)."""
        stint = self._context().get("stint") if stint is None else stint
        return _saved_class_laps(_actions_path_for(self._path), _segment_key(stint))

    def set_class_laps(self, who, cls, laps, stint=None):
        """Set the lap count of a class other than the fastest for one stint, or clear
        it with None. Display only: it never finishes a car."""
        cls = _class_name(cls)
        if laps is not None and (type(laps) is not int or not 0 < laps <= MAX_LAPS):
            raise ValueError(f"laps is a whole number from 1 to {MAX_LAPS}, or null")
        ctx = self._context()
        if cls not in _named_classes(ctx.get("roster") or {}):
            raise ValueError("unknown class")
        stint = _segment_key(ctx.get("stint") if stint is None else stint)

        def update(doc):
            _store_class_laps(doc, stint, cls, laps)
        with self._lock:
            self._log(who, "class_laps", stint=stint, **{"class": cls}, laps=laps)
            self._save_actions(update)
        return self.standings()

    def _prefill_class_laps(self, defaults, ctx):
        """The Schedule's `Laps <Class>` cells of the row on air, where no desk value is."""
        named = {c.lower(): c for c in _named_classes(ctx.get("roster") or {})}
        stint = _segment_key(ctx.get("stint"))
        saved = set(_saved_class_laps(_actions_path_for(self._path), stint)) \
            | _cleared_class_laps(_actions_path_for(self._path), stint)
        fill = {}
        for cls, laps in (defaults.items() if isinstance(defaults, dict) else ()):
            cls = named.get(cls.lower()) if isinstance(cls, str) else None
            if cls and cls not in saved and type(laps) is int and 0 < laps <= MAX_LAPS:
                fill[cls] = laps
        if not fill:
            return

        def update(doc):
            for cls, laps in fill.items():
                _store_class_laps(doc, stint, cls, laps)
        for cls, laps in fill.items():
            self._log("schedule", "class_laps", stint=stint, **{"class": cls}, laps=laps)
        self._save_actions(update)

    def stint_switch(self, final_laps=None, window=True, final_row=None, class_laps=None):
        """The broadcast switched stints: open the change window (unless `window` is
        False, a relay start that finds the last row on air). `final_row` (default:
        `final_laps` given) says the last row is on air; `final_laps` is its lobby's lap
        count, None until a count is set. `class_laps` are the row's `Laps <Class>`
        cells; they never replace a value the desks set."""
        final_row = final_laps is not None if final_row is None else final_row
        ctx = self._context() if class_laps else None
        with self._lock:
            if class_laps:
                try:
                    self._prefill_class_laps(class_laps, ctx)
                except (OSError, ValueError):
                    pass  # the desks can still enter the count
            race = self._current()
            if window:
                race.open_change_window(self._clock())
            if final_row and race.multi_lobby:
                self._final_on_air, self._final_lobby_laps = True, final_laps
                race.final_lobby(final_laps, self._clock())

    def final_lap(self, who="tailnet"):
        with self._lock:
            self._current().final_lap(self._clock())
            self._log(who, "final_lap")

    def finish(self, who="tailnet"):
        with self._lock:
            self._current().finish(self._clock())
            self._log(who, "finish")

    def reset(self, who="tailnet"):
        """Start the race over: its corrections, rejoin marks, retirements, fastest lap
        and open disconnects go with it; tyre compounds, driver names, qualifying
        corrections and the recording stay."""
        def drop_race(doc):
            for key in ("corrections", "rejoin", "retired"):
                doc.pop(key, None)
        if self._recording_dir and self._rec is None and self._rec_allowed and self._enabled():
            self._guarded(self._recorder)
        with self._lock:
            self._log(who, "reset")
            self._save_actions(drop_race)
            try:
                atomic_replace.retry_windows_file_operation(lambda: os.remove(self._fastest_path()))
            except FileNotFoundError:
                pass  # no fastest lap recorded
            self._final_lobby_laps, self._final_on_air = None, False
            self._race = self._build(self._pick()["id"] if self._pick() else None)
            # Under the lock, so a tick reads the new race and the new generation together.
            rec = self._rec
            if rec is not None:
                self._guarded(lambda: rec.new_race(self._wall()))

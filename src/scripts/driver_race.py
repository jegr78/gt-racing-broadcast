"""The relay's live race from driver telemetry (#1112): settings, the picked layout's
line and one `race_distance.Race`, behind a lock.

Event settings live in `runtime/<profile>/driver-race.json`: `formation` (default
on), `finish_mode` (`laps` or `time`) and `lap_count`. In endurance the lap count
applies to the final stint lobby only; the relay passes it with the stint switch
that puts the last schedule row on air. A changed layout pick starts a new race,
because distances along another line mean nothing.

Race Control actions (#1113): red flag, restart mode and release, rejoin marks,
corrections and change overrides. Corrections and rejoin marks persist in
`driver-race-actions.json` next to the settings and come back after a relay
restart; every action appends a line with identity and time to
`driver-race-audit.jsonl`. The red flag restart default is a league rule and lives
in the profile (`profiles/<name>/driver-race.json`, `restart`: `keep` or
`neutralise`), so it travels with a profile export. Stdlib only.
"""
import collections
import json
import math
import os
import tempfile
import threading
import time

import atomic_replace
import race_distance

FILENAME = "driver-race.json"
ACTIONS = "driver-race-actions.json"
AUDIT = "driver-race-audit.jsonl"
RESTART_MODES = ("keep", "neutralise")
MAX_REASON = 200
MAX_ENTRY = 120
MAX_CORRECTION_M = 100_000.0
MAX_PIT_CORRECTION = 50
DEFAULTS = {"formation": True, "finish_mode": "laps", "lap_count": None, "delay_s": 0}
MAX_DELAY_S = 120
FASTEST = "driver-race-fastest.json"
SNAPSHOT_EVERY_S = 0.5
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


def read_league(path):
    """The league's red flag restart default; `keep` when unset or unreadable."""
    restart = _read_json(path).get("restart") if path else None
    return {"restart": restart if restart in RESTART_MODES else "keep"}


def write_league(path, changes):
    if changes.get("restart") not in RESTART_MODES:
        raise ValueError("restart is keep or neutralise")
    doc = _read_json(path)
    doc["restart"] = changes["restart"]
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
    """Drop the corrections, rejoin marks and fastest lap (a fresh event, a race reset);
    the audit log stays."""
    for name in (ACTIONS, FASTEST):
        path = os.path.join(runtime_dir, name)
        try:
            atomic_replace.retry_windows_file_operation(lambda p=path: os.remove(p))
        except FileNotFoundError:
            pass  # nothing recorded


def _saved_fastest(path, track):
    """The persisted fastest lap of this track layout, or None."""
    doc = _read_json(path)
    entry, driver, ms = doc.get("entry"), doc.get("driver"), doc.get("ms")
    if doc.get("track") == track and isinstance(entry, str) and isinstance(driver, str) \
            and type(ms) is int and ms > 0 and len(entry) <= MAX_ENTRY and len(driver) <= MAX_ENTRY:
        return {"entry": entry, "driver": driver, "ms": ms}
    return None


def _viewer_entry(row, roster):
    info = roster.get(row["entry"]) or {}
    return {"entry": row["entry"], "number": info.get("number") or "",
            "driver": row["driver"], "team": info.get("cupTeam") or "",
            "color": info.get("bgColor") or "", "text_color": info.get("textColor") or ""}


def qualifying(connections, segment, roster, min_ms=0):
    """Rank the running segment's entries by GT7 best lap: [{pos, entry, number,
    driver, team, color, text_color, best_ms, gap_ms}]; entries without a lap last.
    Only live senders count, and a best lap under `min_ms` (faster than the line
    allows) is ignored."""
    best = {}
    for c in connections:
        if not c.get("live", True):
            continue
        if segment is not None and c.get("segment") not in (segment, None):
            continue
        ms = (c.get("latest") or {}).get("best_ms")
        ms = ms if type(ms) is int and ms > max(0, min_ms) else None
        cur = best.get(c["entry"])
        if cur is None or (ms is not None and (cur[0] is None or ms < cur[0])):
            best[c["entry"]] = (ms, c.get("name") or "")
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
        if isinstance(entry, str) and 0 < len(entry) <= MAX_ENTRY and isinstance(value, list) \
                and len(value) == 2 and isinstance(value[0], (int, float)) \
                and not isinstance(value[0], bool) and math.isfinite(value[0]) \
                and type(value[1]) is int:
            corrections[entry] = (float(value[0]), value[1])
    raw = doc.get("rejoin")
    for entry in (raw if isinstance(raw, list) else ()):
        if isinstance(entry, str) and 0 < len(entry) <= MAX_ENTRY:
            rejoins.append(entry)
    return corrections, rejoins


def _entry_name(entry):
    if not isinstance(entry, str) or not entry.strip() or len(entry) > MAX_ENTRY:
        raise ValueError(f"entry is 1 to {MAX_ENTRY} characters")
    return entry


def _mode(mode):
    if mode not in RESTART_MODES:
        raise ValueError("mode is keep or neutralise")
    return mode


class Service:
    """Thread-safe holder of the live race."""

    def __init__(self, settings_path, pick, trace_for, multi_lobby, clock=time.monotonic,
                 enabled=lambda: True, league_path=None, wall=time.time, context=None):
        """`enabled()` False (the relay in qualifying mode) ignores samples.
        `league_path` holds the profile's restart default; `wall` dates the audit log.
        `context()` gives the dashboard {roster, stint, remaining, mode, segment,
        connections}; every key optional."""
        self._path = settings_path
        self._pick, self._trace_for = pick, trace_for
        self._multi_lobby = multi_lobby
        self._clock = clock
        self._wall = wall
        self._context = context or (lambda: {})
        self._snapshots = collections.deque(maxlen=int(MAX_DELAY_S / SNAPSHOT_EVERY_S) + 4)
        self._saved_fastest = None
        self._line_cache = None
        self._settings_cache = None
        self._league = league_path
        self._last_action = _last_audited(audit_path_for(settings_path))
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
        corrections, rejoins = _saved_actions(_actions_path_for(self._path))
        for entry, (metres, pits) in corrections.items():
            race.correct(entry, metres, pits)
        for entry in rejoins:
            race.rejoin(entry, True)
        race.fastest = _saved_fastest(self._fastest_path(), pick_id)
        self._saved_fastest = race.fastest
        return race

    def _fastest_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(self._path)), FASTEST)

    def save_fastest(self):
        """Persist the race's fastest lap with its layout when it changed (it survives a
        relay restart); None removes the file. Runs outside the race lock."""
        with self._lock:
            fastest = self._race.fastest if self._race is not None else None
            track = self._race_pick
            if fastest == self._saved_fastest:
                return
            self._saved_fastest = None if fastest is None else dict(fastest)
        try:
            if fastest is None:
                os.remove(self._fastest_path())
            else:
                _write_json(self._fastest_path(), dict(fastest, track=track))
        except OSError:
            pass  # the race keeps it in memory; the next change writes it again

    def set_fastest(self, who, entry=None, driver=None, ms=None):
        """Race Control's correction: clear the fastest lap (no entry) or set it."""
        if entry is not None:
            entry = _entry_name(entry)
            if not isinstance(driver, str) or not 0 < len(driver) <= MAX_ENTRY:
                raise ValueError(f"driver is 1 to {MAX_ENTRY} characters")
            if type(ms) is not int or not 0 < ms < 3_600_000:
                raise ValueError("ms is a lap time in milliseconds")
        with self._lock:
            race = self._current()
            race.fastest = None if entry is None else {"entry": entry, "driver": driver, "ms": ms}
            self._log(who, "fastest", **({"entry": entry, "ms": ms} if entry else {"cleared": True}))
        self.save_fastest()
        return self.standings()

    def _viewer(self, ctx):
        """The dashboard payload: an allowlist, safe for viewers and the public page."""
        roster = ctx.get("roster") or {}
        out = {"v": 1, "mode": ctx.get("mode") or "race", "stint": ctx.get("stint"),
               "remaining": ctx.get("remaining"), "track": self._race_pick
               if isinstance(self._race_pick, str) else None}
        race = self._current()
        if out["mode"] == "qualifying":
            out["qualifying"] = qualifying(ctx.get("connections") or [], ctx.get("segment"), roster,
                                           min_ms=0 if race.length is None else int(race.min_lap_s() * 1000))
            return out
        st = race.standings(self._clock())
        settings = self._settings()
        rows = []
        for i, r in enumerate(st["entries"]):
            row = _viewer_entry(r, roster)
            row.update({k: r[k] for k in ("laps", "stint_laps", "gap_s", "interval_s", "lapped",
                                          "status", "pit_stops", "last_lap_ms", "fuel_l",
                                          "finished", "x", "z")})
            row["pos"] = i + 1
            rows.append(row)
        lead_laps = rows[0]["laps"] if rows and st["entries"][0]["distance_m"] is not None else None
        fl = race.fastest
        out.update({
            "state": st["state"], "red_flag": st["red_flag"] is not None,
            "lap": {"leader": None if lead_laps is None else lead_laps + (2 if race.formation else 1),
                    "count": race.lap_count if settings["finish_mode"] == "laps" else None},
            "fastest": None if fl is None else dict(
                fl, team=(roster.get(fl["entry"]) or {}).get("cupTeam") or ""),
            "entries": rows})
        return out

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
        self.save_fastest()

    def dashboard(self):
        """The newest snapshot at least the delay setting old; the oldest one while the
        delay has not passed yet."""
        delay = self._settings()["delay_s"]
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
            return chosen

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
            self._current().update(reg["entry"], reg["conn"], reg["name"],
                                   dict(sample, t=sample.get("mono", self._clock())))

    def standings(self):
        with self._lock:
            race = self._current()
            out = race.standings(self._clock())
            pick_id = self._race_pick
        out.update(settings=read_settings(self._path), track=pick_id,
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

    def override_change(self, who, entry, conn):
        entry = _entry_name(entry)
        with self._lock:
            done = self._current().override_change(entry, conn, self._clock())
            if not done:
                raise ValueError("that connection is not a sender of this entry")
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

    def final_lap(self, who="tailnet"):
        with self._lock:
            self._current().final_lap(self._clock())
            self._log(who, "final_lap")

    def finish(self, who="tailnet"):
        with self._lock:
            self._current().finish(self._clock())
            self._log(who, "finish")

    def reset(self, who="tailnet"):
        """Start the race over: its corrections and rejoin marks go with it."""
        with self._lock:
            self._log(who, "reset")
            clear_actions(os.path.dirname(os.path.abspath(self._path)))
            self._final_lobby_laps = None
            self._race = self._build(self._pick()["id"] if self._pick() else None)

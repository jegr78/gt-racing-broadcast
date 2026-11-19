"""Per-event race recording: one SQLite file per event plus a bounded writer thread.

Stdlib only. A pointer file names the current recording; `rotate` clears it so the
next `open_current` starts a fresh file. Samples are one JSON document per second."""
import datetime
import json
import logging
import os
import queue
import sqlite3
import threading

import atomic_replace
from race_distance import NO_DATA_S

POINTER = "race-recording.json"
_SAMPLE_COLUMNS = ["state", "red_flag"]
_ROW_COLUMNS = ["pos", "entry", "driver", "laps", "gap_time_s", "interval_time_s", "lapped",
                "status", "finished", "compound", "pit_stops", "correction_m", "rejoin",
                "gt7_position", "position_mismatch", "class", "class_pos"]
CSV_HEADER = ["time_utc"] + _SAMPLE_COLUMNS + _ROW_COLUMNS
SAMPLE_EVERY_S = 1.0
COUNTING_STATES = ("racing", "final_lap", "finishing")
SAMPLE_FIELDS = ("entry", "driver", "laps", "gap_time_s", "interval_time_s", "lapped",
                 "status", "finished", "pit_stops", "correction_m", "rejoin",
                 "gt7_position", "position_mismatch")
DECISIONS = {"confirm": "confirmed", "dismiss": "dismissed"}
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")
_log = logging.getLogger("racecast.relay")   # the relay attaches its log file to this logger

_SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (t REAL PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def rotate(runtime_dir):
    """Forget the current recording so the next open_current starts a new file."""
    try:
        os.remove(os.path.join(runtime_dir, POINTER))
    except FileNotFoundError:
        pass  # no current recording


def _read_pointer(runtime_dir):
    try:
        with open(os.path.join(runtime_dir, POINTER), encoding="utf-8") as f:
            name = json.load(f).get("file")
    except (OSError, ValueError, AttributeError):
        return None
    if isinstance(name, str) and name == os.path.basename(name):
        return name
    return None


def open_current(runtime_dir, wall):
    """Open the recording the pointer names, or create a new file and pointer."""
    os.makedirs(runtime_dir, exist_ok=True)
    name = _read_pointer(runtime_dir)
    if name is None:
        stamp = datetime.datetime.fromtimestamp(wall, datetime.timezone.utc)
        stem = "race-recording-%s" % stamp.strftime("%Y%m%d-%H%M%S")
        name, n = stem + ".sqlite", 1
        while os.path.exists(os.path.join(runtime_dir, name)):
            n += 1
            name = "%s-%d.sqlite" % (stem, n)
        tmp = os.path.join(runtime_dir, POINTER + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"file": name}, f)
        atomic_replace.replace(tmp, os.path.join(runtime_dir, POINTER))
    return Store(os.path.join(runtime_dir, name))


class Store:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def add_sample(self, t_wall, doc):
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO samples (t, doc) VALUES (?,?)",
                               (t_wall, json.dumps(doc)))
            self._conn.commit()

    def sample_at(self, t_wall):
        """The sample nearest t_wall as {"t": ..., **doc}, or None when empty."""
        with self._lock:
            below = self._conn.execute(
                "SELECT t, doc FROM samples WHERE t<=? ORDER BY t DESC LIMIT 1",
                (t_wall,)).fetchone()
            above = self._conn.execute(
                "SELECT t, doc FROM samples WHERE t>? ORDER BY t ASC LIMIT 1",
                (t_wall,)).fetchone()
        if below is None and above is None:
            return None
        if below is None:
            pick = above
        elif above is None or t_wall - below[0] <= above[0] - t_wall:
            pick = below
        else:
            pick = above
        return {"t": pick[0], **json.loads(pick[1])}

    def span(self):
        with self._lock:
            lo, hi = self._conn.execute("SELECT MIN(t), MAX(t) FROM samples").fetchone()
        return None if lo is None else (lo, hi)

    def add_event(self, ev):
        """Insert ev and return its row id; an "id" key in ev picks the id."""
        with self._lock:
            cur = self._conn.execute("INSERT INTO events (id, doc) VALUES (?,?)",
                                     (ev.get("id"), json.dumps(ev)))
            self._conn.commit()
            return cur.lastrowid

    def update_event(self, id, **fields):
        with self._lock:
            row = self._conn.execute("SELECT doc FROM events WHERE id=?", (id,)).fetchone()
            if row is None:
                return
            doc = json.loads(row[0])
            doc.update(fields)
            self._conn.execute("UPDATE events SET doc=? WHERE id=?", (json.dumps(doc), id))
            self._conn.commit()

    def events(self):
        """All events, newest first, each with its row id as "id"."""
        with self._lock:
            rows = self._conn.execute("SELECT id, doc FROM events ORDER BY id DESC").fetchall()
        return [{**json.loads(doc), "id": i} for i, doc in rows]

    def set_meta(self, key, value):
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)",
                               (key, value))
            self._conn.commit()

    def meta(self, key):
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return None if row is None else row[0]

    def csv_rows(self):
        """Header, then one row per (sample, entry), oldest sample first."""
        yield list(CSV_HEADER)
        with self._lock:
            cur = self._conn.execute("SELECT t, doc FROM samples ORDER BY t")
            samples = cur.fetchall()
        for t, raw in samples:
            stamp = datetime.datetime.fromtimestamp(t, datetime.timezone.utc)
            when = stamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            doc = json.loads(raw)
            head = [when, _cell(doc.get("state")), _red_flag_cell(doc.get("red_flag"))]
            for r in doc.get("rows") or []:
                yield head + [_cell(r.get(c)) for c in _ROW_COLUMNS]

    def close(self):
        with self._lock:
            self._conn.close()


def _red_flag_cell(red):
    """The red flag mode, "on" for a flag without one, empty without a flag."""
    if not red:
        return ""
    mode = red.get("mode") if isinstance(red, dict) else None
    return _cell(mode) if isinstance(mode, str) and mode else "on"


def _cell(v):
    """Prefix text a spreadsheet would run as a formula; names come from driver machines."""
    if isinstance(v, str) and v.startswith(_FORMULA_LEAD):
        return "'" + v
    return v


class Writer:
    """Daemon thread that runs queued jobs `fn(store)` in order. put() never blocks:
    a full queue drops the job and counts it in `dropped`. The first failed job is
    logged, every one counts in `failed`."""

    def __init__(self, store, maxsize=120):
        self.store = store
        self.dropped = 0
        self.failed = 0
        self._q = queue.Queue(maxsize)
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._run, name="race-recording", daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stopping.is_set():
            fn = self._q.get()
            try:
                if fn is None:
                    return
                fn(self.store)
            except Exception as e:
                self.failed += 1
                if self.failed == 1:
                    _log.warning("race recording write failed: %s", e)
            finally:
                self._q.task_done()

    def put(self, fn):
        if self._stopping.is_set():
            return False
        try:
            self._q.put_nowait(fn)
            return True
        except queue.Full:
            self.dropped += 1
            return False

    def flush(self, timeout=5.0):
        """Wait until queued jobs are done; True when they finished in time."""
        done = threading.Event()
        threading.Thread(target=lambda: (self._q.join(), done.set()), daemon=True).start()
        return done.wait(timeout)

    def stop(self):
        """End the thread after its current job; queued jobs are dropped."""
        self._stopping.set()
        try:
            self._q.put_nowait(None)   # wakes a thread waiting on an empty queue
        except queue.Full:
            pass  # the thread is busy and sees the flag after this job
        self._thread.join(timeout=5.0)


class Recorder:
    """Turns live standings into one sample per second and disconnect events.

    `wall` is a clock callable for manual actions. Called from the tick thread and
    from request threads; every store write goes through the Writer."""

    def __init__(self, store, writer, wall):
        self._store = store
        self._writer = writer
        self._lock = threading.Lock()
        self._running = store.meta("running") == "1"
        self._started_by = store.meta("started_by")
        started_at = store.meta("started_at")
        self._started_at = float(started_at) if started_at else None
        self._auto_done = store.meta("auto_started") == "1"
        self._span = store.span()
        self._last_sample = None
        self._last_state = None   # None until the first observe, which is a baseline
        self._events = {e["id"]: e for e in store.events()}
        self._next_id = max(self._events, default=0) + 1
        # The event of each entry whose silence has not ended yet.
        self._silent = {e.get("entry"): e["id"] for e in self._events.values()
                        if e.get("returned") is None}
        self._wall = wall

    def _meta(self, key, value):
        self._writer.put(lambda s: s.set_meta(key, value))

    def _begin(self, who, now):
        self._running = True
        self._started_by = who
        self._started_at = now
        self._meta("running", "1")
        self._meta("started_by", who)
        self._meta("started_at", repr(now))

    def _end(self):
        self._running = False
        self._meta("running", "0")

    def start(self, who):
        """Start recording by hand; a recording that already runs keeps its starter."""
        with self._lock:
            if not self._running:
                self._begin(who, self._wall())
            return self._status_head()

    def stop(self, who):
        with self._lock:
            if self._running:
                self._end()
                self._meta("stopped_by", who)
            return self._status_head()

    def observe(self, standings, compounds, now_wall, classes=None):
        """`compounds` is {entry: code} or a callable giving it, read only when a sample is due.
        `classes` is the viewer's [{name, color, lap, laps}], kept only in class mode."""
        with self._lock:
            state = standings.get("state")
            if state in COUNTING_STATES and not self._auto_done:
                self._auto_done = True
                self._meta("auto_started", "1")
                if not self._running:
                    self._begin("auto", now_wall)
            if (state == "finished" and self._last_state not in (None, "finished")
                    and self._running):
                self._end()
                self._auto_done = False          # the next race starts recording again
                self._meta("auto_started", "0")
            self._last_state = state
            if self._running and self._sample_due(now_wall):
                doc = _sample(standings, (compounds() if callable(compounds) else compounds) or {}, classes)
                if self._writer.put(lambda s: s.add_sample(now_wall, doc)):
                    lo, hi = self._span or (now_wall, now_wall)
                    self._span = (min(lo, now_wall), max(hi, now_wall))
            self._detect(standings, now_wall)

    def _sample_due(self, now):
        """Keep a one-second grid; a clock that went back or jumped ahead starts a new one."""
        last = self._last_sample
        if last is None or now < last or now - last > 2 * SAMPLE_EVERY_S:
            self._last_sample = now
            return True
        if now - last >= SAMPLE_EVERY_S:
            self._last_sample = last + SAMPLE_EVERY_S
            return True
        return False

    def _detect(self, standings, now):
        """Close silences whose data came back; only a running recording opens new ones."""
        may_suspect = self._running and standings.get("red_flag") is None
        window = bool(standings.get("change_window"))
        for row in standings.get("entries") or []:
            entry, quiet = row.get("entry"), row.get("quiet_s")
            if quiet is None:
                continue
            eid = self._silent.get(entry)
            if eid is not None:
                if quiet <= NO_DATA_S:
                    self._returned(eid, entry, now)
                continue
            if (quiet > NO_DATA_S and may_suspect and row.get("distance_m") is not None
                    and not row.get("finished") and not row.get("waiting")
                    and row.get("status") != "retired"
                    and not (row.get("in_box") and window)):
                self._suspect(row, quiet, now)

    def _suspect(self, row, quiet, now):
        ev = {"id": self._next_id, "entry": row.get("entry"), "driver": row.get("driver"),
              "state": "suspected", "last_packet": round(now - quiet, 3), "opened": now,
              "returned": None, "dropout_s": None, "decided_by": None, "decided_at": None}
        self._next_id += 1
        self._events[ev["id"]] = ev
        self._silent[ev["entry"]] = ev["id"]
        snapshot = dict(ev)
        self._writer.put(lambda s: s.add_event(snapshot))

    def _returned(self, eid, entry, now):
        del self._silent[entry]
        ev = self._events[eid]
        fields = {"returned": now, "dropout_s": round(now - ev["last_packet"], 3)}
        if ev["state"] == "suspected":
            fields["state"] = "dropout"
        self._update(ev, fields)

    def _update(self, ev, fields):
        ev.update(fields)
        self._writer.put(lambda s: s.update_event(ev["id"], **fields))

    def decide(self, who, event_id, decision):
        """Confirm or dismiss an event; ValueError for a bad decision, KeyError for an unknown id."""
        if not isinstance(decision, str) or decision not in DECISIONS:
            raise ValueError("decision must be confirm or dismiss")
        with self._lock:
            ev = self._events.get(event_id)
            if ev is None:
                raise KeyError(event_id)
            self._update(ev, {"state": DECISIONS[decision], "decided_by": who,
                              "decided_at": self._wall()})
            return dict(ev)

    def confirmed_silent(self):
        """Entries still quiet whose silence Race Control confirmed as a disconnect."""
        with self._lock:
            return {e for e, i in self._silent.items() if self._events[i]["state"] == "confirmed"}

    def sample_at(self, t_wall):
        return self._store.sample_at(t_wall)

    def csv_rows(self):
        return self._store.csv_rows()

    def _status_head(self):
        return {"running": self._running, "started_by": self._started_by,
                "started_at": self._started_at}

    def status(self):
        with self._lock:
            out = self._status_head()
            out["available"] = True
            out["events"] = [dict(self._events[i]) for i in sorted(self._events, reverse=True)]
            out["span"] = self._span
        out["dropped"] = self._writer.dropped
        out["failed"] = self._writer.failed
        return out


def _sample(standings, compounds, classes=None):
    in_classes = bool(standings.get("classes"))
    rows = []
    for i, r in enumerate(standings.get("entries") or []):
        row = {"pos": i + 1, **{k: r.get(k) for k in SAMPLE_FIELDS}}
        row["compound"] = compounds.get(r.get("entry"))
        if in_classes:
            row["class"] = r.get("class")
            for k in ("class_pos", "class_gap_s", "class_interval_s", "class_lapped"):
                row[k] = r.get(k)
        rows.append(row)
    doc = {"state": standings.get("state"), "red_flag": standings.get("red_flag"),
           "rows": rows}
    if in_classes and classes:
        doc["classes"] = [{k: c.get(k) for k in ("name", "color", "lap", "laps")} for c in classes]
    return doc

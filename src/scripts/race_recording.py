"""Per-event race recording: one SQLite file per event plus a bounded writer thread.

Stdlib only. A pointer file names the current recording; `rotate` clears it so the
next `open_current` starts a fresh file. Samples are one JSON document per second."""
import datetime
import json
import os
import queue
import sqlite3
import threading

import atomic_replace

POINTER = "race-recording.json"
CSV_HEADER = ["time_utc", "pos", "entry", "driver", "laps", "gap_time_s",
              "interval_time_s", "lapped", "status", "compound", "pit_stops",
              "correction_m", "rejoin", "gt7_position"]

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
        pass


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
        name = "race-recording-%s.sqlite" % stamp.strftime("%Y%m%d-%H%M%S")
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
        self._conn.execute("PRAGMA busy_timeout=5000")
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
        with self._lock:
            cur = self._conn.execute("INSERT INTO events (doc) VALUES (?)",
                                     (json.dumps(ev),))
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
            for r in json.loads(raw).get("rows") or []:
                yield [when] + [r.get(c) for c in CSV_HEADER[1:]]

    def close(self):
        with self._lock:
            self._conn.close()


class Writer:
    """Daemon thread that runs queued jobs `fn(store)` in order. put() never blocks:
    a full queue drops the job and counts it in `dropped`."""

    def __init__(self, store, maxsize=120):
        self.store = store
        self.dropped = 0
        self.failed = 0
        self._q = queue.Queue(maxsize)
        self._thread = threading.Thread(target=self._run, name="race-recording", daemon=True)
        self._thread.start()

    def _run(self):
        while True:
            fn = self._q.get()
            try:
                if fn is None:
                    return
                fn(self.store)
            except Exception:
                self.failed += 1
            finally:
                self._q.task_done()

    def put(self, fn):
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
        try:
            self._q.put(None, timeout=2.0)
        except queue.Full:
            return
        self._thread.join(timeout=5.0)

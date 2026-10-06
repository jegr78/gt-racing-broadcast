"""Pending Director Panel sheet saves (spec 2026-10-06-async-panel-sheet-saves).

A panel save is live in the relay at once and reaches the sheet in the
background. This store holds one entry per (target, row) until the sheet shows
the saved value. Pure and thread-safe; the relay owns the worker and the HTTP.
"""
import threading
import time

SAVING = "saving"
LOCAL = "local"
CONFIRMED = "confirmed"
# A confirmed entry stays pinned until the sheet CSV shows it; this bounds the
# wait, so a row cannot stay pinned when the sheet stores a value differently.
CONFIRM_GRACE_S = 120.0

_FIELDS = ("url", "name", "stint")


class _Entry:
    __slots__ = ("fields", "rev", "state", "err", "hold", "confirmed_at")

    def __init__(self):
        self.fields = {}
        self.rev = 0
        self.state = SAVING
        self.err = None
        self.hold = False
        self.confirmed_at = None


class PendingSaves:
    def __init__(self, now=time.monotonic):
        self._now = now
        self._lock = threading.Lock()
        self._entries = {}          # (target, row) -> _Entry
        self._queue = []            # keys waiting for a push, in order
        self._inflight = set()

    def put(self, target, row, fields):
        key = (target, int(row))
        with self._lock:
            e = self._entries.get(key) or _Entry()
            e.fields.update({k: v for k, v in fields.items() if k in _FIELDS})
            e.rev += 1
            e.state, e.err, e.hold, e.confirmed_at = SAVING, None, False, None
            self._entries[key] = e
            if key not in self._queue:
                self._queue.append(key)
            return e.rev

    def next_job(self):
        with self._lock:
            for key in self._queue:
                if key in self._inflight:
                    continue
                self._queue.remove(key)
                self._inflight.add(key)
                e = self._entries[key]
                return key[0], key[1], dict(e.fields), e.rev
            return None

    def done(self, target, row, rev, ok, err=None, hold=False):
        key = (target, int(row))
        with self._lock:
            self._inflight.discard(key)
            e = self._entries.get(key)
            if e is None:
                return None
            if rev != e.rev:
                return e.state            # a newer save is queued; it decides
            if ok:
                e.state, e.err, e.confirmed_at = CONFIRMED, None, self._now()
            else:
                e.state, e.err, e.hold = LOCAL, err, bool(hold)
            return e.state

    def _requeue_locked(self, key, e):
        e.state, e.hold = SAVING, False
        if key not in self._queue:
            self._queue.append(key)

    def requeue_local(self):
        with self._lock:
            n = 0
            for key, e in self._entries.items():
                if e.state == LOCAL and not e.hold:
                    self._requeue_locked(key, e)
                    n += 1
            return n

    def requeue(self, target, row):
        key = (target, int(row))
        with self._lock:
            e = self._entries.get(key)
            if e is None or e.state != LOCAL:
                return False
            self._requeue_locked(key, e)
            return True

    def overlay(self, target):
        with self._lock:
            return {row: dict(e.fields) for (t, row), e in self._entries.items()
                    if t == target}

    def reconcile(self, target, sheet_rows):
        now = self._now()
        dropped = []
        with self._lock:
            for (t, row), e in list(self._entries.items()):
                if t != target or e.state != CONFIRMED:
                    continue
                shown = dict(zip(_FIELDS, sheet_rows.get(row, ("", "", "")), strict=True))
                matches = all(shown.get(k, "") == v for k, v in e.fields.items())
                if matches or now - e.confirmed_at >= CONFIRM_GRACE_S:
                    del self._entries[(t, row)]
                    dropped.append(row)
        return sorted(dropped)

    def state(self, target, row):
        with self._lock:
            e = self._entries.get((target, int(row)))
            if e is None or e.state == CONFIRMED:
                return None, None
            if e.state == LOCAL:
                return LOCAL, e.err
            return SAVING, None

    def expire(self, target):
        """Drop confirmed entries of *target* whose grace has passed, without a
        sheet to compare (a fetch that returned nothing, e.g. an emptied tab)."""
        now = self._now()
        dropped = []
        with self._lock:
            for (t, row), e in list(self._entries.items()):
                if (t == target and e.state == CONFIRMED
                        and now - e.confirmed_at >= CONFIRM_GRACE_S):
                    del self._entries[(t, row)]
                    dropped.append(row)
        return sorted(dropped)

    def retryable(self):
        """`local` entries a recovery may push again (not held by an outdated script)."""
        with self._lock:
            return sum(1 for e in self._entries.values() if e.state == LOCAL and not e.hold)

    def states(self, target):
        """row -> (sync, err) for every unconfirmed entry of *target*, including
        a save that emptied its row (the row is then gone from the source)."""
        with self._lock:
            return {row: (LOCAL if e.state == LOCAL else SAVING,
                          e.err if e.state == LOCAL else None)
                    for (t, row), e in self._entries.items()
                    if t == target and e.state != CONFIRMED}

    def unconfirmed(self):
        """Entries the sheet has not confirmed yet (saving or local)."""
        with self._lock:
            return sum(1 for e in self._entries.values() if e.state != CONFIRMED)

    def unsynced(self):
        with self._lock:
            local = [e for e in self._entries.values() if e.state == LOCAL]
            return len(local), (local[-1].err if local else None)

    def has_queued(self):
        with self._lock:
            return bool(self._queue)

    def __len__(self):
        with self._lock:
            return len(self._entries)

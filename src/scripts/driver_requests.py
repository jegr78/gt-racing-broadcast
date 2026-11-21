"""Driver registrations waiting for Race Control's approval.

A registration on the event invite becomes a pending request. The ingest refuses
its code until Race Control or the director approves it, optionally with a corrected
entry, stint and name; a rejection revokes the code for good. Requests belong to one
invite: a renewed link starts empty. The file lives in the profile's runtime
directory, so decisions survive a relay restart. Stdlib only.
"""
import collections
import copy
import json
import os
import tempfile
import threading
import time

import atomic_replace
import driver_invite

FILENAME = "driver-requests.json"
MAX_PENDING = 200             # a safety bound; the per-client caps keep a flood to itself
MAX_PENDING_PER_SLOT = 3      # waiting requests of one client for one entry and stint
MAX_PENDING_PER_CLIENT = 8
MAX_EVICTED = 500             # dropped conns remembered, so their sender hears why (each kind)
STATES = ("pending", "approved", "rejected")


class Full(ValueError):
    """Too many open requests: someone has to decide first."""


class Decided(ValueError):
    """The request's status does not allow this decision."""


def path_in(runtime_dir):
    return os.path.join(runtime_dir, FILENAME)


def clear(path):
    try:
        atomic_replace.retry_windows_file_operation(lambda: os.remove(path))
    except FileNotFoundError:
        pass  # no requests to drop


def _valid(rec):
    if not isinstance(rec, dict) or rec.get("status") not in STATES:
        return False
    conn = rec.get("conn")
    if not isinstance(conn, str) or not 8 <= len(conn) <= 32:
        return False
    reg = rec.get("registered")
    if not isinstance(reg, dict):
        return False
    for doc in (rec, reg):
        if driver_invite._problem(doc.get("entry"), doc.get("name"), doc.get("segment"),
                                  driver_invite.KNOWN_NAME):
            return False
    if isinstance(rec.get("ts"), bool) or not isinstance(rec.get("ts"), (int, float)):
        return False
    if not isinstance(rec.get("client", ""), str) or len(rec.get("client", "")) > 64:
        return False
    return rec.get("who") is None or isinstance(rec.get("who"), str)


def _record(rec):
    reg = rec["registered"]
    return {"conn": rec["conn"], "entry": rec["entry"], "segment": rec.get("segment"),
            "name": rec["name"], "status": rec["status"], "ts": rec["ts"],
            "registered": {"entry": reg["entry"], "segment": reg.get("segment"), "name": reg["name"]},
            "who": rec.get("who"), "decided": rec.get("decided"), "client": rec.get("client", "")}


class Store:
    """The requests of the current invite, keyed by the code's connection id.
    Thread-safe; the relay is the only writer, so the file is read once."""
    MAX_KEPT = 400

    def __init__(self, path, invite_id, wall=time.time):
        self.path = path
        self._invite_id = invite_id
        self._wall = wall
        self._lock = threading.Lock()
        self._loaded = False
        self._invite = None
        self._reqs = {}          # conn -> record, oldest first
        self._evicted = {}       # conn -> None, oldest first
        self._disconnected = {}  # conns the producer disconnected, oldest first

    def _current(self):
        """Bind to the current invite (caller holds the lock); False without one."""
        invite = self._invite_id()
        if not self._loaded:
            self._loaded = True
            try:
                with open(self.path, encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (OSError, ValueError):
                doc = None
            if isinstance(doc, dict) and isinstance(doc.get("requests"), list):
                self._invite = doc.get("invite")
                self._reqs = {r["conn"]: _record(r) for r in doc["requests"] if _valid(r)}
                self._evicted, self._disconnected = (
                    dict.fromkeys(c for c in (gone if isinstance(gone, list) else [])
                                  if isinstance(c, str) and len(c) <= 32)
                    for gone in (doc.get("evicted"), doc.get("disconnected")))
        if invite != self._invite:
            self._invite, self._reqs, self._evicted, self._disconnected = invite, {}, {}, {}
        return invite is not None

    def _commit(self, snapshot, evicted=None):
        """Write the change; on a failed write memory goes back to `snapshot`."""
        try:
            self._save()
        except OSError:
            self._reqs = snapshot
            if evicted is not None:
                self._evicted = evicted
            raise

    def _evict(self, conn):
        del self._reqs[conn]
        self._evicted.pop(conn, None)
        self._evicted[conn] = None
        while len(self._evicted) > MAX_EVICTED:
            del self._evicted[next(iter(self._evicted))]

    def was_evicted(self, conn):
        """True for a request a cap dropped under the current invite."""
        with self._lock:
            return self._current() and conn in self._evicted

    def disconnect(self, conn):
        """Refuse `conn` under the current invite from now on, also after a restart (the
        producer's disconnect). False for an unknown request; raises OSError when the
        file cannot be written, leaving memory as it was."""
        with self._lock:
            if not self._current() or conn not in self._reqs:
                return False
            before = dict(self._disconnected)
            self._disconnected.pop(conn, None)
            self._disconnected[conn] = None
            while len(self._disconnected) > MAX_EVICTED:
                del self._disconnected[next(iter(self._disconnected))]
            try:
                self._save()
            except OSError:
                self._disconnected = before
                raise
            return True

    def was_disconnected(self, conn):
        """True for a code the producer disconnected under the current invite."""
        with self._lock:
            return self._current() and conn in self._disconnected

    def _save(self):
        folder = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".driver-requests-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"invite": self._invite, "requests": list(self._reqs.values()),
                           "evicted": list(self._evicted),
                           "disconnected": list(self._disconnected)}, fh)
            atomic_replace.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def get(self, conn):
        with self._lock:
            if not self._current():
                return None
            rec = self._reqs.get(conn)
            return copy.deepcopy(rec) if rec else None

    def all(self):
        with self._lock:
            if not self._current():
                return []
            return copy.deepcopy(list(self._reqs.values()))

    def add(self, reg, client=""):
        """The request of a verified code (`driver_invite.verify`) from `client`; an
        existing one is returned unchanged. A client over its own cap, for one entry
        and stint or in all, loses its own oldest waiting request, never another
        client's. Raises Full while too many wait in all or the file is full."""
        with self._lock:
            if not self._current():
                raise Full("no event is running")
            if reg["conn"] in self._reqs:
                return copy.deepcopy(self._reqs[reg["conn"]])
            snapshot, evicted = copy.deepcopy(self._reqs), dict(self._evicted)
            client = str(client)[:64]
            slot = (reg["entry"], reg["segment"])
            own = [c for c, r in self._reqs.items() if r["status"] == "pending" and r["client"] == client]
            same = [c for c in own if (self._reqs[c]["entry"], self._reqs[c]["segment"]) == slot]
            for c in same[:max(0, len(same) - MAX_PENDING_PER_SLOT + 1)]:
                self._evict(c)
                own.remove(c)
            for c in own[:max(0, len(own) - MAX_PENDING_PER_CLIENT + 1)]:
                self._evict(c)
            waiting = [c for c, r in self._reqs.items() if r["status"] == "pending"]
            while len(waiting) >= MAX_PENDING:
                # The client holding the most loses its oldest, so a flood displaces itself.
                counts = collections.Counter(self._reqs[c]["client"] for c in waiting)
                top = max(counts.values())
                victim = next(c for c in waiting if counts[self._reqs[c]["client"]] == top)
                self._evict(victim)
                waiting.remove(victim)
            while len(self._reqs) >= self.MAX_KEPT:
                old = next((c for c, r in self._reqs.items() if r["status"] == "rejected"), None)
                if old is None:
                    raise Full("too many registrations for this event")
                del self._reqs[old]
            fields = {k: reg[k] for k in ("entry", "segment", "name")}
            rec = dict(fields, conn=reg["conn"], status="pending", ts=self._wall(),
                       registered=dict(fields), who=None, decided=None, client=client)
            self._reqs[reg["conn"]] = rec
            self._commit(snapshot, evicted)
            return copy.deepcopy(rec)

    def _decide(self, conn, who, allowed, status, fields):
        with self._lock:
            rec = self._reqs.get(conn) if self._current() else None
            if rec is None:
                raise KeyError(conn)
            if rec["status"] not in allowed:
                raise Decided(f"this request is {rec['status']}")
            if fields and driver_invite._problem(fields["entry"], fields["name"], fields["segment"]):
                raise ValueError(driver_invite._problem(fields["entry"], fields["name"], fields["segment"]))
            before, snapshot = copy.deepcopy(rec), copy.deepcopy(self._reqs)
            rec.update(fields, status=status, who=str(who)[:80], decided=self._wall())
            self._commit(snapshot)
            return before, copy.deepcopy(rec)

    def approve(self, conn, who, entry, segment, name):
        """(before, after) of a pending request, approved with these values."""
        return self._decide(conn, who, ("pending",), "approved",
                            {"entry": entry, "segment": segment, "name": name})

    def correct(self, conn, who, entry, segment, name):
        """(before, after) of an approved request with corrected values."""
        return self._decide(conn, who, ("approved",), "approved",
                            {"entry": entry, "segment": segment, "name": name})

    def reject_pending(self, who):
        """Reject every waiting request; returns them as they were."""
        with self._lock:
            if not self._current():
                return []
            now, gone, snapshot = self._wall(), [], copy.deepcopy(self._reqs)
            for rec in self._reqs.values():
                if rec["status"] == "pending":
                    gone.append(copy.deepcopy(rec))
                    rec.update(status="rejected", who=str(who)[:80], decided=now)
            if gone:
                self._commit(snapshot)
            return gone

    def reject(self, conn, who):
        """The request before its rejection; its code is refused from now on."""
        return self._decide(conn, who, ("pending", "approved"), "rejected", {})[0]

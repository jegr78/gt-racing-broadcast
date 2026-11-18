"""The driver telemetry ingest behind the event invite (#1111).

A driver's sender posts the encrypted GT7 packets of one registration (see
`driver_invite`) in batch format v1 (`telemetry_probe.decode_batch`). The hub counts
them per connection, keeps the latest decoded packet and enforces the connection
limit: one live sender per entry in a single-lobby race, two per team in endurance
(the driver on track and the one waiting in the next lobby). A second registration
for a full entry is refused while the others are live, not swapped in; a sender
that reconnects with its own code is the same connection. Live data only, nothing
is written to disk. The HTTP contract is `src/docs/design/driver-telemetry-ingest.md`.
"""
import logging
import threading
import time

import telemetry_probe

LOG = logging.getLogger("racecast.relay")

CONTRACT = 1
RATE_HZ = 20
LIVE_S = 20.0         # a connection without a batch for this long frees its entry slot
FORGET_S = 600.0      # ... and after this long it leaves the lists entirely
MAX_CONNECTIONS = 48


class IngestError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Hub:
    """Connections keyed by the code's connection id. Thread-safe."""

    def __init__(self, multi_lobby, clock=time.monotonic, wall=time.time, rate_hz=RATE_HZ,
                 max_connections=MAX_CONNECTIONS, on_sample=None, invite_id=None):
        """`on_sample(reg, sample)` receives every decoded sample with its connection's
        {conn, entry, segment, name}, outside every lock; `sample["mono"]` is the
        receive time on `clock`. `invite_id()` returns the current invite's id; the hub
        resets when it changes, since a new invite revokes every code."""
        self._multi_lobby = multi_lobby
        self._clock = clock
        self.rate_hz = rate_hz
        self._max = max_connections
        self._on_sample = on_sample
        self._counts = telemetry_probe.Probe(None, clock=clock, wall=wall, rate_hz=rate_hz,
                                             max_senders=max_connections, samples=1,
                                             on_sample=self._sample if on_sample else None)
        self._lock = threading.Lock()
        self._conns = {}        # conn -> {entry, segment, name, last}
        self._dropped = {}         # conn -> None, oldest first
        self._hook_failed = set()
        self._reassigned = {}      # conn -> entry the producer moved it to
        self._invite_id = invite_id
        self._bound_invite = invite_id() if invite_id else None

    def _follow_invite(self):
        if self._invite_id is None:
            return
        current = self._invite_id()
        with self._lock:
            changed = current != self._bound_invite
            self._bound_invite = current
            if changed:     # in the same lock, so no new code's first batch lands before the clear
                gone = list(self._conns)
                self._clear()
        if changed:
            for conn in gone:
                self._counts.forget(conn)

    def _forget_quiet(self, now):
        """Drop connections quiet for FORGET_S. A reassignment outlives the sweep, since
        the sender's code still names the old entry. Caller holds the lock."""
        gone = [c for c, m in self._conns.items() if now - m["last"] >= FORGET_S]
        for conn in gone:
            del self._conns[conn]
            self._hook_failed.discard(conn)
        return gone

    def _live(self, now):
        return {c: m for c, m in self._conns.items() if now - m["last"] < LIVE_S}

    def ingest(self, reg, body, sender_stats):
        conn = reg["conn"]
        self._follow_invite()
        now = self._clock()
        try:
            packets = telemetry_probe.decode_batch(body)    # cheap; refuse junk before a slot
        except telemetry_probe.ProbeError as exc:
            raise IngestError(str(exc), exc.status) from None
        del packets
        with self._lock:
            forgotten = self._forget_quiet(now)
            if conn in self._dropped:
                raise IngestError("disconnected by the producer: register again on the invite page", 410)
            entry = self._reassigned.get(conn, reg["entry"])
            live = self._live(now)
            if conn not in live:
                # A known sender that went quiet competes for its slot like a new one.
                if conn not in self._conns and len(self._conns) >= self._max:
                    for stale in [c for c in self._conns if c not in self._live(now)]:
                        del self._conns[stale]
                        self._counts.forget(stale)
                if conn not in self._conns and len(self._conns) >= self._max:
                    raise IngestError("too many senders for this event", 429)
                allowed = 2 if self._multi_lobby() else 1
                peers = [m for c, m in live.items() if m["entry"] == entry and c != conn]
                if len(peers) >= allowed:
                    raise IngestError(
                        "this team already has two senders" if allowed == 2
                        else "this entry already has a sender", 409)
            # Hold the slot now; decryption runs outside the lock (see Probe.ingest).
            held = self._conns.get(conn)
            self._conns[conn] = {"entry": entry, "segment": reg.get("segment"),
                                 "name": reg["name"], "last": now}
        for stale in forgotten:
            self._counts.forget(stale)
        try:
            reply = self._counts.ingest(conn, body, sender_stats)
        except telemetry_probe.ProbeError as exc:
            with self._lock:
                if held is None:
                    self._conns.pop(conn, None)
            raise IngestError(str(exc), exc.status) from None
        with self._lock:
            gone = conn in self._dropped or conn not in self._conns
        if gone:                     # disconnected or reset while this batch decrypted
            self._counts.forget(conn)
            raise IngestError("disconnected by the producer: register again on the invite page", 410)
        return {"ok": True, "v": CONTRACT, "rate_hz": self.rate_hz, "accepted": reply["accepted"]}

    def _sample(self, conn, sample):
        with self._lock:
            meta = self._conns.get(conn)
            reg = dict(meta, conn=conn) if meta and conn not in self._dropped else None
        if reg is not None:
            reg.pop("last", None)
            try:
                self._on_sample(reg, sample)
            except Exception as exc:  # noqa: BLE001  the race must never break a driver's upload
                with self._lock:
                    first = conn not in self._hook_failed
                    if first and len(self._hook_failed) < 4 * self._max:
                        self._hook_failed.add(conn)
                if first:
                    # The connection id comes from a driver's code: no line breaks in the log.
                    safe = str(conn).replace("\r", "").replace("\n", "")[:40]
                    LOG.warning("driver race: a sample of %s failed (%s); further failures "
                                "of this connection are not logged", safe, type(exc).__name__)

    def entry_for(self, conn, entry):
        """The entry `conn` counts for: the producer's reassignment, else `entry`."""
        with self._lock:
            return self._reassigned.get(conn, entry)

    def entry_full(self, entry):
        """True while `entry` already has as many live senders as the race allows."""
        self._follow_invite()
        now = self._clock()
        allowed = 2 if self._multi_lobby() else 1
        with self._lock:
            return sum(1 for m in self._live(now).values() if m["entry"] == entry) >= allowed

    def reassign(self, conn, entry):
        """Count a known sender for `entry` from now on (a producer's correction of a
        wrong registration). It keeps its slot even when `entry` already has one."""
        with self._lock:
            if conn not in self._conns or conn in self._dropped:
                return False
            self._reassigned.pop(conn, None)          # re-insert as the newest
            self._reassigned[conn] = entry
            while len(self._reassigned) > 4 * self._max:
                del self._reassigned[next(iter(self._reassigned))]
            self._conns[conn]["entry"] = entry
            return True

    def disconnect(self, conn):
        with self._lock:
            if conn not in self._conns:
                return False
            del self._conns[conn]
            self._reassigned.pop(conn, None)
            self._dropped[conn] = None
            while len(self._dropped) > 4 * self._max:
                del self._dropped[next(iter(self._dropped))]
        self._counts.forget(conn)
        return True

    def _clear(self):
        self._conns.clear()
        self._dropped.clear()
        self._reassigned.clear()
        self._hook_failed.clear()

    def reset(self):
        with self._lock:
            self._clear()
        self._counts.reset()

    def connections(self):
        self._follow_invite()
        now = self._clock()
        with self._lock:
            forgotten = self._forget_quiet(now)
        for stale in forgotten:
            self._counts.forget(stale)
        senders = self._counts.snapshot()["senders"]
        with self._lock:
            conns = dict(self._conns)
            moved = set(self._reassigned)
        out = []
        for conn, meta in conns.items():
            stats = senders.get(conn, {})
            samples = stats.get("samples") or []
            out.append({"conn": conn, "entry": meta["entry"], "segment": meta["segment"],
                        "name": meta["name"], "live": now - meta["last"] < LIVE_S,
                        "reassigned": conn in moved,
                        "idle_s": round(now - meta["last"], 1),
                        "packets": stats.get("packets", {}),
                        "packets_per_s": stats.get("packets_per_s"),
                        "sender_stats": stats.get("sender_stats", {}),
                        "latest": samples[-1] if samples else None})
        return sorted(out, key=lambda c: (c["entry"], c["name"]))


def readiness(roster, segments, connections, multi_lobby):
    """One row per entry (in endurance per entry and stint label) with its senders:
    {entry, segment, ready, senders: [{conn, name, live}]}. Senders registered for an
    entry or label outside the roster get their own rows, so nobody is hidden."""
    slots = [(e, s) for e in roster for s in (segments if multi_lobby and segments else [None])]
    rows = {slot: [] for slot in slots}
    for c in connections:
        key = (c["entry"], c["segment"] if multi_lobby and segments else None)
        rows.setdefault(key, []).append({"conn": c["conn"], "name": c["name"], "live": c["live"]})
    return [{"entry": e, "segment": s, "ready": any(x["live"] for x in senders),
             "senders": senders} for (e, s), senders in rows.items()]

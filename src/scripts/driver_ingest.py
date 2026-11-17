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
import threading
import time

import telemetry_probe

CONTRACT = 1
RATE_HZ = 20
LIVE_S = 20.0         # a connection without a batch for this long frees its entry slot
MAX_CONNECTIONS = 48


class IngestError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Hub:
    """Connections keyed by the code's connection id. Thread-safe."""

    def __init__(self, multi_lobby, clock=time.monotonic, wall=time.time, rate_hz=RATE_HZ,
                 max_connections=MAX_CONNECTIONS):
        self._multi_lobby = multi_lobby
        self._clock = clock
        self.rate_hz = rate_hz
        self._max = max_connections
        self._counts = telemetry_probe.Probe(None, clock=clock, wall=wall, rate_hz=rate_hz,
                                             max_senders=max_connections, samples=1)
        self._lock = threading.Lock()
        self._conns = {}        # conn -> {entry, segment, name, last}
        self._dropped = set()

    def _live(self, now):
        return {c: m for c, m in self._conns.items() if now - m["last"] < LIVE_S}

    def ingest(self, reg, body, sender_stats):
        conn = reg["conn"]
        now = self._clock()
        with self._lock:
            if conn in self._dropped:
                raise IngestError("disconnected by the producer: register again on the invite page", 410)
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
                peers = [m for c, m in live.items() if m["entry"] == reg["entry"] and c != conn]
                if len(peers) >= allowed:
                    raise IngestError(
                        "this team already has two senders" if allowed == 2
                        else "this entry already has a sender", 409)
            try:
                reply = self._counts.ingest(conn, body, sender_stats)
            except telemetry_probe.ProbeError as exc:
                raise IngestError(str(exc), exc.status) from None
            self._conns[conn] = {"entry": reg["entry"], "segment": reg.get("segment"),
                                 "name": reg["name"], "last": now}
        return {"ok": True, "v": CONTRACT, "rate_hz": self.rate_hz, "accepted": reply["accepted"]}

    def disconnect(self, conn):
        with self._lock:
            if conn not in self._conns:
                return False
            del self._conns[conn]
            self._dropped.add(conn)
        self._counts.forget(conn)
        return True

    def reset(self):
        with self._lock:
            self._conns.clear()
            self._dropped.clear()
        self._counts.reset()

    def connections(self):
        now = self._clock()
        senders = self._counts.snapshot()["senders"]
        with self._lock:
            conns = dict(self._conns)
        out = []
        for conn, meta in conns.items():
            stats = senders.get(conn, {})
            samples = stats.get("samples") or []
            out.append({"conn": conn, "entry": meta["entry"], "segment": meta["segment"],
                        "name": meta["name"], "live": now - meta["last"] < LIVE_S,
                        "idle_s": round(now - meta["last"], 1),
                        "packets": stats.get("packets", {}),
                        "packets_per_s": stats.get("packets_per_s"),
                        "sender_stats": stats.get("sender_stats", {}),
                        "latest": samples[-1] if samples else None})
        return sorted(out, key=lambda c: (c["entry"], c["name"]))

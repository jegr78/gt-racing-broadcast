"""The driver telemetry probe ingest (#1110): counts what senders deliver.

Stdlib only. A sender posts the encrypted GT7 packets it received in batches; this
module decrypts them, counts loss and decode failures per sender and keeps a bounded
ring of samples in memory for the probe report. Nothing is written to disk, and
nothing feeds the broadcast.

Batch format, version 1: the 4 bytes MAGIC, then per packet a big-endian uint16
length and the packet bytes exactly as the console sent them.
"""
import collections
import hmac
import re
import struct
import threading
import time

import gt7_crypto
import gt7_packet_stats
import gt7_telemetry

MAGIC = b"RCT1"
MAX_PACKET = 512          # the longest GT7 packet is 344 bytes
MAX_BATCH = 120           # two seconds of packets at 60 Hz
MAX_BODY = len(MAGIC) + MAX_BATCH * (2 + MAX_PACKET)
RATE_HZ = 20              # how often a sender posts; the reply tells the sender
SAMPLE_EVERY = 6          # decrypt every 6th packet whole: 10 Hz samples at 60 Hz
PACKET_HZ = 60.0          # GT7's send rate: back-dates a sample within its batch
SENDER_STAT_KEYS = ("received", "forwarded", "posted", "dropped", "post_failed",
                    "interruptions", "heartbeats")
COUNTERS = ("received", "lost", "reordered", "decrypt_failed", "parse_failed")
_NAME = re.compile(r"[A-Za-z0-9 _.\-]{1,32}\Z")


class ProbeError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def encode_batch(packets):
    return MAGIC + b"".join(struct.pack(">H", len(p)) + p for p in packets)


def decode_batch(body):
    if not body.startswith(MAGIC):
        raise ProbeError("not a telemetry batch")
    packets, off = [], len(MAGIC)
    while off < len(body):
        if off + 2 > len(body):
            raise ProbeError("truncated batch")
        size = struct.unpack_from(">H", body, off)[0]
        off += 2
        if size > MAX_PACKET:
            raise ProbeError("packet too long")
        if off + size > len(body):
            raise ProbeError("truncated batch")
        packets.append(body[off:off + size])
        off += size
        if len(packets) > MAX_BATCH:
            raise ProbeError("too many packets in one batch")
    return packets


def _sample(kind, pid, plain, wall):
    pkt = gt7_telemetry.parse_packet(plain)
    return {"t": round(wall, 3), "pid": pid, "kind": kind, "lap": pkt.lap,
            "speed_kmh": round(pkt.speed_mps * 3.6, 1), "on_track": pkt.on_track,
            "paused": pkt.paused, "loading": pkt.loading, "best_ms": pkt.best_ms,
            "last_ms": pkt.last_ms, "race_position": pkt.race_position,
            "car_count": pkt.car_count, "fuel_l": round(pkt.fuel_level, 2),
            "x": pkt.pos_x, "z": pkt.pos_z}


class _Sender:
    def __init__(self, samples):
        self.stats = gt7_packet_stats.PacketStats()
        self.banked = dict.fromkeys(COUNTERS, 0)   # streams that ended: an upload stall,
        self.stream_seen = 0                        # a sender restart or a replay loop
        self.samples = collections.deque(maxlen=samples)
        self.kinds = collections.Counter()
        self.batches = self.bytes = 0
        self.first_at = self.last_at = None
        self.max_gap = 0.0
        self.sender_stats = {}


class Probe:
    """Per-sender counters behind one probe key. Thread-safe."""

    def __init__(self, key, clock=time.monotonic, wall=time.time, rate_hz=RATE_HZ,
                 max_senders=16, samples=600, on_sample=None):
        self.key = key
        self._clock, self._wall = clock, wall
        self.rate_hz = rate_hz
        self._max_senders, self._samples = max_senders, samples
        self._lock = threading.Lock()
        self._senders = {}
        self._on_sample = on_sample      # (name, sample) for every decoded sample, outside the lock

    def check_key(self, presented):
        return isinstance(presented, str) and bool(self.key) and hmac.compare_digest(
            presented.encode("utf-8"), self.key.encode("utf-8"))

    def ingest(self, name, body, sender_stats):
        if not isinstance(name, str) or not _NAME.match(name):
            raise ProbeError("invalid sender name")
        packets = decode_batch(body)
        now, wall = self._clock(), self._wall()
        decoded = [gt7_crypto.decrypt_head(raw) + (raw,) for raw in packets]
        pids = [gt7_packet_stats.packet_id(head) if head is not None else None
                for _kind, head, _raw in decoded]
        newest = max((p for p in pids if p is not None), default=None)
        fresh = []
        with self._lock:
            sender = self._senders.get(name)
            if sender is None:
                if len(self._senders) >= self._max_senders:
                    raise ProbeError("too many senders for this probe", 429)
                sender = self._senders[name] = _Sender(self._samples)
            if sender.last_at is not None:
                sender.max_gap = max(sender.max_gap, now - sender.last_at)
            sender.first_at = now if sender.first_at is None else sender.first_at
            sender.last_at = now
            sender.batches += 1
            sender.bytes += len(body)
            if isinstance(sender_stats, dict):
                sender.sender_stats = {k: sender_stats[k] for k in SENDER_STAT_KEYS
                                       if type(sender_stats.get(k)) is int}
            accepted = 0
            sampled = set()          # a repeated packet id is one sample, never two
            for (kind, _head, raw), pid in zip(decoded, pids, strict=True):
                if pid is None:
                    sender.stats.on_decrypt_failure(now)
                    continue
                sender.stats.on_packet(pid, now)
                self._bank(sender)
                sender.kinds[kind] += 1
                accepted += 1
                if (pid % SAMPLE_EVERY == 0 or not sender.samples) and pid not in sampled:
                    sampled.add(pid)
                    try:
                        sample = _sample(kind, pid, gt7_crypto.decrypt_typed(raw)[1], wall)
                    except (struct.error, ValueError, IndexError, TypeError):
                        sender.stats.on_parse_failure(now)
                        continue
                    # Every packet of a batch arrives at once: date each by its id.
                    sample["mono"] = now - min(MAX_BATCH, max(0, newest - pid)) / PACKET_HZ
                    sender.samples.append(sample)
                    fresh.append(sample)
        if self._on_sample is not None:
            for sample in fresh:
                self._on_sample(name, sample)
        return {"ok": True, "rate_hz": self.rate_hz, "accepted": accepted}

    @staticmethod
    def _bank(sender):
        if sender.stats.stream != sender.stream_seen:
            if sender.stream_seen and sender.stats.previous:
                for k in COUNTERS:
                    sender.banked[k] += sender.stats.previous.get(k, 0)
            sender.stream_seen = sender.stats.stream

    def forget(self, name):
        with self._lock:
            self._senders.pop(name, None)

    def reset(self):
        with self._lock:
            self._senders.clear()

    def snapshot(self):
        now = self._clock()
        with self._lock:
            senders = {}
            for name, s in self._senders.items():
                span = (s.last_at - s.first_at) if s.first_at is not None else 0.0
                stats = s.stats.snapshot(now)
                packets = {k: s.banked[k] + stats[k] for k in COUNTERS}
                senders[name] = {
                    "packets": packets,
                    "current_stream": {k: stats[k] for k in COUNTERS},
                    "loss_rate_10s": stats["loss_rate_10s"], "stream": stats["stream"],
                    "batches": s.batches, "bytes": s.bytes, "kinds": dict(s.kinds),
                    "seconds": round(span, 3), "idle_s": round(now - s.last_at, 3),
                    "packets_per_s": round(packets["received"] / span, 2) if span > 0 else None,
                    "max_batch_gap_s": round(s.max_gap, 3),
                    "sender_stats": dict(s.sender_stats), "samples": list(s.samples)}
        return {"rate_hz": self.rate_hz, "senders": senders}

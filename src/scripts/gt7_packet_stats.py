"""GT7 telemetry packet loss and decode failure counters (#1107).

Stdlib only: the relay, the recorder and the lap index use it. GT7 numbers its packets
at 0x70 and sends about 60 per second; a gap in that sequence is a lost packet. A
sequence that jumps backwards, jumps forward by more than MAX_GAP or resumes after
STREAM_IDLE_S of silence is a new stream and starts a new count: a console that stopped
sending is "no packets", not a lossy network.
"""
import collections
import struct

OFF_PACKET_ID = 0x70
WINDOW_S = 10            # the loss rate covers this many seconds
STALE_S = 5.0            # no packet for this long: no packets, not lossy (the desk's limit)
STREAM_IDLE_S = 5.0
MAX_GAP = 600            # ten seconds of packets; a larger jump is a new stream
REORDER_WINDOW = 60      # a late packet up to one second behind counts as out of order
LOSSY_RATE = 0.05        # lossy: at least 5 % of the packets lost ...
LOSSY_HOLD_S = 20.0      # ... over the 10 s window, continuously for this long
_COUNTERS = ("received", "lost", "reordered", "decrypt_failed", "parse_failed")


def packet_id(plain):
    """The packet sequence number of a decrypted packet, None when it is too short."""
    if len(plain) < OFF_PACKET_ID + 4:
        return None
    return struct.unpack_from("<i", plain, OFF_PACKET_ID)[0]


def gap(last, pid):
    """Packets missing between two consecutive ids of one stream, 0 across a new stream."""
    if last is None or pid is None:
        return 0
    missing = pid - last - 1
    return missing if 0 < missing <= MAX_GAP else 0


class PacketStats:
    """Counters for the current stream plus the totals of the previous one. Not thread-safe:
    the relay's telemetry thread writes, readers take snapshot() under their own lock."""

    def __init__(self):
        self.stream = 0
        self.previous = None
        self._reset()

    def _reset(self):
        self.counts = dict.fromkeys(_COUNTERS, 0)
        self._last_id = None
        self._last_at = None
        self._missing = set()
        self._buckets = collections.deque()
        self._lossy_since = None

    def _new_stream(self):
        if self.stream:
            self.previous = dict(self.counts)
        self.stream += 1
        self._reset()

    def _bucket(self, now):
        second = int(now)
        if not self._buckets or self._buckets[-1][0] != second:
            self._buckets.append([second, 0, 0])
        while self._buckets and self._buckets[0][0] <= second - WINDOW_S:
            self._buckets.popleft()
        return self._buckets[-1]

    def _rate(self, now):
        second = int(now)
        recent = [b for b in self._buckets if b[0] > second - WINDOW_S]
        received = sum(b[1] for b in recent)
        lost = max(0, sum(b[2] for b in recent))
        return round(lost / (received + lost), 4) if received + lost else None

    def _judge(self, now):
        rate = self._rate(now)
        if rate is not None and rate >= LOSSY_RATE:
            self._lossy_since = now if self._lossy_since is None else self._lossy_since
        else:
            self._lossy_since = None

    def on_packet(self, pid, now):
        if (not self.stream or self._last_at is None or now - self._last_at >= STREAM_IDLE_S
                or pid < self._last_id - REORDER_WINDOW or pid - self._last_id - 1 > MAX_GAP):
            self._new_stream()
        bucket = self._bucket(now)
        self.counts["received"] += 1
        bucket[1] += 1
        if self._last_id is None or pid > self._last_id:
            missing = gap(self._last_id, pid)
            if missing:
                self.counts["lost"] += missing
                bucket[2] += missing
                self._missing.update(range(pid - missing, pid))
            self._last_id = pid
        elif pid in self._missing:
            self._missing.discard(pid)
            self.counts["lost"] -= 1
            self.counts["reordered"] += 1
            bucket[2] -= 1
        else:
            self.counts["reordered"] += 1
        self._missing = {m for m in self._missing if m >= self._last_id - REORDER_WINDOW}
        self._last_at = now
        self._judge(now)

    def _failure(self, key, now):
        if not self.stream:
            self._new_stream()
        self.counts[key] += 1

    def on_decrypt_failure(self, now):
        self._failure("decrypt_failed", now)

    def on_parse_failure(self, now):
        self._failure("parse_failed", now)

    def snapshot(self, now):
        live = self._last_at is not None and now - self._last_at < STALE_S
        lossy = bool(live and self._lossy_since is not None and now - self._lossy_since >= LOSSY_HOLD_S)
        return dict(self.counts, stream=self.stream, loss_rate_10s=self._rate(now), lossy=lossy,
                    previous=self.previous)

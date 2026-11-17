"""The public timing page's shared response body and upload budget (#1128).

`TickBody` builds the payload at most once per tick, as JSON and gzip with an ETag, so
every viewer within a tick gets the same bytes. `Budget` estimates the viewers from the
responses it served and returns the poll interval that keeps them within the upload
budget. Stdlib only; the relay imports it.
"""
import collections
import gzip
import hashlib
import json
import math
import threading
import time

TICK_S = 2.0
MIN_INTERVAL_S = 2.0
MAX_INTERVAL_S = 30.0
HEADER_BYTES = 280                # response headers, counted on every answer
DEFAULT_KBPS = 1000


def budget_kbps(raw):
    """RACECAST_TIMING_BUDGET_KBPS: upload for all timing viewers together, in kbit/s."""
    try:
        return max(50, min(100000, int(raw)))
    except (TypeError, ValueError):
        return DEFAULT_KBPS


def encode(raw):
    """(etag, raw, gzip) for a response body."""
    return '"' + hashlib.sha1(raw).hexdigest()[:16] + '"', raw, gzip.compress(raw, mtime=0)


class TickBody:
    """One encoded payload per tick: get() -> (etag, raw_json, gzip_json)."""

    def __init__(self, build, clock=time.monotonic, tick_s=TICK_S):
        self._build, self._clock, self._tick_s = build, clock, tick_s
        self._lock = threading.Lock()
        self._at = None
        self._value = None

    def get(self):
        with self._lock:
            now = self._clock()
            if self._value is None or now - self._at >= self._tick_s:
                raw = json.dumps(self._build(), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                if self._value is None or raw != self._value[1]:
                    self._value = encode(raw)
                self._at = now
            return self._value


class Budget:
    """Poll interval that keeps all viewers within `kbps` of upload."""

    def __init__(self, kbps, clock=time.monotonic, window_s=2 * MAX_INTERVAL_S):
        # A window shorter than the longest interval misses slow viewers and oscillates.
        self._bytes_per_s = kbps * 125
        self._clock, self._window_s = clock, window_s
        self._lock = threading.Lock()
        self._served = collections.deque()      # (at, nbytes, interval_s)
        self._bytes = self._intervals = 0.0

    def _prune(self, now):
        while self._served and now - self._served[0][0] > self._window_s:
            _at, nbytes, interval_s = self._served.popleft()
            self._bytes -= nbytes
            self._intervals -= interval_s
        if not self._served:
            self._bytes = self._intervals = 0.0

    def served(self, nbytes, interval_s):
        with self._lock:
            now = self._clock()
            self._served.append((now, nbytes, interval_s))
            self._bytes += nbytes
            self._intervals += interval_s
            self._prune(now)

    def interval(self):
        with self._lock:
            self._prune(self._clock())
            if not self._served:
                return MIN_INTERVAL_S
            # A viewer polling every I seconds shows up window / I times per window.
            viewers = self._intervals / self._window_s
            mean_bytes = self._bytes / len(self._served)
            need = viewers * mean_bytes / self._bytes_per_s
        need = math.ceil(need * 2) / 2
        return max(MIN_INTERVAL_S, min(MAX_INTERVAL_S, need))

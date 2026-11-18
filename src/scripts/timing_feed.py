"""The public timing page's shared response body and upload budget (#1128).

`TickBody` builds the payload at most once per tick, as JSON and gzip, each with its own
strong ETag, so every viewer within a tick gets the same bytes. `Budget` estimates the viewers from the
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
    """(etag, raw, gzip, gzip_etag) for a response body; each encoding has its own strong tag."""
    tag = hashlib.sha1(raw).hexdigest()[:16]
    return f'"{tag}"', raw, gzip.compress(raw, mtime=0), f'"{tag}-gz"'


def _q(params):
    for p in params:
        k, _, v = p.strip().partition("=")
        if k.strip().lower() == "q":
            try:
                return float(v)
            except ValueError:
                return 0.0
    return 1.0


def accepts_gzip(header):
    """True when an Accept-Encoding header takes gzip (explicitly or through *) with q > 0."""
    explicit = star = None
    for item in (header or "").split(","):
        coding, *params = item.split(";")
        coding = coding.strip().lower()
        if coding in ("gzip", "x-gzip"):
            explicit = _q(params) > 0
        elif coding == "*":
            star = _q(params) > 0
    return explicit if explicit is not None else bool(star)


def etag_matches(header, *etags):
    """If-None-Match against our tags: weak comparison, lists and * as RFC 9110 says."""
    if not header:
        return False
    if header.strip() == "*":
        return True
    tags = {t.strip().removeprefix("W/") for t in header.split(",")}
    return any(e in tags for e in etags)


class TickBody:
    """One encoded payload per tick: get() -> encode()'s (etag, raw, gzip, gzip_etag)."""

    def __init__(self, build, clock=time.monotonic, tick_s=TICK_S):
        self._build, self._clock, self._tick_s = build, clock, tick_s
        self._lock = threading.Lock()
        self._at = None
        self._built = None
        self._value = None

    def get(self):
        """The current body; a failing build keeps the last one and raises only without one."""
        with self._lock:
            now = self._clock()
            if self._at is None or now - self._at >= self._tick_s:
                self._at = now
                try:
                    raw = json.dumps(self._build(), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                except Exception:
                    if self._value is None:
                        self._at = None
                        raise
                    return self._value
                if self._value is None or raw != self._value[1]:
                    self._value = encode(raw)
                self._built = now
            return self._value

    def age(self):
        """Seconds since the body last came from a successful build."""
        with self._lock:
            return 0.0 if self._built is None else self._clock() - self._built


class Budget:
    """Poll interval that keeps all viewers within `kbps` of upload, up to the crowd that
    MAX_INTERVAL_S still covers; beyond it the upload grows with the viewers again."""

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
            now = self._clock()
            self._prune(now)
            if not self._served:
                return MIN_INTERVAL_S
            # A viewer polling every I seconds shows up span / I times in the span the
            # samples cover; a crowd that just arrived covers less than the window.
            span = min(self._window_s, max(MIN_INTERVAL_S, now - self._served[0][0]))
            viewers = self._intervals / span
            mean_bytes = self._bytes / len(self._served)
            need = viewers * mean_bytes / self._bytes_per_s
        need = math.ceil(need * 2) / 2
        return max(MIN_INTERVAL_S, min(MAX_INTERVAL_S, need))

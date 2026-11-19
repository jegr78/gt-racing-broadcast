"""Brand logos for the timing pages: the tower, the desks, the public page and the
league's timing host. One resolver for all of them, so every viewer sees the same
logo. Only a PNG goes out: the public routes never serve markup."""
import collections
import os
import re
import threading
import time

KEY_RE = re.compile(r"[a-z0-9-]{1,64}")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_BYTES = 256 * 1024
MAX_LOGOS = 64
WINDOW_S = 10


class Logos:
    """`dirs` in precedence order (the league's override first); each holds <key>.png.
    get() rereads a file only when its size or mtime changed."""

    def __init__(self, dirs):
        self._dirs = [os.path.realpath(d) for d in dirs if d]
        self._lock = threading.Lock()
        self._cache = {}                # key -> (path, mtime_ns, size, bytes or None)

    def get(self, key):
        """The logo's PNG bytes, or None."""
        if not isinstance(key, str) or not KEY_RE.fullmatch(key):
            return None
        for base in self._dirs:
            path = os.path.realpath(os.path.join(base, key + ".png"))
            if os.path.dirname(path) != base:
                return None
            try:
                st = os.stat(path)
            except OSError:
                continue
            with self._lock:
                hit = self._cache.get(key)
            if hit and hit[:3] == (path, st.st_mtime_ns, st.st_size):
                return hit[3]
            data = _read_png(path, st.st_size)
            with self._lock:
                self._cache[key] = (path, st.st_mtime_ns, st.st_size, data)
            return data
        return None

    def for_keys(self, keys):
        """{key: bytes} for up to MAX_LOGOS keys that resolve, in sorted order."""
        out = {}
        for key in sorted(k for k in set(keys) if k):
            data = self.get(key)
            if data is not None:
                out[key] = data
                if len(out) >= MAX_LOGOS:
                    break
        return out


class ByteWindow:
    """Logo bytes served in the last `window_s`, held to `bytes_per_s` on average; an
    idle window always lets one logo of any allowed size through."""

    def __init__(self, bytes_per_s, window_s=WINDOW_S, clock=time.monotonic):
        self._cap = max(bytes_per_s * window_s, MAX_BYTES)
        self._window_s, self._clock = window_s, clock
        self._lock = threading.Lock()
        self._sent = collections.deque()    # (at, nbytes)
        self._total = 0

    def take(self, nbytes):
        """True and counted when `nbytes` fit, else False."""
        with self._lock:
            now = self._clock()
            while self._sent and now - self._sent[0][0] >= self._window_s:
                self._total -= self._sent.popleft()[1]
            if self._total + nbytes > self._cap:
                return False
            self._sent.append((now, nbytes))
            self._total += nbytes
            return True


def _read_png(path, size):
    if not len(PNG_MAGIC) <= size <= MAX_BYTES:
        return None
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_BYTES)
    except OSError:
        return None
    return data if data.startswith(PNG_MAGIC) else None

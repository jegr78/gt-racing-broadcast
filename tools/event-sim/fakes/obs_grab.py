"""Feed frames for the simulated OBS's program screenshots, read with ffmpeg.

Only the relay's feed URLs are read: http on a loopback host, one of the feed ports
(53001-53003 unless configured), path "/" and nothing else. ffmpeg runs with a protocol
whitelist, the MPEG-TS demuxer forced (no playlists, no nested URLs), no reconnect and a
read timeout. Each feed is read at most once per `interval` in the background; a
screenshot takes the newest frame and waits only for a feed's very first one. A grab is
a short-lived consumer on the relay's fan-out port, see tools/CLAUDE.md. Any failure
gives no frame, and the caller paints the synthetic pattern instead."""
import os
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import obs_ws  # noqa: E402
import simulation  # noqa: E402

FRAME_SIZE = (640, 360)
MAX_FEEDS = 16


class FeedGrabber:
    """`frames(urls)` -> {url: (rgb, w, h) or None}."""

    def __init__(self, cmd=("ffmpeg",), ports=obs_ws.RELAY_PORTS, interval=5.0, timeout=4.0,
                 wait=1.0, max_age=None, size=FRAME_SIZE, clock=time.monotonic):
        self.cmd, self.ports = list(cmd), frozenset(int(p) for p in ports)
        self.interval, self.timeout, self.wait = interval, timeout, wait
        self.size, self.clock = size, clock
        self.max_age = 3 * interval + timeout if max_age is None else max_age
        self.feeds = {}             # url -> {"frame", "at", "busy": Event or None, "used"}
        self.procs = set()
        self.lock = threading.Lock()
        self.closed = False
        self.reads = 0

    def allowed(self, url):
        """Whether `url` is a relay feed this grabber may read."""
        try:
            u = urlsplit(url)
            port = u.port
        except (ValueError, TypeError):
            return False
        return (u.scheme == "http" and simulation.is_loopback_host(u.hostname)
                and port in self.ports and u.path in ("", "/") and not u.query
                and not u.fragment and u.username is None and u.password is None)

    def argv(self, url):
        w, h = self.size
        return self.cmd + ["-hide_banner", "-loglevel", "error", "-nostdin",
                           "-protocol_whitelist", "http,tcp,udp", "-reconnect", "0",
                           "-rw_timeout", str(int(self.timeout * 1_000_000)), "-f", "mpegts",
                           "-i", url, "-an", "-frames:v", "1", "-vf", f"scale={w}:{h}",
                           "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]

    def _run(self, url):
        w, h = self.size
        try:
            proc = subprocess.Popen(self.argv(url), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError:
            return None
        with self.lock:
            self.procs.add(proc)
            closed = self.closed
        if closed:
            proc.kill()
        try:
            out, _ = proc.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return None
        finally:
            with self.lock:
                self.procs.discard(proc)
        if proc.returncode != 0 or len(out) < w * h * 3:
            return None
        return out[:w * h * 3], w, h

    def _grab(self, url, done):
        frame = self._run(url)
        with self.lock:
            entry = self.feeds.get(url)
            if entry is not None:
                if frame is not None:
                    entry["frame"], entry["at"] = frame, self.clock()
                entry["busy"] = None
        done.set()

    def _kick(self, url):
        """Start a background read of `url` unless one runs or the last is fresh. Lock held."""
        entry = self.feeds.get(url)
        if entry is None:
            if len(self.feeds) >= MAX_FEEDS:
                del self.feeds[min(self.feeds, key=lambda k: self.feeds[k]["used"])]
            entry = self.feeds[url] = {"frame": None, "at": None, "busy": None, "used": 0.0}
        entry["used"] = self.clock()
        fresh = entry["at"] is not None and self.clock() - entry["at"] < self.interval
        if entry["busy"] is None and not fresh and not self.closed:
            entry["busy"] = threading.Event()
            self.reads += 1
            threading.Thread(target=self._grab, args=(url, entry["busy"]), daemon=True).start()
        return entry

    def frames(self, urls):
        found, waits = {}, []
        with self.lock:
            for url in urls:
                if not self.allowed(url):
                    found[url] = None
                    continue
                entry = self._kick(url)
                if entry["frame"] is None and entry["busy"] is not None:
                    waits.append(entry["busy"])
        deadline = self.clock() + self.wait
        for event in waits:
            event.wait(max(0.0, deadline - self.clock()))
        with self.lock:
            for url in urls:
                if url in found:
                    continue
                entry = self.feeds.get(url) or {}
                young = entry.get("at") is not None and self.clock() - entry["at"] <= self.max_age
                found[url] = entry.get("frame") if young else None
        return found

    def close(self):
        """Stop new reads and kill the running ffmpeg processes."""
        with self.lock:
            self.closed = True
            procs = list(self.procs)
        for proc in procs:
            try:
                proc.kill()
            except OSError:
                pass  # already exited

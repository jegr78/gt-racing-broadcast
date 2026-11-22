"""One video frame from a feed URL as raw RGB, via ffmpeg, for the simulated OBS's
program screenshots. Only loopback http, tcp and udp URLs are read; any failure (no
ffmpeg, a refused or stalled feed, a short frame) gives None, so the caller falls back
to the synthetic pattern."""
import os
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import simulation  # noqa: E402

SCHEMES = ("http", "tcp", "udp")


def readable(url):
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    return u.scheme in SCHEMES and simulation.is_loopback_host(u.hostname)


class FfmpegGrabber:
    """`grab(url, w, h)` -> w*h*3 RGB bytes or None. Results, failures included, are
    kept for `ttl` seconds, so a polling viewer starts at most one ffmpeg per feed."""

    def __init__(self, cmd=("ffmpeg",), timeout=4.0, ttl=1.0, clock=time.monotonic):
        self.cmd, self.timeout, self.ttl, self.clock = list(cmd), timeout, ttl, clock
        self.cache = {}
        self.lock = threading.Lock()

    def argv(self, url, w, h):
        return self.cmd + ["-hide_banner", "-loglevel", "error", "-nostdin", "-i", url,
                           "-an", "-frames:v", "1", "-vf", f"scale={w}:{h}",
                           "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]

    def _grab(self, url, w, h):
        try:
            out = subprocess.run(self.argv(url, w, h), capture_output=True, timeout=self.timeout,
                                 check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        if out.returncode != 0 or len(out.stdout) < w * h * 3:
            return None
        return out.stdout[:w * h * 3]

    def __call__(self, url, w, h):
        if w <= 0 or h <= 0 or not readable(url):
            return None
        key = (url, w, h)
        with self.lock:
            hit = self.cache.get(key)
            if hit and self.clock() - hit[0] < self.ttl:
                return hit[1]
        frame = self._grab(url, w, h)
        with self.lock:
            self.cache[key] = (self.clock(), frame)
        return frame

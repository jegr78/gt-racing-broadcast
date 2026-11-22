#!/usr/bin/env python3
"""Load test for the public live timing page (#1110, #1128).

Maintainer tool. It runs N simulated viewers against an event's timing link. Each one
loads the page once and then polls its data the way `src/console/timing.html` does:
gzip, `If-None-Match`, and the wait the relay sets in `X-Poll-After`. Run it from
outside the tailnet against the Funnel link to measure what Funnel and the producer's
upload carry. It does not fetch brand logos, which a browser caches after one load.

    python3 tools/timing-viewer-load.py "https://<host>/console/timing?l=<id>" --viewers 200
"""
import argparse
import collections
import http.client
import json
import os
import sys
import threading
import time
from urllib.parse import urlsplit, urlunsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import http_util
import logsetup

PAGE_WAIT_S = 2.0       # the page's own poll interval
BACKOFF_S = 5.0         # the page's wait after a failed request or a bare 429
TIMEOUT_S = 15


def data_url(link):
    """The data URL the page derives from its own address."""
    u = urlsplit(link)
    return urlunsplit((u.scheme, u.netloc, u.path.rstrip("/") + "/data", u.query, ""))


def next_wait(status, poll_after, min_wait=PAGE_WAIT_S):
    """Seconds until the next poll, as the page decides it; None ends the viewer."""
    if status is None:
        return BACKOFF_S
    if status == 403:
        return None
    try:
        pace = float(poll_after)
    except (TypeError, ValueError):
        pace = 0.0
    if pace >= PAGE_WAIT_S:
        return pace
    return BACKOFF_S if status == 429 else min_wait


class Viewer:
    def __init__(self, link):
        u = urlsplit(link)
        self._https = u.scheme == "https"
        self._host = u.netloc
        self._page = u.path + ("?" + u.query if u.query else "")
        d = urlsplit(data_url(link))
        self._data = d.path + ("?" + d.query if d.query else "")
        self._conn = None
        self.status = collections.Counter()
        self.latencies, self.refreshes, self.paces, self.ages = [], [], [], []
        self.body_bytes = self.header_bytes = self.errors = self.page_loads = 0
        self.ended = False

    def _get(self, path, headers):
        """(status, headers) of one keep-alive GET; (None, None) when it failed."""
        try:
            if self._conn is None:
                cls = http.client.HTTPSConnection if self._https else http.client.HTTPConnection
                self._conn = cls(self._host, timeout=TIMEOUT_S)
            started = time.monotonic()
            self._conn.request("GET", path, headers=dict(
                headers, **{"User-Agent": http_util.RACECAST_UA, "Accept-Encoding": "gzip"}))
            res = self._conn.getresponse()
            body = res.read()
        except (OSError, http.client.HTTPException):
            if self._conn is not None:
                self._conn.close()
            self._conn = None
            self.errors += 1
            return None, None
        self.latencies.append(time.monotonic() - started)
        self.body_bytes += len(body)
        self.header_bytes += sum(len(k) + len(v) + 4 for k, v in res.getheaders()) + 19
        return res.status, res.headers

    def watch(self, stop, until, min_wait, page):
        if page and not stop.is_set():
            status, _headers = self._get(self._page, {})
            self.page_loads += status == 200
        etag, last = None, None
        while not stop.is_set() and time.monotonic() < until:
            status, headers = self._get(self._data, {"If-None-Match": etag} if etag else {})
            pace = headers.get("X-Poll-After") if headers else None
            if status is not None:
                self.status[str(status)] += 1
                if pace:
                    self.paces.append(float(pace))
            if status in (200, 304):
                now = time.monotonic()
                if last is not None:
                    self.refreshes.append(now - last)
                last = now
                etag = headers.get("ETag") or etag
                if headers.get("X-Data-Age"):
                    self.ages.append(float(headers["X-Data-Age"]))
            wait = next_wait(status, pace, min_wait)
            if wait is None:
                self.ended = True
                break
            stop.wait(min(wait, max(0.0, until - time.monotonic())))
        if self._conn is not None:
            self._conn.close()


def _spread(values):
    v = sorted(values)
    if not v:
        return {"p50": None, "p95": None, "max": None}
    return {"p50": round(v[len(v) // 2], 3), "p95": round(v[min(len(v) - 1, int(0.95 * len(v)))], 3),
            "max": round(v[-1], 3)}


def summary(viewers, seconds):
    status = collections.Counter()
    for v in viewers:
        status.update(v.status)
    body = sum(v.body_bytes for v in viewers)
    headers = sum(v.header_bytes for v in viewers)
    return {"viewers": len(viewers), "seconds": round(seconds, 1),
            "page_loads": sum(v.page_loads for v in viewers),
            "status": dict(status), "errors": sum(v.errors for v in viewers),
            "ended_403": sum(v.ended for v in viewers),
            "requests_per_s": round(sum(status.values()) / seconds, 1) if seconds else None,
            "bytes": {"body": body, "headers": headers},
            "upload_kbps": round((body + headers) * 8 / 1000 / seconds, 1) if seconds else None,
            "latency_s": _spread([x for v in viewers for x in v.latencies]),
            "poll_after_s": _spread([x for v in viewers for x in v.paces]),
            "refresh_s": _spread([x for v in viewers for x in v.refreshes]),
            "data_age_s": _spread([x for v in viewers for x in v.ages])}


def run(link, viewers, duration, ramp=10.0, min_wait=PAGE_WAIT_S, page=True, stop=None):
    """Run the viewers and return the report. `ramp` spreads their arrival."""
    stop = stop or threading.Event()
    crowd = [Viewer(link) for _ in range(viewers)]
    started = time.monotonic()
    until = started + ramp + duration

    def one(k):
        if not stop.wait(ramp * k / max(1, viewers)):
            crowd[k].watch(stop, until, min_wait, page)
    threads = [threading.Thread(target=one, args=(k,), daemon=True) for k in range(viewers)]
    for t in threads:
        t.start()
    try:
        for t in threads:
            while t.is_alive():
                t.join(0.5)
    except KeyboardInterrupt:
        stop.set()
        for t in threads:
            t.join(TIMEOUT_S)
    return summary(crowd, time.monotonic() - started)


def main(argv=None):
    logsetup.harden_stdio()
    ap = argparse.ArgumentParser(description="Run simulated viewers against a public timing link.")
    ap.add_argument("link", help="the event's timing link (the Funnel one for Funnel)")
    ap.add_argument("--viewers", type=int, default=50, help="simulated viewers")
    ap.add_argument("--duration", type=float, default=120.0, help="seconds with every viewer present")
    ap.add_argument("--ramp", type=float, default=10.0, help="seconds over which the viewers arrive")
    ap.add_argument("--no-page", action="store_true", help="skip the one page load per viewer")
    args = ap.parse_args(argv)
    print(json.dumps(run(args.link, args.viewers, args.duration, ramp=args.ramp,
                         page=not args.no_page), indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

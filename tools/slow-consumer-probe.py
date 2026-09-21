#!/usr/bin/env python3
"""Force a SUSTAINED consumer backlog on a running relay — maintainer harness, not shipped.

Attaches to a feed's fan-out port as an ordinary HTTP consumer and reads at a fixed
fraction of real time, so `consumer_backlog` and everything downstream of it sees a real,
sustained backlog.

It proves detection and the shed's lifecycle. It does NOT prove a rebuild removes OBS's
own backlog: the rebuild targets OBS, and this consumer is not OBS, so a stand-down here
is correct behaviour.

Levers that do NOT work, so nobody retries them: pinning OBS to one core (with or without
busy loops on it), two 1080p60 feeds plus a recording, a VOD source (yt-dlp -g returns a
progressive URL streamlink cannot open), and pausing the media input (ignored). Setting
close_when_inactive=false and switching scene away works only transiently — the relay lets
a consumer that fell behind sprint back to the trailing mark.

    python3 tools/slow-consumer-probe.py --port 53001 --rate 0.4 --seconds 300
"""
import argparse
import json
import socket
import sys
import time
import urllib.request

DEFAULT_PORT = 53001
DEFAULT_RATE = 0.40          # fraction of real time to read at
SAMPLE_S = 10.0              # how often to print the relay's view
CHUNK = 16384


def read_budget(elapsed_s, bytes_read, source_bps, rate):
    """How many bytes this consumer may have read by now. Paced against the source's own
    rate, so the backlog grows linearly and predictably. Pure."""
    return max(0, int(source_bps * rate * elapsed_s) - bytes_read)


def measure_source_bps(port, host, seconds=6.0):
    """Bytes per second the feed actually delivers, measured by reading flat out."""
    with socket.create_connection((host, port), timeout=10) as s:
        s.sendall(b"GET / HTTP/1.1\r\nHost: probe\r\nConnection: close\r\n\r\n")
        s.settimeout(2.0)
        start, total = time.monotonic(), 0
        while time.monotonic() - start < seconds:
            try:
                b = s.recv(CHUNK)
            except socket.timeout:
                continue
            if not b:
                break
            total += len(b)
    span = max(0.1, time.monotonic() - start)
    return total / span


def relay_view(relay, feed):
    """(backlog_s, backlogged, stood_down) as the relay reports them, or Nones."""
    try:
        with urllib.request.urlopen(relay.rstrip("/") + "/status", timeout=5) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:                      # noqa: BLE001 — a probe never dies on this
        return None, None, None
    f = (d.get("feeds") or {}).get(feed) or {}
    return (f.get("backlog_s"), f.get("backlogged"),
            (d.get("rebuild_guard") or {}).get("stood_down"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help="feed fan-out port (A=53001, B=53002, POV=53003)")
    ap.add_argument("--feed", default=None,
                    help="feed key for the /status readout; derived from --port if absent")
    ap.add_argument("--rate", type=float, default=DEFAULT_RATE,
                    help="read at this fraction of real time (default 0.40)")
    ap.add_argument("--seconds", type=float, default=240.0)
    ap.add_argument("--relay", default="http://127.0.0.1:8088")
    args = ap.parse_args()
    if not 0 < args.rate < 1:
        sys.exit("--rate must be between 0 and 1 (1.0 would not fall behind at all)")
    feed = args.feed or {53001: "A", 53002: "B", 53003: "POV"}.get(args.port, "A")

    print(f"measuring what feed {feed} delivers on :{args.port} ...")
    bps = measure_source_bps(args.port, args.host)
    if bps < 1000:
        sys.exit(f"feed {feed} delivered {bps:.0f} B/s — is it serving? (racecast relay status)")
    deficit = bps * (1 - args.rate)
    print(f"  {bps/1e6*8:.1f} Mbit/s; reading at {args.rate:.0%} loses "
          f"{deficit/bps:.1f} s of stream per second")

    with socket.create_connection((args.host, args.port), timeout=10) as s:
        s.sendall(b"GET / HTTP/1.1\r\nHost: probe\r\nConnection: close\r\n\r\n")
        s.settimeout(1.0)
        start = last = time.monotonic()
        total = 0
        print(f"\n{'t':>6}  {'read':>9}  {'backlog':>8}  {'flagged':>7}  stood_down")
        try:
            while time.monotonic() - start < args.seconds:
                now = time.monotonic()
                budget = read_budget(now - start, total, bps, args.rate)
                if budget <= 0:
                    time.sleep(0.05)       # ahead of pace: hold, do NOT read
                    continue
                try:
                    b = s.recv(min(CHUNK, budget))
                except socket.timeout:
                    continue
                if not b:
                    print("\nfeed closed the connection")
                    break
                total += len(b)
                if now - last >= SAMPLE_S:
                    last = now
                    backlog, flagged, stood = relay_view(args.relay, feed)
                    print(f"{now-start:6.0f}  {total/1e6:7.1f}MB  {str(backlog):>8}  "
                          f"{str(flagged):>7}  {stood}")
        except KeyboardInterrupt:
            print("\nstopped")
    print("connection closed — the relay's reading returns to OBS alone")


if __name__ == "__main__":
    main()

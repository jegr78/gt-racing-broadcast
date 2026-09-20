#!/usr/bin/env python3
"""Prefetch-burst probe (#614) — maintainer diagnostic, NOT shipped, NOT run in CI.

Measures the one number the relay's OBS rejoin needs: **how long streamlink takes to
deliver the initial `--hls-live-edge N` burst**, in wall-clock seconds from the first
byte.

Why it matters. In fan-out the ring's time index is byte ARRIVAL time, and OBS rejoins
at `trailing_offset(prebuffer_s)` — the mark `prebuffer_s` seconds before now. For that
join to land AFTER the prefetch instead of at its start, the rejoin has to wait longer
than `burst_arrival_s + prebuffer_s`. The burst's MEDIA duration (N x segment length) is
irrelevant here; only how long the download occupies the wall clock is.

Method. Run streamlink with the relay's exact serve flags, timestamp every chunk that
reaches stdout, and split the arrival pattern at the first gap wider than --gap: the
burst is everything before it. Reports the burst's arrival span and byte count, plus the
first few steady-state inter-arrival gaps as a sanity check that the source really is
live (a VOD has no gaps — it races ahead and the burst never ends).

Usage:
  python3 tools/prefetch-burst-probe.py --url https://www.twitch.tv/<login>
  python3 tools/prefetch-burst-probe.py --url https://www.youtube.com/watch?v=<id>
  python3 tools/prefetch-burst-probe.py --url <u> --tier robust --runs 3

Refuses to run while a relay is up: a second puller on the same source is how a
YouTube 429 starts. Exit 0 on a measurement, 2 on a setup error.
"""
import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# The relay's own serve flags, kept literal so the probe cannot drift from production
# silently. racecast-feeds.py is not importable by name (hyphen), so they are mirrored
# here and checked against the source at startup.
FLAGS = {
    ("youtube", "full"): ["--ringbuffer-size", "64M", "--hls-live-edge", "4"],
    ("youtube", "robust"): ["--ringbuffer-size", "128M", "--hls-live-edge", "6"],
    ("twitch", "full"): ["--ringbuffer-size", "64M", "--hls-live-edge", "2",
                         "--twitch-low-latency"],
    ("twitch", "robust"): ["--ringbuffer-size", "128M", "--hls-live-edge", "2",
                           "--twitch-low-latency"],
}
RELAY_SRC = os.path.join(ROOT, "src", "relay", "racecast-feeds.py")


def live_edge_of(flags):
    """The --hls-live-edge value in a flag list, or None. Mirrors the relay's
    live_edge_segments; the burst size is the only mirrored value that changes the
    measurement, so it is what the drift check compares."""
    try:
        return int(flags[flags.index("--hls-live-edge") + 1])
    except (ValueError, IndexError, TypeError):
        return None


def check_flags_match_relay():
    """Fail loudly if the mirrored flags drifted from the relay's constants.

    Compares the PARSED --hls-live-edge value, not substrings. A substring test is
    useless here: `"4" in 'STREAMLINK_SERVE = [..., "64M", "--hls-live-edge", "4"]'` is
    already satisfied by the "64M", so a drift from 4 to 9 would pass unnoticed — and
    the relay's SEGMENT_FETCH_BUDGET_S is derived from this script's output.
    """
    try:
        with open(RELAY_SRC, encoding="utf-8") as fh:
            src = fh.read()
    except OSError as exc:
        return [f"could not read {RELAY_SRC} ({exc})"]
    # Each relay constant, and the FLAGS key it is mirrored into. The robust Twitch set
    # is wrapped over two source lines, so the whole assignment is read, not one line.
    want = {
        "STREAMLINK_SERVE": ("youtube", "full"),
        "STREAMLINK_SERVE_ROBUST": ("youtube", "robust"),
        "STREAMLINK_TWITCH": ("twitch", "full"),
        "STREAMLINK_TWITCH_ROBUST": ("twitch", "robust"),
    }
    bad = []
    for name, key in want.items():
        m = re.search(rf"^{name} = (\[.*?\])", src, re.MULTILINE | re.DOTALL)
        if m is None:
            bad.append(f"{name} not found in the relay")
            continue
        try:
            relay_flags = ast.literal_eval(m.group(1))
        except (ValueError, SyntaxError) as exc:
            bad.append(f"{name} could not be parsed ({exc})")
            continue
        mine, theirs = live_edge_of(FLAGS[key]), live_edge_of(relay_flags)
        if mine != theirs:
            bad.append(f"{name}: the relay serves --hls-live-edge {theirs}, this probe "
                       f"mirrors {mine}")
        if FLAGS[key] != relay_flags:
            bad.append(f"{name} drifted: relay {relay_flags}, probe {FLAGS[key]}")
    return bad


def platform_of(url):
    """Platform by HOST, not by substring — the relay does the same (is_channel /
    _is_stream_url). `"twitch.tv" in url` would also match a query string or a value
    crafted to look like a flag."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host == "twitch.tv" or host.endswith(".twitch.tv"):
        return "twitch"
    return "youtube"


def https_url(value):
    """argparse type: accept only an https URL with a host. The probe hands this value
    to streamlink as a POSITIONAL, where a leading '-' would become an option and
    streamlink has options that run programs (--player, --ffmpeg-ffmpeg). Self-injection
    only, but the guard is one line and matches the relay's `--` convention."""
    u = urllib.parse.urlparse(value)
    if u.scheme != "https" or not u.hostname:
        raise argparse.ArgumentTypeError(f"expected an https:// URL with a host, got {value!r}")
    return value


def resolve(url, platform, cookies=None):
    """Twitch goes straight through streamlink's plugin; YouTube needs yt-dlp to hand
    over the live HLS URL, exactly as the relay does it. Without the cookie jar YouTube
    offers no MUXED rendition for a live stream, so `b[height<=1080]/b` fails and the
    probe would measure nothing — pass a COPY of the jar, never the relay's own file
    (yt-dlp rewrites it)."""
    if platform == "twitch":
        return url, None
    cmd = ["yt-dlp", "-g", "-f", "b[height<=1080]/b", "--no-warnings", "--no-playlist"]
    if cookies:
        cmd += ["--cookies", cookies]
    cmd += ["--", url]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if p.returncode != 0 or not p.stdout.strip():
        return None, (p.stderr or p.stdout).strip().splitlines()[-1:] or ["yt-dlp failed"]
    return p.stdout.strip().splitlines()[0], None


def measure(target, platform, tier, gap_s, max_s):
    """One run: timestamp stdout chunks, return (burst_span_s, burst_bytes, gaps)."""
    # `--` before the positional, the same guard streamlink_fanout_cmd uses.
    cmd = ["streamlink", *FLAGS[(platform, tier)], "--stdout", "--", target, "best"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    first = None
    prev = None
    burst_end = None
    burst_bytes = 0
    gaps = []
    try:
        while True:
            chunk = proc.stdout.read1(65536)
            if not chunk:
                break
            now = time.monotonic()
            if first is None:
                first = prev = now
            delta = now - prev
            if burst_end is None:
                if delta > gap_s:
                    burst_end = prev          # the burst ended at the LAST byte before the gap
                else:
                    burst_bytes += len(chunk)
            if delta > gap_s:
                gaps.append(round(delta, 2))
            prev = now
            if now - first > max_s:
                break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    if first is None:
        return None
    span = (burst_end - first) if burst_end is not None else (prev - first)
    return {"burst_span_s": round(span, 2), "burst_bytes": burst_bytes,
            "burst_ended": burst_end is not None, "gaps": gaps[:6]}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, type=https_url,
                    help="a LIVE YouTube or Twitch URL (https only)")
    ap.add_argument("--tier", default="full", choices=("full", "robust"))
    ap.add_argument("--runs", type=int, default=3, help="repeat the measurement N times")
    ap.add_argument("--gap", type=float, default=0.5,
                    help="inter-arrival gap (s) that ends the burst (default 0.5)")
    ap.add_argument("--max-s", type=float, default=45.0, help="seconds to observe per run")
    ap.add_argument("--cookies", help="path to the yt-cookies.txt jar (YouTube needs it "
                                      "for a muxed live rendition). The probe works on a "
                                      "temporary COPY, so the relay's own jar is never "
                                      "rewritten by yt-dlp.")
    ap.add_argument("--json", action="store_true", help="machine-readable result")
    args = ap.parse_args()

    drift = check_flags_match_relay()
    if drift:
        for d in drift:
            print(f"ERROR: {d}", file=sys.stderr)
        print("The probe's mirrored flags no longer match the relay — fix them before "
              "trusting a number from this run.", file=sys.stderr)
        return 2

    if os.path.exists(os.path.join(ROOT, "runtime", "relay.pid")):
        print("ERROR: a relay PID file exists. A second puller on the same source is how "
              "a YouTube 429 starts — stop the relay first.", file=sys.stderr)
        return 2

    platform = platform_of(args.url)
    # yt-dlp REWRITES the jar it is handed, so it never gets the real one: a probe run
    # must not be able to log the relay's live session out. Copied into a private temp
    # dir and discarded afterwards.
    with tempfile.TemporaryDirectory(prefix="racecast-probe-") as tmp:
        jar = None
        if args.cookies:
            jar = os.path.join(tmp, "cookies.txt")
            try:
                shutil.copy2(args.cookies, jar)
                os.chmod(jar, 0o600)
            except OSError as exc:
                print(f"ERROR: could not copy the cookie jar ({exc})", file=sys.stderr)
                return 2
        target, err = resolve(args.url, platform, jar)
        if target is None:
            print(f"ERROR: could not resolve {args.url}: {err}", file=sys.stderr)
            return 2
        return _run_measurements(args, platform, target)


def _run_measurements(args, platform, target):

    results = []
    for i in range(args.runs):
        if not args.json:
            print(f"run {i + 1}/{args.runs} ({platform}, {args.tier}) …", flush=True)
        r = measure(target, platform, args.tier, args.gap, args.max_s)
        if r is None:
            print("ERROR: no bytes arrived — is the source live?", file=sys.stderr)
            return 2
        results.append(r)
        if not args.json:
            end = "burst ended" if r["burst_ended"] else "NO GAP SEEN (VOD? not live?)"
            print(f"  burst {r['burst_span_s']:.2f} s, {r['burst_bytes'] / 1e6:.1f} MB "
                  f"— {end}; later gaps {r['gaps']}")

    spans = [r["burst_span_s"] for r in results if r["burst_ended"]]
    out = {"url": args.url, "platform": platform, "tier": args.tier,
           "flags": FLAGS[(platform, args.tier)], "runs": results,
           "burst_span_max_s": max(spans) if spans else None}
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print()
        if spans:
            print(f"burst arrival span: max {max(spans):.2f} s over {len(spans)} clean run(s)")
            print("The rejoin must wait longer than this PLUS RACECAST_FEED_PREBUFFER_S.")
        else:
            print("No run saw the burst end — the source is not behaving like a live "
                  "stream, so this measurement says nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

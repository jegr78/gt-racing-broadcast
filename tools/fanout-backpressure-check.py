#!/usr/bin/env python3
"""#533 fan-out trailing-cursor prebuffer, local back-pressure validation.

Proves that a real 1x-playout consumer, an ffmpeg -re standing in for an OBS
ffmpeg media source, HOLDS the relay's trailing reserve at ~N seconds instead of
reading ahead and draining it back to the live edge.

It exercises the real production join path, `fanout_join_offset` and `FeedRing`
with its time index and `read`, from `src/relay/racecast-feeds.py`, with a real
ffmpeg producer feeding the ring at ~1x and a real ffmpeg consumer reading at ~1x
over loopback. The serve loop here is a byte-for-byte copy of
`FeedFanoutServer._serve`'s cursor/read/send core plus a `sent` counter so the
reserve can be measured from outside; nothing about the join or the ring is
re-implemented.

Reserve is read straight off the ring's time index: the consumer's current byte
`join_offset + sent` became live at some monotonic ts, so `now - that_ts` is the
reserve depth in seconds. If it holds ~N, a source gap shorter than N is absorbed,
because N seconds of already-buffered bytes sit ahead of the consumer. If it drains
toward 0, `RACECAST_FEED_PREBUFFER_S=0` reverts instantly and the spec's Fallback B,
paced de-jitter, is the escalation.

Maintainer-only: needs ffmpeg on PATH, NOT shipped, NOT run in CI.

    python3 tools/fanout-backpressure-check.py [--prebuffer 3.0] [--duration 90]
                                               [--bitrate 2500k] [--port 0]

Exit: 0 = PASS (reserve held), 1 = FAIL (reserve drained), 2 = SKIP (no ffmpeg).
"""
import argparse
import importlib.util
import os
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load_relay():
    spec = importlib.util.spec_from_file_location(
        "irofeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def ts_at_offset(marks, off):
    """When the byte at absolute `off` became live: the ts of the smallest mark
    whose offset >= off, the write that first reached it. marks is the ring's
    (offset, ts) time index, ascending. Returns None when `off` is past the newest
    mark, so the caller skips that sample, and the oldest ts when `off` scrolled out
    of retention, which is the maximum reserve."""
    if not marks:
        return None
    if off < marks[0][0]:
        return marks[0][1]
    for m_off, m_ts in marks:
        if m_off >= off:
            return m_ts
    return None


class _Feed:
    """Shared measurement state between the serve thread and the sampler."""
    def __init__(self):
        self.join_offset = None
        self.sent = 0
        self.lock = threading.Lock()


def serve_once(conn, ring, prebuffer_s, feed, relay_mod, stop, mode):
    """Serve one consumer, driving the REAL production code from the relay module.

    mode="cap":  the SHIPPED path: trailing START via `fanout_join_offset`, then
      `fanout_capped_read` each cycle, the continuous wall-clock high-water cap.
      This IS FeedFanoutServer._serve's read logic, so a PASS here validates the
      shipped function, not a re-implementation.
    mode="join": the pre-fix behaviour for contrast: trailing start, then read to
      the live edge with no cap, which a greedy consumer drains.

    `sent` is counted so the sampler can locate the consumer's cursor."""
    try:
        conn.recv(65536)
        conn.sendall(b"HTTP/1.0 200 OK\r\n"
                     b"Content-Type: video/mp2t\r\n"
                     b"Connection: close\r\n\r\n")
        cursor = relay_mod.fanout_join_offset(ring, prebuffer_s, time.monotonic())
        with feed.lock:
            feed.join_offset = cursor
            feed.sent = 0
        while not stop.is_set() and not ring.closed:
            if mode == "cap":
                data, cursor = relay_mod.fanout_capped_read(ring, cursor, prebuffer_s)
            else:
                data, cursor = ring.read(cursor, timeout=1.0)
            if data:
                conn.sendall(data)                    # blocks under back-pressure
                with feed.lock:
                    feed.sent += len(data)
    except OSError:
        pass                                  # consumer went away, end this serve
    finally:
        try:
            conn.close()
        except OSError:
            pass                              # already closed


def ffmpeg_producer_cmd(bitrate):
    # -re paces lavfi to realtime, so the ring fills at ~1x like a live stream.
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-re",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000",
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
        "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", bitrate,
        "-g", "60", "-c:a", "aac", "-b:a", "128k",
        "-f", "mpegts", "-",
    ]


def ffmpeg_consumer_cmd(url):
    # -re makes ffmpeg read the network input at native 1x rate, the OBS
    # media-source behaviour under test. A small probe bounds the startup gulp.
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-probesize", "1000000", "-analyzeduration", "2000000",
        "-re", "-i", url, "-f", "null", "-",
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prebuffer", type=float, default=3.0,
                    help="trailing prebuffer under test, seconds (default 3.0)")
    ap.add_argument("--duration", type=float, default=90.0,
                    help="consumer run length, seconds (default 90)")
    ap.add_argument("--warmup", type=float, default=12.0,
                    help="seconds ignored after consumer start (probe gulp) (default 12)")
    ap.add_argument("--bitrate", default="2500k", help="producer video bitrate (default 2500k)")
    ap.add_argument("--port", type=int, default=0, help="loopback serve port (default: ephemeral)")
    ap.add_argument("--mode", choices=("join", "cap"), default="cap",
                    help="cap (default) = the SHIPPED continuous trailing cap, validates "
                         "production; join = the disproven static-start path, kept as the "
                         "failure-repro contrast (exits FAIL by design)")
    args = ap.parse_args()

    if not shutil.which("ffmpeg"):
        print("SKIP: ffmpeg not found on PATH; this is a maintainer tool that needs ffmpeg.")
        return 2

    m = load_relay()
    ring = m.FeedRing(m.FANOUT_RING_BYTES)
    feed = _Feed()
    stop = threading.Event()

    prod = subprocess.Popen(ffmpeg_producer_cmd(args.bitrate),
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def pump():
        try:
            while not stop.is_set():
                chunk = prod.stdout.read(65536)
                if not chunk:
                    break
                ring.write(chunk)
        except (OSError, ValueError):
            pass                              # producer pipe or ring closed on teardown
    threading.Thread(target=pump, daemon=True).start()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", args.port))
    port = srv.getsockname()[1]
    srv.listen(1)

    def accept_loop():
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        serve_once(conn, ring, args.prebuffer, feed, m, stop, args.mode)
    threading.Thread(target=accept_loop, daemon=True).start()

    url = f"http://127.0.0.1:{port}"
    print(f"mode={args.mode} | prebuffer under test: {args.prebuffer:.1f}s "
          f"| ring {m.FANOUT_RING_BYTES // (1024*1024)}MB | producer {args.bitrate} | serve {url}")

    # The consumer must find more than `prebuffer` seconds of history to join at.
    warm_deadline = time.monotonic() + max(args.prebuffer + 3.0, 6.0)
    while time.monotonic() < warm_deadline and prod.poll() is None:
        time.sleep(0.25)
    print(f"ring warmed ({ring.live_offset() // 1024} KiB retained); starting 1x consumer...")

    cons = subprocess.Popen(ffmpeg_consumer_cmd(url), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)

    samples = []               # (elapsed_s, reserve_s)
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < args.duration:
            time.sleep(1.0)
            if cons.poll() is not None or prod.poll() is not None:
                print("WARN: an ffmpeg process exited early; stopping sampling.")
                break
            now = time.monotonic()
            with feed.lock:
                jo, sent = feed.join_offset, feed.sent
            if jo is None:
                continue
            with ring._cond:
                marks = list(ring._marks)
                live = ring._base + len(ring._buf)
            cursor = jo + sent
            ts = ts_at_offset(marks, cursor)
            if ts is None:
                continue
            reserve = now - ts
            elapsed = now - t0
            gap_bytes = live - cursor
            samples.append((elapsed, reserve))
            if elapsed >= args.warmup:
                print(f"  t={elapsed:5.1f}s  reserve={reserve:5.2f}s  "
                      f"(gap {gap_bytes // 1024} KiB, consumer {sent // 1024} KiB served)")
    finally:
        stop.set()
        for p in (cons, prod):
            try:
                p.terminate()
            except OSError:
                pass                          # process already exited
        try:
            srv.close()
        except OSError:
            pass                              # listener already closed
        ring.close()

    steady =[r for (e, r) in samples if e >= args.warmup]
    if len(steady) < 5:
        print(f"\nINCONCLUSIVE: only {len(steady)} steady-state samples "
              f"(needed >=5). Re-run with a longer --duration.")
        return 2

    med = statistics.median(steady)
    lo = min(steady)
    last_third = steady[-max(3, len(steady) // 3):]
    med_last = statistics.median(last_third)

    hold_floor = 0.6 * args.prebuffer      # median must stay above this
    drain_floor = 0.5 * args.prebuffer     # the last third must not trend to zero
    passed = med >= hold_floor and med_last >= drain_floor and lo >= 0.35 * args.prebuffer

    print("\n" + "=" * 64)
    print(f"steady-state reserve over {len(steady)} samples "
          f"(target ~{args.prebuffer:.1f}s):")
    print(f"  median   {med:5.2f}s   min {lo:5.2f}s   "
          f"last-third median {med_last:5.2f}s")
    if passed:
        print("VERDICT: PASS, a real 1x consumer HELD the trailing reserve near N.")
        print("  => the continuous cap holds on this machine; a source gap < N is absorbed.")
        return 0
    print("VERDICT: FAIL, the reserve drained below the hold floor "
          f"({hold_floor:.2f}s).")
    print("  => on this machine the 1x consumer reads ahead and empties the reserve.")
    print("  => set RACECAST_FEED_PREBUFFER_S=0 (instant revert) and escalate to")
    print("     the spec's Fallback B (paced de-jitter delivery).")
    return 1


if __name__ == "__main__":
    sys.exit(main())

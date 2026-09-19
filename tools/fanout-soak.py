#!/usr/bin/env python3
"""Local fan-out soak harness (#488) — maintainer, NOT shipped, NOT run in CI.

Drives the REAL relay FeedRing + FeedFanoutServer from a source (synthetic ffmpeg -re, or
a real stream via streamlink) into your LOCAL OBS, so a long real-OBS soak can be run and
observed. It SERVES + LOGS only — the automatic OBS rebuild lives in the RELAY (its
cursor-progress freeze detector, guarded by #582), NOT here; the socket send-block
"stuck"/snap logged below proved BLIND to render drift (see the spec's DESIGN PIVOT) and is
kept only to confirm the ring is fed. Measure render skip directly off OBS (obs-ws GetStats).

No cloud box needed; a real stream needs streamlink but no cookies for a public live.

Usage:
    # 1. Run the harness (prints the URL to point OBS at):
    python3 tools/fanout-soak.py --port 53001                         # synthetic testsrc
    python3 tools/fanout-soak.py --port 53001 --source <URL> --quality 720p60   # real stream
    # 2. In OBS add a Media Source, uncheck "Local File", URL http://127.0.0.1:53001.
    # 3. Watch the log: t / stuck / snaps lines. Let it run for a long soak.

    # Trigger-B check (inject a 3 s source stall every 30 s):
    python3 tools/fanout-soak.py --port 53001 --stall-period 30 --stall-duration 3

    # Manual quality switch (rebuild source at new tier after delay):
    python3 tools/fanout-soak.py --port 53001 --source <URL> --switch-to robust --switch-after 60
"""
import argparse
import importlib.util
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, *rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def soak_stall_active(elapsed_s, *, period_s, duration_s):
    """True during the injected-stall window (the last `duration_s` of every
    `period_s`). period_s<=0 or duration_s<=0 disables. Pure — unit-tested."""
    if period_s <= 0 or duration_s <= 0:
        return False
    return (elapsed_s % period_s) >= (period_s - duration_s)


FFMPEG_CMD = [
    "ffmpeg", "-hide_banner", "-loglevel", "error",
    "-re", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=60",
    "-f", "lavfi", "-i", "sine=frequency=1000",
    "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "8M", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-f", "mpegts", "-",
]


def _build_source_cmd(fe, source, quality, tier, platform):
    """Build a source command. `testsrc` -> the synthetic ffmpeg -re pipe. A real
    URL with a `tier` (a --switch-to rebuild) delegates ENTIRELY to the relay's own
    `streamlink_fanout_cmd` builder (#493) — the exact per-tier buffer/live-edge
    profile (`streamlink_serve_flags`/`streamlink_twitch_flags`) and quality
    selector a real feed would run, not a hand-rolled partial argv that silently
    dropped the tier. Without a tier, use the plain --quality pull."""
    if source == "testsrc":
        return FFMPEG_CMD
    if tier:
        return fe.streamlink_fanout_cmd(source, platform, tier=tier)
    return ["streamlink", "--stdout", "--", source, quality]


def main():
    # Line-buffer our progress output so a `| tee runtime/soak.log` (or any redirect)
    # captures each line immediately instead of block-buffering it — and so a Ctrl-C
    # never loses the buffered tail. Without this, piped stdout is block-buffered and
    # the log file looks empty for minutes.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
        pass  # non-standard stdout — best effort
    ap = argparse.ArgumentParser(description="Fan-out soak harness (#488)")
    ap.add_argument("--port", type=int, default=53001)
    ap.add_argument("--stall-period", type=float, default=0.0, help="s between injected stalls (0=off)")
    ap.add_argument("--stall-duration", type=float, default=3.0, help="s each injected stall lasts")
    ap.add_argument("--log-interval", type=float, default=5.0)
    ap.add_argument("--source", default="testsrc",
                    help="'testsrc' (default synthetic ffmpeg -re) or a stream URL pulled "
                         "via `streamlink --stdout -- <url> <quality>` (real VBR content = the box condition)")
    ap.add_argument("--quality", default="best",
                    help="streamlink quality for a URL --source (e.g. 720p60; default best)")
    ap.add_argument("--switch-to", choices=["full", "robust", "emergency"], default=None,
                    help="After --switch-after seconds, rebuild the source pull at this quality tier")
    ap.add_argument("--switch-after", type=float, default=60.0,
                    help="Seconds to wait before switching tiers (default 60)")
    args = ap.parse_args()

    fe = _load("irofeeds", "src", "relay", "racecast-feeds.py")

    # Detect platform the same way the relay does (twitch.tv host vs default
    # YouTube; used only for a --switch-to tier rebuild).
    platform = fe.platform_of(args.source) if args.source != "testsrc" else None

    ring = fe.FeedRing(fe.FANOUT_RING_BYTES)
    srv = fe.FeedFanoutServer("127.0.0.1", args.port, ring, fe.logging.getLogger("soak"))
    srv.start()
    print(f"[soak] serving on http://127.0.0.1:{srv.port}  — point OBS Media Source at it")
    print(f"[soak] ring={fe.FANOUT_RING_BYTES}B  (the automatic OBS rebuild lives in the RELAY; "
          f"this harness only serves + logs the socket side)")

    # Build initial source command
    source_cmd = _build_source_cmd(fe, args.source, args.quality, None, platform)
    print(f"[soak] source: {args.source} ({' '.join(source_cmd[:2])}...)")
    if args.switch_to:
        print(f"[soak] will switch to tier '{args.switch_to}' after {args.switch_after:.1f}s")

    proc = subprocess.Popen(source_cmd, stdout=subprocess.PIPE)
    stop = threading.Event()
    rebuild_event = threading.Event()  # signal to rebuild the proc at new tier
    switched = threading.Event()       # latch: the tier switch fires AT MOST ONCE (#493)
    started = time.monotonic()

    def _reap(old_proc, timeout=5.0):
        """Wait for a terminated child to actually exit so it never lingers as a
        zombie; escalate to kill() if terminate() didn't land in time. Best-effort."""
        try:
            old_proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                old_proc.kill()
            except OSError:
                pass  # process already gone
            try:
                old_proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass  # kill() didn't land in time either — give up, best-effort
        except OSError:
            pass  # process already reaped/gone

    def _monitor():
        # The socket send-block ("stuck") / cursor-snaps proved BLIND to OBS render drift
        # (see the spec pivot) — logged here only to confirm the ring is fed. The render-skip
        # signal is measured directly off OBS (obs-ws GetStats), not here.
        while not stop.is_set():
            time.sleep(args.log_interval)
            now = time.monotonic()
            stuck_s, snaps = srv.consumer_health(now)
            print(f"[soak] t={now-started:7.1f}s stuck={('-' if stuck_s is None else f'{stuck_s:.1f}')}s "
                  f"snaps={snaps}")

            # Check if it's time to trigger the (one-shot) tier switch. `switched`
            # latches permanently once fired — unlike `rebuild_event` (which the main
            # loop clears after consuming it), this guard is never cleared, so a
            # threshold that stays true forever (every log-interval tick after
            # switch_after) cannot re-fire the rebuild (#493 — was killing/respawning
            # the source every log-interval and leaking a zombie each time).
            if (args.switch_to and not switched.is_set() and
                (now - started) >= args.switch_after):
                print(f"[soak] triggering rebuild to tier '{args.switch_to}'")
                switched.set()
                rebuild_event.set()
                old_proc = proc
                try:
                    old_proc.terminate()
                except OSError:
                    pass  # process already gone
                else:
                    _reap(old_proc)   # avoid leaving a zombie behind

    threading.Thread(target=_monitor, daemon=True).start()
    try:
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                # EOF: check if we should rebuild at a new tier
                if rebuild_event.is_set():
                    rebuild_event.clear()
                    new_cmd = _build_source_cmd(fe, args.source, args.quality, args.switch_to, platform)
                    print(f"[soak] rebuilding at tier '{args.switch_to}': {' '.join(new_cmd[:3])}...")
                    proc = subprocess.Popen(new_cmd, stdout=subprocess.PIPE)
                    continue
                # No rebuild requested — we're done
                break
            if not soak_stall_active(time.monotonic() - started,
                                     period_s=args.stall_period, duration_s=args.stall_duration):
                ring.write(chunk)          # withhold during an injected stall
    except KeyboardInterrupt:
        pass  # Ctrl-C → fall through to cleanup
    finally:
        stop.set()
        try:
            proc.terminate()
        except OSError:
            pass  # process already gone
        else:
            _reap(proc)
        srv.stop()
        print("[soak] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())

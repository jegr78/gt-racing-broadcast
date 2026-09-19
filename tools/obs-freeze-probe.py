#!/usr/bin/env python3
"""Freeze-detection signal probe (maintainer diagnostic, NOT shipped).

Purpose
-------
The recurring "OBS stutter/freeze" (a fresh streamlink stream spliced onto OBS's
STALE ffmpeg demuxer in fan-out mode — the picture freezes at ~1 Hz) is INVISIBLE
to every metric #488 relies on: OBS's encoder FPS holds at 60, dropped/skipped
frames stay flat, and OBS reads the feed socket greedily regardless of render
state (all confirmed live). So `renderSkippedFrames` (a compositor render-timing
metric) never tripped the #488 render-skip auto-resync (removed in #582).

We now have a DETERMINISTIC repro: ARM -> STOP -> ARM on the on-air feed
(`/feed/<X>/activate` -> `/feed/<X>/deactivate` -> `/feed/<X>/activate`). This
probe samples every candidate detection signal SIMULTANEOUSLY while you drive
that repro, so we can see which signal actually distinguishes a frozen picture
from a healthy live one — before we wire a reliable check into the relay.

Candidate signals sampled per tick, per relay feed input (A=53001, B=53002,
POV=53003):
  * IMG   — GetSourceScreenshot of the FEED SOURCE (not the program: the program
            carries the live HUD timer, which changes every second and would mask
            a frozen feed). Small lossless PNG -> sha1. Identical hash across ticks
            == identical pixels == frozen source. This is the ground-truth signal;
            it is cause-agnostic (catches ARM/STOP, drift, and unknown causes).
  * CURSOR — GetMediaInputStatus mediaState + mediaCursor(ms). Cheap if it moves;
            we do not yet know whether OBS advances the cursor for a live HLS
            ffmpeg source or freezes it with the picture. The probe finds out.
  * STATS — GetStats render-skip RATE (delta), activeFps, output-skipped. The
            KNOWN-BLIND control: we expect these to stay flat through the freeze,
            reproducing the "OBS stats looked fine" observation in-tool.
Relay /status (best-effort) annotates each feed's serve state, so the log shows
the smoking gun: feed SERVING (bytes flowing into the ring) while the picture is
FROZEN and every OBS stat looks healthy.

Usage
-----
  python3 tools/obs-freeze-probe.py               # 1 Hz, auto-discovers OBS + feeds
  python3 tools/obs-freeze-probe.py --interval 0.5 --width 96 --freeze-threshold 3
  python3 tools/obs-freeze-probe.py --no-relay    # skip the relay /status annotation

Then, in the Director Panel, do ARM -> STOP -> ARM on the on-air feed and watch
which column flips. Ctrl-C prints a per-feed summary.

Read-only against OBS (GetStats/GetInputList/GetInputSettings/GetSourceScreenshot/
GetMediaInputStatus only — never SetInputSettings); the relay poll is a plain
loopback GET. Nothing here changes broadcast state.
"""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request

# Import the shipped obs-websocket client (stdlib-only) the same way tests do.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "scripts"))
import obs_ws  # noqa: E402

PORT_LABEL = {53001: "A", 53002: "B", 53003: "POV"}


def feed_inputs_by_label(session):
    """{label: obs_input_name} for the relay feed media sources currently in OBS.
    Resolves each ffmpeg_source's network URL to its feed port, then labels it
    A/B/POV. Empty when OBS holds no relay feed inputs (nothing imported yet)."""
    inputs = session.request("GetInputList",
                             {"inputKind": "ffmpeg_source"}).get("inputs", [])
    out = {}
    for inp in inputs:
        name = inp.get("inputName")
        if not name:
            continue
        try:
            settings = session.request("GetInputSettings",
                                       {"inputName": name}).get("inputSettings", {})
        except Exception:                       # one bad input must not stop the rest
            continue
        if settings.get("is_local_file"):
            continue
        url = settings.get("input")
        if not isinstance(url, str):
            continue
        netloc = urllib.parse.urlsplit(url.strip()).netloc
        for port, label in PORT_LABEL.items():
            if netloc in (f"127.0.0.1:{port}", f"localhost:{port}"):
                out[label] = name
    return out


def screenshot_hash(session, name, width):
    """sha1 of a small lossless PNG of the source, or None on a request error
    (e.g. the input is mid-rebuild during a RESET). PNG is lossless, so an
    unchanged frame yields byte-identical data -> a stable hash."""
    try:
        resp = session.request(
            "GetSourceScreenshot",
            obs_ws.screenshot_request_data(name, width, "png", 60))
        data = obs_ws.parse_screenshot_data_uri(resp.get("imageData"))
        return hashlib.sha1(data).hexdigest() if data else None
    except Exception:
        return None


def media_status(session, name):
    """(mediaState, mediaCursor_ms) for a media input, or (None, None)."""
    try:
        r = session.request("GetMediaInputStatus", {"inputName": name})
        return r.get("mediaState"), r.get("mediaCursor")
    except Exception:
        return None, None


def relay_feed_states(base, timeout=1.0):
    """{label: state_str} from the relay /status feeds, best-effort ('' on any
    failure). Loopback GET to our own relay — no external host, no UA needed."""
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/status", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return {}
    out = {}
    for k, v in (data.get("feeds") or {}).items():
        st = "DOWN" if v.get("down") else (v.get("state") or "?")
        out[k.upper()] = st
    return out


def render_skip_rate(stats, prev):
    """Per-interval render-skip fraction from successive GetStats samples (the
    same delta the relay records for its health chart), or None."""
    sk, tot = stats.get("obs_render_skipped_frames"), stats.get("obs_render_total_frames")
    if sk is None or tot is None or prev is None:
        return None
    d_tot = tot - prev[1]
    if d_tot <= 0:
        return 0.0
    return (sk - prev[0]) / d_tot


def main(argv=None):
    ap = argparse.ArgumentParser(description="OBS freeze-detection signal probe (maintainer diagnostic).")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between samples (default 1.0)")
    ap.add_argument("--width", type=int, default=64, help="feed-screenshot width px (default 64; small = cheap + lossless)")
    ap.add_argument("--freeze-threshold", type=float, default=4.0,
                    help="seconds the IMG hash must stay unchanged to flag FROZEN (default 4)")
    ap.add_argument("--relay", default="http://127.0.0.1:8088", help="relay base for /status annotation")
    ap.add_argument("--no-relay", action="store_true", help="skip the relay /status annotation")
    ap.add_argument("--out", default=None, help="also write every output line to this file (line-buffered)")
    ap.add_argument("--host", default="127.0.0.1", help="OBS-WebSocket host")
    ap.add_argument("--port", type=int, default=None, help="OBS-WebSocket port (default: OBS config / 4455)")
    ap.add_argument("--password", default=None, help="OBS-WebSocket password (default: auto-discovered)")
    args = ap.parse_args(argv)

    outf = open(args.out, "a", buffering=1, encoding="utf-8") if args.out else None  # noqa: SIM115

    def emit(s):
        print(s)
        if outf:
            outf.write(s + "\n")

    session, note = obs_ws._connect(args.host, args.port, args.password, timeout=3.0)
    if session is None:
        print(f"cannot reach OBS: {note}", file=sys.stderr)
        return 2

    feeds = feed_inputs_by_label(session)
    if not feeds:
        print("no relay feed inputs found in OBS (import the collection / start the relay first).",
              file=sys.stderr)
        session.close()
        return 2
    order = [lbl for lbl in ("A", "B", "POV") if lbl in feeds]
    emit("watching feeds: " + ", ".join(f"{lbl}={feeds[lbl]!r}" for lbl in order))
    emit("legend: IMG SAME/CHG (frozen=Ns) | CURSOR state +deltams | STATS skip% fps | relay=serve-state")
    emit("drive ARM -> STOP -> ARM on the on-air feed and watch which column flips. Ctrl-C for summary.\n")

    prev_hash = {lbl: None for lbl in order}
    last_change = {lbl: time.time() for lbl in order}
    prev_cursor = {lbl: None for lbl in order}
    prev_counts = None
    # summary accounting
    freeze_episodes = {lbl: 0 for lbl in order}      # transitions into FROZEN while serving
    max_freeze = {lbl: 0.0 for lbl in order}
    was_frozen = {lbl: False for lbl in order}
    stats_moved = False

    try:
        while True:
            now = time.time()
            try:
                stats = obs_ws.parse_obs_stats(session.request("GetStats", {}))
            except Exception:                        # session dropped (e.g. mid-RESET) -> reconnect
                session.close()
                session, note = obs_ws._connect(args.host, args.port, args.password, timeout=3.0)
                if session is None:
                    print(f"OBS connection lost: {note}", file=sys.stderr)
                    return 2
                continue
            rate = render_skip_rate(stats, prev_counts)
            if stats.get("obs_render_skipped_frames") is not None:
                prev_counts = (stats["obs_render_skipped_frames"], stats["obs_render_total_frames"])
            if rate is not None and rate > 0.0:
                stats_moved = True

            rstate = {} if args.no_relay else relay_feed_states(args.relay)

            cells = []
            for lbl in order:
                name = feeds[lbl]
                h = screenshot_hash(session, name, args.width)
                changed = h is not None and h != prev_hash[lbl]
                if changed or prev_hash[lbl] is None:
                    last_change[lbl] = now
                if h is not None:
                    prev_hash[lbl] = h
                frozen_for = now - last_change[lbl]
                # only call it a freeze when the feed is actually serving (bytes flowing)
                is_frozen = (frozen_for >= args.freeze_threshold
                             and (rstate.get(lbl) == "serving" if rstate else True))
                if is_frozen and not was_frozen[lbl]:
                    freeze_episodes[lbl] += 1
                if is_frozen:
                    max_freeze[lbl] = max(max_freeze[lbl], frozen_for)
                was_frozen[lbl] = is_frozen

                mstate, cursor = media_status(session, name)
                dcur = "" if (cursor is None or prev_cursor[lbl] is None) else f"{cursor - prev_cursor[lbl]:+d}ms"
                prev_cursor[lbl] = cursor

                img = "n/a" if h is None else ("CHG " if changed else "SAME")
                frz = f" frozen={frozen_for:0.0f}s" if frozen_for >= args.freeze_threshold else ""
                flag = "  <<< FROZEN" if is_frozen else ""
                rel = f" relay={rstate.get(lbl, '?')}" if rstate else ""
                cells.append(f"{lbl}[{img}{frz} | {mstate or '-'} {dcur or '-'}{rel}]{flag}")

            skip_s = "-" if rate is None else f"{rate * 100:0.1f}%"
            fps = stats.get("obs_fps")
            fps_s = "-" if fps is None else f"{fps:0.0f}"
            statline = f"STATS skip={skip_s} fps={fps_s}"
            ts = time.strftime("%H:%M:%S", time.localtime(now))
            emit(f"{ts}  " + "  ".join(cells) + f"  |  {statline}")

            time.sleep(max(0.05, args.interval))
    except KeyboardInterrupt:
        pass  # Ctrl-C ends the run -> fall through to the summary
    finally:
        session.close()

    emit("\n--- summary ---")
    for lbl in order:
        emit(f"feed {lbl}: freeze episodes={freeze_episodes[lbl]}  longest={max_freeze[lbl]:0.0f}s")
    emit(f"OBS render-skip rate ever moved above 0%: {'YES' if stats_moved else 'NO'}")
    emit("If IMG went SAME/FROZEN while relay=serving AND render-skip stayed 0% -> the picture froze\n"
         "with bytes still flowing and OBS stats blind: the screenshot frame-diff (and possibly the\n"
         "media cursor) is the reliable signal; renderSkippedFrames is not.")
    if outf:
        outf.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

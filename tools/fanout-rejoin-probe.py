#!/usr/bin/env python3
"""Fan-out REJOIN probe (#577) — maintainer diagnostic, NOT shipped, NOT run in CI.

With fan-out on, the relay sets `close_when_inactive=True` on Feed A/B, so OBS drops
an off-air feed and joins it again MID-STREAM on every scene activation. MPEG-TS
survives that join on its own; fMP4/CMAF (served by some Twitch channels, decided
per channel) only survives it with the relay's init-segment repair (#576/#577).
Unit tests prove the bytes; this probe proves the PICTURE in real OBS.

What it does:
  1. Pulls one live stream with `streamlink --stdout` into the REAL FeedRing +
     FeedFanoutServer (loaded from --relay-file, so an older relay can be measured
     for a before/after), on a loopback port that is not a relay feed port.
  2. Reports the stream's container from its first bytes (fMP4 = `ftyp` at 4).
  3. In the RUNNING OBS: adds two temporary scenes and one ffmpeg media source with
     the collection's Feed A settings plus `close_when_inactive=True`, exactly as
     fan-out runs them.
  4. Cycles off-air -> on-air --cycles times. After each rejoin it samples the
     source's media state, whether its cursor advances, and the mean luma (YAVG)
     of a source screenshot. A rejoin counts as PICTURE when the state is playing,
     the cursor advances and YAVG > --black-luma.
  5. Removes the temporary scenes/source and restores the previous program scene.

Refuses to run while an OBS output is active. Needs a LIVE stream: a VOD races
ahead of real time through the ring (no backpressure) and is not a valid test.

Find an fMP4 channel first (the relay passes no --twitch-supported-codecs, so
streamlink's default h264 is what production gets):
  streamlink --stdout https://www.twitch.tv/<login> best | head -c 16 | xxd
  -> bytes 4..7 == "ftyp" means fMP4.

Usage:
  python3 tools/fanout-rejoin-probe.py --source https://www.twitch.tv/<login>
  git show <old-sha>:src/relay/racecast-feeds.py > /tmp/feeds-old.py
  python3 tools/fanout-rejoin-probe.py --source <url> --relay-file /tmp/feeds-old.py
Exit code: 0 when every rejoin shows a picture, 1 otherwise, 2 on a setup error.
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import obs_ws  # noqa: E402

SCENE_FEED = "racecast rejoin probe - feed"
SCENE_IDLE = "racecast rejoin probe - idle"
INPUT_NAME = "racecast rejoin probe - source"
COLLECTION = os.path.join(ROOT, "src", "obs", "GT_Racing_Endurance.json")
_YAVG_RE = re.compile(r"lavfi\.signalstats\.YAVG=([\d.]+)")


def container_of(head):
    """'fMP4' | 'TS' | 'unknown' from the first bytes of a stream. Pure."""
    head = bytes(head or b"")
    if head[4:8] == b"ftyp":
        return "fMP4"
    if head[:1] == b"\x47":
        return "TS"
    return "unknown"


def rejoin_verdict(state, cursor_delta_ms, yavg, black_luma):
    """'PICTURE' or 'BLACK (<reasons>)' for one rejoin sample. Pure."""
    why = []
    if state != "OBS_MEDIA_STATE_PLAYING":
        why.append(f"state={state}")
    if cursor_delta_ms is None or cursor_delta_ms <= 0:
        why.append(f"cursor+{cursor_delta_ms}")
    if yavg is None or yavg <= black_luma:
        why.append(f"yavg={yavg}")
    return "PICTURE" if not why else "BLACK (" + ", ".join(why) + ")"


def feed_a_settings(path=COLLECTION):
    """The shipped collection's Feed A media-source settings, as fan-out runs them."""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    for src in doc.get("sources", []):
        if src.get("name") == "Feed A":
            settings = dict(src.get("settings") or {})
            settings["close_when_inactive"] = True    # what the relay sets under fan-out
            return settings
    raise SystemExit("Feed A not found in " + path)


def load_relay(path):
    spec = importlib.util.spec_from_file_location("rejoin_relay", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def luma(png):
    """Mean luma (YAVG, 0..255) of a PNG via ffmpeg signalstats, or None."""
    if not png:
        return None
    p = subprocess.run(["ffmpeg", "-hide_banner", "-f", "png_pipe", "-i", "-",
                        "-vf", "signalstats,metadata=print:file=-", "-f", "null", "-"],
                       input=png, capture_output=True, timeout=30)
    m = _YAVG_RE.search(p.stdout.decode("utf-8", "replace"))
    return round(float(m.group(1)), 1) if m else None


def sample(session, width):
    """(state, cursor_delta_ms over ~1 s, yavg) of the probe source."""
    def status():
        r = session.request("GetMediaInputStatus", {"inputName": INPUT_NAME})
        return r.get("mediaState"), r.get("mediaCursor")
    _state, c0 = status()
    time.sleep(1.0)
    state, c1 = status()
    delta = (c1 - c0) if isinstance(c0, (int, float)) and isinstance(c1, (int, float)) else None
    try:
        resp = session.request("GetSourceScreenshot",
                               obs_ws.screenshot_request_data(INPUT_NAME, width, "png", 60))
    except ValueError:
        return state, delta, None          # 702 "Failed to render": no frame at all
    return state, delta, luma(obs_ws.parse_screenshot_data_uri(resp.get("imageData")))


def stop_process(proc, timeout=5.0):
    """Terminate `proc` and wait for it, escalating to kill(), so no streamlink
    is left behind as a zombie or still pulling."""
    try:
        proc.terminate()
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=timeout)
    except OSError:
        pass  # already gone


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fan-out rejoin probe (#577), real OBS.")
    ap.add_argument("--source", required=True, help="LIVE stream URL (e.g. a Twitch channel)")
    ap.add_argument("--relay-file", default=os.path.join(ROOT, "src", "relay", "racecast-feeds.py"),
                    help="relay module to serve with (an older copy for a before/after)")
    ap.add_argument("--port", type=int, default=53011, help="loopback serve port (not a feed port)")
    ap.add_argument("--quality", default="best")
    ap.add_argument("--cycles", type=int, default=3, help="off-air -> on-air rejoins")
    ap.add_argument("--settle", type=float, default=6.0, help="s after activation before sampling")
    ap.add_argument("--off-air", type=float, default=4.0, help="s off air between rejoins")
    ap.add_argument("--warmup", type=float, default=12.0, help="s of stream in the ring before the first join")
    ap.add_argument("--prebuffer", type=float, default=3.0, help="trailing join mark (#533)")
    ap.add_argument("--black-luma", type=float, default=16.0)
    ap.add_argument("--width", type=int, default=320)
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
        pass  # non-standard stdout — best effort

    fe = load_relay(args.relay_file)
    session, note = obs_ws._connect("127.0.0.1", None, None, timeout=3.0)
    if session is None:
        print("cannot reach OBS: " + note, file=sys.stderr)
        return 2
    if session.request("GetStreamStatus", {}).get("outputActive") or \
            session.request("GetRecordStatus", {}).get("outputActive"):
        print("an OBS output is active; refusing to touch scenes", file=sys.stderr)
        session.close()
        return 2

    ring = fe.FeedRing(fe.FANOUT_RING_BYTES)
    srv = fe.FeedFanoutServer("127.0.0.1", args.port, ring, fe.logging.getLogger("rejoin"),
                              prebuffer_s=args.prebuffer).start()
    proc = subprocess.Popen(["streamlink", "--stdout", "--", args.source, args.quality],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def pump():
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                return
            ring.write(chunk)
    threading.Thread(target=pump, daemon=True).start()

    prev_scene = session.request("GetCurrentProgramScene", {}).get("currentProgramSceneName")
    results = []
    try:
        time.sleep(args.warmup)
        head = ring.head() if hasattr(ring, "head") else b""
        print(f"relay:     {args.relay_file}")
        print(f"source:    {args.source}  container={container_of(head)}  "
              f"ring={ring.live_offset()} bytes after {args.warmup:.0f}s")
        if ring.live_offset() == 0:
            print("no bytes from streamlink (offline?)", file=sys.stderr)
            return 2
        session.request("CreateScene", {"sceneName": SCENE_IDLE})
        session.request("CreateScene", {"sceneName": SCENE_FEED})
        settings = feed_a_settings()
        settings["input"] = f"http://127.0.0.1:{srv.port}"
        session.request("CreateInput", {"sceneName": SCENE_FEED, "inputName": INPUT_NAME,
                                        "inputKind": "ffmpeg_source", "inputSettings": settings,
                                        "sceneItemEnabled": True})
        session.request("SetCurrentProgramScene", {"sceneName": SCENE_IDLE})
        time.sleep(args.off_air)
        for n in range(1, args.cycles + 1):
            session.request("SetCurrentProgramScene", {"sceneName": SCENE_FEED})
            time.sleep(args.settle)
            state, delta, yavg = sample(session, args.width)
            verdict = rejoin_verdict(state, delta, yavg, args.black_luma)
            results.append(verdict)
            print(f"rejoin {n}: {verdict:40} state={state} cursor+{delta}ms yavg={yavg}")
            session.request("SetCurrentProgramScene", {"sceneName": SCENE_IDLE})
            time.sleep(args.off_air)
    except ValueError as exc:
        # An OBS request failed: a setup problem, not a measurement. Exit 1 is
        # reserved for "no picture", so this must not end as a traceback.
        print(f"OBS request failed: {exc}", file=sys.stderr)
        return 2
    finally:
        cleanup = [("RemoveInput", {"inputName": INPUT_NAME}),
                   ("RemoveScene", {"sceneName": SCENE_FEED}),
                   ("RemoveScene", {"sceneName": SCENE_IDLE})]
        if prev_scene:
            cleanup.insert(0, ("SetCurrentProgramScene", {"sceneName": prev_scene}))
        for req, data in cleanup:
            try:
                session.request(req, data)
            except Exception:                  # noqa: BLE001 — cleanup is best effort
                pass  # already gone / never created
        session.close()
        stop_process(proc)
        srv.stop()
    ok = bool(results) and all(r == "PICTURE" for r in results)
    print(f"RESULT: {sum(r == 'PICTURE' for r in results)}/{len(results)} rejoins with a picture")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

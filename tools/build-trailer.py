#!/usr/bin/env python3
"""Render the Racecast promo trailer deterministically (maintainer tool).

The trailer is an animated HTML page (``tools/trailer/trailer.html``) captured
one crisp frame at a time by pausing every CSS animation and seeking its
``currentTime``, then muxed with a music bed via ffmpeg. Because every frame is
a deterministic seek (not a real-time recording), the render is reproducible on
any machine: same page + same music -> byte-stable video.

The page pulls its screenshots from two places:
  * committed UI shots under ``/src/docs/...`` (served from the repo root), and
  * league broadcast graphics + a redacted Control-Center crop under
    ``/trailer-assets/...`` (served from ``--assets-dir``).
This script starts an in-process HTTP server that serves the repo root and maps
``/trailer-assets/`` onto the assets dir, so no separate ``python3 -m
http.server`` is needed. Assemble the assets dir first with
``tools/trailer/prepare-assets.py``.

Dependencies (not vendored): the Playwright Python library + a Chromium build
(the repo's ``.venv-pw`` venv) and ffmpeg/ffprobe on PATH. The music bed is NOT
shipped in the repo; supply your own royalty-free clip with ``--music``, e.g. a
download from the YouTube Studio Audio Library.

Usage:
    .venv-pw/bin/python tools/build-trailer.py all \\
        --assets-dir runtime/trailer/assets \\
        --music runtime/trailer/assets/the-theme.mp3 \\
        --out runtime/trailer/trailer.mp4

Modes: ``all`` (capture + mux), ``capture`` (fresh frames), ``resume`` (keep
existing frames, render only the missing ones), ``mux`` (frames -> mp4 only).
"""
import argparse
import functools
import http.server
import os
import socketserver
import subprocess
import threading
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
ASSET_MOUNT = "/trailer-assets/"


def _make_handler(repo_root, assets_dir):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def translate_path(self, path):
            clean = urllib.parse.unquote(path.split("?", 1)[0].split("#", 1)[0])
            if clean.startswith(ASSET_MOUNT):
                rel = clean[len(ASSET_MOUNT):].lstrip("/")
                # basename-only join: never escape the assets dir
                safe = os.path.normpath(os.path.join(assets_dir, rel))
                if os.path.commonpath([os.path.abspath(assets_dir),
                                       os.path.abspath(safe)]) != os.path.abspath(assets_dir):
                    return assets_dir  # traversal attempt -> dead end
                return safe
            return super().translate_path(path)

        def log_message(self, *args):
            pass  # quiet

    return functools.partial(Handler, directory=repo_root)


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(repo_root, assets_dir, port):
    httpd = _Server(("127.0.0.1", port), _make_handler(repo_root, assets_dir))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def ffprobe_duration(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path], text=True).strip()
    return float(out)


def capture(url, frames_dir, fps, duration, resume=False):
    from playwright.sync_api import sync_playwright

    n = round(duration * fps)
    os.makedirs(frames_dir, exist_ok=True)
    if not resume:
        for f in os.listdir(frames_dir):
            if f.endswith(".png"):
                os.remove(os.path.join(frames_dir, f))
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--force-color-profile=srgb"])
        page = browser.new_page(viewport={"width": 1920, "height": 1080},
                                device_scale_factor=1)
        page.goto(url, wait_until="networkidle")
        page.evaluate("() => document.fonts.ready")
        page.wait_for_timeout(800)
        # Pause every animation so seeking currentTime is deterministic. Do NOT
        # screenshot with animations="disabled": that fast-forwards every
        # animation to its end state and overrides the seek.
        page.evaluate("() => document.getAnimations().forEach(a => a.pause())")
        step = 1000.0 / fps
        for i in range(n):
            fp = os.path.join(frames_dir, f"frame_{i:05d}.png")
            if resume and os.path.exists(fp):
                continue
            t = i * step
            page.evaluate("(t) => document.getAnimations().forEach(a => { a.currentTime = t; })", t)
            page.screenshot(path=fp, caret="hide")
            if i % 150 == 0:
                print(f"  frame {i}/{n}  ({t/1000:.1f}s)", flush=True)
        browser.close()
    print(f"captured {n} frames -> {frames_dir}")


def mux(frames_dir, music, out, fps, duration):
    music_len = ffprobe_duration(music)
    fade_out_start = max(0.0, min(duration, music_len) - 2.7)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-stats",
        "-framerate", str(fps), "-i", os.path.join(frames_dir, "frame_%05d.png"),
        "-i", music,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "medium",
        "-c:a", "aac", "-b:a", "256k",
        "-af", f"afade=in:st=0:d=1.2,afade=out:st={fade_out_start:.2f}:d=2.7",
        "-t", f"{duration:.3f}",
        out,
    ]
    print("muxing:", " ".join(cmd), flush=True)
    subprocess.check_call(cmd)
    print(f"wrote {out} ({ffprobe_duration(out):.2f}s)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", nargs="?", default="all",
                    choices=["all", "capture", "resume", "mux"])
    ap.add_argument("--html", default=os.path.join(HERE, "trailer", "trailer.html"),
                    help="trailer page (default tools/trailer/trailer.html)")
    ap.add_argument("--assets-dir", default=os.path.join(REPO_ROOT, "runtime", "trailer", "assets"),
                    help="dir served under /trailer-assets/ (see prepare-assets.py)")
    ap.add_argument("--music", help="music bed muxed into the video (required for mux/all)")
    ap.add_argument("--out", default=os.path.join(REPO_ROOT, "runtime", "trailer", "trailer.mp4"))
    ap.add_argument("--frames-dir", default=os.path.join(REPO_ROOT, "runtime", "trailer", "frames"))
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--duration", type=float, default=177.2,
                    help="content length in seconds (matches the trailer.html timeline)")
    ap.add_argument("--port", type=int, default=0, help="0 = pick a free port")
    args = ap.parse_args()

    html_rel = os.path.relpath(os.path.abspath(args.html), REPO_ROOT)
    if html_rel.startswith(".."):
        ap.error("--html must live inside the repo (it is served from the repo root)")

    need_music = args.mode in ("all", "mux") or (args.mode == "resume")
    if need_music and not args.music:
        ap.error("--music is required to mux the video")

    if args.mode in ("all", "capture", "resume"):
        httpd, port = serve(REPO_ROOT, os.path.abspath(args.assets_dir), args.port)
        url = f"http://127.0.0.1:{port}/{html_rel.replace(os.sep, '/')}"
        print(f"serving repo root on :{port} (/trailer-assets/ -> {args.assets_dir})")
        try:
            capture(url, args.frames_dir, args.fps, args.duration,
                    resume=(args.mode == "resume"))
        finally:
            httpd.shutdown()

    if args.mode in ("all", "mux", "resume"):
        mux(args.frames_dir, args.music, args.out, args.fps, args.duration)


if __name__ == "__main__":
    main()

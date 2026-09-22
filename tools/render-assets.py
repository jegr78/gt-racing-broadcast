#!/usr/bin/env python3
"""Render a league's broadcast stills and intro/outro videos from an asset kit.

A kit is a per-profile folder (profiles/<name>/assets-src/) holding the design
as HTML plus the round's texts as JSON. See tools/assets_kit.py for the
contract and profiles/example/assets-src/ for a minimal working kit.

    python3 tools/render-assets.py --profile erf-wspc
    python3 tools/render-assets.py --profile erf-wspc --stills
    python3 tools/render-assets.py --profile erf-wspc --scenes intro
    python3 tools/render-assets.py --profile erf-wspc --probe intro=0,12,24

Output goes to runtime/<profile>/assets/ unless --out says otherwise; add
--out runtime/<profile>/graphics to overwrite the live broadcast graphics.

Maintainer tooling: needs the Playwright Python package (same optional
dependency as tools/e2e.py --playwright) and ffmpeg on PATH. Neither is
required to run racecast itself.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import assets_kit as ak  # noqa: E402  (path shim above must run first)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def die(message):
    sys.exit(f"render-assets: {message}")


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        die("the Playwright Python package is missing.\n"
            "  python3 -m venv .venv-pw && .venv-pw/bin/pip install playwright"
            " && .venv-pw/bin/playwright install chromium\n"
            "  then run this script with .venv-pw/bin/python")
    return sync_playwright


def _ffmpeg():
    exe = shutil.which("ffmpeg")
    if not exe:
        die("ffmpeg not found on PATH (racecast install-tools installs it)")
    return exe


def _page(pw_browser, kit_path, page_name, texts):
    """Open one kit page with the round's texts applied."""
    target = os.path.join(kit_path, page_name)
    if not os.path.isfile(target):
        die(f"{page_name} missing in {kit_path}")
    page = pw_browser.new_page(viewport={"width": 1920, "height": 1080},
                               device_scale_factor=1)
    page.goto("file://" + target.replace(os.sep, "/"))
    page.wait_for_function("document.fonts.status === 'loaded'")
    if texts:
        has_apply = page.evaluate("typeof window.applyText === 'function'")
        if not has_apply:
            die(f"{page_name} has texts to apply but defines no window.applyText")
        page.evaluate("c => window.applyText(c)", texts)
    page.wait_for_timeout(300)
    return page


def render_stills(browser, kit_path, kit, texts, out_dir, only):
    targets = ak.still_targets(kit, only=only)
    if not targets:
        return []
    page = _page(browser, kit_path, ak.STAGE_PAGE, texts)
    written = []
    for screen_id, filename in targets:
        element = page.query_selector("#" + screen_id)
        if element is None:
            die(f"{ak.STAGE_PAGE} has no element with id={screen_id!r}")
        path = os.path.join(out_dir, filename)
        element.screenshot(path=path)
        written.append(path)
        print(f"  {os.path.getsize(path):9d}  {filename}")
    page.close()
    return written


def shoot_scene(browser, kit_path, texts, scene, spec, frames_dir):
    """Write one JPEG per frame; time is set explicitly so runs are identical."""
    page = _page(browser, kit_path, ak.MOTION_PAGE, texts)
    known = page.evaluate("Object.keys(window.SCENES || {})")
    if scene not in known:
        die(f"{ak.MOTION_PAGE} defines no scene {scene!r} (has: {', '.join(known)})")
    page.evaluate("s => window.setScene(s)", scene)
    element = page.query_selector("#" + scene)
    if element is None:
        die(f"{ak.MOTION_PAGE} has no element with id={scene!r}")
    total = ak.frame_count(spec["duration"], spec["fps"])
    for i in range(total):
        page.evaluate("a => window.renderAt(a[0], a[1])", [scene, i / spec["fps"]])
        element.screenshot(path=os.path.join(frames_dir, ak.frame_name(i)),
                           type="jpeg", quality=ak.FRAME_QUALITY)
        if i % 120 == 0:
            print(f"    frame {i}/{total}", flush=True)
    page.close()
    return total


def probe_scene(browser, kit_path, texts, scene, times, out_dir):
    page = _page(browser, kit_path, ak.MOTION_PAGE, texts)
    page.evaluate("s => window.setScene(s)", scene)
    element = page.query_selector("#" + scene)
    if element is None:
        die(f"{ak.MOTION_PAGE} has no element with id={scene!r}")
    for t in times:
        path = os.path.join(out_dir, f"probe-{scene}-{t:07.2f}.png")
        page.evaluate("a => window.renderAt(a[0], a[1])", [scene, t])
        element.screenshot(path=path)
        print(f"  {path}")
    page.close()


def resolve_audio(kit_path, spec, override):
    """Absolute path of a scene's music, or None when there is none.

    Licensed tracks live outside the repo, so a missing file is a warning and
    the scene renders silent.
    """
    if override:
        source = override
    else:
        audio = spec.get("audio") or {}
        source = audio.get("path")
        if source:
            source = os.path.join(kit_path, source)
    if not source:
        return None
    source = os.path.expanduser(source)
    if not os.path.isfile(source):
        print(f"  ! audio not found, rendering silent: {source}", flush=True)
        return None
    return os.path.abspath(source)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--profile", help="render profiles/<name>/assets-src/")
    src.add_argument("--kit", help="render an explicit kit directory")
    ap.add_argument("--out", help="output directory "
                                  "(default runtime/<profile>/assets)")
    ap.add_argument("--stills", action="store_true", help="render stills only")
    ap.add_argument("--scenes", nargs="*", metavar="NAME",
                    help="render these scenes only (no names = all scenes)")
    ap.add_argument("--only", nargs="*", metavar="SCREEN",
                    help="restrict --stills to these screen ids")
    ap.add_argument("--probe", metavar="SCENE=T1,T2",
                    help="write single frames at those seconds instead of a video")
    ap.add_argument("--audio", help="override the music file for every scene")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override one text value (repeatable)")
    ap.add_argument("--keep-frames", action="store_true",
                    help="keep the intermediate frames instead of deleting them")
    args = ap.parse_args()

    kit_path = args.kit or ak.kit_dir(ROOT, args.profile)
    if not os.path.isdir(kit_path):
        die(f"no kit at {kit_path}")
    try:
        kit = ak.load_kit(kit_path)
        event = ak.load_event_texts(kit_path)
    except ak.KitError as exc:
        die(str(exc))

    overrides = {}
    for item in args.set:
        key, _, value = item.partition("=")
        if not _:
            die(f"--set expects KEY=VALUE, got {item!r}")
        overrides[key] = value
    texts = ak.text_config(kit, event, overrides)

    out_dir = args.out or (os.path.join(ROOT, "runtime", args.profile, "assets")
                           if args.profile else os.path.join(kit_path, "out"))
    os.makedirs(out_dir, exist_ok=True)

    sync_playwright = _playwright()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            if args.probe:
                scene, _, raw = args.probe.partition("=")
                times = [float(t) for t in raw.split(",") if t.strip()]
                print(f"probe {scene}:")
                probe_scene(browser, kit_path, texts, scene, times, out_dir)
                return

            # No flags renders everything; --stills or --scenes narrows it, and
            # giving both renders both.
            want_stills = args.stills or args.scenes is None
            want_scenes = args.scenes is not None or not args.stills

            if want_stills:
                print("stills:")
                render_stills(browser, kit_path, kit, texts, out_dir, args.only)

            if want_scenes:
                try:
                    names = ak.scene_names(kit, only=args.scenes or None)
                except ak.KitError as exc:
                    die(str(exc))
                ffmpeg = _ffmpeg() if names else None
                for scene in names:
                    spec = ak.scene_spec(kit, scene)
                    print(f"scene {scene}: {spec['duration']}s @ {spec['fps']}fps")
                    frames_dir = tempfile.mkdtemp(prefix=f"racecast-{scene}-")
                    try:
                        shoot_scene(browser, kit_path, texts, scene, spec,
                                    frames_dir)
                        audio_path = resolve_audio(kit_path, spec, args.audio)
                        out_path = os.path.join(out_dir, spec["output"])
                        cmd = [ffmpeg] + ak.mux_args(
                            frames_dir, spec["fps"], out_path,
                            audio_path=audio_path, audio=spec.get("audio"))
                        if subprocess.call(cmd) != 0:
                            die(f"ffmpeg failed for scene {scene}")
                        print(f"  {os.path.getsize(out_path):9d}  {spec['output']}")
                    finally:
                        if args.keep_frames:
                            print(f"  frames kept in {frames_dir}")
                        else:
                            shutil.rmtree(frames_dir, ignore_errors=True)
        finally:
            browser.close()
    print(f"-> {out_dir}")


if __name__ == "__main__":
    main()

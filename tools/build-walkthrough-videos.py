#!/usr/bin/env python3
"""Render narrated MP4 walkthroughs from the onboarding decks (LOCAL ONLY).

Pipeline per deck (see docs/superpowers/specs/2026-06-22-walkthrough-videos-design.md):
  Reveal deck --Playwright--> one PNG per slide (2x of 1280x720)
  speaker notes (per slide) --Piper TTS--> one WAV per slide
  ffmpeg: PNG + WAV --> slide clip (duration = audio length)
  concat all clips of a deck --> <deck>.mp4

This is a maintainer tool. It is **never** run in CI / GitHub Actions: it needs a
headless browser (the .venv-pw Playwright venv) and the Piper TTS engine. Only the
pure helpers in tools/walkthrough_core.py are unit-tested in CI.

Run it with the Playwright venv's interpreter so Playwright (and Piper) import:
  .venv-pw/bin/python tools/build-walkthrough-videos.py producer.html

TTS engines (--tts):
  piper  (default) offline, OSS, no key. One-time setup in the .venv-pw venv:
           .venv-pw/bin/pip install piper-tts
           .venv-pw/bin/python -m piper.download_voices en_US-lessac-medium \
               --data-dir runtime/piper-voices
  gcloud  Google Cloud Text-to-Speech (Neural2/Studio/Chirp voices). One-time:
           create a GCP project, enable the Cloud Text-to-Speech API + billing,
           create an API key restricted to that API, then put it in .env:
               RACECAST_GCLOUD_TTS_KEY=AIza...
           Free tier covers this workload (~1M chars/month for Neural2). Pick a
           voice with --voice, e.g. en-US-Neural2-J / en-US-Studio-O.

Every video is wrapped with a shared intro + outro title card
(src/docs/slides/walkthrough-{intro,outro}.html), built ONCE per run and reused
for all decks. Disable with --no-bumpers; change length with --bumper-seconds.

A matching <deck>.srt + <deck>.vtt caption sidecar is written next to each MP4
(timed from the exact spoken text + per-slide durations, no transcription).
Disable with --no-captions.

A 1280x720 YouTube thumbnail (<deck>-thumb.png), colour-coded by the deck's role
accent, is written for each deck. Disable with --no-thumbnails; render ONLY the
thumbnails (fast, no TTS/video, no API key) with --thumbnails-only.

Usage:
  build-walkthrough-videos.py [DECK ...]                 # default: all decks
  build-walkthrough-videos.py --list                     # no browser: notes coverage
  build-walkthrough-videos.py --tts gcloud --voice en-US-Studio-O
  build-walkthrough-videos.py --no-bumpers --bumper-seconds 8
  build-walkthrough-videos.py --voice NAME --out DIR --keep-intermediates
"""
import argparse
import base64
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import walkthrough_core as core

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import http_util  # noqa: E402  outbound HTTP funnels through here, UA rule

SLIDES = os.path.join(ROOT, "src", "docs", "slides")
GCLOUD_KEY_ENV = "RACECAST_GCLOUD_TTS_KEY"
DEFAULT_PIPER_VOICE = "en_US-lessac-medium"
DEFAULT_GCLOUD_VOICE = "en-US-Studio-Q"

# Shared intro/outro title cards, built ONCE per run and reused for every deck.
INTRO_HTML = "walkthrough-intro.html"
OUTRO_HTML = "walkthrough-outro.html"
INTRO_TEXT = "GT Racing Broadcast. Onboarding."
OUTRO_TEXT = ("Thanks for watching. You'll find the full setup and reference "
              "documentation in the project wiki.")
DEFAULT_BUMPER_SECONDS = 6.0

# YouTube thumbnails: one 1280x720 card per deck, colour-coded by the deck's role
# accent (data-role -> deck.css var). (data_role, title, subtitle) per deck.
THUMB_HTML = "walkthrough-thumb.html"
THUMBS = {
    "producer.html": ("producer", "Producer", "Event-day playbook"),
    "director.html": ("director", "Director", "Run the show"),
    "commentator.html": ("commentator", "Commentator", "Go on air"),
    "producer-setup.html": ("producer", "Producer Setup", "Set up the machine"),
    "league-admin-setup.html": ("league-admin", "League Admin", "Set up a league"),
    "overlay-designer.html": ("overlay-designer", "Overlay Designer",
                              "Style the broadcast look"),
    "race-control.html": ("race-control", "Race Control",
                          "Monitor the broadcast"),
}
SLIDE_W, SLIDE_H = 1280, 720
_TEMPLATE_RE = re.compile(
    r'<script type="text/template">(.*?)</script>', re.DOTALL)

# Hide the live presentation chrome so the still frames are clean.
_HIDE_CHROME = (".reveal .controls,.reveal .progress,.reveal .slide-number"
                "{display:none!important}")
_NOTE_JS = ("() => { const s = Reveal.getCurrentSlide();"
            " const n = s && s.querySelector('aside.notes');"
            " return n ? n.innerText : ''; }")


def reveal_decks(slides_dir):
    """Basenames of the actual Reveal decks (class="reveal" root marker)."""
    out = []
    for p in sorted(glob.glob(os.path.join(slides_dir, "*.html"))):
        try:
            with open(p, encoding="utf-8") as fh:
                html = fh.read()
        except OSError:
            continue
        if 'class="reveal"' in html:
            out.append(os.path.basename(p))
    return out


def resolve_decks(args_decks, slides_dir):
    """Map user args (basenames, with or without .html) to deck files."""
    if not args_decks:
        return reveal_decks(slides_dir)
    out = []
    for d in args_decks:
        base = os.path.basename(d)
        if not base.endswith(".html"):
            base += ".html"
        if not os.path.exists(os.path.join(slides_dir, base)):
            sys.exit(f"deck not found: {base} (in {slides_dir})")
        out.append(base)
    return out


def deck_notes_from_html(slides_dir, deck):
    """No-browser note coverage: parse template blocks, extract each note.

    Returns a list of (index, sanitized_note) for every markdown section. Used by
    --list; the render path reads notes from the live DOM instead (authoritative).
    """
    with open(os.path.join(slides_dir, deck), encoding="utf-8") as fh:
        html = fh.read()
    out = []
    for i, tpl in enumerate(_TEMPLATE_RE.findall(html)):
        out.append((i, core.sanitize_note_text(core.notes_from_markdown(tpl))))
    return out


def list_coverage(slides_dir, decks):
    missing = 0
    for deck in decks:
        rows = deck_notes_from_html(slides_dir, deck)
        have = sum(1 for _, n in rows if n)
        print(f"{deck}: {have}/{len(rows)} slides narrated")
        for i, n in rows:
            if not n:
                print(f"    slide {i}: NO NOTE")
                missing += 1
    return missing


def _read_env_key(name):
    """Resolve a secret from the real environment, falling back to ROOT/.env."""
    if os.environ.get(name):
        return os.environ[name]
    envfile = os.path.join(ROOT, ".env")
    try:
        with open(envfile, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass  # no .env file -> key simply unset
    return ""


def make_synth(args):
    """Return (synth(text, out_path), audio_ext) for the chosen TTS engine."""
    if args.tts == "piper":
        voice = args.voice or DEFAULT_PIPER_VOICE

        def synth_piper(text, out_path):
            cmd = [sys.executable, "-m", "piper", "-m", voice,
                   "--data-dir", args.voices_dir, "-f", out_path]
            subprocess.run(cmd, input=text.encode("utf-8"),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=True)
        return synth_piper, ".wav"

    # gcloud
    voice = args.voice or DEFAULT_GCLOUD_VOICE
    key = args.gcloud_key or _read_env_key(GCLOUD_KEY_ENV)
    if not key:
        sys.exit(
            "Google Cloud TTS needs an API key. Set "
            f"{GCLOUD_KEY_ENV} in .env (or pass --gcloud-key). One-time setup: "
            "create a GCP project, enable the Cloud Text-to-Speech API + billing, "
            "then create an API key restricted to that API.")

    def synth_gcloud(text, out_path):
        # LINEAR16 returns a real WAV with an EXACT duration, so frame-locking is
        # precise. An MP3's ffprobe duration is only an estimate, off by about
        # 100 ms of encoder padding, and would risk clipping.
        body = core.gcloud_tts_request(text, voice, audio_encoding="LINEAR16",
                                       speaking_rate=args.speaking_rate)
        raw = http_util.post_json(core.gcloud_tts_url(key), body, timeout=60)
        audio = json.loads(raw.decode("utf-8"))["audioContent"]
        with open(out_path, "wb") as fh:
            fh.write(base64.b64decode(audio))
    return synth_gcloud, ".wav"


def _capture_slides(page, deck_path, work):
    """Walk the deck, write one PNG per slide, return [(png, note), ...]."""
    page.goto("file://" + deck_path)
    page.wait_for_function("window.Reveal && Reveal.isReady()")
    page.add_style_tag(content=_HIDE_CHROME)
    page.evaluate("Reveal.configure({transition:'none',autoSlide:0})")
    page.evaluate("Reveal.slide(0,0)")
    total = page.evaluate("Reveal.getTotalSlides()")
    page.wait_for_timeout(600)  # let fonts/diagrams settle once
    slides = []
    for i in range(total):
        page.wait_for_timeout(150)
        png = os.path.join(work, f"slide_{i:03d}.png")
        page.screenshot(path=png)
        note = core.sanitize_note_text(page.evaluate(_NOTE_JS))
        slides.append((png, note))
        page.evaluate("Reveal.next()")
    return slides


def _audio_duration(path):
    """Exact duration (seconds) of a synthesized audio file, via ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def _render_still(page, html_path, png):
    """Screenshot a plain (non-Reveal) HTML page once fonts have loaded."""
    page.goto("file://" + html_path)
    page.wait_for_load_state("load")
    page.evaluate("() => document.fonts.ready")
    page.wait_for_timeout(300)
    page.screenshot(path=png)


def build_bumpers(slides_dir, synth, audio_ext, work, seconds, width, height):
    """Build the shared intro/outro segments ONCE; reused for every deck.

    Returns (intro_segment, outro_segment), each a (png, audio, seconds, text)
    tuple in the same frame-locked shape as a slide segment, so they drop straight
    into the single-pass render. Held for at least `seconds` (audio padded with
    silence). `text` is kept for the caption track.
    """
    from playwright.sync_api import sync_playwright

    specs = [("intro", INTRO_HTML, INTRO_TEXT), ("outro", OUTRO_HTML, OUTRO_TEXT)]
    seg = {}
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(
                viewport={"width": SLIDE_W, "height": SLIDE_H},
                device_scale_factor=2)
            for name, html, text in specs:
                png = os.path.join(work, f"{name}.png")
                _render_still(page, os.path.join(slides_dir, html), png)
                audio = os.path.join(work, f"{name}{audio_ext}")
                synth(text, audio)
                t = core.frame_lock(max(_audio_duration(audio), seconds), fps=30)
                seg[name] = (png, audio, t, text)
        finally:
            browser.close()
    finally:
        pw.stop()
    return seg["intro"], seg["outro"]


_THUMB_JS = ("([role, title, sub]) => { document.body.setAttribute('data-role', role);"
             " document.getElementById('title').textContent = title;"
             " document.getElementById('sub').textContent = sub; }")


def render_thumbnails(slides_dir, decks, out_dir):
    """Render one 1280x720 YouTube thumbnail PNG per deck (no TTS / no video)."""
    from playwright.sync_api import sync_playwright

    os.makedirs(out_dir, exist_ok=True)
    tmpl = "file://" + os.path.join(slides_dir, THUMB_HTML)
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": SLIDE_W, "height": SLIDE_H},
                                    device_scale_factor=2)
            for deck in decks:
                role, title, sub = THUMBS.get(
                    deck, ("producer",
                           os.path.splitext(deck)[0].replace("-", " ").title(), ""))
                page.goto(tmpl)
                page.wait_for_load_state("load")
                page.evaluate(_THUMB_JS, [role, title, sub])
                page.evaluate("() => document.fonts.ready")
                page.wait_for_timeout(200)
                out = os.path.join(out_dir, core.thumbnail_name(deck))
                page.screenshot(path=out)
                print(f"OK {out}")
        finally:
            browser.close()
    finally:
        pw.stop()


def build_deck(deck, slides_dir, out_dir, synth, audio_ext,
               width, height, keep, intro_seg=None, outro_seg=None,
               captions=True):
    from playwright.sync_api import sync_playwright

    deck_path = os.path.join(slides_dir, deck)
    os.makedirs(out_dir, exist_ok=True)
    work = tempfile.mkdtemp(prefix=f"wt_{os.path.splitext(deck)[0]}_",
                            dir=out_dir)
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(
                viewport={"width": SLIDE_W, "height": SLIDE_H},
                device_scale_factor=2)
            slides = _capture_slides(page, deck_path, work)
        finally:
            browser.close()
    finally:
        pw.stop()

    segments = []  # (png, audio, frame_locked_seconds, spoken_text)
    for i, (png, note) in enumerate(slides):
        if not note:
            print(f"  slide {i}: no note -> skipped", file=sys.stderr)
            continue
        audio = os.path.join(work, f"slide_{i:03d}{audio_ext}")
        synth(note, audio)
        # Frame-lock each segment so its video and audio are exactly equal length;
        # otherwise the per-slide A/V gap accumulates and the voice drifts out of
        # sync with the slides (see core.frame_lock / ffmpeg_slideshow_cmd).
        t = core.frame_lock(_audio_duration(audio), fps=30)
        segments.append((png, audio, t, note))

    if not segments:
        sys.exit(f"{deck}: no narrated slides, author Note: blocks first")

    narrated = len(segments)
    # Wrap the deck's slides with the shared intro/outro (built once, reused).
    if intro_seg:
        segments = [intro_seg] + segments
    if outro_seg:
        segments = segments + [outro_seg]

    out_mp4 = os.path.join(out_dir, core.output_mp4_name(deck))
    av = [(png, audio, t) for png, audio, t, _ in segments]
    subprocess.run(
        core.ffmpeg_slideshow_cmd(av, out_mp4, width, height, fps=30),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    if captions:
        cues = core.caption_cues([(text, t) for _, _, t, text in segments])
        stem = os.path.splitext(out_mp4)[0]
        with open(stem + ".srt", "w", encoding="utf-8") as fh:
            fh.write(core.to_srt(cues))
        with open(stem + ".vtt", "w", encoding="utf-8") as fh:
            fh.write(core.to_vtt(cues))

    if not keep:
        for f in glob.glob(os.path.join(work, "*")):
            os.remove(f)
        os.rmdir(work)
    print(f"OK {out_mp4}  ({narrated}/{len(slides)} slides narrated)")
    return out_mp4


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("decks", nargs="*", help="deck basenames (default: all)")
    ap.add_argument("--tts", default="piper", choices=["piper", "gcloud"],
                    help="TTS engine: piper (offline) or gcloud (Google Cloud TTS)")
    ap.add_argument("--voice", default=None,
                    help=f"voice id (default: {DEFAULT_PIPER_VOICE} for piper, "
                         f"{DEFAULT_GCLOUD_VOICE} for gcloud)")
    ap.add_argument("--gcloud-key", default=None,
                    help=f"Google API key (else {GCLOUD_KEY_ENV} from env/.env)")
    ap.add_argument("--speaking-rate", type=float, default=None,
                    help="gcloud speaking rate (1.0 = normal)")
    ap.add_argument("--voices-dir",
                    default=os.path.join(ROOT, "runtime", "piper-voices"))
    ap.add_argument("--out",
                    default=os.path.join(ROOT, "runtime", "walkthroughs"))
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--list", action="store_true",
                    help="no browser: report speaker-note coverage and exit")
    ap.add_argument("--no-bumpers", action="store_true",
                    help="skip the shared intro/outro title cards")
    ap.add_argument("--bumper-seconds", type=float, default=DEFAULT_BUMPER_SECONDS,
                    help="minimum intro/outro length (default 6s)")
    ap.add_argument("--no-captions", action="store_true",
                    help="skip writing the .srt/.vtt subtitle sidecars")
    ap.add_argument("--no-thumbnails", action="store_true",
                    help="skip the per-video YouTube thumbnail PNGs")
    ap.add_argument("--thumbnails-only", action="store_true",
                    help="render only the thumbnails (no TTS/video; no API key)")
    ap.add_argument("--keep-intermediates", action="store_true")
    args = ap.parse_args()

    decks = resolve_decks(args.decks, SLIDES)
    if args.list:
        missing = list_coverage(SLIDES, decks)
        if missing:
            sys.exit(f"{missing} slide(s) without a note")
        return

    if args.thumbnails_only:
        render_thumbnails(SLIDES, decks, args.out)
        return

    synth, audio_ext = make_synth(args)

    # Build the shared intro/outro ONCE, then reuse for every deck.
    intro_seg = outro_seg = None
    bumper_work = None
    if not args.no_bumpers:
        os.makedirs(args.out, exist_ok=True)
        bumper_work = tempfile.mkdtemp(prefix="wt_bumpers_", dir=args.out)
        intro_seg, outro_seg = build_bumpers(
            SLIDES, synth, audio_ext, bumper_work, args.bumper_seconds,
            args.width, args.height)
        print(f"bumpers: intro {intro_seg[2]:.1f}s + outro {outro_seg[2]:.1f}s")

    try:
        for deck in decks:
            print(f"== {deck} ==")
            build_deck(deck, SLIDES, args.out, synth, audio_ext,
                       args.width, args.height, args.keep_intermediates,
                       intro_seg, outro_seg, captions=not args.no_captions)
    finally:
        if bumper_work and not args.keep_intermediates:
            shutil.rmtree(bumper_work, ignore_errors=True)

    if not args.no_thumbnails:
        render_thumbnails(SLIDES, decks, args.out)


if __name__ == "__main__":
    main()

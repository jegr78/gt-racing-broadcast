---
name: walkthrough-videos
description: "Use when (re)building or updating the narrated MP4 onboarding walkthrough videos from the Reveal decks: e.g. a deck's slides or speaker notes changed, the voice/intro/outro needs refreshing, captions are wanted, or the videos must be re-rendered for the YouTube channel."
---

# Build narrated walkthrough videos

Renders one narrated 1080p MP4 per onboarding deck (`src/docs/slides/*.html`) for
YouTube: Playwright screenshots each slide, a TTS engine voices the slide's
`Note:` speaker note, ffmpeg muxes still+audio, and a shared intro/outro wraps
every video. Local maintainer tool: **never CI** (needs a browser + TTS). Pure
logic is in `tools/walkthrough_core.py` (unit-tested in `tests/test_walkthrough.py`,
which DO run in CI); the lifecycle is `tools/build-walkthrough-videos.py`. Design:
`docs/superpowers/specs/2026-06-22-walkthrough-videos-design.md`.

## One-time setup (in the Playwright venv `.venv-pw`)

```bash
.venv-pw/bin/pip install piper-tts            # only if using --tts piper
.venv-pw/bin/python -m piper.download_voices en_US-lessac-medium --data-dir runtime/piper-voices
```

For the chosen engine (Google Cloud TTS) put an API key in the gitignored `.env`:
`RACECAST_GCLOUD_TTS_KEY=AIza...` (GCP project with the Cloud Text-to-Speech API +
billing enabled; key restricted to that API). Free tier covers the whole onboarding.

## Build

```bash
# all 7 decks, chosen voice, with intro/outro + .srt/.vtt captions:
.venv-pw/bin/python tools/build-walkthrough-videos.py --tts gcloud --voice en-US-Studio-Q
# one deck:                 ... producer.html --tts gcloud --voice en-US-Studio-Q
# coverage check (no browser): ... <deck> --list
```

Output per deck (all under `runtime/walkthroughs/`, gitignored): `<deck>.mp4`,
`<deck>.srt`, `<deck>.vtt`, and a 1280x720 YouTube thumbnail `<deck>-thumb.png`
(colour-coded by the deck's role accent). Chosen voice: **en-US-Studio-Q** (Piper
voices were rejected as too synthetic for public videos). Opt out with
`--no-bumpers`, `--bumper-seconds`, `--no-captions`, `--no-thumbnails`. Render ONLY
the thumbnails (fast, no TTS/video/API key) with `--thumbnails-only`.

## Authoring speaker notes

Each `<section data-markdown>` gets a `Note:` block (blank line, then `Note: …`)
just before `</script>`. Plain spoken prose, no markdown/links, narrating ONLY
what the slide shows (repo rule: never invent facts). Notes are invisible in the
live deck.

## Gotchas (verified the hard way)

- **A/V sync:** each segment is frame-locked (audio==video length) and assembled
  in ONE pass via the ffmpeg `concat` filter. Never `-c copy`-concat per-slide
  clips: the per-segment gap accumulates into seconds of drift.
- **gcloud audio = LINEAR16/WAV**, not MP3: MP3's ffprobe duration is only an
  estimate and would clip speech at the frame-lock step.
- **Markdown `---` inside a template** makes Reveal split the slide into vertical
  sub-slides. EACH half then needs its own `Note:`. `--list` counts templates and
  under-reports this; trust the render's `N/N slides narrated` line (Reveal slide
  count), which loudly skips any un-narrated slide.
- The intro/outro pages (`src/docs/slides/walkthrough-{intro,outro}.html`) must
  NOT contain the literal `class="reveal"` anywhere (even in a comment), or the
  `reveal_decks` substring filter treats them as decks and the build hangs.

## Verify

```bash
python3 tests/test_walkthrough.py            # pure logic (also in run-tests.py)
.venv-pw/bin/python tools/check-slides.py    # overflow guard, must stay "7 deck(s)"
```
Then spot-check a rendered MP4: streams should be 1920x1080 h264 + aac with ~0 ms
A/V drift (`ffprobe` the video vs audio stream `duration`).

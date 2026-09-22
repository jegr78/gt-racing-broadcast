# Racecast promo trailer — build pipeline

A maintainer pipeline that renders the ~3-minute promotional trailer for the GT
Endurance Racing Broadcast toolkit as a deterministic MP4. The trailer is an
animated HTML page captured frame-by-frame (every CSS animation paused and
seeked, so the render is reproducible) and muxed with a music bed.

Not shipped to producers. `tools/` is maintainer-only.

## Files

| File | Role |
|---|---|
| `trailer.html` | The animated 10-scene trailer page. Committed. Pulls committed UI shots from `/src/...` and league graphics + a redacted CC crop from `/trailer-assets/...`. |
| `prepare-assets.py` | Builds the `/trailer-assets/` dir: copies 4 league broadcast graphics + generates the redacted `cc-home-crop.png`. |
| `../build-trailer.py` | Serves the repo root (with `/trailer-assets/` mapped onto the assets dir), captures frames via Playwright, muxes with ffmpeg. |

## Prerequisites

- **Playwright venv** at `.venv-pw` (the repo's Playwright Python + Chromium).
- **ffmpeg / ffprobe** on PATH.
- **A music bed**: *not committed* (licensing). Download a royalty-free,
  up-tempo clip from the [YouTube Studio Audio Library](https://studio.youtube.com/)
  (~2:57 to match the timeline) and save it locally, e.g.
  `runtime/trailer/assets/the-theme.mp3`.
- **League graphics**: get a set with `racecast --profile demo graphics`
  (writes `runtime/demo/graphics/`).

## Build

```bash
# 1) league graphics (once per machine)
python3 src/racecast.py --profile demo graphics

# 2) assemble /trailer-assets/ (copies 4 graphics + redacted cc-home crop)
python3 tools/trailer/prepare-assets.py \
    --graphics-dir runtime/demo/graphics \
    --out runtime/trailer/assets

# 3) drop your music bed in place, then render + mux
.venv-pw/bin/python tools/build-trailer.py all \
    --assets-dir runtime/trailer/assets \
    --music runtime/trailer/assets/the-theme.mp3 \
    --out runtime/trailer/trailer.mp4
```

`runtime/` is gitignored, so the frames, the music, the assembled assets, and
the finished `trailer.mp4` all stay machine-local. Upload the MP4 to YouTube and
link it as the `Trailer Video` in the demo/testing Sheet's Assets tab.

### Modes

`build-trailer.py` takes a mode: `all` (capture + mux), `capture` (fresh
frames), `resume` (keep existing frames, render only the missing ones. A killed
render recovers with this), `mux` (frames → mp4 only).

## Notes

- **Determinism:** each frame is a `currentTime` seek on paused animations, not a
  real-time recording: so the same page + music renders byte-stably anywhere. Do
  NOT screenshot with `animations="disabled"`; it fast-forwards every animation to
  its end state and defeats the seek.
- **Privacy:** `cc-home-crop.png` is regenerated from the committed
  `src/docs/slides/assets/img/cc-home.png` with the bottom MagicDNS rows cropped
  and the Tailscale IP painted over. The trailer never leaks tailnet identity. If
  that source screenshot is re-captured at a different size, recheck the crop
  height / IP box constants in `prepare-assets.py`.
- **Editing the design:** the HTML pages are plain files. Edit `trailer.html`,
  re-run `capture`, eyeball a few frames. The scene timeline (durations) lives in
  the `@keyframes`/`animation-delay` values; keep `--duration` in sync.

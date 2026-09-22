# Asset kit

A kit renders a league's broadcast stills (Standby, Intermission, …) and its
intro/outro videos from HTML, so a new race weekend is a text edit rather than a
session in a graphics program. This folder is a minimal working example: copy
it into your own profile and restyle it.

```bash
python3 tools/render-assets.py --profile example              # everything
python3 tools/render-assets.py --profile example --stills     # stills only
python3 tools/render-assets.py --profile example --scenes intro
python3 tools/render-assets.py --profile example --probe intro=0,2,4
```

Output lands in `runtime/<profile>/assets/`. Point `--out` at
`runtime/<profile>/graphics` once a look is approved, and the files overwrite
the live broadcast graphics under the names the Sheet's Assets tab expects.

## Files

| File | Role |
|---|---|
| `kit.json` | what to render: screen id → filename, scene → duration/fps/audio |
| `stage.html` | one 1920×1080 element per still, `id` = the screen id |
| `motion.html` | one element per scene plus `renderAt(scene, t)` |
| `event.json` | the texts of the current round (optional) |

## How a round works

Texts merge in this order, each layer overriding the one before it:

1. `kit.json` → `text`: defaults that rarely change
2. `event.json`: this weekend's title, date, credits
3. `--set key=value`: a one-off override on the command line

The merged object is handed to `window.applyText(cfg)` on both pages before
anything is captured. Keys starting with `_` are treated as documentation and
never reach the page.

## Writing a scene

The renderer sets time explicitly: for every frame it calls
`window.renderAt(scene, t)` and screenshots the element. A frame must therefore
depend **only** on `t`: no CSS animations, no `requestAnimationFrame`, no
`Date.now()`. That is what makes a re-render byte-comparable and lets `--probe`
show you second 24 without rendering the 23 seconds before it.

## Music

`kit.json` may point a scene at an audio file, with the cut expressed as
`start` / `duration` and optional fades. The path is relative to the kit, and
licensed music is normally kept **outside** the repository: when the file is
missing the scene simply renders silent, and `--audio PATH` overrides it per
run.

Mind that a cut is tied to the animation: if the music's first beat is at 0:24
and the scene puts its logo reveal at 23.5 s, then `start` must be 0.5 s. Move
one and you have to move the other, or picture and sound drift apart.

## Requirements

The Playwright Python package (same optional dependency as
`tools/e2e.py --playwright`) and `ffmpeg` on `PATH`. Neither is needed to run
racecast itself: this is maintainer tooling.

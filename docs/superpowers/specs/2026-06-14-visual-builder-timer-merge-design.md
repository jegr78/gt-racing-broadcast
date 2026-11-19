# Visual Builder corrections: timer merge, POV box, property prefill

**Date:** 2026-06-14
**Status:** Approved — ready for implementation
**Area:** OBS collection (`src/obs/GT_Endurance.json`, `src/obs/hud.html`,
removes `src/obs/timer.html`), relay (`src/relay/racecast-feeds.py`), overlay
compiler (`src/scripts/overlay_build.py`), Control Center builder
(`src/ui/control-center.html`, `src/ui/ui_server.py`).
**Supersedes parts of:** `2026-06-13-visual-overlay-builder-design.md` (the
two-page HUD/Timer model — this folds the timer into the HUD).

## Problem

Three operator-reported issues with the visual overlay builder (#114, #146):

1. **The timer is isolated from the HUD.** It lives on its own builder tab, its
   own page (`timer.html`), its own CSS (`timer.css`) and its own OBS browser
   source (`HUD Race Timer`). You cannot see or adjust it in the context of the
   HUD it sits over.
2. **The timer renders at the wrong size/position in the builder.** `timer.html`
   draws a 420px clock at `top:217` on a raw 1920×1080 canvas. OBS then crops it
   to `y[217..895]`, scales it `0.0854`, and places it at `(880, 98)`. The
   builder shows the un-cropped, un-scaled page, so the Timer tab is a giant
   clock over the lower-third frame — nothing like air. Maximally confusing.
3. **Property fields show only overrides, never the template defaults.** A slot
   with no override shows empty fields, so the operator has no idea where a slot
   currently is or how big it is.
4. **The POV box has no fill/border controls** (`data-edit-props="left,top,width,height"`).
5. **The POV box default position was guessed** (`left:40 top:560 480×270`),
   unrelated to where the real POV picture-in-picture sits.

## Decisions (confirmed with the producer)

- **Merge the timer into the main HUD** (one page, one CSS, one OBS source). The
  setup is not live yet, so the OBS scene-layout change is acceptable. Verified
  in the collection: the `HUD Race Timer` source has an *identical* transform in
  every scene that shows it (`Stint`, `Splitscreen`) and is *always* shown
  together with `HUD Overlay` — it is never placed or toggled independently.
  Timer show/hide stays director-driven via the `/timer/*` control endpoints
  (the clock self-blanks on `state.visible`), so no OBS-source-level toggle is
  lost in practice.
- **POV background + border render live** (a framed PiP on the broadcast HUD),
  not a builder-only guide.
- **Prefill + save-all**: fields are pre-filled from the template's effective
  values, and Save writes the full property snapshot per slot (not only the
  diff). The producer accepts that this freezes a profile's look against future
  base-CSS changes.
  Since #1178 an unedited colour that the page takes from a CSS variable is saved
  as the `var(--name)` reference, so theme variables in customCss keep working.

## Key insight: one page = one canvas = exactly what is on air

The confusion (issues 1+2) is a direct consequence of the timer being a separate
OBS source cropped+scaled+repositioned from a 1920×1080 page. Folding the clock
into `hud.html` as one more absolutely-positioned slot at its **true on-air
geometry** removes the transform entirely: the builder canvas becomes a literal
1:1 preview of the broadcast, and the timer is edited in place at real scale. No
second coordinate system, no transform math, no `420px→0.0854` hack.

## Architecture changes

### OBS collection (`src/obs/GT_Endurance.json`)

- **Remove** the `HUD Race Timer` browser source (source def + its scene items
  in `Stint` and `Splitscreen`). The clock is now part of the `/hud` page served
  by the `HUD Overlay` source.
- **No reorder needed.** OBS scene-item arrays are ordered low→high = back→front
  (verified against the base collection: `HUD Overlay` text sits at a *higher*
  index than the `Overlay` PNG frame so the text draws on top). In the base
  collection `HUD Overlay` already sits in front of `Feed POV` (index 6 vs 3 in
  the `Stint` scene), so the POV frame the HUD page draws already renders over the
  video. We therefore only remove the timer source and leave z-order untouched.
  The `#pov` box keeps a transparent center, so the video still shows through;
  only the border/background frame it. (Guarded by
  `t_obs_hud_overlay_renders_in_front`.)
- Re-tokenize with `tools/tokenize-obs.py` if image basenames/URLs shifted; the
  build verify step must still pass (no secrets, tokens intact).

### HUD page (`src/obs/hud.html`)

- **Add a `#clock` slot** at its true on-air geometry. Derivation from the old
  OBS transform (crop `y[217..895]` = 678px page window, scale `0.0854`, pos
  `(880, 98)`): the clock lands at roughly `left:889px top:98px`, `font-size`
  ≈ `36px`, keeping the monospace stack, `font-weight:700`,
  `font-variant-numeric: tabular-nums`, and the existing text-shadow. **Exact
  pixels are verified live** against the pre-merge on-air look (the producer's
  app is running) — the numbers above are the starting estimate, not asserted
  fact.
- **Add a second poll loop** for the clock: `GET /timer/data` (2000ms poll +
  250ms local tick against the end anchor), kept as its own functions next to
  the existing `/hud/data` loop. The clock self-blanks when `!state.visible`.
- `#clock` carries `data-edit="Clock"` with the default text-slot prop set
  (`left,top,width,height,fontSize,fontFamily,color,background,align`).
- **POV box** (`#pov`):
  - Default position aligned to the OBS `Feed POV` box:
    `left:1496px; top:644px; width:384px; height:216px;`.
  - `data-edit-props` gains `background,borderStyle,borderColor,borderWidth`.
  - No fill/border by default → the box stays invisible on air unless styled
    (unchanged default behavior).

### Relay (`src/relay/racecast-feeds.py`)

- **Keep** all timer state/control endpoints unchanged: `/timer/data` and the
  director actions (`/timer/start`, `/timer/pause`, `/timer/reset`, …). The
  merged HUD page is now their only consumer page.
- **Remove** the timer *page* surface: the `/timer` route (served
  `timer.html`) and `/timer/override.css`.
- **`OBS_PAGE_PATHS`**: drop `/timer` and `/timer/override.css`; the refresh
  hash now covers `/hud`, `/hud/override.css` only (the timer is part of `/hud`).
- **Delete** `src/obs/timer.html`.

### Overlay compiler (`src/scripts/overlay_build.py`)

- **Single page.** Remove the `timer` page from `SAMPLE` (move `"clock":
  "1:23:45"` into the `hud` sample) and from all page branching. `extract_slots`
  now naturally returns `#clock` from `hud.html`.
- **New properties** in the prop→CSS map:
  - `borderStyle → border-style` (enum value: `none|solid|dashed|dotted`)
  - `borderColor → border-color`
  - `borderWidth → border-width` (px; add to `_PX_PROPS` and `PROP_ORDER`)
  - `background` already exists.
- The existing safety gates (`_safe_value`, `_UNSAFE_VALUE`, allowed-prop
  gating) apply to the new props unchanged.

### Builder UI (`src/ui/control-center.html`)

- **Remove the HUD/Timer tabs** and `setOverlayPage`/per-page branching;
  `overlayPage` is always `hud`. The single canvas now includes the clock and
  the POV box.
- **Property prefill (`ovRenderPanel`)**: when a slot is selected, fields show
  the slot's **effective value read from the rendered shadow-DOM element via
  `getComputedStyle`** (the cascade ground-truth from base `hud.html`):
  `left, top, width, height, fontSize, color, background, borderWidth` as real
  numbers; colors normalized to `#rrggbb`. `fontFamily`/`align` show the
  effective value as an "(inherited)" hint but remain override-on-change
  (prefilling the base font stack is noise). A small pure color-normalizer
  (`rgb()`→hex) is unit-tested.
- **POV panel**: add background color, border style (`none/solid/dashed/dotted`
  select), border color, and border width fields, gated by the slot's
  `data-edit-props`.
- **Save-all (`saveOverlay`)**: build the layout so every editable slot carries
  its full effective property set (computed base merged with user edits), then
  POST as today. The compiled `hud.css` fully pins each slot. The map in
  `OV_CSSNAME`/`OV_PX` gains the border props to mirror `overlay_build.py`.
- Drop the timer-page migration note and `layout-timer.json` handling.

### Control Center server (`src/ui/ui_server.py`)

- The overlay endpoints (`/api/overlay/slots|layout|fonts|bg|font`) collapse to
  the single `hud` page: ignore/deprecate the `page` query (always `hud`), stop
  reading/writing `layout-timer.json` / `timer.css`. `/api/overlay/bg` still
  serves the profile's `Overlay.png`.

## Data flow (unchanged where possible)

```
hud.html (base CSS + #clock + #pov + data-edit markers)
   │  builder canvas renders it 1:1 in a Shadow-DOM host over Overlay.png
   │  getComputedStyle → prefill the property panel
   ▼
layout-hud.json   (full per-slot snapshots; builder owns it)
   │  compile_overlay_css(layout, slots)
   ▼
profiles/<name>/overlay/hud.css   (served at /hud/override.css; hash gate → OBS refresh)
```

The relay's live data binding (`/hud/data`, `/timer/data`) is untouched.

## Migration & coexistence

- Existing profiles: any hand-written `timer.css` / `layout-timer.json` is now
  orphaned. On first builder load, if a legacy `timer.css` is non-empty, append
  it verbatim into the HUD layout's `customCss` once (same migration sink as the
  original spec) so no styling is silently lost; then ignore the timer files.
  The single existing profile (`iro-gtec`) likely has no timer customization.
- The `/hud` page absorbs the timer; OBS browser sources auto-refresh via the
  existing hash gate on the next `relay start` / `obs refresh`.

## Out of scope (unchanged)

- Editing the `Overlay.png` frame itself.
- Reverse-parsing arbitrary hand-written CSS into the visual model (preserved
  verbatim via `customCss`).
- Animations/timelines.
- An OBS-source-level independent timer toggle (intentionally removed; the
  director's `/timer/*` controls cover show/hide).

## Verification

- `tests/test_overlay.py`: border props compile to `border-*`; `borderWidth` is
  px-gated; `#clock` appears in `extract_slots(hud.html)`; `SAMPLE["hud"]`
  carries `clock`; color-normalizer maps `rgb()`→hex; full-snapshot layout
  compiles every slot.
- `tests/test_hud.py`: the merged page serves the clock markup + both poll
  targets.
- `tests/test_timer.py`: `/timer/data` + control endpoints still work; the
  `/timer` page route is gone.
- `tests/test_racecast.py` / `tests/test_ui_server.py`: `OBS_PAGE_PATHS` no
  longer lists `/timer*`; overlay routes operate on the single `hud` page.
- `python3 tools/run-tests.py` + `python3 tools/lint.py` + `python3
  tools/build.py` (verify step) all green.
- **Live check** in the running Control Center: Preview/Apply shows the clock at
  correct on-air scale/position inside the HUD, and a styled POV box frames the
  PiP. Confirm exact `#clock` pixels and the `HUD Overlay`-above-`Feed POV`
  ordering here, not by assertion.

# POV-Box → OBS "Feed POV" automatisch synchronisieren

**Date:** 2026-06-26
**Status:** Design approved, ready for planning
**Issue:** (none yet — operator-reported: erf-nls POV PiP offset from its overlay frame)

## Problem

The lower-third HUD draws only the *frame* of the POV picture-in-picture. The
actual driver video is a **separate OBS scene item** ("Feed POV", the relay's
`http://127.0.0.1:53003` ffmpeg source). Frame and video are aligned today only
because two places carry the **same** coordinates by hand:

- `src/obs/hud.html` base CSS: `#pov { left: 1496px; top: 644px; width: 384px; height: 216px; }`
- the OBS "Feed POV" scene item: `pos {1496, 644}`, `bounds {384, 216}`,
  `bounds_type: 2` (SCALE_INNER), `align: 5` (top-left).

When a profile moves the POV box in its per-league overlay (the visual builder
writes `layout-hud.json` → generated `hud.css`), only the **frame** moves. The
OBS scene item stays at the template position, so the PiP video sits offset from
its frame.

Confirmed live in `erf-nls`: the operator moved the box to
`{left: 1516, top: 600, width: 384, height: 216}` (builder), so the video is off
by `(+20, −44)` from the frame.

## Goal

Reflect a profile's POV-box position/size onto OBS automatically, so the operator
never adjusts the "Feed POV" scene item by hand. Two complementary paths
(operator chose **both**):

1. **Export bake** — `racecast setup` writes the corrected `pos`/`bounds` into the
   localized import JSON, so a cold import / a producer handoff already starts
   aligned.
2. **Live sync** — the existing `racecast obs refresh` / `relay start` /
   `event start` OBS-refresh hook also pushes the POV transform to the running
   OBS via obs-websocket, so a builder edit takes effect immediately **without**
   re-importing the collection.

## The clean 1:1 mapping

The "Feed POV" scene item is top-left anchored (`align: 5`) with SCALE_INNER
bounds, so CSS maps directly onto the OBS transform:

| CSS (`#pov`) | OBS transform |
|--------------|---------------|
| `left`       | `positionX`   |
| `top`        | `positionY`   |
| `width`      | `boundsWidth` |
| `height`     | `boundsHeight`|

The `#pov-name` label is **pure overlay** (drawn by the full-screen HUD browser
source) — it has no OBS video counterpart and is **never** touched.

## Source of truth — Option B (parse the effective override CSS)

The POV box is read from the profile's **generated/served override CSS**
(`profiles/<name>/overlay/hud.css` on disk; the same bytes the relay serves at
`/hud/override.css`), not from `layout-hud.json`. Rationale:

- **Universal** — covers both the builder (generated `#pov` rule) and the
  hand-written `customCss` / raw-`hud.css` escape hatch, because it reads what OBS
  actually renders. No "layout vs. CSS" drift.
- **One pure function for both paths** — `pov_box_from_css()` serves the export
  (reads the file) and the live sync (the relay already has the served bytes).
- **Partial overrides resolve themselves** — only the properties the CSS actually
  sets are applied; anything unset keeps the OBS scene item's existing value. The
  scene item's base (`1496,644 / 384×216`) equals the base `hud.html` value by
  convention, so "unset = base" is automatic — no need to read `hud.html`.
- **Degrades gracefully** — no override file, no `#pov` rule, or unparseable CSS
  ⇒ no transform applied = today's behavior.

Scope: **POV-only**. The slot→source mapping is a small constant
(`OVERLAY_SLOT_OBS_SOURCES = {"pov": "Feed POV"}`) — exactly one entry today, but
named so a future overlay-element-with-OBS-source is a one-line addition. POV is
currently the only overlay element with a real positioned OBS video source (Feed
A/B are full-screen; clock/race-control/flags are pure overlay).

## Components

### 1. Pure parser — `src/scripts/overlay_build.py`

```
OVERLAY_SLOT_OBS_SOURCES = {"pov": "Feed POV"}

def pov_box_from_css(css_text) -> dict:
    """Effective #pov box overrides from override CSS: a dict with any subset of
    {'left','top','width','height'} (ints, px). The LAST #pov rule wins (CSS
    cascade — covers a customCss override appended after a generated rule).
    Empty dict when absent/unparseable (caller then applies no transform)."""
```

- Tolerant regex: find every `#pov { ... }` block, take the last; within it pull
  `left|top|width|height: <number>px`. Ignore everything else.
- No dependency, no I/O. Pure — unit-tested.

### 2. Export bake — `src/setup-assets.py`

- New flag `--overlay-css PATH` (default empty). `racecast.py`'s
  `_oneshot_extra("setup", …)` passes `profiles/<active>/overlay/hud.css` when it
  exists (resolved via the existing `_active_overlay_dir()`).
- Pure transform applied after the token replacement:

```
def apply_pov_transform(collection, overrides) -> collection:
    """Set pos/bounds of EVERY scene item named 'Feed POV' (across all scenes)
    from `overrides` (the pov_box_from_css dict). Unset keys keep the item's
    existing value. No-op on empty overrides. Mutates + returns (same contract as
    the other setup-assets transforms)."""
```

- `main()`: read `--overlay-css` (if given and readable) → `pov_box_from_css` →
  `apply_pov_transform`. Best-effort: unreadable file or empty result ⇒ no-op,
  one informational line. `setup-assets.py` stays `config.py`-free — it only
  imports the pure helper from `scripts/` (already on `sys.path`, like
  `discord_web`).

### 3. obs-websocket helper — `src/scripts/obs_ws.py`

```
def set_scene_item_transform(scene, source, transform, host=…, port=…,
                             password=…, timeout=2.0) -> (ok, note):
    """Best-effort SetSceneItemTransform on `source` in `scene`. Mirrors
    set_scene_item_enabled: GetSceneItemId → SetSceneItemTransform. Returns
    (True, '') / (False, reason); NEVER raises (OBS closed, wrong password, item
    missing all map to (False, note))."""
```

- `transform` carries `positionX`, `positionY`, `boundsType: 2`,
  `boundsAlignment: 0`, `alignment: 5`, `boundsWidth`, `boundsHeight` — all sent
  explicitly so the result is idempotent regardless of the item's current
  bounds_type.

### 4. Live sync — wired into `_refresh_obs_pages()` (`src/racecast.py`)

- After the `refresh_browser_inputs` (`refreshnocache`) calls succeed, the CLI:
  1. reads the active profile's override CSS (`profiles/<active>/overlay/hud.css`),
  2. `pov_box_from_css` → merge over the **template** base
     (`1496,644 / 384×216`, the known scene-item base) → a full transform,
  3. `obs_ws.set_scene_item_transform(STINT_SCENE, "Feed POV", transform)`.
- Same gate as today: only when the relay control port + OBS/obs-websocket are
  reachable. Best-effort; a failure prints one line and the refresh still
  succeeds. Empty overrides ⇒ apply the base (idempotent — keeps OBS correct even
  after a profile reset).
- Fires on `racecast obs refresh`, and on `relay start` / `event start` (which
  already call `_refresh_obs_pages`), so a running OBS is re-aligned on every
  start without a re-import.

## Data flow

```
Builder save ──► layout-hud.json + generated hud.css
      │
 racecast setup ──► setup-assets reads hud.css ──► pov_box_from_css
      │                                         └─► apply_pov_transform ──► import JSON (baked)
      │
 obs refresh / relay start / event start
      └──► _refresh_obs_pages ──► pov_box_from_css(served) ──► merge over base
                               └─► obs_ws.set_scene_item_transform ──► running OBS aligned now
```

## UX consequence (state explicitly in docs)

- **Live sync** re-aligns the running OBS on every refresh/start ⇒ after a builder
  change you normally **do not** re-import; OBS + obs-websocket up is enough.
- **Export bake** matters for a cold import / handing the profile to another
  producer — the fresh import already carries the correct position.

## Edge cases (all best-effort, never fail the host action)

- No overlay dir / no `hud.css` / no `#pov` rule / unparseable ⇒ no override
  (export) or apply base (live, idempotent) = today's behavior.
- OBS / obs-websocket unreachable ⇒ live sync silently skipped (like `obs refresh`).
- "Feed POV" item absent in the collection (export) or scene (live) ⇒ `(False,
  note)` / no mutation, no abort.
- `#pov-name` and all other slots are ignored — POV box only.
- The `hud.html` base `#pov` and the OBS scene item stay synchronized by
  convention; the existing comment in `hud.html` remains the contract note.

## Tests

- `tests/test_overlay.py` — `pov_box_from_css`: full rule, partial (left/top only),
  absent, `customCss`-appended override wins (last rule), malformed/garbage,
  non-px values ignored; `OVERLAY_SLOT_OBS_SOURCES` constant present.
- `tests/test_setup.py` — `apply_pov_transform`: full/partial override on a fake
  collection with one+ "Feed POV" items, no-op on empty, item-absent no-op,
  unset-keys-keep-existing.
- `tests/test_obsws.py` — `set_scene_item_transform` best-effort contract against a
  fake socket (GetSceneItemId → SetSceneItemTransform request shape; `_connect`
  None ⇒ `(False, note)`; missing item ⇒ `(False, note)`).
- `tests/test_racecast.py` — wiring: `_oneshot_extra("setup")` adds `--overlay-css`
  only when the file exists; the refresh hook calls `set_scene_item_transform` with
  the merged transform when overrides are present.

## Non-goals / not touched

- No change to the visual overlay builder UI (no wiki screenshot refresh needed).
- No new public/Funnel surface; obs-websocket stays never-funnelled.
- No reverse direction (OBS → CSS). The overlay remains the source of truth.
- No generalization beyond the single `pov` → "Feed POV" mapping.

## Files touched

- `src/scripts/overlay_build.py` — `pov_box_from_css`, `OVERLAY_SLOT_OBS_SOURCES`.
- `src/setup-assets.py` — `--overlay-css` flag, `apply_pov_transform`, `main()` wiring.
- `src/scripts/obs_ws.py` — `set_scene_item_transform`.
- `src/racecast.py` — `_oneshot_extra("setup")` overlay-css arg; `_refresh_obs_pages`
  live-sync call.
- `tests/test_overlay.py`, `tests/test_setup.py`, `tests/test_obsws.py`,
  `tests/test_racecast.py`.
- `CLAUDE.md` — note the overlay-POV → OBS-source sync in the OBS token round-trip
  section.

## Revision (#766): visibility follows the profile too

For `overlay_build.VISIBILITY_SLOTS` (`#webcam` -> "Solo Webcam", `#tyres-capture` ->
"Solo Tyres/Fuel Capture") the profile's `hud.css` now also decides whether the OBS item
is shown: `display: none` on the plain slot selector hides it, any other state shows it.
`setup-assets.py` bakes it into the item's `visible` flag in the export scene
(`bake_overlay_visibility`), and the live hook (`_sync_pov_transform`) pushes it with
`obs_ws.set_scene_item_enabled`. The producer decided that the profile is authoritative in
both directions. Feed POV is excluded: the director's live POV toggle owns its visibility.

# Standard properties for all overlay-builder slots

**Date:** 2026-06-15
**Status:** Proposed
**Area:** overlay compiler (`src/scripts/overlay_build.py`), HUD markup
(`src/obs/hud.html`), Control Center overlay builder (`src/ui/control-center.html`),
tests (`tests/test_overlay.py`), wiki image (`src/docs/wiki/images/cc-overlay-builder.png`)

## Problem

The visual overlay builder (issue #114) lets operators position and style the
HUD slots through a property panel, compiling a per-league `override.css`. Which
properties each slot offers is decided by a **hand-curated `data-edit-props`
list per element** in `hud.html`. This has two consequences:

1. **Inconsistent coverage.** The number badge (`team*-num`) exposes neither
   `width`/`height` nor alignment, so a league cannot give the badges a uniform
   width in the builder — it has to be hand-written into the `customCss` escape
   hatch (the workaround used for the IRO GTEC league, issue #172). That CSS is
   invisible in the builder canvas and is clobbered if the builder is saved from
   a stale page.
2. **Missing vocabulary.** Several standard, broadcast-relevant CSS properties
   do not exist in the compiler or UI at all: `padding`, `border-radius`,
   vertical alignment, `text-transform`, `letter-spacing`, `opacity`,
   `line-height`, `text-shadow`, `rotation`.

We want every slot to offer the full, sensible set of standard properties —
driven by a single source of truth rather than per-element whitelists.

## Scope

- **In scope:** the **HUD page only** — the single builder-editable overlay page.
  The race timer is **not** a separate page; it is the `clock` text slot inside
  the HUD (the timer/HUD merge, 2026-06-14) and is fully in scope.
- **Out of scope:** the `splitscreen` page (styled by a separate override CSS, no
  `data-edit` slots) and any change to the relay's live data binding / render
  path. No new overlay page.

## Key insight: slot *kind* decides the property set

Every editable slot is one of two kinds:

- **`text`** — renders text, optionally with a box look: `stint`, `session`,
  `streamer`, `round-top`, `round-country`, `race-control`, `clock`, `pov-name`,
  `team*-num`, `team*-name`.
- **`box`** — a container with no own text, including image slots: `team*-logo`,
  `round-flag`, `pov`.

The kind, not a per-element whitelist, determines which properties the slot
offers. A small `KIND_PROPS` table is the single source; the existing
`slot["props"]` list (consumed by both the compiler and the UI) stays the
interface — it is now derived from the kind instead of hand-maintained.

## Property vocabulary (final)

Existing (unchanged): `left, top, width, height, fontSize, fontFamily, color,
background, align, borderWidth, borderStyle, borderColor, teamNameMax,
teamNameMin`.

New:

| Key | CSS | Type | Kinds |
|---|---|---|---|
| `padding` | `padding` | px | text, box |
| `borderRadius` | `border-radius` | px | text, box |
| `opacity` | `opacity` | number 0–1 | text, box |
| `rotation` | `transform: rotate(Ndeg)` | number (deg) | text, box |
| `valign` | `align-items` | enum top/middle/bottom | text |
| `textTransform` | `text-transform` | enum none/uppercase/lowercase/capitalize | text |
| `letterSpacing` | `letter-spacing` | px | text |
| `lineHeight` | `line-height` | number (unitless) | text |
| `textShadow` | `text-shadow` | compound (offsetX, offsetY, blur px + color) | text |

### Kind → property sets

```
BOX_PROPS  = left, top, width, height,
             background, padding, borderRadius,
             borderWidth, borderStyle, borderColor,
             opacity, rotation
TEXT_PROPS = BOX_PROPS
           + align, valign, fontSize, fontFamily, color,
             letterSpacing, textTransform, lineHeight, textShadow
KIND_PROPS = { "text": TEXT_PROPS, "box": BOX_PROPS }
```

`team*-name` additionally carries `teamNameMax`/`teamNameMin` (auto-fit, issue
#136) — expressed as a per-slot **extra** on top of its kind, not a new kind.

Update (#957): every text slot (any slot offering `fontSize`, whether through the kind,
an explicit `data-edit-props` list or the default set) also offers the generic
auto-fit bounds `fitMax`/`fitMin` (→ `--fit-max`/`--fit-min`, 1..500 px), added by
`extract_slots`. A slot that carries the team-name pair drops the generic one, so a
slot never shows two max/min controls. The pages fit generically through the shared
`src/ui/overlay-fit.js`.

## Architecture (additive — relay untouched)

```
hud.html             each slot carries  data-edit-kind="text|box"
                     (+ optional data-edit-props for extras, e.g. autofit)
        │
        ▼
overlay_build.extract_slots(html)
        │  slot.props = KIND_PROPS[kind] (+ data-edit-props extras), document order
        ▼
  ┌─────────────┴───────────────┐
  ▼                             ▼
compile_overlay_css           /api/overlay/slots  →  Control Center builder
 (emits #id { ... })            (renders fields from slot.props, groups them,
                                 applies them live in the shadow-DOM canvas)
        │
        ▼
profiles/<name>/overlay/hud.css  (served by relay at /hud/override.css)
```

### Component 1 — compiler (`src/scripts/overlay_build.py`)

- `KIND_PROPS`, `BOX_PROPS`, `TEXT_PROPS` constants (the single source).
- Value handling:
  - add `padding`, `borderRadius`, `letterSpacing` to the px props;
  - `opacity` → number validated to `[0, 1]` (dropped if outside, like a bad enum);
  - `lineHeight` → unitless number (validated, sane bounds);
  - `rotation` → `transform: rotate(<n>deg)` (number, validated);
  - `valign` → `align-items` via `_VALIGN = {top: flex-start, middle: center,
    bottom: flex-end}`;
  - `textTransform` → `text-transform` via an enum map;
  - `textShadow` → assembled from four override keys (`textShadowX`,
    `textShadowY`, `textShadowBlur` px + `textShadowColor`) into **one**
    `text-shadow: Xpx Ypx Bpx COLOR` declaration. Emitted only when set. No
    free-text path — the four parts are validated individually, so the existing
    `_UNSAFE_VALUE` guarantee (no value can close a rule or inject CSS) holds.
- `PROP_ORDER` extended so emit order stays stable and deterministic.
- `extract_slots` reads `data-edit-kind`, resolves `props = KIND_PROPS[kind]`
  plus any `data-edit-props` extras, in document order. A slot with an explicit
  `data-edit-props` and no kind still works (back-compat fallback).
- Unknown/disallowed props and bad enum/number values are dropped — the only
  unfiltered path remains `customCss`.

### Component 2 — markup (`src/obs/hud.html`)

- Each slot gains `data-edit-kind="text"` or `"box"`.
- `team*-name`: `data-edit-kind="text" data-edit-props="teamNameMax,teamNameMin"`.
- Redundant explicit prop lists removed where the kind now covers them.
- **Base `<style>` is unchanged** — defaults (the current look) are untouched;
  the feature only widens what the builder can override.

### Component 3 — Control Center builder (`src/ui/control-center.html`)

- Property panel **grouped into sections** to manage density: *Position*,
  *Size*, *Text* (text kind only), *Box*. New widgets: vertical-align select,
  text-transform select, padding / border-radius / letter-spacing number
  fields, opacity (0–1), line-height (number), rotation (degrees), and a
  text-shadow sub-group (offset X/Y, blur, color).
- **Save behaviour:** the new optional properties are **override-only** —
  emitted only when the operator sets them, never force-captured from the base
  style. `ovBaseValues` keeps capturing today's core geometry/colour set; this
  keeps generated CSS lean and avoids brittle computed-style parsing (notably of
  `text-shadow`).
- **Live canvas:** `ovStyleSlot` / `ovApplyProp` apply every new property inline
  in the shadow-DOM canvas, so they are visible in the builder (unlike the
  `customCss` workaround).
- **Rotation caveat (documented limitation):** the canvas selection box and
  resize handles compute axis-aligned geometry. While a slot has a non-zero
  rotation, the handles/selection outline are approximate. The angle is still
  settable via its field and renders correctly live and on air; precise
  resizing should be done at rotation 0. Acceptable for v1.

### Component 4 — tests (`tests/test_overlay.py`, TDD)

Failing first, then implement:

- `extract_slots`: `team*-num` now resolves the full text set (incl.
  `width`, `height`, `align`, `valign`, `padding`, `borderRadius`, `opacity`,
  `letterSpacing`, `textTransform`, `lineHeight`, `textShadow`, `rotation`);
  `team*-logo` / `round-flag` / `pov` resolve the box set (no text props);
  `team*-name` keeps the auto-fit extras.
- `compile_overlay_css`: each new property emits the correct declaration;
  `valign`/`textTransform` reject unknown values; `opacity`/`lineHeight`/
  `rotation` reject non-numbers / out-of-range; `textShadow` assembles one safe
  declaration and omits when unset; injection attempts via the new props are
  dropped.
- Update the pinned slot-prop assertions
  (`t_ob_extract_slots_from_real_hud`) to the new kind-derived sets.

## Backward compatibility & migration

- Existing per-league layouts keep working: slots gain *allowed* properties,
  none are forced; untouched slots compile identically.
- **IRO GTEC follow-up (separate, profile-specific, after this ships):** set the
  number-badge `width`/`height`/`valign` in the builder and delete the
  `customCss` width workaround from that profile — no longer needed once the
  properties are native.

## Definition of done

- `KIND_PROPS`-driven slots; all listed properties compile correctly and safely.
- Builder renders the grouped fields, applies them live, saves them lean.
- Tests above pass; full suite + lint + `tools/build.py` green.
- `cc-overlay-builder.png` wiki screenshot regenerated in the same change
  (CLAUDE.md hard rule — the builder UI changed).

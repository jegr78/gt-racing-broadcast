# Flag graphics follow the Sheet, plus Checkered

Extends `2026-06-29-flag-status-graphics-design.md`.

## Problem

The Race Control desk and the Director Panel offer a fixed set of five flag
graphics (Green, Yellow, Red, Safety Car, Virtual Safety Car). A league that never
linked, say, the Safety Car PNG in its Sheet Assets still gets the button, and
taking it shows the transparent placeholder: nothing on air. A Checkered flag
graphic does not exist at all.

## Decisions

- The code keeps a catalog of flag graphics, each backed by its own OBS image
  source. The buttons show only the catalog entries the league actually linked in
  its Sheet Assets. A flag outside the catalog still needs a code change; a fully
  Sheet-driven list with one shared OBS source was considered and rejected as too
  large a change to the collection, the panel and the Companion board.
- The catalog gains `checkered` -> `Flag Checkered`.
- The HUD flag chip (Director Panel FLAG field, Sheet Setup) is out of scope.
- No Companion button for Checkered. `/obs/flag/set/<key>` stays as it is, so the
  six existing buttons keep working.

## Availability

A catalog flag is available when `<graphics_dir>/<source>.png` exists and is not
byte-identical to the transparent graphic placeholder. That is the same signal
`list_graphics` uses: `get-graphics` writes the placeholder for every
OBS-referenced graphic the Sheet does not link. The "OBS only" (internal) Assets
column is ignored here, because flag rows are usually marked internal.

- `placeholders.is_graphic_placeholder(path)` becomes the one placeholder check;
  the relay's `_is_placeholder_png` uses it.
- `flag_graphic.available_flags(graphics_dir)` returns the available keys in
  catalog order. An unset `graphics_dir` means every catalog flag is available
  (keeps callers without a runtime dir, and today's tests, working).
- `FlagGraphicStore` takes an optional `graphics_dir`. `available()` exposes the
  list; `set(key)` answers an error for a known but unavailable key; `clear()`
  always works.
- The check runs per request, a directory read of at most six files. A
  `racecast graphics` refresh therefore shows up within one poll, no relay restart.
- A persisted active flag that is no longer available stays stored; it simply has
  no button. Taking any available flag or clearing replaces it.

## Surfaces

- `graphic_takes.definitions` is unchanged (it still lists every catalog flag).
  `graphic_takes_view` drops flag entries whose key is not available, so the Race
  Control card shows only available flags and stays hidden when none is.
  `apply_graphic_take` answers 404 for an unavailable flag.
- `GET /obs/flag/data` adds `available: [{"key", "source"}]`. The Director Panel
  builds its flag bus from that list on each poll (rebuilding only when the key
  set changes) instead of its hard-coded `FLAG_GFX`. Button labels come from a
  small key -> label map (`VSC` stays short), falling back to the source name
  without the `Flag ` prefix in upper case.
- `crew-frame.html` gets a black-and-white button style for `Flag Checkered`.

## OBS collections

`Flag Checkered` is added to all three collections as an image source with
`__RACECAST_GRAPHICS__/Flag Checkered.png`, as a scene item next to the other flags
in the same scenes (Stint and Splitscreen in endurance, Program in both solo
collections), hidden by default. Existing installs need a fresh collection import
(`racecast setup`); until then the source is missing in OBS and a take is a no-op
note, as today for any missing source. The wiki (OBS-Setup, Sheet-Template) and the
release notes say so.

## Tests

- `tests/test_flag_graphic.py`: availability with a real PNG, the placeholder, a
  missing file, an internal-marked row; `set` refuses an unavailable key;
  `chequered` and `checkered-flag` normalise to `checkered`.
- Graphic-take view: unavailable flags are dropped, the 404 on take.
- `/obs/flag/data` carries `available`.
- A collection check that every catalog source exists in all three collections in
  every flag scene.
- Wiki screenshots: the Race Control desk and `director-panel.png`.

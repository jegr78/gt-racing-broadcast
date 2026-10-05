# CLAUDE.md: `src/obs/` (OBS collection, overlay pages, broadcast assets)

Loaded when working under `src/obs/`. Moved out of the root CLAUDE.md verbatim; the root
keeps the rules. The per-league overlay override and the visual builder are in
`src/ui/CLAUDE.md`; the relay that serves these pages is in `src/relay/CLAUDE.md`.

## Two token round-trips (keep paths/secrets out of git)
- **OBS.** `src/obs/GT_Racing_Endurance.json` stores tokens: `__RACECAST_GRAPHICS__` (broadcast
  still-graphics dir) and `__RACECAST_MEDIA__` (Intro/Outro/Trailer clip dir). The HUD and the
  race timer are relay-served on the fixed loopback (`127.0.0.1:8088`, no token), the
  Sheet URL is no longer embedded in the collection (the relay reads `SHEET_ID` from the
  active profile). Timer state = Sheet tab `Timer` + `runtime/timer.json`,
  Director-controlled via `/timer/*` endpoints; the race-timer clock is **rendered
  inside `hud.html`** (the page polls `/timer/data`: there is no separate `timer.html`,
  and `/timer/*` is a JSON API, not a served page). The relay's second overlay page is
  **`splitscreen.html`** (`/splitscreen` + `/splitscreen/data`), an alternate layout for
  a two-feed split. **OBS browser sources cache JS aggressively:** after
  `hud.html`/`splitscreen.html` (or a per-profile overlay CSS) change, OBS keeps the old
  page until refreshed. `racecast relay start` and `racecast event start` do that
  automatically: once the relay answers every page in `OBS_PAGE_PATHS`, obs-websocket
  `refreshnocache` reloads every browser source pointing at the relay, unconditionally,
  because a source that loaded while the relay was down keeps CEF's error page;
  `racecast obs refresh` does the same by hand. The
  manual right-click → Refresh remains the fallback when obs-websocket is unreachable.
  Anything that must survive a reload therefore lives server-side (`runtime/timer.json`,
  the Sheet), never in page JS. When you edit scenes inside OBS, re-export and fold it
  back with `tools/tokenize-obs.py exported.json src/obs/GT_Racing_Endurance.json`
  (regex-tokenizes the graphics image-source basenames + any Google-Sheet URLs).
  `src/setup-assets.py`
  does the reverse, injecting real values (the active profile's runtime dirs) into an
  importable collection at `runtime/<profile>/GT_Racing_Endurance.import.json`, naming it the
  league's `OBS_COLLECTION` (default `GT Racing Endurance — <league>`). OBS stores
  **absolute** paths, so the localized collection must not be moved after import.
  `src/relay/get-media.py` downloads the Intro/Outro/Trailer clips (sheet-driven via the
  Assets tab `Intro Video`/`Outro Video`/`Trailer Video` labels, or
  `RACECAST_INTRO_URL`/`RACECAST_OUTRO_URL`/`RACECAST_TRAILER_URL` env overrides) into
  `runtime/<profile>/media/`; the `Intro`/`Outro`/`Trailer` OBS scenes play them looping
  with audio.
  The localized export also **syncs the overlay boxes**: for every slot in
  `overlay_build.OVERLAY_SLOT_OBS_SOURCES` (`#pov` → "Feed POV", `#webcam` → "Solo Webcam",
  `#tyres-capture` → "Solo Tyres/Fuel Capture"), `setup-assets.py` layers the active
  profile's `overlay/hud.css` (`--overlay-css`) over the `hud.html` base box
  (`overlay_build.slot_boxes`) and writes it onto the scene item's `pos`/`bounds`, the 1:1
  overlay-frame↔PiP mapping. Only items the collection actually has are touched and
  reported. The same boxes are pushed **live** by the `racecast obs refresh` /
  `relay start` / `event start` hook (`_sync_pov_transform` →
  `obs_ws.set_scene_item_transform`), so a builder edit aligns the PiP without a
  re-import. The live push targets each slot's scene per kind (`overlay_build.slot_scene`):
  "Feed POV" sits in Stint for endurance and in Program for solo, which is also where the
  relay's POV toggle shows it (`obs_ws.graphic_scene`). obs-websocket v5 takes `boundsType` as the enum name
  (`OBS_BOUNDS_SCALE_INNER`), not the number the collection JSON stores; a number is
  rejected with code 401. A transform OBS rejects is printed, a slot the collection
  lacks stays silent. Spec: `docs/superpowers/specs/2026-06-26-pov-box-obs-sync-design.md`.
  **Visibility follows the profile too** (#766) for `overlay_build.VISIBILITY_SLOTS`
  (`#webcam`, `#tyres-capture`): `display: none` on the plain slot selector in the
  profile's `hud.css` hides the item, anything else shows it, baked into the import's
  `visible` flag (`bake_overlay_visibility`) and pushed live by the same hook
  (`obs_ws.set_scene_item_enabled`). Only a selector-list item exactly `#webcam` counts,
  never `:not(#webcam)` or `#webcam .x`. Feed POV is excluded on purpose: the
  director's live POV toggle owns its visibility.
- **Broadcast graphics are pure-runtime** (same model as the Intro/Outro/Trailer clips): the
  still-graphics (Overlay, Standings, Schedule, Race/Quali Results, the three weather
  overlays, Standby, …) are **never committed**. `python3 src/relay/get-graphics.py`
  downloads each one from the Sheet **Assets** tab into
  `runtime/<profile>/graphics/<Label>.png` (the Sheet label *is* the filename, no
  mapping table; YouTube Intro/Outro/Trailer rows are skipped). They are tokenised
  `__RACECAST_GRAPHICS__/<Label>.png` in the collection and resolved by
  `setup-assets.py` (which warns, never fails, on a missing file → OBS shows black until
  you run `get-graphics.py`). `src/assets/` therefore holds **only** the HUD `flags/` +
  `brands/` logos (still committed, relay-served). Brand logos are served **override-first**:
  `runtime/<profile>/brands/<asset_key>.png` (downloaded from the Sheet `Brands` tab by
  `get-brands.py`) wins over the committed `src/assets/brands/` base; the override directory
  travels in `profile export` as a third asset section alongside `graphics/` and `media/`.
- **Companion.** Export the config into the gitignored `incoming/` folder, then
  `tools/strip_companion_pass.py` blanks the WebSocket password and writes
  `src/companion/racecast-buttons.companionconfig`. `build.py` re-strips defensively.

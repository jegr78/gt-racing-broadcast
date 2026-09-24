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
  automatically: a hash gate over the *served* page bytes (`runtime/obs-pages.hash`,
  covering `OBS_PAGE_PATHS` = `/hud`, `/hud/override.css`, `/splitscreen`,
  `/splitscreen/override.css`) triggers obs-websocket `refreshnocache`
  on every browser source pointing at the relay; `racecast obs refresh` forces it. The
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
  The localized export also **syncs the per-league POV-box position**: `setup-assets.py`
  reads the active profile's `overlay/hud.css` (`--overlay-css`, passed by the CLI) and
  applies its `#pov` box (`left/top/width/height`) onto the OBS **"Feed POV"** scene item
  (`pos`/`bounds`, the 1:1 overlay-frame↔PiP mapping). The same box is pushed **live** to
  a running OBS by the `racecast obs refresh` / `relay start` / `event start` hook
  (`_sync_pov_transform` → `obs_ws.set_scene_item_transform`), so a builder edit aligns the
  PiP immediately without a re-import. POV-only (`overlay_build.OVERLAY_SLOT_OBS_SOURCES`);
  best-effort, a missing overlay/OBS leaves today's behavior. Pure parser:
  `overlay_build.pov_box_from_css`. Spec: `docs/superpowers/specs/2026-06-26-pov-box-obs-sync-design.md`.
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

# CLAUDE.md: Control Center (`src/racecast_ui.py` + `src/ui/`)

Loaded when working under `src/ui/`.

`racecast ui` serves a local web app (`src/ui/ui_server.py`, port 8089 /
`RACECAST_UI_PORT`) for dashboard, service control and logs. Two settings surfaces:
- **Profile view**: switch the active profile, create a new one (new-profile dialog,
  optionally `--from` an existing one), edit the active league's `profile.env`, style the
  per-league overlays in the **visual overlay builder** (drag/resize the HUD/Timer slots
  on a same-origin Shadow-DOM canvas over `Overlay.png`, with a fonts uploader and an
  advanced-CSS escape hatch), download profile-scoped graphics/media, and manage the
  **crew roster** in the **crew editor** (reads the league Sheet's `Crew` tab via the
  relay's `/crew/data`; writes per-row director/producer flags back via the `crew`
  webhook action: routes `/api/crew`, `/api/crew/delete`). The Crew tab
  (`Name | Commentator | Director | Producer | Discord` header in row 1) and the `crew`
  Apps Script action are a league Sheet-side coordination item (see `Sheet-Webhook` wiki
  page); without them roles degrade gracefully and the editor surfaces an
  outdated-script banner. Routes:
  `/api/profiles`, `/api/profile/{use,new,env}`, `/api/overlay`,
  `/api/overlay/{slots,layout,fonts,bg,font/<name>}`, `/api/crew`, `/api/crew/delete`.
- **General Settings**, machine-wide knobs: the `.env` editor (`RACECAST_*` vars),
  cookie refresh, and the **overlay font library** (`runtime/fonts/`, shared across
  leagues). A curated baseline set (`overlay_build.GOOGLE_FONTS`) is downloaded at build
  time into `fonts.zip`, bundled INTO each binary, and extracted into `runtime/fonts/` on
  first start by `ensure_bundled_fonts()` (stamp-gated, only-if-absent, zip-slip-safe, so
  every install has fonts without a manual download, and `racecast update` adds new
  bundled fonts). `racecast fonts restore --force` (Settings: **Restore bundled fonts**,
  route `/api/fonts/restore`) overwrites same-named bundled fonts in the library and in
  every profile's `overlay/fonts/`. Operators add further families by name via the Settings typeahead (routes
  `/api/fonts`, `/api/fonts/{catalog,download,delete}`); `tools/fetch-fonts.py` is the
  maintainer tool that builds the zip. A font a league's design uses is copied into that
  profile's `overlay/fonts/` on save (`_materialize_overlay_fonts`), so `profile export`
  stays self-contained; the relay/canvas serve it locally (no broadcast-time CDN).
- **Telemetry** (solo POV only: the nav item and the view carry `pov-only`): lap
  comparison from the profile's GT7 recordings. `src/scripts/gt7_laps.py` builds a lap
  index per recording (5 m traces, 200 m sectors), cached as `<stem>.laps.json` and
  rebuilt when the recording's size/mtime or `gt7_data.data_version` change; the data
  layer also memoises up to 4096 index summaries per process (`_telemetry_index`). One
  Set track changes `data_version` (it stats `learned-tracks.json`), so every index of the
  profile needs a rebuild, about 16 s per 3 h recording. No request builds an index: the
  routes pass `build=False`. A pool of `/api/telemetry/laps` uses the recordings with a
  valid index and counts the rest in `unindexed`; `?rec=`, `/api/telemetry/lap` and
  `/api/telemetry/learn` answer `unindexed: 1` for a recording without one; every such
  answer carries the `data_version` it was checked against. The page (`tmIndexed`,
  `tmIndexAll`, `tmMayIndex`) then runs the `telemetry-index` job (`racecast telemetry
  index`) once per lap generation and data version (the relay's background GT7 data
  update can change it mid-generation; a joined run from another window records no
  version), polls it through `/api/jobs/<id>` and asks once more; still unindexed shows
  the job's "not indexed" line as an error, or its last line, which names every
  recording the job could not index. Set track shows "Indexing…" on its button while
  its job runs. The CLI and the event-stop report keep building; the Report view's
  Generate runs `racecast report generate` as the `report-generate` job and reads the
  written file through `GET /api/report/read?name=`. A counted lap's trace closes at
  the full lap length with a common zero-based, lap-normalized receiver clock, so its
  sectors add up without a backwards closing point. Index version 7 retains sparse recording seek offsets and separates capture
  completeness, trace coverage, roles, pace eligibility and historical reference status.
  The unfinished tail is `open_lap`, not an extra completed lap; API lists expose it
  separately and the single-lap endpoint can retrieve its trace. Data functions
  `telemetry_*_data` in `src/racecast.py`; routes `/api/telemetry/recordings`,
  `/api/telemetry/laps`, `/api/telemetry/lap`, `/api/telemetry/tracks`,
  `/api/telemetry/learn`, `/api/telemetry/delete` (POST `{rec}`, `telemetry_delete_data`,
  which shares `_telemetry_delete_path` with `racecast telemetry delete`). A recording's
  `/api/telemetry/laps?rec=` answer also carries `summary` (`_telemetry_summary`:
  `report_telemetry.telemetry_block` without the map), so the view's summary row shows the
  post-event report's figures. The recording the relay is writing is listed but refused by
  laps/lap/learn/delete. `learn` only assigns a layout that has a downloaded racing line
  (`TrackDB.has_downloaded_line`) and learns the line otherwise. Charts and map are
  inline SVG in `control-center.html` (block "Telemetry view"). Demo data for
  Expanded channel detail is decoded on demand by `gt7_channels.py` and
  `gt7_channel_detail.py`, through `GET /api/telemetry/channels`. Catalog requests are
  lightweight; selected-channel windows reuse sparse byte offsets from the index,
  with raw time windows bounded to 30 s / 5000 packets and at most eight channels.
  The inline channel UI loads only while expanded, discards stale lap/selection answers,
  reports constants/availability and keeps uncertain raw fields separate. CSV retains
  its legacy column prefix, appends confirmed fields/states and documents them in
  `channels.json`; `--diagnostics` adds a separate raw export.
  screenshots: `tools/make-demo-recording.py`. Tests: `tests/test_gt7_laps.py`,
  `tests/test_racecast.py`, `tests/test_ui_server.py`, `tests/test_make_demo_recording.py`.

## Per-league overlay override + visual builder (moved from the root CLAUDE.md)
- **Per-league overlay (optional).** `profiles/<name>/overlay/hud.css` (+ an optional
  `splitscreen.css`) + `overlay/fonts/` restyle the relay-served overlay pages per
  league via cascade-wins override CSS: the base `hud.html`/`splitscreen.html` carry a
  `<link>` to the override last in `<head>`, so a league can recolor/reposition the
  overlay without forking the page. (A legacy `overlay/timer.css` from before the timer
  merged into the HUD is folded verbatim into the HUD layout's `customCss` on load: see
  `overlay_layout_read_data` in `src/racecast.py`.) The relay serves `/hud/override.css`,
  `/splitscreen/override.css`, and
  `/overlay/fonts/<file>` (each read per request from the `--overlay-dir`; empty body
  when the file is absent). The CLI passes `--overlay-dir profiles/<active>/overlay`
  whenever that dir exists (`_overlay_relay_args` in `src/racecast.py`). The two
  override.css are part of `OBS_PAGE_PATHS`, so every `relay start`/`event start`
  reloads them in OBS. Editable in the Control Center, a **visual overlay
  builder** (issue #114): the slots' `data-edit` markers in `hud.html`
  are the single slot source, a pure compiler (`src/scripts/overlay_build.py`,
  `compile_overlay_css`) turns a `layout-<page>.json` the builder owns into the
  generated `<page>.css`, and a hand-written `<page>.css` is migrated verbatim into the
  layout's `customCss` (the pro escape hatch, appended last) on first use, so the
  relay serves the generated file unchanged. Spec:
  `docs/superpowers/specs/2026-06-13-visual-overlay-builder-design.md`. The **first**
  override on a profile whose `overlay/` did not exist when the relay started needs one
  `racecast relay restart` (the `--overlay-dir` flag is decided at launch), but later
  edits apply live. Tests: `tests/test_overlay.py` (compiler + slot extraction +
  migration), `tests/test_ui_server.py` + `tests/test_racecast.py` (routes + data layer).

## Wiki screenshots for UI changes (full rule, moved from the root CLAUDE.md)
- **Changed a UI surface? Refresh its wiki screenshot in the SAME change.** This
  is the step that keeps getting forgotten. Any visible change to the **Control
  Center** (`src/ui/`), the **Director Panel** (`/panel`), or the **Companion /
  Web Buttons** means the matching image under `src/docs/wiki/images/` is now
  stale and MUST be regenerated and committed alongside the code, never as a
  "later" follow-up. Surface → image: Control Center views → `cc-<view>.png`
  (e.g. the overlay builder → `cc-overlay-builder.png`); Director Panel →
  `director-panel.png`; Companion pages → `companion-page<N>-*.png`. How to
  recapture (all three skills are repo-anchored under `.claude/skills/`, so they
  travel with the checkout. Mac, Windows or Linux): Companion buttons via the
  **`companion-screenshots`** skill; Control Center / Director Panel / the
  `/console` + cockpit pages via the **`wiki-screenshots`** skill (it drives a
  running dev-build instance with the Playwright MCP, takes a **full-window**
  screenshot of the view, or an **element** screenshot of a modal such as
  `#ov-modal .ovmodal-card`, so the framing matches the existing images, and documents the reproducible fake-content
  recipe: the `demo` profile + `tools/obs-sim.py` OBS stand-in, so the pages show a
  believable broadcast with no real OBS/league). Verify a published wiki render
  with **`wiki-visual-test`**. **Always capture Control Center screenshots from a local
  dev build** (run `racecast ui` straight from `src/`, no `VERSION` file stamped) so
  every `cc-*.png` shows the same "dev build" version badge. A real version baked into
  one shot goes stale at the next release and breaks uniformity, the dev-build state
  is the only fully reproducible one. If you refresh a single `cc-*.png`, still use the
  dev build so it matches the rest. Publishing the wiki itself stays a separate
  `tools/sync-wiki.py` step, but the image must already be committed in the repo.

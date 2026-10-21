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
- **Telemetry workspace** (`/telemetry`, `racecast telemetry ui`): local archive
  analysis with a request-scoped profile independent of the broadcast profile.
  The Control Center retains solo POV sharing/replay controls only. See
  `src/docs/design/telemetry-workspace.md`. Lap comparison uses the selected
  analysis profile's GT7 recordings. `src/scripts/gt7_laps.py` builds a lap
  index per recording (5 m traces, 200 m sectors), cached as `<stem>.laps.json` and
  rebuilt when the recording's file identity, size, nanosecond mtime/ctime or
  `gt7_data.data_version` change; the data
  layer also memoises up to 4096 index summaries per process (`_telemetry_index`). One
  Set track changes `data_version` (it stats `learned-tracks.json`), so every index of the
  profile needs a rebuild, about 16 s per 3 h recording. No request builds an index: the
  routes pass `build=False`. A pool of `/api/telemetry/laps` uses the recordings with a
  valid index and counts the rest in `unindexed`. The optional `recs` query restricts
  the pool and all best/theoretical values to explicit recording stems; empty selects
  none. Available recording metadata remains independent of this subset. The page
  defaults to the selected recording, groups pickers in driving order and keeps its
  checkbox choice in page memory. Details: `src/docs/design/telemetry-recording-selection.md`; `?rec=`, `/api/telemetry/lap` and
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
  sectors add up without a backwards closing point. Index version 10 binds sparse
  recording seek offsets to the original file and complete recording header, and separates capture
  completeness, trace coverage, roles, pace eligibility and historical reference status.
  Known-layout completeness is checked against original driven coverage and the
  first projected station before trace normalization. Incomplete captures cannot
  become pace or historical references; usable laps determine historical improvements.
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
  inline SVG rendered by `telemetry-workspace.js` in `telemetry.html`. Demo data for
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
  even before that directory exists (`_overlay_relay_args` in `src/racecast.py`). The two
  override.css are part of `OBS_PAGE_PATHS`, so every `relay start`/`event start`
  reloads them in OBS. Editable in the Control Center, a **visual overlay
  builder** (issue #114): the slots' `data-edit` markers in `hud.html`
  are the single slot source, a pure compiler (`src/scripts/overlay_build.py`,
  `compile_overlay_css`) turns a `layout-<page>.json` the builder owns into the
  generated `<page>.css`, and a hand-written `<page>.css` is migrated verbatim into the
  layout's `customCss` (the pro escape hatch, appended last) on first use, so the
  relay serves the generated file unchanged. Spec:
  `docs/superpowers/specs/2026-06-13-visual-overlay-builder-design.md`. The **first**
  override works without restarting the relay because its overlay path is retained
  before the directory exists. Edits apply through **Apply in OBS**. Tests:
  `tests/test_overlay.py` (compiler + slot extraction +
  migration), `tests/test_ui_server.py` + `tests/test_racecast.py` (routes + data layer).

## Wiki screenshots for UI changes (full rule, moved from the root CLAUDE.md)

The scene selector also exposes Intermission in both profile kinds (#930).
`layout-intermission.json` compiles to `intermission.css` through the same APIs;
scene-scoped `/api/overlay/apply` explicitly sets the saved Intermission video
geometry/visibility and refreshes only its browser source. Automatic refresh only
hides scene items. `tools/e2e-scene-layouts.py --shots` exercises the production
builder and callbacks with temporary synthetic profiles and refreshes both builder
screenshots; it never uses real devices, OBS or league state.
Starter layouts are profile-aware slot patches from `intermission_live.starter_layouts`.
Selection only changes the editor model and Undo history. Intermission Apply also
restores saved visibility through the relay controller; automatic refresh honors
runtime overrides and never enables a video item. The Director Panel reads actual
OBS state through `/obs/intermission/data`; chat uses a separate browser override.
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

Optional AI analysis lives in `ai-analysis.js`, loaded by the dedicated workspace
as a same-origin cached asset.
Machine settings are shared; completed session/lap/reference selection, explicit
preview/start, progress/cancel/history and text-only reports remain profile-bound.
Selection/profile changes invalidate pending previews and discard asynchronous old
responses. `tools/e2e-ai.py` exercises the real callbacks with a synthetic provider
process and no credentials; the visual CI job runs it alongside normal views.

### Splitscreen builder (#935)

Endurance adds `splitscreen` to the scene selector and layout/read/write/Apply APIs;
Solo rejects it before filesystem or OBS mutations. `split_layout.builder_html`
combines the existing Endurance HUD markup with the real Split feed/label markup,
excluding Solo telemetry/capture slots. Feed slots expose bounds only. Label
`offsetRight`/`offsetTop` map to margins and drag independently inside a feed-linked
legacy coordinate plane; selection outlines sum nested offsets to the stage.
Templates patch draft geometry with Undo. `tools/e2e-splitscreen.py --shots` covers
real pointer/numeric/template events, legacy CSS, live labels and profile gating.
The dedicated image is `cc-splitscreen-builder.png`.

### Shared audience widgets (#932)

`audience_widgets.Store` owns `profiles/<name>/audience.json`, outside `overlay/`.
Local `/api/audience` edits require a checked expectedProfile and same origin.
Definitions/manual values/baselines are shared; scene models store placement and
style only. Compiler/editor HTML includes all definitions as an allowlist, while
the canvas filters to current draft placements. The relay injects saved placements
once into HUD, Intermission or the dedicated Split HUD. Shared `audience.js`
updates existing nodes from `/audience/data`; Save alone never creates live nodes.
The common renderer also fills the isolated sample canvas without live polling.
Look restore cannot rewind shared state. `tools/e2e-audience.py` drives the actual
manager, scene callbacks, live pages and fake-clock rendering without services.

### Asset aliases (#936)

The local producer asset API retains filename identities and adds optional
displayName from the active profile's runtime manifests, including private local
labels. Profile galleries poll every three seconds without rebuilding unchanged
media players. Refresh labels runs the fixed graphics/media --metadata-only jobs.
Builder background metadata identifies Overlay.png or Intermission.png explicitly;
scene/role names are not globally substituted. Shared crew and Director captions
use text nodes, and preview identity is stored separately from displayed text.
`tools/e2e-asset-aliases.py` exercises real UI/HTTP callbacks; its optional
--companion argument full-imports only into a supplied disposable native instance.


### Audience provider connections (#933, #934)

The shared manager offers separate YouTube and Twitch dialogs and source choices.
Profile/page/load/request identities reject late replies. Edited verification
inputs disable activation until verified again. Twitch shows only the public
approval code/validated hosted URL, account, output and granted capabilities.
Tokens and private device codes never reach the UI. Explicit activation remains
retryable after a storage error without repeating OAuth exchange. Twitch net
subscription goals measure active change, including decreases, not all new-sub
events. `tools/e2e-twitch-audience.py` uses actual loopback UI/admin/overlay pages
with injected provider fixtures and writes matching wiki/slide shots via --shots.

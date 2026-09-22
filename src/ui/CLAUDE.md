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
  every install has fonts without a manual download, and `racecast update` refreshes the
  set). Operators add further families by name via the Settings typeahead (routes
  `/api/fonts`, `/api/fonts/{catalog,download,delete}`); `tools/fetch-fonts.py` is the
  maintainer tool that builds the zip. A font a league's design uses is copied into that
  profile's `overlay/fonts/` on save (`_materialize_overlay_fonts`), so `profile export`
  stays self-contained; the relay/canvas serve it locally (no broadcast-time CDN).

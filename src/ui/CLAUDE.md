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
  override.css are part of `OBS_PAGE_PATHS`, so editing them advances the refresh hash
  and OBS reloads automatically. Editable in the Control Center, a **visual overlay
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
  running dev-build instance with the Playwright MCP, takes an **element**
  screenshot of the relevant card/modal, e.g. `#ov-modal .ovmodal-card`, so the
  framing matches the existing images, and documents the reproducible fake-content
  recipe: the `demo` profile + `tools/obs-sim.py` OBS stand-in, so the pages show a
  believable broadcast with no real OBS/league). Verify a published wiki render
  with **`wiki-visual-test`**. **Always capture Control Center screenshots from a local
  dev build** (run `racecast ui` straight from `src/`, no `VERSION` file stamped) so
  every `cc-*.png` shows the same "dev build" version badge. A real version baked into
  one shot goes stale at the next release and breaks uniformity, the dev-build state
  is the only fully reproducible one. If you refresh a single `cc-*.png`, still use the
  dev build so it matches the rest. Publishing the wiki itself stays a separate
  `tools/sync-wiki.py` step, but the image must already be committed in the repo.

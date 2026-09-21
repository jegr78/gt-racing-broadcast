# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A self-contained broadcast-production toolkit (**GT Racing Broadcast**) for
sim-racing endurance leagues, run on a producer's machine (Windows, macOS, or Linux).
The core is a **relay** that pulls one commentator YouTube or Twitch stream per race stint
and serves it to OBS; around it sit an OBS scene collection, a Stream Deck (Companion)
button config, and operator docs. It is **multi-profile**: one install hosts several
leagues, each as a `profiles/<name>/` directory (league config + optional per-league
overlay CSS); the active profile is switchable. Pure Python + stdlib (no framework,
no package manager); external runtime deps are `yt-dlp`, `streamlink`, `ffmpeg`,
`deno` (installed via brew, not vendored).

## Hard rules

- **Edit only under `src/`.** `dist/` and `runtime/` are generated and gitignored —
  never hand-edit them. `tools/` are maintainer scripts (not shipped).
- **All scripts and docs must be English only.** (Chat with the user is German; the
  code/docs are read by an international team.)
- **Never hardcode secrets or machine paths.** Secrets come from `.env` (see below).
  The OBS collection and scripts are deliberately path/secret-free in git.
- Tooling is Python-only by design — do not reintroduce `.sh`/`.bat` (the build
  fails if any are shipped).
- **Outbound HTTP goes through `src/scripts/http_util.py`** (the covered side:
  `racecast.py`, `ui_server.py`, `src/scripts/*`). It always sends a racecast
  `User-Agent`; Discord and other Cloudflare-fronted hosts (Google Fonts, some
  vendor endpoints) 403 the default `Python-urllib/x.y` UA, so a bare `urllib`
  call silently fails. `tests/test_http_util.py` enforces this — it fails if a
  covered file uses `urlopen`/`urllib.request` directly. Exceptions, each
  already setting its own UA: the self-contained relay/`get-*`/`setup-assets`
  scripts (deliberately dependency-light, must not import shared modules) and
  `src/scripts/update.py` (needs its own HTTPS-only redirect opener + test seam).
- **Removing/renaming a CLI flag? Grep the whole repo — including `tools/` and
  `.github/`.** Those callers run only in the build/release pipeline, not in the
  test suite (a stale `--timer-url` in the binary smoke test broke every v1.0.0
  release build). CI's `binary-smoke` job now exercises the binary path on every
  PR, but grepping first is cheaper than a red pipeline.
- **Never invent domain rules or crew conventions.** Docs describe the
  *mechanism* (what a button/endpoint does); broadcast procedure (who goes on
  air when, with which feed) is defined by the team, not derived. State
  assumptions explicitly and ask when uncertain instead of asserting.
- **Tests must run on any machine and in CI** — no real IPs, no machine paths,
  no environment-specific values; use fixtures/parameters (Tailscale IPs are
  `100.64.0.0/10` test constants, never this machine's address). Prefer TDD:
  failing test first, then the fix.
- **Cross-platform paths: the test matrix includes Windows.** A helper that
  assembles a *fixed-OS* absolute path — e.g. a macOS `/Applications/<App>.app`
  bundle — must build it with explicit forward slashes (`bundle +
  "/Contents/Info.plist"`), NOT `os.path.join`. `os.path.join` injects
  backslashes on the Windows runner, so a unit test that exercises that helper
  (with a POSIX path pinned in the fixture) passes on macOS/Linux but fails on
  `windows-latest` — even though production only ever runs the helper on its own
  OS. Use `os.path.join` only for paths on the *current* machine; never run it on
  a path you already know belongs to a different OS. (Broke #97's Windows CI.)
- **Pipeline/permission problems (release-please, tokens, branch protection,
  Actions) are research-first:** map the complete lifecycle and requirement set
  (docs + known issues) before changing anything — one planned fix, not
  symptom-per-loop trial and error.
- **Changed a UI surface? Refresh its wiki screenshot in the SAME change.** This
  is the step that keeps getting forgotten. Any visible change to the **Control
  Center** (`src/ui/`), the **Director Panel** (`/panel`), or the **Companion /
  Web Buttons** means the matching image under `src/docs/wiki/images/` is now
  stale and MUST be regenerated and committed alongside the code — never as a
  "later" follow-up. Surface → image: Control Center views → `cc-<view>.png`
  (e.g. the overlay builder → `cc-overlay-builder.png`); Director Panel →
  `director-panel.png`; Companion pages → `companion-page<N>-*.png`. How to
  recapture (all three skills are repo-anchored under `.claude/skills/`, so they
  travel with the checkout — Mac, Windows or Linux): Companion buttons via the
  **`companion-screenshots`** skill; Control Center / Director Panel / the
  `/console` + cockpit pages via the **`wiki-screenshots`** skill (it drives a
  running dev-build instance with the Playwright MCP, takes an **element**
  screenshot of the relevant card/modal — e.g. `#ov-modal .ovmodal-card` — so the
  framing matches the existing images, and documents the reproducible fake-content
  recipe: the `demo` profile + `tools/obs-sim.py` OBS stand-in, so the pages show a
  believable broadcast with no real OBS/league). Verify a published wiki render
  with **`wiki-visual-test`**. **Always capture Control Center screenshots from a local
  dev build** (run `racecast ui` straight from `src/`, no `VERSION` file stamped) so
  every `cc-*.png` shows the same "dev build" version badge. A real version baked into
  one shot goes stale at the next release and breaks uniformity — the dev-build state
  is the only fully reproducible one. If you refresh a single `cc-*.png`, still use the
  dev build so it matches the rest. Publishing the wiki itself stays a separate
  `tools/sync-wiki.py` step, but the image must already be committed in the repo.

## Commands

```bash
# Tests (stdlib only — each file under tests/ is a runnable script, no pytest).
# `ls tests/` lists them; each file's name says what it covers.
python3 tests/test_pov.py            # e.g. one file: relay POV/schedule unit checks
python3 tools/run-tests.py           # the whole suite (exactly what CI runs)
python3 tools/lint.py                # ruff lint (= the CI lint job); --fix auto-corrects.
                                     # Rules mirror the CodeQL alert classes — see ruff.toml.
                                     # Run it after changing any Python file.
# Run ONE test function:
python3 -c "import sys; sys.path.insert(0,'tests'); import test_pov as t; t.t_pov_format_constant()"

# Build the distributable (assembles + self-verifies dist/)
python3 tools/build.py               # -> dist/GT_Racecast_Package/ + .zip
# Standalone binary (maintainer; CI builds all three OSes on tags v*)
python3 tools/build-binary.py        # -> dist/bin/racecast + dist/bin/racecast-ui (+ smoke test)

# End-to-end / regression harness (maintainer; stands up relay + Control Center, asserts the live HTTP surface)
python3 tools/e2e.py                  # synthetic mode: self-contained, no real Sheet/cookies/OBS — the CI `e2e` job runs this
python3 tools/e2e.py --real-league NAME   # local-only: drive the copied real-league dev build (refuses under CI)
python3 tools/e2e.py --playwright [--headed] [--shots DIR]  # optional rendered checks / visible browser / MCP-free screenshot tour

# Unified operator CLI (the producer's main entrypoint) — the canonical, always-current
# command list is the CLI's own help; read it instead of duplicating it here:
python3 src/racecast.py --help
python3 src/racecast.py <group> --help    # e.g. relay | event | profile | console | obs

# Non-obvious bits that `--help` does NOT tell you:
# - cookies: Firefox is the recommended browser — Windows Chrome/Edge exports are blocked
#   by app-bound encryption. `cookies twitch <browser>` is only needed for gated
#   (sub/follower-only) Twitch feeds.
# - install-tools: deno has no apt package, so it is a pinned, SHA-256-verified
#   GitHub-release download into runtime/bin (racecast adds that to PATH); brew is
#   bootstrapped on macOS; Linux apt runs via sudo.
# - obs-browser: Linux/aarch64 only — no distro/PPA ships obs-browser there and the relay
#   HUD/timer need a Browser Source. Pins CEF per OBS version (obs_browser_linux.py).
#   On no-GPU/VM hosts also disable OBS Browser Source Hardware Acceleration.
# - freeport: refuses a running relay/streams (it would cut a live feed) unless --force.
#   Per-process kill (NOT the session-group kill of #133's stop path); port→PID lookup is
#   cross-platform in src/scripts/ports.py.
# - console: the per-league CONSOLE_SECRET is auto-provisioned on first relay start
#   (zero-config) — there is no enable/disable command.
# - event takeover --funnel: authenticated with the shared league CONSOLE_SECRET (step-up
#   X-Console-Secret header); /console/takeover/status is REDACTED — feed stream URLs
#   never leave the tailnet.
# - --profile NAME runs ONE command against a non-active profile.

# Fetch any missing HUD country flags from the sheet's Configuration tab
python3 tools/fetch-flags.py            # adds missing -> src/assets/flags/ (keeps old)

# Probe the broadcast-chat reader (#294) against a LIVE channel — standalone, no
# Sheet/relay/UI. YouTube: resolve the live videoId via yt-dlp + tail Innertube chat.
# Twitch: anonymous IRC (auto-detected for twitch.tv URLs, or --twitch for a bare name).
python3 tools/broadcast-chat-probe.py https://www.youtube.com/@SomeChannel  # --resolve-only / --cookies
python3 tools/broadcast-chat-probe.py https://www.twitch.tv/SomeChannel     # or: --twitch SomeChannel

# Probe GT7 UDP telemetry (#324) against a LIVE PS4/PS5 — standalone, no relay/Sheet.
python3 tools/gt7-telemetry-probe.py --ps-ip 192.168.1.42   # heartbeat + decrypt + field dump

# Publish the GitHub wiki from src/docs/wiki/ (maintainer; --dry-run to preview)
python3 tools/sync-wiki.py
```

After changing the relay, run `python3 tests/test_pov.py`; after any change that
ships, run `python3 tools/build.py` — its verify step is the closest thing to CI
(checks tokenization, blanked password, no secrets, preflight present, no shell
scripts).

## Architecture

### Single-source + build
`src/` is the only source of truth. `tools/build.py` copies it into
`dist/GT_Racecast_Package/` (the artifact handed to other producers), stripping
the Companion password and renaming the tokenized OBS collection to
`GT_Racing_Endurance.template.json`. `runtime/` holds machine-local state (cookies, logs,
caches, the localized OBS import) and is gitignored. Helpers detect whether they run
from the repo (`src/...`) or the distributed package and pick paths accordingly —
see `default_runtime_dir()` (relay/get-cookies) and `state_dir()` (scripts).

### Profiles + config (`src/scripts/config.py`) — the multi-profile model
Config comes from **two layers** and one resolver:
- **Machine `.env`** (gitignored, repo root or next to the binary; template
  `.env.example`) holds ONLY machine-local knobs — never league secrets:
  `RACECAST_OBS_WS_PASSWORD`, `RACECAST_COMPANION_EXE`, `RACECAST_UI_PORT`,
  `RACECAST_UI_PASSWORD` (reserved/unused), and `RACECAST_PROFILE` (default active
  profile when no `--profile` is given).
- **`profiles/<name>/profile.env`** is the **league** — un-prefixed keys `NAME`,
  `SHEET_ID` (Google Sheet driving schedule + HUD), `SHEET_PUSH_URL` (optional Apps
  Script webhook that lets the relay write to the Sheet: race-timer state + the
  panel's HUD/Schedule/POV controls), `INTRO_URL`, `OUTRO_URL`, `TRAILER_URL`, `LOGO`,
  `OBS_COLLECTION`, `CONSOLE_SECRET` (signs per-person console tokens; auto-provisioned
  on first relay start), and optionally `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET`
  (per-league Discord OAuth app — when present, `/console/login` + `/console/oauth/callback`
  are activated; when absent, OAuth is off and signed links remain the only entry path).
  The shipped `profiles/example/` is a template, excluded from the usable-league list.
  One install hosts several leagues this way.

`src/scripts/config.py` is the resolver: it parses the machine `.env` + the selected
`profiles/<name>/profile.env`, picks the active profile (precedence: `--profile` >
`RACECAST_PROFILE` env > `runtime/active-profile` pointer file > the sole profile when
exactly one exists), and returns a `ResolvedConfig`. The CLI then **injects** the
active league's values into child processes as **prefixed** env vars
(`RACECAST_SHEET_ID`, `RACECAST_SHEET_PUSH_URL`, `RACECAST_INTRO_URL`,
`RACECAST_OUTRO_URL`, `RACECAST_TRAILER_URL`, `RACECAST_OBS_COLLECTION` — see `_profile_env_vars` /
`_apply_active_profile_env` in `src/racecast.py`), so the relay and the asset
downloaders read a flat environment and stay profile-agnostic. `racecast profile
list|show|use|new|export|import [--from/--no-assets/--out/--force]` manages profiles;
global `--profile NAME` runs one command against a non-active profile. `racecast
profile export NAME` packages the entire `profiles/<name>/` tree (including
`SHEET_PUSH_URL` in `profile.env`) plus the optional runtime `graphics/`, `media/`, and `brands/`
into a single zip that can be imported on another machine with `racecast profile
import FILE` — this is the onboarding path for handing a league to a new producer.
This is distinct from `racecast backup …` (`backup_admin.py`), which is a
profile-internal named snapshot of the overlay CSS + graphics + media only and never
crosses machines.

A small bounded `load_dotenv()` is **duplicated** in the five self-contained scripts
that can run standalone — `src/relay/racecast-feeds.py`, `src/setup-assets.py`,
`src/relay/get-media.py`, `src/relay/get-graphics.py`, and `src/relay/get-brands.py`
(`get-brands.py` also duplicates `asset_key`) — reading a `.env` only from
the script dir or the project root (marker: `.git`/`.env.example`), never an unrelated
parent; real environment variables take precedence. These deliberately do NOT import
`config.py` (the relay stays dependency-light), but the canonical loader for everything
else is `src/scripts/config.py`. Keep the five `load_dotenv` copies in sync if you
touch one.

### Profile-scoped runtime
Per-league machine state lives under **`runtime/<profile>/`**: the localized OBS
import (`GT_Racing_Endurance.import.json`), downloaded `graphics/` and `media/`, etc. Shared
machine state (cookies jar, the active-profile pointer) stays at `runtime/` top level.
So `racecast graphics` / `media` / `setup` always write into the active profile's
runtime dir; switching profiles points the CLI at a different one.

The **relay is a machine singleton** (it binds the shared control port 8088 + feed
ports, so only one can run), so its **PID file `runtime/relay.pid` lives at the
top level**, not per-profile — with a sidecar `runtime/relay.profile` recording which
profile it was started under. That way `relay stop`/`status`/`logs` find and act on the
one running relay regardless of the active profile, and the relay's per-profile **logs**
still resolve to the profile it actually runs under (`_running_relay_dir()`). A
per-profile relay PID used to let a `profile use` while the relay ran orphan it on 8088
(#273). `profile use` now refuses to switch while a relay/streams is running unless
`--force`, and `relay start` reports a foreign holder of 8088 (recover with `racecast
freeport 8088`).

### Two token round-trips (keep paths/secrets out of git)
- **OBS.** `src/obs/GT_Racing_Endurance.json` stores tokens: `__RACECAST_GRAPHICS__` (broadcast
  still-graphics dir) and `__RACECAST_MEDIA__` (Intro/Outro/Trailer clip dir). The HUD and the
  race timer are relay-served on the fixed loopback (`127.0.0.1:8088`, no token) — the
  Sheet URL is no longer embedded in the collection (the relay reads `SHEET_ID` from the
  active profile). Timer state = Sheet tab `Timer` + `runtime/timer.json`,
  Director-controlled via `/timer/*` endpoints; the race-timer clock is **rendered
  inside `hud.html`** (the page polls `/timer/data` — there is no separate `timer.html`,
  and `/timer/*` is a JSON API, not a served page). The relay's second overlay page is
  **`splitscreen.html`** (`/splitscreen` + `/splitscreen/data`), an alternate layout for
  a two-feed split. **OBS browser sources cache JS aggressively:** after
  `hud.html`/`splitscreen.html` (or a per-profile overlay CSS) change, OBS keeps the old
  page until refreshed. `racecast relay start` and `racecast event start` do that
  automatically — a hash gate over the *served* page bytes (`runtime/obs-pages.hash`,
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
  (`pos`/`bounds` — the 1:1 overlay-frame↔PiP mapping). The same box is pushed **live** to
  a running OBS by the `racecast obs refresh` / `relay start` / `event start` hook
  (`_sync_pov_transform` → `obs_ws.set_scene_item_transform`), so a builder edit aligns the
  PiP immediately without a re-import. POV-only (`overlay_build.OVERLAY_SLOT_OBS_SOURCES`);
  best-effort — a missing overlay/OBS leaves today's behavior. Pure parser:
  `overlay_build.pov_box_from_css`. Spec: `docs/superpowers/specs/2026-06-26-pov-box-obs-sync-design.md`.
- **Broadcast graphics are pure-runtime** (same model as the Intro/Outro/Trailer clips): the
  still-graphics (Overlay, Standings, Schedule, Race/Quali Results, the three weather
  overlays, Standby, …) are **never committed**. `python3 src/relay/get-graphics.py`
  downloads each one from the Sheet **Assets** tab into
  `runtime/<profile>/graphics/<Label>.png` (the Sheet label *is* the filename — no
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
- **Per-league overlay (optional).** `profiles/<name>/overlay/hud.css` (+ an optional
  `splitscreen.css`) + `overlay/fonts/` restyle the relay-served overlay pages per
  league via cascade-wins override CSS — the base `hud.html`/`splitscreen.html` carry a
  `<link>` to the override last in `<head>`, so a league can recolor/reposition the
  overlay without forking the page. (A legacy `overlay/timer.css` from before the timer
  merged into the HUD is folded verbatim into the HUD layout's `customCss` on load — see
  `overlay_layout_read_data` in `src/racecast.py`.) The relay serves `/hud/override.css`,
  `/splitscreen/override.css`, and
  `/overlay/fonts/<file>` (each read per request from the `--overlay-dir`; empty body
  when the file is absent). The CLI passes `--overlay-dir profiles/<active>/overlay`
  whenever that dir exists (`_overlay_relay_args` in `src/racecast.py`). The two
  override.css are part of `OBS_PAGE_PATHS`, so editing them advances the refresh hash
  and OBS reloads automatically. Editable in the Control Center — a **visual overlay
  builder** (issue #114): the slots' `data-edit` markers in `hud.html`
  are the single slot source, a pure compiler (`src/scripts/overlay_build.py`,
  `compile_overlay_css`) turns a `layout-<page>.json` the builder owns into the
  generated `<page>.css`, and a hand-written `<page>.css` is migrated verbatim into the
  layout's `customCss` (the pro escape hatch, appended last) on first use — so the
  relay serves the generated file unchanged. Spec:
  `docs/superpowers/specs/2026-06-13-visual-overlay-builder-design.md`. The **first**
  override on a profile whose `overlay/` did not exist when the relay started needs one
  `racecast relay restart` (the `--overlay-dir` flag is decided at launch), but later
  edits apply live. Tests: `tests/test_overlay.py` (compiler + slot extraction +
  migration), `tests/test_ui_server.py` + `tests/test_racecast.py` (routes + data layer).

### The relay (`src/relay/racecast-feeds.py`) — the heart
A 2-feed "ping-pong": **Feed A** (port 53001) serves odd stints, **Feed B** (53002)
even stints; at each handover the off-air feed advances to the next stint's
commentator stream, so OBS media sources never change URL. A 3rd **POV** feed
(53003) is an optional driver picture-in-picture, paused at start. The schedule is a
Google-Sheet tab read as CSV (no API key); a running feed is never torn off
mid-stint — sheet edits apply on the next `/next` (handover) or `/reload`.
**Qualifying mode** (issue #124): a second `ScheduleSource` reads a separate
`Qualifying` tab (same URL/Streamer/Stint structure); `Relay.mode` ∈
{race, qualifying} and `self.source` is a property returning the active one, so
every path (status/next/reload/set_stint/handover) is mode-aware. Qualifying is a
single stream → it lands on Feed A (B idles). Switch at launch (`--qualifying` /
`racecast event start --qualifying`) or live via `/mode/race`|`/mode/qualifying`
(`set_mode`, re-points feeds like a takeover); the panel has a Qualifying section
(mode toggle + a one-row editor writing the Qualifying tab via the `schedule`
webhook action with `tab:"Qualifying"`). On switch the HUD Streamer/Stint follow
the qualifying row (the issue #112 path).
**Qualifying in the event lifecycle.** Qualifying is also a first-class *broadcast
Part*: a `Q` row in the `Producer` tab (its own Stream Key) is the qualifying
broadcast, and the Director-Panel Parts control is **mode-gated** —
`producer.active_producer_rows(rows, mode)` selects the numeric parts in race mode and
the single `Q` part in qualifying mode (a part is qualifying iff its label, uppercased,
starts with `Q`). Because `Q` is the only (= last) part, ending it fires the existing
last-part auto-stop → report → teardown, so `racecast event start --qualifying` (or the
Control Center **Qualifying** toggle at Start Event, `ui_ops` `_qualifying_flag`) runs a
clean, separate qualifying session with its own OBS stream key. `set_mode` resets the
part pointer to Part 1 on a live mode switch (unless a part is on air). The post-event
**report title** gains a `— Qualifying` marker (`_relay_mode`/`_qualifying_title` in
`racecast.py`, read from the live relay `/status`). The **Director Panel** shows the
schedule editor matching the mode (race editor `#urlsBox` vs the qualifying row
`#qualRow`; the mode toggle, submissions and Parts control stay visible in both). The
cockpit tally/plan, the race-control desk, `/schedule/data` and submission *target*
resolution are already mode-aware (they read the mode-aware `relay.source`); cockpit
stream-link **submissions record their `mode`** so the director's approve writes the
qualifying row via `qualifying_set` (the confirm phrase for the Q part reads
`START PART Q`). Spec/plan:
`docs/superpowers/{specs,plans}/2026-07-05-qualifying-event-lifecycle*.md`.

Pull pipeline per feed: **YouTube** — `yt-dlp -g` resolves the live HLS URL (passing
YouTube's bot-check via `yt-cookies.txt` + deno JS challenge) → `streamlink
--player-external-http` serves that URL to one OBS client. **Twitch** — routed directly
through Streamlink's Twitch plugin (no yt-dlp hop); gated feeds optionally use
`twitch-cookies.txt`. **Local** (#592) — a Schedule URL cell `local:` reads the
producer machine's capture card (`RACECAST_CAPTURE`; game audio defaults to the card's own audio device found by name in ffmpeg's device list — `scan_capture_audio`, override/`none` via `RACECAST_CAPTURE_AUDIO` — from the
machine `.env`) with the relay's own `ffmpeg` writing MPEG-TS to stdout at the same fan-out
seam (`local_capture_cmd`, bitrate capped by `LOCAL_VIDEO_KBPS` against the 16 MB ring);
no resolve, cookies or quality tiers, fan-out required. The producer's commentary mic
for that stint is the OBS input `Commentary Mic Device` (#593, in `Stint` + `Splitscreen`,
shipped muted, `RACECAST_MIC`): the relay opens it only while the local feed is on air
and mutes it on every other handover/SPLIT (`obs_ws.feed_audio_plan`, snapshotted in
`Relay.obs_audio_plan`; only on an endurance machine with `RACECAST_CAPTURE`, never in
solo, where the mic ships hot). A hand-picked STINT A/B uses the same plan through
`GET /obs/stint/<A|B>` / `POST /obs/stint` (`apply_stint_state`) — the panel macro calls
only that, Companion calls it after its direct OBS actions (the break-glass path when the
relay cannot reach OBS; they never touch the mic), and the smoke test does the same. A failed switch (collection imported before #593) is a relay-log WARNING. Only the director/sheet can set
it (`is_feed_source`); the commentator submit and POV paths stay on `is_channel`. (`curl`-ing a feed port returns nothing — it serves a single
consumer; that is not a failure.)

**Feed fan-out (`RACECAST_FEED_FANOUT`, machine `.env`, DEFAULT ON — live-verified 2026-06-29; set `=0` to fall back).** By default the relay is the single `streamlink --stdout` consumer per feed and re-serves the byte stream via an in-relay `FeedFanoutServer` (`FeedRing` bounded byte ring on the same loopback feed port) to OBS *and* the Director Panel preview simultaneously — eliminating the ~2 s stale-on-activation glitch and making the preview tap free (no second pull). Health moves from "serve process exited" to "bytes stopped flowing": a byte-stall watchdog (`RACECAST_FEED_STALL_S`, default 20 s since #488; `FANOUT_STALL_S = 8.0` survives only as `feed_stalled`'s default parameter) kills the reader and drives the DROP state. The fan-out reader PIPEs streamlink's stderr (its only diagnostic channel here — stdout is the video bytes) and pumps it to `feed_X.log` with a `[streamlink]` tag, parity with direct-serve; discarding it once left every fan-out stall/EOF unexplained. **Caveat — VOD vs live:** because the in-relay ring overflows (the reader never blocks), there is NO consumer backpressure, so against a finite **VOD** streamlink races ahead at multiples of real-time → it reaches EOF in minutes (reconnect churn) and its bursty re-fetch can trip the stall watchdog; a real **live** stream is naturally throttled to ~1× at the live edge, so neither happens. A just-dropped *served* feed is a SILENT health blip until it stays down past `HEALTH_CONNECTING_SETTLE_S` (15 s, below the 30 s red grace), so a reconnect that self-heals within a heartbeat does not fire a DEGRADED Discord `@here` (`drop_connecting_notifiable`, unit-tested); a longer stall still surfaces yellow before red, and red (genuine loss) is immediate. The relay also **measures how far OBS is behind the live edge** (#583): `_serve` records the position OBS has accepted at the start of every read cycle (the read itself jumps the cursor to the trailing mark, so only that position shows a slow consumer), `FeedRing.age_at_offset` turns it into seconds, `consumer_backlog` is the live value on `/status` (Director-Panel header pill `BEHIND LIVE`) and `take_backlog_floor` is the per-heartbeat minimum stored in `health-history.db` v9. A floor more than `RACECAST_FEED_BACKLOG_WARN_S` (5 s, uncalibrated until #584) beyond the prebuffer is a quiet, never-paging yellow and a cockpit note (`program_behind_s`), and since 2026-09-21 the relay **sheds it by itself**: `_backlog_shed_tick` (heartbeat, right after the classification it reads) rebuilds the ON-AIR feed's OBS input so the picture returns to the live edge without a director pressing RESET. It is a second REASON on the freeze detector's control, not a second automation — same `f._obs_reconnect()`, same `RebuildGuard` (three ineffective rebuilds then stand down), same cooldown — because two automations turning one control is a race someone debugs mid-broadcast. `RebuildGuard.pending` is therefore a reason tag, and each reason judges only its own rebuild with its own signal; a rebuild still unjudged when the next one fires counts as ineffective rather than being overwritten. The rebuild registers a splice (`_note_obs_splice`, which takes no clock on purpose) so the A/V detector does not flag the relay's own remedy as unexplained. Off by `RACECAST_FEED_BACKLOG_SHED=0`; `RACECAST_FEED_BACKLOG_SHED_TICKS` (default 1) raises the streak, one tick being enough because the classified floor is already a whole-interval minimum. On-air feed only (an off-air one has no consumer under `close_when_inactive`), POV excluded. This **overrides** #581's *"only a human chooses it"*, which came out of Catalunya's 26 black dropouts; the producer's standing preference is automatic recovery with the manual `/obs/feed-reset` (#587) as the fallback, and `RebuildGuard` plus the measured backlog are what make the override survivable. A backlog that keeps GROWING is NOT fixed here (the shed shears it, the slow host rebuilds it, the guard stands down with a warning) — that remains #585. See `docs/superpowers/specs/2026-09-21-automatic-backlog-shed-design.md`. `/status` also carries each feed's `inbound_max_gap_s`, the heartbeat's last #535 reading. It is published read-only, so a 2 s poll never steals `take_max_inbound_gap`'s reset. Its gate is serving + fan-out, which is NOT `backlog_s`'s gate: `backlog_s` also needs an attached consumer, while the gap is measured on the source side and stays valid while OBS is away for a rebuild (blanking it there fed the benchmark a leading run of `None`, which let the pre-restart reading through). The heartbeat keeps two maps for this: `_interval_max_gaps` raw for health-history.db, and `_served_max_gaps` with `None` for an interval nothing measured, so a feed going on air between two ticks cannot republish the idle `0.0` as a healthy-looking measurement. `racecast obs benchmark` records it next to the backlog (#619), since the backlog alone shows a bursty source and a slow consumer the same way. Its resolution is the heartbeat's 30 s, and it drops the reading a window inherits from before the restart, anchored on the first actual reading so a leading `None` run cannot smuggle it back in. That rule (`feed_backlog_degraded` + both env parsers) lives in `health_store.py` so the post-event report's backlog-led finding (#586, `report_build._finding`) counts exactly what the director saw; the report reads the configured OBS frame rate from health-store v10 `obs_fps_target`. The relay unconditionally sets `Feed A`/`Feed B` `close_when_inactive` to match the flag — `True` when fan-out is on (OBS drops off-air so no stale backlog forms), `False` when off (restores the safe direct-serve state so a fallback never leaves feeds stuck at `True`); `Feed POV` ships `True` in both modes and is left untouched (best-effort, OBS unreachable → a note, never a crash). Setting `RACECAST_FEED_FANOUT=0` falls back to the proven `--player-external-http` direct-serve path (one streamlink process → one OBS consumer) — the coexistence switch stays so a producer can revert instantly; a transport choice, not a league setting. By default OBS and the program-audio monitor are **served only up to a trailing high-water mark `RACECAST_FEED_PREBUFFER_S` seconds behind the live edge** (default 3 s, #533) — every read is capped there, not just the join, so a greedy consumer cannot outrun it — holding an in-ring reserve that absorbs bursty-source gaps (`=0` restores the live-edge serve); the Director-Panel preview still taps the live edge. **A restart rejoins OBS** (#614): since the relay owns the socket, a restart would otherwise splice the new stream into OBS's stale demuxer, so `should_obs_reconnect` rebuilds the feed input after a drop **and** whenever a consumer is still attached to the ring — the director's `/reload`, a tier change, and `set_index` on an on-air feed (every `/next` in solo/qualifying). The ping-pong handover is excluded: `close_when_inactive` already dropped the off-air feed. The rejoin **waits out the HLS prefetch burst** first, because the trailing mark is keyed on byte ARRIVAL: a burst that lands inside the prebuffer window sits entirely above the mark, so an immediate rejoin would put OBS at the burst's START, 10-19 s behind live. How long the burst takes to ARRIVE is a download duration (downlink, source bitrate, CDN), so the relay **measures it per serve** instead of predicting it: the rejoin thread watches `last_byte_ts` and takes the first inbound idle of `BURST_IDLE_S` as the burst's end, then waits the prebuffer, then rebuilds. Only two constants are fixed, and both describe the SOURCE's segment cadence, not the connection: `BURST_IDLE_S` (1.0 s — measured 2026-09-20 on YouTube and Twitch, the usable window is ~(0.78, 1.4) because gaps *inside* a burst reach 0.78 s on YouTube while Twitch low-latency's steady cadence is only 1.4-1.9 s) and `SEGMENT_FETCH_BUDGET_S` (1.0 s) as a **ceiling** via `prefetch_land_s(segments, prebuffer_s)`, for a source that never pauses that long — Twitch low-latency does not. A flat 5 s constant was ~3 s short for YouTube ROBUST, which is what started this. Re-measure with `tools/prefetch-burst-probe.py` (no relay, no league needed). A local capture feed (#592) has no `--hls-live-edge` and never waits; a rejoin whose serve was superseded or died during the wait no-ops (`rejoin_is_stale`). `/status` exposes `feed_prebuffer_s` so `racecast obs benchmark` derives the same wait. See `docs/superpowers/specs/2026-06-28-relay-feed-fanout-design.md`.

Control is an **unauthenticated** `ThreadingHTTPServer` on port `8088` exposing GET
endpoints (`/next`, `/reload`, `/set/A/<n>`, `/pov/reload`, `/timer/*`, `/status`,
`/panel`, plus the served pages `/hud`, `/splitscreen` and the per-league overlay assets
`/hud/override.css`, `/splitscreen/override.css`, `/overlay/fonts/<file>`, …)
driven by Companion's Generic-HTTP module. `--bind` defaults to **`auto`** (plug &
play): it binds `127.0.0.1` (OBS always reaches the HUD/feeds on the fixed loopback
address — the OBS collection never needs editing) **and** this machine's Tailscale IP
(auto-detected via `detect_tailscale_ip()`, the `100.64.0.0/10` CGNAT range) when
present, so remote directors/tablets reach `/panel` + `/hud` over the tailnet — *without*
exposing the unauthenticated server on the local LAN the way `0.0.0.0` would. If
Tailscale is down, `auto` falls back to localhost-only (OBS keeps working). Pass an
explicit value (`127.0.0.1` for local-only, or `0.0.0.0`) to override. The endpoints
have no auth and `/status` reveals stream URLs, so the tailnet is the trust boundary —
keep it to invited members. Bind logic is pure + unit-tested: `tests/test_bind.py`.

**Logging.** The relay and each static-stream feed write timestamped, leveled lines
(`YYYY-MM-DD HH:MM:SS LEVEL …`) to per-service log files under `runtime/<profile>/logs/`
via `src/scripts/logsetup.py` (`TimedRotatingFileHandler`, daily midnight rotation,
archive suffix `.YYYY-MM-DD`). Old archives are pruned on each service start: the
retention window defaults to 7 days and is overridable with
`RACECAST_LOG_RETENTION_DAYS`. Each relay feed has its own `feed_A/B/POV.log`; the
streamlink child's output is pumped through the feed logger with a `[streamlink]` tag
and classified levels (ERROR for 4xx/fatal, WARNING for retries). The `relay` and
`streams` CLI log sources are **merged-file views** (console + all feed logs in one
stream); `aggregate` is the default Control Center source and merges all live sources
(relay, streams, OBS, Companion, Tailscale). OBS Studio and Companion logs are
read-only from their native app directories; the Tailscale source appends a
timestamped `tailscale status` snapshot on each service start and on
`racecast tailscale status`. Archive history is accessible with
`relay|streams logs --list` / `--archive <date>` (racecast sources) or by filename
token (OBS/Companion).

The same server also hosts the **lower-third HUD** as one relay-served page,
replacing ~13 cropped Google-Sheets-editor browser sources (the old producer-lag
culprit): `/hud` serves `src/obs/hud.html`, `/hud/data` returns the overlay JSON
(`HudSource` reads the **Overlay** tab for live values + the **Configuration** tab's
brand-text column — header `Brand Name`/`Brand Key`/`Brand`, see `BRAND_TEXT_HEADERS` —
for team→manufacturer), and `/hud/assets/{flags,brands}/<name>`
serves bundled logos from `src/assets/`. The page polls `/hud/data` (no manual
reloads); flags/brands resolve from text via `asset_key()`. Flags: `--no-hud`,
`--overlay-tab`, `--config-tab`, `--hud-poll`, `--overlay-dir` (per-league override
CSS/fonts, passed by the CLI when `profiles/<active>/overlay` exists). The optional
**Quali Times** tab (`--quali-times-tab`, default `Quali Times`) adds each car's
qualifying best lap. Its fetch is **off the HUD refresh path entirely**
(`HudSource.refresh_quali`, called once at boot + by its own `quali_poller` thread every
`QUALI_TIMES_POLL_S` = 60 s — the laps are entered once between qualifying and the race),
so no quali-tab state can delay an on-air `refresh()` or a synchronous panel-push confirm;
`refresh()` only reads the last-good map. A fetch/parse failure keeps that **last-good**
map (never rolled back to empty) and warns once, an
existing tab whose header was renamed/removed replaces the map with empty, and a league
that never created the tab simply stays empty. A lap is matched per **car**: the verbatim
`Team` cell first, then the `#NNN`-stripped name, so two cars of one team keep their own
lap while a bare row still matches every car. The Configuration tab's `BG Color`/`Text
Color` columns surface as `teams[].bgColor`/`textColor`, and `/hud/data` also carries the
relay's `mode`. Tests: `tests/test_hud.py`.

The panel's **sheet controls** write back through one Apps Script webhook
(`RACECAST_SHEET_PUSH_URL`, injected by the CLI from the active profile's
`SHEET_PUSH_URL`, shared with the race timer — wiki: Sheet-Webhook):
Setup fields (Stint label/Streamer/Session/Race Control) are async-optimistic
(`HudSource` override now, sheet poll confirms, 30 s expiry), Schedule/POV URL
writes are synchronous; URL changes never auto-reload a feed. Setup "Stint" =
HUD display label, NOT the feed stint index. `SetupControl` + endpoints
`/setup/*`, `/schedule/*`, `/pov/set` (POST). Tests: `tests/test_setup.py`.

The relay also hosts a **crew chat** (`GET /chat/data`, `POST /chat/send`,
`GET /chat/reload`) — an in-memory ring buffer (400 messages) persisted to
`runtime/<profile>/chat.json`. The panel polls `/chat/data`; messages render via
`textContent` (XSS-safe); the unread badge is keyed on server `ts` (handover-safe).
There is **no destructive HTTP endpoint** — clear/import/pull are producer-only CLI
actions (`racecast chat clear|pull|import|export`, logic in
`src/scripts/chat_admin.py`) that write the file and trigger `/chat/reload`. The
tailnet is the trust boundary (unauthenticated, like the rest of the relay).
Tests: `tests/test_chat.py`.

The relay also hosts a **read-only broadcast-chat reader** (issue #294): a mirror of
the event's **public YouTube and Twitch** broadcast chat inside the `/console` pages (cockpit,
director panel, race-control desk) so the crew can follow it without a separate browser
tab. The broadcast channel(s) come from a Sheet **`Channel`** tab (header `Platform |
Channel`; `Channel` holds a channel URL / `@handle` / `UC…` id — **never** a video id),
read by `ChannelSource` (mirrors `CrewSource`); derived from `SHEET_ID` like the crew
roster, so a custom `--sheet-csv-url` or `--no-broadcast-chat` disables it (flag
`--channel-tab`, default `Channel`). `BroadcastChatSupervisor` reconciles, each ~30 s
cycle, a DESIRED set of readers keyed by a stable id (stop those no longer desired,
start new ones, retry a died one unless it is tombstoned):
- **YouTube** — one `_BroadcastReader` per **currently-live videoId**, resolved via
  yt-dlp (the **`/streams`** tab so CONCURRENT live streams are all found — the
  producer-handover overlap where B's stream starts before A's ends — with `/live` as
  the single-stream fallback; **public streams only**). Each reader bootstraps from the
  `live_chat` page then follows the **Innertube `get_live_chat` continuation** — a native
  stdlib poller (relay-owned network, like `CrewSource`, **exempt** from the `http_util`
  UA guard; it must send a browser `User-Agent` or Innertube 403s). A genuinely-ended
  stream is *tombstoned* (not restarted until its videoId leaves the live set).
- **Twitch** (Phase 2) — one `_TwitchReader` per **channel login** (`twitch:<login>`): a
  persistent **anonymous IRC** connection (`irc.chat.twitch.tv:6697` over TLS, a
  `justinfan` nick, `CAP REQ twitch.tv/tags`, `JOIN #login`) that needs **no API key or
  OAuth** — pure stdlib `socket`+`ssl`. It reconnects on drop with backoff; the login is
  strictly validated (`twitch_login`, `[a-z0-9_]{1,25}`) so a channel value can never
  inject IRC commands. `PRIVMSG`s are parsed by `parse_twitch_privmsg` (display-name +
  message id + `tmi-sent-ts` from the tags).
`BroadcastChatStore`
is an **ephemeral** in-memory ring (`broadcast_chat.MAX_MESSAGES = 500`), dedup-by-id,
**ts-merged across streams** (so a handover overlap renders as one continuous chat,
tagged by source — videoId or `twitch:<login>`) — **never persisted, no write path**. Endpoints:
`GET /broadcast-chat/data` (tailnet/loopback) + `GET /console/broadcast-chat/data`
(Funnel, **ANY-auth under the existing `/console` mount → no new public surface**), both
read-only. The data is already public on the platform, so mirroring it leaks nothing; if the
reader is disabled the endpoints 404 and the front-end card self-hides. A read-only
`target` (`{platform, url}`) field on `/broadcast-chat/data` (and the Funnel
`/console/broadcast-chat/data`) carries the current primary live source so each console
card can show a **"Write in chat ↗"** button that `window.open`s the native YouTube/Twitch
popout chat — the crew posts under their **own browser account**; the relay adds **no write
path** and stays read-only/ephemeral. The target is computed each supervisor cycle (pure
`primary_chat_target` in `broadcast_chat.py`, from the already-resolved live set, KISS:
first live source) and exposed via `BroadcastChatStore.set_target`; the front-end gates the
button on a non-null `target` and validates the URL client-side (`bchatUrlOk`: https +
platform host, mirroring `emote_url_ok`) before opening. **Backend
choice (YouTube):** a native Innertube poller (no new dependency) over `chat-downloader`,
because the product ships as a single binary and broadcast chat is a non-critical
convenience panel that degrades gracefully (fragile vs. YouTube changes → empty, never
crashes the relay); the fetch sits behind a seam so `chat-downloader` could be slotted
in later. Pure parsers (Innertube bootstrap / `get_live_chat` / `runs→text`, the
`Channel` CSV, `live_set_diff`, the URL builders, and the Twitch `twitch_login` /
`parse_twitch_privmsg`) live in `src/scripts/broadcast_chat.py`; the network + threads
are in the relay. Front-end: a read-only "Broadcast chat" card in the three pages, polled
via the `RC_API` shim (tailnet + Funnel), rendered with `textContent` + a per-message
timestamp + a source badge on a handover overlap (no front-end change was needed for
Twitch — it flows through the same store/endpoint/card). Tests:
`tests/test_broadcast_chat.py` (pure parsers + store + endpoint). Live diagnostic
(maintainer, not shipped): `tools/broadcast-chat-probe.py <channel>` resolves + tails a
live channel's chat standalone (YouTube via yt-dlp+Innertube, or `--twitch` / a
`twitch.tv` URL via anonymous IRC) — the way to validate the real path against a live
stream.

The relay also provides a **director→commentator text-cue channel** (an IFB-lite, text-only
stand-in for an earpiece): the Director Panel's **Cues** section lets a director pick a
target (a specific commentator, **All commentators**, or **On air** — resolved server-side at
send time), choose a level, and send a short cue. **`Info`** cues auto-expire after 30 s
and appear as a brief toast in the commentator's cockpit; **`Critical`** cues are sticky banners
the commentator must **Acknowledge** — after which the director sees a **✓ seen** stamp.
Quick-cue **presets** come from a `Cue Preset` column in the Sheet's **Configuration** tab
(same admin-managed vocabulary model as Race Control); free text is always available and
is the only option when the Configuration tab is unreachable. Endpoints `POST /cues/send`,
`GET /cues/data`, `GET /cues/presets`, `GET /cues/reload` are **director**-gated; commentator
endpoints `GET /cockpit/cues` + `POST /cockpit/cues/ack` are identity-scoped to the
token's own commentator. All are reachable via Funnel only through the existing `/console`
mount — no new public surface. Persisted to `runtime/<profile>/cues.json`; producer
takeover (tailnet + `--funnel`) pulls A's still-active cues via `/console/takeover/cues`.
Pure logic: `src/scripts/cue_admin.py`. Tests: `tests/test_cues.py`.

The relay also serves an optional **on-air program-audio monitor**: the on-air
feed's audio, encoded to an endless MP3 stream and offered as a toggle next to the
silent program still on the Director Panel, Commentator Cockpit, and Race Control
desk. Endpoints `GET /preview/program-audio` (director; ANY) and
`GET /console/cockpit/program-audio` (cockpit + race-control; ANY, funnelled under
the existing `/console` mount — no new public surface). One on-demand ffmpeg
(`libmp3lame`, codec parameterized via `PROGRAM_AUDIO_*` constants) taps the feed
fan-out ring and is re-served to many listeners from one output `FeedRing`
(`ProgramAudioService`, reference-counted + idle-reaped — zero cost when nobody
listens); it follows the on-air feed across handovers by restarting on the new
feed's ring (MP3 frames splice, brief silence gap). Requires fan-out (endpoints
404 otherwise; the front-end card self-hides). Default ON; kill-switch
`RACECAST_PROGRAM_AUDIO=0`. NOT the full OBS program mix — feed-audio only (see
`docs/superpowers/specs/2026-07-02-program-audio-monitor-design.md`). Tests:
`tests/test_program_audio.py`.

The relay also serves a **commentator-facing Commentator Cockpit** (issue #191) under an
auth-gated `/cockpit/*` namespace: a live program monitor (reusing
`get_program_screenshot`), an "ON AIR / UP NEXT" tally (`cockpit_tally`, derived from the
on-air feed + the live schedule via `asset_key`-normalised streamer names), the embedded
crew chat (identity forced to the token's streamer), and a read-only timer. A read-only **stint plan** (right column, below the timer) lists the full running order (stint label + streamer name) from a redacted `schedule` field on `/cockpit/data` — no stream URLs (the same Funnel redaction boundary as `/console/takeover/status`); the on-air stint and the viewer's own stints are highlighted (pure `cockpit_schedule`). It is exposed
**publicly via Tailscale Funnel**, which maps **only** the `/console` path prefix to
`127.0.0.1:8088` — the rest of the relay stays tailnet/loopback-only and is **never**
funnelled (the security boundary). `/console/buttons` reverse-proxies (HTTP + a raw-WebSocket
passthrough for Companion's tRPC `/trpc`) to the resolved local Companion bind address,
director-gated (#236); it is a sub-path of the single `/console` mount (no second mount);
OBS-WebSocket remains never funnelled. Funnel passes no Tailscale identity, so auth is 100%
server-side: a per-person token `<streamer_key>.<version>.<sig>` signed with the
**per-league** `CONSOLE_SECRET` (`profiles/<name>/profile.env`, travels with `profile
export`); revocation bumps a streamer's version in
`runtime/<profile>/console-versions.json`.
The cockpit is **zero-config**: the secret is **auto-provisioned** by the CLI on first relay
start (`_ensure_active_cockpit_secret` in `src/racecast.py`, idempotent, never the shipped
`example` profile), so `/cockpit/*` is live **whenever a secret exists** — there is no
separate enable flag. When the secret is absent every `/cockpit/*` path 404s (like chat/timer
when disabled). PUBLIC exposure is the **independent Funnel switch** (`racecast funnel on`),
which mounts **only** `/console` — the only way `/console` leaves the tailnet. The token
rides in the `…/console?t=` link once, then an `HttpOnly; Secure; SameSite=Lax`
`rc_console` cookie. **Discord OAuth second front door:** when `DISCORD_CLIENT_ID` +
`DISCORD_CLIENT_SECRET` are set in `profile.env`, the relay also serves
`/console/login` + `/console/oauth/callback` (scope `identify`); a session-bound
`rc_oauth_state` cookie guards CSRF; on a Crew-tab Discord-handle match the relay mints
the same `rc_console` token. The Crew tab gained `Commentator` and `Discord` columns;
`resolve_roles` is an A1 union (Schedule OR Crew Commentator flag). Auth core:
`src/scripts/console_auth.py`; revocation store: `src/scripts/console_admin.py`;
commentator page: `src/cockpit/cockpit.html`; CLI: `racecast console …`; takeover pulls A's
versions over the tailnet (like `chat pull`). Tests: `tests/test_cockpit.py`. The crew
roster (Crew tab ∪ live Schedule) is exposed via a tailnet-only `GET /crew/data` endpoint
(root path, **never** funnelled — only `/console` is mounted); `racecast links` unions Crew
∪ Schedule to produce role-adaptive `/console` links for every person. The Control Center
cockpit view is now called **"Crew Console"**.

**Producer takeover over Funnel (`/console/takeover/*`, issue #216 Phase 7).** When
producer B is not on the tailnet, `racecast event takeover <A-magicdns-host> --funnel`
pulls the handover state over A's public Funnel. Three read-only endpoints live under
`/console/takeover/` (all reachable via Funnel, **never** adding to the public surface
beyond the existing `/console` mount):
- `GET /console/takeover/status` — **redacted** status: only `live`, `league`,
  `event_title`, `timer`, and `mode`. Feed stream URLs are stripped — they never leave
  the tailnet. This is an allowlist, not a blocklist.
- `GET /console/takeover/chat` — the full chat history (same payload as `/chat/data`).
- `GET /console/takeover/versions` — the console-versions revocation map (same payload
  as `/cockpit/versions`).

All three require the **step-up** `X-Console-Secret` header (legacy name `X-Cockpit-Secret`
still accepted for one release) carrying the shared per-league `CONSOLE_SECRET` (producer-level
auth — the same secret that signs commentator tokens). A
wrong secret returns HTTP 403; the client aborts loudly. A network failure falls back to the
local `--stint N` bringup. On success, B's relay is brought up via the normal `event start`
path with the adopted stint/league/title/mode, and chat + versions are applied locally
(`ca.apply_pulled` / `cpadm.apply_pulled`, same as the tailnet pull path). The tailnet path
(`racecast event takeover <100.x-ip>`) is unchanged and does not use the step-up header.
The security boundary is preserved: only `/console` is Funnel-mounted (with `/console/buttons`
as a director-gated relay-proxy sub-path — no second mount); no takeover endpoint is reachable
without the step-up secret; feed URLs stay tailnet-only; OBS-WebSocket is never funnelled. CLI helper:
`_funnel_takeover_base(host)` + `_takeover_get(url, secret, timeout)`; plan:
`docs/superpowers/plans/2026-06-19-console-roles-phase7-takeover-funnel.md`.

**Commentator stream-link submission (issue #193).** A write-scoped add-on: a commentator
submits a YouTube/Twitch URL for one of *their own* stints from the cockpit
(`POST /cockpit/submit`, the only write reachable over Funnel) — token-auth + per-identity
rate limit + `is_channel()` SSRF guard + server-side **own-rows-only** check
(`own_submission_target`, `asset_key(streamer) == token's streamer_key`). It is stored
**pending** (never auto-published) in `runtime/<profile>/cockpit-pending.json` and pings
Discord (`cockpit_submission_payload`, no-op without a webhook). The director's
**list/approve/reject** live under a separate `/submissions/*` namespace that is **NOT**
funnelled (tailnet-only, reached from `/panel`); approve calls the existing
`SetupControl.schedule_set` (writes the Sheet; applies on the next `/reload`). Pure store +
audit log: `src/scripts/cockpit_submissions.py` (mirrors the `chat_admin.py` /
`console_admin.py` pure-store pattern); thin
thread-safe wrapper `SubmissionStore` + endpoints in the relay; panel section + cockpit
form in the two HTML files. Tests: `tests/test_submissions.py`.

**Role-adaptive /console pages (issue #216).** The relay also serves a `/console` launcher plus `/console/cockpit` and `/console/panel` pages, all role-gated behind the Phase 3a `/console` auth gate; page API calls resolve under the mount via an injected `window.RC_API_BASE` shim. Launcher, cockpit, and panel are in `src/console/console.html`, served with the authenticated subject's role-conditional links; `/console/whoami` returns the authenticated subject. Authorization is per-role (any authenticated subject reaches `/console` + `/console/cockpit`; directors reach `/console/panel`). Tests: `tests/test_console.py` + `tests/test_console_gate.py`.

**Race Control monitoring desk (issue #244).** A **fourth crew role**, `race_control` — a *read-only* monitoring desk: live **program preview** (reusing the cockpit's `get_program_screenshot`), the **redacted streamer/stint schedule** (no stream URLs — the `/console/takeover/status` redaction boundary), the **race timer**, and **crew chat** (identity forced from the token). It triggers **no broadcast actions**. NB: the role string is `race_control` (Crew tab); it shares its label with the director-only HUD `racecontrol` banner (Setup tab) but they never collide in code — that banner stays director-only and this role never writes to it. Roles are additive (a person can be e.g. both director and race_control). Capability `RACE_CONTROL` in `console_policy.py`; the Crew tab gained a **Race Control** column (`CREW_RACE_CONTROL_HEADERS`, header-located like Commentator); `CrewSource`'s canonical row is now a 6-tuple `(name, dir, prod, commentator, race_control, discord)` with a `race_control_keys()` helper; `resolve_roles` gains a `crew_race_control_keys` union. The relay serves `GET /console/race-control` → `src/racecontrol/race-control.html` and `GET /console/race-control/data` (the only new endpoint: `{schedule, event_title, mode, on_air}`, built by the pure `race_control_schedule()`), both gated `Requirement(RACE_CONTROL, False)`. The desk's program/timer/chat **reuse the existing `ANY` cockpit endpoints** (no new public surface). The launcher shows a **Race Control** card when whoami roles include `race_control`. Crew editor: a 6th "Race Control" checkbox round-trips through `/api/crew`. Tests: `tests/test_roles.py`, `tests/test_console.py`, `tests/test_console_gate.py`, `tests/test_cockpit.py`, `tests/test_ui_server.py`.

**Health Monitor.** The relay serves `/health-monitor` (tailnet/loopback) and `/console/health-monitor` (Funnel, any authenticated `/console` subject) — a dashboard of relay health over time backed by a per-profile SQLite store at `runtime/<profile>/health-history.db`, sampled in the relay heartbeat; uPlot (MIT) is vendored at `src/assets/vendor/uplot/` (the first deliberately vendored JS in the repo — see the spec). CLI: `racecast health export|import|pull`.

**Relay-mediated OBS control (Director Panel).** The Director Panel's scene switches, visibility toggles, and audio controls go through the relay — not a direct browser→OBS-WebSocket connection. Six director-gated endpoints (all checked via `console_policy` before dispatch): `POST /obs/scene` (switch scene), `POST /obs/source` (show/hide a source), `POST /obs/audio` (set input volume/mute), `POST /obs/stream` (start/stop the OBS stream output), `POST /obs/state` (batch read of current scene + source visibility + audio levels), `POST /obs/refresh` (reload the relay-served OBS browser sources — the programmatic right-click → Refresh; best-effort; unconditional force). Beside those, `GET`/`POST /obs/split` sets the Splitscreen from the on-air feed (both feeds visible, on-air audio live, the rest muted; pure resolver `obs_ws.split_state_intents`) for the Companion `SPLIT` button and the panel macro, and `/obs/split-audio` is its audio-only predecessor, kept for older boards. `GET /obs/stint/<A|B>` / `POST /obs/stint` does the same for a hand-picked STINT A/B (visibility, audio, the commentary mic of a local stint) without touching the relay's on-air state; the scene cut stays with the caller for both. The relay calls `src/scripts/obs_ws.py` on the producer's machine, where the OBS-WebSocket password is auto-discovered from OBS's own config (overridable via `RACECAST_OBS_WS_PASSWORD` in `.env`); the password never crosses the network and OBS-WebSocket is **never** funnelled. The Director Panel therefore needs **no OBS IP, port, or password** from the director — the panel works fully over Funnel (`/console/panel`) using only the per-person token. The program monitor was already relay-mediated (`GET /preview/program`, any-auth, console-allowed) and is unchanged. All six OBS helpers follow the same best-effort contract as `get_program_screenshot` — they never raise. For the five control/read endpoints an unreachable OBS (`obs_ws._connect()` returning `None`) maps to a `503` with a descriptive note; `POST /obs/refresh` is best-effort fire-and-forget and instead returns `count: 0` with the note (a `503` only when obs-websocket support is absent entirely). Tests: `tests/test_obsws.py`.

### Unified `racecast` CLI (`src/racecast.py`)
`src/racecast.py` is the single shipped entrypoint for operators. It resolves the
active profile (via `src/scripts/config.py`) and injects its league values into the
environment before dispatching to:
- **`src/scripts/services.py`** — daemon helper for the relay and static-streams: spawns
  subprocesses, writes PID + log files under `runtime/`, and provides start/stop/restart/
  status/logs for both. `racecast relay run` is the foreground/debug mode (no daemon).
- **Companion adapter** (over `src/scripts/companion_common.py`) — `racecast companion
  start/stop/restart/status/logs` wraps the Companion bind logic (Windows + macOS
  automated; native Linux companion-pi systemd service controlled; other Linux setups
  — WSL/Docker/manual AppImage — are manual).
- **One-shot wrappers** — `racecast preflight`, `racecast cookies`, `racecast graphics`,
  `racecast media`, `racecast setup` delegate to the corresponding `src/` modules
  without needing to remember individual script paths.
- **`racecast profile list|show|use|new [--from]`** + global `--profile NAME` — manage
  league profiles (logic in `src/scripts/profile_admin.py` + `config.py`); `use` writes
  the `runtime/active-profile` pointer.
- **`racecast status`** — aggregate health of relay + companion + streams at a glance.
- **`src/scripts/obs_ws.py`** — minimal obs-websocket v5 client (stdlib only). After
  `relay stop`/`streams stop` kill the feeds, `_release_obs_feeds()` re-applies the
  feed media inputs' own settings via `SetInputSettings` — the one request that makes
  OBS rebuild the ffmpeg source and close its socket (media STOP/RESTART actions are
  ignored for inactive sources). Without it OBS pins the feed ports in FIN_WAIT_1
  until it restarts and preflight warns "port in use". Must run AFTER the kill (a
  rebuild against a live relay would just reconnect). Password auto-discovered from
  OBS's obs-websocket config.json (`RACECAST_OBS_WS_PASSWORD` in `.env` overrides). Fully
  best-effort: any failure prints one notice and the stop continues.
  It also exposes a scene-collection check/switch (`GetSceneCollectionList` / `SetCurrentSceneCollection`): `racecast obs collection [set]`, a warning during `racecast event start`, a line in `racecast event status`, and the Control Center's OBS row. `racecast event start` auto-switches OBS to the active profile's collection by default (`RACECAST_OBS_COLLECTION_SWITCH=0` restores the old warn-only behaviour) — safe because OBS refuses a switch while an output is active and none is active during bring-up; `event takeover` inherits it. The switch runs before the page-refresh hook (it rebuilds every source). The manual `racecast obs collection set` and the Control Center OBS-row button remain the explicit fallback. `racecast event start` also parks OBS on the **Standby** scene after the collection check and the forced page-refresh (Director Guide: start on Standby, then Start Streaming), default-on (`RACECAST_OBS_STANDBY_ON_START=0` opts out); it is best-effort and **never cuts a live program** — the switch is skipped when OBS output is already active (`obs_ws.switch_to_scene_if_idle`), which also makes a mid-event `event takeover` onto a streaming OBS a no-op. The canonical product name is `EXPECTED_SCENE_COLLECTION` (`GT Racing Endurance`), which mirrors the `name` field of `src/obs/GT_Racing_Endurance.json`; a localized per-league collection defaults to `GT Racing Endurance — <league>` (`PRODUCT_COLLECTION_PREFIX` + the profile name, unless the profile sets `OBS_COLLECTION`), so several leagues keep separate collections in one OBS. `racecast obs collection set` switches to the active profile's expected name.

### Subsystems documented next to their code
These sections moved out of this always-loaded file into nested `CLAUDE.md` files that
load only when you work in the matching directory — read them before changing that area:
- **Control Center** (`racecast ui`, profile/settings views, overlay builder, crew editor,
  font library) → `src/ui/CLAUDE.md`
- **Standalone binary + release pipeline** (PyInstaller, frozen-mode behaviour, the
  release/preview workflows) and the **e2e regression harness** (`tools/e2e.py`) →
  `tools/CLAUDE.md`
- **Static/public-stream mode** (`loopstream.py`, `start-streams.py`) and the **Companion
  remote-access helpers** (`companion_common.py`, `companion_linux.py`) →
  `src/scripts/CLAUDE.md`

## Docs

- `README.md` — operator quickstart (the commands above).
- `src/docs/` — shipped operator material (`README_SETUP.md`,
  `Broadcast_Setup_Guide.md`, printable `cheat_sheets.html`).
- `src/docs/wiki/` — canonical source for the **GitHub wiki** (split-up onboarding
  pages + Mermaid architecture diagrams). The wiki is generated, never hand-edited on
  GitHub: edit these pages, then `python3 tools/sync-wiki.py` mirrors them to the
  `<origin>.wiki.git` repo (clones into `runtime/wiki/`, commits, pushes). First push
  per repo needs a one-time bootstrap — create+save any page via the GitHub Wiki UI so
  GitHub creates the wiki repo. See `src/docs/wiki/Maintaining-this-Wiki.md`.
- `docs/superpowers/{specs,plans}/` — design specs and implementation plans for
  features (POV PiP, repo structure, preflight). Read the matching spec before
  extending one of those features.

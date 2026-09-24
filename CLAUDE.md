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

- **Edit only under `src/`.** `dist/` and `runtime/` are generated and gitignored,
  never hand-edit them. `tools/` are maintainer scripts (not shipped).
- **All scripts and docs must be English only.** (Chat with the user is German; the
  code/docs are read by an international team.)
- **Never hardcode secrets or machine paths.** Secrets come from `.env` (see below).
  The OBS collection and scripts are deliberately path/secret-free in git.
- Tooling is Python-only by design: do not reintroduce `.sh`/`.bat` (the build
  fails if any are shipped).
- **Outbound HTTP goes through `src/scripts/http_util.py`** (the covered side:
  `racecast.py`, `ui_server.py`, `src/scripts/*`). It always sends a racecast
  `User-Agent`; Discord and other Cloudflare-fronted hosts (Google Fonts, some
  vendor endpoints) 403 the default `Python-urllib/x.y` UA, so a bare `urllib`
  call silently fails. `tests/test_http_util.py` enforces this, it fails if a
  covered file uses `urlopen`/`urllib.request` directly. Exceptions, each
  already setting its own UA: the self-contained relay/`get-*`/`setup-assets`
  scripts (deliberately dependency-light, must not import shared modules) and
  `src/scripts/update.py` (needs its own HTTPS-only redirect opener + test seam).
- **Removing/renaming a CLI flag? Grep the whole repo: including `tools/` and
  `.github/`.** Those callers run only in the build/release pipeline, not in the
  test suite (a stale `--timer-url` in the binary smoke test broke every v1.0.0
  release build). CI's `binary-smoke` job now exercises the binary path on every
  PR, but grepping first is cheaper than a red pipeline.
- **Never invent domain rules or crew conventions.** Docs describe the
  *mechanism* (what a button/endpoint does); broadcast procedure (who goes on
  air when, with which feed) is defined by the team, not derived. State
  assumptions explicitly and ask when uncertain instead of asserting.
- **Tests must run on any machine and in CI**, no real IPs, no machine paths,
  no environment-specific values; use fixtures/parameters (Tailscale IPs are
  `100.64.0.0/10` test constants, never this machine's address). Prefer TDD:
  failing test first, then the fix.
- **Cross-platform paths: the test matrix includes Windows.** A helper that
  assembles a *fixed-OS* absolute path: e.g. a macOS `/Applications/<App>.app`
  bundle: must build it with explicit forward slashes (`bundle +
  "/Contents/Info.plist"`), NOT `os.path.join`. `os.path.join` injects
  backslashes on the Windows runner, so a unit test that exercises that helper
  (with a POSIX path pinned in the fixture) passes on macOS/Linux but fails on
  `windows-latest`: even though production only ever runs the helper on its own
  OS. Use `os.path.join` only for paths on the *current* machine; never run it on
  a path you already know belongs to a different OS. (Broke #97's Windows CI.)
- **Console text encoding: the producer host is German Windows** (`cp1252` locale;
  plain ASCII under `LANG=C`). Keep argparse `help=` strings ASCII (`->`, not `→`): one
  non-ASCII character kills `--help` with `UnicodeEncodeError`. Every text-mode
  `subprocess` call under `src/` must pass `errors="replace"`: a decode error in
  subprocess's reader thread never reaches your `except`. Call `logsetup.harden_stdio()`
  first in any new entrypoint's `main()`. The guard tests in `tests/test_logs.py` scan all
  of `src/`: **do not narrow their scope.** Background: `src/scripts/CLAUDE.md`.
- **Pipeline/permission problems (release-please, tokens, branch protection,
  Actions) are research-first:** map the complete lifecycle and requirement set
  (docs + known issues) before changing anything, one planned fix, not
  symptom-per-loop trial and error.
- **Changed a UI surface? Refresh its wiki screenshot in the SAME change**, never as a
  follow-up. Control Center views (`src/ui/`) → `src/docs/wiki/images/cc-<view>.png`;
  Director Panel (`/panel`) → `director-panel.png`; Companion pages →
  `companion-page<N>-*.png`. Recapture with the `wiki-screenshots` skill (Control Center,
  Director Panel, `/console` pages) or `companion-screenshots` (buttons), verify a
  published wiki with `wiki-visual-test`. Capture `cc-*.png` only from a local dev build
  (`racecast ui` from `src/`, no `VERSION` stamped) so every shot shows the same "dev
  build" badge. Full rule: `src/ui/CLAUDE.md`.

## Commands

```bash
# Tests (stdlib only: each file under tests/ is a runnable script, no pytest).
# `ls tests/` lists them; each file's name says what it covers.
python3 tests/test_pov.py            # e.g. one file: relay POV/schedule unit checks
python3 tools/run-tests.py           # the whole suite (exactly what CI runs)
python3 tools/lint.py                # ruff lint (= the CI lint job); --fix auto-corrects.
                                     # Rules mirror the CodeQL alert classes: see ruff.toml.
                                     # Run it after changing any Python file.
# Run ONE test function:
python3 -c "import sys; sys.path.insert(0,'tests'); import test_pov as t; t.t_pov_format_constant()"

# Build the distributable (assembles + self-verifies dist/)
python3 tools/build.py               # -> dist/GT_Racecast_Package/ + .zip
# Standalone binary (maintainer; CI builds all three OSes on tags v*)
python3 tools/build-binary.py        # -> dist/bin/racecast + dist/bin/racecast-ui (+ smoke test)

# End-to-end / regression harness (maintainer; stands up relay + Control Center, asserts the live HTTP surface)
python3 tools/e2e.py                  # synthetic mode: self-contained, no real Sheet/cookies/OBS, the CI `e2e` job runs this
python3 tools/e2e.py --help           # more modes (real league, Playwright): tools/CLAUDE.md

# Unified operator CLI (the producer's main entrypoint), the canonical, always-current
# command list is the CLI's own help; read it instead of duplicating it here:
python3 src/racecast.py --help
python3 src/racecast.py <group> --help    # e.g. relay | event | profile | console | obs

# Non-obvious bits that `--help` does NOT tell you (full text: src/scripts/CLAUDE.md):
# - cookies: Firefox is recommended; Windows Chrome/Edge exports are blocked by
#   app-bound encryption. `cookies twitch <browser>` only for gated Twitch feeds.
# - freeport: refuses while a relay/streams runs (it would cut a live feed) unless --force.
# - console: CONSOLE_SECRET is auto-provisioned on first relay start; no enable command.
# - --profile NAME runs ONE command against a non-active profile.
# Maintainer probes (flags, broadcast chat, GT7 telemetry) and wiki sync: tools/CLAUDE.md.
```

After changing the relay, run `python3 tests/test_pov.py`; after any change that
ships, run `python3 tools/build.py`: its verify step is the closest thing to CI
(checks tokenization, blanked password, no secrets, preflight present, no shell
scripts).

## Architecture

### Single-source + build
`src/` is the only source of truth. `tools/build.py` copies it into
`dist/GT_Racecast_Package/` (the artifact handed to other producers), stripping
the Companion password and renaming the tokenized OBS collection to
`GT_Racing_Endurance.template.json`. `runtime/` holds machine-local state (cookies, logs,
caches, the localized OBS import) and is gitignored. Helpers detect whether they run
from the repo (`src/...`) or the distributed package and pick paths accordingly,
see `default_runtime_dir()` (relay/get-cookies) and `state_dir()` (scripts).

### Profiles + config (`src/scripts/config.py`), the multi-profile model
Config comes from **two layers** and one resolver:
- **Machine `.env`** (gitignored; template `.env.example`) holds ONLY machine-local
  knobs, never league secrets (`RACECAST_OBS_WS_PASSWORD`, `RACECAST_COMPANION_EXE`,
  `RACECAST_UI_PORT`, `RACECAST_PROFILE`, …).
- **`profiles/<name>/profile.env`** is the **league**: un-prefixed keys (`NAME`,
  `SHEET_ID`, `SHEET_PUSH_URL`, `INTRO_URL`/`OUTRO_URL`/`TRAILER_URL`, `LOGO`,
  `OBS_COLLECTION`, `CONSOLE_SECRET`, optional `DISCORD_CLIENT_ID`/`DISCORD_CLIENT_SECRET`).
  The shipped `profiles/example/` is a template, excluded from the usable-league list.

Active profile precedence: `--profile` > `RACECAST_PROFILE` > `runtime/active-profile` >
the sole profile. The CLI **injects** the league's values into child processes as
**prefixed** env vars (`RACECAST_SHEET_ID`, …), so the relay and the asset downloaders
stay profile-agnostic. A bounded `load_dotenv()` is **duplicated** in five standalone
scripts that must not import `config.py`: keep the copies in sync. `profile
export`/`import` (handing a league to another machine) is distinct from `backup`
(look snapshots that never leave the machine). Details: `src/scripts/CLAUDE.md`.

### Profile-scoped runtime
Per-league machine state lives under **`runtime/<profile>/`**: the localized OBS
import (`GT_Racing_Endurance.import.json`), downloaded `graphics/` and `media/`, etc. Shared
machine state (cookies jar, the active-profile pointer) stays at `runtime/` top level.
So `racecast graphics` / `media` / `setup` always write into the active profile's
runtime dir; switching profiles points the CLI at a different one.

The **relay is a machine singleton** (it binds the shared control port 8088 + feed
ports, so only one can run), so its **PID file `runtime/relay.pid` lives at the
top level**, not per-profile: with a sidecar `runtime/relay.profile` recording which
profile it was started under. That way `relay stop`/`status`/`logs` find and act on the
one running relay regardless of the active profile, and the relay's per-profile **logs**
still resolve to the profile it actually runs under (`_running_relay_dir()`). A
per-profile relay PID used to let a `profile use` while the relay ran orphan it on 8088
(#273). `profile use` now refuses to switch while a relay/streams is running unless
`--force`, and `relay start` reports a foreign holder of 8088 (recover with `racecast
freeport 8088`).

### Two token round-trips (keep paths/secrets out of git)
- **OBS.** `src/obs/GT_Racing_Endurance.json` stores tokens (`__RACECAST_GRAPHICS__`,
  `__RACECAST_MEDIA__`), never real paths. `src/setup-assets.py` localizes it into
  `runtime/<profile>/GT_Racing_Endurance.import.json` (absolute paths: do not move it after
  import). After editing scenes in OBS, re-export and fold back with
  `tools/tokenize-obs.py exported.json src/obs/GT_Racing_Endurance.json`. OBS browser
  sources cache aggressively: `relay start`/`event start` refresh them through a hash
  gate, `racecast obs refresh` forces it. Anything that must survive a reload lives
  server-side, never in page JS.
- **Broadcast graphics, clips and brand overrides are pure-runtime**, never committed:
  downloaded from the Sheet into `runtime/<profile>/`. `src/assets/` holds only the HUD
  `flags/` and the base `brands/`.
- **Companion.** Export into the gitignored `incoming/`, then
  `tools/strip_companion_pass.py` blanks the WebSocket password and writes
  `src/companion/racecast-buttons.companionconfig`; `build.py` re-strips defensively.
- **Per-league overlay** CSS/fonts under `profiles/<name>/overlay/`, edited with the
  Control Center's visual overlay builder.

Details: OBS tokens, page refresh, POV-box sync and graphics → `src/obs/CLAUDE.md`; the
overlay override and the builder → `src/ui/CLAUDE.md`.

### The relay (`src/relay/racecast-feeds.py`), the heart
The relay pulls one commentator stream per stint and serves it to OBS: **Feed A**
(port 53001) plays odd stints, **Feed B** (53002) even stints, and at each handover the
off-air feed advances to the next stint, so OBS media sources never change URL. An
optional **POV** feed (53003) is a driver picture-in-picture. The schedule is a
Google-Sheet tab read as CSV; a running feed is never torn off mid-stint. The same
process serves the HUD/splitscreen overlay pages, the Director Panel, crew chat, cues,
the Commentator Cockpit and the `/console` pages.

**The mechanisms live in `src/relay/CLAUDE.md`** (feed fan-out and backlog shedding,
qualifying mode, the YouTube/Twitch/local pull pipeline, HUD, chats, cues, program
audio, cockpit and console auth, takeover, race control, health monitor, relay-mediated
OBS control). Read it before changing the relay or anything it serves, even from a file
outside `src/relay/`.

Security boundaries that apply everywhere:
- The control server on port `8088` is **unauthenticated** and `/status` reveals stream
  URLs. `--bind auto` binds `127.0.0.1` plus this machine's Tailscale IP, **never** the
  LAN (`0.0.0.0` only when passed explicitly). The tailnet is the trust boundary.
- Tailscale Funnel mounts **only** the `/console` path prefix (`/console/buttons` is a
  director-gated sub-path of it, not a second mount). Nothing else is ever funnelled,
  **OBS-WebSocket is never funnelled**, and feed stream URLs never leave the tailnet
  (the redacted takeover status is an allowlist, not a blocklist).
- Everything under `/console` is authenticated server-side: a per-person token signed
  with the league `CONSOLE_SECRET`, or Discord OAuth when configured. The takeover
  endpoints additionally require the step-up `X-Console-Secret` header.
- No destructive HTTP endpoint for chat or cues: clear/import/pull stay producer-only CLI
  actions. A commentator's stream-link submission is own-rows-only and lands as pending,
  never auto-published.

### Unified `racecast` CLI (`src/racecast.py`)
`src/racecast.py` is the single shipped entrypoint for operators: it resolves the active
profile, injects its league values and dispatches to the service helpers
(`src/scripts/services.py` for the relay/streams daemons, the Companion adapter, the
one-shot wrappers, profile admin). `src/scripts/obs_ws.py` is the stdlib obs-websocket
client behind the stop-time feed release, the scene-collection switch and Standby on
event start; it is best-effort and **never cuts a live program**. Details:
`src/scripts/CLAUDE.md`.

### Subsystems documented next to their code
These sections moved out of this always-loaded file into nested `CLAUDE.md` files that
load only when you work in the matching directory, read them before changing that area:
- **The relay** (feeds, fan-out, overlay pages, chats, cues, cockpit/console, takeover,
  race control, health monitor, OBS control) → `src/relay/CLAUDE.md`
- **OBS collection tokens, page refresh, POV-box sync, broadcast graphics** →
  `src/obs/CLAUDE.md`
- **Profiles/config details, the `racecast` CLI and `obs_ws`, console-encoding
  background** → `src/scripts/CLAUDE.md`
- **Control Center** (`racecast ui`, profile/settings views, overlay builder, crew editor,
  font library, the full wiki-screenshot rule) → `src/ui/CLAUDE.md`
- **Standalone binary + release pipeline** (PyInstaller, frozen-mode behaviour, the
  release/preview workflows) and the **e2e regression harness** (`tools/e2e.py`) →
  `tools/CLAUDE.md`
- **Static/public-stream mode** (`loopstream.py`, `start-streams.py`) and the **Companion
  remote-access helpers** (`companion_common.py`, `companion_linux.py`) →
  `src/scripts/CLAUDE.md`

## Docs

- `src/docs/wiki/`: canonical source for the **GitHub wiki** (split-up onboarding
  pages + Mermaid architecture diagrams). The wiki is generated, never hand-edited on
  GitHub: edit these pages, then `python3 tools/sync-wiki.py` mirrors them to the
  `<origin>.wiki.git` repo (clones into `runtime/wiki/`, commits, pushes). First push
  per repo needs a one-time bootstrap: create+save any page via the GitHub Wiki UI so
  GitHub creates the wiki repo. See `src/docs/wiki/Maintaining-this-Wiki.md`.
- `docs/superpowers/{specs,plans}/`: design specs and implementation plans for
  features (POV PiP, repo structure, preflight). Read the matching spec before
  extending one of those features.

# CLAUDE.md: `tools/` (maintainer scripts, not shipped)

Loaded when working under `tools/`. `tools/` is maintainer-only (build, tokenize,
sync) and is never shipped to producers.

## Standalone binary (PyInstaller)
`tools/build-binary.py` freezes `src/racecast.py` into the `racecast` executable and
`src/racecast_ui.py` into the windowed `racecast-ui` (Control Center launcher), one
pair per OS; the whole `src/` tree ships as bundled data under `sys._MEIPASS/src/`, so
here-relative path resolution keeps working. In frozen mode (`sys.frozen`), `racecast`
runs bundled scripts **in-process** (importlib + patched argv, string `sys.exit`
payloads go to stderr) and daemons re-invoke the binary itself (`racecast relay run`,
hidden `racecast streams run-feed`) with `PYINSTALLER_RESET_ENVIRONMENT=1` so each
child extracts its own bundle and outlives the parent. `runtime/`, `profiles/` and
`.env` live next to the binary: keep it in its own folder.
`services.py`/`companion_common.py` carry the per-OS process control (Windows: ctypes
PID probe: `os.kill(pid, 0)` would TERMINATE the target there, taskkill/tasklist,
Companion.exe discovery + `RACECAST_COMPANION_EXE` override in `.env`; native Linux:
companion-pi systemd service via `companion_linux.py`; other Linux setups. WSL/Docker/
manual AppImage: remain manual, matching the pre-existing guidance).
Releases: merge the standing **release-please** Release PR (or push a `v*` tag manually,
both work). `.github/workflows/release.yml` tests, builds, smoke-tests and uploads
`racecast-windows.zip` / `racecast-macos.tar.gz` / `racecast-linux.tar.gz` /
`racecast-linux-arm64.tar.gz` (each contains the `racecast` binary + `.env.example`;
on first run the frozen binary copies it to `.env`: see `ensure_env_file`). The two
Linux archives are built natively on the `ubuntu-latest` (x86-64) and
`ubuntu-24.04-arm` (ARM64) matrix runners; `update.asset_name()` picks the right one
per `platform.machine()` so a self-updating ARM64 binary never fetches the x86-64
archive. release-please tags via GITHUB_TOKEN, which cannot
trigger on-tag workflows, so `release-please.yml` dispatches `release.yml`
explicitly. `ci.yml` runs the suite on all
three OSes for every PR. Unsigned binaries: SmartScreen/Gatekeeper show a
one-time "run anyway" warning.

A separate **preview** channel (`.github/workflows/preview.yml`, helper
`tools/preview_meta.py`) publishes pre-release binaries for testing ahead of a
real release: triggered by the `preview` label on a PR or by `workflow_dispatch`
against a ref. Its tags are `preview-*` (never `v*`), so it never triggers
`release.yml` or release-please; `preview-cleanup.yml` deletes a PR's pre-release
on close.

## End-to-end / regression harness (`tools/e2e.py` + `tools/e2e_checks.py`)
The integration **outer loop** (issue #199): it stands up the relay + Control Center
from `src/` as owned subprocesses and asserts the **live HTTP surface**, the class of
bug the unit suite (pure functions) can't catch. Maintainer-only (`tools/`, not shipped).
`tools/e2e_checks.py` is the pure, import-testable assertion core (free-port,
synthetic-CSV builder, tolerant `http_request`, the `CheckResult`/`run_checks` registry,
the `check_*` callables, `SYNTHETIC_CHECKS`/`REAL_LEAGUE_CHECKS`), unit-tested in
`tests/test_e2e.py`; `tools/e2e.py` owns process lifecycle (spawn, readiness-poll,
guaranteed `finally` teardown, no leaked relays/UI even on failure). Two modes:
- **Synthetic** (`tools/e2e.py`, the default, **CI-runnable**): an ephemeral temp profile +
  an in-process CSV schedule server via `--sheet-csv-url`; spawns an enabled relay + a
  cockpit-disabled relay + the Control Center on free `127.0.0.1` ports; runs 10 checks.
  No real Sheet/cookies/OBS/Tailscale. Because the relay **hard-exits at startup without
  `yt-dlp`/`streamlink` on PATH** (`racecast-feeds.py`), synthetic mode writes **no-op
  stubs** for `yt-dlp`/`streamlink`/`ffmpeg`/`deno` into the temp dir and prepends them
  to the relay's PATH (the fake schedule URLs are never pulled). The dedicated **`e2e`
  CI job** (`.github/workflows/ci.yml`, ubuntu) runs exactly this; the matrix `test` job
  already runs `tests/test_e2e.py` via `run-tests.py`.
- **Real-league** (`--real-league NAME`, **local only, refuses under CI**): drives the
  copied real-league dev build (real Sheet/cookies/`CONSOLE_SECRET`), minting a token for
  a real streamer pulled live from `/schedule/data`. Runs a **non-mutating** subset
  (`REAL_LEAGUE_CHECKS`): it **excludes** `check_submission_pending` (a `POST
  /cockpit/submit` could ping the league's real Discord webhook) and
  `check_cockpit_404_when_disabled` (needs a second relay); `check_chat_round_trip` is
  included (the crew chat is relay-local).

The checks regression-guard the four #191 cockpit bugs (env-clobber via the real
`racecast._set_env_key`, timer `—`, double-"stint" tally, flat `/cockpit/data` shape)
and the #193 own-row submission. Optional **rendered checks** (`--playwright`) use the
Playwright **Python library** (not the MCP) and SKIP when unavailable (CI omits the
flag). Visual helpers, all local-only: `--headed`/`--slowmo` (visible browser),
`--keep` (leave the services up + print the live URLs incl. the cockpit token), and
`--shots DIR` (write a screenshot of each surface, a reproducible, MCP-free tour;
the Control Center shot shows the machine's Tailscale IP, so it is a **local artifact,
never committed**). The local real-league run (copy the deployed instance's
profile/runtime/cookies in, enable cockpit on the copy, set up a Playwright venv, run,
tear down) is captured in the **`racecast-e2e`** skill, which builds on the
**`racecast-local-uat`** skill's data copy-in. Spec/plan:
`docs/superpowers/{specs,plans}/2026-06-17-e2e-regression-harness*.md`.

## Prove a text-only change (`tools/ast-gate.py` + `tools/html-gate.py`)
Two maintainer gates for a pass that is supposed to touch comments and prose only.
Both compare the working tree against a base ref and are read-only.

`ast-gate.py <base-ref> [paths]` parses each Python file, drops docstrings and blanks
prose string constants, then requires the rest to be identical. A changed string
without whitespace is never prose, so a mangled flag, digest or dict key counts as a
logic change rather than a rewording. Strings are compared positionally, so two
swapped ones cannot cancel out, and a file that mentions `__doc__` also reports
docstring changes, because there the module docstring is CLI output.

`html-gate.py <base-ref> <files>` does the same for a page: it strips HTML, CSS and JS
comments and requires everything else to be byte-identical, then syntax-checks every
inline `<script>` with `node --check`. Its comment stripper knows string and regex
literals. Without that it desynchronises on a class like `/[&<>"']/g` and reports every
later comment as a change, which once nearly cost a correct batch.

Neither gate judges wording. Both list what changed so a human reads it.

## Broadcast-asset renderer (`tools/render-assets.py` + `tools/assets_kit.py`)

Renders a league's stills (Standby, Intermission, …) and intro/outro clips from
an **asset kit** in its profile. `--help` documents the invocation and
`profiles/example/assets-src/README.md` the kit contract; what neither can tell
you:

- **Frames must depend only on `t`.** The renderer sets time explicitly per
  frame (`window.renderAt(scene, t)`) instead of letting CSS animate. A kit that
  reaches for a CSS animation, `requestAnimationFrame` or `Date.now()` renders
  differently on every run and breaks `--probe`, which jumps straight to second
  24 without rendering the 23 before it.
- **A music cut is coupled to the animation.** `audio.start` in `kit.json` and
  the beat the scene is built around are one decision: if the track's first beat
  is at 0:24 and the logo reveal sits at 23.5 s, `start` must be 0.5. Change one
  and the other has to move, or picture and sound drift apart, this was live
  once (the cut started at 4 s and the hit landed 3.5 s early).
- **League kits never enter the repo.** `profiles/*` is gitignored bar the
  example/demo profiles, so a real kit (with its poster crops and league logos)
  stays machine-local and travels via `racecast profile export`. Licensed music
  is additionally gitignored; a missing track renders the scene silent instead
  of failing, so a kit still works for someone without the audio.
- Pure logic (manifest validation, filename guarding, ffmpeg argv, text merge)
  lives in `assets_kit.py` and is covered by `tests/test_assets_kit.py`; the
  browser/process layer stays in the script, the same split as
  `e2e_checks.py` vs `e2e.py`.

## Maintainer probes and scripts (moved from the root CLAUDE.md)
```bash
python3 tools/e2e.py --real-league NAME   # local-only: drive the copied real-league dev build (refuses under CI)
python3 tools/e2e.py --playwright [--headed] [--shots DIR]  # optional rendered checks / visible browser / MCP-free screenshot tour

# Fetch any missing HUD country flags from the sheet's Configuration tab
python3 tools/fetch-flags.py            # adds missing -> src/assets/flags/ (keeps old)

# Probe the broadcast-chat reader (#294) against a LIVE channel: standalone, no
# Sheet/relay/UI. YouTube: resolve the live videoId via yt-dlp + tail Innertube chat.
# Twitch: anonymous IRC (auto-detected for twitch.tv URLs, or --twitch for a bare name).
python3 tools/broadcast-chat-probe.py https://www.youtube.com/@SomeChannel  # --resolve-only / --cookies
python3 tools/broadcast-chat-probe.py https://www.twitch.tv/SomeChannel     # or: --twitch SomeChannel

# Probe GT7 UDP telemetry (#324) against a LIVE PS4/PS5: standalone, no relay/Sheet.
python3 tools/gt7-telemetry-probe.py --ps-ip 192.168.1.42   # heartbeat + decrypt + field dump

# Publish the GitHub wiki from src/docs/wiki/ (maintainer; --dry-run to preview)
python3 tools/sync-wiki.py
```

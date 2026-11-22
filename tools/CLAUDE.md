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
the Windows and macOS jobs also upload the driver sender as `racecast-sender-windows.exe`
and `racecast-sender-macos`, a separate stdlib-only one-file build with a `--help` smoke
that the invite page links by release tag, #1111;
on first run the frozen binary copies it to `.env`: see `ensure_env_file`). The two
Linux archives are built natively on the `ubuntu-latest` (x86-64) and
`ubuntu-24.04-arm` (ARM64) matrix runners; `update.asset_name()` picks the right one
per `platform.machine()` so a self-updating ARM64 binary never fetches the x86-64
archive. release-please tags via GITHUB_TOKEN, which cannot
trigger on-tag workflows, so `release-please.yml` dispatches `release.yml`
explicitly. `release.yml` fails at once on a ref that is not a `v*` tag, so a
dispatch from a branch cannot publish a release named after it. It creates the
GitHub release only when release-please has not already done so; any other create
failure stops the run before the builds. `ci.yml` runs the suite on all
three OSes for every PR. A PR whose diff touches only `CHANGELOG.md`, `version.txt` and
`.release-please-manifest.json` (the release-please PR) skips every `ci.yml` job but
`lint`: the `scope` job decides from the diff, never the branch name, which a fork can
choose. CodeQL always runs.
A skipped matrix job reports no check names, so `ci-ok` (`needs` every job, `if:
always()`, red on a failed or cancelled job) is the fixed-name result the ruleset requires.
`rerun-unacquired.py` ignores `ci-ok` when it judges a run, since it fails with the jobs
it needs. Under macOS arm64 capacity constraints GitHub cancels a job
after about 15 minutes with "The job was not acquired by Runner of type hosted even
after multiple attempts" (no runner, no step); `rerun-unacquired.yml` then reruns the
CI run through `tools/rerun-unacquired.py`, only when every failed job is of that kind
and at most twice, so a real failure stays red (#1080). Raising `timeout-minutes`
does not help: the test job was cancelled this way while it had no timeout at all.
The test job stops after 40 minutes, a release build after 90, and
`tools/run-tests.py` fails a test file that runs longer than 600 seconds and names it.
Unsigned binaries: SmartScreen/Gatekeeper show a one-time "run anyway" warning.

A separate **preview** channel (`.github/workflows/preview.yml`, helper
`tools/preview_meta.py`) publishes pre-release binaries for testing ahead of a
real release: triggered by the `preview` label on a PR or by `workflow_dispatch`
against a ref. Its tags are `preview-*` (never `v*`), so it never triggers
`release.yml` or release-please; `preview-cleanup.yml` deletes a PR's pre-release
on close. The jobs that run the PR's code (`meta`, `build`) hold a read-only token
and keep no credentials; `publish` runs only when every build succeeded and creates
the tag and pre-release from the run's artifacts without checking out the PR and refuses any tag outside `preview-*`, since
the tag name comes from PR-head code. `comment` only writes the PR comment, which also
links the driver sender assets.

## End-to-end / regression harness (`tools/e2e.py` + `tools/e2e_checks.py`)
The integration **outer loop** (issue #199): it stands up the relay + Control Center
from `src/` as owned subprocesses and asserts the **live HTTP surface**, the class of
bug the unit suite (pure functions) can't catch. Maintainer-only (`tools/`, not shipped).
`tools/e2e_checks.py` is the pure, import-testable assertion core (free-port,
synthetic-CSV builder, tolerant `http_request`, the `CheckResult`/`run_checks` registry,
the `check_*` callables, `SYNTHETIC_CHECKS`/`REAL_LEAGUE_CHECKS`), unit-tested in
`tests/test_e2e.py`; `tools/e2e.py` owns process lifecycle (spawn, readiness-poll,
guaranteed `finally` teardown, no leaked relays/UI even on failure). Three modes:

Local Linux runs automatically enter a single transient user service before
starting tools: two CPU cores, 512 tasks, 2 GiB RAM, fifteen minutes, and whole-cgroup
cleanup. Missing containment fails closed. Do not bypass it or run heavy local
verification in parallel. Each owned service also has an independent supervisor:
owner pipe closure or its finite lifetime stops its descendants, using owned
process groups or a Windows Job Object. `--keep` stays in the foreground, keeps
the synthetic CSV servers alive and always cleans up, including on failure.
Its default inspection lease is 300 seconds (`--keep-seconds`, at most600).
Ctrl+C ends inspection; never use broad `pkill` commands to stop test services.
- **Synthetic** (`tools/e2e.py`, the default, **CI-runnable**): an ephemeral temp profile +
  an in-process CSV schedule server via `--sheet-csv-url`; spawns an enabled relay + a
  cockpit-disabled relay + the Control Center on free `127.0.0.1` ports; runs `SYNTHETIC_CHECKS`.
  No real Sheet/cookies/OBS/Tailscale. Because the relay **hard-exits at startup without
  `yt-dlp`/`streamlink` on PATH** (`racecast-feeds.py`), synthetic mode writes **no-op
  stubs** for `yt-dlp`/`streamlink`/`ffmpeg`/`deno` into the temp dir and prepends them
  to the relay's PATH (the fake schedule URLs are never pulled). The dedicated **`e2e`
  CI job** (`.github/workflows/ci.yml`, ubuntu) runs exactly this; the matrix `test` job
  already runs `tests/test_e2e.py` via `run-tests.py`.
- **Solo coaching** (`--coach-only`, **CI-runnable on Linux, Windows and macOS**):
  copies the committed `solo-pov` profile into an owned temporary app home, clears inherited
  `RACECAST_*` configuration and uses a loopback OBS stand-in and synthetic replay.
  It uses no POSIX executable stubs. Signed HTTP exercises explicit Driver assignment,
  Start, replay exclusion, private owner history and stale-revision Stop through
  `tools/e2e_coach.py`. No audio/provider operation is enabled. `--build --coach-only`
  builds and drives the actual PyInstaller binary; `--binary --coach-only` uses the
  existing native binary. The `native-package` CI jobs build and run it on Windows
  and macOS; Linux's full source/frozen gates also run the coaching checks. It rejects
  real-league, keep and interactive browser modes before starting any services.
- **Real-league** (`--real-league NAME`, **local only, refuses under CI**): drives the
  copied real-league dev build (real Sheet/cookies/`CONSOLE_SECRET`), minting a token for
  a real streamer pulled live from `/schedule/data`. Runs a **non-mutating** subset
  (`REAL_LEAGUE_CHECKS`): it **excludes** `check_submission_pending` (a `POST
  /cockpit/submit` could ping the league's real Discord webhook) and
  `check_cockpit_404_when_disabled` (needs a second relay); `check_chat_round_trip` is
  included (the crew chat is relay-local).

The checks regression-guard the four #191 cockpit bugs (env-clobber via the real
`racecast._set_env_key`, timer `—`, double-"stint" tally, flat `/cockpit/data` shape)
and the #193 own-row submission. Optional **rendered checks** (`--playwright`: the
cockpit pills and the Director Panel's confirmed keyboard shortcut, #731; the shortcut
check presses NEXT, so `--real-league` leaves it out) use the
Playwright **Python library** (not the MCP) and SKIP when unavailable (CI omits the
flag). Visual helpers, all local-only: `--headed`/`--slowmo` (visible browser),
`--keep` (print live URLs and supervise the services in the foreground until the finite lease ends), and
`--shots DIR` (write a screenshot of each surface, a reproducible, MCP-free tour;
the Control Center shot shows the machine's Tailscale IP, so it is a **local artifact,
never committed**). The local real-league run (copy the deployed instance's
profile/runtime/cookies in, enable cockpit on the copy, set up a Playwright venv, run,
tear down) is captured in the **`racecast-e2e`** skill, which builds on the
**`racecast-local-uat`** skill's data copy-in. Spec/plan:
`docs/superpowers/{specs,plans}/2026-06-17-e2e-regression-harness*.md`.

**Visual acceptance run** (`--visual [--report DIR]`, #772): renders the 13 Control Center
views, the Director Panel, the cockpit and the Race Control desk (the two crew pages also at
390 px) against the synthetic run, which serves a Crew roster (`--crew-csv-url`) and runs
`tools/obs-sim.py`. The Telemetry view (`cc-telemetry`) exists only for a solo POV profile,
so a second Control Center serves it from its own app home in the temp dir: a copy of
`src/` or the binary, the committed `solo-pov` profile, a learned demo track and one indexed
`make-demo-recording.py` recording. Every other surface keeps the first one. `--keep`
prints the second Control Center's URL.
`tools/visual-probe.js` reads element facts in the page;
`tools/e2e_visual.py` applies six rules (page overflow, clipped text, a control left at the
browser default on a dark surface, overlapping controls, WCAG AA contrast, console errors)
and writes `DIR/report.html` plus screenshots. Any finding fails the run (exit 1); a missing
Playwright is exit 2, never a skip. Deliberate exceptions go in `tools/visual-allowlist.json`,
each with a reason; a real defect is allowlisted with reason "known defect, #N" pointing at
its own issue. `surface` and `selector` are full-match regexes; the optional `detail` is
searched, so anchor it with `^`/`$` when it must match exactly. The Control Center check
only sees what is visible inside its scrolling content area (its `<main>` scrolls; the
page itself does not). The CI `visual` job
runs it on every PR and uploads the report. Local setup:
`python3 -m venv runtime/pw-venv && runtime/pw-venv/bin/pip install playwright==1.63.0 &&
runtime/pw-venv/bin/python -m playwright install chromium`.

## Engineer recording replay (`tools/engineer-replay.py`)

Maintainer-only, Python 3.12+. It serves the production signed Engineer desk on a
free loopback port from temporary copies of a finalized recording and optional
labelled reference recordings. It starts no relay feeds, OBS or UDP listener.
Only Engineer analysis and temporary Crew chat routes are served; broadcast
control endpoints are unavailable.

```bash
python3 tools/engineer-replay.py recording.gt7rec --speed 1 --skip-minutes 5
python3 tools/engineer-replay.py recording.gt7rec --reference reference.gt7rec --reference-label "Morning practice"
python3 tools/engineer-replay.py --check
```

Open the printed signed desk URL. After playback completes the desk remains
available until Ctrl+C; packet receipt naturally becomes stale. Originals and
machine configuration remain untouched, and owned workers stop before temporary
copies are removed. Reference context sidecars are copied and explicitly granted
with the reference. Do not commit private captures or the printed access token.

Original packet timestamps drive analysis even at accelerated speed. Wall-clock
pacing uses the selected speed; skipped packets are pre-fed in bounded flushed
batches. The default reads valid files from `runtime/gt7/` and falls back to bundled
car/catalogue data. Use `--data-runtime DIR` for another existing runtime cache;
`--update-data` explicitly downloads into the temporary runtime only. Without
cached signatures, offline recognition may stay unknown, but measured geometry
and same-session comparison remain available.

`--check` uses real synthetic packets, a temporary learned circuit and signed HTTP.
It checks advancing snapshots, confirmed/offline-equal lap times and sectors,
plus available delta/A sectors on multiple growing provisional prefixes. The
CI e2e job runs it, and `tests/test_engineer_replay.py` covers pacing, large skipped
prefixes, private copies, route boundaries, updates and cancellation.

`tools/e2e-telemetry-replay.py` checks operator replay controls with real CLI jobs,
signed Engineer HTTP, on-air confirmation, import and the persisted HUD marker.
The visual CI job runs it. `--shots` refreshes the Control Center telemetry,
solo Director, replay Engineer and replay HUD wiki images from synthetic captures.
`tools/e2e.py` also starts a solo relay and checks that all replay consumers advance
without a replay-created recording. Source design: `src/docs/design/relay-telemetry-replay.md`.

For inspecting the real desk or capturing development screenshots, use the
printed URL with Playwright at 1440, 1024 and 390 px. The reproducible committed
wiki images continue to use synthetic data; see the wiki-screenshots skill.

## Driver telemetry probe (#1110)

Stage 1 of the driver dashboard probe measures what senders deliver. Start the relay
with `RACECAST_TELEMETRY_PROBE=1` (machine `.env`); `GET /telemetry/probe` on the
tailnet shows the key and, per sender, packets, loss, kinds, batch gaps and samples.

```bash
python3 src/relay/racecast-sender.py --url https://<host>/console/probe/ingest --key KEY --name "Mac"
python3 tools/gt7-sim-ps5.py REC.gt7rec --loop      # a simulated PS5 on UDP 33739, replays a recording
python3 tools/gt7-probe-load.py --url https://<host>/console/probe/ingest --key KEY \
    --senders 15 --duration 120 --status http://<tailnet-ip>:8088/telemetry/probe
```

The simulated PS5 answers heartbeats like the console (format per receiver, sends
from its own port, stops 16 seconds after the last heartbeat and takes a new format
only after such a lapse). The sender and the
simulated PS5 cannot share a host on the default ports: both need UDP 33740 or
33739; use `--bind`, `--port`, `--reply-port` and the sender's hidden
`--receive-port`/`--console-port` for a loopback run. The load tool posts through the
sender's own upload code; `--status` adds the relay's counts to its JSON report and
`--reset` clears the probe's counters first.

## Simulated OBS (`tools/event-sim/fakes/obs_sim.py`, #1229)

The event simulation's stand-in OBS (spec
`docs/superpowers/specs/2026-11-21-event-simulation-design.md`). `obs_state.py` holds the
state seeded from `src/obs/GT_Racing_Endurance.json` (or `--collection`): scenes, program
and preview, Cut and Fade, streaming and recording, inputs with settings, mute and
volume, scene item visibility and transforms (as in the collection), media cursors. It answers each request the way OBS does,
including OBS's error codes for a missing scene, item or input. OBS lists the loaded
collection under its own name and the demo profile's `OBS_COLLECTION`
(`--collection-name` adds more), so `event start` can switch to it. Each client gets the
events its `eventSubscriptions` asks for; `obs_ws` asks for none. `obs_render.py` draws
program screenshots at each item's canvas rect: per visible feed its newest frame, or
colour bars with the input name or its `/labels` text when there is none; a solid panel
per opaque item; the scene name on top. `program.feeds[].source` says which the last
screenshot used (`frame`, `synthetic`, or null for a hidden feed). `GetSourceScreenshot`
answers like OBS: `png`, `jpg` or `jpeg` (any other format is code 400). A JPEG of a
picture with a grabbed frame comes from ffmpeg, which works when frames do; otherwise
from a stdlib baseline encoder that writes one-colour 16x16 blocks without a DCT (a
synthetic 1920x1080 picture in well under a second). An unchanged picture reuses its
last JPEG. The picture is the canvas fitted inside `imageWidth`/`imageHeight` (8-4096, the
full 1920x1080 without either), at `imageCompressionQuality`. A fixed `--image` is sent
as it is, labelled with its own type. A screenshot is
planned under the state lock and drawn after it, so a feed read never holds up another
request.

`obs_grab.py` reads the frames only from the relay's feed URLs (http, loopback, ports
53001-53003 or `--feed-port`, path `/`). It makes the HTTP request itself, follows no
redirect and pipes the bytes into ffmpeg, which may only read its stdin (`pipe` is the
one allowed protocol, MPEG-TS demuxer forced) and is killed after 4 s. Each feed is read in the
background at most once per `--grab-interval` (5 s); a screenshot waits only for a
feed's first frame, up to 1 s. `--no-grab` turns the reads off, `ObsSim.stop()` kills
running ones. A read is a short-lived consumer on the relay's fan-out port: while it
runs, `consumer_attached` is true (a relay restart in that moment rebuilds the feed
input, here in the simulated OBS), and `consumer_backlog`, the backlog floor and
`consumer_starved` count it like OBS. Opaque by default are the full-page
graphics of `obs_ws.GRAPHIC_SOURCES` except the transparent Grid Rows, plus the feed
covers (`--opaque` replaces the list); `program.feeds` marks a feed such an item covers
as `occluded` and names it in `occluded_by`.

One loopback port carries the WebSocket and a JSON endpoint for checks: `GET /state`
(`scene` is the program scene), `/events?since=N`, `/requests?since=N` (state-changing
requests; `Get*` reads are only counted, under `reads` in the state), `/program.png`,
and `POST /request` (an operator acting in OBS), `/labels` (text a feed's pattern shows)
and `/freeze` (stop a feed's media cursor; `state` sets what OBS reports, PLAYING by
default as for a stalled stream). New input settings reopen the media and end a plain
freeze, including the relay's own `close_when_inactive` write; `"sticky": true` keeps it
through any rebuild until `"frozen": false`, for a source that stays stalled. As in
OBS, a transition reports `CurrentProgramSceneChanged` when it ends, and switching to
the scene already on air (or already faded to) starts nothing. The stream key never reaches the state or the
request log. A web page must not reach the port: a request needs `Host` 127.0.0.1 or
localhost with this port, no `Origin` (also on the WebSocket upgrade) and a POST
`application/json`. A handler failure answers code 400 instead of dropping the session,
and a WebSocket client that stops reading is closed once its queue fills. In-process use: `ObsSim(...).start()`, `.env()` gives
`RACECAST_SIM_OBS_WS`, `obs_sim.fetch(url, path)` reads the endpoint.

```bash
python3 tools/event-sim/fakes/obs_sim.py --port 0 --ready-file obs.json
```

`tools/obs-sim.py` is its lenient mode for screenshots: a fixed image and the old fixed
answers (no inputs, any scene item exists and is visible, every other request succeeds
without effect). `tools/e2e.py` and the
wiki-screenshots skill use it. `tests/test_obs_sim.py` drives both through the real
`obs_ws` helpers.

## Simulated Sheet and Discord webhook (`tools/event-sim/fakes/`, #1230)

`sheet_sim.py` serves seeded tabs the way gviz exports them (every cell quoted, trailing
blank rows and columns trimmed, `headers=1` changes nothing) under
`/spreadsheets/d/sim/gviz/tq`; a tab nobody seeded is an empty 200. Its
`/macros/s/sim/exec?key=sim` runs the v7 script of the Sheet-Webhook wiki page against
the same tabs: always HTTP 200, `{"ok":true,"action":...,"v":7}` or the script's own
`{"error": ...}` strings (`bad key`, `unknown action: x`, `row out of range: 9`, ...),
JavaScript coercion included (`"false"` is a true crew flag). The Overlay tab mirrors
the Setup tab like the league's formula: label in column B, value in C, `Team N` into
`Teams PN`; without a seeded Overlay it is built from the mirror labels. Script
Properties come from `stream_keys`; a `get_stream_key` (the relay's recovery probe too)
is only counted as `key_reads`, never logged with its key. Every other call lands in
`writes(since)` with seq, time, action, payload, response and `ok`.

`webhook_sink.py` takes any POST path outside `/_sim/` as a Discord webhook (JSON, or
multipart with `payload_json` and files as the report send posts) and answers 204. An
empty, unparseable or over-2000-character message gets Discord's 400 body (code 50006,
50109, 50035) and is counted in `rejected`, not recorded. Each message keeps seq, time,
path, username, content, embeds, file names and sizes, and `text`: content, username and
the embeds' titles, descriptions, fields, footers. `count(needle)` matches `text`
without case.

Both use `loopback_http.py`, the shared loopback base: 127.0.0.1, port 0, `Host`
127.0.0.1 or localhost with this port, no `Origin`, POST `application/json` (the sink
also multipart). In-process: `SheetSim(tabs, stream_keys).start()` with `set_tab`,
`tab`, `records` (`records(tab)[i]` is physical row i+2) and `writes`;
`WebhookSink().start()` with `messages(since)` and `count`. `.env()` gives the
`RACECAST_SIM_SHEET_URL`/`_SHEET_PUSH_URL` and `RACECAST_SIM_DISCORD_WEBHOOK_URL`
values. Over HTTP: `/_sim/state`, `/_sim/tab?name=`, `POST /_sim/tab` (`rows` or
`csv`), `/_sim/writes?since=N`, `/_sim/messages?since=N`.

```bash
python3 tools/event-sim/fakes/sheet_sim.py --port 0 --tabs DIR --ready-file sheet.json
python3 tools/event-sim/fakes/webhook_sink.py --port 0 --ready-file sink.json
```

`tests/test_sheet_sim.py` and `tests/test_webhook_sink.py` drive them through the relay's
own readers and writers (`post_webhook`, `SetupControl`, `HudSource`, `CrewSource`,
`parse_timer_tab`, `apply_stream_service_for_ref` on the Channel tab, get-graphics on the
Assets tab, the Discord payload builders, `http_util.post_multipart`). `env()` carries
only the endpoints: the caller sets `RACECAST_SIMULATION=1`, or the Sheet URL resolves
to Google.

## Driver replay (`tools/driver-replay.py`, #1115)

Up to 15 simulated senders for a running relay: they register on the event's invite,
approve themselves on the tailnet route `/drivers/requests/approve` (so the relay must
listen on the tailnet or loopback, as the default `--relay` does) and post encrypted GT7 packets along the picked layout's line through the real ingest
route, with lap times, best laps and fuel. `--gap` sets the start spacing (small values
give a dense pack for the track map). Needs `racecast event invite --new` and the same
`racecast event track ID`; screenshots of the dashboard, desks and cockpit use it with
the demo profile. Never point it at a league during an event: its senders take real
entries and its laps reach the race and the fastest lap; a profile other than `demo`
needs `--force`. `--pending N` registers N more drivers for the second stint and leaves
them waiting for approval (the desks' Driver requests). `tests/test_driver_replay.py`
pins its packets to the GT7 layout.

```bash
python3 tools/driver-replay.py --profile demo --track 6e378b --cars 15 --gap 4
```

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
  committed `example`, `demo`, `solo-commentary` and `solo-pov` profiles, so a real kit (with its poster crops and league logos)
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
runtime/pw-venv/bin/python tools/e2e.py --visual   # visual acceptance run -> runtime/visual-report/report.html

# Refresh the bundled GT7 reference data (src/assets/gt7/: car tables from
# ddm999/gt7info, MIT-0; track catalogue index.json from
# jbhoorasingh/gt7-datalogger-track-data, CC0) that name the telemetry car id (#713)
# and track (#787). Run before a release when GT7 added cars or tracks. The track
# signatures never ship; each install downloads them (gt7_data.RUNTIME_ONLY).
python3 tools/fetch-gt7-data.py         # --dry-run lists added/removed cars and layouts

# Fetch any missing HUD country flags from the sheet's Configuration tab
python3 tools/fetch-flags.py            # adds missing -> src/assets/flags/ (keeps old)

# Probe the broadcast-chat reader (#294) against a LIVE channel: standalone, no
# Sheet/relay/UI. YouTube: resolve the live videoId via yt-dlp + tail Innertube chat.
# Twitch: anonymous IRC (auto-detected for twitch.tv URLs, or --twitch for a bare name).
python3 tools/broadcast-chat-probe.py https://www.youtube.com/@SomeChannel  # --resolve-only / --cookies
python3 tools/broadcast-chat-probe.py https://www.twitch.tv/SomeChannel     # or: --twitch SomeChannel

# Probe GT7 UDP telemetry (#324) against a LIVE PS4/PS5: standalone, no relay/Sheet.
python3 tools/gt7-telemetry-probe.py --ps-ip 192.168.1.42   # heartbeat + decrypt + field dump
# Stop the relay first: only one listener can own UDP 33740. It requests the extended
# "~" format; a console still streaming "A" switches only after ~10 s of silence.
python3 tools/make-demo-recording.py --out runtime/solo-pov/telemetry-recordings   # synthetic GT7 recording for the Telemetry view (needs runtime/gt7/signatures.json; --mirror: unknown track)

# Publish the GitHub wiki from src/docs/wiki/ (maintainer; --dry-run to preview)
python3 tools/sync-wiki.py
```

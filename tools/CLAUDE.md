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
  already runs `tests/test_e2e.py` via `run-tests.py`. After the checks it loads the league
  event in a `--simulation` app home of its own (`profiles/demo`) against the simulated
  league service: `racecast event league`, the Control Center routes and `event stop`.
  A relay of that home then finishes a race and sends the result, the Director's resend
  replaces it, and after the relay stops `racecast event result send` sends it from the
  race recording (#1254). Before the finish the same relay must post the live state every
  two seconds, and a widget the Director shows reaches `/league-widgets/data` and the
  Intermission page (#1255). Then a timing link carries no league block until
  `LEAGUE_WIDGETS_TIMING=1` lands in the profile, after which `/console/timing/data` lists
  every event widget while none is on air (#1256).
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
views, the Director Panel, the cockpit, the Race Control desk and the public timing page (these
three also at 390 px; the timing page on a frozen fixture with the league block, #1256) against the synthetic run, which serves a Crew roster (`--crew-csv-url`) and runs
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

Stage 2 (#1116) runs in a league lobby; its runbook is
`docs/superpowers/plans/2026-11-24-telemetry-probe-stage2-runbook.md`. The driver's
side is the sender's `--check` (12 s of heartbeat and receive, three empty posts, OK or
FAIL per part, exit code 0 or 1; it never posts telemetry). The producer's side is
`tools/probe-stage2.py` (logic in `tools/probe_stage2.py`, covered by
`tests/test_probe_stage2.py`):

```bash
python3 tools/probe-stage2.py kit --out KIT          # one setup sheet per platform from the invite
python3 tools/probe-stage2.py watch --out RESULTS    # 1 s poll of /drivers/data, /drivers/race, /telemetry/probe
python3 tools/probe-stage2.py mark --out RESULTS "block D change"
python3 tools/probe-stage2.py race-reset --out RESULTS   # the race over between blocks (tailnet route)
python3 tools/probe-stage2.py collect --out RESULTS [--runtime runtime/<profile>]   # --runtime after event stop
python3 tools/probe-stage2.py save-check --out RESULTS LABEL FILE   # a check output without addresses or code
python3 tools/probe-stage2.py report RESULTS         # report.md + analysis.json
```

The sheets carry the invite link and stay out of the results; `collect --runtime` puts the
audit log and the race recording into `RESULTS/private/`, since both name connection ids
and crew members. The report splits its senders and 0x84 tables by block (marks starting
with `block`), and no lap or distance step or change counts across a `race reset` mark.
Rates count only the poll intervals in which a counter rose (`idle` minutes show the
rest), 0x84 agreement compares the order of GT7's positions among the cars that carry one
with the relay's order, and the watch writes driver names as `Driver N` with the names in
`private/names.json`; the race recording CSV goes to `private/` too. A results record carries
no driver code, probe key or invite id; a connection id becomes an 8-character hash that
still joins a sender to its race row. Rates come from counter deltas between polls,
since the relay's `packets_per_s` is a lifetime average; `dev/s` is the device's own
`received`, `relay/s` what reached the relay.

`tools/timing-viewer-load.py LINK --viewers N` runs simulated viewers of the public
timing page (#1128) against an event's timing link: one page load each, then the page's
own poll (gzip, `If-None-Match`, the wait from `X-Poll-After`). Its JSON report gives
the upload in kbit/s, the status counts, and how often a viewer's data refreshed
(`refresh_s`) and how old it was (`data_age_s`). Run it from outside the tailnet against
the Funnel link. It skips the brand logos, which a browser caches.

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
one allowed protocol, MPEG-TS demuxer forced, 1 s probe window since a live feed never
ends) and is killed after 4 s. Each feed is read in the
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
`RACECAST_SIMULATION=1` and `RACECAST_SIM_OBS_WS`, `obs_sim.fetch(url, path)` reads the endpoint.

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
`/spreadsheets/d/sim/gviz/tq`; a tab nobody seeded is an empty 200, or the first seeded
tab with `unknown_tab="first"` (`--unknown-tab first`). Its
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
empty or unparseable message gets Discord's 400 body (50006, 50109); a field of the
wrong type or size (content not a string or over 2000 characters, username not a
string, embeds not a list of at most 10 objects) gets 400 50035 with the field under
`errors`; a file over Discord's documented 20 MiB per-file default a 413 40005. None of
them is recorded; `rejected` counts them. Each message keeps seq, time,
path, username, content, embeds, file names and sizes, and `text`: content, username and
the embeds' titles, descriptions, fields, footers. `count(needle)` matches `text`
without case.

Both use `loopback_http.py`, the shared loopback base: 127.0.0.1, port 0, `Host`
127.0.0.1 or localhost with this port, no `Origin`, POST `application/json` (the sink
also multipart). In-process: `SheetSim(tabs, stream_keys).start()` with `set_tab`,
`tab`, `records` (`records(tab)[i]` is physical row i+2) and `writes`;
`WebhookSink().start()` with `messages(since)` and `count`. `.env()` gives the
`RACECAST_SIM_SHEET_URL`/`_SHEET_PUSH_URL` and `RACECAST_SIM_DISCORD_WEBHOOK_URL`
values plus `RACECAST_SIMULATION=1`, so no caller reaches Google by forgetting the flag.
Over HTTP: `/_sim/state`, `/_sim/tab?name=`, `POST /_sim/tab` (`rows` or `csv`),
`/_sim/writes?since=N`, `/_sim/messages?since=N`.

Failure injection: a rule `{"target", "delay", "status", "error", "times"}` from the
constructor (`faults=[...]`), `.fail(target, ...)` or `POST /_sim/fail` (one rule or a
list; an empty body clears them, as `.clear_faults()` does). Targets: the Sheet's
`tab:<Tab>` and `read` for gviz reads, `action:<action>` and `push` for script calls;
the sink's `path:<path>` and `hook`. Every matching rule adds its `delay`, slept through
the `sleep` seam (tests pass a recorder, never the wall clock; the default wait ends on
`stop()`). The first matching rule with a status (400-599 only) or an error answers in
place of the request, and rules behind it are not hit: the Sheet's script error is HTTP
200 `{"error": ...}`, a read error needs a status and answers an HTML error page, the
sink's error is 400 `{"message": ..., "code": 0}`, a bare status gets `{"error":
"simulated failure"}`. A faulted request never reaches the tabs or the message log.
`times` spends a rule after that many hits; `faults_hit` in `/_sim/state` counts every
rule hit.

Script Properties can be seeded out of process with `--stream-keys FILE` (JSON
`{ref: key}`) or `POST /_sim/stream-keys`, which answers only the number of refs; no
route reads a key back. `redirect=True` (`--redirect`) answers the push URL the way
Apps Script does, with a 302 to a one-time echo URL that urllib follows as a GET.
Seeded cells are formatted like `setValue` writes them (`TRUE`/`FALSE`, null empty).
`stop()` refuses new requests with 503, wakes handlers in a delay and joins them for
at most 5 s. Bodies are capped (Sheet 1 MiB, sink 41 MiB so an oversized file still
reaches the per-file check); a refused body is drained up to 1 MiB first, so the
client receives the answer. The CLIs remove their ready-file on Ctrl+C or SIGTERM
(`obs_sim.py` too).

Unverified against the real services (from memory or chosen, not measured):
- gviz on an unknown `sheet=` name: the fake answers an empty 200. Forum reports of
  gviz answering with the first sheet exist but none tests a missing name, so
  `unknown_tab="first"` offers that instead.
- gviz without `headers=1` may merge leading rows into a header; the fake never does.
- Discord's `?wait=true` (200 with the message object) is ignored: the sink always
  answers 204.
- The Discord 400 and 413 bodies (50006, 50109, 50035, 40005) and the `errors` shapes
  are written from memory; no racecast caller reads them. The 20 MiB per-file limit is
  from Discord's API reference; Nitro and boost tiers raise it, the fake does not.
- gviz types each column by its majority and exports minority-type cells blank (why the
  script pins every written cell to text with `setNumberFormat('@')`); the fake keeps
  every cell as text.
- `/exec` answers a POST with a 302 to `script.googleusercontent.com`; the fake answers
  directly unless `redirect=True`, and its echo URL stays on loopback.

```bash
python3 tools/event-sim/fakes/sheet_sim.py --port 0 --tabs DIR --stream-keys keys.json --ready-file sheet.json
python3 tools/event-sim/fakes/webhook_sink.py --port 0 --ready-file sink.json
```

`tests/test_sheet_sim.py` and `tests/test_webhook_sink.py` drive them through the relay's
own readers and writers (`post_webhook`, `SetupControl`, `HudSource`, `CrewSource`,
`parse_timer_tab`, `apply_stream_service_for_ref` on the Channel tab, get-graphics on the
Assets tab, the Discord payload builders, the relay's own `Relay._discord_post`,
`http_util.post_multipart`).

## Simulated league service (`tools/event-sim/fakes/league_sim.py`, #1253)

`league_sim.py` answers every call of `src/docs/design/league-service.openapi.yaml` under
`/racecast`: `GET /v1/event?ref=` (one two-leg event, ref `SIM-MATCH-1`, a `score` and a
`table` widget), `POST /v1/live` (a stand-in league computation from the posted rows),
`POST /v1/result` (stores the draft, a resend replaces it), `GET /v1/result?ref=&race=`
and, with `graphic=True` (`--graphic`), a `graphic_url` in every status served as
`GET /v1/graphic/<race>.png` (`graphic_body`, call `graphic`).
A wrong or missing bearer key gets 401 and counts in `unauthorized`; a body off the
contract gets 400 with the reason and counts in `rejected`. Accepted calls land in
`calls(since, call)` with the User-Agent and the body, never the key. `answer(call,
status, body | raw, location)` (or `POST /_sim/answer`) makes a call answer exactly that,
for redirects, oversized and broken answers; fault targets are `call:<event|live|result|
result-status>` and `service`. `.env()` gives `RACECAST_SIMULATION=1` and
`RACECAST_SIM_LEAGUE_SERVICE_URL`/`_KEY`, which `config_overrides` puts in place of the
profile's service. The event simulation's stack starts one with a random key. Tests:
`tests/test_league_sim.py`, `tests/test_league_service.py`.

```bash
python3 tools/event-sim/fakes/league_sim.py --port 0 --ready-file league.json [--result-status stored]
```

## League widgets browser run (`tools/e2e-league-widgets.py`, #1255)

Playwright Python, CI `visual` job. A synthetic profile loads the simulated league event;
the real Control Center builder places both widgets and saves; a relay handler with a
`league_widgets.Live` against `league_sim` serves the Director Panel and the HUD. It shows a
widget from the panel, checks that markup in a label stays text, then answers 503 and
waits for the 30 s hold to hide the widget and for the panel to name the reason (about
35 s). `--shots` refreshes `cc-league-widgets.png` and `director-panel-league.png`.

## League replay (`tools/league-replay.py`, #1253)

Plays a race recording (`race-recording-*.sqlite`, opened read-only) against a league
service as live state, one call per 2 s of race time (`--speed` faster), through
`league_service.post_live`, and prints the widget values it answers. A league builds its
endpoints against real race histories with it. The key comes from `LEAGUE_SERVICE_KEY`
or `--key-file`, never argv. A recording keeps no team, number or lap time; its time
gaps go out as `gap_s`/`interval_s` (`gap_s` null for a lapped car). `--dry-run` prints
the first body. `tests/test_league_replay.py` plays a synthetic recording against the
simulated service.

```bash
LEAGUE_SERVICE_KEY=... python3 tools/league-replay.py REC.sqlite --url https://league.example/racecast --ref M-12 --race leg-1 --speed 10
```

## Stream stub (`tools/event-sim/fakes/stream_stub.py`, #1231)

Stand-ins for `yt-dlp` and `streamlink` that serve `sim://<name>` channels and refuse
every other address; they open no connection. `StreamStub(dir).install()` writes them into
`dir`, and `.env()` gives `RACECAST_SIMULATION=1` and `RACECAST_SIM_STREAM_BIN=dir`.

- yt-dlp resolves a live channel to `http://sim.invalid/<name>/<height>.m3u8` and prints
  `rcq <height> 25`, the height being the `-f` cap and at most 360. `.invalid` never resolves, so the
  relay's one manifest probe per resolve fails at the network guard and reports nothing;
  that refused lookup is expected, not a leak. Offline,
  upcoming and ended exit 1 with the text `classify_source_state` reads; the live-status
  print answers `is_upcoming` or `was_live`. Downloads, `--cookies-from-browser` and real
  URLs exit 1, an unknown flag exits 2.
- streamlink maps only that URL or a bare `sim://<name>` back to a channel and writes
  MPEG-TS on `--stdout`. It reports version 8.2.0, and its help lists
  `--stream-segmented-queue-deadline`.
- `--player-external-http` behaves like streamlink's: one player at a time, each with a
  fresh pull, and after a stream ends or goes offline the server waits for the next player
  until it is killed. It binds `127.0.0.1` only, where the real tool binds every interface,
  and never shares the port with another listener (`SO_EXCLUSIVEADDRUSE` on Windows), so a
  busy port exits 1 at once, the relay's fast-exit case (#143). `SO_REUSEADDR` on POSIX lets
  a restart rebind a port a killed server left in TIME_WAIT.
- `tests/test_stream_stub.py` feeds every yt-dlp and streamlink argv the relay,
  `loopstream`, racecast's smoke probes, `get-media` and both cookie exports build through
  the stand-ins' parsers, so a new flag in `src/` fails there.

The picture comes from ffmpeg: `testsrc2` at 640x360 and 25 fps, mpeg2video and mp2 in
MPEG-TS. It carries stint, commentator and channel as drawtext from `label.txt` in
fontconfig's default font; if drawtext fails, the pull logs it and goes on without text.
An 18-cell strip, a band of a thirtieth of the picture (at least `STRIP_MIN` = 8 px) under
the demo Overlay's opaque lower panel (`OVERLAY_PANELS`) and above the HUD's team row, holds
the channel's `code` and stint, and the audio is a sine at `tone_hz(code)`. A stream's id is
`sim:<name>` (`video_id`), never one a YouTube id check accepts.
`decode_identity(rgb, w, h)` reads `(code, stint)` back from a grabbed frame. The stand-in
copies ffmpeg's output itself, so killing it leaves ffmpeg on a broken pipe. A freeze,
a stall's end or a text fallback starts a new ffmpeg; `TsSplicer` forwards whole 188-byte
packets only, continues each PID's continuity counter and gives the new run an
`-output_ts_offset` two frames past the last PTS sent (or the wall clock after a gap), so
PTS and PCR keep rising across the seam. Both are 33-bit 90 kHz values and would wrap after
about 26.5 hours of one pull, longer than any simulated event.

The `card` look (`StreamStub(look="card")`, `install --look card`, stored in `stub.json` so
the stand-in processes read it) is what a record run's feeds show: the demo overlay's
colours (`profiles/demo/overlay/hud.css`) with a slowly moving `gradients` source, and
overlaid the images in `DIR/cards/` (`card_paths`): `<channel>.png` with the stint and the
commentator's name, and a clock whose `hand.png` turns once a minute over `dial.png` from the
pull's offset, so a restarted ffmpeg does not reset it. The clock sits centred under the
title (`card_clock`), clear of the HUD's logo and lower third and the dashboard's map. A record run renders those images
with Playwright's Chromium before the relay starts; ffmpeg draws no text, because stock
builds such as Homebrew's lack `drawtext`. A channel the `stream` role creates during the
run, or whose stint or commentator it changes, gets its card rendered in the run's browser
before the stub serves it (written to a temporary file, then renamed). A channel without
images shows the bare card. A
frozen card is still and has no clock. The identity strip, the tone and one thread per stage
stay as in the pattern look.

Control for the scenario engine: each channel is `<dir>/channels/<name>.json`, which every
running pull reads every 0.2 s; an unreadable file keeps the last state, and a Windows
sharing violation is retried briefly. Names are case-insensitive (stored in lower case),
and the Windows device names `con`, `prn`, `aux`, `nul`, `com1`-`com9` and `lpt1`-`lpt9` are
refused. Every change runs under an `O_EXCL` lock file (`channels/.lock`; one older
than 10 s is renamed aside and removed only if it is still the file seen stale), so several controllers may share a directory. Times are epoch seconds; `at` defaults
to now, `until` to the next `clear`.

- `channel(name, stint=, commentator=, status=)` creates or updates a channel, and a new
  one is live. A directory holds at most 255 channels, since the identity strip carries an
  8-bit code; the 256th is refused. A status other than `live` ends the running pulls.
- `cut(name, at=None)`: the pulls running at `at` end like a finished stream (exit 0);
  later pulls play, so the relay recovers at once.
- `stall(name, at=None, until=None)`: pulls in the window send nothing. The relay's
  byte-stall watchdog kills a pull that had sent bytes; one that sent none, such as the
  re-pull inside the window, gives up after `stream_timeout` (60 s, streamlink's default
  `--stream-timeout`, set by `StreamStub(stream_timeout=)` or `install --stream-timeout`
  and stored in the directory) with a read timeout and exit 1.
- `freeze(name, at=None, until=None)`: pulls in the window send still colour bars with
  the identity and the tone at the normal rate, a freeze only the picture shows.
- `clear(name)` ends a stall and a freeze, running or scheduled.

```bash
python3 tools/event-sim/fakes/stream_stub.py DIR install
python3 tools/event-sim/fakes/stream_stub.py DIR set comms2 --stint 2 --commentator Ben
python3 tools/event-sim/fakes/stream_stub.py DIR freeze comms2 --until 1790000000
```

Windows: racecast starts the stand-in by its absolute path (`simulation.tool`), and a
stand-in there must be an `.exe`. Each stand-in there is distlib's console launcher, `t64.exe`, `t64-arm.exe` or `t32.exe`, the
one pip's own scripts use. The stub reads it from the pip wheel ensurepip bundles or from
an installed pip and appends `#!<python>` and a zip with `__main__.py`; the launcher runs
the interpreter on its own file. Elsewhere a stand-in is an sh/Python script, mode 0o700. `install()` may run again while
pulls run: it writes each stand-in to a temporary file and replaces it, moving a running
`.exe` aside on Windows (a later install removes it), and removes only the work dirs
(`pulls/<pid>-<thread>`) of stand-ins that have ended; a pull recreates its own if it goes
missing. A relative `--ffmpeg` path is made absolute.
The CI e2e job sets `OBS_SIM_REQUIRE_FFMPEG=1`, so the real-ffmpeg tests cannot skip there,
and only there do they also require drawtext to find a default font.
CI installs that ffmpeg through `.github/actions/apt-install`, which skips the runner's
regional apt mirror and fails within its time limit instead of hanging.

## Event simulation engine (`tools/event-sim/event_sim.py`, #1232)

Runs a scenario against the fakes above plus the real relay and Control Center, and writes
`OUT/report.html`, `OUT/report.json`, `OUT/shots/` and `OUT/logs/` (spec
`docs/superpowers/specs/2026-11-21-event-simulation-design.md`). Exit 0: no hard finding;
1: a hard finding; 2: the scenario or the stack did not start. Ctrl+C, SIGTERM and SIGHUP
(unless nohup ignores it) stop a run the same way: teardown, work dir removed, the report so
far, exit 128 plus the signal. SIGTERM and a SIGINT that a background start ignored are
handled again. While the engine runs, a signal is only recorded (raising inside a sync
Playwright call breaks its dispatcher); the engine raises it between steps and in its waits,
within a poll. On local Linux the caller forwards them to the contained run through its stdin
(`e2e_safety.enter(..., stop_pipe=True)`) and waits up to `STOP_GRACE_SECONDS` for its
teardown (a second signal ends that wait). A signal during the teardown waits until the report
is written; a stop while the stack starts still writes one, and a stopped `--repeat` set still
writes its summary. Not in `tools/run-tests.py`;
`tests/test_event_sim_engine.py` covers the engine with in-process fakes and a fake clock.
Each page role's last JS heap, DOM node and event listener counts (Chromium's Performance
metrics, also `page(role)`'s `heap_mb`, `dom_nodes`, `listeners`) go into the report's
`pages` when it closes. Timing, failure snapshots and traces: see "Hunting a flake" below.

```bash
python3 tools/event-sim/event_sim.py --scenario smoke --out DIR   # bundled name or a .toml path
python3 tools/event-sim/event_sim.py --scenario smoke --check     # validate only, starts nothing
python3 tools/event-sim/event_sim.py --scenario smoke --out DIR --no-skips   # a skipped step fails the run
python3 tools/event-sim/event_sim.py --scenario desk-fit --out DIR --no-skips   # layout check, #1267
python3 tools/event-sim/event_sim.py --scenario red-flag --out DIR --no-skips --repeat 12 [--load cpu:2]
```

`desk-fit` runs the `fit` action of the Race Control desk, the Director Panel and the
Commentator Cockpit (no live-column control may need scrolling at 1440x900) with requests
waiting, a red flag with a class lap row (`[event] classes` gives the roster a Class column),
a graphic request under a two-row panel header, and in qualifying. Its 14 cars drive only
while the cockpit's fit with `rows = 14` asks for every timing row in view (ROWS_SCRIPT, `rows`
selector), then park; no step waits on a lap.

`--no-skips` is for a run that must have its browser (the workflow): every skipped step
and every missing screenshot becomes a hard finding. The generic `page` role is skipped
without a browser too.

`stack.Stack` runs the Sheet, sink, stream stub and OBS in process. It copies `src/` and
`profiles/demo` into a temporary app home and starts `racecast --simulation relay run` and
`racecast --simulation ui` there, so the relay.mode stamp, the console secret
(`secrets.token_hex`, passed as `RACECAST_CONSOLE_SECRET`) and every runtime file stay out of
the checkout. Inherited `RACECAST_*` values are dropped. Ports are free loopback ports,
never 8088, 8089 or 53001-53003: `RACECAST_SIM_RELAY_PORT` points the relay and the Control
Center at the same control port, `RACECAST_SIM_FEED_PORTS` gives every relay start of this
app home (also the Control Center's Restart) the run's feed ports, and the OBS collection's feed inputs and relay browser
sources are rewritten to this run's ports (`port_map`). Once the relay answers, the stack
writes `relay.pid` (the control port's holder) and the `relay.profile`/`relay.mode` stamps
into the temporary app home, as `relay start` would, so the Control Center reports it alive. `--keep-work` keeps the app home. Screenshots of pages use Playwright's
own Chromium, as `tools/e2e.py --visual` does (local setup there); without it they are soft
findings.

Lifetimes: a scenario's `max_minutes` (default 12, at most 120, the spec's CI timeout for
`full`) ends the run. Each service runs under `tools/e2e_child.py` and, on local Linux, the
whole run inside `e2e_safety` containment, both for `max_minutes` plus 10 minutes for start,
teardown and report. Only the event simulation opts into more than 900 s
(`e2e_safety.enter(..., long=True)`, `e2e_child.py SECONDS --long -- ...`), capped at
`e2e_safety.LONG_MAX_SECONDS` = 7800; everyone else keeps 900 s or the `MAX_SECONDS` they set.
The unit's 2 GiB memory cap still applies: `full` does not fit into it and runs in the
`event-sim.yml` workflow, where the hosted runner is the boundary and no unit is used.
A check's timeout is cut at the end of the run, and a step that has not run by then fails,
so the report is written inside the lifetime.
It shares the one E2E unit: run it after, not beside, `tools/e2e.py`.

Scenario (`scenario.py`, TOML): `name`, `description`, `max_minutes`, `wait` (default
trigger timeout), `[event]` with `stints`, `stint_laps` (one count per stint for the Schedule's
`Laps` column, which is empty without it), `track` (a GT7 layout id), `track_length_m`, `cars`
(1-15), `classes`, `parts` (0-9), `qualifying_part`, `seed` and `[[event.commentators]]`
(`name`, `channel` for `sim://<channel>`, `stint`, `scheduled` = the Schedule row has the link
from the start); an unknown key or a value the stack would refuse fails the load (`--check`),
`[sheet.tabs]` (replaces tabs built from the event), `[tolerances] state_s` (default 2) and
`[[step]]`: `id` (default `do`, unique, letters, digits, `_` and `-`, no dot), `at`, `role`
(`kind` or `kind:instance`, the instance letters, digits, space, `.`, `_`, `-`), `do`, `args`,
`expect`, `soft`, `timeout` (per check, default 5 s), `wait`, `shot` (a name, or
`{name, role, element}`) and the walkthrough film's `caption` (the line shown while the step
runs), `chapter` (a chapter title starting here), `focus` (the views to enlarge: 1, 2 or 4
of the run's page roles and `program`; four are the quarters of a driver swap) and `card`
(`{title, subtitle}` of a title card before the chapter the step opens; needs `chapter` and `caption`).
The film's top-level `intro` and `outro` take the same table. Only a record run reads
these, into `timeline.json`. The stint of a `submit_link` or `approve_link` must be a
label of the served Configuration tab's Stints column, the event's stints plus the Qualifying
tab's labels, since the relay writes no other; the load refuses any other. `at` is a duration since the run started, `after:<id>[+dur]`
(after that step ended; skipped when it failed), `lap:leader=N` (`race.lap >= N`) or
`state:<name>` (`race.state`); the leader's lap is the relay's `race.lap.leader`. `${id.key}` in args reads an earlier step's result; such a
step waits for that step and is skipped when it failed. `after:` and `${...}` must name an
earlier step in the file (a typo, a forward or a mutual reference is refused at load).
Names that become files (scenario, shot) are letters, digits, `.`, `_`, `-`, start with a
letter or digit, and are no Windows device name (`con`, `nul`, `com1`, ...).

Scheduling: one executor, one action at a time. Each tick it runs the first step in file
order whose trigger fired, so a step that waits never holds back a later one whose trigger
fires first; `after:` gives an explicit order. A trigger is armed at its earliest moment:
its time, the referenced step's end plus the offset for `after:` (unarmed until that step
ran), or the run start for a race trigger. One that has not fired within `wait` of being
armed (default: until the run ends) fails the step hard. A step's own `expect` checks run
inside its action.

`expect` (`expect.py`) is a restricted expression over the roots `relay` (`status`, `up`,
`port`, `get(path)`), `obs` (`scene`, `preview`, `streaming`, `recording`, `state`,
`program_feeds`, `program_channels`: the stub channels whose identity strip the visible
program feeds show), `sheet` (`tab(name)` or `sheet.<tab>`: records, index 0 is the first row
under the header; `writes`, `write_count(action)`), `webhook` (`count(text)`, `messages`,
`rejected`), `race` (the relay's `/drivers/race`), `page(role)` and `result(step_id)`. Keys
match without case, spaces as `_`. Only comparisons, `and`/`or`/`not`, literals, attribute
and index access, probe methods, `len`, `sorted` and these helpers parse: `pluck(rows, key)` (the
key of every row, in order), `row(rows, key, value)` (the first row with that value),
`drift(before, after, key)` (the largest change of a number between rows of the same `entry`,
for gaps after a restart), `swaps(before, after, key, margin)` (the `[ahead, behind]` pairs
more than `margin` apart in `before` that `after` lists the other way round),
`unchanged(before, after, key)` (the entries whose value stayed, e.g. a driver after a
stint change) and `having(texts, word, ...)` (the texts holding every word, e.g. a timing
row); anything else, a `_` name or an unknown root is refused at load. A value that cannot be read yet keeps the check pending until its timeout.
A probe, a method or a function is never a value or an argument. Checks are polled, so they
must have no side effects: `relay.get` takes one literal path from `probes.RELAY_READS`
(`/status`, `/drivers/race`, `/drivers/race/recording` and the read-only `.../data` routes
such as `/setup/data`). Any other path, a control such as `/next` or `/setup/set/...`, is
refused at load and again before a request is sent.

Findings (the report's legend says the same): hard is an action error, an `expect` still
false at its timeout, a trigger that never fires, a script error on a page, a traceback in a
service log (the captured output and the relay's own `runtime/demo/logs`). Soft is a `soft`
check, a check slower than `state_s`, a failed resource load on a page, an ERROR line in a
service log (each distinct line once, with its count), a missing screenshot.

Writing scenarios against the relay:
- A cockpit submission names the stint by its Schedule label: `stint = "Stint 2"`, not `2`.
- The stack pins auto-arm (`RACECAST_MANUAL_FEED_ARM=0`): a feed pulls its stint as soon as
  the link is in the Schedule, so no step arms one. Before a program check or a handover,
  wait for the feed with a check such as `relay.status.feeds.A.state == 'serving'`, or
  `relay.status.handover_next.stint == N` plus
  `relay.status.feeds[relay.status.handover_next.feed].state == 'serving'` for the incoming feed.
- The relay's feed cover hides a feed for about 25 s after it starts, so a program identity
  check needs a timeout of a minute or more.
- A new lobby's driver takes a team over only after standing in the box for the relay's box
  hold (1.5 s): release the cars a few seconds after `lobby`, never in the same moment.
- Start event parks OBS on Standby unless it streams: a scenario that restarts mid-event goes
  live with a Part first, as a real event does.
- `.class` is a Python keyword: read a row's class as `row(...)['class']`.

Roles (`roles/`): a class with `kind`, registered with `@roles.register`; a step's `role`
is `kind` or `kind:instance`, one instance per string, opened on first use and closed at
the end. `do = "x"` calls `act_x(args)`; a dict it returns is the step's result.
`StepError` is a hard failure, `ctx.finding("soft", text)` a soft one, `Unavailable` skips
the step, `page()` feeds
`page(role)`, `shot(path, element)` writes a PNG or raises `ShotUnavailable`, `close()`
returns late findings. `ctx` (`engine.RunContext`) has `url(service)` (`relay`, `ui`, `obs`,
`sheet`, `sink`), `token(name)` (a console token for a crew name), `stub`, `obs`, `event`
(the scenario's `[event]`), `event_link(kind)` and `browser()` (a shared headless Chromium,
None without Playwright). Built in
(`roles/builtin.py`): `api` (`get`/`post` with `service`, `path`, `as`, `json`, `status`),
`obs` (`scene`, `request`; its shot is the program), `stream` (`channel`, `cut`, `stall`,
`freeze`, `clear`), `relay` (`kill`: ends the process that holds the run's control port and
that the app home's `relay.pid` names, as a crash would, nothing else) and `page` (`open`
with `service`, `path` starting with one `/` on that service, `as`, `wait_for`, `viewport`
[width, height] on the first open, e.g. an OBS page at 1920x1080; tokens are redacted from what it reports; the base of the party
roles below). A page's repeated console error is one finding with its count, and its
requests after its service stopped are one more.

### Hunting a flake: timing, failure snapshots, traces, `--repeat`

A flaky step is never fixed by a longer timeout or a retry. The run records what is
needed to explain a failure at the moment it happens.

- Timing. Every step's `actions` in `report.json` lists each page action with its
  seconds, nested by `depth`: `open`, `control` for a control found, visible, enabled
  and scrolled to, `click`, `fill`, `choose`, `request` for the page's own request until
  it is answered, `wait` for the page showing the result, and each `shot`. Calls inside a
  `wait` are its polls, not actions. An action over its budget, `roles/builtin.py`
  `BUDGETS` or else 2 s, is a soft `slow:` finding under the role that acted, a parent
  only when no inner action explains it. A `wait` has no budget: its older 2 s soft
  finding stays. The report lists the slowest actions of the run.
- Host state, `hoststate.py`, Linux only and None elsewhere. Each step's `host` says
  what the run got meanwhile: `cpu_s` of the unit, `unit_cpu_stall_s` and
  `cpu_stall_share` from the unit's `cpu.pressure`, `throttled_s` from the 200 % quota,
  the unit's IO and memory stall, the host's CPU, IO and memory stall, the host's swap-in
  and swap-out pages, new `oom`, `oom_kill`, `high` and `max` memory events and the
  unit's memory peak. The E2E unit runs at `CPUWeight=10`: other work on the machine
  starves it without any throttling, which the stall shows and `throttled_s` does not.
  A step during which this run's memory cgroup OOM-killed a process is a hard finding of
  its own with the kill, the `memory.max` events and the peak, because a killed renderer
  otherwise surfaces later as a timeout.
- Failure snapshot. A step that fails, and a screenshot that fails, also one a step
  takes of another role's page, leaves one in the step's `snapshots`: the host and unit
  state, the change since the step began, every process of the run's cgroup with its CPU
  seconds and major page faults, and for a page role `PageRole.diagnose()`. That is the
  URL, whether the page answers within 2 s and how fast in `alive` and `answered_s`,
  `visibility`, `focus`, `ready`, the title, Chromium's Performance metrics, the CPU
  seconds of each browser process and the last 30 console lines and failed requests. A
  hung page is asked nothing more. Then a picture of the whole page, `shots/fail-<step>.png`
  or `fail-<step>-shot.png` for a failed screenshot, with how long it took: a picture
  that takes 0.1 s right after an 8 s timeout says the page was alive.
- Long tasks and CPU. Each page records its long tasks, the main thread busy 50 ms or
  more, and its long animation frames with the scripts that ran in them,
  `LONG_TASK_OBSERVER`. A step that failed or was slow lists every page's within the
  step in `long_tasks`, and each process's CPU seconds and major faults over the step in
  `cpu`. Every shot carries its own `host` and `cpu`. A long task with about as much
  renderer CPU is page work, one with little CPU is waiting.
- Failure class. A failed step or screenshot whose window, up to the failure itself,
  the unit spent `engine.STARVED_SHARE` of or more waiting on IO or memory gets a class
  on its timeout or failed-check finding. `host starved` needs the host's own IO or
  memory stall over the same share; new `memory.max` or `memory.high` events of the unit
  make it `unit at its memory cap`. The finding stays hard and carries the values.
- Admission. `e2e_safety.enter` names a busy E2E unit and starts no unit while the host
  is short of memory. It needs MemAvailable of the unit's cap plus `MEMORY_MARGIN`, 512
  MiB, and memory PSI avg10 of at most `PSI_SOME_AVG10_MAX` some and
  `PSI_FULL_AVG10_MAX` full. Under more pressure it waits up to `PRESSURE_WAIT_SECONDS`,
  then refuses with "host under memory pressure:" and the values. A missing PSI file
  never blocks, and a hosted CI runner enters no unit and is not checked.
- Traces, off by default. `--trace fail` records a Playwright trace of every page, DOM
  snapshots and screencast, in one chunk per step and keeps the chunks of a step that
  failed, left a snapshot or had a slow action as `traces/<step>-<role>.zip`; `--trace
  all` keeps every step's. Tracing is heavy: Playwright keeps the session's screencast
  and resources in temporary files, which the run's memory pays for on tmpfs. Trace a
  short scenario, or a run outside the unit. Open a trace with `playwright show-trace
  FILE`. A zip holds what the page sent and received, the run's console tokens included:
  they die with the run's secret, but treat the zip as the report's private part.
- The long task observer and a trace's DOM snapshots run in every frame, so Chromium
  logs "Blocked script execution in 'about:blank'" for a sandboxed one, such as the
  Control Center's report preview. A page role keeps that line out of its script errors
  and reports how often it came as one soft finding.
- `--repeat N`, 1 to 50, runs the scenario N times into `DIR/run-NN/`, one contained E2E
  unit run after the other, and waits up to 30 min while another run holds the unit. A
  `DIR` that already holds a `run-NN` is refused. `DIR/summary.html` and `summary.json`
  give pass, fail or not run per run, the failed steps and hard findings, and
  min/median/p95/max of every step's duration and every top-level page action. A run
  passes only with exit code 0 and an ok report; one without `report.json` did not run.
  A shot's tail shows there long before it fails. `--load cpu:K`, K from 1 to 4, starts
  K busy workers inside the run once the stack is up, under `tools/e2e_child.py`: they
  share the unit's 2-CPU quota with the relay and the browser. Load outside the unit
  starves it through its weight instead and is not offered.

How to hunt: run the flaky scenario with `--repeat 12 --no-skips`. Read `summary.html`
for the failing step and the tail of its actions, then that run's report: the slow
action, its `host` and `cpu`, the failure class and the snapshot. A rare failure seen
once is not reproduced until a test forces its cause, and clean runs after a fix prove
nothing on their own. Do not edit `tools/event-sim/` while a set runs, because each run
imports it anew: hunt from a separate worktree.

Each run owns one work dir under `/tmp/racecast-sim-*`, with the app home and, through
`TMPDIR`, the browser's temporary files, and removes it at the end. While it exists,
`OUT/work-dir.txt` names it; an `OUT` that still names one is refused. After a killed
run, remove that path. Never clean up `/tmp/racecast-sim-*` or
`/tmp/playwright-artifacts-*` by pattern: another agent's run may be using its own.

### Role drivers (`roles/party.py`, `roles/drivers.py`, `drive.py`, #1233)

One class per party. A page role opens its page in its own browser context on its first
action and acts on the controls a person uses; the drivers have no page.

| Role (`role =`) | Surface | Actions (`do =`) |
|---|---|---|
| `producer` | Control Center `/` | `open`, `pick_track` (track), `race_settings` (formation, finish_mode, laps), `start_event` (stint, qualifying), `stop_event`, `relay_restart` |
| `director` | Director Panel `/panel` | `open`, `approve_link` (stint; `area = "schedule"` approves it in the Schedule area's pending list, which also holds the stint on air and a qualifying link), `next`, `scene` (scene: a program key such as Stint, Split, Standby, Intermission), `graphic` (name, scene, on), `pov` (on), `hud` (field, value), `mode` (mode), `start_part`, `end_part`, `red_flag` (on), `show_dashboard` (on), `cue` (text, target, level), `chat` (text, name), `play` (media: intro, trailer or outro; returns at the end of the loop the clip plays, from OBS's `mediaDuration` and `mediaCursor` read after the air light: the panel shows no media state and the clips loop), `timer` (duration H:MM:SS, typed into the Timer SET… prompt), `timer_key` (key: START or RESET), `view` (area) |
| `race_control` | `/console/race-control` as `Sim Race Control` | `open`, `approve_driver` (name, entry), `approve_drivers` (one by one), `approve_all` (Approve all waiting; result `approved`, `skipped`, `names`), `reject_driver` (name), `correct_driver` (name, entry, stint: changed on the waiting request, then approved), `compound` (entry, code), `compounds` (code, skip: every entry of the tyre table; result `changed`, and `kept` for an entry already on it, whose select sends nothing), `race_start`, `show_standings` (entry, seconds: the frozen standings tab of its disconnect event, kept in view `seconds` (default 8) once it lists rows, then closed; result `rows`), `final_lap`, `finish`, `red_flag`, `release`, `retire` (entry), `rejoin` (entry), `estimate` (entry, ref, seconds, ahead), `correction` (entry, metres, pits, reason), `set_time` and `add_lap` (entry, time; qualifying mode), `note` (text, target), `confirm_dc` (entry), `class_laps` (class, laps), `stint_laps` (laps: `{label = count}` in the Race area's Stint laps), `take_plan` (buffer: waits for the red flag's restart plan, enters the buffer minutes and takes the suggestion; result `laps`), `view` (area) |
| `commentator:<crew name>` | `/cockpit` with that person's token; the name must be in `[[event.commentators]]` or a Commentator of a `[sheet.tabs] Crew`, checked at load | `open`, `submit_link` (url, stint), `chat` (text) |
| `viewer` | `/console/timing` on the event's timing link | `open`, `read` (cars) |
| `driver` | `/console/drive` on the event's invite link, 390x844, no console token | `open`, `register` (entry, name, stint when the event registers per stint: its number picks the invite's segment label, the key the `drivers` role uses too; the code the page shows goes to the `drivers` role through `ctx.driver_codes`, never into a result) |
| `probe` | `tools/probe-stage2.py` as the runbook runs it, and the real `racecast-sender.py` (#1116) | `watch`, `mark` (text), `kit` (sheets into the role's temporary folder), `race_reset`, `spectator` (name, position, cars: the sender with the probe key against a simulated PS5; needs `[event] probe = true`), `stop_spectator`, `check` (entry, segment, name: registers and runs `--check`), `report` (`collect` and `report`; its result is `probe_stage2.facts`) |
| `drivers` | API: invite registration and `/console/drive/ingest` | `register` (cars, stints or segment: one invite label such as `Qualifying`, each driver registering once for it, positions: 0x84 from the road order, skip: `[{entry, stint}]` seats the `driver` page registers; such a seat sends with the code that page showed, and `start` or `lobby` fails while it has none), `start`, `pit` (entry, seconds), `box` (order, leader_first: a car reaching the pit entry before the first of the order drives on another lap, laps: the lobby's lap count, so no car boxes before the end of its lap `laps - 1`), `lobby` (stint), `release` (gaps: the rows of a race reading, for a keep restart), `drop` (entry), `rejoin` (entry, seconds), `stop` (entries) |

`hold` keeps any page role's page in view for `seconds` (at most 300) inside the action, so
the film plays that stretch in real time under the step's caption.

An action is a mechanism. Which one follows which is the scenario's business, never a
role's: `box`, the director's `next`, `lobby` and `release` are four steps, not one "swap".

UI or API:
- Producer, director, Race Control, commentator, viewer and driver act through their page only.
  `Surface.control` requires a control to be visible and enabled (else `StepError`), notes
  one outside the 1440x900 viewport as a soft finding and scrolls it into view.
  `Surface.answered` waits for the page's own request to be answered (HTTP 400 and up is
  hard), `Surface.until` for the page to show the result (soft when slower than 2 s; a
  job or a feed start is slow by nature and exempt). The desk's armed buttons (Finish, RED FLAG, Restart released, Retire, Race start, Approve all waiting) get both clicks, and an error on the desk's
  status line fails the step.
- The panel's RED FLAG and graphic keys toggle from the OBS state the panel polled last
  (every 2 s), so a press right after a change, its own included, repeats the old command.
  `red_flag` and `graphic` with `on` wait for the panel's next `/obs/state` answer and for
  the light to read the opposite before they press. `pov` toggles on the relay and needs
  no wait.
- `scene` presses a program key, so Split, Standby and Intermission are scene steps.
  `hud` picks a value of the Configuration vocabulary (`""` clears), `mode` accepts the
  page's native confirm dialog.
- `start_part`/`end_part` type the phrase the panel shows. They need `[event] parts = N`
  (`qualifying_part = true` adds the Q Part): the stack then serves a Producer tab, a
  Channel tab and a made-up stream key per Part, without which the panel hides the Part
  control and shows OFFLINE/GO LIVE instead. Ending the last Part stops the event.
- `producer.start_event` on a stopped event starts a fresh relay. `relay_restart` and a
  fresh start are detached relays. A restart, and a plain Start event after a crash, comes
  back on the stint that was on air, on Feed A, with the feeds stopped (#1270): arm Feed A.
- `view` opens a desk or panel area, so a screenshot of an element in it can be taken.
- Not offered: switching the profile (the app home holds the demo profile only).
- Drivers act through the API, as `racecast-sender.py` does for a real driver. The
  `driver` role registers one driver through the invite page's form, so the page is seen. Each driver registers as its own
  device (`X-Forwarded-For` from 198.51.100.0/24, which the relay honours from loopback as
  behind Funnel), since one client may hold 8 waiting requests and register 30 times a
  minute. `register` only asks: Race Control approves on its desk.
- `api`, `obs` and `stream` stay for what no party does: probes, the simulated OBS
  operator, a channel's failure.
- A page a role just opened has not polled yet: `read`, `approve_driver`, `approve_drivers`
  and `scene` wait for the rows, requests or keys before they act.
- `producer.relay_restart` starts a detached relay, no child of the run: `Stack.stop`
  runs the app home's own `relay stop` first. A run killed hard leaves it to the local
  containment unit.

Without a browser a page role raises `roles.Unavailable`: the engine marks the step
`skipped`, records a soft finding and runs neither its checks nor its shot, and steps
chained to it with `after:` or `${...}` are skipped too. So chain every party step with
`after:`; `tests/test_event_sim_roles.py` holds `smoke.toml` to that.

Event data a scenario sets for the roles (`[event]`): `track` (a GT7 layout id), `track_length_m`
(default 1200, at least 860), `cars` (1-15, the roster in the Configuration tab's Teams
column, `drive.TEAMS`), `seed`, `parts` (0-9), `qualifying_part` and `probe` (the relay's
telemetry probe ingest on). The stack then gives the temporary app home a racing
line (`drive.write_track_data`, since the relay's race needs a downloaded line and the
network guard blocks `gt7-data update`): a copy of the checkout's own `runtime/gt7/signatures.json`
when it holds the layout's line within 3 % of `track_length_m` (never committed), else a
stadium-shaped lap as the layout's row; the drivers drive the same line (`ctx.track_trace`).
It also gives one placeholder graphic (Start event refuses a machine without any), the RED
FLAG banner in the Race Control vocabulary, each team a brand of `src/assets/brands` and a
tile colour (`drive.TEAM_LOOKS`), the Setup tab's Team 1-3 and the Overlay tab's round
title and the layout's country. The Channel tab's `sim://broadcast` is a live stub
channel, and `fakes/chat_sim.py` stands in for its YouTube live chat
(`RACECAST_SIM_BROADCAST_CHAT_URL`): the relay's own reader shows a viewer message every 20 s. The run prints, and the report's `setup.track_line` keeps, which line the cars drive. `ctx.event_link("invite" |
"timing")` reads the ids Start event wrote into the app home. They are credentials.
`redaction.py` is the one filter for them: `Engine.data` passes everything the run writes
(step errors, results, check details, findings, and so the printed lines and both report
files) through `ctx.scrub`, which removes `t=`, `i=` and `l=` values and driver codes by
shape and every token, link id and the console secret of the run by value. It reads the
event's current invite and timing ids itself (`ctx.learn`, after every step and before a
scrub), so an id no role asked for, or one renewed mid-run, is covered too. So an
exception text, a navigation error or an `api` response body cannot carry one out. Every
page screenshot blacks out the link texts (`selectors.LINKS`).

`drive.py` is the driver library behind `tools/driver-replay.py`: `packet`, `Line`,
the synthetic track, `Field` (cars and their senders, stepped with `step(now, dt)` on the
caller's clock, so a seed fixes every packet), `Client` (loopback unless told otherwise) and
`Runner` (a field on real time in one thread, joined in `close`; it steps under its lock
and posts the tick's batches outside it, each post bounded by `Client.ingest_timeout`; a
slow tick catches up the lost wall time in tick-sized steps, up to `MAX_CATCH_UP_S`, and
builds a batch only on the last one, because the relay measures gaps in wall seconds).
`register` is all or nothing: on a failure the role holds no driver and names the
registrations the relay already took. A car's GT7 lap counter
reads 0 on the grid and steps at the line; a boxed car stands 22 m off the line; `lobby(n)`
puts stint n's driver into the box on lap 0 beside the old one, `release` sends the cars out
in arrival order, each by its arrival gap. `tests/test_event_sim_drive.py` feeds a field
into the relay's own `race_distance.Race`.

Every selector and desk label lives in `roles/selectors.py` (the desks are due for a
restructure, #1267: that change touches this one file); a test keeps selector fragments out
of the role code.
`t_every_selector_names_something_the_shipped_page_has` fails when a page renames an id,
class, label or button text a role uses, on every OS and without a browser.

`page(role)` facts: `producer` (`summary`, `relay`, `job`), `director` (`on_air`, `up_next`,
`next`, `next_ready`, `next_step`, `red_flag`), `race_control` (`waiting`, `status`,
`status_error`, `lap_line`), `commentator` (`tally`, `on_air`, `pending`, `message`), `viewer`
(`session`, `state`, `fastest`, `rows`, `cars`), `drivers` (`registered`, `cars` with `entry`,
`mode`, `lap`, `driver`, `sending`; `driving`, `standing`, `sending`, `ingest_errors`).

`scenarios/smoke.toml` plays each role once: the producer picks the track, sets the race
and starts the event (the relay's PID and port must stay the stack's), a commentator
submits a link in the cockpit, the director approves it, waits for Feed A to serve and cuts to Stint
(`'comms1' in obs.program_channels`), shows the Standings graphic, sets the HUD's Race
Control field and starts Part 1 (`obs.streaming`), four drivers register, Race Control
approves them, they race, a viewer reads the public timing, Race Control calls the final
lap, the director ends Part 1 and the producer stops the event. Stop event ends the relay, so it is the last step.

### Bundled event scenarios (`scenarios/*.toml`, #1234)

Each plays three stints in three lobbies with one commentator per stint (Comms 1, 2, 3),
checks each commentator's picture on program at their stint, swaps every driver in each new
lobby and ends with Stop event. `tests/test_event_sim_roles.py` holds them to that. Run
times on a maintainer machine, start and teardown included:

- `quick` (4.5 min): Start event, qualifying mode, two Add lap times ranked on the public
  timing, race mode, approval of 12 drivers, a DC confirmed on the desk and rejoined late,
  final lap in the third lobby; asserts the ranking, `no_data` then `dc` (the recording's
  event suspected, then confirmed, and DC on the public timing), the swaps (driver and pit
  stop count) and the finish.
- `full` (about 36 min on GitHub's runner plus about 14 min of qualifying, real time): the
  deck's race day with 14 cars on a Suzuka-length lap, after a qualifying event the day
  before (about 14 min, 10 of them on its timer: Start event with the Qualifying option, Comms 1's qualifying link
  submitted and approved, Comms 1 on air, one driver on the invite page and the others as
  a second `drivers:quali` field registered for the `Qualifying` segment, Approve all, a
  10 min timer the director sets and starts by hand as the cars go out, their laps and
  three added on the desk, the cars in once the timer has run out, Stop event). The race
  event starts in race mode: one driver on the invite page, one request corrected and one rejected, Approve all for
  the other 40, intro and trailer, crew chat, the finished qualifying timer reset and the
  race timer set to the race length, the stint lobbies' lap counts set on the desk, Race
  start on the grid (the timer runs from the first race lap), every car's compound at each
  stint's start, formation, a pit stop with its compound, a DC (a held suspected phase, its
  standings of before the drop), an estimate, a DNF, a late stream link, before each swap
  a next-stint driver on the invite page, the cars boxing behind the leader at the end of
  the lobby's second to last lap, the
  splitscreen once the leader stands in the box and NEXT once every car stands there (a
  soft check bounds that wait to 5 min), the cut back to the stint and the new compounds,
  a red flag with a keep restart whose lobby runs the laps of the plan Race Control takes,
  a pit stop with a compound change, the final lobby finishing by laps with the final lap
  called as the leader starts its last, the outro. Its steps carry
  the walkthrough film's twelve chapters, captions and focus; `tests/test_event_sim_roles.py`
  holds the 50 shot names to their steps, roles and elements, not to their content. It
  runs in the workflow; locally it exceeds the 2 GiB E2E unit. Its 50 screenshots carry
  deck image names (`<slide>-<n>`, e.g. `dc-tower-1`), the race-day subset of the deck's
  151. Not produced: multiclass (`mc-*`), qualifying details (`quali-addlap-*`,
  `quali-flash-*`, `quali-director`, `quali-drivers`, `quali-tower-int`), no-sender and
  takeover (`new-no-sender*`), name overrides and long names (`new-name-override`,
  `new-long-name`), the tyre and fuel columns (`tyres`, `tower-notyre`, `tower-nofuel`),
  the overlay builder (`style-*`), estimates ahead (`est-ahead-*`), lap flashes
  (`new-lap-flash`), public columns (`public-columns`, `new-public-timing`), tower gap and
  interval (`new-tower-gap`, `new-tower-int`), the recording browser (`browse`,
  `new-snapshot`), approval variants (`approval-readiness`, `-correct`, `-dup`,
  `-director`), `pits-after-swap`, `dc-rejoin-mark`, `dc-rejoin-gap`, `swaps-2-early`, and
  further numbers of `dc-decided`, `dc-onair`, `dc-states`, `dc-tower`, `lapped`,
  `new-cockpit-timing`, `new-dnf` and `tower-full`.
- `multiclass` (4 min): Hypercar and GT3 in one lobby; asserts the GT3 leader is
  P2 overall but P1 in its class with class gap 0, the public timing lists both, and a GT3
  lap count set on the desk.
- `late-link` (4 min): Comms 2 submits the stream link during stint 1; asserts it stays
  pending (empty Schedule row, no handover link), the approval writes the row and the
  webhook, and at the change Comms 2 is on program and Comms 1 is not.
- `dc-rejoin` (4.5 min): a sender stops in stint 1; asserts `no_data`, `dc` after Confirm
  DC, the rejoin mark, and in stint 2 the team's new driver racing again behind the field.
- `relay-crash` (5 min): the relay is killed in stint 2 while Part 1 streams; a plain
  Start event brings stint 2 back with the program untouched, and the same driver codes
  count again with no new approval; stint 3 runs to the finish.
- `red-flag` (5 min): the desk's RED FLAG in stint 2 (cover, banner, race held), a
  restart lobby and Restart released; the drivers leave the box by their gaps of before the
  flag (the relay shows no gaps while it holds); asserts every gap within 1 s (the exact order is soft: the paces differ, and cars closer than the 0.2 s driver tick may swap).

- `probe-stage2` (#1116): the stage 2 runbook's producer side. The watch records the
  run into `OUT/probe-stage2/`, a race reset opens block D, three teams change drivers
  twice with 0x84 in their packets and a red flag restart lobby in between, the real
  sender sends as the spectator (`[event] probe = true` sets `RACECAST_TELEMETRY_PROBE=1`)
  and runs its `--check` through the invite; the report step asserts six changes inside
  the window with the anchor carried, four blocks and the restart's session starts.

### Record mode (`--record`, `--obs real`, #1310)

Records a scenario for the event walkthrough video (spec
`docs/superpowers/specs/2026-11-24-event-walkthrough-video-design.md`). The renderer reads
only DIR.

```bash
python3 tools/event-sim/event_sim.py --scenario full --out OUT --record DIR              # simulated OBS
python3 tools/event-sim/event_sim.py --scenario full --out OUT --record DIR --obs real   # program from a real OBS
python3 tools/event-sim/event_sim.py --restore-obs   # macOS: put back the OBS configuration a killed run left
```

- Pages: each page role's browser context records its page as a Playwright video into
  `DIR/pages/<role>/` at the page's own viewport. Only in a record run, two init scripts go
  into each context: a pointer with a click mark (`pointer-events: none`, top frame only)
  and a stylesheet, adopted before the first paint, that paints every `selectors.LINKS`
  match black. The mask changes paint only, so the roles still read the masked text. A
  click, fill or choice first moves the mouse to the control in `POINTER_STEPS` moves.
- `DIR/timeline.json`: `steps` (each step that ran: `id`, `role`, `action`, `status`,
  `start`, `acted`, `end`, plus `caption`, `chapter`, `focus` and `card` when the scenario
  step has them), the scenario's `intro` and `outro` when it has them and `recordings` (`view`, `kind` page or program, `file` relative to DIR, `size`,
  `start`). A tab a step opens is its own page recording: Race Control's Show standings tab
  is the view `race_control:standings`, which a scenario's `focus` may name. `acted` is when the action returned; from there to `end` the step waited for its
  expects, which the film treats as waiting time. All times are seconds since the engine started; a recording started before has a negative
  start. It goes through the run's redaction.
- Feeds and clips: the stream stub runs the `card` look, and before the relay starts the
  run renders every title in one Chromium session (the cards of the scenario's
  commentators, the clip titles and a demo stand-in for every graphic the collection reads,
  into the app home's `runtime/demo/graphics`) and draws `intro.mp4`, `trailer.mp4` and `outro.mp4`
  (9 s, the title image over the card colours, silent audio) into the app home's
  `runtime/demo/media`, where the localized collection reads them.
- Limits: `record.py` states `RECORD_MEMORY` (6 GiB: on macOS `full` with page videos
  peaked at 2.75 GB for the whole tree and the second OBS at 0.74 GB, which `--obs real`
  starts as a descendant of the run), `RECORD_TASKS` and `RECORD_CPU_PERCENT`. Before the unit or the stack
  starts, the run is refused off Linux and macOS, without Playwright, with an ffmpeg that
  lacks one of `record.FILTERS` (all in a stock build; `drawtext` is not needed), on a Linux host whose MemAvailable is
  below the memory plus `e2e_safety.MEMORY_MARGIN`, and on a Mac with less memory. On local
  Linux the run's unit gets these limits (`e2e_safety.enter(..., limits=)`, carried to the
  owner watcher). Outside a verified unit (macOS, a hosted runner) a watchdog samples the
  run's whole process tree every `WATCH_INTERVAL_S`, walking ppid from the run, since
  Playwright's driver, the browsers and their ffmpeg run in their own process groups. Past
  the memory limit it sends the run a SIGINT (the report gets a hard finding, the cleanup
  still quits OBS and restores its configuration) and kills the descendants when the tree is
  still over the limit `WATCH_GRACE_S` later. Once the run is in its cleanup it is no longer interrupted,
  only killed past the grace. `--record` with `--repeat` is refused.
- `--obs real` (needs `--record`; `obs_real.py`): OBS as a second instance (`--multi
  --minimize-to-tray --disable-missing-files-check`, on macOS also
  `--disable-shutdown-check`) with the Sim profile (1920x1080, 30 fps, mkv into
  `DIR/program/`), the demo collection under the profile's `OBS_COLLECTION` name with its
  tokens pointing into the app home and every capture input turned into an empty media
  input of the same name, and its WebSocket on a free port with a random per-run password
  (obs-websocket listens on every interface; the run's client and the relay connect to
  127.0.0.1 with it, through `RACECAST_SIM_OBS_WS_PASSWORD`, and the report redacts it). OBS
  gets port and password on its command line (`--websocket_port`, `--websocket_password`,
  obs-websocket 5 turns authentication on and saves neither), so no file holds the password;
  OBS logs its command line, so the copied `obs.log` is redacted. Its
  stream target is the run's RTMP sink (`fakes/rtmp_sink.py`): an ffmpeg that listens on a
  free loopback port, reads a Part's stream and drops it, and listens again for the next
  Part. It runs under `e2e_child.py` like the services, starts before OBS and stops after it.
  The run sets `RACECAST_SIM_RTMP`, so a Part's start points OBS at the sink instead of the
  Channel tab's platform (`obs_ws.stream_service_payload`); nothing leaves loopback, and
  `obs.streaming` reads a real stream. On Linux its configuration lives under the work dir (`XDG_CONFIG_HOME`),
  so a running OBS of the user stays untouched. On macOS OBS ignores that: the run refuses
  while any OBS runs (`pgrep -x OBS`), copies `~/Library/Application Support/obs-studio` with
  `ditto` to a temporary copy, verifies it, renames it to `obs-studio.racecast-sim-backup`
  and only then writes `obs-studio.racecast-sim-backup.complete`; a copy that breaks off is
  removed. After the run it copies the backup next to the folder, verifies the copy, swaps
  it in (a folder already gone is just replaced) and compares every file by SHA-256. The
  backup is removed only when they match. The quit and the restore hold SIGINT, SIGTERM and
  SIGHUP until they finished; a restore that cannot finish prints the way back. A backup
  left over (a run killed from outside) refuses the next run and names the recovery:
  `event_sim.py --restore-obs` refuses while OBS runs and refuses a backup without its
  completion mark (the run that made it never changed the folder), restores the folder,
  verifies every file and removes the backup only when they match. Its WebSocket listens
  before OBS has loaded and answers 207 (not ready) meanwhile; the run asks again until the
  start timeout (60 s). The run starts the recording once the stack is up and stops it after
  the pages closed, each time until `GetRecordStatus` reports it; the program's start on
  the timeline is the moment OBS answered StartRecord. OBS starts only after the relay answers on its port, because a relay page that
  finds the relay down keeps Chromium's error page; after the start the run reloads every
  relay page once (`refreshnocache`) and notes it in `obs-state.json` (`refreshed`). The RTMP
  sink starts earlier. Evidence for a live run, in `OUT/logs`: `obs-state.json` (`when` start, and
  stint once the first `Stint` scene is on air: `program_scene`, its items with their
  visibility, each browser source's url, width and height), `hud-source-start.png` and
  `hud-source.png` (the HUD source alone at those moments) and `obs.log` (the log files this
  OBS wrote, copied before the restore removes them on macOS), all redacted. After the start the run checks that OBS's first relay page opens the run's
  URL and refuses an OBS that loaded another collection. Then OBS gets
  SIGINT on its own PID only (OBS 32.1 and later save and close on SIGINT; obs-websocket has
  no quit request), SIGTERM 30 s later, and only after that is its process group killed, as
  a finding. A signal to the whole group kills the browser's GPU process first. The probes
  and the `obs` role read OBS through `ObsMirror`, an `ObsState` of the same collection
  that follows OBS's program scene, item visibility and outputs. `program_channels` reads
  each visible feed from OBS's own BMP screenshot of that feed source (`source_frame`, at
  most every 0.5 s per source), never the relay's port: on program the Overlay's panel
  covers the identity strip, and a black or failed picture names no channel.

### Event walkthrough video (`tools/build-event-video.py`, #1312, #1313)

The film is a maintainer run in two steps: record a scenario, then render the recording.
Spec: `docs/superpowers/specs/2026-11-24-event-walkthrough-video-design.md`.

```bash
python3 tools/event-sim/event_sim.py --scenario full --record DIR --obs real --out OUT
python3 tools/build-event-video.py DIR [--music FILE] [--workers N] [--from T] [--to T]
```

- Machine: recording runs on the maintainer Mac (Apple M3, 8 cores, 16 GB). The Linux box
  broke the CPU, task and memory limits of the local E2E unit in gate G4. Record mode
  limits itself to 6 GiB, 4096 tasks and 600 % CPU (`record.py`). `full` takes about
  43 minutes and writes about 1.2 GB of page videos.
- OBS: `--obs real` needs OBS 33 on macOS 27.2; OBS 32.2.2 crashes at launch there. Your
  own OBS stays closed for the whole run. On macOS the run refuses while any OBS runs, and
  it backs up `~/Library/Application Support/obs-studio` and restores it afterwards. A run
  killed from outside leaves the backup, and the next run refuses until
  `event_sim.py --restore-obs` puts it back. Without `--obs real` the OBS stand-in runs,
  nothing records the program, and the film leaves the program view out.
- Render: the renderer transcodes each recording once to all-keyframe VP8 (about 2.5 GB
  per recording of `full`, about 25 GB in all) and checks the free disk before it starts.
  `--from`/`--to` (seconds, `m:ss` or `h:mm:ss`) render a part of the film, named
  `event-walkthrough-<a>s-<b>s.mp4`. A killed render resumes from its finished segments.
  Without `--music` the film is silent.
- Title cards: the intro (5 s), a card before each chapter whose step has one (3 s) and
  the outro (6 s) add film time and play no source time. A recording whose timeline has no
  card takes them from `tools/event-sim/scenarios/<scenario>.toml`, so an older recording
  gets the current texts; a step card only lands on a recorded step that opens the same
  chapter.
- Outputs under `runtime/event-video/<name of DIR>/` (or `--out`): `event-walkthrough.mp4`,
  `chapters.txt` and `captions.vtt`, plus `work/` with the transcodes and segments.
- Films, clips and recordings are never committed; the user uploads the film.
- Pipeline check: every dispatch of the manual Event simulation workflow also runs the
  `event-video` job. It records `smoke` with `--obs sim` and renders its first five
  seconds, then uploads the part, `chapters.txt`, `captions.vtt`, the timeline and the
  report. `tests/test_event_sim_workflow.py` pins that job.

## Driver replay (`tools/driver-replay.py`, #1115)

Up to 15 simulated senders for a running relay: they register on the event's invite,
approve themselves on the tailnet route `/drivers/requests/approve` (so the relay must
listen on the tailnet or loopback, as the default `--relay` does) and post encrypted GT7 packets along the picked layout's line through the real ingest
route, with lap times, best laps and fuel. `--gap` sets the start spacing (small values
give a dense pack for the track map). Needs a running event, whose `racecast event start`
creates the invite, and the same `racecast event track ID`. `event invite --new` only
renews the invite and otherwise refuses with "No event is running". The screenshots of the dashboard, desks and cockpit use it with
the demo profile. Never point it at a league during an event: its senders take real
entries and its laps reach the race and the fastest lap; a profile other than `demo`
needs `--force`. `--pending N` registers N more drivers for the second stint and leaves
them waiting for approval (the desks' Driver requests). Its cars and senders are the event
simulation's driver library (`tools/event-sim/drive.py`). `tests/test_driver_replay.py`
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

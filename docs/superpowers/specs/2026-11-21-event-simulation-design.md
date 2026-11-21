# Event simulation

Status: draft for review. Decided in review: three stints and lobbies with one commentator
each, and the `full` scenario produces the deck's image set.

## Problem

A race day involves the producer, the director, Race Control, two or three commentators,
up to 15 drivers and the viewers. Each of them works on their own screen, and their steps
depend on each other: a commentator submits a stream link, the director approves it, the
feed switches at the stint change, Race Control approves drivers and runs the race, the
timing reaches the tower and the public page. Today the e2e harness checks single surfaces
against synthetic data, and the race simulations behind the slide deck live as scratch
scripts outside the repository. Nobody can replay a whole event from start to stop on
demand, so workflow and operating errors show up first on a real race day.

## Goal

One tool plays a scripted event from Start event to Stop event through the real pages, the
real relay and the real Control Center, with every external party simulated. It:

- fails on hard errors (a workflow that breaks, a wrong state, a missing control),
- writes a report with a timeline, screenshots and soft findings about operation,
- runs the same way on a maintainer machine and in CI, started deliberately,
- names its screenshots so the slide deck and the wiki images can be taken from one run.

Not a goal: a load test, a test of real OBS, YouTube, Twitch, Discord or Tailscale, or a
required check on pull requests.

## Overview

```
tools/event-sim/
  event_sim.py          entry point: run a scenario, write the report
  scenario.py           TOML loader and validation
  engine.py             clock, step runner, checks, report
  roles/                one driver per role (Playwright pages or API)
  fakes/                sheet simulator, webhook sink, stream stub, stateful obs-sim
  scenarios/quick.toml, full.toml, multiclass.toml, late-link.toml, dc-rejoin.toml,
            relay-crash.toml, red-flag.toml
.github/workflows/event-sim.yml
```

`python3 tools/event-sim/event_sim.py --scenario quick --out DIR` stands up the fakes, the
relay and the Control Center in simulation mode on the demo profile, runs the scenario and
writes `DIR/report.html`, `DIR/report.json` and `DIR/shots/`. Exit code 0 means every hard
check passed.

## Simulation mode (product change)

`RACECAST_SIMULATION=1` (also `--simulation` on `racecast`) is a documented mode of the
shipped product, so a producer can rehearse with the demo profile before the first event.

- `event start`, `event stop`, `event takeover` and the Control Center's equivalents do not
  launch OBS, Discord or Companion and do not touch Tailscale or the Funnel. Every place
  that would is reported as "skipped (simulation)" in the job log.
- OBS WebSocket, the Sheet, the Sheet push webhook, Discord webhooks and stream pulls are
  read from simulation endpoints the tool passes in (`RACECAST_SIM_*` variables).
- The mode refuses a profile other than `demo` (or a profile created by the tool) unless
  `--force` is given, like `tools/driver-replay.py`.
- Everything else, including the relay, the console auth, the timing and the recording,
  runs unchanged.

## Simulated parties (`fakes/`)

- **Stateful obs-sim.** Extends `tools/obs-sim.py`: it keeps the scene list, the program
  and preview scene, transitions, streaming and recording state, inputs with settings and
  mute, scene item visibility, and emits the matching obs-websocket events. Checks read its
  state over a small local HTTP endpoint. Program screenshots render the current scene name
  and the visible feed's test pattern.
- **Sheet simulator.** Serves the Schedule, Crew, Configuration, HUD, Graphics and Channel
  tabs as CSV from the scenario (the loopback pattern of `tools/e2e.py`). A fake
  `SHEET_PUSH_URL` accepts the relay's writes (HUD fields, schedule approvals, timer) and
  applies them to the served tabs, so a later read sees them, as with the real Apps Script.
- **Webhook sink.** Stands in for every Discord webhook and records each message with its
  time, for checks such as "one approval message per submission".
- **Stream stub.** Replaces `yt-dlp`/`streamlink` for scenario channel URLs with an ffmpeg
  `lavfi` test pattern that burns in the stint and commentator name and carries a tone, so
  checks can tell which commentator a feed shows. Scenario options cut, stall or freeze a
  stream for the v2 failure scenarios.
- **Drivers.** `tools/driver-replay.py` and the scratch race simulator become a library the
  driver role calls: registration on the invite link, telemetry along the racing line with
  a fixed seed, pit stops, swaps, disconnects, a sender that stops, finish.

## Roles (`roles/`)

Each role drives its own browser context, or the API where no person clicks:

| Role | Surface | Example actions |
|---|---|---|
| Producer | Control Center (`racecast ui`) | Start event, Stop event, profile, relay restart |
| Director | Director Panel `/panel` | Start/End Part, scene and graphics, HUD, splitscreen, cues, intermission, approve stream links, red flag, show dashboard |
| Race Control | `/console/race-control` | approve drivers, corrections, estimates, DNF, final lap, red flag, Add lap in qualifying |
| Commentator 1..3 | Cockpit `/cockpit` | read tally, chat, notes, submit stream link, watch program |
| Driver 1..15 | API (sender) | register, send, pit, swap, drop, rejoin |
| Viewer | `/console/timing` | read the public timing |

A role action names a control by its visible label or test id, waits for it to be visible
and enabled, clicks it and waits for the page's confirmation. A control that is missing,
hidden or disabled is a hard failure.

## Scenario format

TOML, read with `tomllib` (Python 3.11+, no new dependency). A scenario sets the event (track,
mode, stints, laps, classes, roster, crew, commentators) and lists steps on a timeline:

```toml
[event]
track = "6e378b"
mode = "endurance"
stints = 3                    # three stints, each its own GT7 lobby
laps_per_stint = 3            # quick profile; full uses real values
seed = 7

[[step]]
at = "0s"
role = "producer"
do = "start_event"
expect = ["obs.scene == 'Standby'", "relay.status.ok"]

[[step]]
at = "after:start_event+20s"
role = "commentator:Comms 2"
do = "submit_link"
args = { channel = "sim://comms2" }

[[step]]
at = "after:submit_link+10s"
role = "director"
do = "approve_link"
args = { stint = 2 }
expect = ["webhook.count('approved') == 1", "sheet.schedule[2].link == 'sim://comms2'"]

[[step]]
at = "lap:leader=3"
role = "race_control"
do = "final_lap"
```

- `at` is a duration since start, an offset after another step, or a race condition
  (`lap:leader=N`, `state:finishing`).
- `expect` lists hard checks in a small expression language over named probes: `obs`,
  `relay`, `sheet`, `webhook`, `race`, `page(role)`. Each check has a default timeout
  (5 s) that a step may override.
- Steps may name a screenshot (`shot = "dc-tower-1"`, with a role and an optional element),
  which lands in `shots/` under that name.
- Every bundled scenario runs three stints, each in its own GT7 lobby, with one
  commentator per stint (Comms 1, 2, 3). The commentator changes only at a stint change,
  never within a stint.
- The bundled scenarios: `quick` (under 20 minutes: approval, a short qualifying with Add
  lap, three short stints with driver swaps at both stint changes, one DC, final lap in
  the third lobby, finish, stop), `full` (the run 10 event in real time, extended to
  three stints), `multiclass` (Hypercar and GT3), and the failure scenarios
  `late-link`, `dc-rejoin`, `relay-crash` (kill the relay, plain Start event, codes and
  stint resumed) and `red-flag` (keep restart). v2 adds `feed-drop` and `takeover`.

## Checks and findings

- **Hard (exit code 1):** an HTTP error on a step, an `expect` that stays false past its
  timeout, a missing or disabled control, a console error on any page, a webhook message
  missing or duplicated, a relay error in its log, a feed that shows the wrong
  commentator.
- **Soft (report only):** a page needing longer than 2 s to show a new state, an action
  without visible feedback, a control outside the viewport at the role's screen size, the
  layout rules of `tools/e2e.py --visual`, timing deviations within tolerance.
- **Tolerances:** gaps within 1 s, positions exact, order exact at the finish, state
  changes within 5 s. A deviation caused by machine load is soft as long as the order is
  right.

## Report

`report.html` shows one row per step: time, role, action, result, duration, screenshot
thumbnail, findings. A summary lists hard failures first, then soft findings by role.
`report.json` holds the same data for tooling. `shots/` keeps every named screenshot at full
size. The `full` scenario names its screenshots after the slide deck's image set, so a
deck update is one run of `full` followed by a copy of `shots/`; this replaces the scratch
shot lists used so far.

## CI

`.github/workflows/event-sim.yml`, `workflow_dispatch` only, inputs `scenario` (default
`quick`) and `profile` (`quick` or `full`). Ubuntu runner, Playwright Chromium and ffmpeg
installed, timeout 30 minutes for `quick` and 120 for `full`. The report and the shots are
uploaded as an artifact kept 14 days. It is not a required check.

## Security

- Simulation mode never runs against a league profile without `--force`, and it never opens
  an outbound connection: all endpoints are loopback fakes.
- The fakes bind to 127.0.0.1 on free ports; the relay keeps its usual binding rules.
- Tokens minted for the roles are written only to the run's temporary directory.

## Delivery

One epic with a sub-issue per pull request:

1. Simulation mode (`RACECAST_SIMULATION`, skipped launches, `RACECAST_SIM_*` endpoints,
   demo-profile guard).
2. Stateful obs-sim.
3. Sheet simulator and webhook sink.
4. Stream stub.
5. Scenario engine and report.
6. Role drivers.
7. Bundled scenarios (quick, full, multiclass, late-link, dc-rejoin, relay-crash, red-flag).
8. `event-sim.yml` workflow.

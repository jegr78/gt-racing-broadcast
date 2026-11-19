# Race Recording, Disconnect Events and the Leader Gap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The timing tower shows the gap to the leader, the desks show exact gaps for lapped cars, and the relay records the live race once per second with disconnect events that open a frozen standings page.

**Architecture:** `race_distance.Race.standings` gains exact-time and quiet fields. A new stdlib module `race_recording.py` holds a SQLite store per event (with a bounded writer thread) and a pure `Recorder` that samples, auto starts and stops, and turns quiet cars into disconnect events. `driver_race.Service.tick` feeds the recorder; the relay exposes it through the existing `/drivers/race/*` route family, which `console_policy` already maps to the Director (`/console/drivers/*`) and Race Control (`/console/race-control/drivers/*`) desks.

**Tech Stack:** Python 3 stdlib (`sqlite3`, `threading`, `csv`), vanilla JS in the shared desk and the OBS overlay.

**Spec:** `docs/superpowers/specs/2026-11-18-race-recording-design.md`

## Global Constraints

- Stdlib only; relay-side modules never import `config.py` or `http_util`.
- Undelayed data never reaches `driver_race.public_view`: `gap_time_s` and `interval_time_s` stay out of `_PUBLIC_ROW`.
- Every new `/console` route is gated in `src/scripts/console_policy.py` to `director` (under `/console/drivers/`) and `race_control` (under `/console/race-control/drivers/`); no new route reaches a program change.
- Sampling rate: one sample per second (`SAMPLE_EVERY_S = 1.0`), from live standings, never from the delayed snapshot.
- Disconnect threshold: `race_distance.NO_DATA_S` (5 s); no second constant.
- Snapshot offsets offered: 5, 10, 15, 20, 30 s before the last packet, default 10 s.
- Race mode only: nothing records while the relay is in qualifying (`Service._enabled()` false).
- Writes never block the tick: bounded queue, a full queue drops the sample and counts it.
- English UI and docs; no dashes as sentence separators in prose; minimal comments.
- Changed UI surfaces refresh their wiki screenshots in the same task: `driver-dashboard.png`, `director-panel-drivers.png`, `console-race-control.png`.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A relay restart mid-race: the recorder must reopen the same event file and keep appending, and open suspicions must survive (test in Task 3).
2. A car that drops and returns within 5 s: no event at all; one that returns after 7 s before anyone decides: `dropout` with its duration, still confirmable (test in Task 3).
3. A driver change in endurance: the outgoing car standing in the box inside the change window raises nothing; the same car going quiet on track inside the window does (test in Task 3).
4. A red flag that silences the whole field: no suspicion while `red_flag` is set (test in Task 3).
5. A snapshot request before the first sample or after the last one: nearest sample, or a clear `404` when the recording is empty, never a 500 (tests in Tasks 2 and 5).

---

### Task 1: Exact gaps and quiet fields in the standings

**Files:**
- Modify: `src/scripts/race_distance.py` (`_behind` neighbourhood, `standings`)
- Test: `tests/test_race_distance.py`

**Interfaces:**
- Produces, per row of `Race.standings(now)["entries"]`:
  - `gap_time_s: float | None`: seconds behind the leader by distance history, set also when `lapped >= 1`; `0.0` for P1; `None` when the car is quiet, either car is `waiting` under a red flag, or `_time_at` has no history for that distance.
  - `interval_time_s: float | None`: the same against the car ahead; `None` for P1.
  - `quiet_s: float | None`: `now - car.t` of the active car, rounded to 0.1; `None` before any sample.
  - `in_box: bool`: `car.in_box`, reported even while `status` is `no_data`.
- Produces, at the top level of `standings(now)`: `change_window: bool` (`self._window_until is not None and now <= self._window_until`).
- Existing `gap_s`, `interval_s`, `lapped` unchanged.

- [ ] **Step 1: Write the failing tests** in `tests/test_race_distance.py`, using the file's `Car`/`_drive`/`_row` helpers:
  - `t_lapped_car_keeps_an_exact_time_to_the_leader`: drive two cars until car B is one lap down; assert `_row(...)["lapped"] == 1`, `["gap_s"] is None`, and `["gap_time_s"]` within 0.5 s of `L / v_a + (time of B's distance gap at A's speed)` (compute the expected value from the speeds used), and `["interval_time_s"] == ["gap_time_s"]` for P2.
  - `t_leader_has_zero_gap_time_and_no_interval`: P1 row has `gap_time_s == 0.0` and `interval_time_s is None`.
  - `t_quiet_car_reports_its_silence_and_box`: stop sending for car B in the box for 6 s; its row has `status == "no_data"`, `quiet_s >= 6.0`, `in_box is True`, `gap_time_s is None`.
  - `t_standings_report_the_change_window`: `standings(now)["change_window"]` is `False`, then `True` after `race.open_change_window(now)`, then `False` after `now + rd.CHANGE_WINDOW_S + 1`.
- [ ] **Step 2: Run** `python3 tests/test_race_distance.py`. Expected: the four new tests fail with `KeyError`.
- [ ] **Step 3: Implement** `Race._time_behind(self, ahead, ent) -> float | None` in `race_distance.py` (the `_behind` seconds rule without the lap cut-off), fill the four row fields and `change_window` in `standings`.
- [ ] **Step 4: Run** `python3 tests/test_race_distance.py && python3 tests/test_driver_race.py`. Expected: `ALL PASS` twice; assert in `test_driver_race.py` that `public_view` rows carry no `gap_time_s` key (add `t_public_view_has_no_exact_gaps`).
- [ ] **Step 5: Commit** `feat(telemetry): exact gaps for lapped cars and quiet fields in the standings`.

### Task 2: The recording store

**Files:**
- Create: `src/scripts/race_recording.py`
- Test: `tests/test_race_recording.py` (new runnable script in the repo's `t_*` style)

**Interfaces:**
- `POINTER = "race-recording.json"`; files are `race-recording-<UTC yyyymmdd-hhmmss>.sqlite` in the profile runtime dir.
- `rotate(runtime_dir) -> None`: removes the pointer (a fresh event); missing pointer is fine.
- `open_current(runtime_dir, wall) -> Store`: opens the file the pointer names, or creates a new file and pointer.
- `class Store`:
  - `add_sample(t_wall: float, doc: dict) -> None`
  - `sample_at(t_wall: float) -> dict | None`: the sample nearest `t_wall` (`{"t": ..., **doc}`), `None` when empty.
  - `span() -> tuple[float, float] | None`
  - `add_event(ev: dict) -> int`, `update_event(id: int, **fields) -> None`, `events() -> list[dict]` (newest first)
  - `set_meta(key, value)`, `meta(key) -> str | None`
  - `csv_rows() -> Iterator[list]`: header then one row per (sample, entry); columns `time_utc, pos, entry, driver, laps, gap_time_s, interval_time_s, lapped, status, compound, pit_stops, correction_m, rejoin, gt7_position`.
  - `close()`
- `class Writer(store, maxsize=120)`: a daemon thread draining a `queue.Queue(maxsize)`; `put(fn) -> bool` returns False and increments `dropped` when full; `flush(timeout)` for tests; `stop()`.

- [ ] **Step 1: Write the failing tests**:
  - `t_samples_round_trip_and_nearest_lookup`: add samples at t=100,101,102; `sample_at(101.4)["t"] == 101`, `sample_at(50)["t"] == 100`, `sample_at(500)["t"] == 102`; empty store returns `None`.
  - `t_reopen_after_restart_appends_to_the_same_file`: `open_current` twice on one dir yields the same path and both samples.
  - `t_rotate_starts_a_new_file`: after `rotate`, `open_current` returns a different path; the old file still exists.
  - `t_events_update_and_list_newest_first`.
  - `t_csv_has_header_and_one_row_per_car`.
  - `t_full_writer_queue_drops_and_counts`: a `Writer` whose store blocks (a stub with an `Event`) and `maxsize=2` accepts 2, then `put` returns `False` and `dropped == 1`.
- [ ] **Step 2: Run** `python3 tests/test_race_recording.py`. Expected: `ModuleNotFoundError`.
- [ ] **Step 3: Implement** `race_recording.py` (tables `samples(t REAL PRIMARY KEY, doc TEXT)`, `events(id INTEGER PRIMARY KEY, doc TEXT)`, `meta(key TEXT PRIMARY KEY, value TEXT)`; `sqlite3.connect(..., check_same_thread=False, timeout=5.0)` as in `health_store`; nearest lookup by two indexed queries around `t`). Write the pointer with `atomic_replace`.
- [ ] **Step 4: Run** `python3 tests/test_race_recording.py`. Expected: `ALL PASS`.
- [ ] **Step 5: Commit** `feat(telemetry): per-event race recording store`.

### Task 3: The recorder and disconnect events

**Files:**
- Modify: `src/scripts/race_recording.py`
- Test: `tests/test_race_recording.py`

**Interfaces:**
- `class Recorder(store, writer, wall)`:
  - `observe(standings: dict, compounds: dict, now_wall: float) -> None`: called every tick. Auto start on the first `observe` whose `state` is a counting state (`racing`, `final_lap`, `finishing`) while no recording ever ran this event (meta `auto_started` unset); auto stop when `state == "finished"`. While running, writes a sample when `now_wall - last_sample >= SAMPLE_EVERY_S` and runs disconnect detection.
  - `start(who) -> dict`, `stop(who) -> dict`: manual; meta `started_by`, `started_at`, `running`.
  - `decide(who, event_id: int, decision: str) -> dict`: `decision in ("confirm", "dismiss")`, else `ValueError`.
  - `status() -> dict`: `{running, started_by, started_at, span, dropped, events}`.
- Event doc: `{id, entry, driver, state, last_packet, opened, returned, dropout_s, decided_by, decided_at}`; `state` in `suspected`, `dropout`, `confirmed`, `dismissed`. `last_packet = now_wall - quiet_s`.
- Detection rule per row: suspect when `quiet_s > race_distance.NO_DATA_S`, the row has started (`distance_m is not None`), no open event exists for the entry, `standings["red_flag"] is None`, and not (`in_box and standings["change_window"]`). An open `suspected` event whose row has `quiet_s <= NO_DATA_S` again becomes `dropout` with `dropout_s = returned - last_packet`.
- Every store write (sample, event, meta) goes through `writer.put`; reads go to the store directly.
- A restart reloads open events from the store.

- [ ] **Step 1: Write the failing tests** with synthetic standings dicts (no `Race` needed):
  - `t_auto_starts_on_the_first_counting_state_and_stops_at_finish`
  - `t_manual_start_before_the_green_flag_records_the_grid`
  - `t_samples_once_per_second_not_per_tick`: 4 observes 0.5 s apart give 2 samples.
  - `t_quiet_car_becomes_a_suspicion_dated_by_its_last_packet`: `quiet_s=6.2` at `now=1000` gives `last_packet == 993.8`.
  - `t_short_silence_raises_nothing`: `quiet_s=4.9`.
  - `t_return_before_decision_is_a_dropout_and_stays_confirmable`
  - `t_box_in_change_window_raises_nothing_but_track_does`
  - `t_red_flag_raises_nothing`
  - `t_open_suspicion_survives_a_restart`: new `Recorder` on the same store lists it as `suspected`.
  - `t_decide_rejects_unknown_decision_and_id`
- [ ] **Step 2: Run** `python3 tests/test_race_recording.py`. Expected: new tests fail with `AttributeError`.
- [ ] **Step 3: Implement** `Recorder` and `SAMPLE_EVERY_S = 1.0`.
- [ ] **Step 4: Run** `python3 tests/test_race_recording.py`. Expected: `ALL PASS`.
- [ ] **Step 5: Commit** `feat(telemetry): recorder with automatic start and disconnect events`.

### Task 4: Wire the recorder into the race service

**Files:**
- Modify: `src/scripts/driver_race.py` (`Service.__init__`, `tick`, new methods), `src/racecast.py` (`_start_driver_invite`)
- Test: `tests/test_driver_race.py`, `tests/test_racecast_cli.py` (or whichever test covers `_start_driver_invite`; `grep -ln _start_driver_invite tests/`)

**Interfaces:**
- `Service.__init__(..., recording_dir=None)`: when set, `race_recording.open_current(recording_dir, wall)` lazily on first use.
- `tick()`: when `self._enabled()`, calls `recorder.observe(race.standings(now), compounds, self._wall())` outside `self._lock` hold time beyond computing standings.
- `recording_status() -> dict`, `recording_start(who)`, `recording_stop(who)`, `recording_decide(who, event_id, decision)`, `recording_at(t_wall) -> dict | None`, `recording_csv() -> Iterator[list]`. Start, stop and each decision also call `self._log(who, "recording_start" | "recording_stop" | "dc_confirm" | "dc_dismiss", ...)`.
- `racecast._start_driver_invite(fresh=True)` calls `race_recording.rotate(runtime)`.

- [ ] **Step 1: Write the failing tests**: `t_tick_records_in_race_mode_only`, `t_recording_start_is_audited`, `t_fresh_event_start_rotates_the_recording`.
- [ ] **Step 2: Run** the two test files. Expected: the new tests fail.
- [ ] **Step 3: Implement**; the relay passes `recording_dir` = the profile runtime dir where it builds `driver_race.Service` in `src/relay/racecast-feeds.py`.
- [ ] **Step 4: Run** `python3 tests/test_driver_race.py` and the CLI test file. Expected: `ALL PASS`.
- [ ] **Step 5: Commit** `feat(telemetry): the race service records and the event start rotates`.

### Task 5: Routes and policy

**Files:**
- Modify: `src/relay/racecast-feeds.py` (the `/drivers/race/*` GET and POST dispatch), `src/scripts/console_policy.py` (`_DRIVER_DESK_GET`, `_DRIVER_DESK_POST`)
- Test: `tests/test_driver_routes.py`, `tests/test_console.py`

**Interfaces (tailnet path; the desks reach the same under their prefixes):**
- `GET /drivers/race/recording` → `recording_status()`
- `POST /drivers/race/recording` body `{"action": "start" | "stop"}`
- `POST /drivers/race/recording/event` body `{"id": int, "decision": "confirm" | "dismiss"}`
- `GET /drivers/race/recording/at?t=<epoch seconds>` → the sample, `404 {"error": "no recording"}` when empty, `400` for a non-finite `t`
- `GET /drivers/race/recording/csv` → `text/csv`, `Content-Disposition: attachment; filename="race-recording.csv"`
- `GET /drivers/snapshot` → `src/console/race-snapshot.html` (Task 6); policy entry `("snapshot",)`.
- Policy: add `("race","recording")`, `("race","recording","at")`, `("race","recording","csv")`, `("snapshot",)` to `_DRIVER_DESK_GET` and `("race","recording")`, `("race","recording","event")` to `_DRIVER_DESK_POST`.

- [ ] **Step 1: Write the failing tests**: route round trips for each endpoint through both `/console/drivers/` (director token) and `/console/race-control/drivers/` (race_control token); a `commentator` token gets 403 on each; `at` with an empty store returns 404 and with `t=nan` returns 400; the CSV response has the header row.
- [ ] **Step 2: Run** `python3 tests/test_driver_routes.py && python3 tests/test_console.py`. Expected: new tests fail with 404.
- [ ] **Step 3: Implement** the dispatch and the policy entries.
- [ ] **Step 4: Run** both files plus `python3 tests/test_pov.py`. Expected: `ALL PASS`.
- [ ] **Step 5: Commit** `feat(relay): recording routes for the Director and Race Control desks`.

### Task 6: Desk UI and the frozen page

**Files:**
- Modify: `src/console/driver-desk.js`
- Create: `src/console/race-snapshot.html`
- Modify: `src/docs/wiki/images/director-panel-drivers.png`, `src/docs/wiki/images/console-race-control.png`
- Test: `tests/test_driver_routes.py` (served page and its strings), visual check per the `ui-visual-verification` skill

**Interfaces:**
- Race table gap cell for a lapped car: `"+N L · " + secs(e.gap_time_s)` when `gap_time_s` is a number, else `"+N L"`; interval cell shows `secs(e.interval_time_s)` for every car.
- Recording strip above the race table: `Recording since HH:MM:SS by <who>` or `Not recording`, buttons `Start recording` / `Stop recording`, link `Download CSV`.
- Events section titled `Events (<open count>)`; each row: entry, driver, state label (`Suspected DC`, `Dropout, N s`, `Confirmed DC`, `Dismissed`), last packet time, buttons `Confirm DC` / `Dismiss` while open, an offset `<select>` with 5/10/15/20/30 (default 10) and `Show standings`, which opens `<base>/snapshot?t=<last_packet - offset>` in a new tab.
- A suspected entry gets a `Suspected DC` pill in the race table.
- `Sound on suspicion` checkbox, default off, stored in `localStorage` under `racecast.desk.dcSound` (wrapped in try/catch).
- `race-snapshot.html`: reads `t` from the query, fetches `./race/recording/at?t=`, renders the desk's columns plus compound, shows the sample's local time, a slider over `span` from `./race/recording`, and states `No recording yet.` on 404.

- [ ] **Step 1: Write the failing test**: `t_snapshot_page_is_served_to_both_desks` (200 with `<title>Race snapshot</title>` under both prefixes, 403 for a commentator).
- [ ] **Step 2: Run** `python3 tests/test_driver_routes.py`. Expected: FAIL.
- [ ] **Step 3: Implement** the desk changes and the page.
- [ ] **Step 4: Run** `python3 tests/test_driver_routes.py`; then render both desks and the snapshot page with the demo relay and look at them (ui-visual-verification), recapture both screenshots (wiki-screenshots), record the marker. Expected: tests pass, screenshots updated.
- [ ] **Step 5: Commit** `feat(console): recording controls, DC events and the race snapshot page`.

### Task 7: Timing tower shows the gap to the leader

**Files:**
- Modify: `src/obs/drivers.html` (`column()`)
- Modify: `src/docs/wiki/images/driver-dashboard.png`
- Test: `tests/test_driver_routes.py` (the overlay test that already reads `drivers.html`)

**Interfaces:** `column(e, i)` returns `"+" + e.gap_s.toFixed(1)` where it returned `interval_s`; `Leader`, `+N L` and the last lap / fuel cycle unchanged.

- [ ] **Step 1: Write the failing test** `t_tower_gap_column_reads_the_leader_gap`: the overlay source uses `e.gap_s` in `column` and no longer `e.interval_s`.
- [ ] **Step 2: Run** it. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** it, render the overlay with the demo relay, recapture `driver-dashboard.png`, record the marker. Expected: PASS.
- [ ] **Step 5: Commit** `feat(obs): the timing tower shows the gap to the leader`.

### Task 8: Docs and the full gates

**Files:**
- Modify: `src/relay/CLAUDE.md` (driver race section: recording, events, routes), `src/docs/wiki/Driver-Telemetry.md` (tower gap, recording, DC events, snapshot), `src/docs/wiki/Console.md` (Race Control desk: recording and events)

- [ ] **Step 1: Write** the doc sections: mechanism only, no league rejoin rule.
- [ ] **Step 2: Run** `python3 tools/run-tests.py`, `python3 tools/lint.py`, `python3 tools/build.py`. Expected: all exit 0 (`ALL TEST FILES PASS`, `All checks passed!`).
- [ ] **Step 3: Commit** `docs(telemetry): race recording and disconnect events`.

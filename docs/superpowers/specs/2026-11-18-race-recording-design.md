# Race recording, disconnect events and the leader gap

Status: approved in conversation on 2026-11-18, not yet planned.

## Why

Race Control needs to calculate a rejoin after a driver's disconnect (DC). For that it
needs to know what the standings looked like just before the sender went quiet. Today the
relay keeps no history of the standings. A quiet sender only turns its row into "No data"
on the desks and drops its gaps, so the last clean gap is gone the moment it matters.

The broadcast also wants the timing tower to show what TV graphics usually show: the gap to
the leader.

## Goals

1. The timing tower (`src/obs/drivers.html`) shows the gap to the leader in its gap column
   instead of the interval to the car ahead.
2. The Race Control and Director desks show the exact time to the leader and to the car
   ahead, also for a lapped car.
3. The relay records the live standings once per second during the race, automatically and
   by hand.
4. A sender that goes quiet becomes a DC suspicion that Race Control or the Director
   confirms or dismisses.
5. From any event, or any moment of the recording, Race Control opens a frozen page with the
   standings at that time.
6. The recording exports as CSV.

## Non-goals

- No automatic rejoin. The recording delivers the standings; Race Control decides and
  corrects with the existing tools (`correct`, `rejoin` mark). The league's DC rejoin rule
  is not encoded anywhere.
- No recording in qualifying.
- No track map on the frozen page and no car coordinates in the recording.
- No change to the public timing page, the hosted timing page, the cockpit or the track map.
- No public or Funnel link to recorded data beyond the authenticated `/console` routes below.

## Anti-goals

- A recording that leaks undelayed data to the public timing view. The public view stays
  delayed and allowlisted (`driver_race.public_view`).
- A DC suspicion at every driver change or for the whole field at a red flag. That would
  train the crew to dismiss without looking.
- Recording that slows the 0.5 s tick or blocks a request on disk I/O.

## Decisions

### Timing tower

- The gap column shows `gap_s` to the leader as `+12.4`. P1 shows `Leader`, a lapped car
  `+N L`. The column keeps cycling with last lap and fuel as today.
- No other overlay changes. The HUD strip (`src/obs/hud.html`) does not show standings and
  stays as it is.

### Exact gaps for Race Control

- `race_distance.Race.standings` adds two fields per row, `gap_time_s` and
  `interval_time_s`: seconds to the leader and to the car ahead, computed from the ahead
  car's distance history (`_time_at`) also when the car is one or more laps down.
  `None` only while there is no history to interpolate (quiet car, red flag `waiting`, not
  started). The existing `gap_s`, `interval_s` and `lapped` keep their meaning, so the
  tower, the public view and the cockpit are unaffected.
- The shared desk (`src/console/driver-desk.js`) shows a lapped car as `+1 L · 1:42.3`
  in the gap column and the exact interval in the interval column.
- `gap_time_s` and `interval_time_s` are not added to `_PUBLIC_ROW`.

### Recording

- Scope: race mode only.
- Automatic start when the first entry starts counting (its GT7 counter reached the start
  lap: 2 with a formation lap, 1 without). Automatic stop when the race is finished (the
  last car took the flag, or `finish` froze the race).
- Manual start and stop from the desk, by Race Control and the Director. A manual start
  before the green flag records the grid. One recording per event: a start after a stop
  appends to the same recording; the desk shows who started it and since when.
- A red flag does not pause it.
- Rate: one sample per second, taken from the live (undelayed) standings, never from the
  delayed dashboard snapshot.
- Content per sample: wall-clock time and, per row, every field the desk's race table
  shows: position, entry, team, driver, lap, `gap_time_s`, `interval_time_s`, `lapped`,
  status, tyre compound, pit stops, distance correction, rejoin mark, GT7 position and the
  mismatch flag. Race-level fields: red flag state, finishing state.
- Storage: one SQLite file per event under `runtime/<profile>/` (stdlib `sqlite3`, as in
  `health_store`). It survives a relay restart and the relay resumes appending to it. A
  fresh `event start` begins a new file. Old files stay until someone deletes them.
- Writes happen off the tick thread so disk latency never delays the 0.5 s tick.

### Disconnect events

- A suspicion arises when an entry's active car has sent no sample for `NO_DATA_S`
  (5 s, the existing "No data" threshold). Its time is the receive time of the last
  sample, not the moment the 5 s ran out.
- No suspicion for a car standing in the box inside the driver change window, and none
  while a red flag holds. A car that goes quiet on track always raises one, inside the
  change window too.
- Lifecycle: `suspected` → `confirmed` or `dismissed` by Race Control or the Director,
  each with the person's identity. If data returns before anyone decided, the event shows
  `dropout, N s` and can still be confirmed.
- Events are stored in the recording file and listed in an "Events" section of the desk.
  The desk marks the car in the race table and counts open suspicions on the section.
  An optional sound, off by default, toggled per desk in the browser.
- Each decision is also written to the existing audit log (`driver-race-audit.jsonl`).
- Events arise only while a recording runs.

### Frozen page

- Opened from an event with "Show standings" and a choice of 5, 10, 15, 20 or 30 s before
  the last packet (default 10 s), or from a slider over the whole recording.
- Shows the sample nearest the chosen time as a table with the desk's columns, plus the
  sample's time. No track map.
- Has its own link (a time parameter) under `/console`, for the `race_control` and
  `director` roles only.
- CSV export of the whole recording from the desk, for the same roles.

## Approaches considered

1. **SQLite file per event (chosen).** Indexed lookup by time for the frozen page and the
   slider, safe appends across a restart, one place for samples and events. The repo already
   uses `sqlite3` for `health_store`.
2. **JSONL per event.** Simplest to write and to read by hand, but a lookup by time scans the
   file, and a crash mid-line needs repair logic. Rejected for the frozen page's lookups.
3. **Extend the in-memory history.** No disk, but it is lost on a relay restart and bounded
   to about 15 minutes. Rejected: a DC in hour two of an endurance must still be reachable.

## Constraints

- Stdlib only. Relay code may not import `config.py` or `http_util`.
- Every `/console` route is authorized server-side through `console_policy.py`. The new
  routes are role-gated to `race_control` and `director`; the `race_control` role still
  reaches no program change.
- English UI strings and docs; ASCII argparse help if a CLI verb is added.
- Any new `racecast` verb needs its Control Center action in the same change. None is
  planned: start, stop, decisions and export live on the desks.
- Changed UI surfaces (timing tower, Director Panel drivers section, Race Control desk)
  refresh their wiki screenshots in the same change.

## Left to the builder

- Route names and the time parameter format, within the `/console/race-control/drivers/*`
  and `/console/drivers/*` families the desks already use.
- File name pattern of the recording and how a fresh `event start` is detected.
- Schema details and indices, the writer thread's queue bound and what happens when it is
  full (drop the sample and count it, never block).
- CSV column order.

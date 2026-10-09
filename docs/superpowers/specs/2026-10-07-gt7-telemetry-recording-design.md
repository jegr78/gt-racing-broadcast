# GT7 telemetry recording, track recognition and lap analysis

Epic #785 with four parts, one PR and one plan each:

| Part | Issue | Content |
|---|---|---|
| 1 | #786 | record the trace, CSV export, REC key |
| 2 | #787 | GT7 data updates, track recognition |
| 3 | #788 | lap index, mini-sectors, Control Center lap comparison |
| 4 | #789 | telemetry section in the post-event report |

Each part depends on the one before. Extends the solo POV telemetry of #324, #711 and
#713.

## Problem

In a solo POV broadcast the relay receives every GT7 telemetry packet at about 60 Hz
(`_telemetry_loop` -> `TelemetryStore.update`) and throws almost all of it away. It
keeps the reference lap in `telemetry.json` and a 15 s, 30 Hz throttle/brake trace in
memory for the HUD. A driver who wants to analyse a session afterwards has nothing to
work with.

# Part 1: recording and CSV export (#786)

## Decisions

- The relay records the **decrypted raw packets** with a timestamp. Analysis formats
  are derived from that file by an exporter, so a field parsed later (or a future
  exporter for a dedicated telemetry tool) also works on old recordings. Recording
  parsed JSONL was rejected because every field added later would be missing from all
  earlier recordings. Recording the encrypted wire bytes was rejected because the relay
  only keeps packets that decrypt, and the key is a public constant.
- First exporter: CSV (`samples.csv` + `laps.csv`). An exporter for a specific
  telemetry tool (MoTeC i2 or similar) is a separate issue once the tool is chosen.
- **One file per recording** (start to stop). GT7 session changes inside a recording
  appear as a `session` counter in the export, not as separate files.
- **No automatic cleanup.** Recordings stay until the producer deletes them.
  `racecast telemetry list` shows count and size.
- Operator surfaces in part 1: relay endpoints, the profile key, the CLI and a Director
  Panel button. The Control Center only gets the key pre-filled in new solo POV
  profiles. A Companion button is out of scope for the whole epic.
- A live toggle **survives a relay restart**. A new broadcast (`event start` without
  recovery flags) resets it to the profile default.
- Solo POV only: everything hangs off `telemetry_store`, which exists only when
  `telemetry_active` is true.

## Recording file

Path: `runtime/<profile>/telemetry-recordings/<YYYYMMDD-HHMMSS>.gt7rec`, local time of
the first recorded packet. While open the file is named `<...>.gt7rec.part`; a clean
stop renames it. On Windows `relay stop` ends the relay with `taskkill /F`, so no
shutdown code runs there and every recording ends as a `.part` file. The writer
therefore flushes about once per second (at most ~1 s of packets is lost), and every
relay start that has telemetry renames leftover `.part` files in the directory to
`.gt7rec` and logs each one. The content stays as written; the reader skips a
truncated last record.

Layout:

1. One line of UTF-8 JSON terminated by `\n`:
   `{"format": "racecast-gt7rec", "version": 1, "profile": "<name>",
   "started": "<ISO 8601 local time with offset>", "relay_version": "<version>"}`
2. Records until end of file, each:
   - `float64` little-endian, wall-clock seconds (`time.time()`)
   - `uint8`, packet type as its ASCII byte (`A`, `B`, `~`)
   - `uint16` little-endian, payload length
   - the decrypted payload bytes

   Kind byte `0x00` marks a meta record whose payload is UTF-8 JSON `{"dropped": n}`,
   the total of packets dropped so far. The writer adds one at a flush when the count
   changed and at close, so the count also survives a killed relay. Readers keep the
   latest value and do not yield meta records as packets.

About 21 KB/s with the 344-byte `~` packet, so roughly 75 MB per hour.

## Parser additions

`gt7_telemetry.parse_packet` gains three fields, verified on the real fixture packets
(gear 5 to 6 at 256 to 271 km/h, 6900 to 7900 rpm):

| Field | Offset | Type |
|---|---|---|
| `gear` | `0x90`, low nibble | uint8 & 0x0F |
| `rpm` | `0x3C` | float32 |
| `pos_x`, `pos_y`, `pos_z` | `0x04`, `0x08`, `0x0C` | float32 |

The high nibble of `0x90` ("suggested gear" in community docs) reads 2 at 260 km/h on
every fixture and stays unparsed. `_sanitize` treats the new floats like the existing
ones (a non-finite value keeps the previous reading, #717).

`TelemetryEngine._finalise_lap` calls an `on_lap` callback (set by the recording
exporter; `None` in live use) with a lap record `{session, lap, start, end, elapsed,
status, reason, fuel_used, top_speed_mps, car_id}` (`status` is `reference`, `counted`
or `not counted`) in addition to logging it, and `TelemetryEngine` exposes a `session`
counter that `_reset_session` increments. Live behaviour does not change.

## Module `src/scripts/gt7_recording.py`

Stdlib only, no relay imports.

- `RecordingWriter(dir, profile, relay_version)`: `put(wall_ts, kind, plain)` enqueues
  into a bounded queue (`maxsize` 600, about 10 s) and never blocks. A full queue drops
  the packet and increments `dropped`. A writer thread opens the `.part` file on the
  first packet, writes buffered and flushes about once per second. `close()` drains the
  queue, flushes and renames. An error (`OSError`, or any other exception a payload/kind
  can raise) stops the recording, sets `error` to a sanitised message (never the OS
  path: `strerror`, or the exception class name) and logs the full exception as a
  warning; the relay keeps running, and the `.part` file is left unrenamed.
- `Recording(path)`: reads the header, raising `RecordingError` with a clear message
  for an unknown `format` or a `version` above 1, and exposes `.header`, `.dropped` and
  a `packets()` iterator of `(wall_ts, kind, plain)`; a truncated last record is
  skipped silently.
- `export_csv(path, out_dir, include_all=False, excel=False)` writes `samples.csv` and
  `laps.csv` (below).
- `list_recordings(dir, count_laps=False)` -> name, size, start, duration, lap count,
  `partial` flag (the file still ends in `.part`). By default it reads only the header
  (duration = file mtime minus `started`, no lap count), so the Control Center and the
  report stay fast; `count_laps=True` reads every packet, which `racecast telemetry list`
  does.

## Switch and state

The live state is resolved in this order:

1. `runtime/<profile>/telemetry-record.json` `{"active": bool}`, when present and valid.
   Every live toggle writes it atomically (same pattern as `telemetry-view.json`).
2. Otherwise the profile key `TELEMETRY_RECORD`, injected as
   `RACECAST_TELEMETRY_RECORD`. `1`, `true`, `yes`, `on` (any case) mean on; anything
   else, including unset, means off.

`event_start` removes `telemetry-record.json` when it records a new session start
(`_new_session` and not `_is_continuation_start`). A recovery restart with `--stint` or
`--part` keeps it. Deleting the file only resets the *next* relay start's default: a
relay already running keeps its live `RecordControl` state (it loaded the file once, at
construction), so a fresh `event start` against an already-running relay additionally
pushes the resolved `TELEMETRY_RECORD` default to it over HTTP
(`racecast.py`'s `_sync_live_telemetry_record`, via `/telemetry/record/start|stop`),
best-effort, so the file and the live relay never disagree.

When the state is on, the writer opens a new file on the first packet after relay start
or after a toggle to on, so a relay without a console never creates empty files. Every
relay start that records starts a new file. Stopping the relay closes the recording
cleanly. `RecordControl.close()` is the terminal form the relay's shutdown calls: it
sets an internal `_closed` flag that makes every later `put()` a no-op, but unlike
`set_active(False)` it never flips `active` or rewrites the state file, so the next
relay start resumes recording if it was on.

## Relay

- `TelemetryStore` takes an optional recorder. `_telemetry_loop` calls
  `store.record(time.time(), kind, plain)` for every packet it accepts (after the
  source check and `decrypt_typed`), including menu, pause and replay packets. The call
  only enqueues, outside the store lock, and never raises into the UDP loop even if the
  recorder itself is broken.
- `GET /telemetry/record/start|stop|toggle` -> `{"active", "file", "since"}`; 404 when
  `telemetry_store` is None, like the other `/telemetry/*` routes. `console_policy`
  requires DIRECTOR, like `/telemetry/show|hide|toggle`.
- `/status` extends the existing `telemetry` block with `record: {active, file, since,
  elapsed_s, bytes, dropped, error}`; `elapsed_s` is the relay's own wall clock minus
  `since` (not the viewer's clock), so a skewed browser never shows a wrong duration.
  Over the Funnel-exposed `/console` mount, `record` is director/producer-only
  (`redact_console_status`): the file path and byte/drop counters are producer
  detail, kept off the Funnel for every other role.

## CLI

New group `racecast telemetry`. Help strings stay ASCII.

- `record start|stop|status`: talks to the local relay through `http_util`. Without a
  running relay it prints a clear error and exits non-zero.
- `list`: the active profile's recordings (name, size, start, duration, laps). The
  file the running relay is writing (from `/status`) is marked `recording`; any other
  `.part` file is marked `unclosed`.
- `export <name|latest> [--out DIR] [--all] [--excel]`: writes into `<name>/` next to
  the recording, or into `DIR`.
- `delete <name>`: refuses the file the running relay reports as open in `/status`.

`list`, `export` and `delete` read files only and do not need a relay. `--profile`
works as for every other command.

## CSV export

Units are always metric, independent of `RACECAST_TELEMETRY_UNITS`. Fields a packet
does not carry are empty (steering in an `A` packet, for example). By default only
packets with `on_track` and not `paused`/`loading` are exported; `--all` keeps every
packet.

`samples.csv`, one row per packet:

`t_s, session, lap, lap_t_s, lap_dist_m, on_track, paused, speed_kmh, throttle_pct,
brake_pct, throttle_input_pct, brake_input_pct, steer_deg, gear, rpm, fuel_l,
tyre_fl_c, tyre_fr_c, tyre_rl_c, tyre_rr_c, pos_x, pos_y, pos_z, car_id`

- `t_s` counts from the first record, `lap_t_s` from the engine's lap-change edge.
- `lap_dist_m` is the distance driven since the lap-change edge, integrated from speed
  (the engine's `_LapAccumulator.distance`). Part 2 replaces it with the position
  projected onto the racing line when the track is known.
- `session` starts at 1 and follows the engine's session counter.

`laps.csv`, one row per lap the engine finalised:

`session, lap, start_t_s, end_t_s, gt7_time_s, relay_time_s, status, reason,
fuel_used_l, top_speed_kmh, car`

- `gt7_time_s` is the first `last_ms` that changes within the first 3 s of the
  following lap (GT7 updates it shortly after the line). Empty if none arrives.
- `relay_time_s`, `status` and `reason` come from the engine's lap record.
- `car` is `<maker> <name>` from the car tables for the car id of the lap's last
  packet, or `Car #<id>` for an id the tables do not know. Part 2 appends `track` and
  `layout`.

The exporter replays the packets through the same `parse_packet` and `TelemetryEngine`
the HUD uses, so the lap verdicts match what the broadcast showed.

`--excel` writes `;` as separator, `,` as decimal mark and UTF-8 with BOM, so a double
click opens the file correctly in a German Excel. The default is `,` and `.` for pandas,
LibreOffice and Google Sheets. When packets were dropped, the exporter prints the count.

## Surfaces

- **Director Panel:** a `REC` button next to `TELEMETRY` in the solo `vis` list,
  `relay: "telemetry/record"`. Hidden when `/status` has no telemetry block, red with
  the elapsed time (`record.elapsed_s`) while recording, amber when `record.error` is
  set. The REC key lives in the solo `vis` list, so `director-panel-solo.png` is
  refreshed in the same PR, not the endurance `director-panel.png`.
- **Control Center:** `profile_admin` writes an empty `TELEMETRY_RECORD=` into new solo
  POV profiles. No other change, so no `cc-*.png` refresh.

## Error handling

| Case | Behaviour |
|---|---|
| disk full, write error | recording stops, `record.error` set, panel button amber; live telemetry and HUD continue |
| queue full | packet dropped, `dropped` counted, exporter reports it |
| relay killed (crash, or `relay stop` on Windows) | `.part` file readable up to the last complete record, `export` works; the next relay start renames it to `.gt7rec` |
| unknown file version | export aborts with a clear message |
| toggle without solo POV | 404 from the relay, CLI prints why |

## Tests

TDD, failing test first.

- `tests/test_gt7_recording.py`: writer/reader round-trip, truncated tail, queue
  overflow, write error, header validation, CSV columns and filtering, `--excel`
  formatting, lap rows and `gt7_time_s` on synthetic packets.
- `tests/test_gt7_fixture.py`: gear, rpm and position on the real packets.
- `tests/test_gt7_telemetry.py`: the `on_lap` callback's record, session counter.
- `tests/test_telemetry_endpoints.py`: record endpoints, state precedence (file over
  profile key), `/status` block, file opened only on the first packet.
- `console_policy`, CLI dispatch, the `event_start` reset, the pre-filled profile key.
- Director Panel: `ui-visual-verification`, then `wiki-screenshots` for
  `director-panel-solo.png`.

## Docs

- `src/relay/CLAUDE.md`: the GT7 telemetry paragraph gains the recorder.
- Wiki `Relay-Mode.md` (telemetry section): recording, the switch and the export.
- `src/docs/wiki/Director.md`: the `REC` button.

# Part 2: GT7 data updates and track recognition (#787)

## Data sources

| Files | Source | Licence |
|---|---|---|
| `cars.csv`, `maker.csv`, `cargrp.csv` | [ddm999/gt7info](https://github.com/ddm999/gt7info) `_data/db/` | MIT-0 |
| `index.json`, `signatures.json` | [jbhoorasingh/gt7-datalogger-track-data](https://github.com/jbhoorasingh/gt7-datalogger-track-data) | CC0 for `index.json`; `signatures.json` has no stated licence and is downloaded at runtime only |

- `index.json` lists all 121 GT7 layouts: `official_id`, `track`, `layout`,
  `official_name`, `country`, `turns`, `length_m`, `reverse`. It is the catalogue for
  names and for the "set track" choice in part 3.
- `signatures.json` has a row for 78 layouts: `official_id`, `official_name`,
  `length_m`, `min_x/max_x/min_z/max_z`, `path` (racing line as `[x, z]` every 20 m, in
  driving order, starting at the line), `reverse` (`null` or `{official_id,
  official_name}` of the reverse twin, which has no row of its own), `ambiguous_with`
  and `flags`. Coordinates are GT7 world `pos_x`/`pos_z`.
- On the real fixture packets the four `~` packets lie 2.5 to 6.6 m from the Nürburgring
  GP line and the three `A` packets 2 to 10 m from Suzuka; single points also fall into
  up to 35 bounding boxes, so recognition always works on a whole lap.
- The bundled copies live in `src/assets/gt7/` beside the car tables, with
  `LICENSE-track-data` (the CC0 dedication). `src/assets/gt7/README.md` names all
  sources.
- `signatures.json` is never bundled (`gt7_data.RUNTIME_ONLY`): the upstream CC0
  dedication names only `tracks/` and `index.json`. Each install downloads it with the
  first successful `update()`; until then only learned tracks are recognised. The
  24 h gate does not hold back an update while a runtime-only file is missing, and
  `status()` reports such a file as `missing`.

## Module `src/scripts/gt7_data.py`

- `SOURCES`: per file its URL, a validator (CSV columns and minimum row count as in
  today's `tools/fetch-gt7-cars.py`; JSON `format`/`version` and minimum row counts:
  `configurations` >= 100, `signatures` >= 50).
- `data_dir(runtime_base)` -> `<runtime_base>/gt7`. `resolve(name, runtime_base)`
  returns the runtime copy when it exists and validates, else the bundled one.
- `update(runtime_base, force=False, fetch=http_util.get_bytes, now=time.time)`:
  downloads every file, validates, writes atomically, records SHA-256 and time in
  `<runtime_base>/gt7/updated.json`. Without `force` it returns at once when the last
  successful check is younger than 24 h. Any network or validation failure keeps the
  old file and is reported per file in the result; it never raises.
- `status(runtime_base)` -> per file: source (`runtime`, `bundled` or `missing`), last update,
  row count.
- `tools/fetch-gt7-cars.py` becomes `tools/fetch-gt7-data.py`: it calls the same
  download and validation and writes the bundled copies in `src/assets/gt7/`
  (every source except `RUNTIME_ONLY`).

**Automatic.** A relay start with telemetry runs `update()` in a daemon thread. After
a successful update the relay reloads its car database and track database in place.

**Manual.** `racecast gt7-data update` and `racecast gt7-data status`. The CLI accepts
`--force` on `update` but does not advertise it: every manual update already forces a
fetch, so the flag changes nothing observable. The Control Center Settings view gets a
"GT7 data" row with the age of the data and an **Update** button (route `POST
/api/gt7-data/update`, `GET /api/gt7-data`). `cc-settings.png` is refreshed.

The new track database reads through `gt7_data.resolve` (runtime copy when present and
valid, else the bundled one). `gt7_cars.CarDB()` without a directory does not: it reads
`default_dir()` directly, i.e. the bundled tables in `src/assets/gt7/`, with no runtime
override.

## Module `src/scripts/gt7_tracks.py`

- `TrackDB(index_path, signatures_path, learned_path=None)`: loads the catalogue, the
  shipped signatures and the learned ones. A downloaded line always wins: a learned row
  whose id is shipped or is the reverse of a shipped row is ignored on load but stays in
  the file, and assignments always apply. `name(official_id)` -> `{id, track, layout, reverse, country,
  length_m}` or None. `layouts()` -> the catalogue sorted by track and layout.
- `match(points, length_m)` with `points` = `[(x, z), ...]` every ~20 m in driving
  order:
  1. Candidates: rows whose `length_m` is within 3 % of `length_m` and whose box,
     widened by 50 m, contains every point.
  2. Score: mean distance from each point to the racing line (projected onto the nearest `path` segment, not the nearest vertex, since vertices lie 20 m apart). Rows above 15 m
     drop out.
  3. Direction: the sequence of nearest `path` indices, unwrapped modulo the path
     length, must mostly rise (forward) or fall (reverse). Falling selects the row's
     `reverse` twin; a falling match on a row without a twin drops out.
  4. Result `{"id", "track", "layout", "reverse", "score_m"}` for a single best row,
     or `{"candidates": [ids...]}` when more rows score within 3 m of the best, or
     None.
- `learn(official_id, points, length_m, key=None)`: writes a row in the
  `signatures.json` shape (box from the points, `path` = points, `provenance:
  "learned"`) into `learned-tracks.json`, atomically, and with `key`
  (`"<profile>/<stem>"`) records the assignment of that recording to the layout.
  A layout that has a downloaded racing line, or reverses one, keeps it: `learn` then
  records only the assignment (it needs `key`) and returns False; it returns True when
  it wrote a line.
  `assignment(key)` reads it back. Updates never touch this file.
- `project(points, official_id)` -> per point the distance along the row's `path` from
  its start (the line), for `lap_dist_m`.

## Engine and relay

- `_LapAccumulator` also keeps `(pos_x, pos_z)` whenever the driven distance passed
  another 20 m; the lap record (`on_lap`) carries it as `points` plus `distance_m`.
- `TelemetryEngine.track_db` (None by default) and `TelemetryEngine.track`: after each
  closed lap with at least `MIN_TRACK_POINTS` recorded points, the engine calls
  `track_db.match`. A single match sets `track` until the next session boundary;
  candidates set `track = {"candidates": [...]}` and keep trying on later laps.
- `TelemetryStore.data()` and `/status` `telemetry.track` carry `track` (None until
  recognised). The Director Panel status strip shows `<track> - <layout>` next to the
  car (`stTrack`), or `Track ?` with the candidate ids as tooltip.
  `director-panel.png` is refreshed.

## Export

- A recording can hold several GT7 sessions on different tracks, so the track is
  decided **per session**: the match of the session's longest closed lap, unless a
  learned assignment for the recording exists (`assignment("<profile>/<stem>")`, set in
  part 3), which wins for every session. The exporter replays the recording twice:
  once to collect the laps and decide each session's track, once to write.
- `laps.csv` appends `track` and `layout` (empty when unknown). The exporter runs the
  same `TrackDB` the relay uses.
- `lap_dist_m` in `samples.csv`: when the session's track is known, the exporter
  projects each sample onto the racing line (`project`), taking the value (`s`, `s - L`
  or `s + L`) closest to the integrated distance so a sample just behind the line does
  not read as a full lap; otherwise it stays the integrated distance. `TrackDB` keeps a
  50 m grid over each racing line, so a projection looks at a few points, not the
  whole line.

## Tests and docs

- `tests/test_gt7_data.py`: validators, atomic replace, 24 h gate, failure keeps the
  old file, `resolve` fallback; all downloads through an injected fetch.
- `tests/test_gt7_tracks.py`: synthetic laps built from a real signature's `path`
  (forward, reverse, offset by 5 m, a wrong length, a different track), candidates for
  an ambiguous pair, a downloaded line winning over a learned row, `project`; the real `~` fixture points lie
  near the Nürburgring GP line.
- Relay `/status` `telemetry.track`, panel status strip (visual check), CLI
  `gt7-data`, Control Center route and Settings row (visual check).
- Docs: `src/relay/CLAUDE.md`, wiki `Relay-Mode.md`, `Director.md`, `Control-Center.md`,
  `src/assets/gt7/README.md`.

# Part 3: lap index, mini-sectors and lap comparison (#788)

## Module `src/scripts/gt7_laps.py`

- `index(path, track_db, cars, runtime_base, key=None, bundled=None)` returns the lap
  index of one recording and caches it in `<stem>.laps.json` next to the recording. The
  cache records the recording's size and mtime and `gt7_data.data_version` (runtime track
  files and the learned file by path, mtime and size; bundled files by name and content,
  so a onefile binary's per-launch unpack dir does not invalidate it); any change rebuilds
  it.
- Per lap: the `laps.csv` fields plus `rec` (recording stem), `track_id`, `car_id`,
  `car`, `tyre_avg_c` (mean surface temperature per wheel over the lap), and `trace`:
  the lap resampled every 5 m of `lap_dist_m` with `t`, `speed_kmh`,
  `throttle`, `brake`, `steer_deg`, `gear`, `x`, `z`. A counted lap's trace ends with
  one more point at the full lap length (the racing line's length on a known track, else
  the lap's driven `distance_m`) carrying the lap time `time_s`; that point may lie off
  the 5 m grid.
- Each lap's track: its session's track, decided as in the part 2 export (a learned
  assignment keyed `<profile>/<stem>` wins; an assignment whose layout no longer
  resolves falls back to matching). Car name, layout brief, the projection rule and GT7
  lap-time matching are shared public helpers in `gt7_recording`, used by the export and
  the index alike.
- `sectors(trace, length_m, step_m=200)`: sector times from the trace, boundaries every
  200 m from the line, the last sector shorter, times interpolated at the boundaries.
  `lap_length_m(lap)` is the trace end, so every counted lap of a track is cut at the
  same boundaries and its sectors add up to its lap time.
- `best_sectors(laps)`: per sector the minimum over the given counted laps;
  `theoretical_best` = their sum.
- The delta of B against A (time of B minus time of A at each 5 m station) is computed
  in the page from the two traces it already holds; the module has no delta function.

Comparisons pool counted laps of the same `track_id` and `car_id` across all
recordings of the active profile. Laps on an unknown track pool only within their own
recording and session.

## Control Center view `Telemetry`

- Shown when the active profile is solo POV. It reads files through the `racecast.py`
  data layer and needs no running relay.
- Left column: recordings (newest first; the one the relay is writing marked as
  recording, as in `racecast telemetry list`), then the laps of the selected recording with
  time, status, car and track. A recording with an unknown or ambiguous track offers
  **Set track**: a choice from `TrackDB.layouts()` that calls `learn` with the
  recording's longest counted lap and stores the assignment.
- Lap A is the reference, by default the fastest counted lap of the same track and car
  across all recordings other than B; lap B is the selected lap. Both have a picker over all
  matching laps.
- Main area: stacked charts over distance for speed, throttle, brake, steering and
  gear with both laps, then the delta curve of B against A; a shared cursor follows the
  pointer. Right: the track map with both lines, mini-sectors coloured green where B
  gains and red where B loses. Below: the mini-sector table with A, B, the best sector
  and the theoretical best.
- Inline SVG, no external library. The Control Center's existing colour tokens and
  dark theme apply.
- Routes: `GET /api/telemetry/recordings`, `GET /api/telemetry/laps` in three forms
  (`?rec=` lists one recording's laps, `?track=&car=` is the pool of a known track,
  `?rec=&session=&track=&car=` with an empty `track` is the pool of an unknown track
  within one session),
  `GET /api/telemetry/lap?rec=&lap=&session=`, `GET /api/telemetry/tracks`,
  `POST /api/telemetry/learn` `{rec, track_id}`.
- New wiki screenshot `cc-telemetry.png` from a synthetic demo recording (a tool under
  `tools/` builds it from a racing line of the downloaded `signatures.json`), captured
  from a local dev build.

## Tests and docs

- `tests/test_gt7_laps.py`: cache build and invalidation, resampling, sectors with a
  short last sector, best sectors and theoretical best. The page's delta and path helpers
  run under node in `tests/test_ui_server.py`.
- `tests/test_ui_server.py` / `tests/test_racecast.py`: the five routes and their data
  functions; `learn` writes the learned file and the assignment.
- Visual check of the view; wiki `Control-Center.md` with `cc-telemetry.png`.

# Part 4: telemetry in the post-event report (#789)

- `racecast report` collects the active profile's recordings whose time span overlaps
  the report window and reads their lap indexes. Without any, the section is absent;
  endurance reports are unchanged.
- The report also indexes the recording the relay is still writing, because `event stop`
  builds the report before the teardown; that one-time build costs about 16 s per 3 h of
  recording, and the report says its last lap may be missing. The Control Center
  Telemetry view keeps skipping the open file.
- Figures are per track and car; laps on an unknown track group only within their
  recording and GT7 session, the rule part 3 pools by.
- HTML section "Telemetry":
  - key figures: best lap, theoretical best, consistency (standard deviation of the
    counted lap times), fuel per lap, average tyre temperature per wheel;
  - lap-time trend as a small SVG: counted laps as dots, other laps greyed;
  - track map of the best lap as SVG, mini-sectors coloured by the gap to the best
    sector time;
  - the lap table: number in driving order, GT7 lap number, time, status, reason, fuel,
    top speed, car, track.
- One line `Best lap 1:58.432 (theoretical 1:57.910), 23 laps, Suzuka Circuit` goes into
  `render_summary_text` (CLI and Control Center summary) and, as a "Telemetry" field,
  into the Discord embed built by `report_discord_fields`. With several track and car
  combinations the line names the one with the most counted laps.
- `build_report` takes the telemetry block as an optional argument, so its existing
  tests stay unchanged.

## Tests and docs

- `tests/test_report_build.py`: figures, consistency,
  section absent without recordings, summary line; SVG output is well-formed XML.
- Visual check of a rendered report; the post-event report section in wiki
  `Health-Monitor.md` describes the telemetry part.

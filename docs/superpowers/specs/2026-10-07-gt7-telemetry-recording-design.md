# GT7 telemetry recording

Issue #785. Extends the solo POV telemetry of #324, #711 and #713.

## Problem

In a solo POV broadcast the relay receives every GT7 telemetry packet at about 60 Hz
(`_telemetry_loop` -> `TelemetryStore.update`) and throws almost all of it away. It
keeps the reference lap in `telemetry.json` and a 15 s, 30 Hz throttle/brake trace in
memory for the HUD. A driver who wants to analyse a session afterwards has nothing to
work with.

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
- Operator surfaces in scope: relay endpoints, the profile key, the CLI and a Director
  Panel button. The Control Center only gets the key pre-filled in new solo POV
  profiles. A Companion button is out of scope.
- A live toggle **survives a relay restart**. A new broadcast (`event start` without
  recovery flags) resets it to the profile default.
- Solo POV only: everything hangs off `telemetry_store`, which exists only when
  `telemetry_active` is true.

## Recording file

Path: `runtime/<profile>/telemetry-recordings/<YYYYMMDD-HHMMSS>.gt7rec`, local time of
the first recorded packet. While open the file is named `<...>.gt7rec.part`; a clean
stop renames it. A leftover `.part` file is an aborted recording.

Layout:

1. One line of UTF-8 JSON terminated by `\n`:
   `{"format": "racecast-gt7rec", "version": 1, "profile": "<name>",
   "started": "<ISO 8601 local time with offset>", "relay_version": "<version>"}`
2. Records until end of file, each:
   - `float64` little-endian, wall-clock seconds (`time.time()`)
   - `uint8`, packet type as its ASCII byte (`A`, `B`, `~`)
   - `uint16` little-endian, payload length
   - the decrypted payload bytes

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

`TelemetryEngine._finalise_lap` returns a lap record
`{lap, start, end, elapsed, status, reason, fuel_used, top_speed}` (`status` is
`reference`, `counted` or `not counted`) in addition to logging it, and
`TelemetryEngine` exposes a `session` counter that `_reset_session` increments. Live
behaviour does not change.

## Module `src/scripts/gt7_recording.py`

Stdlib only, no relay imports.

- `RecordingWriter(dir, profile, relay_version)`: `put(wall_ts, kind, plain)` enqueues
  into a bounded queue (`maxsize` 600, about 10 s) and never blocks. A full queue drops
  the packet and increments `dropped`. A writer thread opens the `.part` file on the
  first packet, writes buffered and flushes about once per second. `close()` drains the
  queue, flushes and renames. An `OSError` stops the recording, sets `error` to the
  message and logs a warning; the relay keeps running.
- `read_recording(path)` -> `(header, iterator of (wall_ts, kind, plain))`. A truncated
  last record is skipped silently. An unknown `format` or a `version` above 1 raises
  `RecordingError` with a clear message.
- `export_csv(path, out_dir, include_all=False, excel=False)` writes `samples.csv` and
  `laps.csv` (below).
- `list_recordings(dir)` -> name, size, start, duration, lap count, `aborted` flag.

## Switch and state

The live state is resolved in this order:

1. `runtime/<profile>/telemetry-record.json` `{"active": bool}`, when present and valid.
   Every live toggle writes it atomically (same pattern as `telemetry-view.json`).
2. Otherwise the profile key `TELEMETRY_RECORD`, injected as
   `RACECAST_TELEMETRY_RECORD`. `1`, `true`, `yes`, `on` (any case) mean on; anything
   else, including unset, means off.

`event_start` removes `telemetry-record.json` when it records a new session start
(`_new_session` and not `_is_continuation_start`). A recovery restart with `--stint` or
`--part` keeps it.

When the state is on, the writer opens a new file on the first packet after relay start
or after a toggle to on, so a relay without a console never creates empty files. Every
relay start that records starts a new file. Stopping the relay closes the recording
cleanly.

## Relay

- `TelemetryStore` takes an optional recorder. `_telemetry_loop` calls
  `store.record(time.time(), kind, plain)` for every packet it accepts (after the
  source check and `decrypt_typed`), including menu, pause and replay packets. The call
  only enqueues, outside the store lock.
- `GET /telemetry/record/start|stop|toggle` -> `{"active", "file", "since"}`; 404 when
  `telemetry_store` is None, like the other `/telemetry/*` routes. `console_policy`
  requires DIRECTOR, like `/telemetry/show|hide|toggle`.
- `/status` extends the existing `telemetry` block with
  `record: {active, file, since, bytes, dropped, error}`.

## CLI

New group `racecast telemetry`. Help strings stay ASCII.

- `record start|stop|status`: talks to the local relay through `http_util`. Without a
  running relay it prints a clear error and exits non-zero.
- `list`: the active profile's recordings (name, size, start, duration, laps, aborted).
- `export <name|latest> [--out DIR] [--all] [--excel]`: writes into `<name>/` next to
  the recording, or into `DIR`.
- `delete <name>`: refuses the recording that is currently open.

`list`, `export` and `delete` read files only and do not need a relay. `--profile`
works as for every other command.

## CSV export

Units are always metric, independent of `RACECAST_TELEMETRY_UNITS`. Fields a packet
does not carry are empty (steering in an `A` packet, for example). By default only
packets with `on_track` and not `paused`/`loading` are exported; `--all` keeps every
packet.

`samples.csv`, one row per packet:

`t_s, session, lap, lap_t_s, on_track, paused, speed_kmh, throttle_pct, brake_pct,
throttle_input_pct, brake_input_pct, steer_deg, gear, rpm, fuel_l, tyre_fl_c,
tyre_fr_c, tyre_rl_c, tyre_rr_c, pos_x, pos_y, pos_z, car_id`

- `t_s` counts from the first record, `lap_t_s` from the engine's lap-change edge.
- `session` starts at 1 and follows the engine's session counter.

`laps.csv`, one row per lap the engine finalised:

`session, lap, start_t_s, end_t_s, gt7_time_s, relay_time_s, status, reason,
fuel_used_l, top_speed_kmh`

- `gt7_time_s` is the first `last_ms` that changes within the first 3 s of the
  following lap (GT7 updates it shortly after the line). Empty if none arrives.
- `relay_time_s`, `status` and `reason` come from the engine's lap record.

The exporter replays the packets through the same `parse_packet` and `TelemetryEngine`
the HUD uses, so the lap verdicts match what the broadcast showed.

`--excel` writes `;` as separator, `,` as decimal mark and UTF-8 with BOM, so a double
click opens the file correctly in a German Excel. The default is `,` and `.` for pandas,
LibreOffice and Google Sheets. When packets were dropped, the exporter prints the count.

## Surfaces

- **Director Panel:** a `REC` button next to `TELEMETRY` in the solo `vis` list,
  `relay: "telemetry-record"`. Hidden when `/status` has no telemetry block, red with
  the elapsed time while recording, yellow when `record.error` is set.
  `director-panel.png` is refreshed in the same PR.
- **Control Center:** `profile_admin` writes an empty `TELEMETRY_RECORD=` into new solo
  POV profiles. No other change, so no `cc-*.png` refresh.

## Error handling

| Case | Behaviour |
|---|---|
| disk full, write error | recording stops, `record.error` set, panel button yellow; live telemetry and HUD continue |
| queue full | packet dropped, `dropped` counted, exporter reports it |
| relay crash | `.part` file readable up to the last complete record; `list` marks it aborted, `export` works |
| unknown file version | export aborts with a clear message |
| toggle without solo POV | 404 from the relay, CLI prints why |

## Tests

TDD, failing test first.

- `tests/test_gt7_recording.py`: writer/reader round-trip, truncated tail, queue
  overflow, write error, header validation, CSV columns and filtering, `--excel`
  formatting, lap rows and `gt7_time_s` on synthetic packets.
- `tests/test_gt7_fixture.py`: gear, rpm and position on the real packets.
- `tests/test_gt7_telemetry.py`: lap record returned by `_finalise_lap`, session counter.
- `tests/test_telemetry_endpoints.py`: record endpoints, state precedence (file over
  profile key), `/status` block, file opened only on the first packet.
- `console_policy`, CLI dispatch, the `event_start` reset, the pre-filled profile key.
- Director Panel: `ui-visual-verification`, then `wiki-screenshots` for
  `director-panel.png`.

## Docs

- `src/relay/CLAUDE.md`: the GT7 telemetry paragraph gains the recorder.
- Wiki `Relay-Mode.md` (telemetry section): recording, the switch and the export.
- `src/docs/wiki/Director.md`: the `REC` button.

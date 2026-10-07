# GT7 Telemetry Recording Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In a solo POV broadcast the relay records every decrypted GT7 telemetry packet to disk, toggled by a profile key and live by the director, and the CLI exports a recording to CSV.

**Architecture:** A new stdlib module `src/scripts/gt7_recording.py` owns the file format (writer thread, reader, partial-file finalisation), the live on/off switch (`RecordControl`) and the CSV exporter. The relay hands each accepted packet to the switch through `TelemetryStore`, serves `/telemetry/record/*` and reports state in `/status`. The exporter replays a recording through the same `parse_packet` and `TelemetryEngine` the HUD uses, so lap verdicts match the broadcast.

**Tech Stack:** Python 3 stdlib only (`struct`, `queue`, `threading`, `csv`, `json`). Tests are runnable stdlib scripts under `tests/` (no pytest), one file per area.

**Spec:** `docs/superpowers/specs/2026-10-07-gt7-telemetry-recording-design.md`. Issue #785.

## Global Constraints

- Edit only under `src/`, `tests/`, `tools/`, `docs/`. Never touch `dist/` or `runtime/`.
- Code, comments, docs, help text: English only. argparse/usage help strings ASCII only (`->`, not arrows).
- Comments minimal: one reason, one sentence. No investigation history in code.
- Every text-mode `subprocess` call passes `errors="replace"` (none expected here).
- Outbound HTTP from `racecast.py` goes through `http_util` (`http_util.get_json`, `http_util.get_bytes`).
- Telemetry path is best-effort: nothing in recording may raise into `_telemetry_loop` or a request handler.
- Tests run on any machine and on Windows CI: use `tempfile`, `os.path.join` for local paths, no real IPs (console IPs are `100.64.0.0/10` test constants).
- Recording format constants, verbatim: `FORMAT = "racecast-gt7rec"`, `VERSION = 1`, record header `struct "<dBH"` (float64 wall ts, uint8 kind byte, uint16 length), suffix `.gt7rec`, open-file suffix `.gt7rec.part`, queue `maxsize` 600, flush about every 1 s.
- Meta record: kind byte `0x00`, payload UTF-8 JSON `{"dropped": <total>}`, written at each flush when the drop count changed and at close. Readers skip it for packets and keep the latest `dropped`.
- State precedence: `runtime/<profile>/telemetry-record.json` `{"active": bool}` when valid, else `RACECAST_TELEMETRY_RECORD` (`1`, `true`, `yes`, `on`, any case = on; anything else = off).
- Run single test files with `python3 tests/<file>.py`; one function with `python3 -c "import sys; sys.path.insert(0,'tests'); import <mod> as t; t.<fn>()"`. Lint with `python3 tools/lint.py` after every Python change.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/scripts/gt7_telemetry.py` | modify | parse `gear`, `rpm`, `pos_x/y/z`; engine emits lap records, counts sessions, exposes lap start; `TelemetryStore.recorder` |
| `src/scripts/gt7_recording.py` | create | file format, `RecordingWriter`, `Recording` reader, `finalize_partials`, `list_recordings`, `RecordControl`, `record_default`, `export_csv` |
| `src/relay/racecast-feeds.py` | modify | feed packets to the recorder, `/telemetry/record/*`, `/status` record block, startup wiring, shutdown close |
| `src/scripts/console_policy.py` | modify | `/telemetry/record/*` requires DIRECTOR |
| `src/scripts/config.py` | modify | `ResolvedConfig.telemetry_record` from `TELEMETRY_RECORD` |
| `src/racecast.py` | modify | inject `RACECAST_TELEMETRY_RECORD`; `event start` resets the state file; `racecast telemetry` group |
| `src/scripts/profile_admin.py` | modify | pre-fill `TELEMETRY_RECORD=` in new solo POV profiles |
| `tools/build-binary.py` | modify | hidden imports `gt7_recording`, `gt7_telemetry` |
| `src/director/director-panel.html` | modify | `REC` key |
| `tests/test_gt7_telemetry.py`, `tests/test_gt7_fixture.py`, `tests/test_gt7_recording.py` (new), `tests/test_telemetry_endpoints.py`, `tests/test_console.py`, `tests/test_racecast.py`, `tests/test_config.py`, `tests/test_profile.py` | modify/create | tests per task |
| `src/relay/CLAUDE.md`, `src/docs/wiki/Relay-Mode.md`, `src/docs/wiki/Director.md`, `src/docs/wiki/Configuration.md`, `src/docs/wiki/images/director-panel.png` | modify | docs + screenshot |

---

### Task 1: Parse gear, rpm and position

**Files:**
- Modify: `src/scripts/gt7_telemetry.py` (offset constants near line 23-46, `GT7Packet` line 86, `parse_packet` line 107, `_sanitize` line 205)
- Test: `tests/test_gt7_telemetry.py`, `tests/test_gt7_fixture.py`

**Interfaces:**
- Produces: `GT7Packet` gains fields `gear` (int 0-15), `rpm` (float), `pos_x`, `pos_y`, `pos_z` (float). They are appended at the END of the namedtuple field list so existing positional users keep working; all have default `None`. Constants `OFF_POS = 0x04`, `OFF_RPM = 0x3C`, `OFF_GEAR = 0x90`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_gt7_telemetry.py`, extend `_packet` so tests can set the new fields. Add these lines inside `_packet`, before `return bytes(b)`:

```python
    struct.pack_into("<3f", b, tm.OFF_POS, *kw.get("pos", (0.0, 0.0, 0.0)))
    struct.pack_into("<f", b, tm.OFF_RPM, kw.get("rpm", 0.0))
    b[tm.OFF_GEAR] = kw.get("gear_byte", 0)
```

Add after `t_parse_car_id`:

```python
def t_parse_gear_rpm_position():
    p = tm.parse_packet(_packet(gear_byte=0x24, rpm=7350.5, pos=(1.5, -2.0, 300.25)))
    assert p.gear == 4, "gear is the low nibble of 0x90; the high nibble is ignored"
    assert abs(p.rpm - 7350.5) < 1e-3
    assert (p.pos_x, p.pos_y, p.pos_z) == (1.5, -2.0, 300.25)


def t_sanitize_keeps_last_rpm_and_position_on_nan():
    eng = tm.TelemetryEngine()
    eng.update(tm.parse_packet(_packet(rpm=5000.0, pos=(1.0, 2.0, 3.0))), 1.0)
    nan = float("nan")
    eng.update(tm.parse_packet(_packet(rpm=nan, pos=(nan, nan, nan))), 1.1)
    assert eng._last.rpm == 5000.0, "a non-finite rpm keeps the previous reading"
    assert (eng._last.pos_x, eng._last.pos_y, eng._last.pos_z) == (1.0, 2.0, 3.0)
```

In `tests/test_gt7_fixture.py`, add (values measured on the captured PS5 packets):

```python
def t_fixture_gear_rpm_position():
    expect = {
        "PKT_THROTTLE_HEX": (5, 7796.0, (-627.39, 12.26, -229.95)),
        "PKT_BRAKE_HEX": (6, 6859.0, (-775.91, 17.54, -417.9)),
        "PKT_LAP_HEX": (5, 7932.0, (-655.33, 12.87, -245.04)),
        "EXT_TCS_HEX": (4, 11757.0, (-997.69, 144.8, 1802.85)),
    }
    for name, (gear, rpm, pos) in expect.items():
        p = tm.parse_packet(gc.decrypt_packet(bytes.fromhex(globals()[name])))
        assert p.gear == gear, (name, p.gear)
        assert abs(p.rpm - rpm) < 1.0, (name, p.rpm)
        got = (round(p.pos_x, 2), round(p.pos_y, 2), round(p.pos_z, 2))
        assert got == pos, (name, got)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_gt7_telemetry.py; python3 tests/test_gt7_fixture.py`
Expected: FAIL with `AttributeError: module 'gt7_telemetry' has no attribute 'OFF_POS'` (first file) and `AttributeError: 'GT7Packet' object has no attribute 'gear'` (second).

- [ ] **Step 3: Implement**

In `src/scripts/gt7_telemetry.py`, add to the offset block (after `OFF_MAGIC`):

```python
OFF_POS = 0x04          # car position x/y/z, metres (float x3)
OFF_RPM = 0x3C          # engine rpm (float)
OFF_GEAR = 0x90         # low nibble = current gear (uint8)
```

Replace the `GT7Packet` definition:

```python
GT7Packet = namedtuple("GT7Packet", [
    "speed_mps", "fuel_level", "fuel_capacity", "tyre_temp",
    "throttle", "brake", "lap", "best_ms", "last_ms", "day_ms",
    "flags", "on_track", "paused", "loading",
    "car_id",
    "steer_rad", "sway", "heave", "surge", "throttle_input", "brake_input",
    "gear", "rpm", "pos_x", "pos_y", "pos_z",
], defaults=(None,) * 12)   # None when the packet is too short to carry the field
```

In `parse_packet`, add before the closing `)` of the `GT7Packet(...)` call:

```python
        gear=plain[OFF_GEAR] & 0x0F,
        rpm=struct.unpack_from("<f", plain, OFF_RPM)[0],
        pos_x=struct.unpack_from("<f", plain, OFF_POS)[0],
        pos_y=struct.unpack_from("<f", plain, OFF_POS + 4)[0],
        pos_z=struct.unpack_from("<f", plain, OFF_POS + 8)[0],
```

In `_sanitize`, add to the `pkt._replace(...)` call:

```python
        rpm=keep("rpm", 0.0),
        pos_x=keep("pos_x", 0.0),
        pos_y=keep("pos_y", 0.0),
        pos_z=keep("pos_z", 0.0),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_gt7_telemetry.py && python3 tests/test_gt7_fixture.py && python3 tests/test_telemetry_endpoints.py && python3 tools/lint.py`
Expected: each prints `ALL PASS` (or its last `ok` line), lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/scripts/gt7_telemetry.py tests/test_gt7_telemetry.py tests/test_gt7_fixture.py
git commit -m "feat(telemetry): parse gear, rpm and car position from GT7 packets (#785)"
```

---

### Task 2: Lap records and session counter in the engine

**Files:**
- Modify: `src/scripts/gt7_telemetry.py` (`_LapAccumulator` line 225, `TelemetryEngine.__init__` line 294, `_reset_session` line 322, `_finalise_lap` line 380)
- Test: `tests/test_gt7_telemetry.py`

**Interfaces:**
- Consumes: Task 1's `GT7Packet`.
- Produces:
  - `TelemetryEngine.on_lap`: `None` or a callable taking one dict, called once per lap the engine closes (finalised at a lap edge, or abandoned at a session change). Dict keys: `session` (int), `lap` (int), `start` (float, wall ts of the lap's first packet), `end` (float, wall ts of its last packet), `elapsed` (float s, the relay's lap time), `status` (`"reference" | "counted" | "not counted"`), `reason` (str, `""` unless not counted), `fuel_used` (float L or None), `top_speed_mps` (float).
  - `TelemetryEngine.session`: int, starts at 1, +1 at every session boundary.
  - `TelemetryEngine.lap_started_at()`: wall ts of the current lap's first packet, or None.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gt7_telemetry.py` (after `t_laplog_session_change_without_reference`; `_feed_lap` and `_drive` already exist in the file):

```python
def t_engine_emits_lap_records():
    eng = tm.TelemetryEngine()
    laps = []
    eng.on_lap = laps.append
    eng.update(tm.parse_packet(_packet(lap=0)), 99.0)
    t = _feed_lap(eng, 100.0, 1, duration=10.0, speed=50.0)
    _feed_lap(eng, t, 2, duration=11.0, speed=60.0)   # its last packet opens lap 3
    assert [r["lap"] for r in laps] == [0, 1, 2], laps
    assert laps[0]["status"] == "not counted" and "partial" in laps[0]["reason"]
    assert laps[1]["status"] == "reference" and laps[1]["reason"] == ""
    assert laps[2]["status"] == "counted"
    assert abs(laps[2]["top_speed_mps"] - 60.0) < 1e-6
    assert laps[1]["start"] == 100.0 and laps[1]["end"] < laps[2]["start"]
    assert all(r["session"] == 1 for r in laps)


def t_engine_session_change_emits_abandoned_lap_and_counts_session():
    eng = tm.TelemetryEngine()
    laps = []
    eng.on_lap = laps.append
    eng.update(tm.parse_packet(_packet(lap=1)), 99.0)
    t = _feed_lap(eng, 100.0, 2, duration=5.0)   # ends with lap 3's first packet at t
    assert eng.session == 1
    eng.update(tm.parse_packet(_packet(lap=0, speed_mps=0.0)), t)
    assert eng.session == 2
    last = laps[-1]
    assert (last["lap"], last["status"], last["reason"], last["session"]) == \
        (3, "not counted", "session change", 1), last
    assert eng.lap_started_at() == t


def t_engine_without_on_lap_is_unchanged():
    eng = tm.TelemetryEngine()
    assert eng.on_lap is None and eng.lap_started_at() is None
    eng.update(tm.parse_packet(_packet(lap=1)), 1.0)
    assert eng.lap_started_at() == 1.0
```

`_feed_lap(eng, t0, lap, *, duration, dt=0.1, speed=50.0, ...)` already exists in the file: it drives `lap` for `duration` seconds, then sends one packet with `lap + 1` (the lap-change edge) and returns that packet's timestamp.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_gt7_telemetry.py`
Expected: FAIL with `AttributeError` on `on_lap` / `session`.

- [ ] **Step 3: Implement**

`_LapAccumulator`: add `"top_speed"` to `__slots__`, `self.top_speed = 0.0` in `__init__`, and in `add()` directly after the `if pkt.paused or pkt.loading or not pkt.on_track:` block:

```python
        if pkt.speed_mps > self.top_speed:
            self.top_speed = pkt.speed_mps
```

`TelemetryEngine.__init__`: add

```python
        self.session = 1
        self.on_lap = None            # callable(dict) per closed lap; the recording exporter sets it
```

Add methods to `TelemetryEngine`:

```python
    def lap_started_at(self):
        """Wall time of the current lap's first packet, or None before any packet."""
        return self._acc.t0 if self._acc is not None else None

    def _emit_lap(self, acc, status, reason):
        if self.on_lap is None:
            return
        fuel = (acc.fuel_start - acc.fuel_end
                if acc.fuel_start is not None and acc.fuel_end is not None else None)
        self.on_lap({"session": self.session, "lap": self._lap_num, "start": acc.t0,
                     "end": acc.last_t, "elapsed": acc.elapsed, "status": status,
                     "reason": reason, "fuel_used": fuel, "top_speed_mps": acc.top_speed})
```

`_reset_session`: inside the existing `if acc is not None:` block, after the `LOG.info(...)`, add `self._emit_lap(acc, "not counted", "session change")`. After the block add `self.session += 1`.

`_finalise_lap`: in the `if why is not None:` branch, call `self._emit_lap(acc, "not counted", why)` before `return`. Replace the reference/counted logging with:

```python
        if self._ref is None or acc.elapsed < self._ref["time"]:
            self._ref = {"time": acc.elapsed, "samples": acc.samples}
            LOG.info("%s: new reference, delta vs this lap from now on", head)
            self._emit_lap(acc, "reference", "")
        else:
            LOG.info("%s: counted", head)
            self._emit_lap(acc, "counted", "")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_gt7_telemetry.py && python3 tests/test_telemetry_endpoints.py && python3 tools/lint.py`
Expected: ALL PASS, lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/scripts/gt7_telemetry.py tests/test_gt7_telemetry.py
git commit -m "feat(telemetry): emit lap records and count sessions in the engine (#785)"
```

---

### Task 3: Recording file format, writer, reader, listing

**Files:**
- Create: `src/scripts/gt7_recording.py`
- Create: `tests/test_gt7_recording.py`

**Interfaces:**
- Consumes: `gt7_telemetry.OFF_LAP` for lap counting in `list_recordings`.
- Produces (all in `gt7_recording`):
  - Constants `FORMAT`, `VERSION`, `SUFFIX = ".gt7rec"`, `PART = ".part"`, `QUEUE_MAX = 600`, `FLUSH_S = 1.0`.
  - `class RecordingError(Exception)`.
  - `class RecordingWriter(rec_dir, profile, relay_version, queue_max=QUEUE_MAX, flush_s=FLUSH_S)`: `put(wall_ts, kind, plain)` (kind is `"A"`, `"B"` or `"~"`), `close(timeout=5.0)`; attributes `path` (current file path or None), `started` (wall ts or None), `bytes`, `dropped`, `error` (str or None).
  - `class Recording(path)`: `header` (dict), `packets()` generator of `(wall_ts, kind, plain)`, `dropped` (int, valid after `packets()` is exhausted).
  - `finalize_partials(rec_dir) -> list[str]` of new file names.
  - `list_recordings(rec_dir) -> list[dict]` with keys `name`, `path`, `size`, `started`, `duration_s`, `laps`, `partial`, sorted by name.
  - `recording_stem(path) -> str`: file name without `.gt7rec` / `.gt7rec.part`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gt7_recording.py`:

```python
#!/usr/bin/env python3
"""GT7 telemetry recording: file format, writer, reader, switch and CSV export.
Run: python3 tests/test_gt7_recording.py"""
import importlib.util, json, os, struct, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))   # gt7_recording imports gt7_telemetry


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


tm = _load("gt7_telemetry", ("src", "scripts", "gt7_telemetry.py"))
rec = _load("gt7_recording", ("src", "scripts", "gt7_recording.py"))


def _plain(lap=1, size=0x128, fill=0):
    b = bytearray([fill]) * size
    struct.pack_into("<I", b, 0, 0x47375330)
    struct.pack_into("<h", b, tm.OFF_LAP, lap)
    return bytes(b)


def _write(d, items, **kw):
    w = rec.RecordingWriter(d, "Demo", "dev", flush_s=0.05, **kw)
    for ts, kind, plain in items:
        w.put(ts, kind, plain)
    w.close()
    return w


def t_roundtrip_header_and_packets():
    with tempfile.TemporaryDirectory() as d:
        items = [(1000.0 + i / 60, "~", _plain(lap=1, size=0x158, fill=i)) for i in range(5)]
        w = _write(d, items)
        assert w.error is None and w.path.endswith(".gt7rec"), w.path
        assert not os.path.exists(w.path + ".part"), "a clean close drops .part"
        r = rec.Recording(w.path)
        assert r.header["format"] == "racecast-gt7rec" and r.header["version"] == 1
        assert r.header["profile"] == "Demo" and r.header["relay_version"] == "dev"
        assert list(r.packets()) == items
        assert r.dropped == 0


def t_writer_opens_no_file_without_packets():
    with tempfile.TemporaryDirectory() as d:
        w = _write(d, [])
        assert w.path is None and os.listdir(d) == [], os.listdir(d)


def t_name_is_local_start_time_and_never_collides():
    with tempfile.TemporaryDirectory() as d:
        ts = time.mktime((2026, 10, 7, 20, 15, 3, 0, 0, -1))
        a = _write(d, [(ts, "A", _plain())]).path
        b = _write(d, [(ts, "A", _plain())]).path
        assert os.path.basename(a) == "20261007-201503.gt7rec", a
        assert os.path.basename(b) == "20261007-201503-2.gt7rec", b


def t_truncated_tail_is_skipped():
    with tempfile.TemporaryDirectory() as d:
        w = _write(d, [(1.0, "A", _plain()), (2.0, "A", _plain())])
        with open(w.path, "r+b") as fh:
            fh.truncate(os.path.getsize(w.path) - 10)
        assert [p[0] for p in rec.Recording(w.path).packets()] == [1.0]


def t_drop_count_is_persisted():
    with tempfile.TemporaryDirectory() as d:
        w = rec.RecordingWriter(d, "Demo", "dev", flush_s=0.05)
        w.put(1.0, "A", _plain())
        w.dropped = 3                           # as if put() had met a full queue three times
        w.close()
        r = rec.Recording(w.path)
        assert [p[0] for p in r.packets()] == [1.0], "the meta record is not a packet"
        assert r.dropped == 3, r.dropped


def t_put_never_blocks_when_full():
    import queue as _queue
    with tempfile.TemporaryDirectory() as d:
        w = rec.RecordingWriter(d, "Demo", "dev", flush_s=0.05)
        w.close()                               # writer thread gone, nothing drains the queue
        w._stop.clear()
        w._q = _queue.Queue(maxsize=1)
        w.put(1.0, "A", _plain())
        t0 = time.monotonic()
        w.put(2.0, "A", _plain())               # full -> dropped, returns at once
        assert time.monotonic() - t0 < 0.5 and w.dropped == 1


def t_write_error_stops_and_reports():
    with tempfile.TemporaryDirectory() as d:
        blocker = os.path.join(d, "file")
        with open(blocker, "w") as fh:
            fh.write("x")
        w = _write(os.path.join(blocker, "sub"), [(1.0, "A", _plain())])   # dir under a file
        assert w.error, "an unwritable directory must surface as error, not raise"
        w.put(2.0, "A", _plain())               # ignored after an error


def t_reader_rejects_foreign_and_newer_files():
    with tempfile.TemporaryDirectory() as d:
        bad = os.path.join(d, "x.gt7rec")
        with open(bad, "wb") as fh:
            fh.write(b"hello\n")
        try:
            rec.Recording(bad); raise AssertionError("foreign file accepted")
        except rec.RecordingError as e:
            assert "not a racecast telemetry recording" in str(e)
        with open(bad, "wb") as fh:
            fh.write((json.dumps({"format": "racecast-gt7rec", "version": 99}) + "\n").encode())
        try:
            rec.Recording(bad); raise AssertionError("newer version accepted")
        except rec.RecordingError as e:
            assert "version 99" in str(e) and "update racecast" in str(e)


def t_finalize_partials_renames_leftovers():
    with tempfile.TemporaryDirectory() as d:
        w = _write(d, [(1.0, "A", _plain())])
        os.replace(w.path, w.path + ".part")
        assert rec.finalize_partials(d) == [os.path.basename(w.path)]
        assert os.path.exists(w.path) and rec.finalize_partials(d) == []
        assert rec.finalize_partials(os.path.join(d, "missing")) == []


def t_list_recordings_reports_duration_laps_partial():
    with tempfile.TemporaryDirectory() as d:
        items = [(10.0, "A", _plain(lap=1)), (20.0, "A", _plain(lap=2)),
                 (30.0, "A", _plain(lap=3))]
        w = _write(d, items)
        os.replace(w.path, w.path + ".part")
        (row,) = rec.list_recordings(d)
        assert row["name"] == os.path.basename(w.path) + ".part"
        assert row["duration_s"] == 20.0 and row["laps"] == 2 and row["partial"] is True
        assert rec.recording_stem(row["path"]) == os.path.basename(w.path)[:-len(".gt7rec")]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_gt7_recording.py`
Expected: FAIL with `FileNotFoundError` for `gt7_recording.py`.

- [ ] **Step 3: Implement `src/scripts/gt7_recording.py` (file format part)**

```python
#!/usr/bin/env python3
"""GT7 telemetry recording: the relay's decrypted packets on disk, read back, exported.

Stdlib only and no relay imports, so `racecast telemetry export` runs without a relay.
File: one JSON header line, then records of float64 wall ts, uint8 kind, uint16
length and the payload. Kind 0x00 is a meta record carrying {"dropped": n}.
"""
import datetime
import json
import logging
import os
import queue
import struct
import threading
import time

import gt7_telemetry

LOG = logging.getLogger("racecast.relay.telemetry")

FORMAT = "racecast-gt7rec"
VERSION = 1
SUFFIX = ".gt7rec"
PART = ".part"
QUEUE_MAX = 600               # about 10 s of packets at 60 Hz
FLUSH_S = 1.0
_REC = struct.Struct("<dBH")
_META = 0x00


class RecordingError(Exception):
    """A file that is not a readable racecast telemetry recording."""


def _encode(wall_ts, kind_byte, payload):
    return _REC.pack(wall_ts, kind_byte, len(payload)) + payload


def _header(profile, relay_version, started_ts):
    started = datetime.datetime.fromtimestamp(started_ts).astimezone().isoformat(
        timespec="seconds")
    return (json.dumps({"format": FORMAT, "version": VERSION, "profile": profile,
                        "started": started, "relay_version": relay_version})
            + "\n").encode("utf-8")


def _free_path(rec_dir, stem):
    """`<stem>.gt7rec` in rec_dir, or `<stem>-N.gt7rec` when that name (or its .part) exists."""
    n = 1
    while True:
        name = stem + ("" if n == 1 else f"-{n}") + SUFFIX
        path = os.path.join(rec_dir, name)
        if not os.path.exists(path) and not os.path.exists(path + PART):
            return path
        n += 1


def recording_stem(path):
    name = os.path.basename(path)
    if name.endswith(PART):
        name = name[:-len(PART)]
    return name[:-len(SUFFIX)] if name.endswith(SUFFIX) else name


class RecordingWriter:
    """Writes packets to `<rec_dir>/<start>.gt7rec.part` from its own thread and
    renames the file on close. put() never blocks: a full queue drops the packet."""

    def __init__(self, rec_dir, profile, relay_version, queue_max=QUEUE_MAX,
                 flush_s=FLUSH_S):
        self._dir = rec_dir
        self._profile = profile
        self._relay_version = relay_version
        self._flush_s = flush_s
        self._q = queue.Queue(maxsize=queue_max)
        self._stop = threading.Event()
        self.path = None
        self.started = None
        self.bytes = 0
        self.dropped = 0
        self.error = None
        self._thread = threading.Thread(target=self._run, name="gt7-recorder", daemon=True)
        self._thread.start()

    def put(self, wall_ts, kind, plain):
        if self.error is not None or self._stop.is_set():
            return
        try:
            self._q.put_nowait((wall_ts, kind, plain))
        except queue.Full:
            self.dropped += 1

    def close(self, timeout=5.0):
        self._stop.set()
        self._thread.join(timeout)

    def _open(self, first_ts):
        os.makedirs(self._dir, exist_ok=True)
        stem = time.strftime("%Y%m%d-%H%M%S", time.localtime(first_ts))
        path = _free_path(self._dir, stem)
        fh = open(path + PART, "wb")
        head = _header(self._profile, self._relay_version, first_ts)
        fh.write(head)
        self.started = first_ts
        self.bytes = len(head)
        self.path = path              # last: a visible path always has a start time
        return fh

    def _write(self, fh, data):
        fh.write(data)
        self.bytes += len(data)

    def _run(self):
        fh = None
        written_drops = 0
        last_flush = time.monotonic()
        try:
            while True:
                try:
                    item = self._q.get(timeout=self._flush_s)
                except queue.Empty:
                    item = None
                if item is not None:
                    wall_ts, kind, plain = item
                    if fh is None:
                        fh = self._open(wall_ts)
                    self._write(fh, _encode(wall_ts, ord(kind), plain))
                now = time.monotonic()
                done = item is None and self._stop.is_set()
                if fh is not None and (done or now - last_flush >= self._flush_s):
                    if self.dropped != written_drops:
                        written_drops = self.dropped
                        meta = json.dumps({"dropped": written_drops}).encode("utf-8")
                        self._write(fh, _encode(time.time(), _META, meta))
                    fh.flush()
                    last_flush = now
                if done:
                    break
        except OSError as e:
            self.error = str(e)
            LOG.warning("telemetry recording stopped: %s", e)
        finally:
            if fh is not None:
                try:
                    fh.close()
                    if self.error is None:
                        os.replace(self.path + PART, self.path)
                except OSError as e:
                    self.error = self.error or str(e)


class Recording:
    """Read access to one recording file (finished or .part)."""

    def __init__(self, path):
        self.path = path
        self.dropped = 0
        try:
            with open(path, "rb") as fh:
                line = fh.readline()
                self._offset = fh.tell()
        except OSError as e:
            raise RecordingError(f"{path}: {e}") from e
        try:
            header = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            header = None
        if not isinstance(header, dict) or header.get("format") != FORMAT:
            raise RecordingError(f"{path}: not a racecast telemetry recording")
        version = header.get("version")
        if not isinstance(version, int) or version > VERSION:
            raise RecordingError(
                f"{path}: format version {version} is newer than this racecast "
                f"reads ({VERSION}); update racecast")
        self.header = header

    def packets(self):
        """Yield (wall_ts, kind, plain) per packet; a truncated last record ends it."""
        with open(self.path, "rb") as fh:
            fh.seek(self._offset)
            while True:
                head = fh.read(_REC.size)
                if len(head) < _REC.size:
                    return
                wall_ts, kind, n = _REC.unpack(head)
                payload = fh.read(n)
                if len(payload) < n:
                    return
                if kind == _META:
                    try:
                        self.dropped = int(json.loads(payload.decode("utf-8"))["dropped"])
                    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
                        pass  # a damaged meta record only loses the drop count
                    continue
                yield wall_ts, chr(kind), payload


def finalize_partials(rec_dir):
    """Rename every leftover `.gt7rec.part` in rec_dir to `.gt7rec`; returns the new names."""
    try:
        names = sorted(os.listdir(rec_dir))
    except OSError:
        return []
    done = []
    for name in names:
        if not name.endswith(SUFFIX + PART):
            continue
        target = os.path.join(rec_dir, name[:-len(PART)])
        if os.path.exists(target):    # _free_path alone would count this .part as taken
            target = _free_path(rec_dir, name[:-len(SUFFIX + PART)])
        try:
            os.replace(os.path.join(rec_dir, name), target)
            done.append(os.path.basename(target))
        except OSError as e:
            LOG.warning("could not finalise telemetry recording %s: %s", name, e)
    return done


def list_recordings(rec_dir):
    """One dict per readable recording in rec_dir, sorted by name."""
    try:
        names = sorted(os.listdir(rec_dir))
    except OSError:
        return []
    rows = []
    for name in names:
        if not (name.endswith(SUFFIX) or name.endswith(SUFFIX + PART)):
            continue
        path = os.path.join(rec_dir, name)
        try:
            r = Recording(path)
        except RecordingError:
            continue
        first = last = prev_lap = None
        laps = 0
        for wall_ts, _kind, plain in r.packets():
            first = wall_ts if first is None else first
            last = wall_ts
            lap = struct.unpack_from("<h", plain, gt7_telemetry.OFF_LAP)[0]
            if prev_lap is not None and lap != prev_lap:
                laps += 1
            prev_lap = lap
        rows.append({"name": name, "path": path, "size": os.path.getsize(path),
                     "started": r.header.get("started", ""),
                     "duration_s": (last - first) if first is not None else 0.0,
                     "laps": laps, "partial": name.endswith(PART)})
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_gt7_recording.py && python3 tools/lint.py`
Expected: ALL PASS, lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/scripts/gt7_recording.py tests/test_gt7_recording.py
git commit -m "feat(telemetry): recording file format with writer, reader and listing (#785)"
```

---

### Task 4: CSV export

**Files:**
- Modify: `src/scripts/gt7_recording.py`
- Test: `tests/test_gt7_recording.py`

**Interfaces:**
- Consumes: Task 2 (`on_lap`, `session`, `lap_started_at`), Task 3 (`Recording`, `recording_stem`).
- Produces: `export_csv(path, out_dir, include_all=False, excel=False) -> dict` with keys `dir`, `samples` (rows written), `laps`, `dropped`. Constants `SAMPLE_COLUMNS`, `LAP_COLUMNS`, `GT7_TIME_WINDOW_S = 3.0`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gt7_recording.py` (above the `__main__` block). The `_tpkt` builder sets the fields the engine and the exporter read:

```python
import csv  # noqa: E402


def _tpkt(lap, speed=50.0, last_ms=-1, flags=None, throttle=255, steer=None):
    size = 0x158 if steer is not None else 0x128
    b = bytearray(size)
    struct.pack_into("<I", b, 0, 0x47375330)
    struct.pack_into("<f", b, tm.OFF_SPEED, speed)
    struct.pack_into("<f", b, tm.OFF_FUEL_LEVEL, 50.0)
    struct.pack_into("<f", b, tm.OFF_FUEL_CAP, 100.0)
    for off in (tm.OFF_TYRE_FL, tm.OFF_TYRE_FR, tm.OFF_TYRE_RL, tm.OFF_TYRE_RR):
        struct.pack_into("<f", b, off, 80.0)
    struct.pack_into("<h", b, tm.OFF_LAP, lap)
    struct.pack_into("<i", b, tm.OFF_BEST_MS, -1)
    struct.pack_into("<i", b, tm.OFF_LAST_MS, last_ms)
    struct.pack_into("<H", b, tm.OFF_FLAGS, tm.FLAG_ON_TRACK if flags is None else flags)
    b[tm.OFF_THROTTLE] = throttle
    struct.pack_into("<f", b, tm.OFF_RPM, 7000.0)
    b[tm.OFF_GEAR] = 4
    if steer is not None:
        struct.pack_into("<f", b, tm.OFF_STEER, steer)
    return bytes(b)


def _session(d):
    """Lap 0 (mid-lap connect), lap 1 of 10 s, lap 2 of 11 s, a paused packet, then
    lap 3 starts; GT7 reports lap 1's time 0.5 s into lap 2."""
    items = [(1000.0, "A", _tpkt(0))]
    t = 1000.1
    for lap, secs in ((1, 10.0), (2, 11.0)):
        for i in range(int(secs * 10)):
            last = 10000 if (lap == 2 and i >= 5) else -1
            items.append((t, "A", _tpkt(lap, last_ms=last)))
            t += 0.1
    items.append((t, "A", _tpkt(2, last_ms=10000, flags=tm.FLAG_ON_TRACK | tm.FLAG_PAUSED)))
    items.append((t + 0.1, "A", _tpkt(3, last_ms=10000)))
    return _write(d, items).path


def _rows(path, delimiter=","):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh, delimiter=delimiter))


def t_export_samples_and_laps():
    with tempfile.TemporaryDirectory() as d:
        src = _session(d)
        out = rec.export_csv(src, os.path.join(d, "out"))
        assert out["laps"] == 3 and out["dropped"] == 0, out
        samples = _rows(os.path.join(d, "out", "samples.csv"))
        assert list(samples[0]) == list(rec.SAMPLE_COLUMNS)
        assert out["samples"] == len(samples) and all(s["paused"] == "0" for s in samples)
        first = samples[0]
        assert first["t_s"] == "0.000" and first["throttle_pct"] == "100.0"
        assert first["gear"] == "4" and first["rpm"] == "7000" and first["steer_deg"] == ""
        laps = _rows(os.path.join(d, "out", "laps.csv"))
        assert list(laps[0]) == list(rec.LAP_COLUMNS)
        by_lap = {r["lap"]: r for r in laps}
        assert by_lap["1"]["status"] == "reference" and by_lap["1"]["gt7_time_s"] == "10.000"
        assert by_lap["0"]["status"] == "not counted" and by_lap["0"]["gt7_time_s"] == ""
        assert by_lap["1"]["start_t_s"] == "0.100" and by_lap["2"]["start_t_s"] == "10.100", laps


def t_export_all_keeps_paused_packets():
    with tempfile.TemporaryDirectory() as d:
        src = _session(d)
        rec.export_csv(src, os.path.join(d, "out"), include_all=True)
        samples = _rows(os.path.join(d, "out", "samples.csv"))
        assert any(s["paused"] == "1" for s in samples)


def t_export_excel_uses_semicolon_and_decimal_comma():
    with tempfile.TemporaryDirectory() as d:
        src = _session(d)
        rec.export_csv(src, os.path.join(d, "out"), excel=True)
        raw = open(os.path.join(d, "out", "samples.csv"), "rb").read()
        assert raw.startswith(b"\xef\xbb\xbf"), "Excel needs the UTF-8 BOM"
        row = _rows(os.path.join(d, "out", "samples.csv"), delimiter=";")[1]
        assert row["t_s"] == "0,100" and row["throttle_pct"] == "100,0", row


def t_export_steering_in_degrees_positive_left():
    with tempfile.TemporaryDirectory() as d:
        w = _write(d, [(1.0, "~", _tpkt(1, steer=0.5))])
        rec.export_csv(w.path, os.path.join(d, "out"))
        assert _rows(os.path.join(d, "out", "samples.csv"))[0]["steer_deg"] == "28.6"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_gt7_recording.py`
Expected: FAIL with `AttributeError: module 'gt7_recording' has no attribute 'export_csv'`.

- [ ] **Step 3: Implement**

Add `import csv` and `import math` to the imports of `gt7_recording.py`, then append:

```python
SAMPLE_COLUMNS = (
    "t_s", "session", "lap", "lap_t_s", "on_track", "paused", "speed_kmh",
    "throttle_pct", "brake_pct", "throttle_input_pct", "brake_input_pct", "steer_deg",
    "gear", "rpm", "fuel_l", "tyre_fl_c", "tyre_fr_c", "tyre_rl_c", "tyre_rr_c",
    "pos_x", "pos_y", "pos_z", "car_id")
LAP_COLUMNS = (
    "session", "lap", "start_t_s", "end_t_s", "gt7_time_s", "relay_time_s", "status",
    "reason", "fuel_used_l", "top_speed_kmh")
GT7_TIME_WINDOW_S = 3.0       # GT7 updates last_ms shortly after the line


def _fmt(excel):
    def num(value, digits):
        if value is None or not math.isfinite(value):
            return ""
        text = f"{value:.{digits}f}"
        return text.replace(".", ",") if excel else text
    return num


def _pct(byte):
    return None if byte is None else byte * 100.0 / 255.0


def export_csv(path, out_dir, include_all=False, excel=False):
    """Write samples.csv and laps.csv for one recording into out_dir."""
    r = Recording(path)
    num = _fmt(excel)
    eng = gt7_telemetry.TelemetryEngine()
    laps, waiting = [], []
    eng.on_lap = laps.append
    os.makedirs(out_dir, exist_ok=True)
    delimiter = ";" if excel else ","
    encoding = "utf-8-sig" if excel else "utf-8"
    t0 = None
    prev_last_ms = None
    written = 0
    with open(os.path.join(out_dir, "samples.csv"), "w", newline="",
              encoding=encoding) as fh:
        w = csv.writer(fh, delimiter=delimiter)
        w.writerow(SAMPLE_COLUMNS)
        for wall_ts, _kind, plain in r.packets():
            t0 = wall_ts if t0 is None else t0
            pkt = gt7_telemetry.parse_packet(plain)
            closed = len(laps)
            eng.update(pkt, wall_ts)
            for lap in laps[closed:]:
                lap["gt7_time_s"] = None
                waiting.append((lap, wall_ts + GT7_TIME_WINDOW_S, prev_last_ms))
            for item in list(waiting):
                lap, deadline, before = item
                if pkt.last_ms > 0 and pkt.last_ms != before:
                    lap["gt7_time_s"] = pkt.last_ms / 1000.0
                    waiting.remove(item)
                elif wall_ts > deadline:
                    waiting.remove(item)
            prev_last_ms = pkt.last_ms
            if not include_all and (not pkt.on_track or pkt.paused or pkt.loading):
                continue
            started = eng.lap_started_at()
            steer = None if pkt.steer_rad is None else math.degrees(pkt.steer_rad)
            w.writerow([
                num(wall_ts - t0, 3), eng.session, pkt.lap,
                num(wall_ts - started if started is not None else None, 3),
                int(pkt.on_track), int(pkt.paused), num(pkt.speed_mps * 3.6, 1),
                num(_pct(pkt.throttle), 1), num(_pct(pkt.brake), 1),
                num(_pct(pkt.throttle_input), 1), num(_pct(pkt.brake_input), 1),
                num(steer, 1), pkt.gear, num(pkt.rpm, 0), num(pkt.fuel_level, 2),
                *(num(v, 1) for v in pkt.tyre_temp),
                num(pkt.pos_x, 2), num(pkt.pos_y, 2), num(pkt.pos_z, 2),
                "" if pkt.car_id is None else pkt.car_id])
            written += 1
    with open(os.path.join(out_dir, "laps.csv"), "w", newline="", encoding=encoding) as fh:
        w = csv.writer(fh, delimiter=delimiter)
        w.writerow(LAP_COLUMNS)
        for lap in laps:
            w.writerow([
                lap["session"], lap["lap"], num(lap["start"] - t0, 3),
                num(lap["end"] - t0, 3), num(lap["gt7_time_s"], 3),
                num(lap["elapsed"], 3), lap["status"], lap["reason"],
                num(lap["fuel_used"], 2), num(lap["top_speed_mps"] * 3.6, 1)])
    return {"dir": out_dir, "samples": written, "laps": len(laps), "dropped": r.dropped}
```

If `t_export_samples_and_laps` disagrees on `start_t_s`, check the fixture: lap 1 starts at `1000.1`, lap 2 at `1000.1 + 10.0`, so `0.100` and `10.100` are correct; adjust the implementation, not the expectation. Same for `gt7_time_s`: lap 1 closes at the first lap-2 packet, `prev_last_ms` is `-1`, and the sixth lap-2 packet carries `10000`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_gt7_recording.py && python3 tools/lint.py`
Expected: ALL PASS, lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/scripts/gt7_recording.py tests/test_gt7_recording.py
git commit -m "feat(telemetry): export a recording to samples.csv and laps.csv (#785)"
```

---

### Task 5: Live switch (`RecordControl`)

**Files:**
- Modify: `src/scripts/gt7_recording.py`
- Test: `tests/test_gt7_recording.py`

**Interfaces:**
- Consumes: Task 3 `RecordingWriter`.
- Produces:
  - `record_default(environ) -> bool`: `RACECAST_TELEMETRY_RECORD` in `{"1","true","yes","on"}` (stripped, lowercased).
  - `class RecordControl(rec_dir, state_path, default, profile="", relay_version="dev", writer_factory=None)`: `put(wall_ts, kind, plain)`, `set_active(on) -> dict`, `toggle() -> dict`, `status() -> dict`, `close()`. `set_active`/`toggle` return `{"active", "file", "since"}`. `status()` returns `{"active", "file", "since", "bytes", "dropped", "error"}`. `file` is the base name of the open file (`.gt7rec` name, without `.part`) or None. `writer_factory()` returns a `RecordingWriter`-like object; default builds `RecordingWriter(rec_dir, profile, relay_version)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gt7_recording.py`:

```python
def _control(d, default=False, **kw):
    return rec.RecordControl(os.path.join(d, "rec"), os.path.join(d, "telemetry-record.json"),
                             default, profile="Demo", relay_version="dev", **kw)


def t_record_default_tokens():
    for v in ("1", "true", "YES", " on "):
        assert rec.record_default({"RACECAST_TELEMETRY_RECORD": v}) is True, v
    for v in ("", "0", "off", "no", "maybe"):
        assert rec.record_default({"RACECAST_TELEMETRY_RECORD": v}) is False, v
    assert rec.record_default({}) is False


def t_control_state_file_beats_profile_default():
    with tempfile.TemporaryDirectory() as d:
        c = _control(d, default=True)
        assert c.status()["active"] is True, "no state file -> profile default"
        c.set_active(False)
        c.close()
        assert _control(d, default=True).status()["active"] is False, "live toggle survives a restart"
        with open(os.path.join(d, "telemetry-record.json"), "w") as fh:
            fh.write("garbage")
        assert _control(d, default=True).status()["active"] is True, "bad file -> default"


def t_control_opens_file_on_first_packet_only():
    with tempfile.TemporaryDirectory() as d:
        c = _control(d, default=True)
        assert c.status()["file"] is None and not os.path.exists(os.path.join(d, "rec"))
        c.put(time.time(), "A", _plain())
        for _ in range(50):
            if c.status()["file"]:
                break
            time.sleep(0.02)
        st = c.status()
        assert st["file"].endswith(".gt7rec") and st["since"] is not None, st
        c.close()
        assert len(rec.list_recordings(os.path.join(d, "rec"))) == 1


def t_control_off_ignores_packets_and_toggle_starts_new_file():
    with tempfile.TemporaryDirectory() as d:
        c = _control(d, default=False)
        c.put(1.0, "A", _plain())
        assert c.status()["file"] is None
        assert c.toggle()["active"] is True
        c.put(time.time(), "A", _plain())
        c.toggle()                                   # off: closes the file
        c.toggle()                                   # on again: next packet opens a new one
        c.put(time.time() + 1, "A", _plain())
        c.close()
        assert len(rec.list_recordings(os.path.join(d, "rec"))) == 2


def t_control_write_error_reported_until_next_set():
    with tempfile.TemporaryDirectory() as d:
        class Broken:
            path, started, bytes, dropped, error = None, None, 0, 0, "disk full"
            def put(self, *a): pass
            def close(self, timeout=5.0): pass
        c = _control(d, default=True, writer_factory=Broken)
        c.put(1.0, "A", _plain())
        st = c.status()
        assert st["error"] == "disk full" and st["active"] is True, st
        c.set_active(True)
        assert c.status()["error"] is None, "a fresh start clears the error"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_gt7_recording.py`
Expected: FAIL with `AttributeError: ... 'record_default'`.

- [ ] **Step 3: Implement**

Append to `gt7_recording.py`:

```python
_TRUTHY = {"1", "true", "yes", "on"}


def record_default(environ):
    """The profile's TELEMETRY_RECORD default as injected by the CLI."""
    return str(environ.get("RACECAST_TELEMETRY_RECORD", "")).strip().lower() in _TRUTHY


class RecordControl:
    """The live recording switch plus the open writer. A valid `state_path`
    ({"active": bool}) wins over `default`; every set/toggle rewrites it."""

    def __init__(self, rec_dir, state_path, default, profile="", relay_version="dev",
                 writer_factory=None):
        self._lock = threading.Lock()
        self._state_path = state_path
        self._active = self._load(default)
        self._writer = None
        self._error = None
        self._factory = writer_factory or (
            lambda: RecordingWriter(rec_dir, profile, relay_version))

    def _load(self, default):
        try:
            with open(self._state_path, encoding="utf-8") as fh:
                v = json.load(fh).get("active")
            return v if isinstance(v, bool) else bool(default)
        except (OSError, ValueError, AttributeError):
            return bool(default)

    def _save(self):
        try:
            tmp = self._state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"active": self._active}, fh)
            os.replace(tmp, self._state_path)
        except OSError:
            pass  # best-effort, never crash the relay

    def put(self, wall_ts, kind, plain):
        with self._lock:
            if not self._active or self._error is not None:
                return
            if self._writer is None:
                self._writer = self._factory()
            w = self._writer
            if w.error is not None:
                self._error = w.error
                self._writer = None
                w.close()
                return
        w.put(wall_ts, kind, plain)

    def _close_writer(self):
        w, self._writer = self._writer, None
        if w is not None:
            w.close()

    def set_active(self, on):
        with self._lock:
            self._active = bool(on)
            self._error = None
            if not self._active:
                self._close_writer()
            self._save()
            return self._brief()

    def toggle(self):
        with self._lock:
            self._active = not self._active
            self._error = None
            if not self._active:
                self._close_writer()
            self._save()
            return self._brief()

    def _brief(self):
        w = self._writer
        return {"active": self._active,
                "file": os.path.basename(w.path) if w is not None and w.path else None,
                "since": w.started if w is not None else None}

    def status(self):
        with self._lock:
            out = self._brief()
            w = self._writer
            out["bytes"] = w.bytes if w is not None else 0
            out["dropped"] = w.dropped if w is not None else 0
            out["error"] = self._error or (w.error if w is not None else None)
            return out

    def close(self):
        with self._lock:
            self._close_writer()
```

Note `set_active(True)` while already on keeps the open writer (no new file); only a transition through off starts a new file, as the spec says.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_gt7_recording.py && python3 tools/lint.py`
Expected: ALL PASS, lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/scripts/gt7_recording.py tests/test_gt7_recording.py
git commit -m "feat(telemetry): live recording switch with persisted state (#785)"
```

---

### Task 6: Relay wiring, endpoints, policy

**Files:**
- Modify: `src/scripts/gt7_telemetry.py` (`TelemetryStore.__init__` line 598)
- Modify: `src/relay/racecast-feeds.py` (imports ~line 164; `/status` telemetry block ~line 11180; `/telemetry/*` routes ~line 11369; `_telemetry_loop` ~line 12372; startup ~line 12958; `shutdown()` ~line 13059)
- Modify: `src/scripts/console_policy.py` (~line 82)
- Test: `tests/test_telemetry_endpoints.py`, `tests/test_console.py`

**Interfaces:**
- Consumes: Task 5 `RecordControl`, `record_default`; Task 3 `finalize_partials`.
- Produces: `TelemetryStore(..., recorder=None)`; attribute `TelemetryStore.recorder`; method `TelemetryStore.record(wall_ts, kind, plain)`. Routes `GET /telemetry/record/start|stop|toggle`. `/status` `telemetry.record` = `recorder.status()` only when a recorder exists.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_telemetry_endpoints.py` (before `t_zz_no_test_reached_a_real_obs`):

```python
def _recorder(d, default=False):
    return m.gt7_recording.RecordControl(
        os.path.join(d, "rec"), os.path.join(d, "telemetry-record.json"), default)


def t_route_record_start_stop_toggle():
    import json, tempfile
    with tempfile.TemporaryDirectory() as d:
        store = m.gt7_telemetry.TelemetryStore(None, recorder=_recorder(d))
        srv, get = _serve(store)
        try:
            assert json.loads(get("/telemetry/record/start")[2])["active"] is True
            assert json.loads(get("/telemetry/record/toggle")[2])["active"] is False
            assert json.loads(get("/telemetry/record/stop")[2])["active"] is False
            assert get("/telemetry/record/bogus")[0] == 404
        finally:
            srv.shutdown()
            store.recorder.close()


def t_route_record_404_without_store_or_recorder():
    srv, get = _serve(None)
    try:
        assert get("/telemetry/record/start")[0] == 404
    finally:
        srv.shutdown()
    srv, get = _serve(m.gt7_telemetry.TelemetryStore(None))
    try:
        assert get("/telemetry/record/start")[0] == 404
    finally:
        srv.shutdown()


def t_status_reports_record_block():
    import json, tempfile

    class _StatusRelay:
        def status(self):
            return {}

    with tempfile.TemporaryDirectory() as d:
        store = m.gt7_telemetry.TelemetryStore(None, recorder=_recorder(d, default=True))
        srv, get = _serve(store, relay=_StatusRelay())
        try:
            rec = json.loads(get("/status")[2])["telemetry"]["record"]
            assert rec["active"] is True and rec["file"] is None and rec["error"] is None, rec
        finally:
            srv.shutdown()
            store.recorder.close()


def t_telemetry_loop_feeds_the_recorder():
    import socket as _socket
    import threading as _t
    from test_gt7_fixture import EXT_TCS_HEX
    ps_ip = "100.64.0.7"
    stop = _t.Event()
    packets = [bytes.fromhex(EXT_TCS_HEX)] * 3
    got = []

    class FakeRecorder:
        def put(self, wall_ts, kind, plain):
            got.append((kind, len(plain)))

    class FakeSock:
        def __init__(self, *a, **kw): pass
        def setsockopt(self, *a): pass
        def bind(self, addr): pass
        def settimeout(self, s): pass
        def close(self): pass
        def sendto(self, data, addr): pass
        def recvfrom(self, n):
            if packets:
                return packets.pop(), (ps_ip, 33740)
            stop.set()
            raise _socket.timeout()

    store = m.gt7_telemetry.TelemetryStore(None, recorder=FakeRecorder())
    real = m.socket.socket
    m.socket.socket = FakeSock
    try:
        m._telemetry_loop(store, ps_ip, stop)
    finally:
        m.socket.socket = real
    assert got == [("~", 0x158)] * 3, got
```

In `tests/test_console.py`, the director-only test at line ~56 loops over a tuple of path segment lists and asserts `_cap(segs) == ("director", False)`. Extend that tuple after `["telemetry", "toggle"]` with:

```python
                 ["telemetry", "record", "start"], ["telemetry", "record", "stop"],
                 ["telemetry", "record", "toggle"],
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_telemetry_endpoints.py; python3 tests/test_console.py`
Expected: FAIL (`AttributeError: module ... has no attribute 'gt7_recording'`; policy test fails on the role).

- [ ] **Step 3: Implement**

`gt7_telemetry.TelemetryStore.__init__`: add a `recorder=None` keyword parameter and `self.recorder = recorder`. Add method:

```python
    def record(self, wall_ts, kind, plain):
        """Hand one accepted packet to the recorder, if any; never raises."""
        rec = self.recorder
        if rec is None:
            return
        try:
            rec.put(wall_ts, kind, plain)
        except Exception as e:  # noqa: BLE001  recording must never stop the telemetry loop
            LOG.warning("telemetry recording failed: %s", e)
```

`racecast-feeds.py`:

1. After `import gt7_telemetry` (line ~164) add:
   ```python
   import gt7_recording   # GT7 telemetry recording to disk (solo/POV only, #785)
   ```
2. In `_telemetry_loop`, replace `store.update(gt7_telemetry.parse_packet(plain), time.time())` with:
   ```python
               now_wall = time.time()
               store.update(gt7_telemetry.parse_packet(plain), now_wall)
               store.record(now_wall, kind, plain)
   ```
3. In the `/status` builder (line ~11180), after `base["telemetry"] = {...}`:
   ```python
                   if telemetry_store.recorder is not None:
                       base["telemetry"]["record"] = telemetry_store.recorder.status()
   ```
4. In `do_GET`, directly before the show/hide/toggle route (line ~11367):
   ```python
                   if len(p) == 3 and p[:2] == ["telemetry", "record"]:
                       rec = telemetry_store.recorder if telemetry_store is not None else None
                       if rec is None:
                           return self._send({"error": "telemetry recording disabled"}, 404)
                       if p[2] == "start":
                           return self._send(rec.set_active(True))
                       if p[2] == "stop":
                           return self._send(rec.set_active(False))
                       if p[2] == "toggle":
                           return self._send(rec.toggle())
                       return self._send({"error": "unknown record action"}, 404)
   ```
5. In startup (line ~12958), inside `if telemetry_active(args.solo, os.environ):`, before `telemetry_store = gt7_telemetry.TelemetryStore(`:
   ```python
           rec_dir = os.path.join(runtime, "telemetry-recordings")
           for name in gt7_recording.finalize_partials(rec_dir):
               LOG.info("telemetry recording %s finalised (left open by the previous relay)", name)
           recorder = gt7_recording.RecordControl(
               rec_dir, os.path.join(runtime, "telemetry-record.json"),
               gt7_recording.record_default(os.environ),
               profile=os.environ.get("RACECAST_PROFILE_NAME", ""),
               relay_version=VERSION_LABEL)
   ```
   and pass `recorder=recorder` to the `TelemetryStore(...)` call. Extend the `LOG.info("GT7 telemetry listener started ...")` line's arguments with `, recording on|off` only if it stays one line; otherwise log a separate `LOG.info("GT7 telemetry recording %s", "on" if recorder.status()["active"] else "off")`.
6. In `shutdown(*_)`, before `stop_evt.set(); relay.shutdown(); os._exit(0)`:
   ```python
           if telemetry_store is not None and telemetry_store.recorder is not None:
               telemetry_store.recorder.close()   # flush and rename before the hard exit
   ```

`console_policy.py`, after the show/hide/toggle rule:

```python
    if len(p) == 3 and p[:2] == ["telemetry", "record"]:
        return Requirement(DIRECTOR, False)   # telemetry recording start/stop/toggle
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_telemetry_endpoints.py && python3 tests/test_console.py && python3 tests/test_gt7_recording.py && python3 tools/lint.py`
Expected: ALL PASS, lint clean. The existing `t_status_reports_telemetry_visibility` must still pass unchanged (no recorder -> no `record` key).

- [ ] **Step 5: Commit**

```bash
git add src/scripts/gt7_telemetry.py src/relay/racecast-feeds.py src/scripts/console_policy.py tests/test_telemetry_endpoints.py tests/test_console.py
git commit -m "feat(relay): record GT7 telemetry with /telemetry/record and /status (#785)"
```

---

### Task 7: Profile key, injection, event-start reset, new-profile default

**Files:**
- Modify: `src/scripts/config.py` (`ResolvedConfig` line ~185, `resolve_config` line ~250)
- Modify: `src/racecast.py` (`_profile_env_pairs` line ~227; `event_start` line ~4088; new helper next to `_write_session_start` line ~1429)
- Modify: `src/scripts/profile_admin.py` (`_solo_profile_env_text` line 197)
- Test: `tests/test_config.py`, `tests/test_racecast.py`, `tests/test_profile.py`

**Interfaces:**
- Produces: `ResolvedConfig.telemetry_record: str` (raw value, default `""`); child env `RACECAST_TELEMETRY_RECORD`; `racecast._reset_telemetry_record()`; `profile_admin._solo_profile_env_text(display, template)` includes `TELEMETRY_RECORD=` only when `template == "pov"`.

- [ ] **Step 1: Write the failing tests**

`tests/test_racecast.py`, next to `t_profile_env_vars_includes_graphics_take`:

```python
def t_profile_env_vars_includes_telemetry_record():
    rc = m.pcfg.ResolvedConfig(profile="demo", name="Demo", sheet_id="abc",
                               telemetry_record="1")
    assert m._profile_env_vars(rc)["RACECAST_TELEMETRY_RECORD"] == "1"


def t_reset_telemetry_record_removes_state_file():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        state = os.path.join(d, "telemetry-record.json")
        with open(state, "w", encoding="utf-8") as fh:
            fh.write('{"active": false}')
        real = m._runtime_dir
        m._runtime_dir = lambda: d
        try:
            m._reset_telemetry_record()
            assert not os.path.exists(state), "a new broadcast starts from the profile default"
            m._reset_telemetry_record()            # absent file: no error
        finally:
            m._runtime_dir = real
```

`tests/test_config.py`, after `t_resolve_config_kind_solo_with_template`:

```python
def t_resolve_config_telemetry_record():
    with tempfile.TemporaryDirectory() as td:
        root = _mkroot(td)
        _mkprofile(root, "s1", "NAME=Solo One\nKIND=solo\nTEMPLATE=pov\nTELEMETRY_RECORD=on\n")
        assert m.resolve_config(root, environ={}).telemetry_record == "on"
```

`tests/test_profile.py`:

```python
def t_solo_pov_profile_prefills_telemetry_record():
    assert "\nTELEMETRY_RECORD=\n" in m._solo_profile_env_text("Solo", "pov")
    assert "TELEMETRY_RECORD" not in m._solo_profile_env_text("Solo", "commentary")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_racecast.py; python3 tests/test_config.py; python3 tests/test_profile.py`
Expected: FAIL (`TypeError: unexpected keyword argument 'telemetry_record'`, missing `_reset_telemetry_record`, missing key in the text).

- [ ] **Step 3: Implement**

`config.py` `ResolvedConfig`, after `graphics_take`:

```python
    telemetry_record: str = ""   # solo POV: record GT7 telemetry from relay start (#785)
```

`resolve_config`, after `graphics_take=...`:

```python
        telemetry_record=prof.get("TELEMETRY_RECORD", ""),
```

`racecast.py` `_profile_env_pairs`, after the `RACECAST_GRAPHICS_TAKE` pair:

```python
             ("RACECAST_TELEMETRY_RECORD", rc.telemetry_record),
```

`racecast.py`, after `_write_session_start`:

```python
def _reset_telemetry_record():
    """Drop the live recording switch so a new broadcast starts from TELEMETRY_RECORD."""
    try:
        os.remove(os.path.join(_runtime_dir(), "telemetry-record.json"))
    except OSError:
        pass  # no live toggle recorded yet
```

`event_start`, change the new-session block to:

```python
    if _new_session and not _is_continuation_start(rest):
        _write_session_start()
        _reset_telemetry_record()
```

`profile_admin._solo_profile_env_text`: split the return so the POV block is conditional. Insert before the final `CONSOLE_SECRET` comment block:

```python
    pov = (
        "# OPTIONAL: record the GT7 telemetry trace to\n"
        "# runtime/<profile>/telemetry-recordings/ from relay start (1 = on).\n"
        "# The Director Panel REC key and `racecast telemetry record` toggle it live.\n"
        "TELEMETRY_RECORD=\n"
        "\n"
    ) if template == "pov" else ""
```

and concatenate `pov` into the returned string at that position (turn the single parenthesised literal into `head + pov + tail`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_racecast.py && python3 tests/test_config.py && python3 tests/test_profile.py && python3 tools/lint.py`
Expected: ALL PASS, lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/scripts/config.py src/racecast.py src/scripts/profile_admin.py tests/test_racecast.py tests/test_config.py tests/test_profile.py
git commit -m "feat(profile): TELEMETRY_RECORD key, injection and event-start reset (#785)"
```

---

### Task 8: `racecast telemetry` CLI group

**Files:**
- Modify: `src/racecast.py` (USAGE near line 22; verb tuples near line 979; `route()` near line 1037; dispatch table near line 4482; new command functions near `sheet_url_cmd` line 3095)
- Modify: `tools/build-binary.py` (hidden imports, line ~109)
- Test: `tests/test_racecast.py`

**Interfaces:**
- Consumes: Task 3/4 (`list_recordings`, `export_csv`, `recording_stem`, `RecordingError`, `SUFFIX`, `PART`), relay `/telemetry/record/*` and `/status`.
- Produces: `TELEMETRY_VERBS = ("record", "list", "export", "delete")`; functions `telemetry_record_cmd(rest)`, `telemetry_list_cmd(rest)`, `telemetry_export_cmd(rest)`, `telemetry_delete_cmd(rest)`; helper `_telemetry_rec_dir()`, `_resolve_recording(rec_dir, name)`, `_relay_record_status()`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_racecast.py` (follow the `t_sheet_*` route tests at line ~225 for style):

```python
def t_route_telemetry_verbs():
    for verb in ("record", "list", "export", "delete"):
        assert m.route(["telemetry", verb, "x"]) == \
            {"kind": "service", "command": "telemetry", "verb": verb, "rest": ["x"]}
    _raises(lambda: m.route(["telemetry"]))
    _raises(lambda: m.route(["telemetry", "bogus"]))


def _rec_dir_with_one(d):
    import importlib
    gr = importlib.import_module("gt7_recording")
    w = gr.RecordingWriter(d, "Demo", "dev", flush_s=0.05)
    b = bytearray(0x128)
    b[0:4] = (0x47375330).to_bytes(4, "little")
    w.put(1000.0, "A", bytes(b))
    w.close()
    return os.path.basename(w.path)


def t_resolve_recording_by_name_stem_and_latest():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        name = _rec_dir_with_one(d)
        stem = name[:-len(".gt7rec")]
        for q in (name, stem, "latest"):
            assert os.path.basename(m._resolve_recording(d, q)) == name, q
        try:
            m._resolve_recording(d, "nope"); raise AssertionError("unknown accepted")
        except SystemExit as e:
            assert "no recording" in str(e)


def t_telemetry_delete_refuses_the_open_file():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        name = _rec_dir_with_one(d)
        real_dir, real_status = m._telemetry_rec_dir, m._relay_record_status
        m._telemetry_rec_dir = lambda: d
        m._relay_record_status = lambda: {"active": True, "file": name}
        try:
            try:
                m.telemetry_delete_cmd([name]); raise AssertionError("open file deleted")
            except SystemExit as e:
                assert "currently recording" in str(e)
            m._relay_record_status = lambda: None
            m.telemetry_delete_cmd([name])
            assert not os.path.exists(os.path.join(d, name))
        finally:
            m._telemetry_rec_dir, m._relay_record_status = real_dir, real_status


def t_telemetry_export_writes_next_to_recording():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        name = _rec_dir_with_one(d)
        real = m._telemetry_rec_dir
        m._telemetry_rec_dir = lambda: d
        try:
            m.telemetry_export_cmd(["latest"])
        finally:
            m._telemetry_rec_dir = real
        out = os.path.join(d, name[:-len(".gt7rec")])
        assert os.path.exists(os.path.join(out, "samples.csv"))
        assert os.path.exists(os.path.join(out, "laps.csv"))


def t_telemetry_record_without_relay_exits_nonzero():
    real = m._relay_record_call
    m._relay_record_call = lambda verb: None
    try:
        m.telemetry_record_cmd(["start"]); raise AssertionError("no error without relay")
    except SystemExit as e:
        assert e.code not in (0, None)
    finally:
        m._relay_record_call = real
```

`racecast.py` already has `src/scripts` on `sys.path` (line ~47), so `importlib.import_module("gt7_recording")` works in the test.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_racecast.py`
Expected: FAIL (`ValueError` on `route(["telemetry", ...])` or missing attributes).

- [ ] **Step 3: Implement**

USAGE (after the `racecast sheet` line):

```
  racecast telemetry record start|stop|status   # solo POV: record the GT7 telemetry trace (relay must run)
  racecast telemetry list | export <name|latest> [--out DIR] [--all] [--excel] | delete <name>   # recordings of the active profile -> samples.csv + laps.csv
```

Verb tuple next to `SHEET_VERBS`:

```python
TELEMETRY_VERBS = ("record", "list", "export", "delete")   # GT7 telemetry recordings (#785)
```

`route()`, after the `sheet` branch:

```python
    if cmd == "telemetry":
        verb = rest[0] if rest else None
        if verb not in TELEMETRY_VERBS:
            raise ValueError(f"usage: racecast telemetry {{{'|'.join(TELEMETRY_VERBS)}}}")
        return {"kind": "service", "command": "telemetry", "verb": verb, "rest": rest[1:]}
```

Dispatch table, after the `sheet` entries:

```python
    ("telemetry", "record"): telemetry_record_cmd, ("telemetry", "list"): telemetry_list_cmd,
    ("telemetry", "export"): telemetry_export_cmd, ("telemetry", "delete"): telemetry_delete_cmd,
```

Command functions, after `sheet_open_cmd`:

```python
def _telemetry_rec_dir():
    return os.path.join(_runtime_dir(), "telemetry-recordings")


def _relay_record_call(verb):
    """GET /telemetry/record/<verb> on the local relay; None when unreachable or 404."""
    try:
        return http_util.get_json(
            f"http://127.0.0.1:{RELAY_PORT}/telemetry/record/{verb}", timeout=5)
    except Exception:
        return None


def _relay_record_status():
    """The running relay's telemetry.record block from /status, or None."""
    try:
        st = http_util.get_json(f"http://127.0.0.1:{RELAY_PORT}/status", timeout=3)
    except Exception:
        return None
    return ((st or {}).get("telemetry") or {}).get("record")


def _resolve_recording(rec_dir, name):
    import gt7_recording as gr
    rows = gr.list_recordings(rec_dir)
    if name == "latest" and rows:
        return rows[-1]["path"]
    for row in rows:
        if name in (row["name"], gr.recording_stem(row["path"])):
            return row["path"]
    sys.exit(f"no recording named {name!r} in {rec_dir} (see 'racecast telemetry list')")


def telemetry_record_cmd(rest):
    """Start, stop or report the relay's telemetry recording."""
    verb = rest[0] if rest else None
    if verb not in ("start", "stop", "status"):
        sys.exit("usage: racecast telemetry record start|stop|status")
    out = _relay_record_status() if verb == "status" else _relay_record_call(verb)
    if out is None:
        sys.exit("telemetry recording unavailable: the relay is not running, or the "
                 "active profile is not a solo POV broadcast")
    state = "recording" if out.get("active") else "off"
    print(f"telemetry recording: {state}" + (f" -> {out['file']}" if out.get("file") else ""))
    if out.get("error"):
        print(f"error: {out['error']}")


def telemetry_list_cmd(_rest):
    """List the active profile's recordings."""
    import gt7_recording as gr
    rec_dir = _telemetry_rec_dir()
    rows = gr.list_recordings(rec_dir)
    if not rows:
        print(f"no telemetry recordings in {rec_dir}")
        return
    open_file = (_relay_record_status() or {}).get("file")
    total = 0
    for row in rows:
        total += row["size"]
        mark = ("recording" if open_file and row["name"].startswith(open_file)
                else "unclosed" if row["partial"] else "")
        print(f"{gr.recording_stem(row['path'])}  {row['size'] / 1e6:7.1f} MB  "
              f"{row['duration_s'] / 60:6.1f} min  {row['laps']:4d} laps  {mark}".rstrip())
    print(f"{len(rows)} recording(s), {total / 1e6:.1f} MB in {rec_dir}")


def telemetry_export_cmd(rest):
    """Export one recording to samples.csv + laps.csv."""
    import argparse
    import gt7_recording as gr
    ap = argparse.ArgumentParser(prog="racecast telemetry export")
    ap.add_argument("name", help="recording name, its stem, or 'latest'")
    ap.add_argument("--out", help="output directory (default: <recording>/ next to it)")
    ap.add_argument("--all", action="store_true",
                    help="keep menu, pause and loading packets")
    ap.add_argument("--excel", action="store_true",
                    help="semicolon + decimal comma + BOM for a German Excel")
    args = ap.parse_args(rest)
    rec_dir = _telemetry_rec_dir()
    path = _resolve_recording(rec_dir, args.name)
    out_dir = args.out or os.path.join(rec_dir, gr.recording_stem(path))
    try:
        res = gr.export_csv(path, out_dir, include_all=args.all, excel=args.excel)
    except gr.RecordingError as e:
        sys.exit(str(e))
    print(f"wrote {res['samples']} samples and {res['laps']} laps to {res['dir']}")
    if res["dropped"]:
        print(f"note: {res['dropped']} packets were dropped while recording")


def telemetry_delete_cmd(rest):
    """Delete one recording (and its export folder)."""
    import gt7_recording as gr
    if len(rest) != 1:
        sys.exit("usage: racecast telemetry delete <name>")
    rec_dir = _telemetry_rec_dir()
    path = _resolve_recording(rec_dir, rest[0])
    open_file = (_relay_record_status() or {}).get("file")
    if open_file and os.path.basename(path).startswith(open_file):
        sys.exit(f"{os.path.basename(path)} is currently recording; stop it first "
                 "('racecast telemetry record stop')")
    os.remove(path)
    export_dir = os.path.join(rec_dir, gr.recording_stem(path))
    if os.path.isdir(export_dir):
        shutil.rmtree(export_dir)
    print(f"deleted {os.path.basename(path)}")
```

`tools/build-binary.py`, after `"--hidden-import", "gt7_crypto",`:

```python
           "--hidden-import", "gt7_recording", "--hidden-import", "gt7_telemetry",
```

Check `grep -n "def t_function_local_peer_imports_are_frozen" -A30 tests/test_racecast.py`: if that guard keeps its own list of expected modules, it now fails until `build-binary.py` lists `gt7_recording`; that is the intended signal.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_racecast.py && python3 tools/lint.py && python3 src/racecast.py telemetry --help && python3 src/racecast.py telemetry export --help`
Expected: ALL PASS, lint clean, both help outputs print ASCII text and exit 0.

- [ ] **Step 5: Commit**

```bash
git add src/racecast.py tools/build-binary.py tests/test_racecast.py
git commit -m "feat(cli): racecast telemetry record|list|export|delete (#785)"
```

---

### Task 9: Director Panel REC key

**Files:**
- Modify: `src/director/director-panel.html` (CSS near line 158-163; solo `vis` list line ~1105; `buildControls` vis loop line ~1265; `/status` handler line ~2081; reset at line ~1238)
- Modify: `src/docs/wiki/images/director-panel.png` (recapture)

**Interfaces:**
- Consumes: `/status` `telemetry.record` (Task 6), `GET /telemetry/record/toggle`.

- [ ] **Step 1: Add the key**

In the solo `vis` list, after the `TELEMETRY` entry:

```js
    // Records the GT7 telemetry trace to disk (#785); hidden without telemetry.
    {label:"REC", scene:"DISK", relay:"telemetry/record"},
```

The existing click handler already calls `relayCall(item.relay + "/toggle")`, which yields `/telemetry/record/toggle`.

Next to `let teleVisBtn = null;`:

```js
let teleRecBtn = null;                // the relay-driven telemetry recording button (#785)
```

Where line ~1238 resets `teleVisBtn = null;`, also reset `teleRecBtn = null;`. In the vis loop, after `if (item.relay === "telemetry") teleVisBtn = b;`:

```js
    if (item.relay === "telemetry/record") teleRecBtn = b;
```

CSS, after the `button.k.air .tag` rule:

```css
  button.k.warn{border-color:var(--amber)}
  button.k.warn .tag{color:var(--amber)}
```

In the `/status` handler, after the `teleVisBtn` block:

```js
    if (teleRecBtn){                      // recording state; amber when the writer failed
      const rec = d.telemetry && d.telemetry.record;
      teleRecBtn.hidden = !rec;
      teleRecBtn.classList.toggle("air", !!(rec && rec.active && !rec.error));
      teleRecBtn.classList.toggle("warn", !!(rec && rec.error));
      teleRecBtn.title = rec && rec.error ? "Recording stopped: " + rec.error
        : rec && rec.file ? "Recording " + rec.file : "";
      let cost = teleRecBtn.querySelector(".cost");
      if (!cost){ cost = document.createElement("span"); cost.className = "cost";
                  teleRecBtn.appendChild(cost); }
      cost.textContent = rec && rec.active && rec.since
        ? fmtElapsed(Date.now() / 1000 - rec.since) : "";
    }
```

Check whether the panel already has an elapsed-time formatter (`grep -n "function fmt" src/director/director-panel.html`). If none fits, add:

```js
function fmtElapsed(s){
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), ss = s % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(ss).padStart(2, "0");
}
```

- [ ] **Step 2: Verify visually**

Invoke the `ui-visual-verification` skill. Bring up the relay with a solo POV profile and the obs-sim stand-in as that skill and `wiki-screenshots` describe. Check these states at desktop and mobile width:
- endurance profile: no `REC` key;
- solo POV, `curl http://127.0.0.1:8088/telemetry/record/stop`: `REC` visible, unlit;
- `curl http://127.0.0.1:8088/telemetry/record/start`: `REC` red. Without a console no file opens, so no elapsed time shows; with a PS5 running GT7 the elapsed time counts up;
- amber error: stop the relay, replace `runtime/<profile>/telemetry-recordings` with an empty file of that name, start the relay with recording on and a console sending. Without a console, skip this state and note it in the PR.

- [ ] **Step 3: Recapture the wiki screenshot**

Invoke the `wiki-screenshots` skill and regenerate `src/docs/wiki/images/director-panel.png` (and its copy under `src/docs/slides/assets/img/` if the skill lists one).

- [ ] **Step 4: Run the relay/panel tests**

Run: `python3 tests/test_telemetry_endpoints.py && python3 tools/run-tests.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/director/director-panel.html src/docs/wiki/images/director-panel.png src/docs/slides/assets/img/
git commit -m "feat(panel): REC key for the GT7 telemetry recording (#785)"
```

---

### Task 10: Docs and final gates

**Files:**
- Modify: `src/relay/CLAUDE.md` (GT7 telemetry paragraph, line ~343)
- Modify: `src/docs/wiki/Relay-Mode.md` (after the "Steering wheel (solo POV)" paragraph, line ~506)
- Modify: `src/docs/wiki/Director.md` (where the solo `TELEMETRY` key is described: `grep -n "TELEMETRY" src/docs/wiki/Director.md`)
- Modify: `src/docs/wiki/Configuration.md` (profile key list: the `profile.env` example near line 49 and the key descriptions near line 92)

- [ ] **Step 1: Relay CLAUDE.md**

Append to the GT7 telemetry paragraph:

```markdown
**Telemetry recording (solo POV, #785).** `_telemetry_loop` hands every accepted packet to `TelemetryStore.record`, which enqueues it into `gt7_recording.RecordControl`; a writer thread appends it to `runtime/<profile>/telemetry-recordings/<start>.gt7rec.part` and renames the file on stop. The switch is `telemetry-record.json` when present (every `/telemetry/record/start|stop|toggle` writes it), else the profile's `TELEMETRY_RECORD`; a fresh `event start` deletes the file. `relay stop` on Windows is `taskkill /F`, so no shutdown code runs there: the writer flushes every second and the next relay start renames leftover `.part` files. `racecast telemetry export` replays a recording through `parse_packet` and `TelemetryEngine` (`on_lap`), so `laps.csv` matches the HUD's verdicts. Tests: `tests/test_gt7_recording.py`, `tests/test_telemetry_endpoints.py`.
```

- [ ] **Step 2: Wiki Relay-Mode.md**

Add after the steering-wheel paragraph:

```markdown
**Telemetry recording (solo POV).** The relay can record the full GT7 telemetry trace
for later analysis: throttle, brake, steering (extended packets only), speed, gear,
rpm, fuel, tyre temperatures, position and lap times. Set `TELEMETRY_RECORD=1` in the
profile to record from relay start, or toggle it live with the Director Panel's `REC`
key, `/telemetry/record/start|stop|toggle` or `racecast telemetry record start|stop`.
A live toggle survives a relay restart; a new `racecast event start` returns to the
profile setting. Recordings land in `runtime/<profile>/telemetry-recordings/` (about
75 MB per hour) and are never deleted automatically.

`racecast telemetry list` shows them, `racecast telemetry export latest` writes
`samples.csv` (one row per packet, 60 Hz, metric units) and `laps.csv` (one row per
lap with GT7's lap time and whether the relay counted it) next to the recording.
`--excel` writes a file a German Excel opens with a double click; `--all` keeps menu
and pause packets. `racecast telemetry delete <name>` removes a recording.
```

- [ ] **Step 3: Wiki Director.md**

Next to the `TELEMETRY` key description, add one sentence:

```markdown
`REC` (solo POV) starts and stops the telemetry recording; it lights red with the elapsed time while recording and amber when writing failed (disk full).
```

Add to `Configuration.md`, in the `profile.env` example after `GRAPHICS_TAKE=`, the line `TELEMETRY_RECORD=`, and to the key descriptions after `GRAPHICS_TAKE`:

```markdown
- **`TELEMETRY_RECORD`** *(optional, solo POV)*: `1` records the GT7 telemetry trace from
  relay start. The Director Panel `REC` key and `racecast telemetry record` toggle it live.
  See [Relay mode](Relay-Mode).
```

- [ ] **Step 4: Final gates**

Run, in order:

```bash
python3 tools/lint.py
python3 tools/run-tests.py
python3 tools/build.py
```

Expected: lint clean, every test file passes, build verify passes. Then, per the repo's finalisation rules, fetch and integrate `origin/main` before any further expensive build:

```bash
git fetch origin && git rebase origin/main
python3 tools/run-tests.py
```

- [ ] **Step 5: Commit**

```bash
git add src/relay/CLAUDE.md src/docs/wiki/Relay-Mode.md src/docs/wiki/Director.md src/docs/wiki/Configuration.md
git commit -m "docs: GT7 telemetry recording in the relay notes and the wiki (#785)"
```

Then open one PR for #785 following the `ship-feature` skill (self-review with `pr-review`, green CI, squash-merge).

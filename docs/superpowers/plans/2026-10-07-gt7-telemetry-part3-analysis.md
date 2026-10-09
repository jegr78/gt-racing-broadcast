# GT7 Telemetry Part 3: Lap Index, Mini-Sectors and Lap Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A solo POV producer opens a Telemetry view in the Control Center, picks a recording and a lap, and compares it with the fastest lap of the same track and car: speed, throttle, brake, steering, gear and the time delta over lap distance, a track map with 200 m mini-sectors coloured by gain and loss, and a sector table with the theoretical best.

**Architecture:** A new stdlib module `src/scripts/gt7_laps.py` replays a recording once through `parse_packet` and `TelemetryEngine` (the same code the HUD and the CSV export use), cuts the samples into laps, decides each GT7 session's track with part 2's `gt7_recording.session_tracks`, resamples every lap every 5 m along the racing line and caches the result as `<stem>.laps.json` next to the recording. Pure functions in the same module compute sectors, best sectors, the theoretical best and the comparison pool; the page computes the delta from the two traces it already holds. Logic `gt7_laps` shares with part 2's CSV export (car name, layout brief, distance unwrap, GT7 lap-time matching) moves into public helpers in `gt7_recording`. `src/racecast.py` wraps the index in never-raising Control Center data functions with an in-process memo; `src/ui/ui_server.py` serves five routes; `src/ui/control-center.html` gets a `Telemetry` view drawn with inline SVG. `tools/make-demo-recording.py` writes a synthetic recording on a downloaded racing line for screenshots and visual checks.

**Tech Stack:** Python 3 stdlib only (`json`, `bisect`, `math`, `tempfile`). Plain JS and inline SVG in the Control Center page, no library. Tests are runnable stdlib scripts under `tests/` (no pytest).

**Spec:** `docs/superpowers/specs/2026-10-07-gt7-telemetry-recording-design.md`, section "Part 3". Issue #788, part 3 of epic #785. Requires part 1 (#786) and part 2 (#787) merged. Part 4 (#789, `docs/superpowers/plans/2026-10-07-gt7-telemetry-part4-report.md`) consumes `gt7_laps.index`, `sectors`, `best_sectors`, `theoretical_best` and `lap_length_m` exactly as defined here.

## Global Constraints

- Edit only under `src/`, `tests/`, `tools/`, `docs/` (plus the one table row in `.claude/skills/wiki-screenshots/SKILL.md` in Task 10). Never touch `dist/` or `runtime/` in a commit.
- Code, comments, docs, help text: English only. argparse help strings ASCII only (`->`, not arrows).
- Comments minimal: one reason, one sentence. No investigation history in code.
- Nothing in the analysis path may raise into a request handler: every `racecast.py` data function returns `{"ok": False, "error": ...}` instead.
- Values read from recordings (recording names, car names, track names) reach the page only through `textContent` or SVG attributes, never `innerHTML`.
- Tests run on any machine and on Windows CI: `tempfile`, `os.path.join` for local paths, no network, no real IPs. `gt7_laps` tests use a fake track database and do not depend on the bundled track data; only the learn test (Task 3) reads the bundled `src/assets/gt7/` files (`index.json` and the car tables). No test reads `signatures.json`; the demo-tool test (Task 5) writes its own one-row track database.
- Part 1 and 2 interfaces are used as listed below. Allowed changes to part 1 and 2 code: in Task 2 the behaviour fixes named in the carry-overs, the shared public helpers in `gt7_recording` (`car_name`, `brief_track`, `nearest_station`, `LapTimeMatcher`, replacing the private `_car_name` and `_brief`) and `gt7_data.data_version` hashing bundled files by content (memoised per path, size and mtime) and the car tables; in Task 3 `_resolve_recording` wrapping the new `_find_recording`, and `telemetry_export_cmd` / `telemetry_delete_cmd` using the shared key, databases and lap-index removal. Existing signatures stay unchanged.
  - `gt7_recording.Recording(path)` (`header`, `packets()` yielding `(wall_ts, kind, plain)`, `dropped`), `RecordingWriter(rec_dir, profile, relay_version, queue_max=600, flush_s=1.0)`, `list_recordings(rec_dir)`, `recording_stem(path)`, `SUFFIX = ".gt7rec"`, `PART = ".part"`, `GT7_TIME_WINDOW_S = 3.0`, `session_tracks(laps, tracks, key=None) -> {session: {"id","track","layout","reverse"} | {"candidates": [ids]} | None}`.
  - `gt7_telemetry.parse_packet(plain)` (fields incl. `gear`, `rpm`, `pos_x`, `pos_z`, `steer_rad`), `TelemetryEngine` with `on_lap` (records with `session, lap, start, end, elapsed, status, reason, fuel_used, top_speed_mps, car_id, points, distance_m`), `session`, `lap_started_at()`, `lap_distance()`. Offsets `OFF_POS`, `OFF_RPM`, `OFF_GEAR`, `OFF_STEER`, `OFF_THROTTLE_INPUT`, `OFF_BRAKE_INPUT` and the base ones.
  - `gt7_tracks.TrackDB.load(runtime_base, bundled=None)` and `TrackDB(index_path, signatures_path, learned_path=None)` with `name(official_id)` (None for an unknown id), `layouts()`, `match(points, length_m)`, `project(points, official_id) -> list[float | None] | None` (None slots for non-finite points), `line_length(official_id)`, `learn(official_id, points, length_m, key=None)` (raises `ValueError`, or `OSError` on a failed write), `assignment(key)`. Track ids are strings.
  - `gt7_data.data_version(runtime_base, bundled=None)`, `gt7_data.cars_dir(runtime_base, bundled=None)`, `gt7_data.resolve(name, runtime_base, bundled=None)`, `gt7_data.bundled_dir()`. `gt7_cars.CarDB(directory).lookup(car_id)`.
  - `racecast._telemetry_rec_dir()`, `_runtime_base_dir()`, `_active_profile_name()`, `resource_path("assets/gt7")`, `_recording_sort_key(row)`, `_resolve_recording(rec_dir, name)`, `telemetry_export_cmd(rest)`, `telemetry_delete_cmd(rest)`, `_relay_record_status()`, `_foreign_relay_profile()`.
- The learned-assignment key comes from `racecast._telemetry_track_key(path)` (Task 3): `"<profile>/<stem>"`, or None without an active profile. Export and learn both use it.
- Data layer database construction, verbatim: `gt7_tracks.TrackDB.load(_runtime_base_dir(), resource_path("assets/gt7"))` and `gt7_cars.CarDB(gt7_data.cars_dir(_runtime_base_dir(), resource_path("assets/gt7")))`, both inside `racecast._telemetry_dbs()`.
- Constants, verbatim: `INDEX_VERSION = 2` (1 before counted laps closed their trace at the lap length), `CACHE_SUFFIX = ".laps.json"`, `STEP_M = 5.0`, `SECTOR_M = 200.0`, `DECIMATE_M = 2.0`, `COUNTED = ("reference", "counted")`.
- Track map orientation: GT7 x to the right, z downward in SVG (part 4 draws its map the same way).
- Line anchors below were read on `feat/788-gt7-lap-analysis` at 9fb3f54 (parts 1 and 2 merged). Earlier tasks shift them; every anchor quotes the text to grep for.
- Run single test files with `python3 tests/<file>.py`; one function with `python3 -c "import sys; sys.path.insert(0,'tests'); import <mod> as t; t.<fn>()"`. Lint with `python3 tools/lint.py` after every Python change.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.


### Carry-overs from part 2 (read before Task 1)

Part 2 landed with these differences from its spec; the code on the epic branch is the truth, and the tasks below already account for them.
- `signatures.json` never ships (`gt7_data.RUNTIME_ONLY`). Until the first successful update only learned tracks are recognised. `make-demo-recording.py` reads the downloaded copy (`runtime/gt7/signatures.json`) and exits with a hint when it is missing; no test relies on the real file.
- `TrackDB.project` returns `list[float | None]` (None for a non-finite point) or None; `gt7_laps._trace` keeps the driven distance for a None slot.
- `TrackDB.learn` raises `OSError` on a failed write and `ValueError` with fewer than 10 finite points; `telemetry_learn_data` maps both to `{"ok": false, "error": ...}` without the file path.
- `racecast.telemetry_export_cmd` builds the assignment key with an inline f-string that can yield `"None/<stem>"`. Task 3 replaces it with `_telemetry_track_key(path)`, which export and learn share.
- `gt7_recording.session_tracks` returns None for every session when an assigned id cannot be resolved by `name()`. Task 2 makes it fall back to matching.
- `gt7_data.data_version` hashes absolute paths and mtimes, so a onefile binary (a new `_MEIPASS` per launch) would rebuild every lap index on each start. Task 2 hashes bundled files by content, memoised per path, size and mtime.
- The relay picks up changed GT7 files within 60 s (`gt7_data` fingerprint poll), so a learned track reaches a running relay without restart.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/scripts/gt7_laps.py` | create | sector math, lap index with traces, cache, comparison pool |
| `src/scripts/gt7_recording.py` | modify | public helpers shared with `gt7_laps` (`car_name`, `brief_track`, `nearest_station`, `LapTimeMatcher`); `session_tracks` falls back to matching for an unresolvable assignment |
| `src/scripts/gt7_data.py` | modify | `data_version` hashes bundled files by content and covers the car tables |
| `src/racecast.py` | modify | `_find_recording` (under `_resolve_recording`), `_telemetry_track_key`, `_telemetry_dbs`, `_telemetry_index` with its memo, `_telemetry_reason`, five `telemetry_*_data` functions, ctx keys; `telemetry export` uses the shared key and databases; `telemetry delete` removes the lap index |
| `src/ui/ui_server.py` | modify | `GET /api/telemetry/recordings|laps|lap|tracks`, `POST /api/telemetry/learn` |
| `src/ui/control-center.html` | modify | `Telemetry` nav item and view (solo POV only): lists, pickers, charts, map, sector table, Set track |
| `tools/make-demo-recording.py` | create | synthetic recording on a real racing line |
| `tools/build-binary.py` | modify | hidden import `gt7_laps` |
| `tests/test_gt7_laps.py` (new), `tests/test_make_demo_recording.py` (new), `tests/test_gt7_recording.py`, `tests/test_gt7_data.py`, `tests/test_racecast.py`, `tests/test_ui_server.py` | create/modify | tests per task |
| `src/docs/wiki/Relay-Mode.md` | modify | `telemetry delete` also removes the cached lap index |
| `src/docs/wiki/Control-Center.md`, `src/docs/wiki/images/cc-telemetry.png`, `src/ui/CLAUDE.md`, `tools/CLAUDE.md`, `.claude/skills/wiki-screenshots/SKILL.md` | modify/create | docs + screenshot |

---

### Task 1: Sector math in `gt7_laps`

**Files:**
- Create: `src/scripts/gt7_laps.py`
- Create: `tests/test_gt7_laps.py`

**Interfaces:**
- Consumes: nothing beyond the stdlib (the module imports `gt7_data`, `gt7_recording`, `gt7_telemetry` from Task 2 on).
- Produces (all in `gt7_laps`):
  - Constants `STEP_M = 5.0`, `SECTOR_M = 200.0`, `COUNTED = ("reference", "counted")`.
  - A trace is a list of points `{"d", "t", "speed_kmh", "throttle", "brake", "steer_deg", "gear", "x", "z"}`, one every `STEP_M` of lap distance, `trace[0]["d"] == 0.0`, `d` strictly rising.
  - `lap_length_m(lap) -> float`: `lap["trace"][-1]["d"]`, `0.0` without a trace. Every caller of `sectors` for a whole lap passes this length.
  - `sectors(trace, length_m, step_m=SECTOR_M) -> list[float | None]`: boundaries at `0, step_m, 2*step_m, ...` and `length_m`; the last sector is shorter; times interpolated at the boundaries; `None` for a sector the trace does not reach; `[]` without trace or length.
  - `best_sectors(laps) -> list[float | None]`: per sector the minimum of `sectors(lap["trace"], lap_length_m(lap))` over the laps that have a trace.
  - `theoretical_best(laps) -> float | None`: `round(sum(best_sectors(laps)), 3)`, None when there is no sector or one has no time.
- No `delta` function: the page computes the delta of lap B against lap A from the two traces it already holds (Task 7), and part 4 does not use one.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gt7_laps.py`:

```python
#!/usr/bin/env python3
"""GT7 lap index: sector math, traces, the cache and the comparison pool.
Run: python3 tests/test_gt7_laps.py"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import gt7_laps as gl


def _trace(*sector_secs, sector=200.0, step=5.0):
    """Stations every 5 m; sector i of 200 m takes sector_secs[i] s at constant speed."""
    pts, t = [], 0.0
    for i, secs in enumerate(sector_secs):
        v = sector / secs
        for k in range(0 if i == 0 else 1, int(sector / step) + 1):
            pts.append({"d": i * sector + k * step, "t": round(t + k * step / v, 3),
                        "speed_kmh": round(v * 3.6, 1), "throttle": 100.0, "brake": 0.0,
                        "steer_deg": 0.0, "gear": 4, "x": 0.0, "z": 0.0})
        t += secs
    return pts


def t_sectors_every_200_m_from_the_line():
    tr = _trace(4.0, 4.0, 4.0, 4.0, 4.0)
    assert tr[0]["d"] == 0.0 and tr[-1]["d"] == 1000.0
    assert gl.lap_length_m({"trace": tr}) == 1000.0
    assert gl.sectors(tr, 1000.0) == [4.0, 4.0, 4.0, 4.0, 4.0]


def t_sectors_short_last_sector():
    tr = _trace(4.0, 4.0, 4.0, 4.0, 4.0)
    assert gl.sectors(tr, 1000.0, step_m=300.0) == [6.0, 6.0, 6.0, 2.0], \
        "boundaries 0/300/600/900 and the line at 1000"
    short = tr[:-1]                                # the trace ends at 995 m
    assert gl.sectors(short, gl.lap_length_m({"trace": short})) == [4.0, 4.0, 4.0, 4.0, 3.9]


def t_sectors_beyond_the_trace_have_no_time():
    tr = _trace(4.0, 4.0, 4.0, 4.0, 4.0)[:-4]     # ends at 980 m
    assert gl.sectors(tr, 1000.0)[-1] is None, "no time is invented past the trace"
    assert gl.sectors([], 1000.0) == [] and gl.sectors(tr, 0.0) == []
    assert gl.lap_length_m({"trace": []}) == 0.0 and gl.lap_length_m({}) == 0.0


def t_best_sectors_and_theoretical_best():
    a = {"trace": _trace(4.0, 4.0, 4.0, 4.0, 4.0)}
    b = {"trace": _trace(3.5, 4.5, 4.0, 4.2, 3.8)}
    assert gl.best_sectors([a, b]) == [3.5, 4.0, 4.0, 4.0, 3.8]
    assert gl.theoretical_best([a, b]) == 19.3
    assert gl.best_sectors([{"trace": []}]) == [] and gl.theoretical_best([]) is None
    assert gl.theoretical_best([{"trace": []}]) is None, "a lap without trace adds nothing"
    short = {"trace": _trace(4.0, 4.0, 4.0, 4.0, 4.0)[:-4]}      # ends at 980 m
    assert gl.best_sectors([short])[-1] == 3.6, "its own trace end closes the last sector"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_gt7_laps.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'gt7_laps'`.

- [ ] **Step 3: Implement**

Create `src/scripts/gt7_laps.py`:

```python
#!/usr/bin/env python3
"""GT7 lap index: per-lap traces and mini-sectors from a telemetry recording.

Stdlib only and no relay imports. index() replays a recording once through the same
parse_packet and TelemetryEngine the HUD uses and caches the result as
<stem>.laps.json next to the recording. A trace is one point every STEP_M metres of
lap distance, starting at 0.0 on the line.
"""
import bisect
import math

STEP_M = 5.0
SECTOR_M = 200.0
COUNTED = ("reference", "counted")


def _time_at(trace, d):
    """Lap time at distance d, interpolated between stations; None outside the trace."""
    if not trace or d < trace[0]["d"] or d > trace[-1]["d"]:
        return None
    ds = [p["d"] for p in trace]
    k = bisect.bisect_left(ds, d)
    if ds[k] == d:
        return trace[k]["t"]
    a, b = trace[k - 1], trace[k]
    return a["t"] + (b["t"] - a["t"]) * (d - a["d"]) / (b["d"] - a["d"])


def lap_length_m(lap):
    """A lap's length for sectors: where its trace ends."""
    trace = lap.get("trace") or []
    return trace[-1]["d"] if trace else 0.0


def sectors(trace, length_m, step_m=SECTOR_M):
    """Sector times with boundaries every step_m from the line; the last sector ends at
    length_m and may be shorter. None for a sector the trace does not cover."""
    if not trace or not length_m or length_m <= 0:
        return []
    n = max(1, math.ceil(length_m / step_m - 1e-9))
    bounds = [k * step_m for k in range(n)] + [length_m]
    times = [_time_at(trace, b) for b in bounds]
    return [round(t1 - t0, 3) if t0 is not None and t1 is not None else None
            for t0, t1 in zip(times, times[1:], strict=False)]


def best_sectors(laps):
    """Per sector the fastest time over the given laps that carry a trace."""
    per_lap = [sectors(lap["trace"], lap_length_m(lap)) for lap in laps if lap.get("trace")]
    n = max((len(s) for s in per_lap), default=0)
    best = []
    for i in range(n):
        vals = [s[i] for s in per_lap if i < len(s) and s[i] is not None]
        best.append(min(vals) if vals else None)
    return best


def theoretical_best(laps):
    """Sum of the best sectors over the given laps, or None when a sector has no time."""
    best = best_sectors(laps)
    if not best or any(v is None for v in best):
        return None
    return round(sum(best), 3)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_gt7_laps.py && python3 tools/lint.py`
Expected: ALL PASS, lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/scripts/gt7_laps.py tests/test_gt7_laps.py
git commit -m "feat(telemetry): mini-sector times and theoretical best (#788)"
```

---

### Task 2: Shared part 2 helpers, then the lap index with traces, cache and comparison pool

**Files:**
- Modify: `src/scripts/gt7_recording.py` (`_car_name`, `_brief`, `session_tracks`, `_lap_dist`, `export_csv`)
- Modify: `src/scripts/gt7_data.py` (`_stat_hash`, `data_version`)
- Modify: `src/scripts/gt7_laps.py`
- Test: `tests/test_gt7_laps.py`, `tests/test_gt7_recording.py`, `tests/test_gt7_data.py`

**Interfaces:**
- Consumes: Task 1; part 1 `Recording`, `recording_stem`, `GT7_TIME_WINDOW_S`, `parse_packet`, `TelemetryEngine.on_lap/session/lap_started_at/lap_distance`; part 2 `session_tracks`, `TrackDB.project/line_length/name`, `gt7_data.resolve/data_dir/learned_path`.
- Produces in `gt7_recording` (public, used by `export_csv` and `gt7_laps`; the private `_car_name` and `_brief` go away, nothing else imports them):
  - `car_name(cars, car_id) -> str`: `"<maker> <name>"`, the name alone without a maker, `""` without a car id or when `cars` is None.
  - `brief_track(found) -> {"id", "track", "layout", "reverse"}`.
  - `nearest_station(s, driven, length) -> float`: the one of `s`, `s - L`, `s + L` closest to the driven distance (the projection rule of `_lap_dist`).
  - `LapTimeMatcher()` with `lap_closed(lap, wall_ts)` (sets `lap["gt7_time_s"] = None` and waits for GT7's time) and `update(pkt, wall_ts)` (once per packet, after `lap_closed` for the laps that packet closed). It sets `gt7_time_s` from the first new `last_ms > 0` within `GT7_TIME_WINDOW_S`, exactly as `export_csv` did inline.
  - `session_tracks`: an assigned id that `tracks.name()` cannot resolve falls back to matching for that session instead of None.
- Produces in `gt7_data`: `data_version(runtime_base, bundled=None)` keeps hashing runtime files (`index.json`, `signatures.json`, `learned-tracks.json`) by path, mtime and size, but a file resolved to the bundled copy counts by name and size only, so a onefile binary's per-launch `_MEIPASS` dir does not change the version. `fingerprint` is unchanged.
- Produces in `gt7_laps`:
  - Constants `INDEX_VERSION = 1`, `CACHE_SUFFIX = ".laps.json"`, `DECIMATE_M = 2.0`.
  - `cache_path(path) -> str`: `<dir>/<stem>.laps.json`.
  - `cached(path, runtime_base, bundled=None) -> dict | None`: the cached index when `version`, `size`, `mtime` and `data_version` all still match; never builds.
  - `index(path, track_db, cars, runtime_base, key=None, bundled=None) -> dict`: builds or reads the cache. `key` is `"<profile>/<stem>"` for the learned assignment, None without a profile. `cars` may be None (empty car names). Raises `gt7_recording.RecordingError` for an unreadable file. Result:
    - recording level: `version`, `size`, `mtime`, `data_version` (cache stamp), `rec` (stem), `name` (file name), `started` (header string), `start_ts` / `end_ts` (wall time of the first / last packet, meta records excluded, None for an empty file), `dropped`, `track` (display track of the session with the most driven distance: `{"id","track","layout","reverse"}`, `{"candidates": [{"id","track","layout"}]}` or None), `sessions` (`{"<n>": same shape}`), `laps`.
    - per lap: `rec, session, lap, start_t_s, end_t_s` (seconds from `start_ts`), `gt7_time_s` (None when GT7 sent no time), `relay_time_s`, `time_s` (`gt7_time_s` if set, else `relay_time_s`), `status, reason, fuel_used_l, top_speed_kmh, car_id, car` (`car_name`), `track_id` (str or None), `track`, `layout` (`""` when unknown), `distance_m`, `tyre_avg_c` (always 4 floats), `points` (`[[x, z], ...]` every 20 m, for learning), `sectors` (`sectors(trace, lap_length_m(lap))`), `trace`.
  - `summary(lap) -> dict`: the lap without `trace` and `points`.
  - `pool(indexes, track_id, car_id, rec=None, session=None) -> list`: counted laps (`COUNTED`) with this `car_id` sorted by `time_s`. A known `track_id` pools across all indexes; `track_id` None pools only laps with no track of recording stem `rec` and GT7 `session`.
- Why not `gt7_recording.replay_laps`: the index needs every on-track sample per lap and GT7's lap time, which `replay_laps` does not return. `_build` runs its own single replay and shares the rest with the export: `session_tracks` for the per-session decision, `LapTimeMatcher` for GT7's time, `nearest_station` for the projection, `car_name` and `brief_track` for the labels.

- [ ] **Step 1: Write the failing tests**

`tests/test_gt7_recording.py`, before `if __name__ == "__main__":`:

```python
class _UnknownAssignmentTracks(_Tracks):
    """An assignment whose layout the track tables no longer name."""
    def name(self, oid):
        return None


def t_session_tracks_falls_back_to_matching_for_an_unknown_assignment():
    with tempfile.TemporaryDirectory() as d:
        _h, laps, _dropped = rec.replay_laps(_xy_session(d))
        got = rec.session_tracks(laps, _UnknownAssignmentTracks(assigned="gone01"), key="p/s")
        assert got[1]["id"] == "aaa001", f"an unresolvable assignment falls back to matching: {got}"


def t_nearest_station_picks_the_value_nearest_the_driven_distance():
    assert rec.nearest_station(998.0, 1.0, 1000.0) == -2.0, "just behind the line is not a full lap"
    assert rec.nearest_station(3.0, 999.0, 1000.0) == 1003.0
    assert rec.nearest_station(500.0, 497.0, 1000.0) == 500.0
```

`tests/test_gt7_data.py`: add `shutil` to the `import importlib.util, json, os, sys, tempfile` line (`import importlib.util, json, os, shutil, sys, tempfile`), then after `t_data_version_changes_with_learned_file`:

```python
def t_data_version_ignores_where_the_bundled_files_live():
    with tempfile.TemporaryDirectory() as d:
        b1 = _bundled(d)
        b2 = os.path.join(d, "unpacked")
        shutil.copytree(b1, b2)
        os.utime(os.path.join(b2, "index.json"), ns=(1_000_000_000, 1_000_000_000))
        base = os.path.join(d, "runtime")
        assert gd.data_version(base, b1) == gd.data_version(base, b2), \
            "a onefile binary unpacks the same bundled files to a new dir per launch"
        with open(os.path.join(b2, "index.json"), "ab") as fh:
            fh.write(b" ")
        assert gd.data_version(base, b1) != gd.data_version(base, b2), \
            "a different bundled file still changes the version"
```

`tests/test_gt7_laps.py`: replace `import os, sys` with `import contextlib, math, os, struct, sys, tempfile`, then add below `import gt7_laps as gl`:

```python
import gt7_recording
import gt7_telemetry as tm

R = 1000.0 / (2 * math.pi)        # a 1000 m circle around the origin
CAR = 3424


class FakeTracks:
    """Recognises any lap of 900 to 1100 m as 'ring01', the 1000 m circle."""
    def __init__(self, assigned=None, known=True):
        self.assigned, self.known, self.keys, self.learned = assigned, known, [], []

    def match(self, points, length_m):
        if self.known and 900.0 < length_m < 1100.0:
            return {"id": "ring01", "track": "Test Ring", "layout": "Full",
                    "reverse": False, "score_m": 0.4}
        return None

    def name(self, oid):
        return {"id": oid, "track": "Test Ring" if oid == "ring01" else "Other Ring",
                "layout": "Full", "reverse": False, "country": "", "length_m": 1000.0,
                "official_name": "Test Ring"}

    def layouts(self):
        return [self.name("ring01")]

    def line_length(self, oid):
        return 1000.0

    def project(self, points, oid):
        return [(math.atan2(z, x) % (2 * math.pi)) * R for x, z in points]

    def assignment(self, key):
        self.keys.append(key)
        return self.assigned

    def learn(self, oid, points, length_m, key=None):
        self.learned.append((oid, len(points), round(length_m), key))


class FakeCars:
    def lookup(self, car_id):
        return {"id": car_id, "maker": "Mitsubishi", "name": "Lancer Evolution IX",
                "group": "N"}


def _pkt(lap, speed, angle, last_ms=-1):
    b = bytearray(0x158)
    struct.pack_into("<I", b, 0, 0x47375330)
    struct.pack_into("<3f", b, tm.OFF_POS, R * math.cos(angle), 0.0, R * math.sin(angle))
    struct.pack_into("<f", b, tm.OFF_SPEED, speed)
    struct.pack_into("<f", b, tm.OFF_FUEL_LEVEL, 50.0)
    struct.pack_into("<f", b, tm.OFF_FUEL_CAP, 100.0)
    for i, off in enumerate((tm.OFF_TYRE_FL, tm.OFF_TYRE_FR, tm.OFF_TYRE_RL, tm.OFF_TYRE_RR)):
        struct.pack_into("<f", b, off, 80.0 + i)
    struct.pack_into("<h", b, tm.OFF_LAP, lap)
    struct.pack_into("<i", b, tm.OFF_BEST_MS, -1)
    struct.pack_into("<i", b, tm.OFF_LAST_MS, last_ms)
    struct.pack_into("<H", b, tm.OFF_FLAGS, tm.FLAG_ON_TRACK)
    b[tm.OFF_THROTTLE] = 255
    struct.pack_into("<f", b, tm.OFF_RPM, 7000.0)
    b[tm.OFF_GEAR] = 4
    struct.pack_into("<i", b, tm.OFF_CAR_ID, CAR)
    struct.pack_into("<f", b, tm.OFF_STEER, 0.1)
    return bytes(b)


def write_circle_recording(rec_dir, lap_secs=(20.0, 20.0, 16.0, 20.0), t0=1_700_000_000.0,
                           n=400):
    """One lap per entry of lap_secs around the circle, `n` packets each; lap 1 is the
    engine's partial first lap. From the 6th packet of a lap GT7's last_ms reports the
    previous timed lap; 10 packets of one more lap close the last one."""
    w = gt7_recording.RecordingWriter(rec_dir, "Solo", "dev", flush_s=0.05, queue_max=0)
    t, last_ms = t0, -1
    for li, secs in enumerate(list(lap_secs) + [lap_secs[-1]]):
        dt, speed = secs / n, 1000.0 / secs
        for k in range(n if li < len(lap_secs) else 10):
            if li >= 2 and k == 5:
                last_ms = round(lap_secs[li - 1] * 1000)
            w.put(t, "~", _pkt(li + 1, speed, 2 * math.pi * k / n, last_ms))
            t += dt
    w.close()
    return w.path


@contextlib.contextmanager
def _data_version(version):
    real = gl.gt7_data.data_version
    gl.gt7_data.data_version = lambda base, bundled=None: version
    try:
        yield
    finally:
        gl.gt7_data.data_version = real


def _index(path, tracks=None, version="v1"):
    with _data_version(version):
        return gl.index(path, tracks or FakeTracks(), FakeCars(), os.path.dirname(path),
                        key="solo/x")


def _lap(idx, n):
    return [lap for lap in idx["laps"] if lap["lap"] == n][0]


def t_index_laps_status_time_car_and_track():
    with tempfile.TemporaryDirectory() as d:
        path = write_circle_recording(d)
        idx = _index(path)
        assert [(lap["lap"], lap["status"]) for lap in idx["laps"]] == [
            (1, "not counted"), (2, "reference"), (3, "reference"), (4, "counted")]
        assert _lap(idx, 1)["gt7_time_s"] is None
        assert (_lap(idx, 2)["gt7_time_s"], _lap(idx, 3)["time_s"]) == (20.0, 16.0)
        assert abs(_lap(idx, 2)["relay_time_s"] - 19.95) < 1e-6, \
            "the gap to the edge packet belongs to neither lap"
        lap2 = _lap(idx, 2)
        assert lap2["car"] == "Mitsubishi Lancer Evolution IX" and lap2["car_id"] == CAR
        assert (lap2["track_id"], lap2["track"], lap2["layout"]) == ("ring01", "Test Ring", "Full")
        assert lap2["tyre_avg_c"] == [80.0, 81.0, 82.0, 83.0]
        assert len(lap2["points"]) >= 10, "the learn flow needs the 20 m positions"
        assert idx["rec"] == gt7_recording.recording_stem(path)
        assert idx["start_ts"] == 1_700_000_000.0 and idx["end_ts"] > idx["start_ts"]
        assert lap2["start_t_s"] == 20.0, "counted from the first packet"
        assert idx["track"] == {"id": "ring01", "track": "Test Ring", "layout": "Full",
                                "reverse": False}
        assert idx["sessions"] == {"1": idx["track"]}


def t_trace_follows_the_racing_line_every_5_m():
    with tempfile.TemporaryDirectory() as d:
        lap2 = _lap(_index(write_circle_recording(d)), 2)
        tr = lap2["trace"]
        assert [p["d"] for p in tr[:3]] == [0.0, 5.0, 10.0] and tr[-1]["d"] == 995.0, tr[-1]
        mid = tr[100]
        assert abs(mid["t"] - 10.0) < 0.002 and mid["speed_kmh"] == 180.0, mid
        assert (mid["throttle"], mid["brake"], mid["gear"]) == (100.0, 0.0, 4)
        assert mid["steer_deg"] == 5.7, "0.1 rad of steering, positive to the left"
        assert abs(math.hypot(mid["x"], mid["z"]) - R) < 0.2, "positions stay on the circle"
        assert lap2["sectors"] == [4.0, 4.0, 4.0, 4.0, 3.9], lap2["sectors"]


def t_trace_uses_driven_distance_without_track():
    with tempfile.TemporaryDirectory() as d:
        idx = _index(write_circle_recording(d), tracks=FakeTracks(known=False))
        lap2 = _lap(idx, 2)
        assert lap2["track_id"] is None and lap2["track"] == "" and idx["track"] is None
        assert abs(lap2["trace"][100]["t"] - 10.0) < 0.002
        assert abs(lap2["distance_m"] - 997.5) < 0.1
        assert lap2["trace"][-1]["d"] == 995.0 and len(lap2["sectors"]) == 5


def t_assignment_for_the_recording_wins():
    with tempfile.TemporaryDirectory() as d:
        ft = FakeTracks(assigned="ring02")
        idx = _index(write_circle_recording(d), tracks=ft)
        assert "solo/x" in ft.keys, "the learned assignment is looked up by <profile>/<stem>"
        assert {lap["track_id"] for lap in idx["laps"]} == {"ring02"}


def t_index_cache_is_reused_until_something_changes():
    with tempfile.TemporaryDirectory() as d:
        path = write_circle_recording(d)
        builds = []
        real = gl._build

        def counting(*args):
            builds.append(1)
            return real(*args)
        gl._build = counting
        try:
            _index(path)
            assert os.path.basename(gl.cache_path(path)) == \
                gt7_recording.recording_stem(path) + ".laps.json"
            assert os.path.exists(gl.cache_path(path))
            _index(path)
            assert len(builds) == 1, "an unchanged recording reads the cache"
            _index(path, version="v2")
            assert len(builds) == 2, "new track data or a learned track rebuilds"
            with open(path, "ab") as fh:
                fh.write(b"\x00")                  # a truncated record the reader skips
            _index(path, version="v2")
            assert len(builds) == 3, "a grown recording rebuilds"
            with open(gl.cache_path(path), "w", encoding="utf-8") as fh:
                fh.write("{broken")
            assert _index(path, version="v2")["laps"] and len(builds) == 4
            with _data_version("v2"):
                assert gl.cached(path, d) is not None
        finally:
            gl._build = real


def t_pool_compares_counted_laps_of_the_same_track_and_car():
    with tempfile.TemporaryDirectory() as d:
        a = _index(write_circle_recording(d))
        b = _index(write_circle_recording(d, lap_secs=(20.0, 19.0, 18.0), t0=1_700_007_200.0))
        laps = gl.pool([a, b], "ring01", CAR)
        assert [lap["time_s"] for lap in laps] == [16.0, 18.0, 19.0, 20.0, 20.0], laps
        assert all(lap["status"] in gl.COUNTED for lap in laps)
        assert gl.pool([a, b], "ring01", 1) == [], "another car never pools"
        unknown = _index(write_circle_recording(d, t0=1_700_014_400.0),
                         tracks=FakeTracks(known=False))
        assert gl.pool([a, unknown], None, CAR) == [], "an unknown track needs its session"
        mine = gl.pool([a, unknown], None, CAR, rec=unknown["rec"], session=1)
        assert {lap["rec"] for lap in mine} == {unknown["rec"]} and len(mine) == 3
        brief = gl.summary(mine[0])
        assert "trace" not in brief and "points" not in brief and "sectors" in brief

```

`t_nearest_station_picks_the_value_nearest_the_driven_distance` covers the projection rule `_trace` uses; the existing export tests (`t_export_samples_and_laps` checks GT7's lap time, `t_export_car_name_from_tables` the car name, `t_projected_distance_stays_continuous_across_the_line` the projection) cover the refactored `export_csv`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_gt7_recording.py; python3 tests/test_gt7_data.py; python3 tests/test_gt7_laps.py`
Expected: three failures:
- `test_gt7_recording.py`: `AttributeError: module 'gt7_recording' has no attribute 'nearest_station'`.
- `test_gt7_data.py`: `AssertionError: a onefile binary unpacks the same bundled files to a new dir per launch`.
- `test_gt7_laps.py`: `AttributeError: module 'gt7_laps' has no attribute 'gt7_data'` (first index test).

- [ ] **Step 3: Implement**

`src/scripts/gt7_recording.py`: replace `_car_name` with `car_name`, `nearest_station` and the lap-time matcher:

```python
def car_name(cars, car_id):
    """"<maker> <name>" from the car tables; "" without a car id or tables."""
    car = cars.lookup(car_id) if cars is not None and car_id is not None else None
    if car is None:
        return ""
    return f"{car['maker']} {car['name']}" if car.get("maker") else car["name"]


def nearest_station(s, driven, length):
    """The projected distance s, s - L or s + L closest to the driven distance."""
    return min((s, s - length, s + length), key=lambda v: abs(v - driven))


class LapTimeMatcher:
    """Sets gt7_time_s on each closed lap from GT7's last_ms, which arrives shortly
    after the line."""

    def __init__(self):
        self._waiting = []
        self._prev_last_ms = None

    def lap_closed(self, lap, wall_ts):
        """Register a lap the engine closed on this packet; call before update()."""
        lap["gt7_time_s"] = None
        self._waiting.append((lap, wall_ts + GT7_TIME_WINDOW_S, self._prev_last_ms))

    def update(self, pkt, wall_ts):
        for item in list(self._waiting):
            lap, deadline, before = item
            if pkt.last_ms > 0 and pkt.last_ms != before:
                lap["gt7_time_s"] = pkt.last_ms / 1000.0
                self._waiting.remove(item)
            elif wall_ts > deadline:
                self._waiting.remove(item)
        self._prev_last_ms = pkt.last_ms
```

Replace `_brief` with:

```python
def brief_track(found):
    """A matched or named layout as {"id", "track", "layout", "reverse"}."""
    return {k: found[k] for k in ("id", "track", "layout", "reverse")}
```

In `session_tracks`, replace the assigned branch

```python
        if assigned:
            info = tracks.name(assigned)
            out[session] = _brief(info) if info else None
            continue
```

with

```python
        info = tracks.name(assigned) if assigned else None
        if info:
            out[session] = brief_track(info)
            continue
```

and `found = _brief(m)` with `found = brief_track(m)`.

In `_lap_dist`, replace its last two lines (`ref = dist or 0.0` and the `return min(...)`) with:

```python
    return nearest_station(s[0], dist or 0.0, length)
```

In `export_csv`: replace `laps, waiting = [], []` with `laps = []`, add `times = LapTimeMatcher()` after `eng.on_lap = laps.append`, delete `prev_last_ms = None`, and replace the lap-time block inside the packet loop

```python
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
```

with

```python
            for lap in laps[closed:]:
                times.lap_closed(lap, wall_ts)
            times.update(pkt, wall_ts)
```

and `_car_name(cars, lap["car_id"]),` in the `laps.csv` row with `car_name(cars, lap["car_id"]),`. Check with `grep -n "_car_name\|_brief(found\|_brief(m\|_brief(info" src/scripts/gt7_recording.py`: no hits (the `RecordControl._brief` method is unrelated and stays).

`src/scripts/gt7_data.py`: replace `_stat_hash` and `data_version` with:

```python
def _stat_entry(path):
    try:
        st = os.stat(path)
        return f"{path}|{st.st_mtime_ns}|{st.st_size};"
    except OSError:
        return f"{path}|-;"


def _stat_hash(paths):
    h = hashlib.sha1()
    for p in paths:
        h.update(_stat_entry(p).encode("utf-8"))
    return h.hexdigest()[:16]


def data_version(runtime_base, bundled=None):
    """Changes whenever the track data or the learned tracks change (lap-index caches key on it).
    A bundled file counts by name and size only: a onefile binary unpacks it to a new
    temp dir on every launch."""
    h = hashlib.sha1()
    runtime = data_dir(runtime_base) if runtime_base else None
    for name in ("index.json", "signatures.json"):
        path = resolve(name, runtime_base, bundled)
        if runtime and path == os.path.join(runtime, name):
            h.update(_stat_entry(path).encode("utf-8"))
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            size = "-"
        h.update(f"bundled/{name}|{size};".encode("utf-8"))
    if runtime_base:
        h.update(_stat_entry(learned_path(runtime_base)).encode("utf-8"))
    return h.hexdigest()[:16]
```

`src/scripts/gt7_laps.py`, extend the imports:

```python
import bisect
import json
import math
import os
import tempfile

import gt7_data
import gt7_recording
import gt7_telemetry
```

Add after `COUNTED`:

```python
INDEX_VERSION = 1
CACHE_SUFFIX = ".laps.json"
DECIMATE_M = 2.0              # finer samples add nothing to a 5 m trace
```

Append:

```python
def cache_path(path):
    return os.path.join(os.path.dirname(path),
                        gt7_recording.recording_stem(path) + CACHE_SUFFIX)


def _stamp(path, runtime_base, bundled):
    st = os.stat(path)
    return {"version": INDEX_VERSION, "size": st.st_size, "mtime": st.st_mtime,
            "data_version": gt7_data.data_version(runtime_base, bundled)}


def cached(path, runtime_base, bundled=None):
    """The cached index while recording, track data and format are unchanged, else None."""
    try:
        stamp = _stamp(path, runtime_base, bundled)
        with open(cache_path(path), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or any(data.get(k) != v for k, v in stamp.items()):
        return None
    return data


def index(path, track_db, cars, runtime_base, key=None, bundled=None):
    """The lap index of one recording, from the cache when still valid. `key` is
    "<profile>/<stem>", the learned track assignment's key."""
    hit = cached(path, runtime_base, bundled)
    if hit is not None:
        return hit
    stamp = _stamp(path, runtime_base, bundled)
    data = _build(path, track_db, cars, key)
    data.update(stamp)
    _write_cache(cache_path(path), data)
    return data


def _write_cache(path, data):
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".laps-", suffix=".tmp", dir=os.path.dirname(path))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass  # already gone; a read-only dir only costs the cache


def _sample(pkt, t, d):
    """(t, d, kmh, throttle %, brake %, steer deg, gear, x, z) or None for a broken float."""
    if any(v is None or not math.isfinite(v) for v in (pkt.speed_mps, pkt.pos_x, pkt.pos_z, d)):
        return None
    steer = pkt.steer_rad
    steer = math.degrees(steer) if steer is not None and math.isfinite(steer) else None
    return (t, d, pkt.speed_mps * 3.6, pkt.throttle * 100.0 / 255.0,
            pkt.brake * 100.0 / 255.0, steer, pkt.gear, pkt.pos_x, pkt.pos_z)


def _station(x, pts, ds, j):
    a = pts[j]
    if j + 1 >= len(pts) or x <= ds[j]:
        b, f = a, 0.0
    else:
        b, f = pts[j + 1], (x - ds[j]) / (ds[j + 1] - ds[j])

    def mix(i):
        return a[i] + (b[i] - a[i]) * f
    steer = mix(5) if a[5] is not None and b[5] is not None else None
    return {"d": x, "t": round(mix(0), 3), "speed_kmh": round(mix(2), 1),
            "throttle": round(mix(3), 1), "brake": round(mix(4), 1),
            "steer_deg": None if steer is None else round(steer, 1), "gear": a[6],
            "x": round(mix(7), 1), "z": round(mix(8), 1)}


def _trace(samples, track_db, track_id, length):
    """Samples resampled every STEP_M of lap distance: along the racing line when the
    track is known, else the driven distance."""
    if not samples:
        return []
    kept = [samples[0]]
    for s in samples[1:]:
        if s[1] >= kept[-1][1] + DECIMATE_M:
            kept.append(s)
    if kept[-1] is not samples[-1]:
        kept.append(samples[-1])
    dists = [s[1] for s in kept]
    if track_id is not None and length:
        proj = track_db.project([(s[7], s[8]) for s in kept], track_id)
        if proj:
            dists = [d if p is None else gt7_recording.nearest_station(p, d, length)
                     for p, d in zip(proj, dists, strict=True)]
    pts, ds = [], []
    for s, d in zip(kept, dists, strict=True):
        d = max(0.0, d)
        if ds and d <= ds[-1]:
            continue              # corner cutting can step the projection back
        pts.append(s)
        ds.append(d)
    end = min(ds[-1], length) if length else ds[-1]
    out, j = [], 0
    for i in range(int(end // STEP_M) + 1):
        x = i * STEP_M
        while j + 1 < len(ds) and ds[j + 1] <= x:
            j += 1
        out.append(_station(x, pts, ds, j))
    return out


def _track_info(found, track_db):
    """A session's track for display: the layout, the candidate layouts, or None."""
    if not found:
        return None
    if "id" in found:
        return gt7_recording.brief_track(found)
    names = [track_db.name(c) for c in found.get("candidates") or []] if track_db else []
    return {"candidates": [{"id": n["id"], "track": n["track"], "layout": n["layout"]}
                           for n in names if n]}


def _display_track(laps, by_session, track_db):
    driven = {}
    for lap in laps:
        driven[lap["session"]] = driven.get(lap["session"], 0.0) + (lap.get("distance_m") or 0.0)
    if not driven:
        return None
    return _track_info(by_session.get(max(driven, key=driven.get)), track_db)


def _build(path, track_db, cars, key):
    rec = gt7_recording.Recording(path)
    eng = gt7_telemetry.TelemetryEngine()
    laps, lap_samples, lap_tyres = [], [], []
    eng.on_lap = laps.append
    times = gt7_recording.LapTimeMatcher()
    cur, tyre = [], [0.0, 0.0, 0.0, 0.0, 0]
    first = last = None
    for wall_ts, _kind, plain in rec.packets():
        first = wall_ts if first is None else first
        last = wall_ts
        pkt = gt7_telemetry.parse_packet(plain)
        closed = len(laps)
        eng.update(pkt, wall_ts)
        for lap in laps[closed:]:
            times.lap_closed(lap, wall_ts)
            lap_samples.append(cur)
            lap_tyres.append(tyre)
            cur, tyre = [], [0.0, 0.0, 0.0, 0.0, 0]
        times.update(pkt, wall_ts)
        if not pkt.on_track or pkt.paused or pkt.loading:
            continue
        sample = _sample(pkt, wall_ts - eng.lap_started_at(), eng.lap_distance())
        if sample is None:
            continue
        cur.append(sample)
        if all(math.isfinite(v) for v in pkt.tyre_temp):
            for i in range(4):
                tyre[i] += pkt.tyre_temp[i]
            tyre[4] += 1
    by_session = gt7_recording.session_tracks(laps, track_db, key)
    stem = gt7_recording.recording_stem(path)
    out = []
    for lap, samples, tyres in zip(laps, lap_samples, lap_tyres, strict=True):
        found = by_session.get(lap["session"])
        track_id = found["id"] if found and "id" in found else None
        length = track_db.line_length(track_id) if track_id is not None else None
        trace = _trace(samples, track_db, track_id, length)
        relay = round(lap["elapsed"], 3)
        gt7_s = lap["gt7_time_s"]
        out.append({
            "rec": stem, "session": lap["session"], "lap": lap["lap"],
            "start_t_s": round(lap["start"] - first, 3), "end_t_s": round(lap["end"] - first, 3),
            "gt7_time_s": gt7_s, "relay_time_s": relay,
            "time_s": gt7_s if gt7_s is not None else relay,
            "status": lap["status"], "reason": lap["reason"],
            "fuel_used_l": None if lap["fuel_used"] is None else round(lap["fuel_used"], 2),
            "top_speed_kmh": round(lap["top_speed_mps"] * 3.6, 1),
            "car_id": lap["car_id"], "car": gt7_recording.car_name(cars, lap["car_id"]),
            "track_id": track_id,
            "track": found["track"] if track_id is not None else "",
            "layout": found["layout"] if track_id is not None else "",
            "distance_m": round(lap.get("distance_m") or 0.0, 1),
            "tyre_avg_c": [round(s / tyres[4], 1) if tyres[4] else 0.0 for s in tyres[:4]],
            "points": [[round(x, 1), round(z, 1)] for x, z in lap.get("points") or []],
            "sectors": sectors(trace, lap_length_m({"trace": trace})),
            "trace": trace})
    return {"rec": stem, "name": os.path.basename(path),
            "started": rec.header.get("started", ""), "start_ts": first, "end_ts": last,
            "dropped": rec.dropped, "track": _display_track(laps, by_session, track_db),
            "sessions": {str(s): _track_info(v, track_db) for s, v in by_session.items()},
            "laps": out}


def summary(lap):
    """A lap without its trace and positions, for lists."""
    return {k: v for k, v in lap.items() if k not in ("trace", "points")}


def pool(indexes, track_id, car_id, rec=None, session=None):
    """Counted laps comparable with a lap on track_id with car_id, fastest first. A known
    track pools across all given recordings; an unknown one only within (rec, session)."""
    out = []
    for idx in indexes:
        for lap in idx.get("laps", []):
            if lap["status"] not in COUNTED or lap["car_id"] != car_id:
                continue
            if track_id is not None:
                if lap["track_id"] != track_id:
                    continue
            elif lap["track_id"] is not None or (lap["rec"], lap["session"]) != (rec, session):
                continue
            out.append(lap)
    out.sort(key=lambda lap: lap["time_s"])
    return out
```

Notes for the implementer, each checked against the fixture:
- Lap 2 runs 400 packets at 0.05 s; the engine adds `dt` from the second packet on, so `elapsed` is 19.95 s and the driven distance 997.5 m. Stations end at 995 m (`int(997.5 // 5) * 5`), so the last sector is 195 m at 50 m/s = 3.9 s.
- `start_t_s` of lap 2 is 20.0: lap 1's 400 packets take 20.0 s from `start_ts`.
- When `t_trace_follows_the_racing_line_every_5_m` fails on `mid["t"]`, check `_station`'s interpolation before touching the fixture.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_gt7_laps.py && python3 tests/test_gt7_recording.py && python3 tests/test_gt7_data.py && python3 tests/test_gt7_tracks.py && python3 tools/lint.py`
Expected: ALL PASS, lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/scripts/gt7_laps.py src/scripts/gt7_recording.py src/scripts/gt7_data.py tests/test_gt7_laps.py tests/test_gt7_recording.py tests/test_gt7_data.py
git commit -m "feat(telemetry): cached lap index with 5 m traces and a comparison pool (#788)"
```

---
### Task 3: Control Center data layer in `racecast.py`

**Files:**
- Modify: `src/racecast.py` (`_resolve_recording`, line 3204; `telemetry_export_cmd`, line 3259; `telemetry_delete_cmd`, line 3291; new functions after `telemetry_delete_cmd`)
- Modify: `tools/build-binary.py` (hidden imports: after `"--hidden-import", "gt7_data", "--hidden-import", "gt7_tracks",`, line 112)
- Modify: `src/docs/wiki/Relay-Mode.md` (the sentence "`racecast telemetry delete <name>` removes a recording and its exported CSVs.")
- Test: `tests/test_racecast.py`

**Interfaces:**
- Consumes: Task 2 (`index`, `cached`, `cache_path`, `pool`, `summary`, `best_sectors`, `theoretical_best`, `COUNTED`, `STEP_M`, `SECTOR_M`; `gt7_recording.brief_track`); part 1 `_telemetry_rec_dir`, `list_recordings`, `recording_stem`, `RecordingError`, `_recording_sort_key`, `_relay_record_status`, `_foreign_relay_profile`; part 2 `TrackDB.load`, `name`, `layouts`, `learn`, `gt7_data.cars_dir`, `gt7_data.data_version`.
- Produces (all in `racecast`, every `*_data` function never raises):
  - `_find_recording(rec_dir, name) -> str | None`: the path `list_recordings` reports for a recording whose file name or stem is `name` (or the newest for `"latest"`); None for anything else (separators, `.`, `..`, empty, None, missing). Only names found by `os.listdir` match, so a crafted name never reaches the filesystem. `_resolve_recording(rec_dir, name)` keeps its signature and its `sys.exit` on a miss by wrapping it.
  - `_telemetry_track_key(path) -> str | None`: `"<profile>/<stem>"`, None without an active profile. `telemetry_export_cmd` and `telemetry_learn_data` use it; export also takes its databases from `_telemetry_dbs()`.
  - `_telemetry_dbs() -> (TrackDB, CarDB)`.
  - `_telemetry_index(path, dbs=None) -> dict`: `gt7_laps.index` with `key=_telemetry_track_key(path)` and `bundled=resource_path("assets/gt7")`, memoised in `_TELEMETRY_MEMO` (`path -> ((size, mtime_ns, data_version), index)`, least recently used first, at most `TELEMETRY_MEMO_MAX = 64` paths). The memo holds the summary form (laps without `trace` and `points`). A memo hit skips the cache file and `_telemetry_dbs()`. Callers treat the result as read-only.
  - `_telemetry_full_index(path, dbs=None) -> dict`: the index with every lap's `trace` and `points`, from the cache file (built when missing or stale), not memoised. `telemetry_lap_data`, `telemetry_learn_data` and part 4 use it.
  - The pool and the recordings list never build the index of the file the relay is writing; the pool leaves it out.
  - `_telemetry_reason(exc) -> str`: an error text without the machine path an `OSError` carries.
  - `telemetry_recordings_data() -> {"ok", "recordings": [{"name", "rec", "started", "size", "duration_s", "laps", "partial", "recording", "indexed", "track"}]}` newest first by `_recording_sort_key`; `laps` and `track` come only from a still-valid cache (None before the first index), never builds an index; `recording` is true for the file the relay of this profile is writing, the same test `racecast telemetry list` uses; no machine paths.
  - `telemetry_laps_data(rec=None, session=None, track=None, car=None)`: all arguments are query strings or None.
    - `car is None`: one recording's laps, `{"ok", "recording": {"rec", "name", "started", "start_ts", "end_ts", "dropped", "track"}, "laps": [summary]}`.
    - `car` given (`""` means no car id): the pool, `{"ok", "laps": [summary], "best_sectors", "theoretical_best", "reference": summary | None}`. `track` empty or None needs `rec` and `session`.
  - `telemetry_lap_data(rec, session, lap) -> {"ok", "lap": lap without points, "step_m", "sector_m"}`.
  - `telemetry_tracks_data() -> {"ok", "tracks": [{"id", "track", "layout", "reverse"}]}`.
  - `telemetry_learn_data(rec, track_id) -> {"ok", "track": {"id", "track", "layout", "reverse"}}`: learns the layout from the recording's longest counted lap with key `_telemetry_track_key(path)`; refuses without an active profile, because `learn(key=None)` would store the line without assigning the recording. The learned file changes `gt7_data.data_version`, so every memo entry and cached index rebuilds on its next read; nothing deletes caches explicitly.
  - Errors: number arguments are parsed before any file access ("car and session must be numbers", "session and lap must be numbers"); an unreadable recording is "`<rec>` is not a readable recording"; other failures pass through `_telemetry_reason`. No error text carries a machine path.
  - `telemetry_delete_cmd` also removes `<stem>.laps.json` and the memo entry, after the recording and its export folder are gone.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_racecast.py` (after part 1's `t_telemetry_record_without_relay_exits_nonzero`). `test_gt7_laps` sits next to this file, so it imports when the file runs as a script or through `sys.path.insert(0, 'tests')`:

```python
@contextlib.contextmanager
def _telemetry_sandbox(fake_dbs=True):
    """A profile 'solo' with an empty recordings dir and no relay; fake track and car
    tables unless fake_dbs is False."""
    import test_gt7_laps as tgl
    saved = (m._telemetry_rec_dir, m._runtime_base_dir, m._active_profile_name,
             m._telemetry_dbs, m._relay_record_status, m._running_relay_profile)
    with tempfile.TemporaryDirectory() as td:
        rec_dir = os.path.join(td, "runtime", "solo", "telemetry-recordings")
        os.makedirs(rec_dir)
        m._telemetry_rec_dir = lambda: rec_dir
        m._runtime_base_dir = lambda: os.path.join(td, "runtime")
        m._active_profile_name = lambda: "solo"
        m._relay_record_status = lambda: None
        m._running_relay_profile = lambda: ""
        if fake_dbs:
            m._telemetry_dbs = lambda: (tgl.FakeTracks(), tgl.FakeCars())
        m._TELEMETRY_MEMO.clear()
        try:
            yield rec_dir, tgl
        finally:
            m._TELEMETRY_MEMO.clear()
            (m._telemetry_rec_dir, m._runtime_base_dir, m._active_profile_name,
             m._telemetry_dbs, m._relay_record_status, m._running_relay_profile) = saved


def _stem(path):
    return os.path.basename(path)[:-len(".gt7rec")]


def t_find_recording_accepts_only_names_in_the_dir():
    with _telemetry_sandbox() as (rec_dir, tgl):
        path = tgl.write_circle_recording(rec_dir)
        for q in (_stem(path), _stem(path) + ".gt7rec"):
            assert m._find_recording(rec_dir, q) == path, q
        for bad in ("", ".", "..", "../" + _stem(path), os.path.join("..", _stem(path)),
                    "nope", None):
            assert m._find_recording(rec_dir, bad) is None, bad


def t_telemetry_track_key_needs_a_profile():
    with _telemetry_sandbox() as (rec_dir, tgl):
        path = tgl.write_circle_recording(rec_dir)
        assert m._telemetry_track_key(path) == f"solo/{_stem(path)}"
        m._active_profile_name = lambda: None
        assert m._telemetry_track_key(path) is None, "never 'None/<stem>'"
        assert m.telemetry_learn_data(_stem(path), "ring01") == {
            "ok": False, "error": "no active profile"}


def t_telemetry_recordings_data_newest_first_with_cached_track():
    with _telemetry_sandbox() as (rec_dir, tgl):
        old = tgl.write_circle_recording(rec_dir)
        new = tgl.write_circle_recording(rec_dir, t0=1_700_007_200.0)
        d = m.telemetry_recordings_data()
        assert d["ok"] and [r["name"] for r in d["recordings"]] == \
            [os.path.basename(new), os.path.basename(old)]
        top = d["recordings"][0]
        assert top["indexed"] is False and top["track"] is None and top["laps"] is None
        assert top["recording"] is False and "path" not in top, "machine paths stay server-side"
        m.telemetry_laps_data(rec=top["rec"])
        top = m.telemetry_recordings_data()["recordings"][0]
        assert top["indexed"] is True and top["track"]["id"] == "ring01" and top["laps"] == 4


def t_telemetry_recordings_data_marks_the_file_the_relay_writes():
    with _telemetry_sandbox() as (rec_dir, tgl):
        old = tgl.write_circle_recording(rec_dir)
        new = tgl.write_circle_recording(rec_dir, t0=1_700_007_200.0)
        m._relay_record_status = lambda: {"active": True, "file": os.path.basename(new)}
        rows = m.telemetry_recordings_data()["recordings"]
        assert [r["recording"] for r in rows] == [True, False], \
            f"only {os.path.basename(new)} is being written, not {os.path.basename(old)}"


def t_telemetry_laps_data_for_a_recording_and_the_pool():
    with _telemetry_sandbox() as (rec_dir, tgl):
        a = tgl.write_circle_recording(rec_dir)
        tgl.write_circle_recording(rec_dir, lap_secs=(20.0, 19.0, 18.0), t0=1_700_007_200.0)
        d = m.telemetry_laps_data(rec=_stem(a))
        assert d["ok"] and d["recording"]["rec"] == _stem(a) and len(d["laps"]) == 4
        assert d["recording"]["start_ts"] == 1_700_000_000.0
        assert "trace" not in d["laps"][0] and "points" not in d["laps"][0]
        p = m.telemetry_laps_data(track="ring01", car=str(tgl.CAR))
        assert p["ok"] and [lap["time_s"] for lap in p["laps"]] == [16.0, 18.0, 19.0, 20.0, 20.0]
        assert p["reference"]["time_s"] == 16.0
        assert p["best_sectors"] == [3.2, 3.2, 3.2, 3.2, 3.12], p["best_sectors"]
        assert p["theoretical_best"] == 15.92
        assert m.telemetry_laps_data(track="", car=str(tgl.CAR))["ok"] is False, \
            "an unknown track compares only within one session"
        mine = m.telemetry_laps_data(rec=_stem(a), session="1", track="", car=str(tgl.CAR))
        assert mine["ok"] and mine["laps"] == [], "every lap of this recording has a track"
        assert m.telemetry_laps_data(rec="../etc")["ok"] is False
        assert m.telemetry_laps_data(track="ring01", car="x") == {
            "ok": False, "error": "car and session must be numbers"}


def t_telemetry_laps_data_names_a_broken_recording_without_its_path():
    with _telemetry_sandbox() as (rec_dir, tgl):
        import gt7_recording
        stem = _stem(tgl.write_circle_recording(rec_dir))
        real = m._telemetry_index

        def broken(path, dbs=None):
            raise gt7_recording.RecordingError(f"{path}: damaged")
        m._telemetry_index = broken
        try:
            d = m.telemetry_laps_data(rec=stem)
        finally:
            m._telemetry_index = real
        assert d["ok"] is False and rec_dir not in d["error"], d


def t_telemetry_lap_data_returns_the_trace():
    with _telemetry_sandbox() as (rec_dir, tgl):
        stem = _stem(tgl.write_circle_recording(rec_dir))
        d = m.telemetry_lap_data(stem, "1", "3")
        assert d["ok"] and d["lap"]["lap"] == 3 and d["lap"]["trace"][0]["d"] == 0.0
        assert (d["step_m"], d["sector_m"]) == (5.0, 200.0) and "points" not in d["lap"]
        assert m.telemetry_lap_data(stem, "1", "99")["ok"] is False
        assert m.telemetry_lap_data(stem, "x", "3")["ok"] is False
        assert m.telemetry_lap_data(stem, None, "3")["ok"] is False


def t_telemetry_index_is_memoised_until_the_file_or_the_data_change():
    with _telemetry_sandbox() as (rec_dir, tgl):
        path = tgl.write_circle_recording(rec_dir)
        first = m._telemetry_index(path)
        assert m._telemetry_index(path) is first, "an unchanged recording is not read again"
        with open(path, "ab") as fh:
            fh.write(b"\x00")
        second = m._telemetry_index(path)
        assert second is not first, "a grown recording is read again"
        os.makedirs(os.path.join(m._runtime_base_dir(), "gt7"))
        with open(os.path.join(m._runtime_base_dir(), "gt7", "learned-tracks.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{}")
        assert m._telemetry_index(path) is not second, "a learned track is read again"
        for k in range(m.TELEMETRY_MEMO_MAX + 1):
            m._telemetry_index(tgl.write_circle_recording(
                rec_dir, t0=1_700_007_200.0 + 3600 * k, n=40))
        assert len(m._TELEMETRY_MEMO) == m.TELEMETRY_MEMO_MAX, "the memo stays bounded"
        assert path not in m._TELEMETRY_MEMO, "the least recently used index goes first"


def t_telemetry_tracks_data_lists_layouts():
    with _telemetry_sandbox():
        d = m.telemetry_tracks_data()
        assert d == {"ok": True, "tracks": [{"id": "ring01", "track": "Test Ring",
                                             "layout": "Full", "reverse": False}]}


def t_telemetry_learn_data_records_the_assignment():
    import gt7_tracks
    with _telemetry_sandbox(fake_dbs=False) as (rec_dir, tgl):
        stem = _stem(tgl.write_circle_recording(rec_dir))
        base, bundled = m._runtime_base_dir(), m.resource_path("assets/gt7")
        tid = gt7_tracks.TrackDB.load(base, bundled).layouts()[0]["id"]
        assert m.telemetry_learn_data(stem, "no-such-layout")["ok"] is False
        assert m.telemetry_learn_data("nope", tid)["ok"] is False
        d = m.telemetry_learn_data(stem, tid)
        assert d["ok"] and d["track"]["id"] == tid, d
        assert gt7_tracks.TrackDB.load(base, bundled).assignment(f"solo/{stem}") == tid
        laps = m.telemetry_laps_data(rec=stem)["laps"]
        assert {lap["track_id"] for lap in laps} == {tid}, "the assignment wins for every session"


def t_telemetry_learn_data_hides_the_path_of_a_failed_write():
    import gt7_tracks
    with _telemetry_sandbox(fake_dbs=False) as (rec_dir, tgl):
        stem = _stem(tgl.write_circle_recording(rec_dir))
        real = gt7_tracks.TrackDB.learn

        def locked(self, *_a, **_k):
            raise PermissionError(13, "Permission denied", os.path.join(rec_dir, "x.json"))
        gt7_tracks.TrackDB.learn = locked
        try:
            tid = m._telemetry_dbs()[0].layouts()[0]["id"]
            d = m.telemetry_learn_data(stem, tid)
        finally:
            gt7_tracks.TrackDB.learn = real
        assert d == {"ok": False, "error": "could not save the learned track: Permission denied"}, d


def t_telemetry_delete_removes_the_lap_index():
    with _telemetry_sandbox() as (rec_dir, tgl):
        path = tgl.write_circle_recording(rec_dir)
        m.telemetry_laps_data(rec=_stem(path))
        cache = os.path.join(rec_dir, _stem(path) + ".laps.json")
        assert os.path.exists(cache) and path in m._TELEMETRY_MEMO
        m.telemetry_delete_cmd([_stem(path)])
        assert not os.path.exists(cache) and not os.path.exists(path)
        assert path not in m._TELEMETRY_MEMO
```

The best sectors come from lap 3 of the first recording (16 s): its trace ends at 995 m, so its fifth sector is 195 m at 62.5 m/s = 3.12 s and the theoretical best 15.92 s.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_racecast.py`
Expected: FAIL with `AttributeError: module 'racecast' has no attribute '_telemetry_dbs'` (the sandbox of the first new test).

- [ ] **Step 3: Implement**

In `src/racecast.py`, replace `_resolve_recording` with a non-exiting lookup and a wrapper that keeps the CLI's exit:

```python
def _find_recording(rec_dir, name):
    """Path of the recording `name` (file name, stem or 'latest') in rec_dir, or None.
    Only names list_recordings found in rec_dir match, so a path never resolves."""
    import gt7_recording as gr
    rows = gr.list_recordings(rec_dir)
    if name == "latest" and rows:
        return max(rows, key=_recording_sort_key)["path"]
    for row in rows:
        if name in (row["name"], gr.recording_stem(row["path"])):
            return row["path"]
    return None


def _resolve_recording(rec_dir, name):
    path = _find_recording(rec_dir, name)
    if path is None:
        sys.exit(f"no recording named {name!r} in {rec_dir} (see 'racecast telemetry list')")
    return path
```

Replace `telemetry_export_cmd` and `telemetry_delete_cmd` with:

```python
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
    tracks, cars = _telemetry_dbs()
    try:
        res = gr.export_csv(path, out_dir, include_all=args.all, excel=args.excel,
                            cars=cars, tracks=tracks, key=_telemetry_track_key(path))
    except gr.RecordingError as e:
        sys.exit(str(e))
    print(f"wrote {res['samples']} samples and {res['laps']} laps to {res['dir']}")
    if res["dropped"]:
        print(f"note: {res['dropped']} packets were dropped while recording")


def telemetry_delete_cmd(rest):
    """Delete one recording, its export folder and its lap index."""
    import gt7_laps
    import gt7_recording as gr
    if len(rest) != 1:
        sys.exit("usage: racecast telemetry delete <name>")
    rec_dir = _telemetry_rec_dir()
    path = _resolve_recording(rec_dir, rest[0])
    name = os.path.basename(path)
    open_file = None if _foreign_relay_profile() else (_relay_record_status() or {}).get("file")
    if open_file and name.startswith(open_file):
        sys.exit(f"{name} is currently recording; stop it first "
                 "('racecast telemetry record stop')")
    export_dir = os.path.join(rec_dir, gr.recording_stem(path))
    try:
        os.remove(path)
        if os.path.isdir(export_dir):
            shutil.rmtree(export_dir)
    except OSError as e:
        sys.exit(f"could not delete {name}: {e.strerror}")
    _TELEMETRY_MEMO.pop(path, None)
    try:
        os.remove(gt7_laps.cache_path(path))
    except OSError:
        pass  # never indexed
    print(f"deleted {name}")
```

The lap-index removal sits after the `try/except` that exits with "could not delete", so a missing cache never fails the command and a failed delete keeps the cache.

Add after `telemetry_delete_cmd`:

```python
_TELEMETRY_MEMO = {}          # path -> (stamp, lap index), oldest first
TELEMETRY_MEMO_MAX = 8


def _telemetry_track_key(path):
    """The learned-assignment key "<profile>/<stem>", or None without an active profile."""
    import gt7_recording as gr
    name = _active_profile_name()
    return f"{name}/{gr.recording_stem(path)}" if name else None


def _telemetry_dbs():
    """(TrackDB, CarDB) for the lap index: the updated GT7 data when valid, else bundled."""
    import gt7_cars
    import gt7_data
    import gt7_tracks
    base, bundled = _runtime_base_dir(), resource_path("assets/gt7")
    return (gt7_tracks.TrackDB.load(base, bundled),
            gt7_cars.CarDB(gt7_data.cars_dir(base, bundled)))


def _telemetry_index(path, dbs=None):
    """gt7_laps.index for a recording of the active profile, memoised per process while
    the file and the GT7 data are unchanged. Callers treat the result as read-only."""
    import gt7_data
    import gt7_laps
    base, bundled = _runtime_base_dir(), resource_path("assets/gt7")
    st = os.stat(path)
    stamp = (st.st_size, st.st_mtime_ns, gt7_data.data_version(base, bundled))
    hit = _TELEMETRY_MEMO.pop(path, None)
    if hit is not None and hit[0] == stamp:
        _TELEMETRY_MEMO[path] = hit
        return hit[1]
    tracks, cars = dbs or _telemetry_dbs()
    idx = gt7_laps.index(path, tracks, cars, base, key=_telemetry_track_key(path),
                         bundled=bundled)
    _TELEMETRY_MEMO[path] = (stamp, idx)
    while len(_TELEMETRY_MEMO) > TELEMETRY_MEMO_MAX:
        del _TELEMETRY_MEMO[next(iter(_TELEMETRY_MEMO))]
    return idx


def _telemetry_reason(exc):
    """An error text without the machine path an OSError carries."""
    return (exc.strerror or type(exc).__name__) if isinstance(exc, OSError) else str(exc)


def telemetry_recordings_data():
    """Control Center Telemetry view: the active profile's recordings, newest first, with
    the track from a still-valid lap index. Never builds an index and never raises."""
    try:
        import gt7_laps
        import gt7_recording as gr
        base, bundled = _runtime_base_dir(), resource_path("assets/gt7")
        status = None if _foreign_relay_profile() else _relay_record_status()
        open_file = (status or {}).get("file")
        rows = []
        for row in sorted(gr.list_recordings(_telemetry_rec_dir()), key=_recording_sort_key,
                          reverse=True):
            idx = gt7_laps.cached(row["path"], base, bundled)
            rows.append({"name": row["name"], "rec": gr.recording_stem(row["path"]),
                         "started": row["started"], "size": row["size"],
                         "duration_s": round(row["duration_s"], 1),
                         "laps": len(idx["laps"]) if idx else None,
                         "partial": row["partial"],
                         "recording": bool(open_file and row["name"].startswith(open_file)),
                         "indexed": idx is not None,
                         "track": idx.get("track") if idx else None})
        return {"ok": True, "recordings": rows}
    except Exception as exc:
        return {"ok": False,
                "error": f"could not list telemetry recordings: {_telemetry_reason(exc)}"}


def telemetry_laps_data(rec=None, session=None, track=None, car=None):
    """One recording's laps (car None), or the counted laps comparable with a track and
    car across the profile's recordings. Arguments are query strings. Never raises."""
    import gt7_laps
    import gt7_recording as gr
    try:
        car_id = int(car) if car else None
        sess = int(session) if session else None
    except (TypeError, ValueError):
        return {"ok": False, "error": "car and session must be numbers"}
    try:
        rec_dir = _telemetry_rec_dir()
        if car is None:
            path = _find_recording(rec_dir, rec)
            if not path:
                return {"ok": False, "error": f"no recording named {rec!r}"}
            idx = _telemetry_index(path)
            head = {k: idx.get(k) for k in ("rec", "name", "started", "start_ts", "end_ts",
                                            "dropped", "track")}
            return {"ok": True, "recording": head,
                    "laps": [gt7_laps.summary(lap) for lap in idx["laps"]]}
        track_id = track or None
        stem = gr.recording_stem(rec) if rec else None
        if track_id is None and (stem is None or sess is None):
            return {"ok": False, "error": "laps on an unknown track compare within one "
                                          "session: pass rec and session"}
        dbs = _telemetry_dbs()
        indexes = []
        for row in gr.list_recordings(rec_dir):
            try:
                indexes.append(_telemetry_index(row["path"], dbs))
            except Exception:  # noqa: BLE001  one unreadable recording must not hide the others
                continue
        laps = gt7_laps.pool(indexes, track_id, car_id, rec=stem, session=sess)
        return {"ok": True, "laps": [gt7_laps.summary(lap) for lap in laps],
                "best_sectors": gt7_laps.best_sectors(laps),
                "theoretical_best": gt7_laps.theoretical_best(laps),
                "reference": gt7_laps.summary(laps[0]) if laps else None}
    except gr.RecordingError:
        return {"ok": False, "error": f"{rec} is not a readable recording"}
    except Exception as exc:
        return {"ok": False, "error": f"could not read the laps: {_telemetry_reason(exc)}"}


def telemetry_lap_data(rec, session, lap):
    """One lap with its 5 m trace for the comparison charts. Never raises."""
    import gt7_laps
    import gt7_recording as gr
    try:
        s, n = int(session), int(lap)
    except (TypeError, ValueError):
        return {"ok": False, "error": "session and lap must be numbers"}
    try:
        path = _find_recording(_telemetry_rec_dir(), rec)
        if not path:
            return {"ok": False, "error": f"no recording named {rec!r}"}
        for row in _telemetry_index(path)["laps"]:
            if row["session"] == s and row["lap"] == n:
                return {"ok": True, "lap": {k: v for k, v in row.items() if k != "points"},
                        "step_m": gt7_laps.STEP_M, "sector_m": gt7_laps.SECTOR_M}
        return {"ok": False, "error": f"no lap {n} in session {s} of {rec}"}
    except gr.RecordingError:
        return {"ok": False, "error": f"{rec} is not a readable recording"}
    except Exception as exc:
        return {"ok": False, "error": f"could not read the lap: {_telemetry_reason(exc)}"}


def telemetry_tracks_data():
    """Every GT7 layout for the Set track choice. Never raises."""
    try:
        tracks, _cars = _telemetry_dbs()
        return {"ok": True, "tracks": [{k: t[k] for k in ("id", "track", "layout", "reverse")}
                                       for t in tracks.layouts()]}
    except Exception as exc:
        return {"ok": False,
                "error": f"could not load the track list: {_telemetry_reason(exc)}"}


def telemetry_learn_data(rec, track_id):
    """Assign a recording to a layout and learn the layout from its longest counted lap,
    so later recordings on it are recognised. Never raises."""
    import gt7_laps
    import gt7_recording as gr
    try:
        path = _find_recording(_telemetry_rec_dir(), rec)
        if not path:
            return {"ok": False, "error": f"no recording named {rec!r}"}
        key = _telemetry_track_key(path)
        if key is None:
            return {"ok": False, "error": "no active profile"}
        tracks, cars = _telemetry_dbs()
        info = tracks.name(str(track_id or ""))
        if info is None:
            return {"ok": False, "error": "unknown track layout"}
        idx = _telemetry_index(path, (tracks, cars))
        counted = [lap for lap in idx["laps"]
                   if lap["status"] in gt7_laps.COUNTED and lap.get("points")]
        if not counted:
            return {"ok": False, "error": "this recording has no counted lap to learn the "
                                          "track from"}
        best = max(counted, key=lambda lap: lap["distance_m"])
        tracks.learn(info["id"], best["points"], best["distance_m"], key=key)
        return {"ok": True, "track": gr.brief_track(info)}
    except gr.RecordingError:
        return {"ok": False, "error": f"{rec} is not a readable recording"}
    except ValueError as exc:
        return {"ok": False, "error": f"could not learn the track: {exc}"}
    except OSError as exc:
        return {"ok": False,
                "error": f"could not save the learned track: {_telemetry_reason(exc)}"}
    except Exception as exc:
        return {"ok": False, "error": f"could not set the track: {_telemetry_reason(exc)}"}
```

`tools/build-binary.py`: after the line `"--hidden-import", "gt7_data", "--hidden-import", "gt7_tracks",` add

```python
           "--hidden-import", "gt7_laps",
```

`t_function_local_peer_imports_are_frozen` (`tests/test_racecast.py`, line 3785) fails until this line exists.

`src/docs/wiki/Relay-Mode.md`: "`racecast telemetry delete <name>` removes a recording and its exported CSVs." becomes "`racecast telemetry delete <name>` removes a recording, its exported CSVs and its cached lap index."

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_racecast.py && python3 tests/test_gt7_laps.py && python3 tools/lint.py`
Expected: ALL PASS, lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/racecast.py tools/build-binary.py tests/test_racecast.py src/docs/wiki/Relay-Mode.md
git commit -m "feat(ui): telemetry data functions for recordings, laps, tracks and learning (#788)"
```

---

### Task 4: Control Center routes

**Files:**
- Modify: `src/ui/ui_server.py` (`make_handler` docstring, lines 104-126; GET block after the `/api/backup` route, line 566, before `if path == "/api/init/plan":`, line 573; POST block after the `/api/backup/delete` route, line 924, before `if path.startswith("/api/init/step/"):`, line 936)
- Modify: `src/racecast.py` (`ctx` dict, after `"report_send": report_send_data,`, line 7330 before Task 3)
- Test: `tests/test_ui_server.py`, `tests/test_racecast.py`

**Interfaces:**
- Consumes: Task 3 data functions.
- Produces: ctx keys `"telemetry_recordings"`, `"telemetry_laps"`, `"telemetry_lap"`, `"telemetry_tracks"`, `"telemetry_learn"`. Routes:
  - `GET /api/telemetry/recordings` -> `ctx["telemetry_recordings"]()`.
  - `GET /api/telemetry/laps?rec=&session=&track=&car=` -> `ctx["telemetry_laps"](rec, session, track, car)`; parsed with `keep_blank_values=True`, so an absent parameter is None and an empty one `""`.
  - `GET /api/telemetry/lap?rec=&session=&lap=` -> `ctx["telemetry_lap"](rec, session, lap)`.
  - `GET /api/telemetry/tracks` -> `ctx["telemetry_tracks"]()`.
  - `POST /api/telemetry/learn {rec, track_id}` -> `ctx["telemetry_learn"](rec, track_id)`.
  - GET routes answer 200 with the data dict even when `ok` is false (like `/api/profile/env`); a raising ctx function is 500 with JSON. POST answers 400 for `ok: false` or a malformed body.

- [ ] **Step 1: Write the failing tests**

`tests/test_ui_server.py`, `_ctx` (line 73): add these defaults to the returned dict, after `"report_send"` (line 241) and before `"resources"`:

```python
            "telemetry_recordings": lambda: {"ok": True, "recordings": [
                {"name": "20261007-201503.gt7rec", "rec": "20261007-201503",
                 "started": "2026-10-07T20:15:03+02:00", "size": 1000, "duration_s": 600.0,
                 "laps": None, "partial": False, "recording": False, "indexed": False,
                 "track": None}]},
            "telemetry_laps": lambda rec=None, session=None, track=None, car=None: {
                "ok": True, "laps": []},
            "telemetry_lap": lambda rec, session, lap: {"ok": False, "error": "no lap"},
            "telemetry_tracks": lambda: {"ok": True, "tracks": [
                {"id": "suzuka01", "track": "Suzuka Circuit", "layout": "Full Course",
                 "reverse": False}]},
            "telemetry_learn": lambda rec, track_id: {"ok": True, "track": {"id": track_id}},
```

Add after `t_api_ps_save_rejects_bad_ip` (line 2106):

```python
def t_telemetry_routes_pass_their_arguments():
    calls = []
    ctx = _ctx()
    ctx["telemetry_laps"] = lambda *a: calls.append(("laps",) + a) or {"ok": True, "laps": []}
    ctx["telemetry_lap"] = lambda *a: calls.append(("lap",) + a) or {"ok": False, "error": "x"}
    ctx["telemetry_learn"] = lambda *a: calls.append(("learn",) + a) or {
        "ok": False, "error": "unknown track layout"}
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/telemetry/recordings")
        assert code == 200 and json.loads(body)["recordings"][0]["rec"] == "20261007-201503"
        assert _get(port, "/api/telemetry/laps?rec=20261007-201503")[0] == 200
        assert _get(port, "/api/telemetry/laps?track=&car=3424&rec=r&session=2")[0] == 200
        code, body = _get(port, "/api/telemetry/lap?rec=r&session=1&lap=3")
        assert code == 200 and json.loads(body)["ok"] is False, "a GET reports a miss in the body"
        code, body = _get(port, "/api/telemetry/tracks")
        assert code == 200 and json.loads(body)["tracks"][0]["id"] == "suzuka01"
        code, _ = _post_json(port, "/api/telemetry/learn", {"rec": "r", "track_id": "x"})
        assert code == 400, "a refused learn is a client error"
        assert calls == [("laps", "20261007-201503", None, None, None),
                         ("laps", "r", "2", "", "3424"),
                         ("lap", "r", "1", "3"),
                         ("learn", "r", "x")], calls
    finally:
        httpd.shutdown()


def t_telemetry_routes_stay_json_on_errors():
    ctx = _ctx()

    def boom(*_a):
        raise RuntimeError("disk")
    ctx["telemetry_recordings"] = boom
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/telemetry/recordings")
        assert code == 500 and "disk" in json.loads(body)["error"]
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/telemetry/learn",
                                     method="POST", data=b"{bad",
                                     headers={"Content-Type": "application/json"})
        try:
            with _urlopen(req, timeout=5) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        assert code == 400, "a malformed body is refused"
        code, body = _post_json(port, "/api/telemetry/learn", {"rec": "r", "track_id": "suzuka01"})
        assert code == 200 and json.loads(body)["track"]["id"] == "suzuka01"
    finally:
        httpd.shutdown()
```

`tests/test_racecast.py`, after the Task 3 tests:

```python
def t_control_center_wires_the_telemetry_routes():
    with open(os.path.join(ROOT, "src", "racecast.py"), encoding="utf-8") as fh:
        src = fh.read()
    for key in ("recordings", "laps", "lap", "tracks", "learn"):
        assert f'"telemetry_{key}": telemetry_{key}_data,' in src, key
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_ui_server.py; python3 tests/test_racecast.py`
Expected: FAIL (404 on `/api/telemetry/recordings`; the ctx mapping is missing).

- [ ] **Step 3: Implement**

`src/ui/ui_server.py`, `make_handler` docstring: before `jobs (ui_jobs.JobManager), ...` add

```
    telemetry_recordings() -> dict, telemetry_laps(rec, session, track, car) -> dict,
    telemetry_lap(rec, session, lap) -> dict, telemetry_tracks() -> dict,
    telemetry_learn(rec, track_id) -> dict (solo POV lap analysis, query strings in),
```

GET block, before `if path == "/api/init/plan":`:

```python
            if path == "/api/telemetry/recordings":
                try:
                    return self._json(ctx["telemetry_recordings"]())
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not list recordings: {exc}"},
                                      code=500)
            if path in ("/api/telemetry/laps", "/api/telemetry/lap"):
                q = parse_qs(urlparse(self.path).query or "", keep_blank_values=True)
                arg = {k: v[0] for k, v in q.items()}
                try:
                    if path.endswith("/laps"):
                        result = ctx["telemetry_laps"](arg.get("rec"), arg.get("session"),
                                                       arg.get("track"), arg.get("car"))
                    else:
                        result = ctx["telemetry_lap"](arg.get("rec"), arg.get("session"),
                                                      arg.get("lap"))
                except Exception as exc:
                    return self._json({"ok": False, "error": f"could not read laps: {exc}"},
                                      code=500)
                return self._json(result)
            if path == "/api/telemetry/tracks":
                try:
                    return self._json(ctx["telemetry_tracks"]())
                except Exception as exc:
                    return self._json({"ok": False,
                                       "error": f"could not list tracks: {exc}"},
                                      code=500)
```

POST block, before `if path.startswith("/api/init/step/"):`:

```python
            if path == "/api/telemetry/learn":
                body = self._body_json()
                if body is None:
                    return self._json({"ok": False, "error": "malformed JSON body"},
                                      code=400)
                try:
                    result = ctx["telemetry_learn"](body.get("rec"), body.get("track_id"))
                except Exception as exc:
                    return self._json({"ok": False, "error": f"could not set the track: {exc}"},
                                      code=500)
                return self._json(result, code=200 if result.get("ok") else 400)
```

`src/racecast.py` `ctx` dict, after `"report_send": report_send_data,`:

```python
        "telemetry_recordings": telemetry_recordings_data,
        "telemetry_laps": telemetry_laps_data,
        "telemetry_lap": telemetry_lap_data,
        "telemetry_tracks": telemetry_tracks_data,
        "telemetry_learn": telemetry_learn_data,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_ui_server.py && python3 tests/test_racecast.py && python3 tools/lint.py`
Expected: ALL PASS, lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/ui/ui_server.py src/racecast.py tests/test_ui_server.py tests/test_racecast.py
git commit -m "feat(ui): /api/telemetry routes for the lap comparison (#788)"
```

---

### Task 5: Demo recording tool

**Files:**
- Create: `tools/make-demo-recording.py`
- Create: `tests/test_make_demo_recording.py`

**Interfaces:**
- Consumes: part 1 `RecordingWriter` (with `queue_max=0`, an unbounded queue, so a fast producer never drops), packet offsets in `gt7_telemetry`; part 2 `gt7_data.resolve("signatures.json", runtime_base)`, `gt7_tracks.TrackDB(index_path, signatures_path, learned_path)`, the `signatures.json` row shape (`official_id`, `official_name`, `length_m`, `min_x`/`max_x`/`min_z`/`max_z`, `path` as `[x, z]` every 20 m from the line in driving order, `reverse`, `ambiguous_with`, `flags`); Task 2 `gt7_laps.index` (with `cars=None`).
- Produces (module loaded by file path, the name has dashes):
  - `load_rows(path) -> list` (signature rows sorted by `official_name`), `pick_row(rows, query) -> dict` (first row whose id equals `query` or whose name contains it, case-insensitive; `SystemExit` when none).
  - `build(out_dir, row, laps=6, hz=60, start=None, car_id=CAR_ID, profile="demo", mirror=False) -> {"path", "official_id", "official_name", "lap_times"}`.
  - CLI `python3 tools/make-demo-recording.py --out DIR [--track TEXT] [--laps N] [--hz N] [--start TS] [--signatures PATH] [--mirror]`. `--signatures` defaults to the downloaded racing lines, `gt7_data.resolve("signatures.json", <repo>/runtime)`; `signatures.json` never ships, so without a download the tool exits with "no racing lines yet: run 'racecast gt7-data update'".
- The test never reads `signatures.json`: it builds its own closed line with corners of different radii (not mirror-symmetric, so `--mirror` matches nothing), writes it as a one-row `index.json` and `signatures.json` into a temp dir and builds `TrackDB(...)` directly, because `TrackDB.load` resolves through `gt7_data` and a one-row runtime file fails validation (`MIN_SIGNATURES = 50`).
- Shape of the recording: `~` packets (so steering exists); lap 0 is an out-lap that starts at 60 % of the line (the engine's partial first lap); `laps` timed laps follow; 4 s of one more lap close the last one. `best_ms` stays -1 (a flip from a time to -1 would read as a session change); speed never drops below 12 m/s (no pit detection); `last_ms` reports each timed lap 0.5 s after the line. Each lap scales the grip-limited speed profile by its own base factor and a 3-wave sine with a lap-dependent phase, so two laps trade mini-sectors. `--mirror` negates x so no layout matches, which shows the Set track flow.

- [ ] **Step 1: Write the failing test**

Create `tests/test_make_demo_recording.py`:

```python
#!/usr/bin/env python3
"""tools/make-demo-recording.py writes a recording the lap index recognises.
Run: python3 tests/test_make_demo_recording.py"""
import importlib.util, json, math, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import gt7_laps
import gt7_tracks

_spec = importlib.util.spec_from_file_location(
    "make_demo_recording", os.path.join(ROOT, "tools", "make-demo-recording.py"))
demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(demo)


def _circuit(step=20.0):
    """A closed line with corners of different radii, counter-clockwise, a point every
    ~step metres; not mirror-symmetric, so a mirrored lap matches nothing."""
    dense = []
    for i in range(3600):
        a = 2 * math.pi * i / 3600
        r = 300.0 * (1 + 0.35 * math.cos(2 * a) + 0.12 * math.cos(3 * a))
        dense.append((r * math.cos(a), r * math.sin(a)))
    path, run = [dense[0]], 0.0
    for p, q in zip(dense, dense[1:], strict=False):
        run += math.dist(p, q)
        if run >= step:
            path.append(q)
            run = 0.0
    return [[round(x, 1), round(z, 1)] for x, z in path]


def _row():
    path = _circuit()
    xs, zs = [p[0] for p in path], [p[1] for p in path]
    length = sum(math.dist(path[i], path[(i + 1) % len(path)]) for i in range(len(path)))
    return {"official_id": "demo01", "official_name": "Demo Circuit",
            "length_m": round(length, 1), "min_x": min(xs), "max_x": max(xs),
            "min_z": min(zs), "max_z": max(zs), "path": path, "reverse": None,
            "ambiguous_with": [], "flags": []}


def _db(d, row):
    """A track database that knows only `row`."""
    ip, sp = os.path.join(d, "index.json"), os.path.join(d, "signatures.json")
    with open(ip, "w", encoding="utf-8") as fh:
        json.dump({"format": "gt7-datalogger-track-index", "version": 1, "configurations": [
            {"official_id": row["official_id"], "track": "Demo Circuit", "layout": "Full",
             "reverse": False, "official_name": row["official_name"], "country": "X",
             "turns": 6, "length_m": row["length_m"]}]}, fh)
    with open(sp, "w", encoding="utf-8") as fh:
        json.dump({"format": "gt7-datalogger-track-signatures", "version": 1,
                   "signatures": [row]}, fh)
    return gt7_tracks.TrackDB(ip, sp, os.path.join(d, "learned-tracks.json"))


def _index(d, row, **kw):
    out = demo.build(os.path.join(d, "rec"), row, hz=20, start=1_700_000_000.0, **kw)
    return out, gt7_laps.index(out["path"], _db(d, row), None, d, key="demo/x")


def t_demo_recording_is_recognised_and_laps_trade_sectors():
    row = _row()
    with tempfile.TemporaryDirectory() as d:
        out, idx = _index(d, row, laps=3)
        assert len(out["lap_times"]) == 3 and out["official_id"] == row["official_id"]
        timed = [lap for lap in idx["laps"] if lap["status"] in gt7_laps.COUNTED]
        assert len(timed) == 3, [(lap["lap"], lap["status"], lap["reason"]) for lap in idx["laps"]]
        assert {lap["track_id"] for lap in timed} == {row["official_id"]}, idx["sessions"]
        assert len({lap["time_s"] for lap in timed}) == 3, "every lap has its own time"
        a, b = timed[0]["sectors"], timed[1]["sectors"]
        diffs = [y - x for x, y in zip(a, b, strict=False) if x is not None and y is not None]
        assert any(v < 0 for v in diffs) and any(v > 0 for v in diffs), \
            "lap B gains in some mini-sectors and loses in others"
        assert all(lap["trace"][10]["steer_deg"] is not None for lap in timed)


def t_mirrored_demo_is_not_recognised():
    with tempfile.TemporaryDirectory() as d:
        _out, idx = _index(d, _row(), laps=2, mirror=True)
        assert idx["track"] is None or "candidates" in idx["track"], idx["track"]


def t_pick_row_by_name_or_id():
    rows = [{"official_id": "a1", "official_name": "Alpha Ring"},
            {"official_id": "b2", "official_name": "Beta Park"}]
    assert demo.pick_row(rows, "beta")["official_id"] == "b2"
    assert demo.pick_row(rows, "a1")["official_name"] == "Alpha Ring"
    assert demo.pick_row(rows, "")["official_id"] == "a1"
    try:
        demo.pick_row(rows, "gamma"); raise AssertionError("unknown layout accepted")
    except SystemExit as e:
        assert "gamma" in str(e)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 tests/test_make_demo_recording.py`
Expected: FAIL with `FileNotFoundError` for `tools/make-demo-recording.py`.

- [ ] **Step 3: Implement**

Create `tools/make-demo-recording.py`:

```python
#!/usr/bin/env python3
"""Write a synthetic GT7 telemetry recording that follows a real racing line.

Maintainer tool for the Control Center Telemetry view (screenshots, visual checks): an
out-lap and N timed laps on one layout from the downloaded signatures.json (run
`racecast gt7-data update` once). Every lap has its own pace pattern, so two laps trade
mini-sectors.

    python3 tools/make-demo-recording.py --out runtime/solo-pov/telemetry-recordings
"""
import argparse
import bisect
import json
import math
import os
import struct
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import gt7_data
import gt7_recording
import gt7_telemetry as tm
import logsetup

A_LAT = 14.0          # m/s^2 cornering grip
A_BRAKE = 11.0
A_ACCEL = 5.5
V_MAX = 80.0
V_MIN = 12.0
CAR_ID = 2158         # 458 Italia GT3 '13 in the bundled car table
PACE_AMP = 0.035
PACE_WAVES = 3
LAP_BASE = (1.0, 0.992, 1.006, 0.997, 1.003, 0.994, 1.001, 0.996)
GEARS = (16.0, 24.0, 32.0, 41.0, 51.0, 62.0)   # upshift speeds, m/s


def load_rows(path):
    with open(path, encoding="utf-8") as fh:
        return sorted(json.load(fh)["signatures"], key=lambda r: r["official_name"])


def pick_row(rows, query):
    q = (query or "").lower()
    for r in rows:
        if not q or r["official_id"] == query or q in r["official_name"].lower():
            return r
    raise SystemExit(f"no layout in signatures.json matches {query!r}")


def _line(path):
    """Segment lengths, cumulative distance and signed curvature of the closed line."""
    n = len(path)
    seg = [math.dist(path[i], path[(i + 1) % n]) for i in range(n)]
    cum = [0.0]
    for s in seg:
        cum.append(cum[-1] + s)
    curv = []
    for i in range(n):
        a, b, c = path[i - 2], path[i], path[(i + 2) % n]
        turn = math.atan2(c[1] - b[1], c[0] - b[0]) - math.atan2(b[1] - a[1], b[0] - a[0])
        turn = (turn + math.pi) % (2 * math.pi) - math.pi
        span = math.dist(a, b) + math.dist(b, c)
        curv.append(turn / span if span else 0.0)
    return seg, cum, curv


def _speeds(seg, curv, lap):
    """Target speed per line point for one lap: grip in corners, braking and traction."""
    n, total = len(seg), sum(seg)
    base, phase = LAP_BASE[lap % len(LAP_BASE)], 1.9 * lap
    v, pos = [], 0.0
    for i in range(n):
        pace = base * (1 + PACE_AMP * math.sin(2 * math.pi * PACE_WAVES * pos / total + phase))
        grip = math.sqrt(A_LAT * pace / abs(curv[i])) if abs(curv[i]) > 1e-6 else V_MAX
        v.append(max(V_MIN, min(V_MAX * pace, grip)))
        pos += seg[i]
    for _ in range(2):            # twice, so the line's start sees the end's limits
        for i in range(n):
            v[i] = min(v[i], math.sqrt(v[i - 1] ** 2 + 2 * A_ACCEL * seg[i - 1]))
        for i in range(n - 1, -1, -1):
            v[i] = min(v[i], math.sqrt(v[(i + 1) % n] ** 2 + 2 * A_BRAKE * seg[i]))
    return v


def _at(cum, values, s):
    """Linear interpolation of per-point values at distance s along the closed line."""
    s %= cum[-1]
    i = min(bisect.bisect_right(cum, s) - 1, len(values) - 1)
    f = (s - cum[i]) / (cum[i + 1] - cum[i]) if cum[i + 1] > cum[i] else 0.0
    return values[i] + (values[(i + 1) % len(values)] - values[i]) * f


def _packet(x, z, v, lap, last_ms, fuel, throttle, brake, steer, gear, rpm, tyres, car_id):
    b = bytearray(0x158)
    struct.pack_into("<I", b, tm.OFF_MAGIC, 0x47375330)
    struct.pack_into("<3f", b, tm.OFF_POS, x, 0.0, z)
    struct.pack_into("<f", b, tm.OFF_RPM, rpm)
    struct.pack_into("<f", b, tm.OFF_FUEL_LEVEL, fuel)
    struct.pack_into("<f", b, tm.OFF_FUEL_CAP, 100.0)
    struct.pack_into("<f", b, tm.OFF_SPEED, v)
    struct.pack_into("<4f", b, tm.OFF_TYRE_FL, *tyres)
    struct.pack_into("<h", b, tm.OFF_LAP, lap)
    struct.pack_into("<i", b, tm.OFF_BEST_MS, -1)
    struct.pack_into("<i", b, tm.OFF_LAST_MS, last_ms)
    struct.pack_into("<H", b, tm.OFF_FLAGS, tm.FLAG_ON_TRACK)
    b[tm.OFF_GEAR] = gear
    b[tm.OFF_THROTTLE] = throttle
    b[tm.OFF_BRAKE] = brake
    struct.pack_into("<i", b, tm.OFF_CAR_ID, car_id)
    struct.pack_into("<f", b, tm.OFF_STEER, steer)
    b[tm.OFF_THROTTLE_INPUT] = throttle
    b[tm.OFF_BRAKE_INPUT] = brake
    return bytes(b)


def build(out_dir, row, laps=6, hz=60, start=None, car_id=CAR_ID, profile="demo",
          mirror=False):
    """Write one recording into out_dir; returns its path, layout and timed lap times."""
    path = [(-p[0] if mirror else p[0], p[1]) for p in row["path"]]
    seg, cum, curv = _line(path)
    total = cum[-1]
    xs, zs = [p[0] for p in path], [p[1] for p in path]
    w = gt7_recording.RecordingWriter(out_dir, profile, "demo", queue_max=0)
    ts = start if start is not None else time.time() - 3600
    dt = 1.0 / hz
    s = 0.6 * total               # the out-lap starts mid-lap, as after leaving the pits
    lap, lap_t, last_ms, fuel = 0, 0.0, -1, 60.0
    times, report_at, tail = [], None, None
    v_lap = _speeds(seg, curv, lap)
    while tail is None or ts < tail:
        v = _at(cum, v_lap, s)
        acc = (_at(cum, v_lap, s + v * dt) - v) / dt
        throttle = 0 if acc < -1.0 else 255 if acc > 0.3 or v >= V_MAX * 0.97 else 140
        brake = min(255, int(-acc / A_BRAKE * 255)) if acc < -1.0 else 0
        steer = max(-2.6, min(2.6, _at(cum, curv, s) * 2.7 * 14.0))
        gear = 1 + sum(v > g for g in GEARS)
        lo = GEARS[gear - 2] if gear > 1 else 0.0
        hi = GEARS[gear - 1] if gear <= len(GEARS) else V_MAX
        rpm = 4200.0 + 4300.0 * (v - lo) / max(1.0, hi - lo)
        tyres = tuple(78.0 + 6.0 * math.sin(s / 900.0 + i) + 0.4 * lap for i in range(4))
        if report_at is not None and lap_t >= report_at:
            last_ms, report_at = round(times[-1] * 1000), None
        w.put(ts, "~", _packet(_at(cum, xs, s), _at(cum, zs, s), v, lap, last_ms, fuel,
                               throttle, brake, steer, gear, rpm, tyres, car_id))
        ts += dt
        lap_t += dt
        fuel -= 0.00045 * v * dt
        s_next = s + v * dt
        if int(s_next // total) > int(s // total):
            if lap >= 1:
                times.append(lap_t)
                report_at = 0.5          # GT7 shows the lap time shortly after the line
            lap, lap_t = lap + 1, 0.0
            v_lap = _speeds(seg, curv, lap)
            if lap > laps:
                tail = ts + 4.0
        s = s_next
    w.close()
    return {"path": w.path, "official_id": row["official_id"],
            "official_name": row["official_name"], "lap_times": times}


def main():
    logsetup.harden_stdio()
    ap = argparse.ArgumentParser(
        description="Write a synthetic GT7 telemetry recording along a real racing line.")
    ap.add_argument("--out", required=True,
                    help="recordings dir, e.g. runtime/solo-pov/telemetry-recordings")
    ap.add_argument("--track", default="Suzuka",
                    help="part of the layout name or its official id (default: Suzuka)")
    ap.add_argument("--laps", type=int, default=6, help="timed laps after the out-lap")
    ap.add_argument("--hz", type=int, default=60, help="packets per second")
    ap.add_argument("--start", type=float,
                    help="wall time of the first packet (default: one hour ago)")
    ap.add_argument("--signatures",
                    default=gt7_data.resolve("signatures.json", os.path.join(ROOT, "runtime")),
                    help="signatures.json with the racing lines (default: the downloaded one)")
    ap.add_argument("--mirror", action="store_true",
                    help="mirror the line so no layout matches (shows Set track)")
    args = ap.parse_args()
    if not os.path.isfile(args.signatures):
        sys.exit("no racing lines yet: run 'racecast gt7-data update'")
    row = pick_row(load_rows(args.signatures), args.track)
    out = build(args.out, row, laps=args.laps, hz=args.hz, start=args.start,
                mirror=args.mirror)
    laps = ", ".join(f"{t:.3f}" for t in out["lap_times"])
    print(f"wrote {out['path']}: {out['official_name']} ({out['official_id']}), laps {laps}")


if __name__ == "__main__":
    main()
```

If `t_demo_recording_is_recognised_and_laps_trade_sectors` reports no track or candidates for the synthetic circuit, print `idx["sessions"]` and the matcher's score; a wrong line orientation (`_at` across the closing segment) is the likely cause, not the matcher. With the fixture as written, the three timed laps take about 30.2, 29.8 and 29.9 s on a 2152 m line, and laps 1 and 2 trade mini-sectors.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_make_demo_recording.py && python3 tools/make-demo-recording.py --help && python3 tools/lint.py`
Expected: ALL PASS, ASCII help, lint clean. Then `python3 tools/make-demo-recording.py --out "$(mktemp -d)" --signatures /nonexistent`. Expected: exit 1 with "no racing lines yet: run 'racecast gt7-data update'".

- [ ] **Step 5: Commit**

```bash
git add tools/make-demo-recording.py tests/test_make_demo_recording.py
git commit -m "feat(tools): synthetic GT7 recording on a real racing line (#788)"
```

---
### Task 6: Telemetry view: skeleton, recordings, laps, pickers, sector table

**Files:**
- Modify: `src/ui/control-center.html`:
  - CSS: after the kind-gating rules ending with `body:not(.tpl-commentary) .commentary-only, body:not(.tpl-pov) .pov-only { display: none; }` (line 474), before `</style>`.
  - Nav: after the Report nav button (`data-nav="report"`, line 508), before the Help button.
  - View: before `<!-- Post-Event Report -->` (line 1193).
  - `showView` (line 1264): one line after `if (name === 'console') loadConsole();`.
  - `applyKindGating` (line 3042): two lines at its end.
  - `useProfile`: one line after `activeProfile = d.active || name;` (line 3074).
  - JS block: before `function _mbps(bps)` (line 5045).
- Test: `tests/test_ui_server.py`

**Interfaces:**
- Consumes: Task 4 routes `/api/telemetry/recordings`, `/api/telemetry/laps`, `/api/telemetry/lap`.
- Produces (page globals used by Tasks 7 to 9): `tmState` (`loaded, recs, rec, recLaps, pool, a, b, lapA, lapB, sectorM, tracks, chart, map, cursor`), `tmLapCache`, `tmKey(lap)`, `tmTime(s)`, `tmSigned(v)`, `tmEl(tag, attrs, parent)` (SVG element), `tmErr(msg)`, `tmGet(url)`, `tmTrackLabel(track)`, `tmLapTrack(lap)`, `tmReset()`, `tmClear(msg)`, `tmLoad()`, `tmRenderRecs()`, `tmSelectRec(rec)`, `tmRenderLaps()`, `tmSelectB(lap)`, `tmFillPickers(lapB)`, `tmPick(which, key)`, `tmLoadPair()`, `tmFetchLap(key)`, `tmRender()`, `tmRenderSectors()`. Lap key: `"<rec>|<session>|<lap>"`. DOM ids: `tm-err`, `tm-recs`, `tm-laps`, `tm-rec-track`, `tm-settrack`, `tm-track-pick`, `tm-learn`, `tm-a`, `tm-b`, `tm-charts`, `tm-map`, `tm-map-sub`, `tm-sectors`, `tm-sec-sub`.
- The whole view's CSS lands here, so Tasks 7 to 9 only add markup-free JS.
- Recording rows: the name gets ` (recording)` while the relay writes it and ` (unclosed)` for a leftover `.part` (the marks of `racecast telemetry list`); the lap count shows only once the recording is indexed (`laps` is null before).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ui_server.py`, after `t_solo_device_rows_follow_the_template` (line 2047). Behaviour of the pure helpers runs under node through the existing `_run_js` (skipped where node is missing, and only ASCII output is compared, because the Windows runner decodes node's stdout with its locale); the wiring checks stay textual:

```python
def _tm_script(page):
    start = page.index("// Telemetry view (solo POV, #788)")
    return page[start:page.index("// end of the Telemetry view", start)]


def _tm_fn(tm, name):
    i = tm.index("function " + name + "(")
    return tm[i:tm.index("\n}\n", i) + 2]


def t_telemetry_view_is_solo_pov_only():
    page = _cc_page()
    nav = re.search(r'<button class="([^"]*)" data-nav="telemetry"', page)
    assert nav and "pov-only" in nav.group(1).split(), "the nav item exists for solo POV only"
    view = re.search(r'<div class="([^"]*)" data-view="telemetry"', page)
    assert view and {"view", "pov-only"} <= set(view.group(1).split()), \
        "the view is hidden outside solo POV by the existing .pov-only rule"
    show = page[page.index("function showView(name)"):page.index("let _reportPath")]
    assert "name === 'telemetry'" in show, "opening the view loads the recordings"
    gate = page[page.index("function applyKindGating(data)"):page.index("async function useProfile(")]
    assert "currentView === 'telemetry'" in gate, "leaving solo POV leaves the Telemetry view"
    assert "tmLoad()" in gate, "another solo POV profile shows its own recordings at once"
    use = page[page.index("async function useProfile("):page.index("function onKindChange()")]
    assert "tmReset()" in use, "another profile has other recordings"
    tm = _tm_script(page)
    assert "innerHTML" not in tm, "recording values reach the page only as text"
    for route in ("/api/telemetry/recordings", "/api/telemetry/laps?", "/api/telemetry/lap?"):
        assert route in tm, route
    assert "tmRenderSectors()" in tm[tm.index("function tmRender()"):]


def t_telemetry_times_read_as_lap_times():
    tm = _tm_script(_cc_page())
    out = _run_js(_tm_fn(tm, "tmTime") + _tm_fn(tm, "tmSigned") + """
console.log([tmTime(83.456), tmTime(9.5), tmTime(3600), tmSigned(0.25), tmSigned(-1),
             tmSigned(0.0001)].join("|"));""")
    if out is not None:
        assert out.strip() == "1:23.456|0:09.500|60:00.000|+0.250|-1.000|0.000", out


def t_telemetry_recording_rows_mark_open_files_and_unindexed_laps():
    tm = _tm_script(_cc_page())
    fn = tm[tm.index("function tmRenderRecs()"):tm.index("async function tmSelectRec(")]
    assert "r.recording ? ' (recording)'" in fn and "r.partial ? ' (unclosed)'" in fn
    assert "r.laps == null ? ''" in fn, "the list has no lap count before the first index"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 tests/test_ui_server.py`
Expected: FAIL with `ValueError: substring not found` (the first new test in name order, `t_telemetry_recording_rows_mark_open_files_and_unindexed_laps`, finds no Telemetry block).

- [ ] **Step 3: Implement**

CSS, before `</style>`:

```css
  /* Telemetry view (solo POV, #788): lap comparison drawn as inline SVG. */
  .view[data-view="telemetry"] { max-width:1480px; --tm-a:var(--warn); --tm-b:var(--link); }
  .tmgrid { display:grid; grid-template-columns:270px minmax(0,1fr) 340px; gap:14px;
            align-items:start; }
  .tmside, .tmmap { display:flex; flex-direction:column; gap:14px; min-width:0; }
  .tmgrid section { padding:10px 12px; }
  .tmlist { display:flex; flex-direction:column; max-height:36vh; overflow:auto;
            color:var(--faint); font:12px var(--mono); }
  .tmitem { display:grid; grid-template-columns:1fr auto; gap:2px 8px; width:100%;
            min-height:0; text-align:left; background:transparent; border:0;
            border-top:1px solid var(--line); border-radius:6px; padding:7px 8px;
            color:var(--txt); font:12px var(--mono); }
  .tmitem:first-child { border-top:0; }
  .tmitem:hover { background:rgba(148,163,184,.08); }
  .tmitem.sel { background:rgba(59,130,246,.14); box-shadow:inset 2px 0 0 var(--accent); }
  .tmitem.nc { color:var(--dim); }
  .tmitem .tmsub { grid-column:1 / -1; color:var(--dim); font-size:11px; overflow:hidden;
                   text-overflow:ellipsis; white-space:nowrap; }
  .tmpick { display:flex; flex-wrap:wrap; gap:10px 18px; align-items:center; margin:0 0 10px;
            font:12px var(--mono); color:var(--dim); }
  .tmpick label { display:inline-flex; align-items:center; gap:6px; }
  .tmpick select { max-width:320px; font:12px var(--mono); }
  .tmkey { display:inline-block; width:18px; height:3px; border-radius:2px; }
  .tmkey.a { background:var(--tm-a); }
  .tmkey.b { background:var(--tm-b); }
  #tm-charts, #tm-map { display:block; width:100%; height:auto; }
  #tm-charts text, #tm-map text { font:11px var(--mono); fill:var(--dim); }
  #tm-charts text.tm-read { fill:var(--txt); }
  #tm-charts text.tm-unit { fill:var(--faint); }
  .tm-frame { fill:none; stroke:var(--line); }
  .tm-zero { stroke:var(--off); stroke-dasharray:3 3; }
  .tm-a { fill:none; stroke:var(--tm-a); stroke-width:1.4; }
  .tm-b { fill:none; stroke:var(--tm-b); stroke-width:1.6; }
  .tm-gain { fill:rgba(34,197,94,.28); }
  .tm-loss { fill:rgba(239,68,68,.28); }
  .tm-cursor { stroke:var(--txt); stroke-width:1; opacity:.55; pointer-events:none; }
  .tm-seg { fill:none; stroke-width:4; stroke-linecap:round; }
  .tm-seg.gain { stroke:var(--ok); }
  .tm-seg.loss { stroke:var(--bad); }
  .tm-seg.even { stroke:var(--off); }
  .tm-line { fill:var(--txt); }
  .tm-dot-a { fill:var(--tm-a); stroke:var(--bg); stroke-width:1.5; }
  .tm-dot-b { fill:var(--tm-b); stroke:var(--bg); stroke-width:1.5; }
  .tmset { display:flex; gap:8px; align-items:center; flex-wrap:wrap; padding:2px 0 8px; }
  .tmset select { flex:1; min-width:0; font:12px var(--mono); }
  table.tmsec { font-size:12px; }
  table.tmsec td, table.tmsec th { padding:4px 6px; text-align:right;
                                   font-variant-numeric:tabular-nums; }
  table.tmsec td:first-child, table.tmsec th:first-child { text-align:left; }
  table.tmsec td.gain { color:#4ADE80; }
  table.tmsec td.loss { color:var(--bad-text); }
  table.tmsec td.best { color:#C4B5FD; }
  table.tmsec tfoot td { font-weight:600; border-bottom:0; }
  .tmsecwrap { max-height:46vh; overflow:auto; }
  @media (max-width: 1280px) {
    .tmgrid { grid-template-columns:250px minmax(0,1fr); }
    .tmmap { grid-column:1 / -1; flex-direction:row; flex-wrap:wrap; }
    .tmmap section { flex:1 1 320px; }
  }
  @media (max-width: 860px) { .tmgrid { grid-template-columns:1fr; } }
```

Nav, after the Report button:

```html
      <button class="navitem pov-only" data-nav="telemetry" onclick="showView('telemetry')">
        <svg viewBox="0 0 24 24"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>Telemetry</button>
```

View, before `<!-- Post-Event Report -->`:

```html
      <!-- Telemetry (solo POV): lap comparison from the GT7 recordings, #788 -->
      <div class="view pov-only" data-view="telemetry" hidden>
        <div class="viewhead"><h2>Telemetry</h2>
          <span class="sub">laps from this profile's GT7 recordings</span>
          <span class="vhbtns"><button onclick="tmLoad()" title="Read the recordings again">
            <svg viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>Refresh</button></span></div>
        <p class="enverr" id="tm-err" hidden></p>
        <div class="tmgrid">
          <div class="tmside">
            <section><div class="viewhead"><h3>Recordings</h3></div>
              <div class="tmlist" id="tm-recs"></div></section>
            <section><div class="viewhead"><h3>Laps</h3><span class="sub" id="tm-rec-track"></span></div>
              <div class="tmset" id="tm-settrack" hidden>
                <select id="tm-track-pick" aria-label="Track layout"></select>
                <button id="tm-learn" onclick="tmLearn()">Set track</button></div>
              <div class="tmlist" id="tm-laps"></div></section>
          </div>
          <section class="tmmain">
            <div class="tmpick">
              <label><span class="tmkey a"></span>Lap A
                <select id="tm-a" aria-label="Lap A (reference)" onchange="tmPick('a', this.value)"></select></label>
              <label><span class="tmkey b"></span>Lap B
                <select id="tm-b" aria-label="Lap B" onchange="tmPick('b', this.value)"></select></label>
            </div>
            <svg id="tm-charts" role="img" aria-label="Speed, throttle, brake, steering, gear and delta over lap distance"></svg>
          </section>
          <div class="tmmap">
            <section><div class="viewhead"><h3>Track</h3><span class="sub" id="tm-map-sub"></span></div>
              <svg id="tm-map" role="img" aria-label="Track map, mini-sectors green where lap B gains and red where it loses"></svg></section>
            <section><div class="viewhead"><h3>Mini-sectors</h3><span class="sub" id="tm-sec-sub"></span></div>
              <div class="tmsecwrap"><table class="prodtable tmsec" id="tm-sectors"></table></div></section>
          </div>
        </div>
      </div>
```

`showView`, after `if (name === 'console') loadConsole();`:

```js
  if (name === 'telemetry' && !tmState.loaded) tmLoad();
```

`applyKindGating`, as its last lines:

```js
  if (currentView === 'telemetry' && !(kind === 'solo' && template === 'pov')) showView('home');
  else if (currentView === 'telemetry' && !tmState.loaded) tmLoad();   // switched to another solo POV profile
```

`useProfile`, after `activeProfile = d.active || name;`:

```js
  tmReset();                              // the recordings belong to the profile
```

JS block, before `function _mbps(bps)`:

```js
// Telemetry view (solo POV, #788): recordings -> laps -> lap A/B comparison.
// Values from recordings reach the page only through textContent and SVG attributes.
const TM_NS = 'http://www.w3.org/2000/svg';
const tmState = {loaded: false, recs: [], rec: null, recLaps: [], pool: null,
                 a: null, b: null, lapA: null, lapB: null, sectorM: 200, tracks: null,
                 chart: null, map: null, cursor: null};
const tmLapCache = new Map();

function tmKey(l) { return l.rec + '|' + l.session + '|' + l.lap; }
function tmTime(s) {
  if (s == null) return '—';
  const ms = Math.round(s * 1000), m = Math.floor(ms / 60000), r = (ms % 60000) / 1000;
  return m + ':' + (r < 10 ? '0' : '') + r.toFixed(3);
}
function tmSigned(v) {
  if (v == null) return '—';
  if (Math.abs(v) < 0.0005) return '0.000';
  return (v > 0 ? '+' : '') + v.toFixed(3);
}
function tmEl(tag, attrs, parent) {
  const e = document.createElementNS(TM_NS, tag);
  Object.entries(attrs || {}).forEach(([k, v]) => e.setAttribute(k, v));
  if (parent) parent.appendChild(e);
  return e;
}
function tmErr(msg) { const e = $('tm-err'); e.textContent = msg || ''; e.hidden = !msg; }
async function tmGet(url) {
  try { return await (await fetch(url, {cache: 'no-store'})).json(); }
  catch (e) { return {ok: false, error: 'Control Center not reachable.'}; }
}
function tmTrackLabel(t) {
  if (!t) return 'track unknown';
  if (t.candidates) return 'track ambiguous';
  return t.track + (t.layout ? ' - ' + t.layout : '');
}
function tmLapTrack(l) { return l.track ? l.track + (l.layout ? ' - ' + l.layout : '') : 'track unknown'; }

function tmClear(msg) {
  tmState.lapA = tmState.lapB = tmState.pool = tmState.a = tmState.b = null;
  tmState.chart = tmState.map = tmState.cursor = null;
  ['tm-charts', 'tm-map', 'tm-sectors', 'tm-a', 'tm-b', 'tm-map-sub', 'tm-rec-track',
   'tm-sec-sub'].forEach(id => { $(id).textContent = ''; });
  $('tm-laps').textContent = msg || '';
  $('tm-settrack').hidden = true;
}
function tmReset() {
  tmState.loaded = false; tmState.rec = null; tmState.recs = []; tmState.recLaps = [];
  tmLapCache.clear();
  $('tm-recs').textContent = '';
  tmErr('');
  tmClear('');
}

async function tmLoad() {
  tmState.loaded = true;
  tmLapCache.clear();
  tmErr('');
  const d = await tmGet('/api/telemetry/recordings');
  if (!d.ok) { tmErr(d.error || 'could not list the recordings'); return; }
  tmState.recs = d.recordings || [];
  if (!tmState.recs.length) {
    tmState.rec = null;
    tmRenderRecs();
    tmClear('No recordings yet. The Director Panel REC key or TELEMETRY_RECORD=1 in the profile records a session.');
    return;
  }
  const keep = tmState.recs.find(r => r.rec === tmState.rec);
  tmSelectRec((keep || tmState.recs[0]).rec);
}

function tmRenderRecs() {
  const box = $('tm-recs');
  box.textContent = '';
  tmState.recs.forEach(r => {
    const b = document.createElement('button');
    b.className = 'tmitem' + (r.rec === tmState.rec ? ' sel' : '');
    const name = document.createElement('span');
    name.textContent = r.rec + (r.recording ? ' (recording)' : r.partial ? ' (unclosed)' : '');
    const laps = document.createElement('span');
    laps.textContent = r.laps == null ? '' : r.laps + ' laps';
    const sub = document.createElement('span');
    sub.className = 'tmsub';
    sub.textContent = (r.indexed ? tmTrackLabel(r.track) : 'not indexed yet') + ' · '
      + Math.round(r.duration_s / 60) + ' min · ' + (r.size / 1e6).toFixed(1) + ' MB';
    b.append(name, laps, sub);
    b.onclick = () => tmSelectRec(r.rec);
    box.appendChild(b);
  });
}

async function tmSelectRec(rec) {
  tmState.rec = rec;
  tmRenderRecs();
  $('tm-laps').textContent = 'Indexing…';         // the first open replays the whole recording
  const d = await tmGet('/api/telemetry/laps?' + new URLSearchParams({rec}));
  if (tmState.rec !== rec) return;                  // another recording was picked meanwhile
  if (!d.ok) { tmErr(d.error || 'could not read the recording'); $('tm-laps').textContent = ''; return; }
  tmErr('');
  tmState.recLaps = d.laps || [];
  const row = tmState.recs.find(r => r.rec === rec);
  if (row) { row.indexed = true; row.track = d.recording.track; tmRenderRecs(); }
  $('tm-rec-track').textContent = tmTrackLabel(d.recording.track);
  const counted = tmState.recLaps.filter(l => l.status !== 'not counted');
  const pick = (counted.length ? counted : tmState.recLaps).slice(-1)[0];
  if (pick) tmSelectB(pick);
  else tmClear('This recording has no laps.');
}

function tmRenderLaps() {
  const box = $('tm-laps');
  box.textContent = '';
  if (!tmState.recLaps.length) { box.textContent = 'No laps in this recording.'; return; }
  tmState.recLaps.forEach(l => {
    const b = document.createElement('button');
    b.className = 'tmitem' + (l.status === 'not counted' ? ' nc' : '')
      + (tmKey(l) === tmState.b ? ' sel' : '');
    const name = document.createElement('span');
    name.textContent = 'Lap ' + l.lap + (l.session > 1 ? ' · session ' + l.session : '');
    const time = document.createElement('span');
    time.textContent = tmTime(l.time_s);
    const sub = document.createElement('span');
    sub.className = 'tmsub';
    sub.textContent = [l.status === 'not counted' ? 'not counted: ' + l.reason : l.status,
                       l.car || 'car unknown', tmLapTrack(l)].join(' · ');
    sub.title = sub.textContent;
    b.append(name, time, sub);
    b.onclick = () => tmSelectB(l);
    box.appendChild(b);
  });
}

async function tmSelectB(l) {
  const key = tmKey(l);
  tmState.b = key;
  tmRenderLaps();
  const q = new URLSearchParams({car: l.car_id == null ? '' : String(l.car_id),
                                 track: l.track_id == null ? '' : l.track_id});
  if (l.track_id == null) { q.set('rec', l.rec); q.set('session', String(l.session)); }
  const p = await tmGet('/api/telemetry/laps?' + q);
  if (tmState.b !== key) return;
  if (!p.ok) tmErr(p.error || 'could not load the comparable laps');
  tmState.pool = p.ok ? p : {laps: [], best_sectors: [], theoretical_best: null, reference: null};
  tmState.a = tmState.pool.reference ? tmKey(tmState.pool.reference) : null;
  tmFillPickers(l);
  await tmLoadPair();
}

function tmFillPickers(lapB) {
  const pool = tmState.pool.laps || [];
  const forB = pool.some(l => tmKey(l) === tmKey(lapB)) ? pool : pool.concat([lapB]);
  [['tm-a', pool, tmState.a], ['tm-b', forB, tmState.b]].forEach(([id, list, cur]) => {
    const sel = $(id);
    sel.textContent = '';
    if (!list.length) {
      const o = document.createElement('option');
      o.value = ''; o.textContent = 'no counted lap to compare';
      sel.appendChild(o);
    }
    list.forEach(l => {
      const o = document.createElement('option');
      o.value = tmKey(l);
      o.textContent = tmTime(l.time_s) + '  ' + l.rec + ' lap ' + l.lap
        + (l.status === 'not counted' ? ' (not counted)' : '');
      sel.appendChild(o);
    });
    sel.value = cur || '';
  });
}

function tmPick(which, key) {
  tmState[which] = key || null;
  if (which === 'b') tmRenderLaps();
  tmLoadPair();
}

async function tmFetchLap(key) {
  if (tmLapCache.has(key)) return tmLapCache.get(key);
  const [rec, session, lap] = key.split('|');
  const d = await tmGet('/api/telemetry/lap?' + new URLSearchParams({rec, session, lap}));
  if (!d.ok) { tmErr(d.error || 'could not load the lap'); return null; }
  tmState.sectorM = d.sector_m || 200;
  tmLapCache.set(key, d.lap);
  return d.lap;
}

async function tmLoadPair() {
  const want = [tmState.a, tmState.b];
  const [a, b] = await Promise.all(want.map(k => (k ? tmFetchLap(k) : null)));
  if (want[0] !== tmState.a || want[1] !== tmState.b) return;
  tmState.lapA = a;
  tmState.lapB = b;
  tmRender();
}

function tmRender() {
  const B = tmState.lapB;
  $('tm-map-sub').textContent = B ? tmLapTrack(B) : '';
  tmRenderSectors();
}

function tmRenderSectors() {
  const t = $('tm-sectors');
  t.textContent = '';
  const A = tmState.lapA, B = tmState.lapB;
  if (!B) return;
  const best = (tmState.pool && tmState.pool.best_sectors) || [];
  const sa = A ? A.sectors || [] : [], sb = B.sectors || [];
  const head = t.createTHead().insertRow();
  ['Sector', 'A', 'B', 'B-A', 'Best'].forEach(h => {
    const th = document.createElement('th'); th.textContent = h; head.appendChild(th);
  });
  const body = t.createTBody();
  const f = v => (v == null ? '—' : v.toFixed(3));
  for (let i = 0; i < Math.max(sa.length, sb.length); i++) {
    const ta = sa[i], tb = sb[i], bs = best[i];
    const diff = ta != null && tb != null ? tb - ta : null;
    const row = body.insertRow();
    [String(i + 1), f(ta), f(tb), tmSigned(diff), f(bs)].forEach(v => { row.insertCell().textContent = v; });
    if (diff != null && Math.abs(diff) >= 0.0005) row.cells[3].className = diff < 0 ? 'gain' : 'loss';
    if (tb != null && bs != null && Math.abs(tb - bs) < 0.0005) row.cells[2].className = 'best';
  }
  const foot = t.createTFoot().insertRow();
  const lapDiff = A && A.time_s != null && B.time_s != null ? B.time_s - A.time_s : null;
  ['Lap', tmTime(A ? A.time_s : null), tmTime(B.time_s), tmSigned(lapDiff),
   tmTime(tmState.pool ? tmState.pool.theoretical_best : null)].forEach(v => {
    foot.insertCell().textContent = v;
  });
  foot.cells[4].title = 'Theoretical best: the sum of the best mini-sectors';
  if (lapDiff != null && Math.abs(lapDiff) >= 0.0005) foot.cells[3].className = lapDiff < 0 ? 'gain' : 'loss';
  const n = tmState.pool ? (tmState.pool.laps || []).length : 0;
  $('tm-sec-sub').textContent = (tmState.sectorM || 200) + ' m' + (n ? ' · best of ' + n + ' laps' : '');
}
// end of the Telemetry view
```

- [ ] **Step 4: Run the tests and check the page**

Run: `python3 tests/test_ui_server.py && python3 tools/lint.py`
Expected: ALL PASS, lint clean.

Syntax-check the page script (skip when `node` is not installed; Task 10 renders the page anyway):

```bash
python3 - <<'EOF'
import os, re, subprocess, tempfile
page = open("src/ui/control-center.html", encoding="utf-8").read()
fd, path = tempfile.mkstemp(suffix=".js")
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    fh.write("\n".join(re.findall(r"<script>(.*?)</script>", page, re.S)))
subprocess.run(["node", "--check", path], check=True)
os.unlink(path)
print("js syntax ok")
EOF
```

Quick look with the demo data. The tool needs the downloaded racing lines (`runtime/gt7/signatures.json`; run `python3 src/racecast.py gt7-data update` once) and the `solo-pov` profile, which is not tracked in git (if `profiles/solo-pov/` is missing: `python3 src/racecast.py profile new solo-pov --kind solo --template pov`). Run `python3 tools/make-demo-recording.py --out runtime/solo-pov/telemetry-recordings`, then `RACECAST_UI_PORT=8090 python3 src/racecast.py --profile solo-pov ui --no-browser`, open `http://127.0.0.1:8090/`, click Telemetry. Expected: the recording, its laps with times and statuses, filled pickers and the sector table. Charts and map stay empty until Tasks 7 and 8. Stop the UI with Ctrl+C.

- [ ] **Step 5: Commit**

```bash
git add src/ui/control-center.html tests/test_ui_server.py
git commit -m "feat(ui): Telemetry view with recordings, laps and the mini-sector table (#788)"
```

---

### Task 7: Stacked charts with a shared cursor

**Files:**
- Modify: `src/ui/control-center.html` (the Telemetry JS block from Task 6)
- Test: `tests/test_ui_server.py`

**Interfaces:**
- Consumes: Task 6 `tmState`, `tmEl`, `tmSigned`, `tmRender`.
- Produces: `TM_STEP = 5` (mirrors `gt7_laps.STEP_M`), `TM_PAD`, `TM_CH` (panels in this order: `speed_kmh`, `throttle`, `brake`, `steer_deg`, `gear`, `delta`), `tmDelta(lapA, lapB) -> [{d, delta}]` (`round(B.t - A.t, 3)` at each station both traces reach; station `i` is `d = i * TM_STEP`), `tmPath(rows, key, X, Y, step)`, `tmDeltaArea(svg, delta, X, Y)`, `tmRenderCharts()`, `tmHover(ev)`, `tmLeave()`. `tmState.chart = {X, maxD, W, panels: [{c, read}]}`, `tmState.cursor` (the SVG line).
- Delta is computed in the page, not on the server, because both traces are already loaded and share their 5 m stations. Sector times always come from the server (`lap.sectors`, `pool.best_sectors`).

- [ ] **Step 1: Write the failing test**

Add after `t_telemetry_view_is_solo_pov_only`:

```python
def t_telemetry_charts_share_one_cursor():
    tm = _tm_script(_cc_page())
    keys = re.findall(r"\{key: '(\w+)'", tm[tm.index("const TM_CH"):tm.index("];", tm.index("const TM_CH"))])
    assert keys == ["speed_kmh", "throttle", "brake", "steer_deg", "gear", "delta"], keys
    assert "addEventListener('pointermove', tmHover)" in tm
    assert "tmRenderCharts()" in tm[tm.index("function tmRender()"):]


def t_telemetry_delta_and_paths_follow_the_traces():
    tm = _tm_script(_cc_page())
    out = _run_js(_tm_fn(tm, "tmDelta") + _tm_fn(tm, "tmPath") + """
const tr = ts => ts.map((t, i) => ({d: i * 5, t}));
console.log(JSON.stringify(tmDelta({trace: tr([0, 1, 2])}, {trace: tr([0, 0.5, 1, 1.5])})));
const rows = [{d: 0, v: 1}, {d: 5, v: 2}, {d: 10, v: null}, {d: 15, v: 3}];
console.log(tmPath(rows, 'v', d => d, v => 10 * v, false));
console.log(tmPath(rows.slice(0, 2), 'v', d => d, v => 10 * v, true));
console.log(tmPath([], 'v', d => d, v => v, false));""")
    if out is not None:
        assert out.strip().splitlines() == [
            '[{"d":0,"delta":0},{"d":5,"delta":-0.5},{"d":10,"delta":-1}]',
            "M0.0 10.0L5.0 20.0M15.0 30.0", "M0.0 10.0H5.0V20.0", "M0 0"], \
            "delta is B minus A over the stations both laps reach; a gap lifts the pen"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 tests/test_ui_server.py`
Expected: FAIL with `ValueError: substring not found` (no `const TM_CH`).

- [ ] **Step 3: Implement**

In the Telemetry JS block, after `const tmLapCache = new Map();`:

```js
const TM_STEP = 5;                        // gt7_laps.STEP_M: one trace point per 5 m
const TM_PAD = {l: 70, r: 10, t: 4, gap: 10, b: 22};
const TM_CH = [
  {key: 'speed_kmh', label: 'Speed', unit: 'km/h', h: 120, lo: 0},
  {key: 'throttle', label: 'Throttle', unit: '%', h: 52, lo: 0, hi: 100},
  {key: 'brake', label: 'Brake', unit: '%', h: 52, lo: 0, hi: 100},
  {key: 'steer_deg', label: 'Steering', unit: 'deg', h: 70, sym: true},
  {key: 'gear', label: 'Gear', unit: '', h: 52, lo: 0, step: true},
  {key: 'delta', label: 'Delta B-A', unit: 's', h: 90, sym: true},
];
```

Replace `tmRender` with:

```js
function tmRender() {
  const B = tmState.lapB;
  $('tm-map-sub').textContent = B ? tmLapTrack(B) : '';
  tmRenderCharts();
  tmRenderSectors();
}
```

Add before `// end of the Telemetry view`:

```js
function tmDelta(a, b) {
  const n = Math.min(a.trace.length, b.trace.length), out = [];
  for (let i = 0; i < n; i++) {
    out.push({d: a.trace[i].d, delta: Math.round((b.trace[i].t - a.trace[i].t) * 1000) / 1000});
  }
  return out;
}

function tmPath(rows, key, X, Y, step) {
  let d = '', pen = false;
  rows.forEach(r => {
    const v = r[key];
    if (v == null) { pen = false; return; }
    const x = X(r.d).toFixed(1), y = Y(v).toFixed(1);
    d += !pen ? 'M' + x + ' ' + y : step ? 'H' + x + 'V' + y : 'L' + x + ' ' + y;
    pen = true;
  });
  return d || 'M0 0';
}

function tmDeltaArea(svg, delta, X, Y) {
  const y0 = Y(0), x0 = X(delta[0].d), x1 = X(delta[delta.length - 1].d);
  let d = 'M' + x0.toFixed(1) + ' ' + y0.toFixed(1);
  delta.forEach(r => { d += 'L' + X(r.d).toFixed(1) + ' ' + Y(r.delta).toFixed(1); });
  d += 'L' + x1.toFixed(1) + ' ' + y0.toFixed(1) + 'Z';
  const defs = tmEl('defs', {}, svg);
  tmEl('rect', {x: x0, y: y0 - 2000, width: x1 - x0, height: 2000}, tmEl('clipPath', {id: 'tm-clip-up'}, defs));
  tmEl('rect', {x: x0, y: y0, width: x1 - x0, height: 2000}, tmEl('clipPath', {id: 'tm-clip-dn'}, defs));
  tmEl('path', {d, class: 'tm-loss', 'clip-path': 'url(#tm-clip-up)'}, svg);   // B behind
  tmEl('path', {d, class: 'tm-gain', 'clip-path': 'url(#tm-clip-dn)'}, svg);   // B ahead
}

function tmRenderCharts() {
  const svg = $('tm-charts');
  svg.textContent = '';
  tmState.chart = tmState.cursor = null;
  const A = tmState.lapA, B = tmState.lapB;
  if (!B || !B.trace.length) return;
  const W = Math.max(420, Math.round(svg.getBoundingClientRect().width) || 760);
  const H = TM_PAD.t + TM_CH.reduce((s, c) => s + c.h + TM_PAD.gap, 0) + TM_PAD.b;
  svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
  const last = l => (l && l.trace.length ? l.trace[l.trace.length - 1].d : 0);
  const maxD = Math.max(last(A), last(B)) || 1;
  const X = d => TM_PAD.l + d / maxD * (W - TM_PAD.l - TM_PAD.r);
  const delta = A && A.trace.length ? tmDelta(A, B) : [];
  const panels = [];
  let y = TM_PAD.t;
  TM_CH.forEach(c => {
    const series = c.key === 'delta' ? (delta.length ? [{cls: 'tm-b', rows: delta}] : [])
      : [A && {cls: 'tm-a', rows: A.trace}, {cls: 'tm-b', rows: B.trace}].filter(Boolean);
    const vals = [];
    series.forEach(s => s.rows.forEach(r => { if (r[c.key] != null) vals.push(r[c.key]); }));
    let lo = c.lo != null ? c.lo : (vals.length ? Math.min(...vals) : 0);
    let hi = c.hi != null ? c.hi : (vals.length ? Math.max(...vals) : 1);
    if (c.sym) {
      const m = Math.max(0.1, ...vals.map(Math.abs)) * 1.1;
      lo = -m; hi = m;
    } else if (c.hi == null) {
      hi = c.step ? Math.max(hi, 1) + 0.5 : Math.max(hi, lo + 1) * 1.05;
    }
    const top = y;
    const Y = v => top + c.h - (v - lo) / (hi - lo) * c.h;
    tmEl('rect', {x: TM_PAD.l, y: top, width: W - TM_PAD.l - TM_PAD.r, height: c.h, class: 'tm-frame'}, svg);
    if (lo < 0 && hi > 0) tmEl('line', {x1: TM_PAD.l, x2: W - TM_PAD.r, y1: Y(0), y2: Y(0), class: 'tm-zero'}, svg);
    tmEl('text', {x: 4, y: top + 13}, svg).textContent = c.label;
    tmEl('text', {x: 4, y: top + 27, class: 'tm-unit'}, svg).textContent = c.unit;
    if (c.key === 'delta') {
      if (delta.length) tmDeltaArea(svg, delta, X, Y);
      else tmEl('text', {x: TM_PAD.l + 8, y: top + c.h / 2, class: 'tm-unit'}, svg).textContent = 'no lap A to compare';
    }
    series.forEach(s => tmEl('path', {d: tmPath(s.rows, c.key, X, Y, c.step), class: s.cls}, svg));
    panels.push({c, read: tmEl('text', {x: W - TM_PAD.r - 6, y: top + 13, 'text-anchor': 'end', class: 'tm-read'}, svg)});
    y += c.h + TM_PAD.gap;
  });
  const tick = maxD > 4000 ? 1000 : maxD > 1500 ? 500 : 200;
  for (let d = 0; d <= maxD; d += tick) {
    tmEl('text', {x: X(d), y: H - 6, 'text-anchor': 'middle'}, svg).textContent = (d / 1000).toFixed(1) + ' km';
  }
  tmState.cursor = tmEl('line', {class: 'tm-cursor', x1: -10, x2: -10, y1: TM_PAD.t, y2: H - TM_PAD.b}, svg);
  tmState.chart = {X, maxD, W, panels};
}

function tmHover(ev) {
  const ch = tmState.chart;
  if (!ch) return;
  const box = $('tm-charts').getBoundingClientRect();
  const px = (ev.clientX - box.left) * ch.W / box.width;
  const d = Math.max(0, Math.min(ch.maxD, (px - TM_PAD.l) / (ch.W - TM_PAD.l - TM_PAD.r) * ch.maxD));
  const x = ch.X(d).toFixed(1);
  tmState.cursor.setAttribute('x1', x);
  tmState.cursor.setAttribute('x2', x);
  const at = l => (l && l.trace.length ? l.trace[Math.min(l.trace.length - 1, Math.round(d / TM_STEP))] : null);
  const ra = at(tmState.lapA), rb = at(tmState.lapB);
  ch.panels.forEach((p, i) => {
    const k = p.c.key;
    const txt = k === 'delta' ? (ra && rb ? tmSigned(rb.t - ra.t) + ' s' : '')
      : [ra, rb].map(r => (r && r[k] != null ? Number(r[k]).toFixed(k === 'gear' ? 0 : 1) : '-')).join(' / ');
    p.read.textContent = (i === 0 ? (d / 1000).toFixed(2) + ' km   ' : '') + txt;
  });
}

function tmLeave() {
  if (tmState.cursor) { tmState.cursor.setAttribute('x1', -10); tmState.cursor.setAttribute('x2', -10); }
  if (tmState.chart) tmState.chart.panels.forEach(p => { p.read.textContent = ''; });
}

$('tm-charts').addEventListener('pointermove', tmHover);
$('tm-charts').addEventListener('pointerleave', tmLeave);
let tmResizeQueued = false;
window.addEventListener('resize', () => {
  if (currentView !== 'telemetry' || !tmState.lapB || tmResizeQueued) return;
  tmResizeQueued = true;
  requestAnimationFrame(() => { tmResizeQueued = false; tmRenderCharts(); });
});
```

Readouts show `A / B` per panel (`-` where a lap has no value, e.g. steering in an `A` packet), the distance on the speed panel, and the signed gap on the delta panel. Below the zero line on the delta panel B is ahead (green), above it B is behind (red).

- [ ] **Step 4: Run the tests and check the page**

Run: `python3 tests/test_ui_server.py && python3 tools/lint.py`, then the `node --check` snippet from Task 6 Step 4.
Expected: ALL PASS, lint clean, `js syntax ok`.

Reload the demo view (Task 6 Step 4). Expected: six stacked panels with both laps, the delta curve shaded green below and red above zero, a cursor line and readouts that follow the pointer.

- [ ] **Step 5: Commit**

```bash
git add src/ui/control-center.html tests/test_ui_server.py
git commit -m "feat(ui): telemetry charts over lap distance with a shared cursor (#788)"
```

---

### Task 8: Track map with mini-sectors

**Files:**
- Modify: `src/ui/control-center.html` (the Telemetry JS block)
- Test: `tests/test_ui_server.py`

**Interfaces:**
- Consumes: Task 6 `tmState.sectorM`, lap `sectors`; Task 7 `tmHover`, `tmLeave`.
- Produces: `tmRenderMap()`, `tmMapCursor(rowA, rowB)`; `tmState.map = {P, dotA, dotB}` where `P(point) -> [svgX, svgY]` maps GT7 x to the right and z downward. Lap A is a thin line; lap B is drawn per mini-sector, class `gain` (B faster than A in that sector), `loss` (slower) or `even` (equal within 0.5 ms, or no A).

- [ ] **Step 1: Write the failing test**

Add after `t_telemetry_charts_share_one_cursor`:

```python
def t_telemetry_map_colours_mini_sectors():
    tm = _tm_script(_cc_page())
    fn = tm[tm.index("function tmRenderMap()"):tm.index("function tmMapCursor(")]
    assert "(p.z - z0) * s" in fn, "GT7 z runs downward on the map, as in the report"
    assert "'gain'" in fn and "'loss'" in fn and "tm-seg" in fn
    assert "tmMapCursor(ra, rb)" in tm[tm.index("function tmHover("):tm.index("function tmLeave(")]
    assert "tmRenderMap()" in tm[tm.index("function tmRender()"):]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 tests/test_ui_server.py`
Expected: FAIL with `ValueError: substring not found` (no `function tmRenderMap()`).

- [ ] **Step 3: Implement**

Replace `tmRender` with:

```js
function tmRender() {
  const B = tmState.lapB;
  $('tm-map-sub').textContent = B ? tmLapTrack(B) : '';
  tmRenderCharts();
  tmRenderMap();
  tmRenderSectors();
}
```

In `tmHover`, add as its last line: `  tmMapCursor(ra, rb);`. In `tmLeave`, add as its last line: `  tmMapCursor(null, null);`.

Add before `$('tm-charts').addEventListener('pointermove', tmHover);`:

```js
function tmRenderMap() {
  const svg = $('tm-map');
  svg.textContent = '';
  tmState.map = null;
  const A = tmState.lapA, B = tmState.lapB;
  if (!B || !B.trace.length) return;
  const all = B.trace.concat(A ? A.trace : []);
  const xs = all.map(p => p.x), zs = all.map(p => p.z);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), z0 = Math.min(...zs), z1 = Math.max(...zs);
  const W = 320, pad = 12, s = (W - 2 * pad) / (Math.max(x1 - x0, z1 - z0) || 1);
  svg.setAttribute('viewBox', '0 0 ' + W + ' ' + Math.round((z1 - z0) * s + 2 * pad));
  const P = p => [pad + (p.x - x0) * s, pad + (p.z - z0) * s];
  const line = pts => 'M' + pts.map(p => P(p).map(v => v.toFixed(1)).join(' ')).join('L');
  if (A && A.trace.length) tmEl('path', {d: line(A.trace), class: 'tm-a'}, svg);
  const step = tmState.sectorM || 200;
  const sa = A ? A.sectors || [] : [];
  (B.sectors || []).forEach((tb, i) => {
    const seg = B.trace.filter(p => p.d >= i * step && p.d <= (i + 1) * step);
    if (seg.length < 2) return;
    const ta = sa[i];
    const diff = ta != null && tb != null ? tb - ta : null;
    const cls = diff == null || Math.abs(diff) < 0.0005 ? 'even' : diff < 0 ? 'gain' : 'loss';
    const path = tmEl('path', {d: line(seg), class: 'tm-seg ' + cls}, svg);
    tmEl('title', {}, path).textContent = 'Sector ' + (i + 1) + (diff == null ? '' : ': ' + tmSigned(diff) + ' s');
  });
  const [sx, sy] = P(B.trace[0]);
  tmEl('circle', {cx: sx, cy: sy, r: 3.5, class: 'tm-line'}, svg);   // start/finish line
  tmState.map = {P, dotA: tmEl('circle', {r: 5, cx: -20, cy: -20, class: 'tm-dot-a'}, svg),
                 dotB: tmEl('circle', {r: 5, cx: -20, cy: -20, class: 'tm-dot-b'}, svg)};
}

function tmMapCursor(ra, rb) {
  const m = tmState.map;
  if (!m) return;
  [[m.dotA, ra], [m.dotB, rb]].forEach(([dot, r]) => {
    const [x, y] = r ? m.P(r) : [-20, -20];
    dot.setAttribute('cx', x);
    dot.setAttribute('cy', y);
  });
}
```

- [ ] **Step 4: Run the tests and check the page**

Run: `python3 tests/test_ui_server.py && python3 tools/lint.py`, then the `node --check` snippet.
Expected: ALL PASS, lint clean, `js syntax ok`.

Reload the demo view. Expected: the layout's outline, lap B in green and red mini-sectors, a start/finish dot, and two dots that follow the chart cursor. The demo uses Suzuka by default: compare the outline with the real circuit map. If it is mirrored, the axis assumption in `P` is wrong; stop and report it, because part 4 draws the report map the same way.

- [ ] **Step 5: Commit**

```bash
git add src/ui/control-center.html tests/test_ui_server.py
git commit -m "feat(ui): telemetry track map with mini-sectors gained and lost (#788)"
```

---

### Task 9: Set track for unknown or ambiguous recordings

**Files:**
- Modify: `src/ui/control-center.html` (the Telemetry JS block; one line in `tmSelectRec`)
- Test: `tests/test_ui_server.py`

**Interfaces:**
- Consumes: Task 4 `GET /api/telemetry/tracks`, `POST /api/telemetry/learn`; Task 6 `tmSelectRec`, `tmLapCache`; existing `confirmModal(body, opts)` (line 4992).
- Produces: `tmShowSetTrack(track)`, `tmLearn()`. Shown when the recording's display track is None or `{"candidates": [...]}`; candidates are listed first.

- [ ] **Step 1: Write the failing test**

Add after `t_telemetry_map_colours_mini_sectors`:

```python
def t_telemetry_set_track_confirms_then_learns():
    tm = _tm_script(_cc_page())
    learn = tm[tm.index("async function tmLearn()"):]
    learn = learn[:learn.index("\n}\n")]
    assert learn.index("confirmModal(") < learn.index("fetch('/api/telemetry/learn'"), \
        "learning writes machine-wide data, so it is confirmed first"
    assert "tmLapCache.clear()" in learn and "tmSelectRec(" in learn
    assert "/api/telemetry/tracks" in tm
    sel = tm[tm.index("async function tmSelectRec("):tm.index("function tmRenderLaps()")]
    assert "tmShowSetTrack(d.recording.track)" in sel
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 tests/test_ui_server.py`
Expected: FAIL with `ValueError: substring not found` (no `async function tmLearn()`).

- [ ] **Step 3: Implement**

In `tmSelectRec`, after `$('tm-rec-track').textContent = tmTrackLabel(d.recording.track);` add:

```js
  tmShowSetTrack(d.recording.track);
```

Add before `$('tm-charts').addEventListener('pointermove', tmHover);`:

```js
async function tmShowSetTrack(track) {
  const open = !track || !!track.candidates;
  $('tm-settrack').hidden = !open;
  if (!open) return;
  if (!tmState.tracks) {
    const d = await tmGet('/api/telemetry/tracks');
    tmState.tracks = d.ok ? d.tracks : [];
  }
  const want = new Set(((track && track.candidates) || []).map(c => c.id));
  const list = tmState.tracks.filter(t => want.has(t.id))
    .concat(tmState.tracks.filter(t => !want.has(t.id)));
  const sel = $('tm-track-pick');
  sel.textContent = '';
  list.forEach(t => {
    const o = document.createElement('option');
    o.value = t.id;
    o.textContent = t.track + (t.layout ? ' - ' + t.layout : '') + (t.reverse ? ' (reverse)' : '');
    sel.appendChild(o);
  });
}

async function tmLearn() {
  const sel = $('tm-track-pick'), rec = tmState.rec;
  if (!sel.value || !rec) return;
  const label = sel.options[sel.selectedIndex].textContent;
  if (!(await confirmModal('Set the track of ' + rec + ' to ' + label + '?\n'
      + 'racecast learns the layout from the longest counted lap, so later recordings '
      + 'on it are recognised.', {title: 'Set track'}))) return;
  const btn = $('tm-learn');
  btn.disabled = true;
  try {
    const r = await (await fetch('/api/telemetry/learn', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({rec, track_id: sel.value})})).json();
    if (!r.ok) { tmErr(r.error || 'could not set the track'); return; }
    tmErr('');
    tmLapCache.clear();                 // every lap of this recording gets the new track
    tmSelectRec(rec);
  } catch (e) {
    tmErr('Control Center not reachable.');
  } finally {
    btn.disabled = false;
  }
}
```

- [ ] **Step 4: Run the tests and check the flow**

Run: `python3 tests/test_ui_server.py && python3 tools/lint.py`, then the `node --check` snippet.
Expected: ALL PASS, lint clean, `js syntax ok`.

Write a mirrored demo recording (no layout matches it): `python3 tools/make-demo-recording.py --out runtime/solo-pov/telemetry-recordings --mirror --laps 3 --start <an hour before the first demo>`. In the view, select it. Expected: `track unknown` with the Set track row. Pick a layout and confirm. Expected: the laps reload with that track, the row disappears. The learned row now lives in `runtime/gt7/learned-tracks.json`; delete the file and the mirrored recording after the check (`racecast --profile solo-pov telemetry delete <name>`).

- [ ] **Step 5: Commit**

```bash
git add src/ui/control-center.html tests/test_ui_server.py
git commit -m "feat(ui): Set track learns an unknown layout from a recording (#788)"
```

---

### Task 10: Visual verification, wiki screenshot, docs

**Files:**
- Create: `src/docs/wiki/images/cc-telemetry.png`
- Modify: `src/docs/wiki/Control-Center.md` (new section between `### Post-Event Report`, line 233, and `### Help & Docs`, line 250)
- Modify: `src/ui/CLAUDE.md` (after the "General Settings" bullet, lines 21-33)
- Modify: `tools/CLAUDE.md` (the maintainer-tool command list next to `python3 tools/gt7-telemetry-probe.py`, line 170)
- Modify: `.claude/skills/wiki-screenshots/SKILL.md` (the `cc-<view>.png` list in the scope table)

**Interfaces:**
- Consumes: Tasks 5 to 9.

- [ ] **Step 1: Demo data**

The demo tool reads the downloaded racing lines: if `runtime/gt7/signatures.json` is missing, run `python3 src/racecast.py gt7-data update` once. If `profiles/solo-pov/` is missing (it is not tracked in git): `python3 src/racecast.py profile new solo-pov --kind solo --template pov`.

```bash
python3 tools/make-demo-recording.py --out runtime/solo-pov/telemetry-recordings --track Suzuka --laps 6
python3 tools/make-demo-recording.py --out runtime/solo-pov/telemetry-recordings --track Suzuka --laps 4 \
    --start "$(python3 -c 'import time; print(time.time() - 3 * 3600)')"
```

Two recordings on the same layout and car, so lap A comes from the pool across recordings.

- [ ] **Step 2: Visual verification**

Invoke the `ui-visual-verification` skill. Run the dev build: `RACECAST_UI_PORT=8090 python3 src/racecast.py --profile solo-pov ui --no-browser` (never `profile use`). There is no hash routing: click the Telemetry nav item or run `showView('telemetry')` through Playwright. Check at 1600x1000, 1200x900 and 800x900:
- the nav item shows for `solo-pov` and is absent for `--profile demo` (endurance);
- the recordings list (newest first), the laps with time, status, car and track, the selected lap highlighted;
- pickers: A is the fastest counted lap, B the selected one; switching either redraws everything;
- charts: six panels, both colours, steering present, gear as steps, delta shading, cursor and readouts aligned across panels;
- map: not mirrored against the real Suzuka layout, mini-sectors green and red, dots follow the cursor;
- sector table: A, B, signed gap coloured, best column, footer lap times and theoretical best;
- no horizontal page scroll at 800 px, text never overlaps in the chart labels;
- keyboard: Tab reaches the recording and lap buttons and both pickers, focus rings visible.
Fix what the check finds before the screenshot.

- [ ] **Step 3: Screenshot**

Invoke the `wiki-screenshots` skill (Part A, Control Center). Viewport 1600x1000, demo recordings from Step 1, lap B a lap with mixed green and red sectors, the pointer resting at about 40 % of the chart so the cursor and map dots show. Element screenshot of `.view[data-view="telemetry"]`, saved to `src/docs/wiki/images/cc-telemetry.png`. Run the UI from `src/` with no `VERSION` file (dev build), as every `cc-*.png` is captured.

- [ ] **Step 4: Docs**

`src/docs/wiki/Control-Center.md`, before `### Help & Docs`:

```markdown
### Telemetry

![Control Center: Telemetry lap comparison](images/cc-telemetry.png)

Solo POV profiles only. The view reads the profile's GT7 telemetry recordings
(`runtime/<profile>/telemetry-recordings/`, see [Relay mode](Relay-Mode)) and needs no
running relay.

- **Recordings and laps.** Pick a recording, newest first, to list its laps with time,
  status (reference, counted, or not counted with the reason), car and track. The first
  open replays the recording once and caches the result next to it as
  `<stem>.laps.json`; later opens are instant until the recording or the GT7 track data
  changes. A recording the relay is still writing is marked as recording.
- **Lap A and lap B.** Lap B is the lap you click. Lap A starts as the fastest counted
  lap with the same track and car across all recordings of the profile. Both pickers
  list every comparable lap. Laps on an unknown track compare only within their GT7
  session.
- **Charts.** Speed, throttle, brake, steering, gear and the delta of B against A over
  lap distance. Below zero B is ahead, above zero behind. Hover to read both laps at one
  point; the map shows where that point is.
- **Track map and mini-sectors.** Lap A is the thin line; lap B is split into 200 m
  mini-sectors, green where B is faster and red where it is slower. The table lists every
  mini-sector for A and B, the gap and the best time of any comparable lap; its last row
  adds the theoretical best, the sum of the best mini-sectors.
- **Set track.** When racecast does not recognise the layout, or two layouts fit, pick it
  from the list. racecast assigns the recording to it and learns the layout from the
  recording's longest counted lap, so later recordings on it are recognised. Until
  racecast has downloaded the racing lines (`racecast gt7-data update`), every recording
  starts as track unknown.

> **CLI alternative:** `racecast telemetry export <name>` writes the same laps as CSV.
```

`src/ui/CLAUDE.md`, a new bullet after the General Settings bullet:

```markdown
- **Telemetry** (solo POV only: the nav item and the view carry `pov-only`): lap
  comparison from the profile's GT7 recordings. `src/scripts/gt7_laps.py` builds a lap
  index per recording (5 m traces, 200 m sectors), cached as `<stem>.laps.json` and
  rebuilt when the recording's size/mtime or `gt7_data.data_version` change; the data
  layer also memoises up to 64 index summaries per process (`_telemetry_index`). Data
  functions `telemetry_*_data` in `src/racecast.py`; routes `/api/telemetry/recordings`,
  `/api/telemetry/laps`, `/api/telemetry/lap`, `/api/telemetry/tracks`,
  `/api/telemetry/learn`. Charts and map are inline SVG in `control-center.html` (block
  "Telemetry view"). Demo data for screenshots: `tools/make-demo-recording.py`. Tests:
  `tests/test_gt7_laps.py`, `tests/test_racecast.py`, `tests/test_ui_server.py`.
```

`tools/CLAUDE.md`, next to the `gt7-telemetry-probe.py` line:

```
python3 tools/make-demo-recording.py --out runtime/solo-pov/telemetry-recordings   # synthetic GT7 recording for the Telemetry view (needs runtime/gt7/signatures.json; --mirror: unknown track)
```

`.claude/skills/wiki-screenshots/SKILL.md`: add `telemetry` to the `cc-<view>.png` list in the scope table, and below the table one line: "`cc-telemetry.png` needs a solo POV profile with recordings and the downloaded racing lines: `racecast gt7-data update` once, then `python3 tools/make-demo-recording.py --out runtime/solo-pov/telemetry-recordings` and `--profile solo-pov ui`."

- [ ] **Step 5: Commit**

```bash
git add src/docs/wiki/images/cc-telemetry.png src/docs/wiki/Control-Center.md src/ui/CLAUDE.md tools/CLAUDE.md .claude/skills/wiki-screenshots/SKILL.md
git commit -m "docs: Telemetry view in the wiki with cc-telemetry.png (#788)"
```

---

### Task 11: Final gates and PR

**Files:** none new.

- [ ] **Step 1: Targeted checks**

Run: `python3 tests/test_gt7_laps.py && python3 tests/test_gt7_recording.py && python3 tests/test_gt7_data.py && python3 tests/test_make_demo_recording.py && python3 tests/test_racecast.py && python3 tests/test_ui_server.py && python3 tools/lint.py`
Expected: ALL PASS, lint clean.

- [ ] **Step 2: Integrate the current base**

Per the repository's finalisation rules, before the expensive build:

Part 3 is a sub-PR of epic #785: it targets `epic/785-gt7-telemetry`, not `main`.

```bash
git fetch origin && git rebase origin/epic/785-gt7-telemetry
```

Resolve conflicts in `control-center.html`, `ui_server.py` and `racecast.py` by keeping both sides; re-run Step 1 after a conflict.

- [ ] **Step 3: Full gates**

```bash
python3 tools/lint.py
python3 tools/run-tests.py
python3 tools/build.py
```

Expected: lint clean, every test file passes, build verify passes (no secrets, no shell scripts, `tools/make-demo-recording.py` is not shipped).

- [ ] **Step 4: Clean the demo data**

Remove the demo recordings from `runtime/solo-pov/telemetry-recordings/` (`racecast --profile solo-pov telemetry delete <name>` per recording); `runtime/` is never committed.

- [ ] **Step 5: PR**

Open one PR for #788 with the `ship-feature` skill, with `--base epic/785-gt7-telemetry` (self-review with `pr-review`). Title: `feat(telemetry): lap index, mini-sectors and lap comparison in the Control Center`. Body: what the view does, the five routes, the cache rule, the shared `gt7_recording` helpers and the `data_version` change, `cc-telemetry.png`, then `Part of #785` and `Refs #788` (closing keywords do not act on a non-default base). The epic branch has no protection, so `--auto` would merge before CI: merge (squash) only after every check is green. After the merge, close #788 by hand with a comment that links the PR.

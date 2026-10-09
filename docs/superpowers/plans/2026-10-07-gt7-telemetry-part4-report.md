# GT7 Telemetry Part 4: Telemetry in the Post-Event Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the active profile is solo POV, `racecast report` adds a "Telemetry" section built from the GT7 recordings that overlap the report window: best lap, theoretical best, consistency, fuel per lap, tyre temperatures, a lap-time trend, a mini-sector map of the best lap and the lap table. The CLI summary and the Discord embed carry one best-lap line. Endurance reports do not change.

**Architecture:** A new pure module `src/scripts/report_telemetry.py` turns full lap indexes (part 3's `_telemetry_full_index`) into a JSON-safe block grouped by track and car, with the grouping rule of `gt7_laps.pool`, and renders its two inline SVGs. `report_build.build_report` takes the block as an optional `telemetry` argument and stores it in the report dict; `render_html`, `render_summary_text` and `report_discord_fields` render it when present. `racecast.py` gains `_recordings_in_window` (pure) and `_report_telemetry(frm, to)`, which lists recordings, indexes the overlapping ones and builds the block. It is gated on `_profile_has_telemetry()` and never raises.

**Tech Stack:** Python 3 stdlib only (`statistics`, `html`, `datetime`, `xml.etree` in tests). Tests are runnable stdlib scripts under `tests/` (no pytest).

**Spec:** `docs/superpowers/specs/2026-10-07-gt7-telemetry-recording-design.md`, section "Part 4". Issue #789, part 4 of epic #785. Builds on parts 1 to 3 (#786 to #788), which are merged into `epic/785-gt7-telemetry`; this branch starts from there.

## Global Constraints

- Edit only under `src/`, `tests/`, `tools/`, `docs/`. Never touch `dist/` or `runtime/`.
- Code, comments, docs: English only. No new CLI flags, so no new argparse help; any help text stays ASCII.
- Comments minimal: one reason, one sentence. No investigation history in code.
- Python target is 3.11 (`ruff.toml`): an f-string must not reuse its own quote character inside `{...}`.
- The report stays self-contained: inline CSS and SVG only, no JS, no external references. Everything that enters markup goes through `html.escape` (`_esc` and `_table` in `report_build`). The SVGs must parse with `xml.etree.ElementTree.fromstring`.
- Telemetry must never fail the report: `_report_telemetry` catches everything, prints one `note:` line and returns None. A single broken recording is skipped, the others still count. Error texts in notes go through part 3's `_telemetry_reason`, so no machine path reaches the output.
- Interfaces from parts 1 to 3 are used as they are, never redesigned:
  - `gt7_recording.list_recordings(rec_dir) -> [{name, path, size, started, duration_s, laps, partial}]`; `started` is the header's ISO 8601 string with offset, `duration_s` is the file's mtime minus `started` (for an open file, its last flush), `laps` is None without `count_laps=True` (never read it here). The file the relay is writing is named `<stem>.gt7rec.part` and has `partial: True`; a finished one is `<stem>.gt7rec`.
  - `gt7_laps.index(path, track_db, cars, runtime_base, key=None, bundled=None) -> {"rec", "name", "started", "start_ts", "end_ts", "dropped", "track", "sessions", "laps", ...}`; each lap has `rec, session, lap, start_t_s, end_t_s, gt7_time_s, relay_time_s, time_s, status, reason, fuel_used_l, top_speed_kmh, car_id, car, track_id, track, layout, distance_m, tyre_avg_c, points, sectors, trace`. `time_s` is GT7's lap time when it arrived, else the relay's. `track` and `layout` come from the session's TrackDB row (both `""` when `track_id` is None). `tyre_avg_c` is `[0.0, 0.0, 0.0, 0.0]` for a lap without a finite tyre sample. `sectors` are the lap's 200 m sector times, cut at `lap_length_m`, so a counted lap's sectors add up to its time; an entry is None where the trace does not cover the sector. `trace` points carry `d, t, speed_kmh, throttle, brake, steer_deg, gear, x, z` every 5 m; a counted lap's trace ends with one more point at the full lap length, which is not on the 5 m grid. The track is decided per GT7 session, so each lap carries its own `track_id`; the recording-level `track` field is not used here.
  - `gt7_laps.COUNTED` (`("reference", "counted")`), `gt7_laps.SECTOR_M` (`200.0`), `gt7_laps.sectors(trace, length_m, step_m=SECTOR_M)`, `gt7_laps.lap_length_m(lap)`, `gt7_laps.best_sectors(laps)` (prefers `lap["sectors"]`), `gt7_laps.theoretical_best(laps)`. `gt7_laps.pool` pools a known track across recordings and an unknown track only within `(rec, session)`.
  - Part 3 helpers in `racecast.py`: `_telemetry_dbs() -> (TrackDB, CarDB)`; `_telemetry_full_index(path, dbs=None)` (laps with `trace` and `points`, read from the lap-index cache file `<stem>.laps.json`, built when missing or stale, with the learned-assignment key `"<profile>/<stem>"` and the bundled dir); `_telemetry_reason(exc, rec=None)` (an error text without machine paths); `_telemetry_rec_dir()`; `_profile_has_telemetry()`. The report reads every name from the index, so it names cars and tracks exactly as the Control Center does.
- Decisions the tests pin:
  - A lap is **counted** when `status` is in `gt7_laps.COUNTED`. A reference lap is a valid lap that set a new best, so it belongs in every figure.
  - Lap time = the index's `time_s`. Track label = `track - layout` as the Control Center's `tmLapTrack` builds it, `track` alone without a layout, `Unknown track` without a track.
  - A lap belongs to the report when its absolute start `start_ts + start_t_s` lies inside the window `[from_ts, to_ts]`.
  - Figures are computed per group over the counted laps inside the window. The group key is `(track_id, car_id)` on a known track and `(None, car_id, rec, session)` on an unknown one, the rule `gt7_laps.pool` uses. Groups are ordered by counted laps (most first), then by best lap time. The summary line names the first group.
  - Consistency = `statistics.pstdev` of the counted lap times; None with fewer than two counted laps (one lap has no spread, 0.0 would read as perfect).
  - Theoretical best and best sectors use the `sectors` of the group's counted laps. The best lap's mini-sectors are its own `sectors`.
  - Average tyre temperature skips laps whose `tyre_avg_c` is all zero.
  - Mini-sector colour: gap = best-lap sector time minus best sector time (floored at 0), linear in RGB from `#2e7d32` (gap 0) to `#c62828` (the largest gap of that lap); all gaps 0 gives all green; a sector without a time on either side is grey (`#bdbdbd`).
  - Trend y-scale spans the counted laps plus 10 % margin (1 s when they are equal); a slower or faster not-counted lap is clamped to the edge and drawn grey, so one pit lap does not flatten the counted dots.
  - Map: GT7 `x` to the right, `z` downward, uniform scale, no flip. This is the transform of the Control Center map (`tmRenderMap`).
  - The lap table and the trend tooltip show the driving-order number `n` next to GT7's lap number, because a report window can span several sessions and lap numbers repeat.
  - The report indexes every overlapping recording, including the one the relay is still writing. `event stop` builds the report before the teardown, so skipping it would leave the automatic report without telemetry. That costs one cold index of the open file per report build (about 16 s per 3 h of recording). The block is then `partial` and the report says its last lap may be missing. This holds for every report build (`racecast report`, `event stop`, the Control Center Report view); the Control Center Telemetry view keeps skipping the open file, because it would rebuild it on every request. Do not align the two.
- Run single test files with `python3 tests/<file>.py`; one function with `python3 -c "import sys; sys.path.insert(0,'tests'); import <mod> as t; t.<fn>()"`. Lint with `python3 tools/lint.py` after every Python change.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/scripts/report_telemetry.py` | create | `fmt_lap`, `track_label`, `telemetry_block`, `summary_line`, `gap_color`, `svg_lap_trend`, `svg_track_map` |
| `src/scripts/report_build.py` | modify | `build_report(..., telemetry=None)`, `_telemetry_html`, section in `render_html`, line in `render_summary_text`, field in `report_discord_fields`, two CSS rules |
| `src/racecast.py` | modify | `_recordings_in_window`, `_report_telemetry`; pass `telemetry=` in `_build_report_file` |
| `tools/build-binary.py` | modify | hidden import `report_telemetry` |
| `tests/test_report_telemetry.py` | create | block figures, groups, window, summary line, map colours, SVG well-formed |
| `tests/test_report_build.py` | modify | section rendering, unknown-track heading, absence, summary line, Discord field, partial caveat |
| `tests/test_report.py` | modify | window filter, solo-only gate, broken recording skipped and open recording read, wiring in `_build_report_file` |
| `src/docs/wiki/Health-Monitor.md` | modify | telemetry paragraph in "Post-Event Report" |

No `cc-report.png` refresh: the Control Center Report view is captured from the endurance demo profile, which never gets the section, and the view itself does not change.

---

### Task 1: Lap figures and groups (`report_telemetry.telemetry_block`)

**Files:**
- Create: `src/scripts/report_telemetry.py`
- Create: `tests/test_report_telemetry.py`

**Interfaces:**
- Consumes: `gt7_laps.COUNTED`, `gt7_laps.theoretical_best(laps) -> float | None`; in tests also `gt7_laps.sectors` and `gt7_laps.lap_length_m` (part 3).
- Produces (all in `report_telemetry`):
  - `fmt_lap(seconds) -> str`: `118.432 -> "1:58.432"`, None -> `"—"` (the report's empty-cell mark).
  - `track_label(lap) -> str`: `"Suzuka Circuit - Full Course"`, `"Nürburgring"` without a layout, `"Unknown track"` without a track.
  - `telemetry_block(indexes, window) -> dict | None`. `indexes` are full lap indexes, each optionally with `"partial": bool`; `window = (from_ts, to_ts)`. Returns None when no lap starts inside the window, else:
    `{"laps": [row...], "laps_total": int, "laps_counted": int, "partial": bool, "groups": [group...]}`;
    row = `{"n", "ts", "rec", "session", "lap", "time_s", "status", "reason", "counted", "fuel_l", "top_speed_kmh", "car", "track"}` in driving order (`n` from 1);
    group = `{"track_id", "car_id", "rec", "session", "track", "track_name", "car", "laps_total", "laps_counted", "best_s", "best_lap": {"n", "session", "lap", "rec"} | None, "theoretical_s", "consistency_s", "fuel_per_lap_l", "tyre_avg_c": [fl, fr, rl, rr] | None, "trend": [{"n", "lap", "time_s", "counted", "best"}]}`; `rec` and `session` are set only for an unknown-track group, `track_name` is the bare track (`""` when unknown). Task 2 adds `"map"`.
  - `summary_line(block) -> str`: `"Best lap 1:58.432 (theoretical 1:57.910), 23 laps, Suzuka Circuit"`; the lap count is the first group's counted laps.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_report_telemetry.py`:

```python
#!/usr/bin/env python3
"""Telemetry block of the post-event report: figures, groups, mini-sector map, SVG.
Run: python3 tests/test_report_telemetry.py"""
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))

import gt7_laps
import report_telemetry as rtel

LENGTH = 2000.0
RADIUS = LENGTH / (2 * math.pi)
REC = "20261007-200000"
WINDOW = (1000.0, 2000.0)


def _trace(first_mps, second_mps):
    """A lap on a 2000 m circle, one speed per half, a point every 5 m."""
    pts, t = [], 0.0
    for i in range(int(LENGTH // 5) + 1):
        d = i * 5.0
        a = d / RADIUS
        v = first_mps if d < LENGTH / 2 else second_mps
        pts.append({"d": d, "t": t, "speed_kmh": v * 3.6, "throttle": 100, "brake": 0,
                    "steer_deg": 0.0, "gear": 5, "x": RADIUS * math.cos(a),
                    "z": RADIUS * math.sin(a)})
        t += 5.0 / v
    return pts


def _lap(n, start, trace=None, status="counted", reason="", fuel=2.0,
         tyres=(80.0, 81.0, 70.0, 71.0), track_id=101, car_id=3424,
         car="Porsche 911 RSR", relay=None, rec=REC, session=1):
    """One lap in the shape gt7_laps.index writes."""
    trace = trace or []
    gt7 = round(trace[-1]["t"], 3) if trace else None
    relay = relay if relay is not None else (gt7 or 0.0) + 0.02
    time_s = gt7 if gt7 is not None else relay
    known = track_id is not None
    return {"rec": rec, "session": session, "lap": n, "start_t_s": start,
            "end_t_s": start + time_s, "gt7_time_s": gt7, "relay_time_s": relay,
            "time_s": time_s, "status": status, "reason": reason, "fuel_used_l": fuel,
            "top_speed_kmh": 180.0, "car_id": car_id, "car": car, "track_id": track_id,
            "track": "Suzuka Circuit" if known else "",
            "layout": "Full Course" if known else "",
            "distance_m": trace[-1]["d"] if trace else 0.0, "tyre_avg_c": list(tyres),
            "points": [[p["x"], p["z"]] for p in trace],
            "sectors": gt7_laps.sectors(trace, gt7_laps.lap_length_m({"trace": trace})),
            "trace": trace}


def session_index():
    """Three counted laps and a pit lap. Lap 3 is the best (43.810 s); laps 1 and 2
    hold the fast halves, so the theoretical best is 40.000 s."""
    laps = [_lap(1, 10.0, _trace(50.0, 40.0), status="reference", fuel=2.0,
                 tyres=(80.0, 81.0, 70.0, 71.0)),
            _lap(2, 55.0, _trace(40.0, 50.0), fuel=2.2, tyres=(82.0, 83.0, 72.0, 73.0)),
            _lap(3, 100.0, _trace(50.0, 42.0), status="reference", fuel=2.1,
                 tyres=(84.0, 85.0, 74.0, 75.0)),
            _lap(4, 143.81, status="not counted", reason="pit", fuel=0.5,
                 tyres=(60.0, 60.0, 60.0, 60.0), relay=95.0)]
    return {"rec": REC, "started": "2026-10-07T20:00:00+02:00",
            "start_ts": 1000.0, "end_ts": 1240.0, "track": None, "laps": laps}


def block():
    return rtel.telemetry_block([session_index()], WINDOW)


def t_fmt_lap():
    assert rtel.fmt_lap(118.432) == "1:58.432"
    assert rtel.fmt_lap(43.81) == "0:43.810"
    assert rtel.fmt_lap(59.9996) == "1:00.000", "rounding to the millisecond carries into the minute"
    assert rtel.fmt_lap(None) == "—"


def t_track_label_as_in_the_control_center():
    assert rtel.track_label({"track": "Suzuka Circuit", "layout": "Full Course"}) == \
        "Suzuka Circuit - Full Course"
    assert rtel.track_label({"track": "Nürburgring", "layout": ""}) == "Nürburgring"
    assert rtel.track_label({"track": "", "layout": ""}) == "Unknown track"


def t_block_figures():
    b = block()
    assert b["laps_total"] == 4 and b["laps_counted"] == 3, b
    assert len(b["groups"]) == 1
    g = b["groups"][0]
    assert abs(g["best_s"] - 43.81) < 1e-9, g["best_s"]
    assert g["best_lap"]["lap"] == 3 and g["best_lap"]["n"] == 3, g["best_lap"]
    assert abs(g["theoretical_s"] - 40.0) < 1e-6, \
        "the theoretical best joins lap 1's first half and lap 2's second half"
    assert abs(g["consistency_s"] - statistics.pstdev([45.0, 45.0, 43.81])) < 1e-9, \
        g["consistency_s"]
    assert abs(g["fuel_per_lap_l"] - 2.1) < 1e-9, "the pit lap's fuel stays out"
    assert g["tyre_avg_c"] == [82.0, 83.0, 72.0, 73.0], g["tyre_avg_c"]
    assert g["track"] == "Suzuka Circuit - Full Course" and g["track_name"] == "Suzuka Circuit"
    assert g["car"] == "Porsche 911 RSR"
    assert [p["best"] for p in g["trend"]] == [False, False, True, False]


def t_tyre_average_skips_laps_without_tyre_data():
    idx = session_index()
    idx["laps"][1]["tyre_avg_c"] = [0.0, 0.0, 0.0, 0.0]
    g = rtel.telemetry_block([idx], WINDOW)["groups"][0]
    assert g["tyre_avg_c"] == [82.0, 83.0, 72.0, 73.0], \
        f"a lap without a tyre sample must not count as 0 °C: {g['tyre_avg_c']}"


def t_lap_rows_in_driving_order():
    rows = block()["laps"]
    assert [(r["n"], r["lap"]) for r in rows] == [(1, 1), (2, 2), (3, 3), (4, 4)]
    assert rows[0]["ts"] == 1010.0 and rows[0]["counted"], rows[0]
    pit = rows[3]
    assert (pit["status"], pit["reason"], pit["time_s"], pit["counted"]) == \
        ("not counted", "pit", 95.0, False), pit
    assert rows[0]["track"] == "Suzuka Circuit - Full Course"


def t_consistency_needs_two_counted_laps():
    idx = session_index()
    idx["laps"] = idx["laps"][2:]
    g = rtel.telemetry_block([idx], WINDOW)["groups"][0]
    assert g["laps_counted"] == 1 and g["consistency_s"] is None, "one lap has no spread"


def t_laps_outside_the_window_are_ignored():
    assert rtel.telemetry_block([session_index()], (5000.0, 6000.0)) is None
    assert rtel.telemetry_block([], WINDOW) is None
    b = rtel.telemetry_block([session_index()], (1050.0, 2000.0))
    assert [r["lap"] for r in b["laps"]] == [2, 3, 4], \
        "a lap is in the report when it starts inside the window"


def t_groups_by_track_and_car_most_counted_first():
    other = {"rec": "20261007-210000", "started": "2026-10-07T20:08:20+02:00",
             "start_ts": 1500.0, "end_ts": 1600.0, "track": None,
             "laps": [_lap(1, 5.0, _trace(45.0, 45.0), track_id=None, car_id=1234,
                           car="Mazda Roadster", rec="20261007-210000")]}
    b = rtel.telemetry_block([other, session_index()], WINDOW)
    assert [g["car"] for g in b["groups"]] == ["Porsche 911 RSR", "Mazda Roadster"]
    unknown = b["groups"][1]
    assert (unknown["track"], unknown["track_name"]) == ("Unknown track", "")
    assert (unknown["rec"], unknown["session"]) == ("20261007-210000", 1)
    assert (b["groups"][0]["rec"], b["groups"][0]["session"]) == (None, None), \
        "a known track groups across recordings"
    assert b["laps"][-1]["car"] == "Mazda Roadster", "rows stay chronological across recordings"


def t_unknown_track_groups_per_session():
    idx = session_index()
    idx["laps"] = [_lap(1, 10.0, _trace(50.0, 40.0), track_id=None, session=1),
                   _lap(1, 100.0, _trace(40.0, 50.0), track_id=None, session=2)]
    groups = rtel.telemetry_block([idx], WINDOW)["groups"]
    assert [g["session"] for g in groups] == [1, 2], groups
    assert all(abs(g["theoretical_s"] - 45.0) < 1e-6 for g in groups), \
        "two GT7 sessions on an unknown track may be two circuits and share no theoretical best"


def t_summary_line():
    assert rtel.summary_line(block()) == \
        "Best lap 0:43.810 (theoretical 0:40.000), 3 laps, Suzuka Circuit"
    idx = session_index()
    idx["laps"] = idx["laps"][3:]
    assert rtel.summary_line(rtel.telemetry_block([idx], WINDOW)) == \
        "1 lap, none counted, Suzuka Circuit"


def t_partial_flag_from_an_open_recording():
    assert rtel.telemetry_block([dict(session_index(), partial=True)], WINDOW)["partial"] is True
    assert block()["partial"] is False


def run():
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")


if __name__ == "__main__":
    run()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_report_telemetry.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'report_telemetry'`.

- [ ] **Step 3: Implement**

Create `src/scripts/report_telemetry.py`:

```python
#!/usr/bin/env python3
"""Telemetry section of the post-event report, from GT7 lap indexes (stdlib only, no I/O).

Input is the lap index (gt7_laps.index) of every recording that overlaps the report
window; output is a JSON-safe block that report_build renders. Figures are per track
and car and use counted laps only (the relay's verdicts, as on the HUD).
"""
import statistics

import gt7_laps


def fmt_lap(seconds):
    """118.432 -> '1:58.432'; None -> the report's empty-cell mark."""
    if seconds is None:
        return "—"
    m, ms = divmod(int(round(float(seconds) * 1000)), 60000)
    return f"{m}:{ms // 1000:02d}.{ms % 1000:03d}"


def track_label(lap):
    """'Track - Layout' as the Control Center names a lap's track, 'Unknown track' without one."""
    track = lap.get("track") or ""
    if not track:
        return "Unknown track"
    layout = lap.get("layout") or ""
    return f"{track} - {layout}" if layout else track


def _count(n):
    return f"{n} lap" if n == 1 else f"{n} laps"


def _key(lap):
    """A known track groups across recordings, an unknown one only within its recording
    and GT7 session, as gt7_laps.pool pools them."""
    if lap.get("track_id") is not None:
        return (lap["track_id"], lap.get("car_id"))
    return (None, lap.get("car_id"), lap.get("rec"), lap.get("session"))


def _row(n, ts, lap):
    return {"n": n, "ts": ts, "rec": lap.get("rec") or "", "session": lap.get("session"),
            "lap": lap.get("lap"), "time_s": lap.get("time_s"),
            "status": lap.get("status") or "", "reason": lap.get("reason") or "",
            "counted": lap.get("status") in gt7_laps.COUNTED,
            "fuel_l": lap.get("fuel_used_l"), "top_speed_kmh": lap.get("top_speed_kmh"),
            "car": lap.get("car") or "", "track": track_label(lap)}


def _group(key, members):
    track_id, car_id, *where = key
    rec, session = where or (None, None)
    first = members[0][1]
    valid = [(row, lap) for row, lap in members if row["counted"] and row["time_s"] is not None]
    best_row, best = min(valid, key=lambda m: m[0]["time_s"]) if valid else (None, None)
    times = [row["time_s"] for row, _lap in valid]
    timed = [lap for _row, lap in valid if lap.get("sectors")]
    fuel = [lap["fuel_used_l"] for _row, lap in valid if lap.get("fuel_used_l") is not None]
    tyres = [lap["tyre_avg_c"] for _row, lap in valid
             if len(lap.get("tyre_avg_c") or []) == 4 and any(lap["tyre_avg_c"])]
    return {
        "track_id": track_id, "car_id": car_id, "rec": rec, "session": session,
        "track": track_label(first), "track_name": first.get("track") or "",
        "car": members[0][0]["car"],
        "laps_total": len(members), "laps_counted": len(valid),
        "best_s": best_row["time_s"] if best_row else None,
        "best_lap": ({"n": best_row["n"], "session": best_row["session"],
                      "lap": best_row["lap"], "rec": best_row["rec"]} if best_row else None),
        "theoretical_s": gt7_laps.theoretical_best(timed) if timed else None,
        "consistency_s": statistics.pstdev(times) if len(times) >= 2 else None,
        "fuel_per_lap_l": statistics.fmean(fuel) if fuel else None,
        "tyre_avg_c": ([statistics.fmean(t[i] for t in tyres) for i in range(4)]
                       if tyres else None),
        "trend": [{"n": row["n"], "lap": row["lap"], "time_s": row["time_s"],
                   "counted": row["counted"], "best": row is best_row}
                  for row, _lap in members],
    }


def telemetry_block(indexes, window):
    """The report's telemetry block for the laps that start inside window=(from_ts,
    to_ts), or None when there are none."""
    frm, to = window
    picked, partial = [], False
    for idx in indexes:
        found = False
        for lap in idx.get("laps") or []:
            ts = (idx.get("start_ts") or 0.0) + (lap.get("start_t_s") or 0.0)
            if frm <= ts <= to:
                picked.append((ts, lap))
                found = True
        partial = partial or (found and bool(idx.get("partial")))
    if not picked:
        return None
    picked.sort(key=lambda p: p[0])
    rows = [_row(i + 1, ts, lap) for i, (ts, lap) in enumerate(picked)]
    by_key = {}
    for row, (_ts, lap) in zip(rows, picked, strict=True):
        by_key.setdefault(_key(lap), []).append((row, lap))
    groups = [_group(key, members) for key, members in by_key.items()]
    groups.sort(key=lambda g: (-g["laps_counted"],
                               g["best_s"] if g["best_s"] is not None else float("inf")))
    return {"laps": rows, "laps_total": len(rows),
            "laps_counted": sum(1 for r in rows if r["counted"]),
            "partial": partial, "groups": groups}


def summary_line(block):
    """One line for the CLI summary and Discord: the track and car with the most counted laps."""
    g = block["groups"][0]
    if g["best_s"] is None:
        parts = [f"{_count(g['laps_total'])}, none counted"]
    else:
        theo = (f" (theoretical {fmt_lap(g['theoretical_s'])})"
                if g["theoretical_s"] is not None else "")
        parts = [f"Best lap {fmt_lap(g['best_s'])}{theo}", _count(g["laps_counted"])]
    if g["track_name"]:
        parts.append(g["track_name"])
    return ", ".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_report_telemetry.py && python3 tools/lint.py`
Expected: ALL PASS, lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/scripts/report_telemetry.py tests/test_report_telemetry.py
git commit -m "feat(report): telemetry figures per track and car from GT7 lap indexes (#789)"
```

---

### Task 2: Mini-sector map and inline SVG

**Files:**
- Modify: `src/scripts/report_telemetry.py` (`_group`, new functions after `_row`, SVG renderers at the end)
- Modify: `tests/test_report_telemetry.py`

**Interfaces:**
- Consumes: `gt7_laps.best_sectors(laps) -> [float | None]`, `gt7_laps.SECTOR_M` (part 3); the laps' own `sectors` from the index.
- Produces (all in `report_telemetry`):
  - `GAP_GREEN = (0x2E, 0x7D, 0x32)`, `GAP_RED = (0xC6, 0x28, 0x28)`, `GREY = "#bdbdbd"`.
  - `gap_color(gap_s, worst_s) -> "#rrggbb"`, linear from green (0) to red (`worst_s`).
  - group key `"map"`: `{"sectors": [{"points": [[x, z], ...], "gap_s": float | None, "color": str}], "worst_gap_s": float}` or None (no best lap, no sector times, or a trace with fewer than 2 points).
  - `svg_lap_trend(trend) -> str` (`""` for no timed lap), `svg_track_map(track_map) -> str` (`""` for None). Both return one `<svg>` element that parses as XML. The trend tooltip reads `#<n> (lap <lap>): <time>`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_report_telemetry.py` (imports at the top: `import xml.etree.ElementTree as ET`), before `def run():`:

```python
def t_gap_color_scale():
    assert rtel.gap_color(0.0, 0.5) == "#2e7d32"
    assert rtel.gap_color(0.5, 0.5) == "#c62828"
    assert rtel.gap_color(0.25, 0.5) == "#7a532d", "halfway is the RGB midpoint, rounded half up"
    assert rtel.gap_color(0.3, 0.0) == "#2e7d32", "a lap without any loss is all green"


def t_map_colours_sectors_by_gap_to_best():
    m = block()["groups"][0]["map"]
    assert len(m["sectors"]) == 10, len(m["sectors"])
    assert abs(m["worst_gap_s"] - (round(200 / 42, 3) - 4.0)) < 1e-9, m["worst_gap_s"]
    assert [s["color"] for s in m["sectors"][:5]] == ["#2e7d32"] * 5, "no loss is green"
    assert [s["color"] for s in m["sectors"][5:]] == ["#c62828"] * 5, "the largest loss is red"
    assert all(len(s["points"]) >= 2 for s in m["sectors"])


def t_map_greys_a_sector_without_time():
    idx = session_index()
    idx["laps"][2]["sectors"][0] = None
    m = rtel.telemetry_block([idx], WINDOW)["groups"][0]["map"]
    assert (m["sectors"][0]["gap_s"], m["sectors"][0]["color"]) == (None, "#bdbdbd"), m["sectors"][0]
    assert m["sectors"][9]["color"] == "#c62828", "the other sectors keep their colours"


def t_map_absent_when_the_best_lap_has_no_trace():
    idx = session_index()
    idx["laps"][2]["trace"] = []
    g = rtel.telemetry_block([idx], WINDOW)["groups"][0]
    assert g["map"] is None, "the map needs the best lap's x/z"
    assert abs(g["theoretical_s"] - 40.0) < 1e-6, "the sector times still give the theoretical best"


def t_svgs_are_well_formed_xml():
    g = block()["groups"][0]
    trend = ET.fromstring(rtel.svg_lap_trend(g["trend"]))
    tmap = ET.fromstring(rtel.svg_track_map(g["map"]))
    assert trend.tag == "svg" and tmap.tag == "svg"
    fills = [c.get("fill") for c in trend.iter("circle")]
    assert fills.count("#bdbdbd") == 1, "the pit lap is greyed"
    assert fills.count("#2e7d32") == 1, "the best lap is green"
    assert "#3 (lap 3): 0:43.810" in [t.text for t in trend.iter("title")], \
        "the tooltip names the driving-order number next to GT7's lap number"
    assert len(list(tmap.iter("polyline"))) == 10


def t_trend_pins_a_slow_lap_to_the_top_edge():
    trend = ET.fromstring(rtel.svg_lap_trend(block()["groups"][0]["trend"]))
    circles = list(trend.iter("circle"))
    pit = [c for c in circles if c.get("fill") == "#bdbdbd"][0]
    assert float(pit.get("cy")) == min(float(c.get("cy")) for c in circles), \
        "the 95 s pit lap sits on the top edge instead of stretching the scale"
    assert all(12.0 <= float(c.get("cy")) <= 138.0 for c in circles)


def t_svg_empty_inputs():
    assert rtel.svg_lap_trend([]) == ""
    assert rtel.svg_track_map(None) == ""
```

`round(200 / 42, 3)` because `gt7_laps.sectors` rounds sector times to the millisecond.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_report_telemetry.py`
Expected: FAIL with `AttributeError: module 'report_telemetry' has no attribute 'gap_color'` (alphabetical order runs `t_gap_color_scale` first among the new tests).

- [ ] **Step 3: Implement**

In `src/scripts/report_telemetry.py`, add `import html` above `import statistics`, then after `import gt7_laps`:

```python
GAP_GREEN = (0x2E, 0x7D, 0x32)
GAP_RED = (0xC6, 0x28, 0x28)
GREY = "#bdbdbd"
```

Add after `_row`:

```python
def gap_color(gap_s, worst_s):
    """Linear from green (no loss to the best sector) to red (the lap's largest loss)."""
    f = 0.0 if worst_s <= 0 else min(1.0, max(0.0, gap_s / worst_s))
    r, g, b = (int(a + (c - a) * f + 0.5) for a, c in zip(GAP_GREEN, GAP_RED, strict=True))
    return f"#{r:02x}{g:02x}{b:02x}"


def _sector_map(best, pool):
    """The best lap's mini-sectors with the time each lost to the fastest counted lap;
    a sector without a time on either side has gap None and is drawn grey."""
    trace = best.get("trace") or []
    own = best.get("sectors") or []
    if len(trace) < 2 or not own or not pool:
        return None
    ref = gt7_laps.best_sectors(pool)
    n = min(len(own), len(ref))
    gaps = [max(0.0, own[i] - ref[i]) if own[i] is not None and ref[i] is not None else None
            for i in range(n)]
    worst = max((g for g in gaps if g is not None), default=0.0)
    sectors = []
    for i, gap in enumerate(gaps):
        lo = i * gt7_laps.SECTOR_M
        hi = (i + 1) * gt7_laps.SECTOR_M if i < n - 1 else float("inf")
        pts = [[p["x"], p["z"]] for p in trace if lo <= p["d"] <= hi]
        if len(pts) >= 2:
            color = GREY if gap is None else gap_color(gap, worst)
            sectors.append({"points": pts, "gap_s": gap, "color": color})
    return {"sectors": sectors, "worst_gap_s": worst} if sectors else None
```

In `_group`, add as the last key of the returned dict:

```python
        "map": _sector_map(best, timed) if best is not None else None,
```

Append at the end of the module:

```python
def svg_lap_trend(trend, w=720, h=150):
    """Lap times in driving order: counted laps dark, the best green, the rest grey and
    clamped into the counted laps' range."""
    pts = [p for p in trend if p.get("time_s") is not None]
    if not pts:
        return ""
    left, right, top, bottom = 64, 12, 12, 12
    scale = [p["time_s"] for p in pts if p["counted"]] or [p["time_s"] for p in pts]
    lo, hi = min(scale), max(scale)
    margin = (hi - lo) * 0.1 or 1.0
    lo, hi = lo - margin, hi + margin
    step = (w - left - right) / max(1, len(pts) - 1)
    marks = []
    for i, p in enumerate(pts):
        x = left + i * step if len(pts) > 1 else (left + w - right) / 2
        v = min(hi, max(lo, p["time_s"]))
        y = top + (hi - v) / (hi - lo) * (h - top - bottom)
        if p.get("best"):
            fill, r = "#2e7d32", 5
        elif p["counted"]:
            fill, r = "#1c1e21", 3.5
        else:
            fill, r = GREY, 3.5
        tip = html.escape(f"#{p['n']} (lap {p['lap']}): {fmt_lap(p['time_s'])}")
        marks.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{fill}">'
                     f"<title>{tip}</title></circle>")
    grid = "".join(
        f'<line x1="{left}" y1="{y}" x2="{w - right}" y2="{y}" stroke="#eceef1"/>'
        f'<text x="{left - 6}" y="{y + 4}" font-size="11" text-anchor="end" '
        f'fill="#65676b">{fmt_lap(v)}</text>'
        for y, v in ((top, hi), (h - bottom, lo)))
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" '
            f'aria-label="Lap time trend">{grid}{"".join(marks)}</svg>')


def svg_track_map(track_map, size=360, pad=14):
    """The best lap's line from GT7 x/z (x right, z down), one polyline per mini-sector
    in its gap colour, with a start/finish marker."""
    if not track_map or not track_map.get("sectors"):
        return ""
    pts = [p for s in track_map["sectors"] for p in s["points"]]
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    z0, z1 = min(p[1] for p in pts), max(p[1] for p in pts)
    inner = size - 2 * pad
    k = inner / (max(x1 - x0, z1 - z0) or 1.0)
    ox = pad + (inner - (x1 - x0) * k) / 2
    oz = pad + (inner - (z1 - z0) * k) / 2

    def xy(p):
        return (ox + (p[0] - x0) * k, oz + (p[1] - z0) * k)

    lines = []
    for i, s in enumerate(track_map["sectors"], 1):
        coords = " ".join(f"{x:.1f},{y:.1f}" for x, y in map(xy, s["points"]))
        gap = "no time" if s["gap_s"] is None else f"+{s['gap_s']:.3f} s"
        tip = html.escape(f"Sector {i}: {gap}")
        lines.append(f'<polyline points="{coords}" fill="none" stroke="{s["color"]}" '
                     f'stroke-width="5" stroke-linecap="round" stroke-linejoin="round">'
                     f"<title>{tip}</title></polyline>")
    sx, sy = xy(track_map["sectors"][0]["points"][0])
    marker = (f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="5" fill="#ffffff" stroke="#1c1e21" '
              f'stroke-width="2"><title>Start/finish</title></circle>')
    return (f'<svg viewBox="0 0 {size} {size}" width="100%" role="img" '
            f'aria-label="Track map of the best lap">{"".join(lines)}{marker}</svg>')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_report_telemetry.py && python3 tools/lint.py`
Expected: ALL PASS, lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/scripts/report_telemetry.py tests/test_report_telemetry.py
git commit -m "feat(report): mini-sector map and lap-time trend as inline SVG (#789)"
```

---

### Task 3: Telemetry section in the report

**Files:**
- Modify: `src/scripts/report_build.py` (`build_report` and its returned dict, `_STYLE`, a new `_telemetry_html` after `_fps_cell`, `render_html` after the desync caveat, `render_summary_text`, `report_discord_fields`)
- Modify: `tests/test_report_build.py`

**Interfaces:**
- Consumes: Task 1 and 2 (`report_telemetry.fmt_lap`, `summary_line`, `svg_lap_trend`, `svg_track_map`), `gt7_laps.SECTOR_M`.
- Produces:
  - `build_report(samples, events, name_for_stint, event_title, window, now, host=None, prebuffer_s=..., backlog_warn_s=..., telemetry=None)`; the report dict gains `"telemetry"` (the block or None). Every reader uses `report.get("telemetry")`, so hand-built report dicts in existing tests keep working.
  - `render_html`: `<h2>Telemetry</h2>` section after the on-air block, before "Producer handovers". A group heading reads `<track> · <car>`, and on an unknown track `Unknown track · <car> · <rec>, session <n>`.
  - `render_summary_text`: line `"  " + summary_line(block)` after the uptime line.
  - `report_discord_fields`: extra `("Telemetry", summary_line(block))` when a block exists.

- [ ] **Step 1: Write the failing tests**

In `tests/test_report_build.py`, add below the `rb = _load(...)` line:

```python
import re
import xml.etree.ElementTree as ET

sys.path.insert(0, HERE)
import test_report_telemetry as trt
```

Add before `def run():`:

```python
LINE = "Best lap 0:43.810 (theoretical 0:40.000), 3 laps, Suzuka Circuit"


def _solo_report(*extra, **idx_kw):
    idx = dict(trt.session_index(), **idx_kw)
    tel = trt.rtel.telemetry_block([idx, *extra], trt.WINDOW)
    return rb.build_report([_sample(0.0), _sample(30.0)], [], {}, "Solo", (0.0, 30.0),
                           now=1000.0, telemetry=tel)


def t_telemetry_section_renders_figures_trend_map_and_laps():
    html = rb.render_html(_solo_report())
    assert "<h2>Telemetry</h2>" in html
    for text in ("0:43.810", "0:40.000", "± 0.561 s", "2.10 L", "3 of 4",
                 "Suzuka Circuit - Full Course", "Porsche 911 RSR", "82.0", "pit",
                 "not counted", "200 m mini-sectors", "+0.762 s", "<th>#</th>"):
        assert text in html, text
    svgs = re.findall(r"<svg\b.*?</svg>", html, re.S)
    assert len(svgs) == 3, "health strip, lap trend, track map"
    for svg in svgs:
        ET.fromstring(svg)
    assert html.index("<h2>Telemetry</h2>") < html.index("<h2>Feed reliability</h2>")
    assert "may be missing" not in html


def t_unknown_track_heading_names_recording_and_session():
    other = {"rec": "20261007-210000", "start_ts": 1500.0, "laps": [trt._lap(
        1, 5.0, trt._trace(45.0, 45.0), track_id=None, car_id=1234, car="Mazda Roadster",
        rec="20261007-210000", session=2)]}
    html = rb.render_html(_solo_report(other))
    assert "<h3>Unknown track · Mazda Roadster · 20261007-210000, session 2</h3>" in html, \
        "groups on an unknown track are per GT7 session, so the heading says which"
    assert "<h3>Suzuka Circuit - Full Course · Porsche 911 RSR</h3>" in html


def t_telemetry_section_absent_without_recordings():
    rep = rb.build_report([_sample(0.0), _sample(30.0)], [], {}, "Endurance", (0.0, 30.0),
                          now=1000.0)
    assert rep["telemetry"] is None
    assert "<h2>Telemetry</h2>" not in rb.render_html(rep)
    assert "Best lap" not in rb.render_summary_text(rep)
    assert "Telemetry" not in dict(rb.report_discord_fields(rep))


def t_summary_and_discord_carry_the_best_lap_line():
    rep = _solo_report()
    assert "  " + LINE in rb.render_summary_text(rep).splitlines()
    assert dict(rb.report_discord_fields(rep))["Telemetry"] == LINE


def t_open_recording_adds_a_caveat():
    assert "may be missing" in rb.render_html(_solo_report(partial=True))
```

`+0.762 s` is the worst mini-sector loss of the best lap (4.762 - 4.000 s, sector times rounded to the millisecond), shown in the map note.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_report_build.py`
Expected: FAIL with `TypeError: build_report() got an unexpected keyword argument 'telemetry'`.

- [ ] **Step 3: Implement**

`build_report`: add `telemetry=None` as the last parameter:

```python
def build_report(samples, events, name_for_stint, event_title, window, now,
                 host=None, prebuffer_s=hs.DEFAULT_FEED_PREBUFFER_S,
                 backlog_warn_s=hs.FEED_BACKLOG_WARN_S, telemetry=None):
```

Append one sentence to its docstring: `` `telemetry` is the solo POV block from report_telemetry.telemetry_block, or None.`` In the returned dict, after `"health_bands": health_bands,` add:

```python
        "telemetry": telemetry,
```

`_STYLE`: add before the closing `"""`:

```
h3{font-size:14px;margin:20px 0 8px}
.tele-map{max-width:360px;margin:8px 0}
```

Add after `_fps_cell`, before `def render_html`:

```python
def _telemetry_html(tel):
    """The solo POV telemetry section: figures, trend and map per track and car, then every lap."""
    import gt7_laps
    import report_telemetry as rtel

    parts = ["<h2>Telemetry</h2>",
             f"<p class='note'>{tel['laps_total']} laps from the GT7 telemetry recording, "
             f"{tel['laps_counted']} counted by the relay as on the HUD. The figures use "
             "counted laps only, timed by GT7 where its lap time arrived.</p>"]
    for g in tel["groups"]:
        where = f" · {g['rec']}, session {g['session']}" if g["rec"] else ""
        parts.append(f"<h3>{_esc(g['track'])} · {_esc(g['car'] or 'Unknown car')}"
                     f"{_esc(where)}</h3>")
        cons, fuel = g["consistency_s"], g["fuel_per_lap_l"]
        kpis = [(rtel.fmt_lap(g["best_s"]), "Best lap"),
                (rtel.fmt_lap(g["theoretical_s"]), "Theoretical best"),
                ("—" if cons is None else f"± {cons:.3f} s", "Consistency"),
                ("—" if fuel is None else f"{fuel:.2f} L", "Fuel per lap"),
                (f"{g['laps_counted']} of {g['laps_total']}", "Counted laps")]
        parts.append("<div class='kpis'>" + "".join(
            f"<div class='kpi'><div class='n'>{_esc(n)}</div><div class='l'>{_esc(lbl)}</div></div>"
            for n, lbl in kpis) + "</div>")
        if g["tyre_avg_c"]:
            parts.append(_table(["Average tyre temperature", "FL", "FR", "RL", "RR"],
                                [["°C"] + [f"{v:.1f}" for v in g["tyre_avg_c"]]]))
        trend = rtel.svg_lap_trend(g["trend"])
        if trend:
            parts.append(trend)
            parts.append("<p class='note'>Lap times in driving order: counted laps dark, "
                         "the best lap green, other laps grey and pinned to the edge of "
                         "the scale.</p>")
        tmap = rtel.svg_track_map(g.get("map"))
        if tmap:
            parts.append(f"<div class='tele-map'>{tmap}</div>")
            parts.append(f"<p class='note'>Best lap in {gt7_laps.SECTOR_M:.0f} m mini-sectors, "
                         "coloured by the time it lost to the fastest counted lap in each: "
                         "green no loss, red its largest loss "
                         f"(+{g['map']['worst_gap_s']:.3f} s), linear in between, grey "
                         "without a sector time.</p>")
    if tel["partial"]:
        parts.append("<p class='caveat'>A recording was still open when this report was "
                     "built, so its last lap may be missing.</p>")
    rows = [(r["n"], r["lap"], rtel.fmt_lap(r["time_s"]), r["status"], r["reason"] or "—",
             "—" if r["fuel_l"] is None else f"{r['fuel_l']:.2f}",
             "—" if r["top_speed_kmh"] is None else f"{r['top_speed_kmh']:.0f}",
             r["car"] or "—", r["track"]) for r in tel["laps"]]
    parts.append(_table(["#", "Lap", "Time", "Status", "Reason", "Fuel (L)",
                         "Top speed (km/h)", "Car", "Track"], rows))
    return "".join(parts)
```

`report_build` imports `report_telemetry` inside the three functions that need it, so endurance code paths never load the GT7 modules.

`render_html`: directly after the `if oa.get("desync_seconds", 0) > 0:` caveat block, before `if report["producer_handovers"]:`, add:

```python
    if report.get("telemetry"):
        parts.append(_telemetry_html(report["telemetry"]))
```

`render_summary_text`: after the `lines = [...]` assignment, add:

```python
    if report.get("telemetry"):
        import report_telemetry as rtel
        lines.append(f"  {rtel.summary_line(report['telemetry'])}")
```

`report_discord_fields`: replace the body with:

```python
    hd = report["header"]
    fields = [("Uptime", f"{hd['uptime_pct']}%"),
              ("On air", _fmt_dur(hd.get("on_air_s", hd["duration_s"]))),
              ("Incidents", str(len(report["incidents"]))),
              ("Session length", _fmt_dur(hd["duration_s"])),
              ("Window", f"{_fmt_clock(hd['start'])}–{_fmt_clock(hd['end'])}")]
    if report.get("telemetry"):
        import report_telemetry as rtel
        fields.append(("Telemetry", rtel.summary_line(report["telemetry"])))
    return fields
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_report_build.py && python3 tests/test_report_telemetry.py && python3 tests/test_report.py && python3 tools/lint.py`
Expected: ALL PASS in each (the existing report tests are unchanged), lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/scripts/report_build.py tests/test_report_build.py
git commit -m "feat(report): telemetry section, summary line and Discord field (#789)"
```

---

### Task 4: Collect recordings in `racecast report`

**Files:**
- Modify: `src/racecast.py` (two new functions between `_report_backlog_thresholds` and `_build_report_file`; the `rbuild.build_report(...)` call in `_build_report_file`)
- Modify: `tools/build-binary.py` (hidden import after `"--hidden-import", "gt7_laps",`)
- Modify: `tests/test_report.py`

**Interfaces:**
- Consumes: `gt7_recording.list_recordings`, `report_telemetry.telemetry_block` and, in `racecast.py`, `_profile_has_telemetry`, `_telemetry_rec_dir`, `_telemetry_dbs`, `_telemetry_full_index`, `_telemetry_reason`.
- Produces (in `racecast`):
  - `_recordings_in_window(rows, frm, to) -> list` (pure): rows whose `[started, started + duration_s]` overlaps `[frm, to]`; a row with an unparseable `started` is dropped.
  - `_report_telemetry(frm, to) -> dict | None`: the block, None for a non-solo-POV profile, no overlapping recording, or any failure. Never raises. It indexes every overlapping recording, including the open `.gt7rec.part` the relay is writing, and passes each row's `partial` into the block.
  - `_build_report_file` passes `telemetry=_report_telemetry(frm, to)` to `build_report`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_report.py`, add below `hs = _load(...)`:

```python
import contextlib
import datetime
import io

import gt7_recording
import report_telemetry as rtel

sys.path.insert(0, HERE)
import test_report_telemetry as trt
```

Add before `def run():`:

```python
def t_recordings_in_window_by_start_and_duration():
    t0 = datetime.datetime(2026, 10, 7, 20, 0, tzinfo=datetime.timezone.utc)
    base = t0.timestamp()
    rows = [{"name": "early", "started": t0.isoformat(), "duration_s": 600.0},
            {"name": "late", "started": (t0 + datetime.timedelta(hours=2)).isoformat(),
             "duration_s": 60.0},
            {"name": "bad", "started": "", "duration_s": 5.0}]

    def pick(frm, to):
        return [r["name"] for r in rc._recordings_in_window(rows, frm, to)]

    assert pick(base + 300, base + 900) == ["early"]
    assert pick(base + 7000, base + 7230) == ["late"]
    assert pick(base + 1000, base + 2000) == [], "no recording overlaps a gap between them"


def t_report_telemetry_is_solo_pov_only():
    def boom():
        raise AssertionError("an endurance report must not look for recordings")
    orig = (rc._profile_has_telemetry, rc._telemetry_rec_dir)
    rc._profile_has_telemetry = lambda: False
    rc._telemetry_rec_dir = boom
    try:
        assert rc._report_telemetry(0.0, 1.0) is None
    finally:
        rc._profile_has_telemetry, rc._telemetry_rec_dir = orig


def t_report_telemetry_skips_a_broken_recording_and_reads_the_open_one():
    started = "2026-10-07T20:00:00+02:00"
    start = datetime.datetime.fromisoformat(started).timestamp()
    rows = [{"name": f"{p}.gt7rec{ext}", "path": p, "size": 1, "started": started,
             "duration_s": 600.0, "laps": None, "partial": bool(ext)}
            for p, ext in (("a", ""), ("b", ""), ("c", ".part"))]
    asked = []

    def full_index(path, dbs=None):
        asked.append(path)
        if path == "b":
            raise gt7_recording.RecordingError("/home/someone/b.gt7rec: corrupt")
        lap = trt._lap(1, 5.0, status="not counted", reason="pit", relay=95.0, rec=path)
        return {"rec": path, "started": started, "start_ts": start, "end_ts": start + 600,
                "track": None, "laps": [lap]}

    stubs = {"_profile_has_telemetry": lambda: True, "_telemetry_rec_dir": lambda: "unused",
             "_telemetry_dbs": lambda: (None, None), "_telemetry_full_index": full_index,
             "_relay_open_file": lambda: "c.gt7rec"}
    orig = {k: getattr(rc, k) for k in stubs}
    orig_list = gt7_recording.list_recordings
    for k, v in stubs.items():
        setattr(rc, k, v)
    gt7_recording.list_recordings = lambda d: rows
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            block = rc._report_telemetry(start, start + 600)
    finally:
        for k, v in orig.items():
            setattr(rc, k, v)
        gt7_recording.list_recordings = orig_list
    assert asked == ["a", "b", "c"], "the report also indexes the recording the relay is writing"
    assert block is not None and block["laps_total"] == 2, "the broken recording adds no lap"
    assert block["partial"] is True, "the open recording marks the block partial"
    assert "b.gt7rec is not a readable recording" in out.getvalue(), out.getvalue()
    assert "/home/someone" not in out.getvalue(), "no machine path in the note"


def t_generate_passes_the_window_to_telemetry():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "health-history.db")
        _seed_db(db)
        seen = []
        tel = rtel.telemetry_block([trt.session_index()], trt.WINDOW)

        def fake(frm, to):
            seen.append((frm, to))
            return tel

        orig = (rc._health_db_path, rc._runtime_dir, rc._report_name_map,
                rc._report_event_title, rc._report_telemetry)
        rc._health_db_path = lambda: db
        rc._runtime_dir = lambda: d
        rc._report_name_map = lambda: {}
        rc._report_event_title = lambda: "Solo Event"
        rc._report_telemetry = fake
        try:
            r = rc._build_report_file()
        finally:
            (rc._health_db_path, rc._runtime_dir, rc._report_name_map,
             rc._report_event_title, rc._report_telemetry) = orig
        assert seen == [r["window"]], seen
        assert "<h2>Telemetry</h2>" in r["html"]
        assert "Best lap 0:43.810" in r["summary"]
```

The test stubs the part 3 helpers on `rc` and patches only `gt7_recording.list_recordings`, so `_telemetry_reason` still sees the real `RecordingError`. `_relay_open_file` is stubbed to name the open file: an implementation that skipped it would fail on `asked`, and the stub keeps the test from calling a relay running on the dev machine.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 tests/test_report.py`
Expected: FAIL with `AttributeError: module 'racecast' has no attribute '_report_telemetry'` (alphabetical order runs `t_generate_passes_the_window_to_telemetry` first among the new tests).

- [ ] **Step 3: Implement**

Leave the module import line of `src/racecast.py` alone. Between `_report_backlog_thresholds` and `_build_report_file`, add:

```python
def _recordings_in_window(rows, frm, to):
    """The list_recordings rows whose span [started, started + duration_s] overlaps [frm, to]."""
    import datetime
    out = []
    for r in rows:
        try:
            start = datetime.datetime.fromisoformat(r["started"]).timestamp()
        except (KeyError, TypeError, ValueError):
            continue
        if start <= to and start + float(r.get("duration_s") or 0.0) >= frm:
            out.append(r)
    return out


def _report_telemetry(frm, to):
    """The report's telemetry block from the active profile's GT7 recordings in the
    window, including the one the relay is still writing. Solo POV only; None when
    nothing overlaps or the data is unreadable."""
    if not _profile_has_telemetry():
        return None
    try:
        import gt7_recording
        import report_telemetry as rtel
        rows = _recordings_in_window(gt7_recording.list_recordings(_telemetry_rec_dir()),
                                     frm, to)
        if not rows:
            return None
        dbs = _telemetry_dbs()
        indexes = []
        for r in rows:
            try:
                idx = _telemetry_full_index(r["path"], dbs)
            except Exception as exc:  # noqa: BLE001  one broken recording must not drop the others
                print(f"note: telemetry recording {r['name']} skipped "
                      f"({_telemetry_reason(exc, r['name'])}).")
                continue
            indexes.append(dict(idx, partial=r["partial"]))
        return rtel.telemetry_block(indexes, (frm, to))
    except Exception as exc:  # noqa: BLE001  telemetry must never fail the report
        print(f"note: telemetry section skipped ({_telemetry_reason(exc)}).")
        return None
```

In `_build_report_file`, replace the `rbuild.build_report(...)` call:

```python
    report = rbuild.build_report(bucketed, events, _report_name_map(), title,
                                 (frm, to), time.time(), host=_report_host(),
                                 telemetry=_report_telemetry(frm, to),
                                 **_report_backlog_thresholds())
```

`tools/build-binary.py`: after the line `"--hidden-import", "gt7_laps",` add:

```python
           "--hidden-import", "report_telemetry",
```

The other `gt7_*` modules are already hidden imports from parts 1 to 3. `t_function_local_peer_imports_are_frozen` in `tests/test_racecast.py` fails without this line, because `racecast.py` imports `report_telemetry` inside a function.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 tests/test_report.py && python3 tests/test_racecast.py && python3 tools/lint.py`
Expected: ALL PASS in both, lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/racecast.py tools/build-binary.py tests/test_report.py
git commit -m "feat(report): collect solo POV telemetry recordings for the report window (#789)"
```

---

### Task 5: Visual check, wiki and final gates

**Files:**
- Modify: `src/docs/wiki/Health-Monitor.md` (section "Post-Event Report", after the `**Name resolution:**` paragraph)

- [ ] **Step 1: Render a synthetic report**

```bash
python3 - <<'EOF'
import os, sys, tempfile
sys.path[:0] = ["src/scripts", "tests"]
import report_build as rb
import test_report_build as trb
import test_report_telemetry as trt
other = {"rec": "20261007-210000", "started": "", "start_ts": 1500.0, "end_ts": 1600.0,
         "track": None, "partial": True,
         "laps": [trt._lap(1, 5.0, trt._trace(45.0, 45.0), track_id=None, car_id=1234,
                           car="Mazda Roadster", rec="20261007-210000")]}
tel = trt.rtel.telemetry_block([trt.session_index(), other], trt.WINDOW)
rep = rb.build_report([trb._sample(0.0), trb._sample(30.0)], [], {}, "Solo Test", (0.0, 30.0),
                      now=1000.0, telemetry=tel)
d = tempfile.mkdtemp()
with open(os.path.join(d, "report.html"), "w", encoding="utf-8") as fh:
    fh.write(rb.render_html(rep))
print(d)
print(rb.render_summary_text(rep))
EOF
```

Expected: a temp directory path, then the summary with `  Best lap 0:43.810 (theoretical 0:40.000), 3 laps, Suzuka Circuit` right after the uptime line (the feed lines follow).

- [ ] **Step 2: Verify visually**

Invoke the `ui-visual-verification` skill. Serve the printed directory with `python3 -m http.server 8765 --bind 127.0.0.1 --directory <dir>` (file:// is blocked in the Playwright MCP) and open `http://127.0.0.1:8765/report.html`. Take element screenshots of the Telemetry section at 1280 px and 390 px width, `Read` them and check:
- both groups have a heading, five key figures and the tyre table; the Mazda group (one counted lap) shows `—` for consistency and its heading names `20261007-210000, session 1`;
- trend: time labels inside the left margin, the pit lap grey on the top edge, the best lap green;
- map: a round circle (uniform scale), first half green, second half red, start marker visible, nothing clipped at the edges;
- the partial caveat shows; the lap table with its `#` column fits at 1280 px. At 390 px compare with the existing tables (Stream & OBS quality): if the lap table overflows the card while they do not, wrap `_table(...)` for the lap table in `<div style='overflow-x:auto'>` and rerun Task 3's tests.

The report HTML is not a file the Stop hook gates; record the marker only if the hook lists a changed file.

- [ ] **Step 3: Wiki paragraph**

In `src/docs/wiki/Health-Monitor.md`, after the paragraph starting `**Name resolution:**`, add:

```markdown
**Telemetry (solo POV):** when the active profile is solo POV and a GT7 telemetry
recording (see [Relay mode](Relay-Mode)) overlaps the report window, the report gains a
Telemetry section. For each track and car it shows the best lap, the theoretical best
(the fastest 200 m mini-sectors of the counted laps added up), the consistency (standard
deviation of the counted lap times), fuel per lap and the average tyre temperature per
wheel. Laps on an unknown track are grouped per GT7 session, as in the Control Center.
A small chart plots every lap time in driving order, and a map draws the best lap with
each mini-sector coloured from green (no time lost to the fastest counted lap in that
sector) to red (the lap's largest loss). A table lists every lap with its number in
driving order, GT7's lap number, its time, the relay's verdict and reason, fuel, top
speed, car and track. Counted laps are the laps the relay counted on the HUD, including
each new reference lap; lap times are GT7's own where they arrived. The CLI summary and
the Discord embed add one line such as
`Best lap 1:58.432 (theoretical 1:57.910), 23 laps, Suzuka Circuit` for the track and
car with the most counted laps. The report also reads the recording the relay is still
writing, so the report at `event stop` covers the event that just ended; its last lap may
be missing, and the report says so. Reading that open recording adds about 16 s per 3 h of
recording to the report build. A recording that cannot be read is skipped; it never stops
the report. Endurance reports have no Telemetry section.
```

- [ ] **Step 4: Commit**

```bash
git add src/docs/wiki/Health-Monitor.md src/scripts/report_build.py
git commit -m "docs(wiki): telemetry section of the post-event report (#789)"
```

(`report_build.py` only when Step 2 needed the overflow wrapper.)

- [ ] **Step 5: Final gates**

With the tree clean, run lint, then integrate the current epic branch before the expensive build:

```bash
python3 tools/lint.py
git fetch origin && git rebase origin/epic/785-gt7-telemetry
python3 tools/run-tests.py
python3 tools/build.py
```

Expected: lint clean, the rebase applies without conflicts, every test file passes, build verify passes.

Then open one PR following the `ship-feature` skill, with base `epic/785-gt7-telemetry` (`gh pr create --base epic/785-gt7-telemetry`), never `main`. The body says `Part of #785` and names #789 without a closing keyword, because closing keywords act only on the default branch. Self-review with `pr-review`. The epic branch has no branch protection, so do not rely on auto-merge: wait until every check is green, then squash-merge into `epic/785-gt7-telemetry`, and close #789 by hand.

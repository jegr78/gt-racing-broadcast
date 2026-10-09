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

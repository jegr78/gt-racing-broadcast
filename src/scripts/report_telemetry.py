#!/usr/bin/env python3
"""Telemetry section of the post-event report, from GT7 lap indexes (stdlib only, no I/O).

Input is the lap index (gt7_laps.index) of every recording that overlaps the report
window; output is a JSON-safe block that report_build renders. Figures are per track
and car and use counted laps only (the relay's verdicts, as on the HUD).
"""
import html
import json
import math
import statistics

import gt7_laps

GAP_GREEN = (0x2E, 0x7D, 0x32)
GAP_RED = (0xC6, 0x28, 0x28)
GREY = "#bdbdbd"


def fmt_lap(seconds):
    """118.432 -> '1:58.432'; None or a non-finite value -> the report's empty-cell mark."""
    if seconds is None or not math.isfinite(seconds):
        return "—"
    m, ms = divmod(int(round(float(seconds) * 1000)), 60000)
    return f"{m}:{ms // 1000:02d}.{ms % 1000:03d}"


def track_label(lap):
    """'Track - Layout' as the Control Center names a lap's track, 'Unknown track' without one."""
    track = lap.get("track") or ""
    if not track:
        return "Unknown track"
    layout = lap.get("layout") or ""
    # GT7 names some tracks after their only layout ("Grand Valley - Highway 1" + "Highway 1");
    # only a whole trailing name part counts, so "Nordschleife" + "Schleife" keeps both.
    if not layout or track.lower() == layout.lower() or track.lower().endswith(" " + layout.lower()):
        return track
    return f"{track} - {layout}"


def lap_count(n):
    """'1 lap' or 'N laps'."""
    return f"{n} lap" if n == 1 else f"{n} laps"


def _key(lap):
    """A known track groups across recordings, an unknown one only within its recording
    and GT7 session, as gt7_laps.pool pools them."""
    if lap.get("track_id") is not None:
        base = (lap["track_id"], lap.get("car_id"), None, None)
    else:
        base = (None, lap.get("car_id"), lap.get("rec"), lap.get("session"))
    context = lap.get('compound')
    strategies = lap.get('strategies') or [lap.get('strategy', {})]
    signatures = {json.dumps({k: s[k] for k in ('fuel_map', 'shortshift', 'targets')
                              if s.get(k) is not None and (k != 'targets' or s[k])}, sort_keys=True)
                  for s in strategies}
    strategy = json.dumps(sorted(signatures))
    settings = lap.get('context_settings', {}) if lap.get('settings_confirmed', True) else {}
    group_settings = json.dumps({k: settings.get(k) for k in (
        'bop', 'fixed_setup', 'fuel_x', 'tyre_x', 'time_progression', 'time_of_day')}, sort_keys=True)
    return base + (context, strategy, group_settings, lap.get('definition_fingerprint'), lap.get('sector_variant_id'))


def _row(n, ts, lap):
    return {"n": n, "ts": ts, "rec": lap.get("rec") or "", "session": lap.get("session"),
            "lap": lap.get("lap"), "time_s": lap.get("time_s"),
            "status": lap.get("status") or "", "reason": lap.get("reason") or "",
            "counted": lap.get("comparison_eligible", gt7_laps.pace_eligible(lap)),
            "fuel_l": lap.get("fuel_used_l"), "top_speed_kmh": lap.get("top_speed_kmh"),
            "car": lap.get("car") or "", "track": track_label(lap),
            "shift_analysis": lap.get('shift_analysis')}


def gap_color(gap_s, worst_s):
    """Linear from green (no loss to the best sector) to red (the lap's largest loss)."""
    f = 0.0 if worst_s <= 0 else min(1.0, max(0.0, gap_s / worst_s))
    r, g, b = (int(a + (c - a) * f + 0.5) for a, c in zip(GAP_GREEN, GAP_RED, strict=True))
    return f"#{r:02x}{g:02x}{b:02x}"


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _finite_points(trace):
    """Trace points whose d/x/z are usable numbers; a corrupt cache must not blank the map."""
    return [p for p in trace
            if isinstance(p, dict) and all(_number(p.get(k)) for k in ("d", "x", "z"))]


def _sector_map(best, pool):
    """The best lap's mini-sectors with the time each lost to the fastest counted lap;
    a sector without a time on either side (gt7_laps.best_sectors blanks the last one
    for unequal lap lengths) has gap None and is drawn grey."""
    trace = _finite_points(best.get("trace") or [])
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
            sectors.append({"i": i + 1, "points": pts, "gap_s": gap, "color": color})
    return {"sectors": sectors, "worst_gap_s": worst} if sectors else None


def _group(key, members):
    track_id, car_id, rec, session, compound, _strategy, _settings = key[:7]
    first = members[0][1]
    valid = [(row, lap) for row, lap in members if row["counted"] and row["time_s"] is not None]
    best_row, best = min(valid, key=lambda m: m[0]["time_s"]) if valid else (None, None)
    times = [row["time_s"] for row, _lap in valid]
    timed = [lap for _row, lap in valid if lap.get("sectors")]
    theoretical_s = gt7_laps.theoretical_best(timed) if timed else None
    sector_theory = None
    if first.get('track_definition') and first.get('sector_variant_id'):
        import gt7_track_definitions
        sector_theory = gt7_track_definitions.theoretical(
            [lap for _row, lap in valid], first['track_definition'], first['sector_variant_id'])
    fuel = [lap["fuel_used_l"] for _row, lap in valid if lap.get("fuel_used_l") is not None]
    tyres = [lap["tyre_avg_c"] for _row, lap in valid
             if len(lap.get("tyre_avg_c") or []) == 4 and any(lap["tyre_avg_c"])]
    return {
        "track_id": track_id, "car_id": car_id, "rec": rec, "session": session,
        "track": track_label(first), "track_name": first.get("track") or "",
        "car": members[0][0]["car"], "compound": compound,
        "context_confirmed": all(l.get('context_confirmed', False) for _r, l in members),
        "laps_total": len(members), "laps_counted": len(valid),
        "recs": len({row["rec"] for row, _lap in members}),
        "best_s": best_row["time_s"] if best_row else None,
        "best_lap": ({"n": best_row["n"], "session": best_row["session"],
                      "lap": best_row["lap"], "rec": best_row["rec"]} if best_row else None),
        "theoretical_s": theoretical_s, "mini_sector_sum_s": theoretical_s, "sector_theory": sector_theory,
        "consistency_s": statistics.pstdev(times) if len(times) >= 2 else None,
        # all-zero fuel means consumption is off, not a real 0.0 L lap
        "fuel_per_lap_l": statistics.fmean(fuel) if fuel and any(fuel) else None,
        "tyre_avg_c": ([statistics.fmean(t[i] for t in tyres) for i in range(4)]
                       if tyres else None),
        "trend": [{"n": row["n"], "lap": row["lap"], "time_s": row["time_s"],
                   "counted": row["counted"], "best": row is best_row}
                  for row, _lap in members],
        "map": _sector_map(best, timed) if best is not None else None,
    }


def telemetry_block(indexes, window):
    """The report's telemetry block for the laps that start inside window=(from_ts,
    to_ts), or None when there are none."""
    frm, to = window
    picked, partial, snapshots, definitions, references = [], False, {}, {}, {}
    for idx in indexes:
        found = False
        for lap in idx.get("laps") or []:
            ts = (idx.get("start_ts") or 0.0) + (lap.get("start_t_s") or 0.0)
            if frm <= ts <= to:
                picked.append((ts, lap))
                found = True
        partial = partial or (found and bool(idx.get("partial")))
        if found and idx.get("context_snapshot") is not None:
            snapshots[idx["rec"]] = idx["context_snapshot"]
        if found:
            definitions.update(idx.get('track_definition_snapshots', {}))
            references.update(idx.get('shift_reference_snapshots', {}))
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
    return {"laps": rows, "laps_total": len(rows), "context_snapshots": snapshots, "track_definition_snapshots": definitions,
            "shift_reference_snapshots": references,
            "laps_counted": sum(1 for r in rows if r["counted"]),
            "partial": partial, "groups": groups}


def summary_line(block, esc=None):
    """One line for the CLI summary and Discord: the track and car with the most counted
    laps. `esc`, when given, escapes the track name only (an upstream track name reaches
    this unsanitized, the rest of the line is our own text)."""
    g = block["groups"][0]
    if g["best_s"] is None:
        parts = [f"{lap_count(g['laps_total'])}, none counted"]
    else:
        theory = g.get('sector_theory')
        theo = (f" ({'sector best' if theory.get('confirmed') else 'unconfirmed sector sum'} {fmt_lap(theory['time_s'])})"
                if theory and theory.get('time_s') is not None else "")
        parts = [f"Best lap {fmt_lap(g['best_s'])}{theo}", lap_count(g["laps_counted"])]
    if g["track_name"]:
        parts.append(esc(g["track_name"]) if esc else g["track_name"])
    return ", ".join(parts)


def svg_lap_trend(trend, w=720, h=150):
    """Lap times in driving order: counted laps dark, the best green, the rest grey and
    clamped into the counted laps' range."""
    pts = [p for p in trend if p.get("time_s") is not None and math.isfinite(p["time_s"])]
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


def _thin(coords, min_px=1.0):
    """Drop points closer than min_px to the last kept one; a sector's own first and
    last point always stay so neighbouring sectors still join exactly."""
    if len(coords) <= 2:
        return coords
    kept = [coords[0]]
    for p in coords[1:-1]:
        if math.hypot(p[0] - kept[-1][0], p[1] - kept[-1][1]) >= min_px:
            kept.append(p)
    kept.append(coords[-1])
    return kept


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
    for s in track_map["sectors"]:
        coords = " ".join(f"{x:.1f},{y:.1f}" for x, y in _thin([xy(p) for p in s["points"]]))
        gap = "no time" if s["gap_s"] is None else f"+{s['gap_s']:.3f} s"
        tip = html.escape(f"Sector {s['i']}: {gap}")
        lines.append(f'<polyline points="{coords}" fill="none" stroke="{s["color"]}" '
                     f'stroke-width="5" stroke-linecap="round" stroke-linejoin="round">'
                     f"<title>{tip}</title></polyline>")
    sx, sy = xy(track_map["sectors"][0]["points"][0])
    marker = (f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="5" fill="#ffffff" stroke="#1c1e21" '
              f'stroke-width="2"><title>Start/finish</title></circle>')
    return (f'<svg viewBox="0 0 {size} {size}" width="100%" role="img" '
            f'aria-label="Track map of the best lap">{"".join(lines)}{marker}</svg>')

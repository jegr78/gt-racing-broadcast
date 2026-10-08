#!/usr/bin/env python3
"""GT7 lap index: per-lap traces of a recording, sector math, the cache and the comparison pool.

Stdlib only and no relay imports. A trace is one point every STEP_M metres of lap
distance, starting at 0.0 on the line.
"""
import bisect
import json
import math
import os
import tempfile

import gt7_data
import gt7_recording
import gt7_telemetry

STEP_M = 5.0
SECTOR_M = 200.0
COUNTED = ("reference", "counted")
INDEX_VERSION = 1
CACHE_SUFFIX = ".laps.json"
DECIMATE_M = 2.0              # finer samples add nothing to a 5 m trace
PROJECT_TOL_M = 50.0          # corner cutting moves the projection metres, another branch of the line far more


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


def cache_path(path):
    return os.path.join(os.path.dirname(path),
                        gt7_recording.recording_stem(path) + CACHE_SUFFIX)


def _stamp(path, runtime_base, bundled):
    st = os.stat(path)
    return {"version": INDEX_VERSION, "size": st.st_size, "mtime": st.st_mtime,
            "data_version": gt7_data.data_version(runtime_base, bundled)}


def _read_cache(path, stamp):
    try:
        with open(cache_path(path), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or any(data.get(k) != v for k, v in stamp.items()):
        return None
    return data


def cached(path, runtime_base, bundled=None):
    """The cached index while recording, track data and format are unchanged, else None."""
    try:
        stamp = _stamp(path, runtime_base, bundled)
    except OSError:
        return None
    return _read_cache(path, stamp)


def index(path, track_db, cars, runtime_base, key=None, bundled=None):
    """The lap index of one recording, from the cache when still valid. `key` is
    "<profile>/<stem>", the learned track assignment's key."""
    try:
        stamp = _stamp(path, runtime_base, bundled)
    except OSError as e:
        raise gt7_recording.RecordingError(f"{path}: {e}") from e
    hit = _read_cache(path, stamp)
    if hit is not None:
        return hit
    data = _build(path, track_db, cars, key)
    data.update(stamp)
    _write_cache(cache_path(path), data)
    return data


def _write_cache(path, data):
    """Atomic write; a failed write only costs the cache and never leaves a temp file."""
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".laps-", suffix=".tmp", dir=os.path.dirname(path))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, separators=(",", ":"))
        os.replace(tmp, path)
        tmp = None
    except OSError:
        pass  # a read-only dir only costs the cache
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass  # already gone


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


def _follow(proj, driven, length):
    """Projected distances that stay within PROJECT_TOL_M of the previous one plus the
    driven increment; a missing or implausible projection takes that expected value."""
    out = []
    for i, (p, d) in enumerate(zip(proj, driven, strict=True)):
        expected = d if i == 0 else out[-1] + d - driven[i - 1]
        s = None if p is None else gt7_recording.nearest_station(p, expected, length)
        out.append(s if s is not None and abs(s - expected) <= PROJECT_TOL_M else expected)
    return out


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
            dists = _follow(proj, dists, length)
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
    cur, tail, tyre = [], None, [0.0, 0.0, 0.0, 0.0, 0]
    first = last = None
    for wall_ts, _kind, plain in rec.packets():
        first = wall_ts if first is None else first
        last = wall_ts
        pkt = gt7_telemetry.parse_packet(plain)
        closed = len(laps)
        eng.update(pkt, wall_ts)
        for lap in laps[closed:]:
            times.lap_closed(lap, wall_ts)
            lap_samples.append(cur if tail is None else cur + [tail])
            lap_tyres.append(tyre)
            cur, tail, tyre = [], None, [0.0, 0.0, 0.0, 0.0, 0]
        times.update(pkt, wall_ts)
        if not pkt.on_track or pkt.paused or pkt.loading:
            continue
        sample = _sample(pkt, wall_ts - eng.lap_started_at(), eng.lap_distance())
        if sample is None:
            continue
        if not cur or sample[1] >= cur[-1][1] + DECIMATE_M:    # _trace's rule, applied early
            cur.append(sample)
            tail = None
        else:
            tail = sample
        if all(math.isfinite(v) for v in pkt.tyre_temp):
            for i in range(4):
                tyre[i] += pkt.tyre_temp[i]
            tyre[4] += 1
    by_session = gt7_recording.session_tracks(laps, track_db, key)
    stem = gt7_recording.recording_stem(path)
    out = []
    for i, (lap, tyres) in enumerate(zip(laps, lap_tyres, strict=True)):
        found = by_session.get(lap["session"])
        track_id = found["id"] if found and "id" in found else None
        length = track_db.line_length(track_id) if track_id is not None else None
        samples, lap_samples[i] = lap_samples[i], None    # free each lap's samples once traced
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

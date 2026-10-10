#!/usr/bin/env python3
"""GT7 lap index: per-lap traces of a recording, sector math, the cache and the comparison pool.

Stdlib only and no relay imports. A trace is one point every STEP_M metres of lap
distance, starting at 0.0 on the line; a counted lap's trace ends with a point at the
full lap length carrying the lap time.
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
INDEX_VERSION = 5
CACHE_SUFFIX = ".laps.json"
DECIMATE_M = 2.0              # finer samples add nothing to a 5 m trace
RESUME_CHECK = 64             # bytes before a resume point that must be unchanged to continue there


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
    """A lap's length for sectors: where its trace ends, the full lap length for a counted
    lap; 0.0 when unknown."""
    if lap.get("length_m"):
        return lap["length_m"]
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
    """Per sector the fastest time over the given laps, from their sector times or traces.
    When the laps end at different lengths, every sector from the shortest lap's last
    (its stub) onward is None: those cover a different distance per lap."""
    used = [lap for lap in laps if "sectors" in lap or lap.get("trace")]
    per_lap = [lap["sectors"] if "sectors" in lap else sectors(lap["trace"], lap_length_m(lap))
               for lap in used]
    n = max((len(s) for s in per_lap), default=0)
    best = []
    for i in range(n):
        vals = [s[i] for s in per_lap if i < len(s) and s[i] is not None]
        best.append(min(vals) if vals else None)
    if best and len({lap_length_m(lap) for lap in used} - {0.0}) > 1:
        cut = min((len(s) for s in per_lap if s), default=n) - 1
        best[cut:] = [None] * (n - cut)
    return best


def theoretical_best(laps):
    """Sum of the best sectors over the given laps, or None when a sector has no time
    (also when the laps differ in length, see best_sectors). Clamped to the fastest lap's
    own time_s: rounding every sector to the millisecond can sum a few ms above the lap
    that actually set them all."""
    best = best_sectors(laps)
    if not best or any(v is None for v in best):
        return None
    theo = round(sum(best), 3)
    times = [lap["time_s"] for lap in laps if lap.get("time_s") is not None]
    return min(theo, min(times)) if times else theo


def cache_path(path):
    return os.path.join(os.path.dirname(path),
                        gt7_recording.recording_stem(path) + CACHE_SUFFIX)


def _stamp(path, runtime_base, bundled):
    st = os.stat(path)
    return {"version": INDEX_VERSION, "size": st.st_size, "mtime": st.st_mtime,
            "data_version": gt7_data.data_version(runtime_base, bundled)}


def _load_cache(path):
    try:
        with open(cache_path(path), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _valid(data, stamp):
    return data is not None and all(data.get(k) == v for k, v in stamp.items())


def _public(data):
    """The index as callers see it, without the resume point."""
    data.pop("resume", None)
    return data


def _read_cache(path, stamp):
    data = _load_cache(path)
    return _public(data) if _valid(data, stamp) else None


def _resumable(path, data, stamp):
    """True when the stale cache `data` has a consistent resume point that the recording
    still matches: same header, offset past it, unchanged bytes before the offset."""
    if (data is None or data.get("version") != INDEX_VERSION
            or data.get("data_version") != stamp["data_version"]):
        return False
    point = data.get("resume")
    if not isinstance(point, dict):
        return False
    try:
        rec = gt7_recording.Recording(path)
        offset, check = point["offset"], bytes.fromhex(point["check"])
        n = point["laps"]
        if (not isinstance(offset, int) or not rec.header_end <= offset <= stamp["size"]
                or point["started"] != rec.header.get("started", "")
                or not isinstance(n, int) or n != len(point["raw_laps"])
                or len(data["laps"]) < n or len(check) > offset):
            return False
        with open(path, "rb") as fh:
            fh.seek(offset - len(check))
            return fh.read(len(check)) == check
    except (OSError, gt7_recording.RecordingError, KeyError, TypeError, ValueError):
        return False


def lookup(path, runtime_base, bundled=None):
    """(the cached index while recording, track data and format are unchanged, else None;
    the loaded cache file to hand to index() as `old`, so a stale one is read once)."""
    try:
        stamp = _stamp(path, runtime_base, bundled)
    except OSError:
        return None, None
    data = _load_cache(path)
    return (_public(data), None) if _valid(data, stamp) else (None, data)


def cached(path, runtime_base, bundled=None):
    """The cached index while recording, track data and format are unchanged, else None."""
    return lookup(path, runtime_base, bundled)[0]


_READ = object()


def index(path, track_db, cars, runtime_base, key=None, bundled=None, old=_READ):
    """The lap index of one recording, from the cache when still valid. `key` is
    "<profile>/<stem>", the learned track assignment's key; `old` is the cache file
    lookup() already loaded."""
    try:
        stamp = _stamp(path, runtime_base, bundled)
    except OSError as e:
        raise gt7_recording.RecordingError(f"{path}: {e}") from e
    old = _load_cache(path) if old is _READ else old
    if _valid(old, stamp):
        return _public(old)
    data = None
    if _resumable(path, old, stamp):
        try:
            data = _build(path, track_db, cars, key, old)
        except Exception:  # noqa: BLE001  any doubt about the old cache means a full build
            data = None
    if data is None:
        data = _build(path, track_db, cars, key)
    data.update(stamp)
    _write_cache(cache_path(path), data)
    return _public(data)


def _write_cache(path, data):
    """Atomic write; a failed write only costs the cache and never leaves a temp file."""
    tmp = None
    try:
        prefix = os.path.basename(path)[:-len(".json")] + "-"     # "<stem>.laps-", deletable with it
        fd, tmp = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=os.path.dirname(path))
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
    """Projected distances that follow the previous one plus the driven increment
    (gt7_recording.follow_station)."""
    out = []
    for i, (p, d) in enumerate(zip(proj, driven, strict=True)):
        expected = d if i == 0 else out[-1] + d - driven[i - 1]
        out.append(gt7_recording.follow_station(p, expected, length))
    return out


def _trace(samples, track_db, track_id, length, close=None):
    """Samples resampled every STEP_M of lap distance: along the racing line when the
    track is known, else the driven distance. `close` is (lap length, lap time): the trace
    then ends with a station there, so every lap's sectors share their boundaries and add
    up to the lap time."""
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
    if close is None or not close[0] or close[0] <= 0 or close[1] is None:
        return out
    stop = round(close[0], 1)
    out = [p for p in out if p["d"] < stop]
    while j + 1 < len(ds) and ds[j + 1] <= stop:
        j += 1
    last = _station(stop, pts, ds, j)
    # Receiver-clock samples and GT7's settled duration have different boundary
    # delays. Reconcile the entire captured interval, not just its closing point.
    # A valid boundary packet is included by _Replay, so interpolation reaches
    # the line without flattening the last metres to the last pre-line sample.
    first_t, span = out[0]["t"], last["t"] - out[0]["t"]
    if span <= 0:
        return out  # no captured interval: do not invent a complete lap
    full = out + [last]
    for p in full:
        p["t"] = round((p["t"] - first_t) * close[1] / span, 3)
    full[0]["t"] = 0.0
    full[-1]["t"] = round(close[1], 3)
    return full


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


class _Replay:
    """One pass of the lap index over a recording. It keeps a resume point after the last
    lap whose GT7 time is settled, so the index of a grown recording continues there."""

    def __init__(self, point=None):
        self.eng = gt7_telemetry.TelemetryEngine()
        self.laps = []
        self.eng.on_lap = self.laps.append
        self.times = gt7_recording.LapTimeMatcher()
        self.lap_samples, self.lap_tyres = [], []
        self.cur, self.tail, self.tyre = [], None, [0.0, 0.0, 0.0, 0.0, 0]
        self.first = self.last = None
        self.start, self.dropped, self.point = None, 0, None
        if point is not None:
            self.eng.restore(point["eng"])
            self.times.restore(point["times"])
            self.laps.extend(dict(lap, points=[tuple(p) for p in lap["points"]])
                             for lap in point["raw_laps"])
            self.cur = [tuple(x) for x in point["cur"]]
            self.tail = None if point["tail"] is None else tuple(point["tail"])
            self.tyre = list(point["tyre"])
            self.first, self.last = point["first"], point["last"]
            self.start, self.dropped = point["offset"], point["dropped"]
            self.point = {k: v for k, v in point.items()
                          if k not in ("raw_laps", "started", "check")}
        self.reused = len(self.laps)

    def run(self, rec):
        rec.dropped = self.dropped
        marked = len(self.laps)
        for wall_ts, _kind, plain in rec.packets(self.start):
            self._feed(wall_ts, gt7_telemetry.parse_packet(plain))
            if len(self.laps) > marked and self.times.settled():
                marked = len(self.laps)
                self.point = {
                    "offset": rec.pos, "laps": marked, "eng": self.eng.resume_state(),
                    "times": self.times.resume_state(), "cur": list(self.cur),
                    "tail": self.tail, "tyre": list(self.tyre), "first": self.first,
                    "last": self.last, "dropped": rec.dropped}
        self.dropped = rec.dropped

    def _feed(self, wall_ts, pkt):
        self.first = wall_ts if self.first is None else self.first
        self.last = wall_ts
        eng, times = self.eng, self.times
        closed = len(self.laps)
        eng.update(pkt, wall_ts)
        for lap in self.laps[closed:]:
            times.lap_closed(lap, wall_ts)
            samples = self.cur if self.tail is None else self.cur + [self.tail]
            if lap["status"] in COUNTED:
                dt = max(0.0, wall_ts - lap["end"])
                boundary = _sample(pkt, wall_ts - lap["start"],
                                   lap["distance_m"] + max(0.0, samples[-1][2] / 3.6 if samples else 0.0) * dt)
                if boundary is not None:
                    samples = samples + [boundary]
            self.lap_samples.append(samples)
            self.lap_tyres.append(self.tyre)
            self.cur, self.tail, self.tyre = [], None, [0.0, 0.0, 0.0, 0.0, 0]
        times.update(pkt, wall_ts)
        if not pkt.on_track or pkt.paused or pkt.loading:
            return
        sample = _sample(pkt, wall_ts - eng.lap_started_at(), eng.lap_distance())
        if sample is None:
            return
        cur = self.cur
        if not cur or sample[1] >= cur[-1][1] + DECIMATE_M:    # _trace's rule, applied early
            cur.append(sample)
            self.tail = None
        else:
            self.tail = sample
        if all(math.isfinite(v) for v in pkt.tyre_temp):
            tyre = self.tyre
            for i in range(4):
                tyre[i] += pkt.tyre_temp[i]
            tyre[4] += 1

    def resume_point(self, rec):
        """The JSON-safe resume point for the cache, or None before any settled lap."""
        if self.point is None:
            return None
        offset = self.point["offset"]
        with open(rec.path, "rb") as fh:
            fh.seek(max(0, offset - RESUME_CHECK))
            check = fh.read(offset - fh.tell())
        return dict(self.point, raw_laps=self.laps[:self.point["laps"]],
                    started=rec.header.get("started", ""), check=check.hex())


def _indexed_lap(stem, lap, samples, tyres, first, track_db, cars, found, open_lap=False,
                 after_service=False):
    track_id = found["id"] if found and "id" in found else None
    length = track_db.line_length(track_id) if track_id is not None else None
    relay = round(lap["elapsed"], 3)
    gt7_s = lap.get("gt7_time_s")
    time_s = None if open_lap else (gt7_s if gt7_s is not None else relay)
    close = (length or (samples[-1][1] if samples else lap.get("distance_m")), time_s) \
        if lap["status"] in COUNTED else None
    trace = _trace(samples, track_db, track_id, length, close)
    row = {
        "rec": stem, "session": lap["session"], "lap": lap["lap"],
        "start_t_s": round(lap["start"] - first, 3), "end_t_s": round(lap["end"] - first, 3),
        "gt7_time_s": gt7_s, "relay_time_s": relay,
        "time_s": time_s,
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
        "length_m": lap_length_m({"trace": trace}),
        "sectors": sectors(trace, lap_length_m({"trace": trace})),
        "trace": trace}
    complete_trace = bool(close and len(trace) >= 2 and trace[0]["t"] == 0.0
                          and trace[-1]["d"] == round(close[0], 1)
                          and trace[-1]["t"] == round(close[1], 3)
                          and all(a["t"] <= b["t"] for a, b in zip(trace, trace[1:], strict=False)))
    row.update(capture_complete=lap["capture_complete"], trace_complete=complete_trace,
               data_quality=lap["data_quality"], reasons=lap["reasons"],
               pace_eligible=lap["status"] in COUNTED and complete_trace,
               is_reference=lap["status"] == "reference", after_service=after_service,
               lap_role=("pit" if lap["pit"] else "partial" if not lap["capture_complete"]
                         else "first" if lap["lap"] == 1 else "regular"),
               time_basis="lap-normalized receiver clock" if complete_trace else "receiver clock")
    return row


def _build(path, track_db, cars, key, old=None):
    """The lap index; with `old` (a stale cache whose resume point the file still
    matches) only the bytes after that point are replayed."""
    rec = gt7_recording.Recording(path)
    replay = _Replay(old["resume"] if old is not None else None)
    replay.run(rec)
    laps = replay.laps
    by_session = gt7_recording.session_tracks(laps, track_db, key)
    out = old["laps"][:replay.reused] if old is not None else []
    for lap in out:
        found = by_session.get(lap["session"])
        if (found["id"] if found and "id" in found else None) != lap["track_id"]:
            raise ValueError("a session's track changed, its traces need a full build")
    stem = gt7_recording.recording_stem(path)
    first = replay.first
    for i, lap in enumerate(laps[replay.reused:]):
        previous = laps[replay.reused + i - 1] if replay.reused + i else None
        samples, replay.lap_samples[i] = replay.lap_samples[i], None
        out.append(_indexed_lap(stem, lap, samples, replay.lap_tyres[i], first,
                                track_db, cars, by_session.get(lap["session"]),
                                after_service=bool(previous and previous["pit"]
                                                   and previous["session"] == lap["session"])))
    unfinished = replay.eng.open_lap_record()
    open_lap = None
    if unfinished is not None and (replay.cur or replay.tail):
        samples = replay.cur if replay.tail is None else replay.cur + [replay.tail]
        previous = laps[-1] if laps else None
        open_lap = _indexed_lap(stem, unfinished, samples, replay.tyre, first,
                                track_db, cars, by_session.get(unfinished["session"]),
                                open_lap=True, after_service=bool(previous and previous["pit"]
                                and previous["session"] == unfinished["session"]))
    return {"rec": stem, "name": os.path.basename(path),
            "started": rec.header.get("started", ""), "start_ts": first,
            "end_ts": replay.last, "dropped": replay.dropped,
            "track": _display_track(laps, by_session, track_db),
            "sessions": {str(s): _track_info(v, track_db) for s, v in by_session.items()},
            "laps": out, "open_lap": open_lap, "resume": replay.resume_point(rec)}


def summary(lap):
    """A lap without its trace and positions, for lists."""
    return {k: v for k, v in lap.items() if k not in ("trace", "points")}


def pace_eligible(lap):
    """Explicit numerical eligibility, with compatibility for old caller-made summaries."""
    return lap.get("pace_eligible", lap.get("status") in COUNTED)


def pool(indexes, track_id, car_id, rec=None, session=None):
    """Counted laps comparable with a lap on track_id with car_id, fastest first. A known
    track pools across all given recordings; an unknown one only within (rec, session)."""
    out = []
    for idx in indexes:
        for lap in idx.get("laps", []):
            if not pace_eligible(lap) or lap["car_id"] != car_id:
                continue
            if track_id is not None:
                if lap["track_id"] != track_id:
                    continue
            elif lap["track_id"] is not None or (lap["rec"], lap["session"]) != (rec, session):
                continue
            out.append(lap)
    out.sort(key=lambda lap: lap["time_s"])
    return out

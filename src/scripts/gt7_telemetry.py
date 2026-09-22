#!/usr/bin/env python3
"""GT7 telemetry: pure packet parsing + the derived-metrics engine.

No sockets: the relay owns the UDP thread and feeds decrypted packets here. All
functions are deterministic (timestamps are injected) so the engine is unit-tested
without a console. Offsets follow the community packet-'A' layout;
tools/gt7-telemetry-probe.py checks them against a live console. See the design spec.
"""
import json
import os
import struct
import threading
from collections import deque, namedtuple

# Packet 'A' field offsets (little-endian).
OFF_MAGIC = 0x00
OFF_SPEED = 0x4C        # metres/second (float)
OFF_FUEL_LEVEL = 0x44   # litres in tank (float)
OFF_FUEL_CAP = 0x48     # tank capacity (float)
OFF_TYRE_FL = 0x60      # tyre surface temp, °C (float x4)
OFF_TYRE_FR = 0x64
OFF_TYRE_RL = 0x68
OFF_TYRE_RR = 0x6C
OFF_LAP = 0x74          # current lap (int16)
OFF_BEST_MS = 0x78      # best lap time, ms (int32; -1 = none)
OFF_LAST_MS = 0x7C      # last lap time, ms (int32; -1 = none)
OFF_DAY_PROGRESSION = 0x80  # time of day on track, ms since midnight (int32)
OFF_FLAGS = 0x8E        # simulator flags (uint16 bitfield)
OFF_THROTTLE = 0x91     # 0-255 (uint8)
OFF_BRAKE = 0x92        # 0-255 (uint8)

# Simulator flag bits, the subset we use.
FLAG_ON_TRACK = 1 << 0
FLAG_PAUSED = 1 << 1
FLAG_LOADING = 1 << 2   # loading / processing (menu / replay transitions)

# Trace buffer (throttle/brake).
TRACE_WINDOW_S = 15.0
TRACE_MIN_DT = 1.0 / 30      # decimate 60 Hz -> ~30 Hz

TYRE_AVG_WINDOW_S = 30.0

# Delta trend: which way the gap to best is moving.
DELTA_TREND_WINDOW_S = 1.5    # compare current delta vs this-many-seconds-ago
DELTA_TREND_DEADBAND = 0.02   # s; |change| below this reads as "flat" (anti-jitter)

# A lap only becomes a completed/reference lap if it was opened AT a lap-change
# edge (not the first accumulator, which starts mid-lap when the relay connects
# while the driver is already on track) AND ran a plausible minimum length. This
# stops a partial "connect mid-lap" lap or a menu/out-lap blip (lapCount -> 0/-1)
# from being installed as the reference and permanently corrupting delta/predicted.
MIN_LAP_S = 5.0           # seconds, a loose floor below any real circuit lap
MIN_LAP_DIST = 100.0      # metres; started_at_boundary is the primary guard, so these
                          # two only drop the shortest blips that slip through clean
# Cap the per-lap distance/time sample list so a same-lap packet flood (lap held
# constant, distance forced up) cannot grow it without bound. Normal laps decimate
# to a few hundred samples; a lap that exceeds the cap is bogus and marked unclean.
SAMPLE_MIN_DIST = 4.0     # metres between retained samples
MAX_SAMPLES = 4000        # ~16 km at 4 m spacing, far past any real lap

# A pit (in/out) lap is not representative: its time is inflated by the pit-lane
# transit and the stationary service, and a refuel makes its fuel delta negative.
# GT7 sends no pit flag, so we derive one: a sustained standstill (the car must
# stop in the box for ANY service, incl. tyre-only) OR fuel rising during the lap
# (a refuel). Such a lap is excluded from the reference and the time/fuel averages.
STOPPED_SPEED_MPS = 0.5   # at/below this the car counts as stationary
PIT_STOP_MIN_S = 2.0      # cumulative standstill (s) that marks a pit lap
FUEL_RISE_L = 0.05        # litres; fuel_end above fuel_start by this = a refuel

GT7Packet = namedtuple("GT7Packet", [
    "speed_mps", "fuel_level", "fuel_capacity", "tyre_temp",
    "throttle", "brake", "lap", "best_ms", "last_ms", "day_ms",
    "flags", "on_track", "paused", "loading",
])


def parse_packet(plain):
    """Parse a decrypted packet-'A' buffer into a GT7Packet."""
    flags = struct.unpack_from("<H", plain, OFF_FLAGS)[0]
    return GT7Packet(
        speed_mps=struct.unpack_from("<f", plain, OFF_SPEED)[0],
        fuel_level=struct.unpack_from("<f", plain, OFF_FUEL_LEVEL)[0],
        fuel_capacity=struct.unpack_from("<f", plain, OFF_FUEL_CAP)[0],
        tyre_temp=(
            struct.unpack_from("<f", plain, OFF_TYRE_FL)[0],
            struct.unpack_from("<f", plain, OFF_TYRE_FR)[0],
            struct.unpack_from("<f", plain, OFF_TYRE_RL)[0],
            struct.unpack_from("<f", plain, OFF_TYRE_RR)[0],
        ),
        throttle=plain[OFF_THROTTLE],
        brake=plain[OFF_BRAKE],
        lap=struct.unpack_from("<h", plain, OFF_LAP)[0],
        best_ms=struct.unpack_from("<i", plain, OFF_BEST_MS)[0],
        last_ms=struct.unpack_from("<i", plain, OFF_LAST_MS)[0],
        day_ms=struct.unpack_from("<i", plain, OFF_DAY_PROGRESSION)[0],
        flags=flags,
        on_track=bool(flags & FLAG_ON_TRACK),
        paused=bool(flags & FLAG_PAUSED),
        loading=bool(flags & FLAG_LOADING),
    )


class _LapAccumulator:
    """Accumulates time + distance samples within one lap.

    started_at_boundary is False for the first accumulator (opened mid-lap when
    the relay connects), True for accumulators opened at a real lap-change edge.
    Only the latter may become a completed/reference lap (see _finalise_lap)."""
    __slots__ = ("t0", "elapsed", "distance", "samples", "clean", "last_t",
                 "fuel_start", "fuel_end", "started_at_boundary", "pit", "stopped_s")

    def __init__(self, now, started_at_boundary=False):
        self.t0 = now
        self.elapsed = 0.0
        self.distance = 0.0
        self.samples = [(0.0, 0.0)]   # (distance, time) pairs, monotonic in distance
        self.clean = True
        self.last_t = now
        self.fuel_start = None
        self.fuel_end = None
        self.started_at_boundary = started_at_boundary
        self.pit = False
        self.stopped_s = 0.0

    def add(self, pkt, now):
        dt = now - self.last_t
        self.last_t = now
        if dt <= 0:
            return
        if dt > 2.0:                  # a long gap (stall/menu) makes the lap time unreliable
            self.clean = False
            return
        if pkt.paused or pkt.loading or not pkt.on_track:
            self.clean = False
            return
        self.elapsed += dt
        self.distance += max(0.0, pkt.speed_mps) * dt
        if pkt.speed_mps < STOPPED_SPEED_MPS:             # standstill in the pit box
            self.stopped_s += dt
            if self.stopped_s >= PIT_STOP_MIN_S:
                self.pit = True
        if self.distance >= self.samples[-1][0] + SAMPLE_MIN_DIST:
            if len(self.samples) >= MAX_SAMPLES:
                self.clean = False        # bogus/flooded lap: cap memory, drop the lap
                return
            self.samples.append((self.distance, self.elapsed))
        if self.fuel_start is None:
            self.fuel_start = pkt.fuel_level
        self.fuel_end = pkt.fuel_level
        if self.fuel_end > self.fuel_start + FUEL_RISE_L:  # refuel = pit lap
            self.pit = True


class TelemetryEngine:
    """Consumes GT7Packets (timestamp injected) and derives lap/delta/predicted.

    Lap detection uses the packet lap counter; menu/replay/paused activity never
    finalises a lap. The reference is
    the fastest clean completed lap, stored as time-vs-distance samples.
    """

    def __init__(self):
        self._last = None                 # last GT7Packet
        self._lap_num = None
        self._acc = None                  # current _LapAccumulator
        self._ref = None                  # reference (best) lap: {"time": s, "samples": [...]}
        self._lap_time_sum = 0.0  # Σ admitted clean lap durations (s), whole session
        self._lap_time_n = 0
        self._lap_fuel_sum = 0.0  # Σ admitted clean lap fuel burns (L), whole session
        self._lap_fuel_n = 0
        self._session_dist_m = 0.0    # Σ on-track distance driven this session (m)
        self._trace = deque()     # (t, throttle01, brake01), decimated + windowed
        self._trace_last_t = None
        self._tyre_hist = deque()     # (t, (fl,fr,rl,rr)) over TYRE_AVG_WINDOW_S
        self._top_speed = 0.0
        self._delta_hist = deque()    # (t, delta) over DELTA_TREND_WINDOW_S; cleared per lap

    def _is_session_boundary(self, pkt):
        """A new session (practice->quali->race, or a restart) is signalled by the
        lap counter going backwards or the best lap clearing to -1. GT7 sends no
        explicit session-change event, so we derive it from these two signals."""
        if self._lap_num is None:
            return False
        if pkt.lap < self._lap_num:                       # lap counter went backwards
            return True
        if self._last is not None and self._last.best_ms > 0 and pkt.best_ms == -1:
            return True                                    # best lap was wiped
        return False

    def _reset_session(self, now, pkt):
        """Drop everything derived from the previous session (possibly a different
        track/car) and re-open a fresh lap at the boundary."""
        self._ref = None
        self._lap_time_sum = 0.0
        self._lap_time_n = 0
        self._lap_fuel_sum = 0.0
        self._lap_fuel_n = 0
        self._session_dist_m = 0.0
        self._top_speed = 0.0
        self._delta_hist.clear()
        self._lap_num = pkt.lap
        self._acc = _LapAccumulator(now, started_at_boundary=True)

    def update(self, pkt, now):
        if self._lap_num is None:         # first packet: open a lap MID-lap (not a boundary)
            self._lap_num = pkt.lap
            self._acc = _LapAccumulator(now)                       # started_at_boundary=False
        elif self._is_session_boundary(pkt):   # session change: wipe stale derived state
            self._reset_session(now, pkt)
        elif pkt.lap != self._lap_num:    # lap-change edge: this new lap starts at the line
            self._finalise_lap()
            if self._acc is not None:     # bank the closing lap's driven distance
                self._session_dist_m += self._acc.distance
            self._lap_num = pkt.lap
            self._acc = _LapAccumulator(now, started_at_boundary=True)
            self._delta_hist.clear()
            if self._last is not None:    # seed the new lap's start-of-lap fuel reading
                self._acc.fuel_start = self._last.fuel_level
        if self._acc is not None:
            self._acc.add(pkt, now)
        self._last = pkt
        if self._trace_last_t is None or (now - self._trace_last_t) >= TRACE_MIN_DT:
            self._trace_last_t = now
            self._trace.append((now, pkt.throttle / 255.0, pkt.brake / 255.0))
            cutoff = now - TRACE_WINDOW_S
            while self._trace and self._trace[0][0] < cutoff:
                self._trace.popleft()
        if pkt.on_track and not pkt.paused and pkt.speed_mps > self._top_speed:
            self._top_speed = pkt.speed_mps
        self._tyre_hist.append((now, pkt.tyre_temp))
        tcut = now - TYRE_AVG_WINDOW_S
        while self._tyre_hist and self._tyre_hist[0][0] < tcut:
            self._tyre_hist.popleft()
        d = self._current_delta()
        if d is not None:
            self._delta_hist.append((now, d))
            dcut = now - DELTA_TREND_WINDOW_S
            while self._delta_hist and self._delta_hist[0][0] < dcut:
                self._delta_hist.popleft()

    def _finalise_lap(self):
        acc = self._acc
        if acc is None or not acc.clean or len(acc.samples) < 2:
            return
        if acc.pit:                       # in/out lap (standstill or refuel): never a
            return                        # reference, and out of the time/fuel averages
        # Only a lap that opened at a real lap-change edge and ran a plausible
        # minimum length counts. That rejects the mid-lap-connect partial and the
        # menu/out-lap blips, which would poison the reference and the averages.
        if not acc.started_at_boundary or acc.elapsed < MIN_LAP_S or acc.distance < MIN_LAP_DIST:
            return
        if self._ref is None or acc.elapsed < self._ref["time"]:
            self._ref = {"time": acc.elapsed, "samples": acc.samples}
        self._lap_time_sum += acc.elapsed
        self._lap_time_n += 1
        if acc.fuel_start is not None and acc.fuel_end is not None:
            burn = acc.fuel_start - acc.fuel_end
            if burn > 0:
                self._lap_fuel_sum += burn
                self._lap_fuel_n += 1

    def _ref_time_at(self, distance):
        """Interpolate the reference lap's elapsed time at a given distance."""
        s = self._ref["samples"]
        if distance <= s[0][0]:
            return s[0][1]
        if distance >= s[-1][0]:
            return s[-1][1]
        lo, hi = 0, len(s) - 1
        while lo + 1 < hi:                # binary search on distance
            mid = (lo + hi) // 2
            if s[mid][0] <= distance:
                lo = mid
            else:
                hi = mid
        (d0, t0), (d1, t1) = s[lo], s[hi]
        if d1 == d0:
            return t0
        return t0 + (t1 - t0) * (distance - d0) / (d1 - d0)

    def _current_delta(self):
        """Instantaneous delta vs the reference at the current distance (seconds),
        positive = behind the reference. None when there is no reference/accumulator
        or the lap has not accumulated any time yet."""
        acc = self._acc
        if self._ref is None or acc is None or acc.elapsed <= 0:
            return None
        return acc.elapsed - self._ref_time_at(acc.distance)

    def _avg_lap_s(self):
        """Session mean of the admitted clean laps (s), or None before any lap."""
        return self._lap_time_sum / self._lap_time_n if self._lap_time_n else None

    def _fuel(self):
        pkt = self._last
        level = pkt.fuel_level if pkt else 0.0
        per_lap = laps = time_rem = None
        if self._lap_fuel_n:
            per_lap = self._lap_fuel_sum / self._lap_fuel_n
            if per_lap > 0:
                laps = level / per_lap
                avg = self._avg_lap_s()
                if avg is not None:
                    time_rem = laps * avg
        return {"level": level, "per_lap": per_lap,
                "laps_remaining": laps, "time_remaining_s": time_rem}

    def _tyre_avg(self):
        if not self._tyre_hist:
            return self._last.tyre_temp if self._last else (0.0, 0.0, 0.0, 0.0)
        sums = [0.0, 0.0, 0.0, 0.0]
        for _, temps in self._tyre_hist:
            for i in range(4):
                sums[i] += temps[i]
        n = len(self._tyre_hist)
        return tuple(s / n for s in sums)

    def snapshot(self):
        pkt = self._last
        acc = self._acc
        has_ref = self._ref is not None
        delta = predicted = best = None
        if has_ref:
            best = self._ref["time"]
            delta = self._current_delta()
            if delta is not None:
                predicted = best + delta
        delta_dir = None
        if has_ref and len(self._delta_hist) >= 2:
            diff = self._delta_hist[-1][1] - self._delta_hist[0][1]
            if diff > DELTA_TREND_DEADBAND:
                delta_dir = "up"        # gap growing -> losing time now
            elif diff < -DELTA_TREND_DEADBAND:
                delta_dir = "down"      # gap shrinking -> gaining now
            else:
                delta_dir = "flat"
        return {
            "speed_mps": pkt.speed_mps if pkt else 0.0,
            "tyre_temp": pkt.tyre_temp if pkt else (0.0, 0.0, 0.0, 0.0),
            "lap": pkt.lap if pkt else 0,
            "current_lap_s": acc.elapsed if acc else 0.0,
            "best_s": best,
            "delta_s": delta,
            "delta_dir": delta_dir,
            "predicted_s": predicted,
            "has_reference": has_ref,
            "avg_lap_s": self._avg_lap_s(),
            "session_dist_m": self._session_dist_m + (acc.distance if acc else 0.0),
            "fuel": self._fuel(),
            "tyre_temp_avg": self._tyre_avg(),
            "top_speed_mps": self._top_speed,
            "time_of_day_ms": pkt.day_ms if pkt else None,
        }

    def trace_batch(self, limit=150):
        items = list(self._trace)[-limit:]
        return [{"t": t, "throttle": thr, "brake": brk} for (t, thr, brk) in items]


def _fmt_time(seconds):
    if seconds is None:
        return None
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m}:{s:06.3f}" if m else f"{s:.3f}"


def _fmt_clock(ms):
    """Format ms-since-midnight as HH:MM:SS (wrapped to 24 h). None -> None."""
    if ms is None:
        return None
    total = (int(ms) // 1000) % 86400
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _band(temp_c, thresholds):
    cold, opt_hi, crit = thresholds
    if temp_c < cold:
        return "cold"
    if temp_c <= opt_hi:
        return "optimal"
    if temp_c <= crit:            # critical is strictly ABOVE the threshold (spec: >crit)
        return "hot"
    return "critical"


def format_snapshot(snap, units, thresholds):
    imperial = units == "imperial"
    spd = snap["speed_mps"] * (2.2369363 if imperial else 3.6)
    dist = snap.get("session_dist_m", 0.0) * (0.000621371 if imperial else 0.001)
    avgs = snap["tyre_temp_avg"]
    tyres = []
    for i, c in enumerate(snap["tyre_temp"]):
        val = c * 9 / 5 + 32 if imperial else c
        a = avgs[i] * 9 / 5 + 32 if imperial else avgs[i]
        tyres.append({"value": round(val), "avg": round(a), "band": _band(c, thresholds)})
    fuel = snap["fuel"]
    lvl = fuel["level"] * (0.2641720 if imperial else 1.0)   # L -> gal
    return {
        "speed": round(spd),
        "top_speed": round(snap["top_speed_mps"] * (2.2369363 if imperial else 3.6)),
        "tyres": tyres,
        "lap": snap["lap"],
        "current_lap": _fmt_time(snap["current_lap_s"]),
        "best_lap": _fmt_time(snap["best_s"]),
        "avg_lap": _fmt_time(snap.get("avg_lap_s")),
        "delta": None if snap["delta_s"] is None else round(snap["delta_s"], 2),
        "predicted": _fmt_time(snap["predicted_s"]),
        "has_reference": snap["has_reference"],
        "time_of_day": _fmt_clock(snap["time_of_day_ms"]),
        "session_distance": round(dist, 1),
        "fuel": {
            "level": round(lvl, 1),
            "per_lap": (None if fuel["per_lap"] is None
                        else round(fuel["per_lap"] * (0.2641720 if imperial else 1.0), 1)),
            "laps_remaining": (None if fuel["laps_remaining"] is None
                               else round(fuel["laps_remaining"], 1)),
            "time_remaining": _fmt_time(fuel["time_remaining_s"]),
        },
        "delta_dir": snap.get("delta_dir"),
        "units": {
            "speed": "mph" if imperial else "km/h",
            "temp": "°F" if imperial else "°C",
            "fuel": "gal" if imperial else "L",
            "distance": "mi" if imperial else "km",
        },
    }


class TelemetryStore:
    """Thread-safe wrapper around TelemetryEngine. Persists ONLY the reference lap
    to `path` (survives an OBS Browser Source reload; resets on relay restart).
    """

    def __init__(self, path=None, units="metric", thresholds=(70, 85, 95), reset=False):
        self._eng = TelemetryEngine()
        self._lock = threading.Lock()
        self._source = None            # latched console IP (discovery/UX), or None
        self._path = path
        self._units = units
        self._thresholds = thresholds
        self._dirty_ref = None
        if reset:
            # Fresh session: the relay resets the reference on every start (spec §D)
            # so a stale lap from another track/car/session is never loaded. Drop
            # any persisted file so it can't resurface; _save recreates it live.
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass  # no stale file to clear
        else:
            self._load()

    def update(self, pkt, now):
        with self._lock:
            had = self._eng._ref
            self._eng.update(pkt, now)
            if self._eng._ref is not had:
                if self._eng._ref is None:     # session reset dropped the reference
                    self._remove_file()
                else:                          # a new reference lap was set
                    self._save()

    def set_source(self, ip):
        """Record the console IP the listener latched (surfaced on /telemetry/data
        so `racecast gt7-discover` can read it without a second 33740 bind)."""
        with self._lock:
            self._source = ip

    def data(self):
        with self._lock:
            snap = self._eng.snapshot()
            source = self._source
        out = format_snapshot(snap, self._units, self._thresholds)
        out["source"] = source
        return out

    def trace(self, limit=150):
        with self._lock:
            return self._eng.trace_batch(limit)

    def _save(self):
        if not self._path or self._eng._ref is None:
            return
        try:
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._eng._ref, fh)
            os.replace(tmp, self._path)
        except OSError:
            pass                               # best-effort, never crash the relay

    def _remove_file(self):
        if not self._path:
            return
        try:
            os.remove(self._path)
        except OSError:
            pass                               # best-effort, never crash the relay

    def _load(self):
        if not self._path:
            return
        try:
            with open(self._path, encoding="utf-8") as fh:
                ref = json.load(fh)
            if (isinstance(ref, dict) and isinstance(ref.get("time"), (int, float))
                    and isinstance(ref.get("samples"), list) and len(ref["samples"]) >= 2):
                ref["samples"] = [(float(d), float(t)) for d, t in ref["samples"]]
                self._eng._ref = ref
        except (OSError, ValueError, TypeError):
            pass  # no valid reference file yet; start without one

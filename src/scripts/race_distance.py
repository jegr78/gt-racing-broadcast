"""Race distance, order, gaps, driver changes and finish from driver telemetry (#1112).

Pure: the relay feeds every decoded sample of every driver connection with its
receive time on one clock, and reads `standings`. Each entry's distance is
`anchor + (lap - lap0) * L + (station - station0)` along the picked layout's line:
GT7's lap counter gives the whole laps, the projection onto the line the fraction,
and of the three candidates one lap apart the one nearest the predicted distance
wins, so a counter that flips a moment before or after the line cannot jump a lap.

- Start: with a formation lap a car's distance starts when it crosses into lap 2,
  without one at its first sample on lap 1 (the grid lies behind the line).
- Box: a car standing (below BOX_SPEED_MPS) more than BOX_LATERAL_M off the line for
  BOX_HOLD_S. Not confirmed by a recording yet (#1110 measures pit stops).
- Driver change (multi-lobby only): inside the change window the active car
  standing in the box sets the team's anchor; another connection of the team that
  then leaves the box continues from it. Lap 1 of the follow-up lobby, driven before
  that car leaves the box, never counts.
- Plausibility: every field comes from a driver's machine. A sample with a non-finite
  value, a position far off the line's area, a time before the car's last sample or a
  distance step faster than MAX_SPEED_MPS is dropped before it changes anything.
- Finish: by lap count (the leader completes `lap_count` on its lobby's lap counter,
  lap 1 included) or by `final_lap` (the leader's next crossing); every other car at
  its next crossing after the leader; `finish` freezes everything at once. The race
  timer never ends a race.
"""
import bisect
import math

from gt7_tracks import _Line

NO_DATA_S = 5.0
BOX_SPEED_MPS = 1.0
BOX_LATERAL_M = 12.0
BOX_HOLD_S = 1.5
BOX_LEAVE_MPS = 2.0
CHANGE_WINDOW_S = 600.0     # a stint switch's window closes after this, or when every team changed
HISTORY_S = 900.0
MAX_HISTORY = 12000         # samples per entry, a hard cap next to HISTORY_S
MAX_SPEED_MPS = 120.0
STEP_SLACK_M = 100.0        # projection noise and a counter flipping a moment off the line
AREA_MARGIN_M = 300.0
CAR_FORGET_S = 120.0        # a quiet connection that no entry follows is dropped
PRUNE_EVERY_S = 10.0


class _Car:
    def __init__(self, conn, name):
        self.conn, self.name = conn, name
        self.t = self.lap = self.s = self.lateral = None
        self.speed = 0.0
        self.position = None
        self.still_since = None
        self.in_box = False
        self.left_box = False      # this sample left the box
        self.counting = False
        self.anchor = self.lap0 = self.s0 = 0.0


class _Entry:
    def __init__(self, entry, conn):
        self.entry, self.active = entry, conn
        self.distance = None
        self.ts, self.ds = [], []
        self.change_anchor = None
        self.changed = False
        self.pit_stops = 0
        self.finished_at = None


class Race:
    def __init__(self, trace, formation=True, multi_lobby=False, finish_mode="laps",
                 lap_count=None):
        pts = [(p["x"], p["z"]) for p in trace] if trace else None
        self._line = _Line(pts) if pts and len(pts) >= 3 else None
        self.length = self._line.length if self._line else None
        self._area = None
        if self._line is not None:
            xs, zs = [p[0] for p in pts], [p[1] for p in pts]
            self._area = (min(xs) - AREA_MARGIN_M, max(xs) + AREA_MARGIN_M,
                          min(zs) - AREA_MARGIN_M, max(zs) + AREA_MARGIN_M)
        self._pruned_at = None
        self.formation = formation
        self.multi_lobby = multi_lobby
        self.finish_mode = finish_mode
        self.lap_count = lap_count
        self._cars, self._entries = {}, {}
        self._window_until = None
        self._final_lap_at = None
        self._finishing_at = None
        self._finished = False

    # -- commands ------------------------------------------------------------------
    def open_change_window(self, now):
        if self.multi_lobby:
            self._window_until = now + CHANGE_WINDOW_S
            for ent in self._entries.values():
                ent.changed = False

    def final_lap(self, now):
        if self._final_lap_at is None and not self._finished:
            self._final_lap_at = now

    def finish(self, now):
        for ent in self._entries.values():
            if ent.finished_at is None:
                ent.finished_at = now
        self._finished = True

    # -- samples -------------------------------------------------------------------
    @staticmethod
    def _finite(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)

    def _valid(self, sample):
        """The sample's checked (t, lap, speed m/s, x, z); None drops it."""
        t, lap = sample.get("t"), sample.get("lap")
        speed, x, z = sample.get("speed_kmh") or 0.0, sample.get("x"), sample.get("z")
        if not self._finite(t) or not self._finite(speed) or speed < 0:
            return None
        if lap is not None and type(lap) is not int:
            return None
        if x is not None or z is not None:
            if not (self._finite(x) and self._finite(z)):
                return None
            a = self._area
            if a is not None and not (a[0] <= x <= a[1] and a[2] <= z <= a[3]):
                return None
        return t, lap, speed / 3.6, x, z

    def _prune(self, now):
        if self._pruned_at is not None and now - self._pruned_at < PRUNE_EVERY_S:
            return
        self._pruned_at = now
        active = {e.active for e in self._entries.values()}
        for conn in [c for c, car in self._cars.items()
                     if c not in active and car.t is not None and now - car.t > CAR_FORGET_S]:
            del self._cars[conn]

    def update(self, entry, conn, name, sample):
        checked = self._valid(sample)
        if checked is None:
            return
        t, lap, speed, x, z = checked
        car = self._cars.get(conn)
        if car is not None and car.t is not None and t < car.t:
            return                                # older than what this car already sent
        if car is None:
            car = self._cars[conn] = _Car(conn, name)
        ent = self._entries.get(entry)
        if ent is None:
            ent = self._entries[entry] = _Entry(entry, conn)
        self._prune(t)
        located = None
        if self._line is not None and x is not None:
            located = self._line.locate(x, z)
            if lap is not None and not self._plausible(ent, car, t, lap, located[2]):
                return
        prev_lap = car.lap
        car.t, car.lap, car.speed = t, lap, speed
        car.position = sample.get("race_position")
        if located is None:
            return
        _i, car.lateral, car.s = located
        self._box(car, t)
        if conn == ent.active and car.left_box and car.counting:
            ent.pit_stops += 1
        if self.multi_lobby:
            self._change(ent, car, t)
        if conn != ent.active or ent.finished_at is not None or car.lap is None:
            return
        crossed = prev_lap is not None and car.lap > prev_lap
        if not car.counting:
            if self.formation and not ent.changed and car.lap < 2:
                return
            car.counting = True
            car.anchor, car.lap0, car.s0 = 0.0, (2 if self.formation else 1), 0.0
            raw = self._raw(car)
            ent.distance = raw - self.length if raw > self.length / 2 else raw
        else:
            raw = self._raw(car)
            predicted = ent.distance + car.speed * max(0.0, t - ent.ts[-1]) if ent.ts else raw
            ent.distance = min((raw, raw - self.length, raw + self.length),
                               key=lambda v: abs(v - predicted))
        ent.ts.append(t)
        ent.ds.append(ent.distance)
        if ent.ts[0] < t - HISTORY_S:
            cut = bisect.bisect_left(ent.ts, t - HISTORY_S)
            del ent.ts[:cut], ent.ds[:cut]
        if len(ent.ts) > MAX_HISTORY:
            del ent.ts[:len(ent.ts) - MAX_HISTORY], ent.ds[:len(ent.ds) - MAX_HISTORY]
        if crossed:
            self._crossing(ent, car, t)

    def _plausible(self, ent, car, t, lap, s):
        """False when the counting active car's next distance is out of reach."""
        if not car.counting or car.conn != ent.active or ent.distance is None or not ent.ts:
            return True
        raw = car.anchor + (lap - car.lap0) * self.length + (s - car.s0)
        step = min((raw, raw - self.length, raw + self.length), key=lambda v: abs(v - ent.distance))
        return abs(step - ent.distance) <= MAX_SPEED_MPS * (t - ent.ts[-1]) + STEP_SLACK_M

    def _raw(self, car):
        return car.anchor + (car.lap - car.lap0) * self.length + (car.s - car.s0)

    def _box(self, car, t):
        car.left_box = False
        if car.speed < BOX_SPEED_MPS and car.lateral > BOX_LATERAL_M:
            car.still_since = t if car.still_since is None else car.still_since
            car.in_box = car.in_box or t - car.still_since >= BOX_HOLD_S
        else:
            car.still_since = None
            if car.in_box and car.speed >= BOX_LEAVE_MPS:
                car.in_box, car.left_box = False, True

    def _change(self, ent, car, t):
        window = self._window_until is not None and t <= self._window_until
        if car.conn == ent.active:
            if window and car.in_box and ent.change_anchor is None and ent.distance is not None \
                    and not ent.changed:
                ent.change_anchor = ent.distance
            elif car.left_box:
                ent.change_anchor = None          # it drove on: a pit stop, not a change
            return
        if car.left_box and ent.change_anchor is not None and window:
            ent.active = car.conn
            car.counting = True
            car.anchor, car.lap0, car.s0 = ent.change_anchor, car.lap, car.s
            ent.distance = ent.change_anchor
            ent.change_anchor, ent.changed = None, True
            if all(e.changed for e in self._entries.values()):
                self._window_until = None

    def _crossing(self, ent, car, t):
        if self._finished:
            return
        if self._finishing_at is not None:
            if ent.finished_at is None and t > self._finishing_at:
                ent.finished_at = t
                self._finished = all(e.finished_at is not None or self._quiet(e, t)
                                     for e in self._entries.values())
            return
        leader = max((e.distance for e in self._entries.values() if e.distance is not None),
                     default=None)
        if ent.distance is None or ent.distance < leader:
            return
        by_laps = self.finish_mode == "laps" and self.lap_count and car.lap > self.lap_count
        by_flag = self._final_lap_at is not None and t > self._final_lap_at
        if by_laps or by_flag:
            ent.finished_at = self._finishing_at = t
            self._finished = len(self._entries) == 1

    def _quiet(self, ent, now):
        car = self._cars.get(ent.active)
        return car is None or car.t is None or now - car.t > NO_DATA_S

    # -- reading -------------------------------------------------------------------
    def _state(self):
        if self._line is None:
            return "no_line"
        if self._finished:
            return "finished"
        if self._finishing_at is not None:
            return "finishing"
        if self._final_lap_at is not None:
            return "final_lap"
        if all(e.distance is None for e in self._entries.values()):
            return "waiting"
        return "racing"

    @staticmethod
    def _time_at(ent, d):
        """When `ent` reached distance d, interpolated; None outside its history."""
        if not ent.ds or d < ent.ds[0] or d > ent.ds[-1]:
            return None
        i = bisect.bisect_left(ent.ds, d)
        if i == 0 or ent.ds[i] == d:
            return ent.ts[i]
        d0, d1, t0, t1 = ent.ds[i - 1], ent.ds[i], ent.ts[i - 1], ent.ts[i]
        return t0 + (t1 - t0) * (d - d0) / (d1 - d0) if d1 > d0 else t1

    def _behind(self, ahead, ent):
        """(laps behind, seconds behind) of `ent` relative to `ahead`."""
        if ahead.distance is None or ent.distance is None:
            return None, None
        laps = math.floor((ahead.distance - ent.distance) / self.length)
        if laps >= 1:
            return laps, None
        car = self._cars[ent.active]
        if ent.finished_at is not None and ahead.finished_at is not None:
            return 0, ent.finished_at - ahead.finished_at
        reached = self._time_at(ahead, ent.distance)
        return 0, (None if reached is None else max(0.0, car.t - reached))

    def standings(self, now):
        ents = list(self._entries.values())

        def key(e):
            done = e.finished_at is not None and self._finishing_at is not None
            dist = e.distance if e.distance is not None else -math.inf
            laps = math.floor(dist / self.length) if self.length and e.distance is not None else -1
            return (0, -laps, e.finished_at) if done else (1, -dist, 0)
        ents.sort(key=key)
        mismatch = self._mismatches(ents)
        rows = []
        for i, e in enumerate(ents):
            car = self._cars[e.active]
            quiet = self._quiet(e, now)
            lapped = gap = interval = None
            if e.distance is not None and not quiet:
                lapped, gap = self._behind(ents[0], e) if i else (0, 0)
                interval = self._behind(ents[i - 1], e)[1] if i else None
            rows.append({
                "entry": e.entry, "driver": car.name, "conn": e.active,
                "distance_m": None if e.distance is None else round(e.distance, 1),
                "laps": max(0, math.floor(e.distance / self.length)) if e.distance is not None else 0,
                "gap_s": None if lapped else (None if gap is None else round(gap, 2)),
                "interval_s": None if interval is None else round(interval, 2),
                "lapped": lapped or 0,
                "status": "no_data" if quiet else ("box" if car.in_box else
                                                   ("waiting" if e.distance is None else "racing")),
                "finished": e.finished_at is not None,
                "pit_stops": e.pit_stops,
                "gt7_position": car.position,
                "position_mismatch": e.entry in mismatch})
        return {"state": self._state(), "line_length_m": self.length, "entries": rows}

    def _mismatches(self, ordered):
        """Entries whose GT7 positions contradict the distance order (single lobby only)."""
        if self.multi_lobby:
            return set()
        known = [(e.entry, self._cars[e.active].position) for e in ordered
                 if e.distance is not None and self._cars[e.active].position]
        out = set()
        for i, (a, pa) in enumerate(known):
            for b, pb in known[i + 1:]:
                if pb < pa:
                    out.update((a, b))
        return out

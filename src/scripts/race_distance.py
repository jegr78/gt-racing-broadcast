"""Race distance, order, gaps, driver changes and finish from driver telemetry (#1112).

Pure: the relay feeds every decoded sample of every driver connection with its
receive time on one clock, and reads `standings`. GT7's lap counter reads 0 on the
grid and steps at each crossing of the finish line. The finish line's station on the
picked layout's line is learned per car from its own crossings; a car that has not
crossed yet uses the agreeing medoid of the counting cars of at least LINE_QUORUM
entries, so one sender cannot move it for everyone.
A car's distance is `anchor + (lap - lap0) * L + frac - frac0`, with `frac` its
position past the line:
the counter gives whole laps, the projection the fraction. Only near the line, where
the counter may flip a moment before or after the projection wraps, the candidate
nearest the predicted distance decides.

- Start: with a formation lap a car's distance starts when its counter reaches 2,
  without one when it reaches 1; the crossing times form the starting gaps. A sender
  that connects later starts from its counter, at most on the leader's lap.
- A counter that drops (a new GT7 session, such as practice before the race) restarts
  that entry in a single lobby; in endurance it holds the entry until Race Control
  rejoins it (#1113).
- Box: a car standing (below BOX_SPEED_MPS) more than BOX_LATERAL_M off the line for
  BOX_HOLD_S. Not confirmed by a recording yet (#1110 measures pit stops).
- Driver change (multi-lobby only): inside the change window the active car
  standing in the box sets the team's anchor; another connection of the team that
  then leaves the box continues from it. A driver who stays on for the next stint keeps
  the connection: its counter drops in the new lobby, and leaving that lobby's box
  continues from the anchor too. Lap 1 of the follow-up lobby, driven before that car
  leaves the box, never counts.
- Plausibility: every field comes from a driver's machine. A sample with a non-finite
  value, a position far off the line's area or a time before the car's last sample is
  dropped; one whose distance step is faster than MAX_SPEED_MPS only keeps the
  connection live. Loading and paused samples only keep it live. A car takes over
  only with a lobby counter of at most MAX_CHANGE_LAP.
- Finish: single lobby when the leader completes `lap_count` laps of GT7's counter,
  lap 1 (a formation lap) included, checked against its distance;
  endurance when the leader's car of the final lobby (it took over after the final
  stint switch) passes `lap_count` on that lobby's counter. `final_lap` ends the race
  at the leader's next crossing. Every other car finishes at its next crossing after
  the leader; `finish` freezes everything at once. The race timer never ends a race.
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
SNAP_M = 40.0               # around the line the counter may be a sample early or late
AREA_MARGIN_M = 300.0
CAR_FORGET_S = 120.0        # a quiet connection that no entry follows is dropped
PRUNE_EVERY_S = 10.0
MAX_CHANGE_LAP = 2          # a car leaving a fresh lobby's box has driven at most lap 1
LINE_QUORUM = 3


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
        self.anchor = self.f0 = 0.0
        self.lap0 = 0
        self.line_s = None         # the finish line's station, from this car's own crossing


class _Entry:
    def __init__(self, entry, conn):
        self.entry, self.active = entry, conn
        self.distance = None
        self.ts, self.ds = [], []
        self.change_anchor = None
        self.changed = False
        self.held = False          # endurance: the counter dropped, waiting for a rejoin
        self.relobby = False       # endurance: the same driver moved on to the next lobby
        self.since = None          # when the active car began counting
        self.pit_stops = 0
        self.finished_at = None

    def restart(self):
        self.distance, self.ts, self.ds = None, [], []
        self.pit_stops, self.finished_at, self.since = 0, None, None


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
        self.formation = formation
        self.multi_lobby = multi_lobby
        self.finish_mode = finish_mode
        self.lap_count = lap_count
        self._cars, self._entries = {}, {}
        self._window_until = None
        self._final_at = None       # endurance: when the final stint lobby went on air
        self._final_lap_at = None
        self._finishing_at = None
        self._finished = False
        self._pruned_at = None

    # -- commands ------------------------------------------------------------------
    def open_change_window(self, now):
        if self.multi_lobby:
            self._window_until = now + CHANGE_WINDOW_S
            for ent in self._entries.values():
                ent.changed = False

    def final_lobby(self, lap_count, now):
        """Endurance: the final stint lobby is on air and runs `lap_count` laps."""
        self.lap_count, self._final_at = lap_count, now

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

    def _prune(self, now, keep):
        if self._pruned_at is not None and now - self._pruned_at < PRUNE_EVERY_S:
            return
        self._pruned_at = now
        active = {e.active for e in self._entries.values()} | {keep}
        for conn in [c for c, car in self._cars.items()
                     if c not in active and car.t is not None and now - car.t > CAR_FORGET_S]:
            del self._cars[conn]

    def _circ(self, a, b):
        d = abs(a - b) % self.length
        return min(d, self.length - d)

    def _line_of(self, car):
        """This car's line station; else the medoid of the counting cars of at least
        LINE_QUORUM entries when most of them agree within SNAP_M; else None."""
        if car.line_s is not None:
            return car.line_s
        known = []
        for e in self._entries.values():
            c = self._cars.get(e.active)
            if c is not None and c.counting and c.line_s is not None:
                known.append(c.line_s)
        if len(known) < LINE_QUORUM:
            return None
        medoid = min(known, key=lambda a: sum(self._circ(a, b) for b in known))
        close = sum(1 for b in known if self._circ(medoid, b) <= SNAP_M)
        return medoid if 2 * close > len(known) else None

    def _frac(self, car, s):
        return (s - car.line) % self.length

    def _raw(self, car, lap, s):
        return car.anchor + (lap - car.lap0) * self.length + self._frac(car, s) - car.f0

    def _next_distance(self, ent, car, t, lap, s):
        """The counting car's distance at this sample."""
        raw = self._raw(car, lap, s)
        frac = self._frac(car, s)
        if SNAP_M < frac < self.length - SNAP_M or not ent.ts:
            return raw
        # Just before the line the counter may have flipped already, just after not yet.
        other = raw - self.length if frac > self.length / 2 else raw + self.length
        predicted = ent.distance + car.speed * max(0.0, t - ent.ts[-1])
        return min((raw, other), key=lambda v: abs(v - predicted))

    def _start(self, ent, car, t, anchor, lap0, s, from_line=True):
        """Count from here; False while no line station is known. `from_line`: the
        distance counts from the race start line (a fresh start or a late sender);
        otherwise from `anchor` at this spot (a driver change)."""
        line = self._line_of(car)
        if line is None:
            return False
        car.line = line
        frac = self._frac(car, s)
        car.counting, car.anchor, car.lap0 = True, anchor, lap0
        car.f0 = 0.0 if from_line else frac
        ent.distance, ent.since = self._raw(car, car.lap, s), t
        if from_line and frac > self.length - SNAP_M:
            ent.distance -= self.length    # the counter flipped: a few metres short is past it
        return True

    def _late_start_ok(self, ent, lap, lap0):
        """A sender may start past the start lap only up to the leader's lap."""
        ahead = [math.floor(e.distance / self.length) for e in self._entries.values()
                 if e is not ent and e.distance is not None]
        return lap - lap0 <= (max(ahead) if ahead else 0)

    def update(self, entry, conn, name, sample):
        checked = self._valid(sample)
        if checked is None:
            return
        t, lap, speed, x, z = checked
        self._prune(t, conn)
        car = self._cars.get(conn)
        if car is not None and car.t is not None and t < car.t:
            return                                # older than what this car already sent
        if car is None:
            car = self._cars[conn] = _Car(conn, name)
        ent = self._entries.get(entry)
        if ent is None:
            ent = self._entries[entry] = _Entry(entry, conn)
        if sample.get("loading") or sample.get("paused") or self._line is None \
                or x is None or lap is None:
            car.t = t
            return
        _i, lateral, s = self._line.locate(x, z)
        active = conn == ent.active
        prev_lap = car.lap
        if active and prev_lap is not None and lap < prev_lap and ent.finished_at is None:
            car.counting = False                  # a new GT7 session on this console
            if self.multi_lobby and ent.change_anchor is not None:
                ent.relobby = True
            elif self.multi_lobby:
                ent.held = ent.distance is not None
            else:
                ent.restart()
        distance = None
        if active and car.counting and ent.finished_at is None:
            distance = self._next_distance(ent, car, t, lap, s)
            if ent.ts and abs(distance - ent.distance) > \
                    MAX_SPEED_MPS * (t - ent.ts[-1]) + STEP_SLACK_M:
                car.t = t                         # implausible: live, but nothing moves
                return
        if prev_lap is not None and lap == prev_lap + 1 and car.line_s is None:
            car.line_s = s                        # this car's own crossing marks the line
        car.t, car.lap, car.speed, car.lateral, car.s = t, lap, speed, lateral, s
        car.position = sample.get("race_position")
        self._box(car, t)
        if active and car.left_box and car.counting:
            ent.pit_stops += 1
        if self.multi_lobby:
            self._change(ent, car, t)
            if conn != ent.active or not active:
                return
        if not active or ent.finished_at is not None or ent.held:
            return
        if not car.counting:
            lap0 = 2 if self.formation else 1
            if lap < lap0 or (self.multi_lobby and ent.changed) \
                    or not self._late_start_ok(ent, lap, lap0) \
                    or not self._start(ent, car, t, 0.0, lap0, s):
                return
        elif distance is not None:
            ent.distance = distance
        ent.ts.append(t)
        ent.ds.append(ent.distance)
        if ent.ts[0] < t - HISTORY_S:
            cut = bisect.bisect_left(ent.ts, t - HISTORY_S)
            del ent.ts[:cut], ent.ds[:cut]
        if len(ent.ts) > MAX_HISTORY:
            del ent.ts[:len(ent.ts) - MAX_HISTORY], ent.ds[:len(ent.ds) - MAX_HISTORY]
        if prev_lap is not None and lap > prev_lap:
            self._crossing(ent, car, t)

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
            elif car.left_box and ent.relobby and window and car.lap <= MAX_CHANGE_LAP:
                if self._start(ent, car, t, ent.change_anchor, car.lap, car.s, from_line=False):
                    self._changed(ent, t)
            elif car.left_box:
                ent.change_anchor = None          # it drove on: a pit stop, not a change
            return
        if car.left_box and ent.change_anchor is not None and window \
                and car.lap <= MAX_CHANGE_LAP \
                and self._start(ent, car, t, ent.change_anchor, car.lap, car.s, from_line=False):
            ent.active = car.conn
            self._changed(ent, t)

    def _changed(self, ent, t):
        ent.change_anchor, ent.changed, ent.held, ent.relobby = None, True, False, False
        ent.ts.append(t)
        ent.ds.append(ent.distance)
        if all(e.changed for e in self._entries.values()):
            self._window_until = None

    def _final_car(self, ent):
        return self._final_at is not None and ent.since is not None and ent.since >= self._final_at

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
        if self.finish_mode == "laps" and self.lap_count:
            by_laps = (car.lap > self.lap_count and self._final_car(ent)) if self.multi_lobby \
                else ent.distance >= (self.lap_count - car.lap0 + 1) * self.length - SNAP_M
        else:
            by_laps = False
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

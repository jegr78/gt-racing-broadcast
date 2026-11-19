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
  lap 1 (a formation lap) included, counted in race distance so a red flag restart
  from the box keeps the count;
  endurance when the leader's car of the final lobby (it took over after the final
  stint switch) passes `lap_count` on that lobby's counter. `final_lap` ends the race
  at the leader's next crossing. Every other car finishes at its next crossing after
  the leader; `finish` freezes everything at once. The race timer never ends a race.
  From the winner's flag on the order is laps, then who reached that lap count first,
  so a lapped car taking the flag stays behind a lead-lap car still running.
- Red flag (#1113): `red_flag` freezes every entry's distance. In the restart lobby
  (a new connection, or a counter that dropped) each car continues from its anchor
  when it leaves the box. `keep` anchors at the frozen distance, `neutralise`
  anchors every car of a lap at that lap's best distance. `release` ends the mode:
  a car still in its old session counts on from where it is, one in a restart lobby
  still resumes at its box exit.
- Rejoin (endurance): a marked team's next car to leave the box inside the change
  window starts from the leader's distance when the window opened.
- Lap times (#1115): the relay times every lap of the active car between two steps
  of GT7's counter. A lap counts only when the car counted for all of it (not the
  formation lap, not lap 1 of a follow-up lobby, not a lap with a start, red flag,
  change or move in it), and GT7's `last_ms` for it is taken only when it agrees with
  the relay's own timing within LAP_TIME_TOLERANCE. The fastest of those is the race's
  fastest lap.
- Corrections add metres and pit stops per entry; `override_change` makes a team's
  connection active at once, continuing from the current distance.
- Estimate: Race Control puts a quiet car S seconds behind a reference car. While it
  stays quiet it shows the reference's distance S seconds ago, frozen under a red flag
  (its restart anchor) and after the flag; its next data ends the estimate.
"""
import bisect
import math
from array import array

from gt7_tracks import _Line

NO_DATA_S = 5.0
BOX_SPEED_MPS = 1.0
BOX_LATERAL_M = 12.0
BOX_HOLD_S = 1.5
BOX_LEAVE_MPS = 2.0
CHANGE_WINDOW_S = 600.0     # a stint switch's window closes after this, or when every team changed
HISTORY_S = 900.0
MAX_HISTORY = 12000         # samples per entry, a hard cap next to HISTORY_S
COARSE_STEP_S = 1.0         # history older than the fine one keeps one point per second
MAX_COARSE = 86400          # 24 hours of coarse history per entry
MAX_SPEED_MPS = 120.0
STEP_SLACK_M = 20.0         # projection noise on top of MAX_SPEED_MPS over the sample's time
SNAP_M = 40.0               # around the line the counter may be a sample early or late
AREA_MARGIN_M = 300.0
CAR_FORGET_S = 120.0        # a quiet connection that no entry follows is dropped
LAP_TIME_TOLERANCE = (1.0, 0.03)   # GT7's lap time vs the relay's: 1 s or 3 %, the larger
LAP_TIME_WAIT_S = 6.0       # GT7 reports a lap's time shortly after the line
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
        self.line = None           # the line station this car counts from
        self.anchor = self.f0 = 0.0
        self.lap0 = 0
        self.line_s = None         # the finish line's station, from this car's own crossing
        self.entry = None
        self.restart_ready = False # red flag: this car is in the restart lobby
        self.fuel = self.x = self.z = None
        self.line_t = None         # relay time of the last counter step while counting
        self.lap_ok = False        # the running lap has been counted from its line
        self.pending = None        # (relay lap time, line time) waiting for GT7's last_ms
        self.last_ms = None        # the last last_ms seen


def _interpolate(values, keys, k):
    """The value at key k in one history (time at a distance, or the reverse); None
    outside it."""
    if not keys or k < keys[0] or k > keys[-1]:
        return None
    i = bisect.bisect_left(keys, k)
    if i == 0 or keys[i] == k:
        return values[i]
    k0, k1, v0, v1 = keys[i - 1], keys[i], values[i - 1], values[i]
    return v0 + (v1 - v0) * (k - k0) / (k1 - k0) if k1 > k0 else v1


def _across(values, keys, cvalues, ckeys, k):
    """The value at key k over the coarse and the fine history and the step between
    them; None outside both."""
    if keys and k >= keys[0]:
        return _interpolate(values, keys, k)
    if not ckeys or k < ckeys[0]:
        return None
    if k <= ckeys[-1] or not keys:
        return _interpolate(cvalues, ckeys, k)
    k0, k1, v0, v1 = ckeys[-1], keys[0], cvalues[-1], values[0]
    return v0 + (v1 - v0) * (k - k0) / (k1 - k0) if k1 > k0 else v1


class _Entry:
    def __init__(self, entry, conn):
        self.entry, self.active = entry, conn
        self.distance = None
        self.ts, self.ds = [], []
        self.cts, self.cds = array("d"), array("d")
        self.change_anchor = None
        self.changed = False
        self.held = False          # endurance: the counter dropped, waiting for a rejoin
        self.relobby = False       # endurance: the same driver moved on to the next lobby
        self.since = None          # when the active car began counting
        self.frozen = None         # red flag: the distance when it was shown
        self.waiting = False       # red flag: not yet resumed in the restart lobby
        self.rejoin = False
        self.adjust = 0.0          # corrections in metres
        self.pit_adjust = 0
        self.pit_stops = 0
        self.finished_at = None
        self.last_lap_ms = None
        self.stint_laps = 0        # race laps since the active car began counting
        self.parked = None         # (conn, counting state) of the car moved away from it

    def restart(self):
        self.distance, self.ts, self.ds = None, [], []
        self.cts, self.cds = array("d"), array("d")
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
        self._red = None            # {"at", "mode"} while the red flag mode runs
        self._restart_mode = "keep"
        self._preset = {}           # entry -> [metres, pit stops, rejoin] for entries not seen yet
        self._parked = {}           # entries whose only car was moved to another entry
        self._used_rejoins = []     # entries whose rejoin mark a change consumed
        self.fastest = None         # {entry, driver, ms}
        self._window_leader = None  # the leader's distance when the change window opened
        self._est = {}              # entry -> {ref, seconds, held, on}
        self._ended_est = []        # entries whose estimate their returning data ended

    # -- commands ------------------------------------------------------------------
    def open_change_window(self, now):
        if self.multi_lobby:
            self._window_until = now + CHANGE_WINDOW_S
            self._window_leader = max((self._shown(e) for e in self._entries.values()
                                       if e.distance is not None), default=None)
            for ent in self._entries.values():
                ent.changed = False

    def red_flag(self, now, mode):
        """Freeze every entry; each continues from its anchor in the restart lobby."""
        self._advance(now)
        self._red = {"at": now, "mode": mode}
        self._restart_mode = mode
        for ent in self._entries.values():
            if ent.waiting:
                continue                           # a second red flag keeps the first anchor
            ent.frozen = None
            est = self._est.get(ent.entry)
            if est is not None and est["on"] and ent.finished_at is None:
                ent.frozen, ent.waiting = est["held"] - ent.adjust, True
            elif ent.distance is not None and ent.finished_at is None:
                ent.frozen, ent.waiting = ent.distance, True
            car = self._cars.get(ent.active)
            if car is not None:
                car.counting = False
        for car in self._cars.values():
            car.restart_ready = False
            car.lap_ok, car.pending = False, None

    def set_restart_mode(self, mode):
        """Override the restart for entries still waiting to resume."""
        self._restart_mode = mode
        if self._red is not None:
            self._red["mode"] = mode

    def release(self, now):
        """End the red flag mode. An entry whose car is still in the session it raced in
        (a mis-press, or a suspension the lobby drove through) counts on from where it
        is; one waiting in a restart lobby resumes when it leaves the box."""
        self._red = None
        for ent in self._entries.values():
            car = self._cars.get(ent.active)
            if ent.waiting and car is not None and not car.restart_ready and car.lap is not None:
                car.counting, ent.waiting = True, False

    def take_used_rejoins(self):
        """The entries whose rejoin mark was consumed since the last call."""
        used, self._used_rejoins = self._used_rejoins, []
        return used

    def rejoin(self, entry, on):
        ent = self._entries.get(entry) or self._parked.get(entry)
        if ent is None:
            self._preset.setdefault(entry, [0.0, 0, False])[2] = bool(on)
        else:
            ent.rejoin = bool(on)

    def correct(self, entry, metres=0.0, pit_stops=0):
        """Add metres and pit stops; an entry without data yet gets them on arrival."""
        ent = self._entries.get(entry) or self._parked.get(entry)
        if ent is None:
            preset = self._preset.setdefault(entry, [0.0, 0, False])
            preset[0] += metres
            preset[1] += pit_stops
        else:
            ent.adjust += metres
            ent.pit_adjust += pit_stops

    def estimate(self, entry, ref, seconds, now):
        """Show the quiet `entry` `seconds` behind `ref` until its data returns; setting
        it again replaces it. ValueError when it cannot apply."""
        ent, other = self._entries.get(entry), self._entries.get(ref)
        if entry == ref:
            raise ValueError("an entry cannot follow itself")
        if ent is None:
            raise ValueError("that entry has no car in the race")
        if other is None or other.distance is None or self._quiet(other, now):
            raise ValueError("the reference needs a car with data")
        if self._red is not None:
            raise ValueError("no estimate under a red flag")
        if self._finished or ent.finished_at is not None or other.finished_at is not None:
            raise ValueError("no estimate after the flag")
        self._est[entry] = {"ref": ref, "seconds": float(seconds), "held": None, "on": False}
        self._advance(now)
        if self._quiet(ent, now) and not self._est[entry]["on"]:
            del self._est[entry]
            raise ValueError("the reference has no distance that far back")

    def clear_estimate(self, entry):
        """Remove an entry's estimate; False when it had none."""
        return self._est.pop(entry, None) is not None

    def take_ended_estimates(self):
        """The entries whose estimate their returning data ended since the last call."""
        ended, self._ended_est = self._ended_est, []
        return ended

    def _advance(self, now):
        """Move each estimate of a quiet car with its reference; it holds under a red
        flag, after the flag and while the reference is quiet or waiting to restart."""
        for entry, est in list(self._est.items()):
            ent, ref = self._entries.get(entry), self._entries.get(est["ref"])
            if ent is not None and est["held"] is not None and not self._quiet(ent, now):
                del self._est[entry]                   # its data returned, on any connection
                self._ended_est.append(entry)
                continue
            if ent is None or not self._quiet(ent, now):
                est["on"] = False
                continue
            if ref is not None and ref.distance is not None and self._red is None \
                    and not self._finished and ent.finished_at is None \
                    and ref.finished_at is None and not ref.waiting and not self._quiet(ref, now):
                d = self._distance_at(ref, now - est["seconds"])
                if d is not None:
                    est["held"] = d + ref.adjust
            est["on"] = est["held"] is not None

    def override_change(self, entry, conn, now):
        """Make `conn` the team's active connection now, from the current distance;
        False (nothing changed) when it is no car of the team or no line is known for it."""
        ent, car = self._entries.get(entry), self._cars.get(conn)
        if ent is None or car is None or car.entry != entry or car.s is None \
                or ent.distance is None:
            return False
        if not self._start(ent, car, now, ent.distance, car.lap, car.s, from_line=False):
            return False
        old = self._cars.get(ent.active)
        if old is not None and old is not car:
            old.counting = False
        ent.active = conn
        ent.changed, ent.change_anchor, ent.held, ent.relobby = True, None, False, False
        return True

    def _anchor_of(self, ent):
        """Where an entry continues after the red flag. Neutralise levels the cars the
        tower shows on one lap, corrections included."""
        if self._restart_mode == "neutralise":
            lap = math.floor((ent.frozen + ent.adjust) / self.length)
            level = max(e.frozen + e.adjust for e in self._entries.values()
                        if e.frozen is not None
                        and math.floor((e.frozen + e.adjust) / self.length) == lap)
            return level - ent.adjust
        return ent.frozen

    def final_lobby(self, lap_count, now):
        """Endurance: the final stint lobby is on air and runs `lap_count` laps."""
        self.lap_count, self._final_at = lap_count, now

    def final_lap(self, now):
        if self._final_lap_at is None and not self._finished:
            self._final_lap_at = now

    def finish(self, now):
        self._advance(now)
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

    def _move(self, car, entry):
        """The producer moved this connection to `entry`: it stops counting for the old
        entry. An old entry with no other car known is parked with its distance,
        corrections and this car's counting state, so moving the car back resumes it."""
        old = self._entries.get(car.entry)
        state = (car.counting, car.anchor, car.lap0, car.f0, car.line)
        car.entry, car.counting = entry, False
        car.lap_ok, car.pending = False, None
        if old is not None and old.active == car.conn:
            others = [c.conn for c in self._cars.values() if c.entry == old.entry]
            if others:
                old.active = others[0]
            else:
                old.parked = (car.conn, state)
                self._parked[old.entry] = self._entries.pop(old.entry)

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
        car.lap_ok, car.line_t, car.pending = False, None, None   # the running lap is partial
        ent.stint_laps = 0
        ent.distance, ent.since = self._raw(car, car.lap, s), t
        if from_line and frac > self.length - SNAP_M:
            ent.distance -= self.length    # the counter flipped: a few metres short is past it
        return True

    def _late_start_ok(self, ent, lap, lap0):
        """A sender may start past the start lap only up to the leader's lap. With no
        entry counting yet (a race rebuilt mid-race: relay restart, new pick, reset)
        the bound is the lap that min(LINE_QUORUM - 1, known) other entries report, one
        lap per entry, so no entry lifts it alone."""
        ahead = [math.floor(e.distance / self.length) for e in self._entries.values()
                 if e is not ent and e.distance is not None]
        if ahead:
            return lap - lap0 <= max(ahead)
        if lap <= lap0:
            return True
        best = {}
        for c in self._cars.values():
            if c.entry != ent.entry and c.lap is not None:
                best[c.entry] = max(best.get(c.entry, c.lap), c.lap)
        laps = sorted(best.values(), reverse=True)
        return bool(laps) and lap <= laps[min(LINE_QUORUM - 1, len(laps)) - 1]

    def update(self, entry, conn, name, sample):
        checked = self._valid(sample)
        if checked is None:
            return
        t, lap, speed, x, z = checked
        self._prune(t, conn)
        car = self._cars.get(conn)
        if car is not None and car.t is not None and t <= car.t:
            return                                # not newer than what this car already sent
        if car is not None and car.entry != entry:
            self._move(car, entry)
        ent = self._entries.get(entry)
        resumed = None
        if ent is None and entry in self._parked:
            ent = self._entries[entry] = self._parked.pop(entry)
            ent.active, resumed, ent.parked = conn, ent.parked, None
        elif ent is None:
            ent = self._entries[entry] = _Entry(entry, conn)
            ent.adjust, ent.pit_adjust, ent.rejoin = self._preset.pop(entry, [0.0, 0, False])
        if car is None:
            car = self._cars[conn] = _Car(conn, name)
            car.entry = entry
            car.restart_ready = ent.waiting                # it joined the restart lobby
        if entry in self._est and conn == ent.active and (car.t is None or t - car.t > NO_DATA_S):
            del self._est[entry]                           # the car is back: its own distance
            self._ended_est.append(entry)
        if resumed is not None and resumed[0] == conn and resumed[1][0]:
            car.counting = True                            # moved back: its own laps go on
            _, car.anchor, car.lap0, car.f0, car.line = resumed[1]
        if sample.get("loading") or sample.get("paused") or self._line is None \
                or x is None or lap is None:
            car.t = t
            return
        _i, lateral, s = self._line.locate(x, z)
        active = conn == ent.active
        prev_lap = car.lap
        if prev_lap is not None and lap < prev_lap and ent.waiting:
            car.restart_ready = True              # the restart lobby after a red flag
        elif active and prev_lap is not None and lap < prev_lap and ent.finished_at is None:
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
        car.x, car.z = x, z
        fuel = sample.get("fuel_l")
        car.fuel = fuel if self._finite(fuel) and 0 <= fuel <= 1000 else None
        last_ms = sample.get("last_ms")
        new_lap_time = type(last_ms) is int and last_ms > 0 and last_ms != car.last_ms
        if type(last_ms) is int:
            car.last_ms = last_ms
        self._box(car, t)
        if active and car.left_box and car.counting:
            ent.pit_stops += 1
        if ent.waiting:
            if car.restart_ready and car.left_box and lap <= MAX_CHANGE_LAP:
                old = self._cars.get(ent.active)
                if old is not None and old is not car:
                    old.counting = False
                if self._start(ent, car, t, self._anchor_of(ent), lap, s, from_line=False):
                    ent.active, ent.waiting, ent.held = conn, False, False
                    ent.ts.append(t)
                    ent.ds.append(ent.distance)
            return
        if self.multi_lobby:
            self._change(ent, car, t)
            if conn != ent.active or not active:
                return
        if not active or ent.finished_at is not None or ent.held:
            return
        before = ent.distance
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
        self._trim(ent, t)
        self._lap_timing(ent, car, t, prev_lap is not None and lap > prev_lap,
                         last_ms if new_lap_time else None)
        # A single lobby counts laps in race distance: after a red flag restart from the
        # box the restart lobby's line no longer sits on a whole lap of it.
        if self.multi_lobby:
            crossed = prev_lap is not None and lap > prev_lap
        else:
            crossed = before is not None and ent.distance is not None and \
                math.floor(ent.distance / self.length) > math.floor(before / self.length)
        if crossed:
            self._crossing(ent, car, t)

    def min_lap_s(self):
        """The shortest believable lap: the line at MAX_SPEED_MPS."""
        return self.length / MAX_SPEED_MPS if self.length else math.inf

    def _lap_timing(self, ent, car, t, line, last_ms):
        """Time the active counting car's laps and take GT7's time when it agrees."""
        if line:
            if car.lap_ok:
                ent.stint_laps += 1               # a lap counted from its own line
            if car.lap_ok and car.line_t is not None:
                car.pending = (t - car.line_t, t)
            car.line_t, car.lap_ok = t, True
        if car.pending is not None and t - car.pending[1] > LAP_TIME_WAIT_S:
            car.pending = None
        if last_ms is None or car.pending is None:
            return
        measured = car.pending[0]
        car.pending = None
        if measured < self.min_lap_s():
            return                                # faster than any car can drive the line
        if abs(last_ms / 1000 - measured) > max(LAP_TIME_TOLERANCE[0], LAP_TIME_TOLERANCE[1] * measured):
            return
        ent.last_lap_ms = last_ms
        if self.fastest is None or last_ms < self.fastest["ms"]:
            self.fastest = {"entry": ent.entry, "driver": car.name, "ms": last_ms}

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
        if ent.rejoin and window and car.left_box and car.lap <= MAX_CHANGE_LAP \
                and self._window_leader is not None:
            old = self._cars.get(ent.active)
            if old is not None and old is not car:
                old.counting = False
            if self._start(ent, car, t, self._window_leader - ent.adjust, car.lap, car.s,
                           from_line=False):
                ent.active, ent.rejoin = car.conn, False
                self._used_rejoins.append(ent.entry)
                self._changed(ent, t)
            return
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
        # The order Race Control's corrections set is the order that takes the flag.
        shown = self._shown(ent)
        leader = max((self._shown(e) for e in self._entries.values() if e.distance is not None),
                     default=None)
        if shown is None or shown < leader:
            return
        if self.finish_mode == "laps" and self.lap_count:
            by_laps = (car.lap > self.lap_count and self._final_car(ent)) if self.multi_lobby \
                else shown >= (self.lap_count - (1 if self.formation else 0)) * self.length - SNAP_M
        else:
            by_laps = False
        by_flag = self._final_lap_at is not None and t > self._final_lap_at
        if by_laps or by_flag:
            ent.finished_at = self._finishing_at = t
            self._finished = len(self._entries) == 1

    def _shown(self, ent):
        est = self._est.get(ent.entry)
        if est is not None and est["on"]:
            return est["held"]
        return None if ent.distance is None else ent.distance + ent.adjust

    def _est_xy(self, ent):
        """Map position of an estimated car: its reference's spot, set back by the gap."""
        ref = self._entries.get(self._est[ent.entry]["ref"])
        car = self._cars.get(ref.active) if ref is not None else None
        shown, ahead = self._shown(ent), self._shown(ref) if ref is not None else None
        if car is None or car.s is None or shown is None or ahead is None:
            last = self._cars.get(ent.active)
            return (last.x, last.z) if last is not None else (None, None)
        return self._line.point_at(car.s - (ahead - shown))

    def _estimated(self, ent):
        est = self._est.get(ent.entry)
        return est is not None and est["on"]

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
    def _trim(ent, t):
        """Keep HISTORY_S at full rate; older samples move to the coarse history."""
        cut = bisect.bisect_left(ent.ts, t - HISTORY_S) if ent.ts[0] < t - HISTORY_S else 0
        cut = max(cut, len(ent.ts) - MAX_HISTORY)
        if cut <= 0:
            return
        for ts, d in zip(ent.ts[:cut], ent.ds[:cut], strict=True):
            if not ent.cts or ts - ent.cts[-1] >= COARSE_STEP_S:
                ent.cts.append(ts)
                ent.cds.append(d)
        del ent.ts[:cut], ent.ds[:cut]
        if len(ent.cts) > MAX_COARSE:
            del ent.cts[:len(ent.cts) - MAX_COARSE], ent.cds[:len(ent.cds) - MAX_COARSE]

    @staticmethod
    def _time_at(ent, d):
        """When `ent` reached distance d, interpolated; None outside its history."""
        return _across(ent.ts, ent.ds, ent.cts, ent.cds, d)

    @staticmethod
    def _distance_at(ent, t):
        """`ent`'s distance at time t, interpolated; its last one after its history,
        None before it."""
        last = ent.ts[-1] if ent.ts else (ent.cts[-1] if ent.cts else None)
        if last is not None and t >= last:
            return ent.ds[-1] if ent.ds else ent.cds[-1]
        return _across(ent.ds, ent.ts, ent.cds, ent.cts, t)

    def _behind(self, ahead, ent):
        """(laps behind, seconds behind) of `ent` relative to `ahead`."""
        if ahead.distance is None or ent.distance is None:
            return None, None
        laps = math.floor((self._shown(ahead) - self._shown(ent)) / self.length)
        if laps >= 1:
            return laps, None
        return 0, self._time_behind(ahead, ent)

    def _time_behind(self, ahead, ent, now=None):
        """Seconds `ent` is behind `ahead` by distance history, also when lapped; None
        without a running time (red flag) or history for that distance, and with `now`
        also while `ahead` is quiet (its history is stale)."""
        if ahead.distance is None or ent.distance is None:
            return None
        if ent.finished_at is not None:
            # Against when `ahead` passed that point: a car ranked ahead can take the flag later.
            reached = self._time_at(ahead, ent.distance + ent.adjust - ahead.adjust)
            if reached is None:
                reached = ahead.finished_at
            return None if reached is None else max(0.0, ent.finished_at - reached)
        if ent.waiting or ahead.waiting:
            return None                            # no running time while a red flag holds them
        if now is not None and self._quiet(ahead, now):
            return None
        reached = self._time_at(ahead, ent.distance + ent.adjust - ahead.adjust)
        return None if reached is None else max(0.0, self._cars[ent.active].t - reached)

    def standings(self, now):
        self._advance(now)
        ents = list(self._entries.values())
        winner = next((e for e in ents if self._finishing_at is not None
                       and e.finished_at == self._finishing_at and e.distance is not None), None)
        # No car is classified with more laps than the winner took the flag on.
        cap = math.floor(self._shown(winner) / self.length) if winner and self.length else math.inf

        def key(e):
            shown = self._shown(e)
            dist = shown if shown is not None else -math.inf
            if self._finishing_at is None:
                # An estimate never passes its reference on a tie.
                return (0, -dist, 1.0 if self._estimated(e) else 0.0, 0.0, "")
            if e is winner:
                return (-1, 0, 0.0, 0.0, "")
            laps = min(math.floor(dist / self.length), cap) if self.length and shown is not None else -1
            # Whoever reached that lap count first is ahead, finished or still running.
            reached = self._time_at(e, laps * self.length - e.adjust) if laps >= 0 else None
            est = self._est.get(e.entry) if self._estimated(e) else None
            ref = self._entries.get(est["ref"]) if est else None
            if ref is not None and laps >= 0:
                at = self._time_at(ref, laps * self.length - ref.adjust)
                reached = None if at is None else at + est["seconds"]
            if reached is None:
                reached = e.finished_at if e.finished_at is not None else math.inf
            return (0, -laps, reached, -dist, e.entry)
        ents.sort(key=key)
        mismatch = self._mismatches(ents)
        rows = []
        for i, e in enumerate(ents):
            car = self._cars[e.active]
            quiet = self._quiet(e, now)
            lapped = gap = interval = gap_time = interval_time = None
            if e.distance is not None and not quiet:
                lapped, gap = self._behind(ents[0], e) if i else (0, 0)
                interval = self._behind(ents[i - 1], e)[1] if i else None
                gap_time = self._time_behind(ents[0], e, now) if i else 0.0
                interval_time = self._time_behind(ents[i - 1], e, now) if i else None
            shown = self._shown(e)
            est = self._est.get(e.entry)
            rows.append({
                "entry": e.entry, "driver": car.name, "conn": e.active,
                "distance_m": None if shown is None else round(shown, 1),
                "laps": max(0, math.floor(shown / self.length)) if shown is not None else 0,
                "gap_s": None if lapped else (None if gap is None else round(gap, 2)),
                "interval_s": None if interval is None else round(interval, 2),
                "gap_time_s": None if gap_time is None else round(gap_time, 2),
                "interval_time_s": None if interval_time is None else round(interval_time, 2),
                "quiet_s": None if car.t is None else round(max(0.0, now - car.t), 1),
                "in_box": bool(car.in_box),
                "lapped": lapped or 0,
                "status": ("est" if self._estimated(e) else "no_data") if quiet else
                          "box" if car.in_box else
                          "waiting" if e.distance is None else
                          "red_flag" if e.waiting else "racing",
                "finished": e.finished_at is not None,
                "waiting": e.waiting,
                "pit_stops": max(0, e.pit_stops + e.pit_adjust),
                "correction_m": round(e.adjust, 1),
                "last_lap_ms": e.last_lap_ms, "stint_laps": e.stint_laps,
                "fuel_l": None if car.fuel is None else round(car.fuel, 1),
                **dict(zip(("x", "z"), self._est_xy(e) if self._estimated(e) and quiet
                                       else (car.x, car.z), strict=True)),
                "rejoin": e.rejoin,
                "gt7_position": car.position,
                "position_mismatch": e.entry in mismatch,
                "estimate": None if est is None else {"ref": est["ref"], "seconds": est["seconds"]}})
        self._estimate_gaps(ents, rows)
        red = None if self._red is None else dict(self._red)
        return {"state": self._state(), "line_length_m": self.length, "red_flag": red,
                "restart_mode": self._restart_mode,
                "change_window": self._window_until is not None and now <= self._window_until,
                "fastest": self.fastest,
                "lap": self._lap_display(ents), "entries": rows}

    def _estimate_gaps(self, ents, rows):
        """An estimated car's gaps are its reference's plus the seconds; the intervals
        next to it follow from the gaps."""
        by_entry = {r["entry"]: r for r in rows}
        lead = self._shown(ents[0]) if ents else None
        for i, (e, row) in enumerate(zip(ents, rows, strict=True)):
            if self._estimated(e):
                est = self._est[e.entry]
                ref = by_entry.get(est["ref"]) or {}
                lapped = math.floor((lead - est["held"]) / self.length) if i and lead is not None else 0
                gap = None if lapped or ref.get("gap_s") is None else ref["gap_s"] + est["seconds"]
                gap_time = None if ref.get("gap_time_s") is None else ref["gap_time_s"] + est["seconds"]
                row.update(lapped=max(0, lapped), gap_s=None if gap is None else round(gap, 2),
                           gap_time_s=None if gap_time is None else round(gap_time, 2))
            elif not (i and self._estimated(ents[i - 1])):
                continue
            if i:
                prev = rows[i - 1]
                for key, by in (("interval_s", "gap_s"), ("interval_time_s", "gap_time_s")):
                    a, b = row[by], prev[by]
                    row[key] = None if a is None or b is None else round(max(0.0, a - b), 2)

    def _lap_display(self, ordered):
        """{leader, count}: the leader's running lap against the lap count. In endurance
        the count belongs to the final lobby, so it shows only with that lobby's car and
        its lobby counter; never a lap past the count once the flag is out."""
        count = self.lap_count if self.finish_mode == "laps" else None
        lead = ordered[0] if ordered and ordered[0].distance is not None else None
        if lead is None:
            return {"leader": None, "count": count}
        car = self._cars.get(lead.active)
        if self.multi_lobby and count and self._final_car(lead) and car is not None and car.lap is not None:
            leader = car.lap
        else:
            leader = max(0, math.floor(self._shown(lead) / self.length)) + (2 if self.formation else 1)
            if self.multi_lobby:
                count = None
        if count and self._finishing_at is not None:
            leader = min(leader, count)
        return {"leader": leader, "count": count}

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

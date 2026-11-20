"""Race distance, order, gaps, driver changes and finish from driver telemetry (#1112).

Pure: the relay feeds every decoded sample of every driver connection with its
receive time on one clock, and reads `standings`. GT7's lap counter reads 0 on the
grid and steps at each crossing of the finish line. The finish line's station on the
picked layout's line is learned per car from its own crossings; a car that has not
crossed yet uses the agreeing medoid of the counting cars of at least LINE_QUORUM
entries, so one sender cannot move it for everyone; once agreed it stays.
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
  from the box keeps the count; `final_lap` ends it at the leader's next crossing.
  Endurance follows the final lobby on the road, as GT7 does: the first of its cars
  (each took over after the final stint switch) to pass `lap_count` on that lobby's
  counter, or after `final_lap` the first to cross on the lobby's leading counter,
  takes the flag, whoever leads in race distance. Every other car finishes at its
  next crossing, timed between its samples around the line, so a crossing after the
  flag that arrived before it still counts; `finish` freezes everything at once. The race timer never ends a race.
  From the first flag on the order is laps, then who reached that lap count first,
  so a lapped car taking the flag stays behind a lead-lap car still running. The first
  flag's car stays first when it led in race distance; no car counts more laps than
  the leader had at the flag, and a finished car's gap is measured at that lap count.
- Red flag (#1113): `red_flag` freezes every entry's distance. In the restart lobby
  (a new connection, or a counter that dropped) each car continues from its anchor
  when it leaves the box. `keep` is a release staggered by the gaps: every car
  anchors at the leader's frozen distance less its whole laps behind, and its later
  box exit is its gap. `neutralise` anchors every car of a lap at that lap's best
  distance. Gaps leave out the stoppage, from the red flag to the first box exit.
  `release` ends the mode: a car still in its old session counts on from where it
  is, one in a restart lobby still resumes at its box exit.
- Rejoin (endurance): a marked team's next car to leave the box inside the change
  window starts from the leader's distance where its old lobby's car stopped (where
  the leader is, while it still runs).
- Lap times (#1115): the relay times every lap of the active car between two steps
  of GT7's counter. A lap counts only when the car counted for all of it (not the
  formation lap, not lap 1 of a follow-up lobby, not a lap with a start, red flag,
  change or move in it), and GT7's `last_ms` for it is taken only when it agrees with
  the relay's own timing within LAP_TIME_TOLERANCE. The fastest of those is the race's
  fastest lap.
- Corrections add metres and pit stops per entry; `override_change` makes a team's
  connection active at once, continuing from the current distance.
- Estimate: Race Control puts a quiet car S seconds behind or ahead of a reference car.
  While it stays quiet it shows the reference's distance S seconds ago (behind) or the
  reference's distance plus what the reference covered in the last S seconds (ahead),
  frozen under a red flag (its restart anchor) and after the flag; its next data ends
  the estimate. Before the reference's history covers S, behind holds at the
  reference's first known distance and ahead adds what the reference covered since it.
  An estimated car never takes the flag and never keeps a real car from taking it.
- Roster: every entry of `set_roster` stands in the order; one that never sent reads
  `no_sender` at the end until a sender connects or an estimate places it.
- Names: Race Control may replace the shown name of one driver of an entry.
- Retirement: a retired car (DNF) stops counting and ranks below every running car,
  retired cars among themselves by distance; undoing it counts on from that distance.
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
GAP_SPEED_MPS = 10.0        # below this a gap from distance and speed means nothing


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


_NO_CAR = _Car(None, "")      # stands in for the car of an entry that never sent


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
        self.stint_end = None      # change window: the shown distance where its old car stopped
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
        self.resume = False        # an undone retirement: count on from `distance`
        self.crossed = None        # (interpolated time, sample time) of its last crossing

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
        self._winner_entry = None   # the first flag's car when it led in race distance
        self._flag_laps = math.inf
        self._finished = False
        self._pruned_at = None
        self._red = None            # {"at", "mode"} while the red flag mode runs
        self._stopped = None        # red flag: when the stoppage began, until the first restart
        self._pauses = []           # (from, to) of each stoppage, left out of every gap
        self._restart_mode = "keep"
        self._preset = {}           # entry -> [metres, pit stops, rejoin] for entries not seen yet
        self._parked = {}           # entries whose only car was moved to another entry
        self._used_rejoins = []     # entries whose rejoin mark a change consumed
        self.fastest = None         # {entry, driver, ms}
        self.fastest_by_class = {}  # class name -> {entry, driver, ms}
        self._classes = {}          # entry -> class name
        self._class_laps = {}       # class name -> the lap count Race Control set
        self._est = {}              # entry -> {ref, seconds, ahead, held, lag, on}
        self._roster = []           # the Configuration entries, in order
        self._retired = set()
        self._names = {}            # (entry, registered driver or "") -> the name Race Control set
        self._ended_est = []        # entries whose estimate their returning data ended
        self._shared_line = None    # the line station a quorum of counting cars agreed on

    # -- commands ------------------------------------------------------------------
    def set_roster(self, entries):
        """The Configuration entries in order; each stands in the standings, with or
        without a sender."""
        self._roster = [e for e in entries if isinstance(e, str)]

    def set_classes(self, classes):
        """Each entry's class name; an entry missing or mapped to "" has none."""
        self._classes = {e: c for e, c in classes.items() if isinstance(e, str) and isinstance(c, str) and c}

    def set_class_laps(self, laps):
        """Each class's lap count as Race Control set it; display only, it never
        finishes a car."""
        self._class_laps = dict(laps)

    def class_of(self, entry):
        """The entry's class name, "" without one."""
        return self._classes.get(entry, "")

    def set_driver_name(self, entry, driver, name):
        """Show `name` for the driver registered as `driver` ("" for an entry without a
        sender); a falsy name removes it."""
        if name:
            self._names[(entry, driver)] = name
        else:
            self._names.pop((entry, driver), None)

    def display_name(self, entry, driver):
        return self._names.get((entry, driver), driver)

    def retire(self, entry, on, now):
        """Mark a car retired (DNF), or undo it: the car then counts on from its last
        distance. Returns the entries whose estimate followed the retired car and ended."""
        self._advance(now)
        ent = self._entries.get(entry)
        ended = []
        if on:
            self._retired.add(entry)
            self._est.pop(entry, None)
            ended = [e for e, est in self._est.items() if est["ref"] == entry]
            for e in ended:
                del self._est[e]
            if ent is not None:
                ent.resume, ent.waiting, ent.frozen = False, False, None
                car = self._cars.get(ent.active)
                if car is not None:
                    car.counting, car.lap_ok, car.pending = False, False, None
        elif entry in self._retired:
            self._retired.discard(entry)
            if ent is not None and self._finished and ent.finished_at is None:
                ent.finished_at = now              # a finish by hand classifies it where it stopped
            elif ent is not None and ent.distance is not None:
                ent.resume = True
        self._settle_stoppage()
        return ended

    def _out(self, ent):
        return ent.entry in self._retired

    def _ghost(self, entry):
        """A row for a roster entry the race has not seen, with its preset corrections."""
        ent = _Entry(entry, None)
        ent.adjust, ent.pit_adjust, ent.rejoin = self._preset.get(entry, [0.0, 0, False])
        return ent

    def open_change_window(self, now):
        if self.multi_lobby:
            self._window_until = now + CHANGE_WINDOW_S
            for ent in self._entries.values():
                ent.changed, ent.stint_end = False, None

    def red_flag(self, now, mode):
        """Freeze every entry; each continues from its anchor in the restart lobby."""
        self._advance(now)
        self._red = {"at": now, "mode": mode}
        self._restart_mode = mode
        if self._stopped is None:                  # a second red flag keeps the first stoppage
            self._stopped = max((c.t for c in self._cars.values() if c.t is not None), default=now)
        for ent in self._entries.values():
            if ent.waiting or self._out(ent):
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
        self._settle_stoppage()

    def _settle_stoppage(self, end=None):
        """Drop an open stoppage that no car in a restart lobby waits out: the field
        raced on in its own lobby. `end` (a finish) closes one still running there."""
        if self._stopped is None:
            return
        waited = self._red is not None or any(
            e.waiting and not self._out(e) and c is not None and c.restart_ready
            for e in self._entries.values() for c in [self._cars.get(e.active)])
        if end is not None and waited:
            self._close_stoppage(end)
        elif not waited:
            self._stopped = None

    def _close_stoppage(self, t):
        """End the stoppage at sample time `t`, the time base it began on."""
        if t > self._stopped:
            self._pauses.append((self._stopped, t))
        self._stopped = None

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

    def estimate(self, entry, ref, seconds, now, ahead=False):
        """Show the quiet `entry` `seconds` behind `ref`, or ahead of it, until its data
        returns; setting it again replaces it. ValueError when it cannot apply."""
        ent, other = self._entries.get(entry), self._entries.get(ref)
        if entry == ref:
            raise ValueError("an entry cannot follow itself")
        if ent is None and entry not in self._roster and entry not in self._parked:
            raise ValueError("that entry is not in the race")
        if entry in self._retired or ref in self._retired:
            raise ValueError("no estimate for or behind a retired car")
        if other is None or other.distance is None or self._quiet(other, now):
            raise ValueError("the reference needs a car with data")
        if self._red is not None:
            raise ValueError("no estimate under a red flag")
        if self._finished or (ent is not None and ent.finished_at is not None) \
                or other.finished_at is not None:
            raise ValueError("no estimate after the flag")
        if ent is None:
            ent = self._adopt(entry)
        self._est[entry] = {"ref": ref, "seconds": float(seconds), "ahead": bool(ahead),
                            "held": None, "lag": float(seconds), "on": False}
        self._advance(now)
        if self._quiet(ent, now) and not self._est[entry]["on"]:
            del self._est[entry]
            raise ValueError("the reference has no distance to follow yet")

    def _adopt(self, entry):
        """The entry, created or taken back from the parked ones, without a car yet."""
        if entry in self._parked:
            ent = self._entries[entry] = self._parked.pop(entry)
            ent.active = None
        else:
            ent = self._entries[entry] = _Entry(entry, None)
            ent.adjust, ent.pit_adjust, ent.rejoin = self._preset.pop(entry, [0.0, 0, False])
        return ent

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
                then = self._wall(self._racing(now) - est["seconds"])
                d, lag = self._distance_at(ref, then), est["seconds"]
                first = self._first(ref)
                if d is None and first is not None:   # its history starts later: hold there
                    d, lag = first[1], now - first[0]
                if d is not None and est["ahead"]:
                    d = 2 * ref.distance - d
                if d is not None:
                    est["held"], est["lag"] = d + ref.adjust, lag
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
        ent.stint_end = ent.distance + ent.adjust
        return True

    def _anchor_of(self, ent):
        """Where an entry continues after the red flag, corrections included. Keep
        starts every car from the leader's frozen distance less its whole laps behind,
        since Race Control releases the cars by their gaps. Neutralise levels the cars
        the tower shows on one lap."""
        frozen = [e.frozen + e.adjust for e in self._entries.values() if e.frozen is not None]
        own = ent.frozen + ent.adjust
        if self._restart_mode == "neutralise":
            lap = math.floor(own / self.length)
            level = max(f for f in frozen if math.floor(f / self.length) == lap)
            return level - ent.adjust
        lead = max(frozen)
        return lead - math.floor((lead - own) / self.length) * self.length - ent.adjust

    def final_lobby(self, lap_count, now):
        """Endurance: the final stint lobby is on air and runs `lap_count` laps."""
        self.lap_count, self._final_at = lap_count, now

    def final_lap(self, now):
        if self._final_lap_at is None and not self._finished:
            self._final_lap_at = now

    def finish(self, now):
        self._advance(now)
        for ent in self._entries.values():
            if ent.finished_at is None and not self._out(ent) \
                    and (ent.active is not None or self._estimated(ent)):
                ent.finished_at = now
        self._finished = True
        self._settle_stoppage(max((c.t for c in self._cars.values() if c.t is not None), default=now))

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
        """This car's line station; else the field's shared one (`_agree`)."""
        if car.line_s is not None:
            return car.line_s
        return self._agree()

    def _agree(self):
        """The medoid of the counting cars of at least LINE_QUORUM entries when most of
        them agree within SNAP_M. Once found it stays the fallback, because a driver
        change swaps every counting car for one without a crossing of its own; None
        before any quorum agreed."""
        known = []
        for e in self._entries.values():
            c = self._cars.get(e.active)
            if c is not None and c.counting and c.line_s is not None:
                known.append(c.line_s)
        if len(known) < LINE_QUORUM:
            return self._shared_line
        medoid = min(known, key=lambda a: sum(self._circ(a, b) for b in known))
        close = sum(1 for b in known if self._circ(medoid, b) <= SNAP_M)
        if 2 * close > len(known):
            self._shared_line = medoid
        return self._shared_line

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
        self._agree()
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
        if car is not None:
            car.name = name                       # an approval may correct it, whatever the sample's time
        if car is not None and car.t is not None and t <= car.t:
            return                                # not newer than what this car already sent
        if car is not None and car.entry != entry:
            self._move(car, entry)
        ent = self._entries.get(entry) or self._adopt(entry)
        resumed = None
        if ent.active is None:
            ent.active, resumed, ent.parked = conn, ent.parked, None
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
        elif active and prev_lap is not None and lap < prev_lap and ent.finished_at is None \
                and not self._out(ent):
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
        if self._out(ent):
            return
        if ent.waiting:
            if car.restart_ready and car.left_box and lap <= MAX_CHANGE_LAP:
                old = self._cars.get(ent.active)
                if old is not None and old is not car:
                    old.counting = False
                if self._start(ent, car, t, self._anchor_of(ent), lap, s, from_line=False):
                    ent.active, ent.waiting, ent.held = conn, False, False
                    if self._stopped is not None:  # the first car out ends the stoppage
                        self._close_stoppage(t)
                    ent.ts.append(t)
                    ent.ds.append(ent.distance)
            return
        if self.multi_lobby:
            self._change(ent, car, t)
            if conn != ent.active or not active:
                return
        if not active or ent.finished_at is not None or ent.held:
            return
        before, before_t = ent.distance, ent.ts[-1] if ent.ts else None
        if not car.counting and ent.resume:
            if not self._start(ent, car, t, ent.distance, lap, s, from_line=False):
                return
            ent.resume = False
        elif not car.counting:
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
            self._crossing(ent, car, t, self._crossed_at(ent, car, s, before, before_t, t))

    def _crossed_at(self, ent, car, s, before, before_t, t):
        """When the car passed the line between its previous sample and this one."""
        if before is None or before_t is None or ent.distance is None or ent.distance <= before:
            return t
        if self.multi_lobby:
            frac = self._frac(car, s)
            line = ent.distance - frac if frac < self.length / 2 else ent.distance + self.length - frac
        else:
            line = math.floor(ent.distance / self.length) * self.length
        return before_t + (t - before_t) * min(1.0, max(0.0, (line - before) / (ent.distance - before)))

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
        lap = {"entry": ent.entry, "driver": car.name, "ms": last_ms}
        if self.fastest is None or last_ms < self.fastest["ms"]:
            self.fastest = dict(lap)
        cls = self._classes.get(ent.entry, "")
        if cls not in self.fastest_by_class or last_ms < self.fastest_by_class[cls]["ms"]:
            self.fastest_by_class[cls] = lap

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
        anchor = self._rejoin_anchor(ent) if ent.rejoin and window and car.left_box \
            and car.lap <= MAX_CHANGE_LAP else None
        if anchor is not None:
            old = self._cars.get(ent.active)
            if old is not None and old is not car:
                old.counting = False
            if self._start(ent, car, t, anchor - ent.adjust, car.lap, car.s, from_line=False):
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

    def _rejoin_anchor(self, ent):
        """The leader's distance where its car of the old lobby stopped: the stint end
        of a team that changed, else where the team's car is now (standing in the box,
        or still on track); estimated cars move with another team and never count. None
        before any car counts."""
        return max((e.stint_end if e.stint_end is not None else self._shown(e)
                    for e in self._entries.values()
                    if e is not ent and e.distance is not None and not self._out(e)
                    and not self._estimated(e)), default=None)

    def _changed(self, ent, t):
        ent.stint_end = ent.distance + ent.adjust
        ent.change_anchor, ent.changed, ent.held, ent.relobby = None, True, False, False
        ent.ts.append(t)
        ent.ds.append(ent.distance)
        if all(e.changed for e in self._entries.values()
               if e.active is not None and not self._out(e)):
            self._window_until = None

    def _final_car(self, ent):
        return self._final_at is not None and ent.since is not None and ent.since >= self._final_at

    def _crossing(self, ent, car, t, at):
        """`at`: when the car passed the line, between its previous sample and `t`."""
        ent.crossed = (at, t)
        if self._finished:
            return
        if self._finishing_at is not None:
            if ent.finished_at is None and t > self._finishing_at:
                ent.finished_at = t
                self._finished = all(e.finished_at is not None or self._quiet(e, t) or self._out(e)
                                     for e in self._entries.values())
            return
        if self.multi_lobby and self._final_car(ent):
            # GT7 ends the final lobby on the road, which after a red flag restart need
            # not follow race distance.
            by_laps = self.finish_mode == "laps" and self.lap_count and car.lap > self.lap_count
            by_flag = self._final_lap_at is not None and t > self._final_lap_at \
                and car.lap >= self._lobby_lead_lap(t)
            if by_laps or by_flag:
                self._flag(ent, t, at)
            return
        # The order Race Control's corrections set is the order that takes the flag.
        shown, leader = self._shown(ent), self._race_lead(real=True)
        if shown is None or (leader is not None and shown < leader):
            return
        by_laps = not self.multi_lobby and self.finish_mode == "laps" and self.lap_count \
            and shown >= (self.lap_count - (1 if self.formation else 0)) * self.length - SNAP_M
        by_flag = self._final_lap_at is not None and t > self._final_lap_at
        if by_laps or by_flag:
            self._flag(ent, t, at)

    def _race_lead(self, real=False):
        """The leading shown distance, None before any car counts; `real` leaves out
        estimated cars."""
        return max((self._shown(e) for e in self._entries.values()
                    if e.distance is not None and not self._out(e)
                    and not (real and self._estimated(e))), default=None)

    def _flag(self, ent, t, at=None):
        """`ent` takes the first flag, passing the line at `at`. It is the winner when it
        leads in race distance; no car is classified with more race laps than the leader
        has now. A car whose crossing after `at` arrived first finishes there."""
        lead, shown = self._race_lead(), self._shown(ent)
        self._winner_entry = ent if shown is not None and shown >= lead else None
        self._flag_laps = math.floor(lead / self.length) if lead is not None else math.inf
        ent.finished_at = self._finishing_at = t
        at = t if at is None else at
        late = [e for e in self._entries.values()
                if e is not ent and e.finished_at is None and not self._out(e)
                and e.crossed is not None and e.crossed[0] > at]
        for e in late:
            e.finished_at = e.crossed[1]
        self._finished = sum(1 for e in self._entries.values()
                             if e.active is not None and not self._out(e)) == 1 \
            or bool(late) and all(e.finished_at is not None or self._quiet(e, t) or self._out(e)
                                  for e in self._entries.values())

    def _lobby_lead_lap(self, now, entries=None):
        """The highest GT7 counter among the final lobby's running cars (of `entries`)."""
        laps = [c.lap for e in (self._entries.values() if entries is None else entries)
                if not self._out(e) and not e.waiting and self._final_car(e) and not self._quiet(e, now)
                for c in [self._cars.get(e.active)] if c.counting and c.lap is not None]
        return max(laps, default=0)

    def _shown(self, ent):
        est = self._est.get(ent.entry)
        if est is not None and est["on"]:
            return est["held"]
        return None if ent.distance is None else ent.distance + ent.adjust

    def _est_xy(self, ent):
        """Map position of an estimated car: its reference's spot, moved by the gap."""
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

    def _racing(self, t):
        """`t` on the racing clock: a red flag stoppage, until the first car leaves the
        restart lobby's box, does not count."""
        open_ = t - self._stopped if self._stopped is not None and t > self._stopped else 0.0
        return t - open_ - sum(min(t, b) - a for a, b in self._pauses if t > a)

    def _wall(self, r):
        """The time at which the racing clock read `r`."""
        for a, b in self._pauses:
            if r > a:
                r += b - a
        return r

    @staticmethod
    def _first(ent):
        """(time, distance) where `ent`'s history begins; None without one."""
        if ent.cts:
            return ent.cts[0], ent.cds[0]
        return (ent.ts[0], ent.ds[0]) if ent.ts else None

    @staticmethod
    def _last(ent):
        """(time, distance) where `ent`'s history ends; None without one."""
        if ent.ts:
            return ent.ts[-1], ent.ds[-1]
        return (ent.cts[-1], ent.cds[-1]) if ent.cts else None

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
        laps = self._laps_behind(ahead, ent)
        if laps >= 1:
            return laps, None
        return 0, self._time_behind(ahead, ent)

    def _laps_behind(self, ahead, ent):
        """Whole laps `ent` is behind `ahead`; for a car that took the flag the laps the
        order counts, since a frozen finish distance overshoots the line by chance."""
        if ent.finished_at is not None and self._finishing_at is not None:
            return max(0, self._ranked_laps(ahead) - self._ranked_laps(ent))
        shown = self._shown(ent)
        if ahead.finished_at is not None and not self._estimated(ent):
            # Against a frozen finish the laps at that moment, not a distance it closes on.
            then = self._distance_at(ent, ahead.finished_at)
            shown = shown if then is None else then + ent.adjust
        return math.floor((self._shown(ahead) - shown) / self.length)

    def _time_behind(self, ahead, ent, now=None):
        """Seconds `ent` is behind `ahead` by distance history, also when lapped; None
        without a running time (red flag) or history for that distance, and with `now`
        also while `ahead` is quiet (its history is stale)."""
        if ahead.distance is None or ent.distance is None:
            return None
        if ent.finished_at is not None:
            # At the lap count the order compares: a car ranked ahead can take the flag later.
            laps = self._ranked_laps(ent)
            if laps is not None and laps > 0:
                mine = self._time_at(ent, laps * self.length - ent.adjust)
                theirs = self._time_at(ahead, laps * self.length - ahead.adjust)
                if mine is not None and theirs is not None:
                    return max(0.0, self._racing(mine) - self._racing(theirs))
            reached = self._time_at(ahead, ent.distance + ent.adjust - ahead.adjust)
            return None if reached is None else max(0.0, self._racing(ent.finished_at) - self._racing(reached))
        if ent.waiting or ahead.waiting:
            return None                            # no running time while a red flag holds them
        if now is not None and self._quiet(ahead, now):
            return None
        at = ent.distance + ent.adjust - ahead.adjust
        last = self._last(ahead) if ahead.finished_at is not None else None
        if last is not None and at >= last[1]:
            # Past a finished car's last point: the gap where this car passed it.
            mine = self._time_at(ent, last[1] + ahead.adjust - ent.adjust)
            if mine is not None:
                return max(0.0, self._racing(mine) - self._racing(last[0]))
        reached = self._time_at(ahead, at)
        if reached is None:
            return self._gap_by_speed(ahead, ent)
        return max(0.0, self._racing(self._cars[ent.active].t) - self._racing(reached))

    def _gap_by_speed(self, ahead, ent):
        """The distance to `ahead` at `ent`'s speed: an estimate while `ahead`'s history
        does not reach back to `ent` (both started counting mid-lap); None when slow."""
        car = self._cars.get(ent.active)
        if car is None or car.speed < GAP_SPEED_MPS:
            return None
        return max(0.0, self._shown(ahead) - self._shown(ent)) / car.speed

    def _winner(self):
        """The car classified first after the flag regardless of laps: the first flag's
        car when it led in race distance. An endurance flag can fall on the road ahead
        of the distance leader after a red flag restart; then the order alone decides."""
        e = self._winner_entry
        if self._finishing_at is None or e is None or e.distance is None or self._out(e) \
                or (e.active is None and not self._estimated(e)):
            return None
        return e

    def _ranked_laps(self, ent):
        """The race laps the order counts for `ent`; after the flag never more than the
        winner's, or without one the leader's when the flag fell."""
        shown = self._shown(ent)
        if shown is None or not self.length:
            return None
        laps = math.floor(shown / self.length)
        if self._finishing_at is None:
            return laps
        winner = self._winner()
        cap = math.floor(self._shown(winner) / self.length) if winner is not None else self._flag_laps
        return min(laps, cap)

    def standings(self, now):
        self._advance(now)
        place = {n: i for i, n in enumerate(self._roster)}
        live = [e for e in self._entries.values() if not self._out(e)]
        ents = [e for e in live if e.active is not None or self._estimated(e)]
        idle = [e for e in live if e.active is None and not self._estimated(e)] + \
            [self._ghost(n) for n in self._roster if n not in self._entries and n not in self._retired]
        idle.sort(key=lambda e: place.get(e.entry, len(place)))
        out = [e for e in self._entries.values() if self._out(e)] + \
            [self._ghost(n) for n in self._roster if n in self._retired and n not in self._entries]
        out.sort(key=lambda e: (-self._shown(e) if e.distance is not None else math.inf, e.entry))
        winner = self._winner()

        def key(e):
            shown = self._shown(e)
            dist = shown if shown is not None else -math.inf
            if self._finishing_at is None:
                # On a tie an estimate stays on its side of its reference.
                side = (-1.0 if self._est[e.entry]["ahead"] else 1.0) if self._estimated(e) else 0.0
                return (0, -dist, side, 0.0, "")
            if e is winner:
                return (-1, 0, 0.0, 0.0, "")
            laps = self._ranked_laps(e)
            laps = -1 if laps is None else laps
            # Whoever reached that lap count first is ahead, finished or still running.
            reached = self._time_at(e, laps * self.length - e.adjust) if laps >= 0 else None
            est = self._est.get(e.entry) if self._estimated(e) else None
            ref = self._entries.get(est["ref"]) if est else None
            if ref is not None and laps >= 0:
                at = self._time_at(ref, laps * self.length - ref.adjust)
                if est["ahead"] and at is None and self._shown(ref) is not None:
                    # The reference is not there yet: it was this far behind.
                    at = self._time_at(ref, laps * self.length - (est["held"] - self._shown(ref)) - ref.adjust)
                    reached = at
                else:
                    reached = None if at is None else at + (-est["lag"] if est["ahead"] else est["lag"])
            if reached is None:
                reached = e.finished_at if e.finished_at is not None else math.inf
            return (0, -laps, reached, -dist, e.entry)
        ents.sort(key=key)
        mismatch = self._mismatches(ents)
        lap = self._lap_display(ents, now)
        ranked = len(ents)
        # Gaps are measured from the first car with data; an estimated leader shifts them later.
        base = next((k for k, e in enumerate(ents) if not self._estimated(e)), 0)
        ents += idle + out
        rows = []
        for i, e in enumerate(ents):
            car = self._cars.get(e.active) or _NO_CAR
            quiet, retired = self._quiet(e, now), self._out(e)
            lapped = gap = interval = gap_time = interval_time = None
            if self._timed(e, now):
                lapped, gap = self._behind(ents[base], e) if i != base else (0, 0)
                interval = self._behind(ents[i - 1], e)[1] if i else None
                gap_time = self._time_behind(ents[base], e, now) if i != base else 0.0
                interval_time = self._time_behind(ents[i - 1], e, now) if i else None
            shown = self._shown(e)
            est = self._est.get(e.entry)
            rows.append({
                "entry": e.entry, "conn": e.active,
                "driver": self.display_name(e.entry, car.name), "driver_registered": car.name,
                "distance_m": None if shown is None else round(shown, 1),
                "laps": max(0, self._ranked_laps(e)) if shown is not None else 0,
                "gap_s": None if lapped else (None if gap is None else round(gap, 2)),
                "interval_s": None if interval is None else round(interval, 2),
                "gap_time_s": None if gap_time is None else round(gap_time, 2),
                "interval_time_s": None if interval_time is None else round(interval_time, 2),
                "quiet_s": None if car.t is None else round(max(0.0, now - car.t), 1),
                "in_box": bool(car.in_box),
                "lapped": lapped or 0,
                "status": "retired" if retired else
                          ("est" if self._estimated(e) else
                           "no_sender" if e.active is None else "no_data") if quiet else
                          "box" if car.in_box else
                          "waiting" if e.distance is None else
                          "red_flag" if e.waiting else "racing",
                "finished": e.finished_at is not None and not retired,
                "waiting": e.waiting,
                "pit_stops": max(0, e.pit_stops + e.pit_adjust),
                "correction_m": round(e.adjust, 1),
                "last_lap_ms": e.last_lap_ms, "stint_laps": e.stint_laps,
                "fuel_l": None if car.fuel is None or retired else round(car.fuel, 1),
                **dict(zip(("x", "z"), (None, None) if retired else
                           self._est_xy(e) if self._estimated(e) and quiet
                           else (car.x, car.z), strict=True)),
                "rejoin": e.rejoin,
                "gt7_position": car.position,
                "position_mismatch": e.entry in mismatch,
                "estimate": None if est is None else {"ref": est["ref"], "seconds": est["seconds"],
                                                      "ahead": est["ahead"]},
                "class": None, "class_pos": None, "class_gap_s": None, "class_interval_s": None,
                "class_lapped": None, "class_gap_time_s": None, "class_interval_time_s": None})
        self._estimate_gaps(ents, rows, now)
        classes = self._class_pass(ents, rows, ranked, now)
        shown = {name: self._lap_display([e for e in ents[:ranked] if self._classes.get(e.entry, "") == name], now)
                 for name in classes}
        # A class count shows only with the overall one; in endurance on the class leader's final lobby counter.
        class_counts = {name: lap["count"] is not None and (not self.multi_lobby or d["count"] is not None)
                        for name, d in shown.items()}
        first = next((name for name in classes if name), None)
        class_lap = {}
        for name, d in shown.items():
            # The first class runs the overall count, which `_lap_display` already caps.
            count = self._class_laps.get(name) if name and name != first and class_counts[name] else None
            leader = d["leader"]
            lead = next((e for e in ents[:ranked] if self._classes.get(e.entry, "") == name), None)
            if leader is not None and count and (self._finishing_at is not None
                                                 or (lead is not None and self._estimated(lead))):
                leader = min(leader, count)
            class_lap[name] = leader
        red = None if self._red is None else dict(self._red)
        return {"state": self._state(), "line_length_m": self.length, "red_flag": red,
                "restart_mode": self._restart_mode,
                "change_window": self._window_until is not None and now <= self._window_until,
                "fastest": self.fastest,
                "lap": lap, "classes": classes, "class_lap": class_lap, "class_counts": class_counts, "entries": rows}

    def _estimate_gaps(self, ents, rows, now):
        """An estimated car's gaps are its reference's plus the seconds behind, minus
        them ahead. An estimated leader puts every gap relative to itself; the
        intervals next to an estimate follow from the gaps."""
        by_entry = {r["entry"]: r for r in rows}
        keys = ("gap_s", "gap_time_s")
        for e, row in zip(ents, rows, strict=True):
            if self._estimated(e):
                est = self._est[e.entry]
                ref = by_entry.get(est["ref"]) or {}
                lag = -est["lag"] if est["ahead"] else est["lag"]
                row.update({k: None if ref.get(k) is None else ref[k] + lag for k in keys})
        led = bool(ents) and self._estimated(ents[0])
        lead = self._shown(ents[0]) if ents else None
        # A lapped reference has no gap_s; its time gap places the leader then.
        lead_gap = next((rows[0][k] for k in keys if rows[0][k] is not None), None) if led else 0.0
        shift = {k: rows[0][k] if led and rows[0][k] is not None else lead_gap for k in keys}
        for i, (e, row) in enumerate(zip(ents, rows, strict=True)):
            if self._estimated(e) or (led and self._timed(e, now)):
                for k in keys:
                    row[k] = 0.0 if not i else None if row[k] is None or shift[k] is None \
                        else round(max(0.0, row[k] - shift[k]), 2)
                shown = self._shown(e)
                lapped = self._laps_behind(ents[0], e) if i and None not in (lead, shown) else 0
                row["lapped"] = max(0, lapped)
                if lapped >= 1:
                    row["gap_s"] = None
            elif not (i and self._estimated(ents[i - 1])):
                continue
            if i:
                prev = rows[i - 1]
                for key, by in (("interval_s", "gap_s"), ("interval_time_s", "gap_time_s")):
                    a, b = row[by], prev[by]
                    row[key] = None if a is None or b is None else round(max(0.0, a - b), 2)

    def _timed(self, e, now):
        """Whether a row's gaps are measured: a running car with data, or a finished one
        (a finish is final)."""
        return e.distance is not None and not self._out(e) \
            and (not self._quiet(e, now) or e.finished_at is not None)

    def _class_pass(self, ents, rows, ranked, now):
        """Fill each row's class fields like the overall ones, within its class, and
        return the class names in order; [] with fewer than two named classes."""
        names = list(dict.fromkeys(self._classes.get(e.entry, "") for e in ents))
        if len([n for n in names if n]) < 2:
            return []
        if "" in names:
            names.remove("")
            names.append("")
        members = {}
        for i, e in enumerate(ents):
            cls = self._classes.get(e.entry, "")
            members.setdefault(cls, []).append(i)
            rows[i].update({"class": cls, "class_pos": len(members[cls])})
        for idx in members.values():
            refs = [i for i in idx if i < ranked]    # retired and no_sender rows rank behind
            for k, i in enumerate(refs):
                lead, e, row = ents[refs[0]], ents[i], rows[i]
                prev = ents[refs[k - 1]] if k else None
                live = self._timed(e, now)
                lapped = gap = interval = gap_time = None
                if not k:
                    lapped, gap = (0, 0) if live or self._estimated(e) else (None, None)
                    gap_time = 0.0 if gap is not None else None
                elif self._estimated(e) or self._estimated(lead):
                    if live or self._estimated(e):
                        lapped, gap = self._class_gap_from_rows(rows[refs[0]], row, lead, e)
                        gap_time = self._row_time_gap(rows[refs[0]], row)
                elif live:
                    lapped, gap = self._behind(lead, e)
                    gap_time = self._time_behind(lead, e, now)
                gap = None if lapped or gap is None else round(gap, 2)
                row["class_gap_time_s"] = None if gap_time is None else round(gap_time, 2)
                interval_time = None
                if k and (self._estimated(e) or self._estimated(prev)):
                    ahead = rows[refs[k - 1]]["class_gap_s"]
                    interval = None if gap is None or ahead is None else round(max(0.0, gap - ahead), 2)
                    a, b = rows[refs[k - 1]]["class_gap_time_s"], row["class_gap_time_s"]
                    interval_time = None if a is None or b is None else max(0.0, b - a)
                elif k and live:
                    interval = self._behind(prev, e)[1]
                    interval = None if interval is None else round(interval, 2)
                    interval_time = self._time_behind(prev, e, now)
                row.update(class_lapped=lapped or 0, class_gap_s=gap, class_interval_s=interval,
                           class_interval_time_s=None if interval_time is None else round(interval_time, 2))
        return names

    def _class_gap_from_rows(self, lead_row, row, lead, e):
        """(laps, seconds) of `e` behind its class leader from their overall rows: the
        rule for estimated cars, whose own history is stale."""
        if self._shown(lead) is None or self._shown(e) is None:
            return None, None
        laps = self._laps_behind(lead, e)
        if laps >= 1:
            return laps, None
        return 0, self._row_time_gap(lead_row, row)

    @staticmethod
    def _row_time_gap(lead_row, row):
        """Seconds between two overall rows' time gaps; None without both."""
        t0, t1 = lead_row["gap_time_s"], row["gap_time_s"]
        return None if t0 is None or t1 is None else max(0.0, t1 - t0)

    def _lap_display(self, ordered, now):
        """{leader, count}: the leader's running lap against the lap count. In endurance
        the count belongs to the final lobby, so it shows only with that lobby's car, on
        the lobby's leading counter on the road like the flag; never a lap past the count
        once the flag is out or while an estimated car leads."""
        count = self.lap_count if self.finish_mode == "laps" else None
        lead = ordered[0] if ordered and self._shown(ordered[0]) is not None else None
        if lead is None:
            return {"leader": None, "count": count}
        real = next((e for e in ordered if not self._estimated(e)), lead)   # an estimate has no counter
        car = self._cars.get(real.active)
        if self.multi_lobby and count and self._final_car(real) and car is not None and car.lap is not None:
            leader = self._lobby_lead_lap(now, ordered) or car.lap
        else:
            leader = max(0, math.floor(self._shown(lead) / self.length)) + (2 if self.formation else 1)
            if self.multi_lobby:
                count = None
        if count and (self._finishing_at is not None or self._estimated(lead)):
            leader = min(leader, count)
        return {"leader": leader, "count": count}

    def _mismatches(self, ordered):
        """Entries whose GT7 positions contradict the distance order (single lobby only)."""
        if self.multi_lobby:
            return set()
        known = [(e.entry, self._cars[e.active].position) for e in ordered
                 if e.distance is not None and e.active in self._cars and self._cars[e.active].position]
        out = set()
        for i, (a, pa) in enumerate(known):
            for b, pb in known[i + 1:]:
                if pb < pa:
                    out.update((a, b))
        return out

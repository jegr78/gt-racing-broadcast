"""Simulated GT7 drivers: cars on a racing line whose senders post encrypted packets.

`Field` holds the cars and steps them on a clock the caller passes in, so a seed fixes
every packet. It knows mechanisms only: start, pit, box, lobby, release, drop, rejoin,
stop. `send(code, body)` is the transport (`Client.ingest` against a relay); `Runner`
steps a field on real time in a thread. `write_track_data` gives an app home a racing
line: the host's downloaded one when it fits, else a simulated lap. Used by the event simulation's drivers role and by
`tools/driver-replay.py`."""
import bisect
import http.client
import json
import math
import os
import random
import shutil
import struct
import sys
import threading
import time
from urllib.parse import quote, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
_SCRIPTS = os.path.join(ROOT, "src", "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import gt7_crypto  # noqa: E402
import gt7_data  # noqa: E402
import gt7_telemetry  # noqa: E402
import gt7_tracks  # noqa: E402
import telemetry_probe  # noqa: E402

TICK_S = 0.2
MAX_CATCH_UP_S = 5.0        # a paused process resumes with a gap, not a replayed race
PACKETS_PER_TICK = 6        # one sample per batch at the probe's SAMPLE_EVERY
PIT_IN_M = 250.0            # pit entry to the first box, and box to the pit exit
BOX_BACK_M = 180.0          # the first box stands this far before the finish line
LAT_RAMP_M = 150.0
BOX_LAT_M = 22.0
STALL_M = 5.0               # boxes apart, the first one furthest forward
GRID_GAP_M = 16.0
RACE_MPS = 55.0
MIN_LENGTH_M = 2 * (PIT_IN_M + BOX_BACK_M)
MAX_CARS = 15
PATH_STEP_M = 20.0
FUEL_CAP = 100.0
CHANGE_STOP_S = 6.0         # the next car leaves its box this long after its team's old car stood
DEFAULT_TRACK_M = 1200.0

TEAMS = ("Apex Works #11", "Blue Harbor Racing #22", "Cedar Motorsport #33", "Delta Sim Team #44",
         "Echo Racing #55", "Falcon Engineering #66", "Granite GP #77", "Harbor Lights #88",
         "Ion Racing #9", "Juniper Motorsport #10", "Kestrel Sim Racing #12", "Lakeside Racing #14",
         "Meridian Team #16", "Northwind Racing #18", "Orbit Motorsport #20")
# Per team: a brand of src/assets/brands and a tile colour (Configuration Brand, BG Color).
TEAM_LOOKS = (("Porsche", "#c8102e"), ("BMW", "#1c69d4"), ("Ferrari", "#e8b70a"), ("Audi", "#5c6670"),
              ("Aston Martin", "#00665e"), ("Lamborghini", "#7ab800"), ("McLaren", "#ff8000"), ("AMG", "#00a19c"),
              ("Ford", "#003478"), ("Nissan", "#8a1538"), ("Toyota", "#eb0a1e"), ("Honda", "#6d2077"),
              ("Lexus", "#d9d9d6"), ("Mazda", "#101820"), ("Chevy", "#cd9834"))


class DriveError(Exception):
    """An action the field cannot take: an unknown entry, a car in the wrong state."""


def roster(cars):
    """The first `cars` team labels of the simulated roster (Configuration, Teams)."""
    if type(cars) is not int or not 1 <= cars <= MAX_CARS:
        raise ValueError(f"cars is a whole number 1-{MAX_CARS}, not {cars!r}")
    return list(TEAMS[:cars])


def text_colour(bg):
    """A readable text colour on the tile colour `bg` (#rrggbb)."""
    r, g, b = (int(bg[i:i + 2], 16) for i in (1, 3, 5))
    return "#12161c" if 0.299 * r + 0.587 * g + 0.114 * b > 150 else "#ffffff"


def driver_name(entry, stint):
    """A stable driver name for `entry` in 0-based `stint`."""
    number = entry.rsplit("#", 1)[-1].strip()
    return f"Driver {number}-{stint + 1}"


def device_address(car, stint):
    """A documentation address (TEST-NET-2) per driver, as the Funnel proxy would append
    it, so each driver registers as its own device."""
    return f"198.51.100.{10 + car * 4 + stint % 4}"


def packet(pid, lap, speed, x, z, last_ms, best_ms, fuel, fuel_cap=FUEL_CAP, position=None, cars=None):
    """One encrypted GT7 'A' packet with the fields the race engine reads; `position`
    and `cars` fill 0x84, which stays empty without them."""
    return gt7_crypto.encrypt(plain_packet(pid, lap, speed, x, z, last_ms, best_ms, fuel, fuel_cap,
                                           position, cars), "A", iv=pid)


def plain_packet(pid, lap, speed, x, z, last_ms, best_ms, fuel, fuel_cap=FUEL_CAP, position=None, cars=None):
    """`packet` before encryption, as a recording holds it."""
    b = bytearray(0x128)
    struct.pack_into("<I", b, gt7_telemetry.OFF_MAGIC, 0x47375330)
    struct.pack_into("<f", b, gt7_telemetry.OFF_FUEL_LEVEL, fuel)
    struct.pack_into("<f", b, gt7_telemetry.OFF_FUEL_CAP, fuel_cap)
    struct.pack_into("<f", b, gt7_telemetry.OFF_SPEED, speed)
    struct.pack_into("<h", b, gt7_telemetry.OFF_LAP, lap)
    struct.pack_into("<i", b, gt7_telemetry.OFF_BEST_MS, best_ms)
    struct.pack_into("<i", b, gt7_telemetry.OFF_LAST_MS, last_ms)
    struct.pack_into("<H", b, gt7_telemetry.OFF_FLAGS, gt7_telemetry.FLAG_ON_TRACK)
    struct.pack_into("<3f", b, gt7_telemetry.OFF_POS, x, 0.0, z)
    struct.pack_into("<i", b, 0x70, pid)
    if position is not None:
        struct.pack_into("<hh", b, gt7_telemetry.OFF_RACE_POSITION, position, cars)
    return bytes(b)


class Line:
    """Position along a closed reference line, wrapping at its length."""

    def __init__(self, trace):
        self.trace = trace
        self.length = trace[-1]["d"]
        self.ds = [p["d"] for p in trace]

    def at(self, s, lateral=0.0):
        s %= self.length
        i = max(1, bisect.bisect_left(self.ds, s))
        a, b = self.trace[i - 1], self.trace[i]
        u = (s - a["d"]) / ((b["d"] - a["d"]) or 1)
        x, z = a["x"] + u * (b["x"] - a["x"]), a["z"] + u * (b["z"] - a["z"])
        if lateral:
            dx, dz = b["x"] - a["x"], b["z"] - a["z"]
            n = math.hypot(dx, dz) or 1.0
            x, z = x - dz / n * lateral, z + dx / n * lateral
        return x, z


def synthetic_path(length_m):
    """[[x, z], ...] of a stadium-shaped lap of about `length_m`: two straights joined by
    two half circles, a point every PATH_STEP_M, starting on the finish line."""
    if not isinstance(length_m, (int, float)) or isinstance(length_m, bool) or length_m < MIN_LENGTH_M:
        raise ValueError(f"a simulated lap is at least {MIN_LENGTH_M:.0f} m, not {length_m!r}")
    straight = length_m / 4
    radius = length_m / (4 * math.pi)
    half = math.pi * radius
    out = []
    for i in range(int(length_m // PATH_STEP_M)):
        s = i * PATH_STEP_M
        if s < straight:
            x, z = s - straight / 2, -radius
        elif s < straight + half:
            a = (s - straight) / radius - math.pi / 2
            x, z = straight / 2 + radius * math.cos(a), radius * math.sin(a)
        elif s < 2 * straight + half:
            x, z = straight / 2 - (s - straight - half), radius
        else:
            a = (s - 2 * straight - half) / radius + math.pi / 2
            x, z = -straight / 2 + radius * math.cos(a), radius * math.sin(a)
        out.append([round(x, 1), round(z, 1)])
    return out


def trace(path):
    """The closed d/x/z trace of `path`, as the relay's TrackDB builds it."""
    out, d = [], 0.0
    for i, (x, z) in enumerate(path):
        if i:
            d += math.hypot(x - path[i - 1][0], z - path[i - 1][1])
        out.append({"d": d, "x": float(x), "z": float(z)})
    d += math.hypot(path[0][0] - path[-1][0], path[0][1] - path[-1][1])
    out.append({"d": d, "x": float(path[0][0]), "z": float(path[0][1])})
    return out


def signatures_doc(track_id, path):
    """A `signatures.json` whose one drivable layout is `track_id` on `path`. The rows
    without a path only fill the file up to the size the relay's validator asks for."""
    xs, zs = [p[0] for p in path], [p[1] for p in path]
    row = {"official_id": track_id, "official_name": "Simulated circuit",
           "length_m": round(trace(path)[-1]["d"], 1),
           "min_x": min(xs), "max_x": max(xs), "min_z": min(zs), "max_z": max(zs),
           "provenance": "simulation", "path": path, "reverse": None, "ambiguous_with": [], "flags": []}
    pad = [{"official_id": f"sim-pad-{i:02d}", "length_m": 0, "min_x": 0, "max_x": 0, "min_z": 0, "max_z": 0}
           for i in range(gt7_data.MIN_SIGNATURES)]
    return {"format": "gt7-datalogger-track-signatures", "version": 1, "signatures": [row] + pad}


def home_trace(runtime_base, track_id):
    """The d/x/z line of `track_id` the relay reads from `runtime_base`, or None."""
    return gt7_tracks.TrackDB.load(runtime_base, gt7_data.bundled_dir()).reference_trace(track_id)


def line_note(runtime_base, track_id):
    """Where the line of `track_id` in `runtime_base` came from, for the run's output."""
    line = home_trace(runtime_base, track_id)
    if not line:
        return "none"
    with open(os.path.join(gt7_data.data_dir(runtime_base), "signatures.json"), encoding="utf-8") as fh:
        rows = json.load(fh)["signatures"]
    if any(r.get("official_id") == track_id and r.get("provenance") == "simulation" for r in rows):
        return f"simulated lap ({line[-1]['d']:.0f} m)"
    db = gt7_tracks.TrackDB.load(runtime_base, gt7_data.bundled_dir())
    name = (db.name(track_id) or {}).get("official_name") or track_id
    return f"host signatures.json ({name}, {line[-1]['d']:.0f} m)"


def _host_signatures(host, track_id, length_m):
    """The host's downloaded signatures.json when it holds a line of `track_id` within the
    relay's length tolerance of `length_m` (a scenario times its laps by that length), else None."""
    if not host:
        return None
    path = os.path.join(gt7_data.data_dir(host), "signatures.json")
    if gt7_data.resolve("signatures.json", host) != path:
        return None
    line = home_trace(host, track_id)
    if not line or abs(line[-1]["d"] - length_m) > gt7_tracks.LENGTH_TOL * length_m:
        return None
    return path


def write_track_data(runtime_base, track_id, length_m, host=None):
    """Give `runtime_base`/gt7 a line of `track_id`: a copy of the `host` runtime's
    downloaded signatures.json when it fits (`_host_signatures`), else the simulated lap."""
    target = gt7_data.data_dir(runtime_base)
    os.makedirs(target, exist_ok=True)
    source = _host_signatures(host, track_id, length_m)
    if source:
        shutil.copyfile(source, os.path.join(target, "signatures.json"))
        return
    with open(os.path.join(target, "signatures.json"), "w", encoding="utf-8") as fh:
        json.dump(signatures_doc(track_id, synthetic_path(length_m)), fh)


class Sender:
    """One driver's connection: a code, its packet counter and its lap times."""

    def __init__(self, name):
        self.name = name
        self.code = None
        self.pid = 0
        self.last_ms = self.best_ms = -1
        self.line_t = None
        self.timed = 0             # laps timed in this session

    def new_session(self):
        self.line_t = None
        self.timed = 0
        self.last_ms = self.best_ms = -1          # GT7 clears both in a new session


class Car:
    def __init__(self, k, entry, senders, base, fuel, gap_m):
        self.k, self.entry = k, entry
        self.senders = senders
        self.active = 0
        self.extra = None            # the next lobby's sender while both stand in the box
        self.odo = -15.0 - gap_m * k
        self.lap = 0
        self.base = self.pace = base
        self.v = 0.0
        self.fuel = fuel
        self.lat = 0.0
        self.mode = "grid"           # grid, drive, pit_in, stand, pit_out, dropped, off
        self.pit = None
        self.requests = []
        self.left_at = None
        self.rejoined = False        # joined a new session in this lobby: its lap no longer counts the lobby's
        self.flagged = False         # crossed the line after the chequered flag
        self.flag_sent = False       # ... and that crossing went out: the sender stops
        self.next = None             # the next lobby's car while this one still races
        self.owner = None            # on a next car: the old lobby's car it takes over from

    @property
    def sender(self):
        return self.senders[self.active]

    @property
    def driver(self):
        return self.sender.name


class Field:
    """The cars of one race. Every method but `step` is an instruction; `step(now, dt)`
    moves the cars and posts one batch per sending driver. Not thread-safe: `Runner`
    serialises a field with its lock."""

    def __init__(self, line_trace, send, seed=7, pace_mps=RACE_MPS, gap_m=GRID_GAP_M, positions=False):
        """`positions` fills GT7's race position (0x84) with each car's place on the road."""
        self.line = Line(line_trace)
        self.positions = positions
        self._places = {}
        if self.line.length < MIN_LENGTH_M:
            raise ValueError(f"a lap is at least {MIN_LENGTH_M:.0f} m, not {self.line.length:.0f}")
        self.send = send
        self.rnd = random.Random(seed)
        self.pace, self.gap_m = pace_mps, gap_m
        self.cars = []
        self.stint = 0
        self.released_at = None
        self._release = False
        self.box_lead = None         # a leader-first box call: no car enters the pit lane before it
        self.chequered = False
        self.change_allowed = False  # next cars may leave their box (the broadcast's stint switch)
        self._finish_order = []      # GT7 keeps a finished car at its finishing place
        self.box_station = self.line.length - BOX_BACK_M
        self.errors = 0
        self.outbox = []

    def add(self, entry, drivers):
        """A car on the next grid slot; `drivers` names its driver per stint (the same
        name twice in a row is one driver staying on)."""
        if any(c.entry == entry for c in self.cars):
            raise DriveError(f"entry {entry!r} is already on the grid")
        if len(self.cars) >= MAX_CARS:
            raise DriveError(f"the grid holds {MAX_CARS} cars")
        k = len(self.cars)
        senders = []
        for i, name in enumerate(drivers):
            senders.append(senders[i - 1] if i and name == drivers[i - 1] else Sender(name))
        base = self.pace + 0.6 - 0.12 * k - self.rnd.uniform(0, 0.15)
        car = Car(k, entry, senders, base, FUEL_CAP - 0.6 * k, self.gap_m)
        self.cars.append(car)
        return car

    def car(self, entry):
        for c in self.cars:
            if c.entry == entry:
                return c
        raise DriveError(f"no car of entry {entry!r} on the grid")

    def _pick(self, entries):
        return list(self.cars) if entries is None else [self.car(e) for e in entries]

    def set_code(self, entry, stint, code):
        car = self.car(entry)
        if not 0 <= stint < len(car.senders):
            raise DriveError(f"{entry!r} has no driver for stint {stint + 1}")
        car.senders[stint].code = code

    # -- instructions --------------------------------------------------------------
    def start(self):
        for c in self.cars:
            if c.mode == "grid":
                c.mode = "drive"

    def pit(self, entry, seconds=10.0):
        """The car stops at its box the next time it reaches the pit entry."""
        self.car(entry).requests.append({"seconds": float(seconds), "stall": 0})

    def box(self, entries=None, leader_first=False, laps=None):
        """Each car drives into the box at its next pit entry and waits for `release`;
        `entries` sets the box order, the first one furthest forward. With `leader_first`
        a car reaching the pit entry before the first of them drives on another lap. In a
        lobby of `laps` laps each car boxes at the end of its lap `laps - 1`, a car that
        rejoined the lobby at its next pit entry."""
        self.released_at, self._release = None, False
        self.box_lead = None
        for i, c in enumerate(self._pick(entries)):
            if c.mode in ("grid", "drive", "pit_out"):
                c.requests.append({"hold": True, "stall": i, "lap": laps - 1 if laps else None})
                if leader_first and self.box_lead is None:
                    self.box_lead = c.entry

    def open_next(self, stint):
        """The lobby of 0-based `stint` opens while the current one still races: each running
        car's next driver starts there on the grid, drives lap 1 and waits in the box. Once
        `allow_change` came and its team's old car stood CHANGE_STOP_S in the old lobby's
        box, the next car leaves and takes over; the old sender stops."""
        if stint < 0:
            raise DriveError("a stint counts from 1")
        self.stint, self.change_allowed = stint, False
        for c in self.cars:
            if c.mode in ("dropped", "off") or c.next is not None:
                continue
            nxt = min(stint, len(c.senders) - 1)
            if c.senders[nxt] is c.sender:
                continue                    # a driver staying on changes nothing until the old lobby ends
            twin = Car(c.k, c.entry, c.senders, c.base, FUEL_CAP, self.gap_m)
            twin.active, twin.owner = nxt, c
            twin.mode = "drive"
            twin.requests = [{"hold": True, "change": True, "stall": 0, "lap": 1}]
            twin.sender.new_session()
            c.next = twin

    def allow_change(self):
        """The broadcast switched stints: each next car may take over at its box stop."""
        self.change_allowed = True

    def lobby(self, stint):
        """The lobby of 0-based `stint` opens: its drivers stand in the box beside the
        cars waiting there, on lap 0 of a new session."""
        if stint < 0:
            raise DriveError("a stint counts from 1")
        self.stint = stint
        for c in self.cars:
            if c.mode != "stand" or not c.pit.get("hold"):
                continue
            nxt = min(stint, len(c.senders) - 1)
            c.rejoined = False
            if c.senders[nxt] is c.sender:
                c.lap = 0
                c.sender.new_session()
            else:
                c.extra = nxt
                c.senders[nxt].new_session()

    def release(self, delays=None):
        """The held cars leave the box in box order, each by the gap it arrived with, or
        by its seconds in `delays` ({entry: s}, a keep restart's frozen gaps); a held car
        without one leaves with the last."""
        for entry in delays or {}:
            self.car(entry)
        held = [c for c in self.cars if c.mode in ("pit_in", "stand") and c.pit.get("hold")]
        stood = [c.pit["stood"] for c in held if c.pit.get("stood") is not None]
        first = min(stood) if stood else None
        for c in held:
            if delays is not None:
                c.pit["delay"] = max(0.0, float(delays.get(c.entry, max(delays.values(), default=0.0))))
                continue
            # A car still on its way in, or stopped this very tick, leaves once it stood.
            gap = c.pit["stood"] - first if c.pit.get("stood") is not None else 0.0
            c.pit["delay"] = max(0.0, gap - c.pit["stall"] * STALL_M / self.pace)
        self._release = True

    def drop(self, entry):
        """The driver's connection ends where the car is."""
        car = self.car(entry)
        if car.mode in ("dropped", "off"):
            raise DriveError(f"{entry!r} is not sending")
        car.mode, car.v, car.pit, car.requests = "dropped", 0.0, None, []
        if self.box_lead == entry:
            self.box_lead = None

    def rejoin(self, entry, seconds=5.0):
        """A dropped car's driver of the current stint joins a new session in the box and
        leaves after `seconds`."""
        car = self.car(entry)
        if car.mode != "dropped":
            raise DriveError(f"{entry!r} is not dropped")
        car.active = min(self.stint, len(car.senders) - 1)
        car.extra = None
        car.sender.new_session()
        car.lap, car.rejoined = 0, True
        lap_start = math.floor(car.odo / self.line.length) * self.line.length
        car.odo = lap_start + self.box_station
        car.lat, car.v, car.mode = BOX_LAT_M, 0.0, "stand"
        car.pit = {"seconds": float(seconds), "stall": 0, "stop": PIT_IN_M,
                   "start_odo": car.odo - PIT_IN_M, "stood": None}

    def chequer(self, done=()):
        """The chequered flag is out: the cars in `done` have finished and stop sending,
        every other car stops once it next crosses the line."""
        self.chequered = True
        for car in self._pick(list(done)):
            if car not in self._finish_order:
                self._finish_order.append(car)
        self.stop(list(done))

    def stop(self, entries=None):
        """The senders of `entries` (default all) close."""
        for c in self._pick(entries):
            c.mode, c.v, c.requests = "off", 0.0, []
            if self.box_lead == c.entry:
                self.box_lead = None

    def snapshot(self):
        return [{"entry": c.entry, "mode": c.mode, "lap": c.lap, "driver": c.driver,
                 "sending": c.mode not in ("dropped", "off") and c.sender.code is not None,
                 "next": c.next.mode if c.next is not None else None}
                for c in self.cars]

    # -- the clock -----------------------------------------------------------------
    def step(self, now, dt, flush=True, post=True):
        """Move every car one tick. The batches go out at once, or with `flush=False`
        wait in `outbox` for a `flush()` outside the caller's lock; `post=False` moves
        the cars without building a batch."""
        if self._release:
            self._release, self.released_at = False, now
        if self.positions:
            running = [c for c in self._finish_order] + sorted(
                (c for c in self.cars if c.mode not in ("dropped", "off") and c not in self._finish_order),
                key=lambda c: -c.odo)
            self._places = {c.entry: (k, len(running)) for k, c in enumerate(running, 1)}
        for car in list(self.cars):
            self._step(car, now, dt, post)
            if car.next is not None:
                self._step(car.next, now, dt, post)
        if flush:
            self.flush(self.take())

    def take(self):
        batches, self.outbox = self.outbox, []
        return batches

    def flush(self, batches):
        """Post `batches` ((code, body) pairs); a refused or failed post only counts."""
        for code, body in batches:
            try:
                self.send(code, body)
            except (OSError, http.client.HTTPException):
                self.errors += 1

    def _crossed(self, car, now):
        s = car.sender
        if s.line_t is not None:
            s.last_ms = round((now - s.line_t) * 1000)
            s.timed += 1
            if s.timed > 1:                       # GT7 marks a session's lap 1 red
                s.best_ms = s.last_ms if s.best_ms < 0 else min(s.best_ms, s.last_ms)
        s.line_t = now
        car.lap += 1
        car.pace = car.base + self.rnd.uniform(-0.45, 0.45)
        car.fuel = max(3.0, car.fuel - 2.6)

    def _stand_done(self, car, now):
        p = car.pit
        if p["stood"] is None:
            p["stood"] = now
        if p.get("change"):
            old = car.owner
            return self.change_allowed and old.mode in ("stand", "off", "dropped") and \
                (old.mode != "stand" or now - (old.pit["stood"] or now) >= CHANGE_STOP_S)
        if p.get("hold"):
            return self.released_at is not None and "delay" in p and now >= self.released_at + p["delay"]
        return now - p["stood"] >= p["seconds"]

    def _take_over(self, twin):
        """`twin` leaves its box: it is its team's car from now on, the old one stops."""
        old = twin.owner
        old.mode, old.v, old.requests, old.next = "off", 0.0, [], None
        twin.owner = None
        self.cars[self.cars.index(old)] = twin
        if self.box_lead == old.entry:
            self.box_lead = None

    def _step(self, car, now, dt, post=True):
        if car.flag_sent and car.mode != "off":
            car.mode, car.v, car.requests = "off", 0.0, []
        if car.mode in ("dropped", "off"):
            return
        length = self.line.length
        before, speed = car.odo, 0.0
        if car.mode == "drive":
            car.v += max(-3.0, min(2.0, car.pace - car.v)) * dt * 2
            speed = car.v
            car.odo += speed * dt
            car.lat = 0.0
            entry_s = self.box_station - PIT_IN_M
            pos = car.odo % length
            waits = car.requests and (
                (self.box_lead not in (None, car.entry) and car.requests[0].get("hold"))
                or (not car.rejoined and car.lap < (car.requests[0].get("lap") or 0)))
            if car.requests and not waits and entry_s <= pos < entry_s + speed * dt + 1:
                if self.box_lead == car.entry and car.requests[0].get("hold"):
                    self.box_lead = None
                req = car.requests.pop(0)
                car.mode = "pit_in"
                car.pit = dict(req, p=0.0, stop=PIT_IN_M - req["stall"] * STALL_M, v0=max(car.v, 1.0),
                               start_odo=car.odo - (pos - entry_s), stood=None)
        elif car.mode == "pit_in":
            p = car.pit
            brake = p["v0"] ** 2 / (2 * p["stop"])
            speed = max(0.6, math.sqrt(max(0.0, p["v0"] ** 2 - 2 * brake * p["p"])))
            p["p"] = min(p["stop"], p["p"] + speed * dt)
            car.odo = p["start_odo"] + p["p"]
            car.lat = BOX_LAT_M * min(1.0, max(0.0, (p["p"] - (p["stop"] - LAT_RAMP_M)) / LAT_RAMP_M))
            if p["p"] >= p["stop"] - 0.5:
                p["p"], speed = p["stop"], 0.0
                car.odo = p["start_odo"] + p["stop"]
                car.lat = BOX_LAT_M
                car.mode, car.v = "stand", 0.0
        elif car.mode == "stand":
            if self._stand_done(car, now):
                if car.extra is not None:
                    car.active, car.extra = car.extra, None
                    car.lap = 0
                car.fuel = FUEL_CAP
                car.mode, car.pit["q"], car.left_at = "pit_out", 0.0, now
                if car.owner is not None:
                    self._take_over(car)
        elif car.mode == "pit_out":
            p = car.pit
            accel = car.pace ** 2 / (2 * PIT_IN_M)
            speed = max(2.5, math.sqrt(2 * accel * p["q"]))
            p["q"] = min(PIT_IN_M, p["q"] + speed * dt)
            car.odo = p["start_odo"] + p["stop"] + p["q"]
            car.lat = BOX_LAT_M * max(0.0, 1 - p["q"] / LAT_RAMP_M)
            car.v = speed
            if p["q"] >= PIT_IN_M:
                car.mode, car.pit = "drive", None
        if math.floor(car.odo / length) > math.floor(before / length):
            line = math.floor(car.odo / length) * length
            frac = (car.odo - line) / max(car.odo - before, 1e-6)
            self._crossed(car, now - frac * dt)
            if self.chequered and not car.flagged:
                car.flagged = True
                self._finish_order.append(car)
        if not post:
            return
        x, z = self.line.at(car.odo, car.lat)
        place = self._places.get(car.entry, (None, None)) if car.owner is None else (None, None)
        self._post(car.sender, car.lap, speed, x, z, car.fuel, place)
        car.flag_sent = car.flagged
        if car.extra is not None and car.mode == "stand":
            self._post(car.senders[car.extra], 0, 0.0, x, z, FUEL_CAP, place)

    def _post(self, sender, lap, speed, x, z, fuel, place=(None, None)):
        if sender.code is None:
            return
        batch = []
        for _ in range(PACKETS_PER_TICK):
            sender.pid += 1
            batch.append(packet(sender.pid, lap, speed, x, z, sender.last_ms, sender.best_ms, fuel,
                                position=place[0], cars=place[1]))
        self.outbox.append((sender.code, telemetry_probe.encode_batch(batch)))


class Client:
    """The driver's side of a relay: the invite's data, a registration and the ingest
    route. Loopback only, unless the caller names another relay on purpose."""

    def __init__(self, base, timeout=10.0, loopback_only=True, ingest_timeout=2.0):
        u = urlsplit(base)
        if u.scheme != "http" or not u.hostname:
            raise ValueError(f"a relay address is http://host:port, not {base!r}")
        if loopback_only and u.hostname not in ("127.0.0.1", "localhost"):
            raise ValueError(f"the simulated drivers post to a loopback relay only, not {base!r}")
        self.host, self.port, self.timeout = u.hostname, u.port, timeout
        self.ingest_timeout = ingest_timeout     # a stuck post must not stall the field

    def call(self, method, path, body=None, headers=None, timeout=None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout or self.timeout)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            raw = resp.read()
            status = resp.status
        finally:
            conn.close()
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        return status, data if isinstance(data, dict) else {}

    def data(self, invite):
        status, data = self.call("GET", "/console/drive/data?i=" + quote(invite, safe=""))
        if status != 200:
            raise DriveError(f"the invite's data: HTTP {status}")
        return data

    def register(self, invite, entry, name, segment=None, device=None):
        """Register a driver on the invite; returns the sender code. The registration
        waits for Race Control's approval."""
        body = {"entry": entry, "name": name} | ({"segment": segment} if segment else {})
        headers = {"Content-Type": "application/json"} | ({"X-Forwarded-For": device} if device else {})
        status, data = self.call("POST", "/console/drive/register?i=" + quote(invite, safe=""),
                                  json.dumps(body).encode(), headers)
        if status != 200 or not data.get("code"):
            raise DriveError(f"register {name!r} on {entry!r}: HTTP {status} {data.get('error') or ''}".strip())
        return data["code"]

    def ingest(self, code, body):
        status, _ = self.call("POST", "/console/drive/ingest", body, {"X-Driver-Code": code},
                              timeout=self.ingest_timeout)
        if status != 200:
            raise OSError(f"ingest: HTTP {status}")


class Runner:
    """Steps a field on real time in its own thread. Take `lock` around an instruction."""

    def __init__(self, field, clock=time.monotonic, sleep=time.sleep, tick_s=TICK_S):
        self.field = field
        self.clock, self.sleep, self.tick_s = clock, sleep, tick_s
        self.lock = threading.Lock()
        self.halt = threading.Event()
        self.thread = None
        self.error = ""          # why the loop ended on its own

    def loop(self, until=None):
        """Step until halted, or until the clock reaches `until`."""
        last = self.clock()
        while not self.halt.is_set() and (until is None or self.clock() < until):
            now = self.clock()
            try:
                with self.lock:
                    backlog = min(max(now - last, 0.0), MAX_CATCH_UP_S)
                    steps = max(1, math.ceil(backlog / self.tick_s - 1e-6))
                    dt = backlog / steps or self.tick_s
                    for k in range(steps, 0, -1):
                        self.field.step(now - (k - 1) * dt, dt, flush=False, post=k == 1)
                    batches = self.field.take()
                self.field.flush(batches)        # outside the lock: an instruction never waits for HTTP
            except Exception as exc:  # a dead sender thread must not look like quiet cars
                self.error = f"{type(exc).__name__}: {exc}"
                return
            last = now
            self.sleep(max(0.0, self.tick_s - (self.clock() - now)))

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self.loop, name="event-sim-drivers", daemon=True)
            self.thread.start()

    def close(self, timeout=5.0):
        """Stop stepping; True when the thread ended."""
        self.halt.set()
        if self.thread is not None:
            self.thread.join(timeout)
            return not self.thread.is_alive()
        return True

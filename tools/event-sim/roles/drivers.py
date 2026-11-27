"""The drivers of an event: senders that register on the event's invite and post GT7
telemetry through the relay's ingest route, as `racecast-sender.py` does for a real
driver. No page: the `driver` page role shows the registration form for one of them.

`register` only asks (with `now` only some stints; `register_stint` registers a later
one before its lobby); Race Control approves on its desk. The other actions are what
happens on track: `start`, `pit`, `box`, `lobby`, `next_lobby`, `change`, `release`, `drop`,
`rejoin`, `stop`."""
import http.client

import drive
import probes
from roles import Role, StepError, register

UNGAPPED_STEP_S = 1.0    # cars without a gap (lapped) leave after the last, this far apart


@register
class DriversRole(Role):
    """`register` (cars, stints or segment: one invite label such as Qualifying, positions:
    GT7's 0x84 position from the road order, skip: [{entry, stint}] seats the `driver`
    page registers, now: the stints that register at once), `register_stint` (stint),
    `start`, `pit` (entry, seconds), `box` (order: "race",
    "grid" or a list of entries; leader_first), `lobby` (stint), `release` (gaps), `drop` (entry),
    `rejoin` (entry, seconds), `stop` (entries), `next_lobby` (stint: opens while the current
    lobby still races), `change` (the broadcast switched stints: a driver staying on and a dropped team's next
    car go, the stint moves on). An entry is its
    team label or its grid slot."""
    kind = "drivers"
    client_factory = drive.Client
    runner_factory = drive.Runner

    def open(self):
        self.field = self.runner = self.client = None
        self.registered = 0
        self.waiting = {}        # (entry, 0-based stint) -> its key in ctx.driver_codes
        self.later = {}          # (entry, 0-based stint) -> (grid slot, segment): registers before its lobby

    def _field(self):
        if self.field is None:
            raise StepError("the drivers have not registered yet")
        return self.field

    def _entry(self, value):
        cars = self._field().cars
        if type(value) is int and 1 <= value <= len(cars):
            return cars[value - 1].entry
        if not any(c.entry == value for c in cars):
            raise StepError(f"no car {value!r} (the grid: {', '.join(c.entry for c in cars)})")
        return value

    def _do(self, act, what="act"):
        """One instruction to the field, between two of the runner's ticks."""
        try:
            if self.runner is None:
                return act()
            with self.runner.lock:
                return act()
        except drive.DriveError as exc:
            raise StepError(str(exc)) from None
        except Exception as exc:  # a bug in the field is this step's failure, named as such
            raise StepError(f"the drivers could not {what}: {type(exc).__name__}: {exc}") from None

    def act_register(self, args):
        """Every driver of `stints` (default: all), or of the one `segment`, registers on the invite;
        with `now` (1-based stints) only those, the others with `register_stint` before their lobby."""
        if self.field is not None:
            raise StepError("the drivers registered already")
        event = self.ctx.event
        track = event.get("track")
        if not track:
            raise StepError("the drivers need [event] track")
        invite = self.ctx.event_link("invite")
        self.client = self.client_factory(self.ctx.url("relay"))
        try:
            data = self.client.data(invite)
            entries = list(data.get("entries") or [])[:int(args.get("cars") or drive.MAX_CARS)]
            segments = list(data.get("segments") or [])
            if "segment" in args:
                if args.get("stints") not in (None, 1):
                    raise StepError(f"register: a named segment is one stint, not stints {args['stints']!r}")
                if args["segment"] not in segments:
                    raise StepError(f"the invite has no segment {args['segment']!r} "
                                    f"(it offers {', '.join(segments) or 'none'})")
                segments = [args["segment"]]
            if not entries:
                raise StepError("the invite lists no entries: the roster ([event] cars) is empty")
            stints = int(args.get("stints") or len(segments) or 1)
            skip = self._skips(args.get("skip", []), entries, stints)
            now = args.get("now", list(range(1, stints + 1)))
            if not isinstance(now, list) or not all(type(n) is int and 1 <= n <= stints for n in now):
                raise StepError(f"register: now lists stints 1-{stints}, not {now!r}")
            length = float(event.get("track_length_m", drive.DEFAULT_TRACK_M))
            line = self.ctx.track_trace or drive.trace(drive.synthetic_path(length))
            field = drive.Field(line, self.client.ingest,
                                seed=int(event.get("seed", 7)), positions=bool(args.get("positions")))
            for k, entry in enumerate(entries):
                field.add(entry, [drive.driver_name(entry, s) for s in range(stints)])
                for s in range(stints):
                    segment = segments[s] if s < len(segments) else None
                    if (entry, s) in skip:
                        self.waiting[(entry, s)] = (entry, segment or "")
                        continue
                    if s + 1 not in now:
                        self.later[(entry, s)] = (k, segment)
                        continue
                    field.set_code(entry, s, self.client.register(
                        invite, entry, drive.driver_name(entry, s), segment, drive.device_address(k, s)))
                    self.registered += 1
        except (drive.DriveError, ValueError, OSError, http.client.HTTPException) as exc:
            made, self.registered = self.registered, 0
            why = str(exc) if isinstance(exc, (drive.DriveError, ValueError)) else \
                f"the relay did not answer the drivers: {exc}"
            left = f" {made} registrations were already made and stay waiting at the relay." if made else ""
            raise StepError(why + "." + left + " No driver of this role is registered.") from None
        self.field = field
        return {"registered": self.registered, "skipped": len(self.waiting), "entries": entries, "stints": stints}

    def act_register_stint(self, args):
        """The drivers of 1-based `stint` that `register` left for later register now."""
        field, stint = self._field(), args.get("stint")
        stints = len(field.cars[0].senders) if field.cars else 0
        if type(stint) is not int or not 1 <= stint <= stints:
            raise StepError(f"register_stint: no stint {stint!r} (stints 1-{stints})")
        seats = [(seat, slot) for seat, slot in self.later.items() if seat[1] == stint - 1]
        if not seats:
            raise StepError(f"stint {stint} registered already")
        made = 0
        try:
            for (entry, s), (k, segment) in seats:
                code = self.client.register(self.ctx.event_link("invite"), entry, drive.driver_name(entry, s), segment,
                                            drive.device_address(k, s))
                self._do(lambda e=entry, s=s, c=code: field.set_code(e, s, c), "register")
                del self.later[(entry, s)]
                made += 1
                self.registered += 1
        except (drive.DriveError, ValueError, OSError, http.client.HTTPException) as exc:
            raise StepError(f"stint {stint}: {made} drivers registered, then the relay did not answer: {exc}") from None
        return {"stint": stint, "registered": made}

    @staticmethod
    def _skips(skip, entries, stints):
        """The seats `skip` names as {(entry, 0-based stint)}."""
        if not isinstance(skip, list) or not all(isinstance(s, dict) for s in skip):
            raise StepError("skip is a list of {entry, stint}")
        out = set()
        for seat in skip:
            entry, stint = seat.get("entry"), seat.get("stint", 1)
            if entry not in entries:
                raise StepError(f"skip: no entry {entry!r} on the invite")
            if type(stint) is not int or not 1 <= stint <= stints:
                raise StepError(f"skip: {entry} has no stint {stint!r} (stints 1-{stints})")
            out.add((entry, stint - 1))
        return out

    def _seats_of(self, stint):
        """Give the page-registered seats of 0-based `stint` the code their page showed;
        a seat still without one fails, as its car would send nothing."""
        for (entry, s), key in list(self.waiting.items()):
            code = self.ctx.driver_codes.get(key)
            if code:
                self.field.set_code(entry, s, code)
                del self.waiting[(entry, s)]
            elif s == stint:
                raise drive.DriveError(f"{entry} has no sender code for stint {s + 1}: "
                                       "the driver page has not registered that seat")

    def _registered_for(self, stint):
        """Refuse a 0-based `stint` (past the last counts as the last) whose drivers `register`
        left for later: their cars would drive without a code and send nothing."""
        stints = len(self._field().cars[0].senders) if self._field().cars else 1
        stint = min(stint, stints - 1)
        if any(s == stint for _e, s in self.later):
            raise StepError(f"the stint {stint + 1} drivers have not registered: register_stint first")

    def act_start(self, args):
        """The senders start and the cars leave the grid."""
        field = self._field()
        self._registered_for(field.stint)
        self._do(lambda: self._seats_of(field.stint), "start")
        if self.runner is None:
            self.runner = self.runner_factory(field)
            self.runner.start()
        self._do(field.start, "start")

    def act_pit(self, args):
        entry = self._entry(args.get("entry"))
        self._do(lambda: self._field().pit(entry, float(args.get("seconds", 10))), "pit")

    def _race_rows(self):
        """The relay's race rows of this field's cars, in race order."""
        try:
            status, data = probes.http_json(self.ctx.url("relay"), "/drivers/race")
        except (OSError, http.client.HTTPException) as exc:
            raise StepError(f"the race order: {exc}") from None
        rows = data.get("entries") if status == 200 and isinstance(data, dict) else None
        if not rows:
            raise StepError(f"the relay has no race order (HTTP {status})")
        known = {c.entry for c in self._field().cars}
        return [r for r in rows if r.get("entry") in known]

    def _race_order(self):
        return [r["entry"] for r in self._race_rows()]

    def act_chequer(self, args):
        """The chequered flag: the cars the relay counts finished stop sending at once,
        every other car once it next crosses the line."""
        self._do(lambda: self._field().chequer(), "chequer")    # first, so no crossing slips past
        done = [r["entry"] for r in self._race_rows() if r.get("finished") is True]
        self._do(lambda: self._field().chequer(done), "chequer")
        return {"finished": done}

    def act_box(self, args):
        """Every car drives into its box and waits there for `release`; with `leader_first`
        none enters the pit lane before the first of the order, with `laps` (the lobby's
        lap count) none before the end of its lap `laps - 1`."""
        order = args.get("order", "race")
        leader_first = args.get("leader_first", False)
        if not isinstance(leader_first, bool):
            raise StepError(f"box: leader_first is true or false, not {leader_first!r}")
        laps = args.get("laps")
        if laps is not None and (type(laps) is not int or laps < 1):
            raise StepError(f"box: laps is a whole number from 1 (the lobby's lap count), not {laps!r}")
        if order == "race":
            entries = self._race_order()
        elif order == "grid":
            entries = None
        elif isinstance(order, list):
            entries = [self._entry(e) for e in order]
        else:
            raise StepError("box order is 'race', 'grid' or a list of entries")
        self._do(lambda: self._field().box(entries, leader_first, laps), "box")
        out = {"order": entries or [c.entry for c in self._field().cars]}
        return dict(out, laps=laps) if laps else out

    def act_lobby(self, args):
        """The next GT7 lobby opens: the drivers of `stint` join in the box."""
        stint = args.get("stint")
        if type(stint) is not int or stint < 1:
            raise StepError("lobby needs args.stint, counted from 1")
        self._registered_for(stint - 1)
        self._do(lambda: (self._seats_of(stint - 1), self._field().lobby(stint - 1)), "open the lobby")

    def act_next_lobby(self, args):
        """The `stint` lobby opens while the current one races its last laps: the next
        drivers start there, drive lap 1 and wait in the box for their team's car."""
        stint = args.get("stint")
        if type(stint) is not int or stint < 1:
            raise StepError("next_lobby needs args.stint, counted from 1")
        self._registered_for(stint - 1)
        self._do(lambda: (self._seats_of(stint - 1), self._field().open_next(stint - 1)), "open the next lobby")

    def act_change(self, args):
        """The broadcast switched stints. Each next car goes on its old car's change stop
        alone; this lets a driver staying on and a dropped team's next car go."""
        self._do(self._field().allow_change, "allow the change")

    def act_release(self, args):
        """The held cars leave the box; with `gaps` (the rows of a race reading, such as
        race right before a red flag) each leaves its gap after the first; a car without
        a gap, such as a lapped one, leaves after the last in the reading's order."""
        delays = None
        if "gaps" in args:
            rows = args["gaps"]
            if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
                raise StepError("release gaps are the rows of a race reading, e.g. ${before_red.entries}")
            known = {c.entry for c in self._field().cars}
            rows = [r for r in rows if r.get("entry") in known]    # a roster entry may have no car
            gaps = {r.get("entry"): float(r["gap_s"]) for r in rows
                    if isinstance(r.get("gap_s"), (int, float)) and not isinstance(r.get("gap_s"), bool)}
            if not gaps:
                raise StepError("the race reading has no gap to release by")
            first = min(gaps.values())
            delays = {entry: gap - first for entry, gap in gaps.items()}
            last = max(delays.values())
            for r in rows:
                if r.get("entry") not in delays:
                    last += UNGAPPED_STEP_S
                    delays[r.get("entry")] = last
        self._do(lambda: self._field().release(delays), "release")

    def act_drop(self, args):
        entry = self._entry(args.get("entry"))
        self._do(lambda: self._field().drop(entry), "drop")

    def act_rejoin(self, args):
        entry = self._entry(args.get("entry"))
        self._do(lambda: self._field().rejoin(entry, float(args.get("seconds", 5))), "rejoin")

    def act_stop(self, args):
        entries = [self._entry(e) for e in args["entries"]] if args.get("entries") else None
        self._do(lambda: self._field().stop(entries), "stop")

    def page(self):
        if self.field is None:
            return {"registered": 0, "cars": []}
        cars = self._do(self.field.snapshot, "report")
        modes = [c["mode"] for c in cars]
        waiting = [c["next"] for c in cars if c.get("next")]
        return {"registered": self.registered, "cars": cars, "driving": modes.count("drive"),
                "standing": modes.count("stand"), "sending": sum(1 for c in cars if c["sending"]),
                "next_driving": waiting.count("drive"), "next_standing": waiting.count("stand"),
                "next_waiting": len(waiting), "ingest_errors": self.field.errors}

    def close(self):
        out = []
        if self.runner is not None and not self.runner.close():
            out.append(("soft", "the drivers' sender thread did not stop in time"))
        if self.runner is not None and getattr(self.runner, "error", ""):
            out.append(("hard", f"the drivers stopped sending: {self.runner.error}"))
        if self.field is not None and self.field.errors:
            out.append(("soft", f"{self.field.errors} telemetry posts were refused or failed"))
        return out

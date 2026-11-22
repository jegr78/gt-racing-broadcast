"""The drivers of an event: senders that register on the event's invite and post GT7
telemetry through the relay's ingest route, as `racecast-sender.py` does for a real
driver. No page: a driver's registration form adds nothing a sender does not do.

`register` only asks; Race Control approves on its desk. The other actions are what
happens on track: `start`, `pit`, `box`, `lobby`, `release`, `drop`, `rejoin`, `stop`."""
import http.client

import drive
import probes
from roles import Role, StepError, register


@register
class DriversRole(Role):
    """`register` (cars, stints), `start`, `pit` (entry, seconds), `box` (order: "race",
    "grid" or a list of entries), `lobby` (stint), `release`, `drop` (entry), `rejoin`
    (entry, seconds), `stop` (entries). An entry is its team label or its grid slot."""
    kind = "drivers"
    client_factory = drive.Client
    runner_factory = drive.Runner

    def open(self):
        self.field = self.runner = self.client = None
        self.registered = 0

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

    def _do(self, act):
        """One instruction to the field, between two of the runner's ticks."""
        try:
            if self.runner is None:
                return act()
            with self.runner.lock:
                return act()
        except drive.DriveError as exc:
            raise StepError(str(exc)) from None

    def act_register(self, args):
        """Every driver of `stints` (default: all) registers on the invite."""
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
            if not entries:
                raise StepError("the invite lists no entries: the roster ([event] cars) is empty")
            stints = int(args.get("stints") or len(segments) or 1)
            length = float(event.get("track_length_m", drive.DEFAULT_TRACK_M))
            field = drive.Field(drive.trace(drive.synthetic_path(length)), self.client.ingest,
                                seed=int(event.get("seed", 7)))
            for k, entry in enumerate(entries):
                field.add(entry, [drive.driver_name(entry, s) for s in range(stints)])
                for s in range(stints):
                    segment = segments[s] if s < len(segments) else None
                    field.set_code(entry, s, self.client.register(
                        invite, entry, drive.driver_name(entry, s), segment, drive.device_address(k, s)))
                    self.registered += 1
        except (drive.DriveError, ValueError) as exc:
            raise StepError(str(exc)) from None
        except (OSError, http.client.HTTPException) as exc:
            raise StepError(f"the relay did not answer the drivers: {exc}") from None
        self.field = field
        return {"registered": self.registered, "entries": entries, "stints": stints}

    def act_start(self, args):
        """The senders start and the cars leave the grid."""
        field = self._field()
        if self.runner is None:
            self.runner = self.runner_factory(field)
            self.runner.start()
        self._do(field.start)

    def act_pit(self, args):
        entry = self._entry(args.get("entry"))
        self._do(lambda: self._field().pit(entry, float(args.get("seconds", 10))))

    def _race_order(self):
        try:
            status, data = probes.http_json(self.ctx.url("relay"), "/drivers/race")
        except (OSError, http.client.HTTPException) as exc:
            raise StepError(f"the race order: {exc}") from None
        rows = data.get("entries") if status == 200 and isinstance(data, dict) else None
        if not rows:
            raise StepError(f"the relay has no race order (HTTP {status})")
        known = {c.entry for c in self._field().cars}
        return [r["entry"] for r in rows if r.get("entry") in known]

    def act_box(self, args):
        """Every car drives into its box and waits there for `release`."""
        order = args.get("order", "race")
        if order == "race":
            entries = self._race_order()
        elif order == "grid":
            entries = None
        elif isinstance(order, list):
            entries = [self._entry(e) for e in order]
        else:
            raise StepError("box order is 'race', 'grid' or a list of entries")
        self._do(lambda: self._field().box(entries))
        return {"order": entries or [c.entry for c in self._field().cars]}

    def act_lobby(self, args):
        """The next GT7 lobby opens: the drivers of `stint` join in the box."""
        stint = args.get("stint")
        if type(stint) is not int or stint < 1:
            raise StepError("lobby needs args.stint, counted from 1")
        self._do(lambda: self._field().lobby(stint - 1))

    def act_release(self, args):
        self._do(self._field().release)

    def act_drop(self, args):
        entry = self._entry(args.get("entry"))
        self._do(lambda: self._field().drop(entry))

    def act_rejoin(self, args):
        entry = self._entry(args.get("entry"))
        self._do(lambda: self._field().rejoin(entry, float(args.get("seconds", 5))))

    def act_stop(self, args):
        entries = [self._entry(e) for e in args["entries"]] if args.get("entries") else None
        self._do(lambda: self._field().stop(entries))

    def page(self):
        if self.field is None:
            return {"registered": 0, "cars": []}
        cars = self._do(self.field.snapshot)
        modes = [c["mode"] for c in cars]
        return {"registered": self.registered, "cars": cars, "driving": modes.count("drive"),
                "standing": modes.count("stand"), "sending": sum(1 for c in cars if c["sending"]),
                "ingest_errors": self.field.errors}

    def close(self):
        out = []
        if self.runner is not None and not self.runner.close():
            out.append(("soft", "the drivers' sender thread did not stop in time"))
        if self.field is not None and self.field.errors:
            out.append(("soft", f"{self.field.errors} telemetry posts were refused or failed"))
        return out

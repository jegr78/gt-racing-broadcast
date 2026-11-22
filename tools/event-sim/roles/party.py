"""The parties of an event that work on a page: producer (Control Center), director
(Director Panel), Race Control (its desk), commentator (cockpit) and viewer (public
timing). Each acts in its own browser context on the controls a person would use: a
control that is missing, hidden or disabled is a hard failure, and an action returns
only after the page showed its result. Without a browser every action is skipped.

An action is a mechanism (click this, submit that); the scenario decides the order."""
import time
from urllib.parse import quote, urlsplit

from roles import ShotUnavailable, StepError, Unavailable, builtin, register, selectors

CONTROL_MS = 8000
CONFIRM_S = 10.0
SLOW_S = 2.0             # the spec's soft threshold for a page showing a new state
JOB_S = 240.0
FEED_S = 120.0
POLL_MS = 100
DESK = selectors.DESK
STALE_S = 1.0
HUD_FIELDS = ("stint", "streamer", "session", "flag", "racecontrol")
URI_SAFE = "!'()*~"      # what the page's encodeURIComponent leaves as it is


def stint_label(value):
    """`2` or "2" -> "Stint 2"; any other text as it is (a Schedule label)."""
    text = str(value).strip()
    return f"Stint {text}" if text.isdigit() else text


class Surface(builtin.PageRole):
    """A party's page. Subclasses set `service`, `path`, `ready` (a selector visible once
    the page works), `crew` (whose console token opens it) and `selectors`."""
    service, path, ready, crew = "relay", "/", "body", ""
    sources = ()             # the shipped files `selectors` comes from (see the drift test)
    selectors = {}
    masked = ()              # selector keys a screenshot blacks out: invite and timing links
    clock = staticmethod(time.monotonic)

    def open(self):
        super().open()
        self._noted = set()

    def target(self):
        return self.path

    def act_open(self, args):
        if self.ctx.browser() is None:
            raise Unavailable(f"{self.kind} needs a browser: {self.ctx.browser_problem()}")
        spec = {"service": self.service, "path": self.target(), "wait_for": self.ready}
        who = self.instance or self.crew
        if who:
            spec["as"] = who
        return super().act_open(spec)

    def do(self, action, args):
        if self.pg is None and action != "open":
            self.act_open({})
        return super().do(action, args)

    # -- acting on controls ---------------------------------------------------
    def sel(self, key, **values):
        return self.selectors[key].format(**values)

    def control(self, selector, what):
        """The visible, enabled control at `selector`, scrolled into view."""
        loc = self.pg.locator(selector).first
        try:
            loc.wait_for(state="visible", timeout=CONTROL_MS)
        except Exception:
            raise StepError(f"{what}: the control is missing or hidden ({selector})") from None
        if not loc.is_enabled():
            raise StepError(f"{what}: the control is disabled ({selector})")
        self._note_outside(loc, what)
        loc.scroll_into_view_if_needed(timeout=CONTROL_MS)
        return loc

    def _note_outside(self, loc, what):
        box, view = loc.bounding_box(), self.viewport
        if not box or what in self._noted:
            return
        if box["y"] < 0 or box["x"] < 0 or box["y"] + box["height"] > view["height"] \
                or box["x"] + box["width"] > view["width"]:
            self._noted.add(what)
            self.ctx.finding("soft", f"{what}: the control is outside the {view['width']}x{view['height']} "
                                     "viewport, the page has to be scrolled")

    def click(self, selector, what):
        self.control(selector, what).click(timeout=CONTROL_MS)

    def fill(self, selector, text, what):
        self.control(selector, what).fill(str(text), timeout=CONTROL_MS)

    def choose(self, selector, what, label=None, value=None):
        loc = self.control(selector, what)
        try:
            if label is not None:
                loc.select_option(label=str(label), timeout=CONTROL_MS)
            else:
                loc.select_option(value=str(value), timeout=CONTROL_MS)
        except Exception:
            raise StepError(f"{what}: no option {label if label is not None else value!r}") from None

    def text(self, selector):
        """The first match's text on one line, "" when there is none."""
        loc = self.pg.locator(selector)
        return " ".join(loc.first.inner_text().split()) if loc.count() else ""

    def attr(self, selector, name):
        loc = self.pg.locator(selector)
        return (loc.first.get_attribute(name) or "") if loc.count() else ""

    def texts(self, selector):
        return [" ".join(t.split()) for t in self.pg.locator(selector).all_inner_texts()]

    def count(self, selector):
        return self.pg.locator(selector).count()

    def until(self, test, what, timeout=CONFIRM_S, slow=SLOW_S):
        """Wait until `test()` is truthy: the page showing the result of an action."""
        begin = self.clock()
        while True:
            try:
                value = test()
            except Exception as exc:  # a locator between two renders: not there yet
                value, detail = None, builtin.redact(exc)
            else:
                detail = ""
            waited = self.clock() - begin
            if value:
                if slow is not None and waited > slow:
                    self.ctx.finding("soft", f"{what}: the page took {waited:.1f}s to show it")
                return value
            if waited >= timeout:
                raise StepError(f"{what}: not shown within {timeout:g}s" + (f" ({detail})" if detail else ""))
            self.pg.wait_for_timeout(POLL_MS)

    def answered(self, act, path, what):
        """Run `act` and return the page's own request to a route ending in `path` once
        it is answered (a console page sends it below `/console`)."""
        try:
            with self.pg.expect_response(lambda r: urlsplit(r.url).path.endswith(path),
                                         timeout=CONTROL_MS) as info:
                act()
            resp = info.value
        except StepError:
            raise
        except Exception:
            raise StepError(f"{what}: the page sent no request to {path}") from None
        if resp.status >= 400:
            raise StepError(f"{what}: {path} answered HTTP {resp.status}")
        return resp

    def shown(self, selector):
        """The first match's text when a person can see it, else ""."""
        loc = self.pg.locator(selector)
        return self.text(selector) if loc.count() and loc.first.is_visible() else ""

    def accepting(self, act):
        """Run `act`, answering the page's one native confirm dialog with OK."""
        self.pg.once("dialog", lambda dialog: dialog.accept())
        act()

    def toggle(self, key, what, route, args, poll=None):
        """Press a toggle key whose light is the class `on`. With `on` in `args`, let the
        page finish its next `poll` and wait for the light to read the opposite first: the
        key toggles from the state the page last polled."""
        if "on" in args:
            want = bool(args["on"])
            if poll:
                self.answered(lambda: None, poll, f"{what}: the page's next poll")
            self.until(lambda: ("on" in self.attr(key, "class").split()) != want,
                       f"{what} reading {'off' if want else 'on'} before the press", slow=None)
        was = "on" in self.attr(key, "class").split()
        self.answered(lambda: self.click(key, what), route, what)
        self.until(lambda: ("on" in self.attr(key, "class").split()) != was, f"the {what} light")
        return {"on": not was}

    def facts(self):
        return {}

    def page(self):
        base = super().page()
        if self.pg is not None:
            base.update(self.facts())
        return base

    def shot(self, path, element=""):
        if self.pg is None:
            raise ShotUnavailable(f"role {self.name!r} has no open page")
        target = self.pg.locator(element).first if element else self.pg
        target.screenshot(path=path, mask=[self.pg.locator(self.sel(k)) for k in self.masked],
                          timeout=CONTROL_MS)


@register
class ProducerRole(Surface):
    """The producer in the Control Center: `pick_track` (track), `race_settings`
    (formation, finish_mode, laps), `start_event` (qualifying, stint), `stop_event`,
    `relay_restart`."""
    kind = "producer"
    service, ready = "ui", selectors.READY["PRODUCER"]
    sources = selectors.SOURCES["PRODUCER"]
    selectors = selectors.PRODUCER
    masked = ("invite", "timing")

    def view(self, name):
        self.click(self.sel("view", view=name), f"view {name}")

    def _no_error(self, name, what):
        loc = self.pg.locator(self.sel(name)).first
        if loc.count() and loc.is_visible():
            raise StepError(f"{what}: {self.text(self.sel(name))}")

    def act_pick_track(self, args):
        track = str(args.get("track") or self.ctx.event.get("track") or "")
        if not track:
            raise StepError("pick_track needs args.track or [event] track")
        self.view("home")
        self.until(lambda: self.count(self.sel("track_option", track=track)), f"layout {track} in the list")
        self.answered(lambda: self.choose(self.sel("track"), "Track layout", value=track),
                      "/api/event-track", "Track layout")
        self.until(lambda: self.control(self.sel("track"), "Track layout").input_value() == track,
                   "the picked layout")
        self._no_error("track_error", "Track layout")
        return {"track": track}

    def act_race_settings(self, args):
        done = {}
        self.view("home")
        if "formation" in args:
            box = self.control(self.sel("formation"), "Formation lap")
            if box.is_checked() != bool(args["formation"]):
                self.answered(lambda: box.set_checked(bool(args["formation"]), timeout=CONTROL_MS),
                              "/api/event-race", "Formation lap")
            done["formation"] = bool(args["formation"])
        if "finish_mode" in args:
            self.answered(lambda: self.choose(self.sel("finish_mode"), "How the race finishes",
                                              value=args["finish_mode"]), "/api/event-race", "Finish mode")
            done["finish_mode"] = args["finish_mode"]
        if "laps" in args:
            def enter():
                field = self.control(self.sel("laps"), "Lap count")
                field.fill(str(args["laps"]), timeout=CONTROL_MS)
                field.press("Tab", timeout=CONTROL_MS)
            self.answered(enter, "/api/event-race", "Lap count")
            done["laps"] = args["laps"]
        self._no_error("race_error", "Driver race")
        return done

    def _job(self, button, op, what):
        """Run the job behind `button` and wait for this job's result: the chip of an
        earlier job stays up until the new one starts."""
        chip = self.sel("job")
        self.click(self.sel(button), what)
        self.answered(lambda: self.click(self.sel("confirm"), f"{what}: confirm"), "/api/op/" + op, what)
        self.until(lambda: not self.pg.locator(chip).first.is_visible(), f"{what}: the job starting")
        self.until(lambda: self.shown(chip), f"{what}: the job's result", timeout=JOB_S, slow=None)
        result = self.text(chip)
        if result != "done":
            tail = builtin.redact(self.text(self.sel("job_log"))[-600:])
            raise StepError(f"{what}: the job ended {result!r}: {tail}")
        return {"job": result}

    def act_relay_restart(self, args):
        """Relay view, Restart: the relay comes back as a new process."""
        self.view("services")
        return self._job("relay_restart", "relay-restart", "Restart relay")

    def act_start_event(self, args):
        self.view("home")
        if args.get("stint"):
            self.fill(self.sel("stint"), args["stint"], "Stint number")
        if "qualifying" in args:
            self.control(self.sel("qualifying"), "Qualifying").set_checked(bool(args["qualifying"]),
                                                                           timeout=CONTROL_MS)
        return self._job("start", "event-start", "Start event")

    def act_stop_event(self, args):
        self.view("home")
        return self._job("stop", "event-stop", "Stop event")

    def facts(self):
        return {"summary": self.text(self.sel("summary")), "relay": self.text(self.sel("relay")),
                "job": self.text(self.sel("job"))}


@register
class DirectorRole(Surface):
    """The director on the Director Panel: `approve_link` (stint), `arm_feed` (feed),
    `arm`, `next`, `scene` (scene: a program key such as Stint, Split, Standby,
    Intermission), `graphic` (name, scene, on), `pov` (on), `hud` (field, value), `mode`
    (mode), `start_part`, `end_part`, `red_flag` (on), `show_dashboard` (on), `cue` (text,
    target, level), `chat` (text, name)."""
    kind = "director"
    path, ready = "/panel", selectors.READY["DIRECTOR"]
    sources = selectors.SOURCES["DIRECTOR"]
    selectors = selectors.DIRECTOR
    masked = ("links",)

    def area(self, name):
        self.click(self.sel("area", area=name), f"area {name}")

    def _classes(self, selector):
        return self.attr(selector, "class").split()

    def act_approve_link(self, args):
        stint = stint_label(args.get("stint", ""))
        self.area("handover")
        row = self.sel("handover_row", stint=stint)
        self.answered(lambda: self.click(self.sel("approve", stint=stint), f"APPROVE {stint}"),
                      "/submissions/approve", f"APPROVE {stint}")
        self.until(lambda: "link ready" in self.text(row), f"{stint}: link ready")
        return {"stint": stint}

    def act_arm_feed(self, args):
        feed = str(args.get("feed", "")).upper()
        if feed not in ("A", "B"):
            raise StepError("arm_feed needs args.feed = 'A' or 'B'")
        self.area("fault")
        self.answered(lambda: self.click(self.sel("feed_arm", feed=feed), f"ARM feed {feed}"),
                      f"/feed/{feed}/activate", f"ARM feed {feed}")
        state = self.sel("feed_state", feed=feed)
        self.until(lambda: "STOP" not in self.text(state), f"feed {feed} armed")
        return {"feed": feed, "state": self.text(state)}

    def act_arm(self, args):
        arm = self.sel("arm")
        self.control(arm, "ARM")
        label = self.text(arm)
        feed = self.attr(arm, "data-feed")
        if self.attr(arm, "data-on") != "1":
            raise StepError(f"ARM: the button offers no arming, it reads {label!r}")
        self.answered(lambda: self.click(arm, "ARM"), f"/feed/{feed}/activate", "ARM")
        self.until(lambda: {"starting", "ready"} & set(self._classes(arm)), "the feed starting")
        return {"feed": feed, "label": label}

    def act_next(self, args):
        try:
            self.until(lambda: self.count(self.sel("next_ready")), "NEXT ready", timeout=FEED_S, slow=None)
        except StepError:
            raise StepError(f"NEXT is not ready: {self.text(self.sel('next_sub'))}") from None
        before = self.text(self.sel("on_air"))
        self.answered(lambda: self.click(self.sel("next"), "NEXT"), "/next", "NEXT")
        self.until(lambda: self.text(self.sel("on_air")) != before, "the next stint on air")
        return {"on_air": self.text(self.sel("on_air"))}

    def act_scene(self, args):
        scene = str(args.get("scene", "")).strip()
        key = self.sel("scene", scene=scene.upper())
        try:
            self.until(lambda: scene and self.count(key) == 1, "the scene keys")
        except StepError:
            raise StepError(f"scene {scene!r}: {self.count(key) if scene else 0} matching scene keys") from None
        self.answered(lambda: self.click(key, f"scene {scene}"), "/obs/scene", f"scene {scene}")
        self.until(lambda: "air" in self._classes(key), f"scene {scene} on air")
        return {"scene": scene}

    def act_red_flag(self, args):
        """Press RED FLAG, a toggle on the cover state the panel last polled."""
        return self.toggle(self.sel("red_flag"), "RED FLAG", "/obs/source", args, poll="/obs/state")

    def _one(self, key, what):
        try:
            self.until(lambda: self.count(key) == 1, what)
        except StepError:
            raise StepError(f"{what}: {self.count(key)} matching keys ({key})") from None
        return key

    def act_graphic(self, args):
        """Toggle a graphic key by its caption; `scene` (the key's tag) tells two apart."""
        name, scene = str(args.get("name", "")).upper(), str(args.get("scene", ""))
        self.area("graphics")
        key = self.sel("graphic_in", name=name, scene=scene) if scene else self.sel("graphic", name=name)
        out = self.toggle(self._one(key, f"graphic {name}"), f"graphic {name}", "/obs/source", args,
                          poll="/obs/state")
        return dict(out, name=name)

    def act_pov(self, args):
        """Toggle the POV box's visibility."""
        return self.toggle(self._one(self.sel("pov"), "POV"), "POV", "/pov/toggle", args)

    def act_hud(self, args):
        """Set a HUD field (stint, streamer, session, flag, racecontrol); "" clears it."""
        field, value = str(args.get("field", "")), str(args.get("value", ""))
        if field not in HUD_FIELDS:
            raise StepError(f"hud field is one of {', '.join(HUD_FIELDS)}, not {field!r}")
        self.area("hud")
        box = self.sel("hud_field", field=field)
        route = f"/setup/set/{field}/{quote(value, safe=URI_SAFE)}" if value else f"/setup/clear/{field}"
        self.until(lambda: self.control(box, f"HUD {field}").is_enabled(), f"HUD {field} ready")
        self.answered(lambda: self.choose(box, f"HUD {field}", value=value), route, f"HUD {field}")
        self.until(lambda: self.control(box, f"HUD {field}").input_value() == value, f"HUD {field} = {value!r}")
        return {"field": field, "value": value}

    def act_mode(self, args):
        """Switch the relay between race and qualifying mode (Schedule, confirmed)."""
        mode = str(args.get("mode", "")).lower()
        if mode not in ("race", "qualifying"):
            raise StepError("mode is 'race' or 'qualifying'")
        self.area("schedule")
        switch = self.sel("mode_switch")
        self.control(switch, "mode switch")
        if mode.upper() not in self.text(switch).upper():
            raise StepError(f"the switch offers {self.text(switch)!r}, not {mode}")
        self.answered(lambda: self.accepting(lambda: self.click(switch, "mode switch")),
                      "/mode/" + mode, "mode switch")
        self.until(lambda: self.text(self.sel("mode_chip")).upper() == mode.upper(), f"{mode} mode")
        return {"mode": mode}

    def _part(self, verb, route, shows):
        self.area("broadcast")
        button = self.sel("part_button")
        self.control(button, f"{verb} Part")
        label = self.text(button)
        if not label.startswith(verb + " Part"):
            raise StepError(f"the Part button reads {label!r}, not {verb} Part")
        self.click(button, label)
        phrase = self.until(lambda: self.shown(self.sel("part_phrase")), "the confirmation phrase")
        self.fill(self.sel("part_input"), phrase, "confirmation phrase")
        self.answered(lambda: self.click(self.sel("part_confirm"), f"{label}: Confirm"), route, label)
        status = self.sel("part_status")
        self.until(lambda: any(word in self.text(status) for word in shows), f"{label}: the Part status")
        return {"part": label, "status": self.text(status)}

    def act_start_part(self, args):
        """Start the next broadcast Part: OBS goes live after the typed confirmation."""
        return self._part("Start", "/parts/start", ("LIVE",))

    def act_end_part(self, args):
        """End the live Part. Ending the last one stops the event, relay included."""
        return self._part("End", "/parts/end", ("ended", "complete", "ending"))

    def act_show_dashboard(self, args):
        on = bool(args.get("on", True))
        self.area("setup")
        if not self.pg.locator(self.sel("dashboard")).first.is_visible():
            self.click(self.sel("desk_open"), "Driver telemetry")
        show, hide = DESK["dashboard_show"], DESK["dashboard_hide"]
        want, shown = (hide, show) if on else (show, hide)
        button = self.sel("dashboard")
        self.control(button, "Show dashboard")
        if self.text(button) != shown:
            raise StepError(f"the dashboard button does not read {shown!r}")
        self.answered(lambda: self.click(button, shown), "/drivers/race/settings", shown)
        self.until(lambda: self.text(button) == want, f"the button reading {want!r}")
        return {"on": on}

    def act_cue(self, args):
        text = str(args.get("text", ""))
        self.area("cues")
        if args.get("target"):
            self.choose(self.sel("cue_target"), "Cue target", label=args["target"])
        if args.get("level"):
            self.choose(self.sel("cue_level"), "Cue level", label=args["level"])
        self.fill(self.sel("cue_text"), text, "Cue text")
        self.answered(lambda: self.click(self.sel("cue_send"), "SEND CUE"), "/cues/send", "SEND CUE")
        return {"text": text}

    def act_chat(self, args):
        text = str(args.get("text", ""))
        self.fill(self.sel("chat_name"), args.get("name", "Director"), "chat name")
        self.fill(self.sel("chat_text"), text, "chat message")
        self.click(self.sel("chat_send"), "SEND")
        self.until(lambda: text in self.text(self.sel("chat_log")), "the message in the crew chat")
        return {"text": text}

    def facts(self):
        return {"on_air": self.text(self.sel("on_air")),
                "arm": self.text(self.sel("arm")), "next": self.text(self.sel("next_sub")),
                "next_ready": bool(self.count(self.sel("next_ready"))),
                "next_step": self.text(self.sel("next_step")),
                "red_flag": "on" in self._classes(self.sel("red_flag"))}


@register
class RaceControlRole(Surface):
    """Race Control on its desk: `approve_driver` (name, entry), `approve_drivers`,
    `final_lap`, `finish`, `red_flag`, `release`, `retire` (entry), `rejoin` (entry),
    `estimate` (entry, ref, seconds, ahead), `correction` (entry, metres, pits, reason),
    `set_time` and `add_lap` (entry, time; qualifying), `note` (text, target)."""
    kind = "race_control"
    path, ready, crew = "/console/race-control", selectors.READY["RACE_CONTROL"], "Sim Race Control"
    base = "/race-control"
    sources = selectors.SOURCES["RACE_CONTROL"]
    selectors = selectors.RACE_CONTROL
    masked = ("links",)

    def _line(self):
        status = self.sel("status")
        return self.text(status), "err" in self.attr(status, "class").split()

    def _status(self, what, expect, before):
        """The desk's status line after an action; an error line fails the step. The
        line `before` the click counts only once it outlived STALE_S: the desk writes
        the new one a moment after its request is answered."""
        begin = self.clock()

        def settled():
            line = self._line()
            return line if line[0] and (line != before or self.clock() - begin >= STALE_S) else None
        text, error = self.until(settled, f"{what}: the desk's status line")
        if error:
            raise StepError(f"{what}: {text}")
        if not text.startswith(expect):
            raise StepError(f"{what}: the desk says {text!r}, not {expect!r}")
        return text

    def _press(self, label, route, done, armed=False):
        """Click the desk button `label`; an armed one asks for a second click."""
        button = self.sel("button", label=label)
        before = self._line()
        if armed:
            self.click(button, label)
            self.until(lambda: self.text(button) == DESK["confirm_prefix"] + label, f"{label}: the confirm state")
        self.answered(lambda: self.click(button, label), self.base + route, label)
        return {"status": self._status(label, done, before)}

    def act_approve_driver(self, args):
        name = str(args.get("name", ""))
        row = self.sel("request", name=name)
        try:
            before = self.until(lambda: self.count(row), "the request")
        except StepError:
            raise StepError(f"no waiting request of {name!r}") from None
        if args.get("entry"):
            self.choose(self.sel("request_entry", name=name), f"Entry of {name}", value=args["entry"])
        self.answered(lambda: self.click(self.sel("request_approve", name=name), f"Approve {name}"),
                      self.base + "/drivers/requests/approve", f"Approve {name}")
        self.until(lambda: self.count(row) < before, f"{name} approved")
        return {"name": name}

    def act_approve_drivers(self, args):
        """Approve every waiting request, top to bottom; none waiting is a failure."""
        self.until(lambda: self.count(self.sel("waiting")), "a waiting driver request")
        names = []
        while self.count(self.sel("waiting")):
            name = self.text(self.sel("waiting_name"))
            self.act_approve_driver({"name": name})
            names.append(name)
        return {"approved": len(names), "names": names}

    def act_final_lap(self, args):
        return self._press(DESK["final_lap"], "/drivers/race/final-lap", "Final lap", armed=True)

    def act_finish(self, args):
        return self._press(DESK["finish"], "/drivers/race/finish", "Race finished", armed=True)

    def act_red_flag(self, args):
        return self._press(DESK["red_flag"], "/red-flag", "RED FLAG", armed=True)

    def act_release(self, args):
        return self._press(DESK["release"], "/drivers/race/release", "Restart released", armed=True)

    def act_retire(self, args):
        self.choose(self.sel("select", label=DESK["retire_entry"]), "Entry to retire", value=args.get("entry"))
        return self._press(DESK["retire"], "/drivers/race/retire", "Retired", armed=True)

    def act_rejoin(self, args):
        self.choose(self.sel("select", label=DESK["rejoin_entry"]), "Team to rejoin", value=args.get("entry"))
        return self._press(DESK["rejoin"], "/drivers/race/rejoin", "Rejoin marked")

    def act_estimate(self, args):
        self.choose(self.sel("select", label=DESK["estimate_entry"]), "Quiet entry", value=args.get("entry"))
        self.choose(self.sel("select", label=DESK["estimate_side"]), "Behind or ahead",
                    label=DESK["side_ahead"] if args.get("ahead") else DESK["side_behind"])
        self.choose(self.sel("select", label=DESK["estimate_ref"]), "Reference entry", value=args.get("ref"))
        self.fill(self.sel("input", label=DESK["estimate_seconds"]), args.get("seconds", ""), "Seconds")
        return self._press(DESK["estimate"], "/drivers/race/estimate", "Estimate set")

    def act_correction(self, args):
        """Correct an entry's distance (`metres`) or pit stop count (`pits`), with a reason."""
        self.choose(self.sel("select", label=DESK["correct_entry"]), "Entry to correct", value=args.get("entry"))
        if "metres" in args:
            self.fill(self.sel("input", label=DESK["correct_metres"]), args["metres"], "metres")
        if "pits" in args:
            self.fill(self.sel("input", label=DESK["correct_pits"]), args["pits"], "pit stops")
        self.fill(self.sel("input", label=DESK["correct_reason"]), args.get("reason", ""), "Reason")
        return self._press(DESK["correct"], "/drivers/race/correct", "Correction applied")

    def act_set_time(self, args):
        """Set an entry's qualifying time (`time` as m:ss.mmm)."""
        self.choose(self.sel("select", label=DESK["quali_entry"]), "Qualifying entry", value=args.get("entry"))
        self.fill(self.sel("input", label=DESK["quali_time"]), args.get("time", ""), "Qualifying time")
        return self._press(DESK["set_time"], "/drivers/race/quali", "Qualifying time set")

    def act_note(self, args):
        """Send a note to the commentators (`target`: the Send to label, default as shown)."""
        text = str(args.get("text", ""))
        if args.get("target"):
            self.choose(self.sel("note_target"), "Send to", label=args["target"])
        self.fill(self.sel("note_text"), text, "note")
        message = self.sel("note_message")
        self.answered(lambda: self.click(self.sel("note_send"), "Send"), "/race-control/cues", "Send")
        self.until(lambda: self.text(message), "the note's receipt")
        if "err" in self.attr(message, "class").split():
            raise StepError(f"note: {self.text(message)}")
        return {"text": text}

    def act_add_lap(self, args):
        self.choose(self.sel("select", label=DESK["quali_entry"]), "Qualifying entry", value=args.get("entry"))
        self.fill(self.sel("input", label=DESK["quali_time"]), args.get("time", ""), "Qualifying time")
        return self._press(DESK["add_lap"], "/drivers/race/quali-lap", "Qualifying lap added")

    def facts(self):
        text, error = self._line()
        return {"waiting": self.count(self.sel("waiting")), "status": text, "status_error": error,
                "lap_line": self.text(self.sel("lap_line"))}


@register
class CommentatorRole(Surface):
    """A commentator in the cockpit (`commentator:<crew name>`): `submit_link` (url,
    stint), `chat` (text). `page(role)` reads the tally."""
    kind = "commentator"
    path, ready = "/cockpit", selectors.READY["COMMENTATOR"]
    sources = selectors.SOURCES["COMMENTATOR"]
    selectors = selectors.COMMENTATOR

    def open(self):
        super().open()
        if not self.instance:
            raise StepError("a commentator is `commentator:<crew name>`")

    def act_submit_link(self, args):
        url, stint = str(args.get("url", "")), stint_label(args.get("stint", ""))
        self.choose(self.sel("stint"), "Your stint", label=stint)
        self.fill(self.sel("url"), url, "stream link")
        resp = self.answered(lambda: self.click(self.sel("submit"), "Submit for approval"),
                             "/cockpit/submit", "Submit for approval")
        self.until(lambda: "Submitted" in self.text(self.sel("message")), "the submission's receipt")
        try:
            data = resp.json()
        except Exception:
            data = {}
        return {"id": data.get("id") if isinstance(data, dict) else None, "stint": stint}

    def act_chat(self, args):
        text = str(args.get("text", ""))
        self.fill(self.sel("chat_text"), text, "chat message")
        self.click(self.sel("chat_send"), "Send")
        self.until(lambda: text in self.text(self.sel("chat_log")), "the message in the crew chat")
        return {"text": text}

    def facts(self):
        tally = self.sel("tally")
        return {"tally": self.text(tally),
                "on_air": "onair" in self.attr(tally, "class").split(),
                "pending": self.text(self.sel("pending")), "message": self.text(self.sel("message"))}


@register
class ViewerRole(Surface):
    """A viewer on the public timing page: `read` (cars) returns what the page shows."""
    kind = "viewer"
    ready = selectors.READY["VIEWER"]
    sources = selectors.SOURCES["VIEWER"]
    selectors = selectors.VIEWER
    MAX_ROWS = 20

    def target(self):
        return "/console/timing?l=" + quote(self.ctx.event_link("timing"), safe="")

    def facts(self):
        rows = self.texts(self.sel("rows"))[:self.MAX_ROWS]
        return {"session": self.text(self.sel("session")),
                "state": self.shown(self.sel("state")), "fastest": self.shown(self.sel("fastest")),
                "rows": rows, "cars": len(rows)}

    def act_read(self, args):
        """What the page shows once it lists `cars` rows (default 1; 0 reads at once)."""
        cars = int(args.get("cars", 1))
        self.until(lambda: self.count(self.sel("rows")) >= cars, f"{cars} cars in the timing")
        return self.facts()

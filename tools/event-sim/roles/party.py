"""The parties of an event that work on a page: producer (Control Center), director
(Director Panel), Race Control (its desk), commentator (cockpit) and viewer (public
timing). Each acts in its own browser context on the controls a person would use: a
control that is missing, hidden or disabled is a hard failure, and an action returns
only after the page showed its result. Without a browser every action is skipped.

A driver (the invite page on a phone) is a party with a page too.

An action is a mechanism (click this, submit that); the scenario decides the order."""
import re
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
PLAN_S = 60.0           # the restart plan needs a validated lobby lap of the leader
DETACHED = "not attached"   # Playwright's text for an element the page replaced
HUD_FIELDS = ("stint", "streamer", "session", "flag", "racecontrol")
URI_SAFE = "!'()*~"      # what the page's encodeURIComponent leaves as it is
MEDIA_PLAYING = "OBS_MEDIA_STATE_PLAYING"
MEDIA_SLACK_S = 10.0     # how long past its length a clip may take to end on air
ERROR_S = 2.0            # how long a page takes to show the reason of a refused request
POINTER_STEPS = 12       # mouse moves per pointer travel in a record run: visible motion, not a jump
HOLD_MAX_S = 300
STANDINGS_HOLD_S = 8
DURATION = re.compile(r"\d{1,2}:[0-5]\d:[0-5]\d")   # H:MM:SS as the panel's timer shows it


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
        if self.pg is None and action not in ("open", "close"):
            self.act_open({})
        return super().do(action, args)

    def act_hold(self, args):
        """Keep the page in view for `seconds`: the action lasts that long, so the film
        plays the moment in real time."""
        seconds = args.get("seconds")
        if type(seconds) not in (int, float) or not 0 < seconds <= HOLD_MAX_S:
            raise StepError(f"hold: seconds is a number above 0 and up to {HOLD_MAX_S}, not {seconds!r}")
        with self.timed("wait", f"hold {seconds:g}s", budget=None):
            self.pg.wait_for_timeout(seconds * 1000)
        return {"seconds": seconds}

    # -- acting on controls ---------------------------------------------------
    def sel(self, key, **values):
        return self.selectors[key].format(**values)

    def control(self, selector, what):
        """The visible, enabled control at `selector`, scrolled into view."""
        with self.timed("control", what):
            return self._control(selector, what)

    def _control(self, selector, what):
        loc = self.pg.locator(selector).first
        try:
            loc.wait_for(state="visible", timeout=CONTROL_MS)
        except Exception:
            raise StepError(f"{what}: the control is missing or hidden ({selector})") from None
        if not loc.is_enabled():
            raise StepError(f"{what}: the control is disabled ({selector})")
        self._note_outside(loc, what)
        # A desk re-renders its rows with every poll: a row replaced between finding and
        # scrolling is found again, the locator resolves to the new one.
        begin = self.clock()
        while True:
            try:
                loc.scroll_into_view_if_needed(timeout=CONTROL_MS)
                return loc
            except Exception as exc:
                if DETACHED not in str(exc):
                    raise
                if self.clock() - begin >= CONTROL_MS / 1000:
                    raise StepError(f"{what}: the page keeps replacing the control, {DETACHED}") from None
                self.pg.wait_for_timeout(POLL_MS)

    def _note_outside(self, loc, what):
        box, view = loc.bounding_box(), self.viewport
        if not box or what in self._noted:
            return
        if box["y"] < 0 or box["x"] < 0 or box["y"] + box["height"] > view["height"] \
                or box["x"] + box["width"] > view["width"]:
            self._noted.add(what)
            self.ctx.finding("soft", f"{what}: the control is outside the {view['width']}x{view['height']} "
                                     "viewport, the page has to be scrolled")

    def point(self, loc):
        """In a record run, move the visible pointer to the control's centre; returns `loc`."""
        rec = getattr(self.ctx, "recorder", None)
        box = loc.bounding_box() if rec else None
        if box:
            self.pg.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=POINTER_STEPS)
        return loc

    def click(self, selector, what):
        with self.timed("click", what):
            self.point(self.control(selector, what)).click(timeout=CONTROL_MS)

    def fill(self, selector, text, what):
        with self.timed("fill", what):
            self.point(self.control(selector, what)).fill(str(text), timeout=CONTROL_MS)

    def choose(self, selector, what, label=None, value=None):
        with self.timed("choose", what):
            loc = self.point(self.control(selector, what))
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
        with self.timed("wait", what, budget=None):
            return self._until(test, what, timeout, slow)

    def _until(self, test, what, timeout, slow):
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
        it is answered (a console page sends it below `/console`). The answer may come
        during `act` or up to CONTROL_MS after it; `act` keeps its own timeouts."""
        with self.timed("request", what):
            return self._answered(act, path, what)

    def _answered(self, act, path, what):
        got = []

        def seen(resp):
            if not got and urlsplit(resp.url).path.endswith(path):
                got.append(resp)
        self.pg.on("response", seen)
        try:
            act()
            begin = self.clock()
            while not got and self.clock() - begin < CONTROL_MS / 1000:
                self.pg.wait_for_timeout(POLL_MS)
        except StepError:
            raise
        except Exception as exc:  # Playwright's text says what the act met
            detail = builtin.redact(str(exc).splitlines()[0] if str(exc) else type(exc).__name__, self.ctx.secrets)
            raise StepError(f"{what}: the page sent no request to {path} ({detail})") from None
        finally:
            self.pg.remove_listener("response", seen)
        if not got:
            raise StepError(f"{what}: the page sent no request to {path} within {CONTROL_MS / 1000:g}s "
                            "of the action")
        resp = got[0]
        if resp.status >= 400:
            raise StepError(f"{what}: {path} answered HTTP {resp.status}")
        return resp

    def shown(self, selector):
        """The first match's text when a person can see it, else ""."""
        loc = self.pg.locator(selector)
        return self.text(selector) if loc.count() and loc.first.is_visible() else ""

    def accepting(self, act, text=None):
        """Run `act`, answering the one native dialog it opens with OK (with `text` for a
        prompt). The answer is withdrawn when `act` ends, so no later dialog gets it."""
        answered = []

        def answer(dialog):
            if not answered:
                answered.append(True)
                dialog.accept() if text is None else dialog.accept(text)
        self.pg.on("dialog", answer)
        try:
            return act()
        finally:
            self.pg.remove_listener("dialog", answer)

    def toggle(self, key, what, route, args, poll=None):
        """Press a toggle key whose light is the class `on`. With `on` in `args`, let the
        page finish its next `poll` and wait for the light to read the opposite first: the
        page sends the state its light shows, and the relay switches only from that state."""
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
        self._screenshot(element, path, [self.pg.locator(self.sel(k)) for k in self.masked], CONTROL_MS)


class LiveColumn:
    """A page with a live column (its `live` selector): `fit` fails when a visible control
    there needs scrolling, the layout promise of the panel and the desk (#1267). It waits
    for the role's `live_required` controls plus `require` (labels) and fails without them.
    `viewport` ([width, height]) measures in that window; `reachable` then only asks that
    scrolling the column reaches every control, so nothing clips one. The page may still
    settle its layout (the panel's program tile shrinks a frame later), so it measures
    again until the controls fit or the time is up. `rows` (a count) also asks that the
    page shows at least that many of its `rows` (the cockpit's timing cars) and that every
    row it shows fits whole, every column included, without scrolling: the count proves
    the cars are there, and no shown car may hide. `view` (area) opens one of its areas, so a screenshot shows it."""
    live_required = ()

    def area(self, name):
        self.click(self.sel("area", area=name), f"area {name}")

    def act_view(self, args):
        """Open an area, so a screenshot shows it."""
        self.area(str(args.get("area", "")))
        return {"area": str(args.get("area", ""))}

    def _labels_in(self, found, labels):
        names = [c["label"] for c in found]
        return [want for want in labels if not any(n == want or n.startswith(want) for n in names)]

    def act_fit(self, args):
        size = args.get("viewport")
        if size is not None and (not isinstance(size, list) or len(size) != 2
                                 or not all(type(v) is int and v > 0 for v in size)):
            raise StepError(f"fit: viewport is [width, height], not {size!r}")
        want = list(self.live_required) + [str(x) for x in args.get("require", [])]
        key = "clip" if args.get("reachable") else "cut"
        rows = args.get("rows")
        if rows is not None:
            if type(rows) is not int or rows < 1:
                raise StepError(f"fit: rows is a whole number of at least 1, not {rows!r}")
            if "rows" not in self.selectors:
                raise StepError(f"{self.kind}: the page has no rows to measure")
        if size:
            self.pg.set_viewport_size({"width": size[0], "height": size[1]})
        last = {}

        def settled():
            found = last["found"] = self.fit_report(self.sel("live"))
            if not found or self._labels_in(found, want):
                return None
            if rows:
                shown = last["rows"] = self.rows_report(self.sel("rows")) or []
                if len(shown) < rows:
                    return None
            last["cut"] = [(c["label"], c[key]) for c in found if c[key] > builtin.FIT_TOLERANCE_PX]
            last["rows_cut"] = [(c["label"], max(c["cut"], c.get("side", 0))) for c in last.get("rows", [])
                                if max(c["cut"], c.get("side", 0)) > builtin.FIT_TOLERANCE_PX]
            return not last["cut"] and not last["rows_cut"]
        try:
            try:
                self.until(settled, f"{self.kind}: the live controls", slow=None)
            except StepError:
                found = last.get("found")
                if found is None:
                    raise StepError(f"{self.kind}: the page has no live column ({self.sel('live')})") from None
                if not found:
                    raise StepError(f"{self.kind}: the live column shows no control") from None
                if self._labels_in(found, want):
                    raise StepError(f"{self.kind}: the live column lacks {', '.join(self._labels_in(found, want))}") \
                        from None
                if rows and len(last.get("rows", [])) < rows:
                    raise StepError(f"{self.kind}: the page shows {len(last.get('rows', []))} of {rows} rows "
                                    f"({self.sel('rows')})") from None
        finally:
            if size:
                self.pg.set_viewport_size(dict(self.viewport))
        found, cut = last["found"], last.get("cut", [])
        if cut:
            how = "are clipped" if key == "clip" else "need scrolling"
            raise StepError(f"{self.kind}: {len(cut)} of {len(found)} live controls {how}: " +
                            ", ".join(f"{label} {px}px" for label, px in cut[:8]))
        if last.get("rows_cut"):
            raise StepError(f"{self.kind}: {len(last['rows_cut'])} of {len(last['rows'])} rows need scrolling: " +
                            ", ".join(f"{label} {px}px" for label, px in last["rows_cut"][:8]))
        return {"controls": len(found), "rows": len(last["rows"])} if rows else {"controls": len(found)}


class CrewChat:
    """`chat` (text, and `name` on a page with a name field, default `chat_as`): the
    message is sent once the crew chat shows it."""
    chat_as = ""

    def act_chat(self, args):
        text = str(args.get("text", ""))
        if "chat_name" in self.selectors:
            self.fill(self.sel("chat_name"), args.get("name", self.chat_as), "chat name")
        self.fill(self.sel("chat_text"), text, "chat message")
        self.click(self.sel("chat_send"), "chat send")
        self.until(lambda: text in self.text(self.sel("chat_log")), "the message in the crew chat")
        return {"text": text}


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
    JOBS = "/api/jobs/"

    def open(self):
        super().open()
        self.ended = set()       # paths of the job event streams the page saw end

    def act_open(self, args):
        fresh = self.pg is None
        out = super().act_open(args)
        if fresh:
            self.pg.on("requestfinished", self._finished)
        return out

    def _finished(self, request):
        path = urlsplit(request.url).path
        if path.startswith(self.JOBS):
            self.ended.add(path)

    def _requestfailed(self, request):
        # The page's done handler may close the stream before the server does.
        super()._requestfailed(request)
        self._finished(request)

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
        """Run the job behind `button` and wait for this job's result: once the page's
        event stream of this job (its id from the start answer) has ended, the chip shows
        it. The page hides the chip only while a job runs, which a quick job ends between
        two looks, so that is no sign of this job."""
        chip = self.sel("job")
        self.click(self.sel(button), what)
        resp = self.answered(lambda: self.click(self.sel("confirm"), f"{what}: confirm"), "/api/op/" + op, what)
        try:
            job = resp.json().get("job_id")
        except Exception:  # noqa: BLE001  an answer without a job is checked below
            job = None
        if not job:
            raise StepError(f"{what}: the Control Center started no job")
        stream = f"{self.JOBS}{job}/stream"
        self.until(lambda: stream in self.ended, f"{what}: the job's end", timeout=JOB_S, slow=None)
        self.until(lambda: self.shown(chip), f"{what}: the job's result")
        result = self.text(chip)
        if result != "done":
            tail = builtin.redact(self.text(self.sel("job_log")), self.ctx.secrets)[-600:]
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
class DirectorRole(LiveColumn, CrewChat, Surface):
    """The director on the Director Panel: `approve_link` (stint, area), `next`, `scene`
    (scene: a program key such as Stint, Split, Standby, Intermission), `graphic` (name, scene, on), `pov` (on), `hud` (field, value), `mode`
    (mode), `start_part`, `end_part`, `red_flag` (on), `show_dashboard` (on), `cue` (text,
    target, level), `chat` (text, name), `play` (media: intro, trailer or outro), `timer`
    (duration as H:MM:SS), `timer_key` (key: START or RESET), `view` (area), `fit`."""
    kind = "director"
    vocab_stint_actions = ("approve_link",)   # their `stint` is a label of the Configuration Stints column
    path, ready = "/panel", selectors.READY["DIRECTOR"]
    sources = selectors.SOURCES["DIRECTOR"]
    live_required = selectors.LIVE_REQUIRED["DIRECTOR"]
    selectors = selectors.DIRECTOR
    masked = ("links",)
    chat_as = "Director"

    def _classes(self, selector):
        return self.attr(selector, "class").split()

    def act_approve_link(self, args):
        """APPROVE a submitted link in the Handover area, or with `area = "schedule"` in the
        Schedule area's pending list, which also holds the links the handover never lists
        (the stint on air, qualifying)."""
        stint, area = stint_label(args.get("stint", "")), args.get("area", "handover")
        if area == "schedule":
            return self._approve_submission(stint)
        if area != "handover":
            raise StepError(f"approve_link: area is handover or schedule, not {area!r}")
        self.area("handover")
        row = self.sel("handover_row", stint=stint)
        self.answered(lambda: self.click(self.sel("approve", stint=stint), f"APPROVE {stint}"),
                      "/submissions/approve", f"APPROVE {stint}")
        self.until(lambda: "link ready" in self.text(row), f"{stint}: link ready")
        return {"stint": stint}

    def _approve_submission(self, stint):
        self.area("schedule")
        row = self.sel("submission", stint=stint)
        self.until(lambda: self.count(row) == 1, f"one pending submission for {stint}")
        self.answered(lambda: self.click(self.sel("submission_approve", stint=stint), f"APPROVE {stint}"),
                      "/submissions/approve", f"APPROVE {stint}")
        self.until(lambda: not self.count(row), f"{stint}: the submission approved")
        return {"stint": stint}

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

    def _clip(self, source):
        """OBS's (mediaState, mediaDuration, mediaCursor) of a clip in ms: the panel shows none."""
        try:
            status = self.ctx.obs.state.request("GetMediaInputStatus", {"inputName": source}, origin="http") or {}
        except Exception as exc:  # noqa: BLE001  OBS's own words say why
            raise StepError(f"{source}: OBS gives no media status ({builtin.redact(exc)})") from None
        return status.get("mediaState"), status.get("mediaDuration"), status.get("mediaCursor")

    def _playing(self, source):
        """(length, seconds left of the current loop) of a playing clip, else None. The
        clips loop, so OBS never reports their end: a cursor counts within its loop."""
        state, length, cursor = self._clip(source)
        if state != MEDIA_PLAYING or not isinstance(length, (int, float)) or length <= 0:
            return None
        at = cursor % length if isinstance(cursor, (int, float)) and cursor >= 0 else 0
        return length / 1000, (length - at) / 1000

    def act_play(self, args):
        """Take the Intro, Trailer or Outro key and return once the clip reached the end of
        the loop it plays. The panel shows no media state: OBS's cursor tells the end."""
        name = str(args.get("media", "")).strip().lower()
        source = selectors.MEDIA.get(name)
        if source is None:
            raise StepError(f"play: media is intro, trailer or outro, not {name!r}")
        if self.ctx.obs is None:
            raise StepError(f"play {name}: this run has no OBS to tell when the clip ends")
        key = self._one(self.sel("media", source=source), f"{name} key")
        self.answered(lambda: self.click(key, source), "/obs/scene", source)
        self.until(lambda: "air" in self._classes(key), f"{source} on air")
        length, left = self.until(lambda: self._playing(source), f"{source} playing", slow=None)
        begin = self.clock()
        self.until(lambda: self.clock() - begin >= left, f"{source}: its end", timeout=left + MEDIA_SLACK_S, slow=None)
        return {"media": name, "source": source, "length_s": round(length, 1), "left_s": round(left, 1)}

    def act_red_flag(self, args):
        """Press RED FLAG, a toggle from the cover state the key shows."""
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
        self.accepting(lambda: self.answered(lambda: self.click(switch, "mode switch"), "/mode/" + mode, "mode switch"))
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

    def act_timer(self, args):
        """SET… in the HUD area's Timer keys: the race duration, typed into the panel's prompt."""
        duration = str(args.get("duration", ""))
        if not DURATION.fullmatch(duration):
            raise StepError(f"timer: duration is H:MM:SS, not {duration!r}")
        self.area("hud")
        # The prompt's answer stays armed until its request is answered, then never longer.
        self.accepting(lambda: self.answered(lambda: self.click(self.sel("timer_set"), "Timer SET"),
                                             "/timer/set/" + duration, "Timer SET"), duration)
        self.until(lambda: f"duration {duration}" in self.text(self.sel("timer_info")), f"the timer's duration {duration}")
        return {"duration": duration}

    TIMER_KEYS = {"START": "/timer/start", "RESET": "/timer/reset"}

    def act_timer_key(self, args):
        """START or RESET in the HUD area's Timer keys."""
        key = args.get("key")
        if key not in self.TIMER_KEYS:
            raise StepError(f"timer_key: key is {' or '.join(self.TIMER_KEYS)}, not {key!r}")
        self.area("hud")
        self.answered(lambda: self.click(self.sel("timer_key", label=key), f"Timer {key}"),
                      self.TIMER_KEYS[key], f"Timer {key}")
        return {"key": key}

    def act_start_part(self, args):
        """Start the next broadcast Part: OBS goes live after the typed confirmation."""
        return self._part("Start", "/parts/start", ("LIVE",))

    def act_end_part(self, args):
        """End the live Part. Ending the last one stops the event, relay included."""
        return self._part("End", "/parts/end", ("ended", "complete", "ending"))

    def act_show_dashboard(self, args):
        on = bool(args.get("on", True))
        self.area("race")
        if not self.pg.locator(self.sel("dashboard")).first.is_visible():
            self.click(self.sel("desk_tab", sub="dashboard"), "Race desk: Dashboard")
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

    def facts(self):
        return {"on_air": self.text(self.sel("on_air")),
                "up_next": self.text(self.sel("up_next")), "next": self.text(self.sel("next_sub")),
                "next_ready": bool(self.count(self.sel("next_ready"))),
                "next_step": self.text(self.sel("next_step")),
                "red_flag": "on" in self._classes(self.sel("red_flag"))}


@register
class RaceControlRole(LiveColumn, Surface):
    """Race Control on its desk: `approve_driver` (name, entry), `approve_drivers` (one by
    one), `approve_all` (Approve all waiting), `reject_driver` (name), `correct_driver`
    (name, entry, stint), `compound` (entry, code), `compounds` (code, skip), `race_start`,
    `show_standings` (entry), `final_lap`, `finish`, `red_flag`, `release`, `retire` (entry), `rejoin` (entry),
    `estimate` (entry, ref, seconds, ahead), `correction` (entry, metres, pits, reason),
    `set_time` and `add_lap` (entry, time; qualifying), `note` (text, target), `confirm_dc`
    (entry), `class_laps` (class, stint, laps), `view` (area), `fit`. An
    action first opens the desk area that holds its controls; the race controls stay in
    the live column."""
    kind = "race_control"
    path, ready, crew = "/console/race-control", selectors.READY["RACE_CONTROL"], "Sim Race Control"
    base = "/race-control"
    sources = selectors.SOURCES["RACE_CONTROL"]
    live_required = selectors.LIVE_REQUIRED["RACE_CONTROL"]
    selectors = selectors.RACE_CONTROL
    masked = ("links",)
    tabs = ("standings",)    # a record run records the Show standings tab as `race_control:standings`

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

    def _waiting(self, name):
        """The waiting request of `name` and how many of that name wait."""
        self.area("drivers")
        row = self.sel("request", name=name)
        try:
            return row, self.until(lambda: self.count(row), "the request")
        except StepError:
            raise StepError(f"no waiting request of {name!r}") from None

    def _approve(self, name, row, before):
        self.answered(lambda: self.click(self.sel("request_approve", name=name), f"Approve {name}"),
                      self.base + "/drivers/requests/approve", f"Approve {name}")
        self.until(lambda: self.count(row) < before, f"{name} approved")

    def act_approve_driver(self, args):
        name = str(args.get("name", ""))
        row, before = self._waiting(name)
        if args.get("entry"):
            self.choose(self.sel("request_entry", name=name), f"Entry of {name}", value=args["entry"])
        self._approve(name, row, before)
        return {"name": name}

    def act_correct_driver(self, args):
        """Change a waiting request's entry or stint (a multi-lobby event), then approve it."""
        name, entry = str(args.get("name", "")), str(args.get("entry") or "")
        stint = stint_label(args["stint"]) if args.get("stint") else ""
        if not entry and not stint:
            raise StepError("correct_driver needs args.entry or args.stint")
        row, before = self._waiting(name)
        if entry:
            self.choose(self.sel("request_entry", name=name), f"Entry of {name}", value=entry)
        if stint:
            self.choose(self.sel("request_stint", name=name), f"Stint of {name}", value=stint)
        approved = self.sel("approved", name=name)

        def matching():
            rows = [t.split(" \u00b7 ") for t in self.texts(approved)]
            return sum(1 for r in rows if (not entry or r[0] == entry) and (not stint or r[-1] == stint))
        had, line = matching(), self._line()
        self._approve(name, row, before)
        status = self._status(f"Approve {name}", "Driver approved", line)
        self.until(lambda: matching() > had, f"{name} approved as {' '.join(filter(None, [entry, stint]))}")
        return {"name": name, "entry": entry, "stint": stint, "status": status}

    def act_reject_driver(self, args):
        """Reject a waiting request: Reject is armed, a second click confirms it."""
        name = str(args.get("name", ""))
        row, before = self._waiting(name)
        button, what = self.sel("request_reject", name=name), f"Reject {name}"
        line = self._line()
        self.click(button, what)
        self.until(lambda: self.text(button) == DESK["confirm_prefix"] + DESK["reject"], f"{what}: the confirm state")
        self.answered(lambda: self.click(button, what), self.base + "/drivers/requests/reject", what)
        self.until(lambda: self.count(row) < before, f"{name} rejected")
        return {"name": name, "status": self._status(what, "Driver rejected", line)}

    def act_compound(self, args):
        """Set an entry's tyre compound (`code` such as RM, "" clears it) in the tyre table."""
        entry, code = str(args.get("entry", "")), str(args.get("code") or "")
        self.area("dashboard")
        box, what = self.sel("compound", entry=entry), f"compound of {entry}"
        line = self._line()

        def pick():
            self.control(box, what).focus(timeout=CONTROL_MS)   # the desk answers in the part with the focus
            self.choose(box, what, value=code)
        self.answered(pick, self.base + "/drivers/race/compound", what)
        return {"entry": entry, "code": code, "status": self._status(what, "Compound set", line)}

    def act_approve_drivers(self, args):
        """Approve every waiting request, top to bottom; none waiting is a failure."""
        self.area("drivers")
        self.until(lambda: self.count(self.sel("waiting")), "a waiting driver request")
        names = []
        while self.count(self.sel("waiting")):
            name = self.text(self.sel("waiting_name"))
            self.act_approve_driver({"name": name})
            names.append(name)
        return {"approved": len(names), "names": names}

    def act_approve_all(self, args):
        """Approve all waiting: one confirmed press approves the requests the desk lists."""
        self.area("drivers")
        try:
            self.until(lambda: self.count(self.sel("waiting")), "a waiting driver request")
        except StepError:
            raise StepError("approve_all: no waiting driver request") from None
        label = DESK["approve_all"]
        button, before = self.sel("button", label=label), self._line()
        self.click(button, label)
        self.until(lambda: self.text(button) == DESK["confirm_prefix"] + label, f"{label}: the confirm state")
        resp = self.answered(lambda: self.click(button, label), self.base + "/drivers/requests/approve-pending", label)
        try:
            body = resp.json() or {}
        except Exception:  # noqa: BLE001  an answer without a body approved nothing the step can name
            body = {}
        approved, skipped = list(body.get("approved") or []), list(body.get("skipped") or [])
        done = f"{len(approved)} driver" if approved else "No driver approved"
        return {"approved": len(approved), "skipped": len(skipped), "names": [a.get("name") for a in approved],
                "status": self._status(label, done, before)}

    def act_compounds(self, args):
        """Enter `code` as the compound of every entry the tyre table lists, except `skip`;
        an entry already on it is kept, since its select would send nothing."""
        code, skip = str(args.get("code") or ""), list(args.get("skip") or [])
        self.area("dashboard")
        try:
            entries = self.until(lambda: self.texts(self.sel("tyre_entries")), "the tyre table's entries")
        except StepError:
            raise StepError("compounds: the tyre table lists no entry") from None
        unknown = [e for e in skip if e not in entries]
        if unknown:
            raise StepError(f"compounds: skip: no entry {unknown[0]!r} in the tyre table")
        changed, kept = [], []
        for entry in entries:
            if entry in skip:
                continue
            if self.control(self.sel("compound", entry=entry), f"compound of {entry}").input_value() == code:
                kept.append(entry)
                continue
            self.act_compound({"entry": entry, "code": code})
            changed.append(entry)
        return {"code": code, "changed": changed, "kept": kept}

    def act_race_start(self, args):
        """Race start in the Race area, pressed with the cars on the grid."""
        self.area("race")
        return self._press(DESK["race_start"], "/drivers/race/start", "Race start armed", armed=True)

    def act_show_standings(self, args):
        """Show standings of `entry`'s disconnect event: the desk opens the frozen standings
        of before its last packet in a new tab, which stays in view for `seconds` once it
        lists its rows, then closes. A record run records the tab as `<role>:standings`."""
        entry, seconds = str(args.get("entry", "")), args.get("seconds", STANDINGS_HOLD_S)
        if type(seconds) not in (int, float) or not 0 < seconds <= HOLD_MAX_S:
            raise StepError(f"show_standings: seconds is a number above 0 and up to {HOLD_MAX_S}, not {seconds!r}")
        self.area("race")
        key, what = self.sel("dc_standings", entry=entry), f"Show standings of {entry}"
        try:
            self.control(key, what)
        except StepError:
            raise StepError(f"no event of {entry!r} to show the standings of") from None
        try:
            with self.context.expect_page(timeout=CONTROL_MS) as info:
                self.click(key, what)
            tab = info.value
        except StepError:
            raise
        except Exception as exc:  # noqa: BLE001  Playwright's text says why no tab opened
            raise StepError(f"{what}: no tab opened ({builtin.redact(exc, self.ctx.secrets)})") from None
        rec = getattr(self.ctx, "recorder", None)
        if rec:
            rec.page_opened(f"{self.name}:{self.tabs[0]}", tab, self.viewport)
        try:
            tab.wait_for_load_state(timeout=CONTROL_MS)
            rows = self.until(lambda: tab.locator(self.sel("snapshot_rows")).count(), "the frozen standings")
            with self.timed("wait", f"hold {seconds:g}s", budget=None):
                self.pg.wait_for_timeout(seconds * 1000)
        finally:
            tab.close()
            if rec:
                rec.page_closed(f"{self.name}:{self.tabs[0]}")
        return {"entry": entry, "rows": rows}

    def act_final_lap(self, args):
        return self._press(DESK["final_lap"], "/drivers/race/final-lap", "Final lap")

    def act_finish(self, args):
        return self._press(DESK["finish"], "/drivers/race/finish", "Race finished", armed=True)

    def act_red_flag(self, args):
        return self._press(DESK["red_flag"], "/red-flag", "RED FLAG", armed=True)

    def act_release(self, args):
        return self._press(DESK["release"], "/drivers/race/release", "Restart released", armed=True)

    def act_retire(self, args):
        self.area("corrections")
        self.choose(self.sel("select", label=DESK["retire_entry"]), "Entry to retire", value=args.get("entry"))
        return self._press(DESK["retire"], "/drivers/race/retire", "Retired", armed=True)

    def act_rejoin(self, args):
        self.area("corrections")
        self.choose(self.sel("select", label=DESK["rejoin_entry"]), "Team to rejoin", value=args.get("entry"))
        return self._press(DESK["rejoin"], "/drivers/race/rejoin", "Rejoin marked")

    def act_confirm_dc(self, args):
        """Confirm the open disconnect event of `entry` in the Race area's events list."""
        entry = str(args.get("entry", ""))
        self.area("race")
        key = self.sel("dc_confirm", entry=entry)
        try:
            self.control(key, f"Confirm DC of {entry}")
        except StepError:
            raise StepError(f"no open event of {entry!r} to confirm") from None
        before = self._line()
        self.answered(lambda: self.click(key, f"Confirm DC of {entry}"), self.base + "/drivers/race/recording/event",
                      f"Confirm DC of {entry}")
        return {"status": self._status("Confirm DC", "DC confirmed", before)}

    def act_class_laps(self, args):
        """Set the lap count of a class after the first one in one stint lobby (its column of
        the Race area's Stint laps); a count the field already holds is kept, since its input
        would send nothing."""
        cls, stint = str(args.get("class", "")), str(args.get("stint", ""))
        if not cls or not stint:
            raise StepError("class_laps needs args.class and args.stint, e.g. { class = \"GT3\", stint = \"Stint 3\" }")
        name = f"{cls} in {stint}"
        self.area("race")
        field = self.sel("input", label=DESK["class_laps"] + name)
        if self.control(field, f"Lap count of {name}").input_value() == str(args.get("laps", "")):
            return {"kept": True}
        before = self._line()

        def enter():
            box = self.control(field, f"Lap count of {name}")
            box.fill(str(args.get("laps", "")), timeout=CONTROL_MS)
            box.press("Tab", timeout=CONTROL_MS)
        self.answered(enter, self.base + "/drivers/race/stint-laps", f"Lap count of {name}")
        return {"status": self._status(f"Lap count of {name}", f"{cls} lap count of {stint} saved", before)}

    def act_stint_laps(self, args):
        """Set the lap count of each stint lobby ({label: laps}) in the Race area's Stint laps;
        a count its field already holds is kept, since its input would send nothing."""
        counts = args.get("laps")
        if not isinstance(counts, dict) or not counts:
            raise StepError("stint_laps needs args.laps, e.g. { \"Stint 1\" = 7 }")
        for label, laps in counts.items():
            if type(laps) is not int or laps < 1:
                raise StepError(f"the lap count of {label} is a whole number from 1, not {laps!r}")
        self.area("race")
        changed, kept = {}, []
        for label, laps in counts.items():
            field = self.sel("input", label=DESK["class_laps"] + label)
            if self.control(field, f"Lap count of {label}").input_value() == str(laps):
                kept.append(label)
                continue
            before = self._line()

            def enter(field=field, label=label, laps=laps):
                box = self.control(field, f"Lap count of {label}")
                box.fill(str(laps), timeout=CONTROL_MS)
                box.press("Tab", timeout=CONTROL_MS)
            self.answered(enter, self.base + "/drivers/race/stint-laps", f"Lap count of {label}")
            self._status(f"Lap count of {label}", f"{label} lap count saved", before)
            changed[label] = laps
        return {"set": changed, "kept": kept}

    def act_take_plan(self, args):
        """Wait for the red flag's restart plan, enter the `buffer` minutes, and take the
        suggested lap count for the restart lobby; returns it as `laps`."""
        self.area("race")
        if args.get("buffer") is not None:
            self.fill(self.sel("input", label=DESK["plan_buffer"]), str(args["buffer"]), "Buffer until the restart")
        take = self.sel("button", label=DESK["take"])
        self.until(lambda: self.count(take) and self.pg.locator(take).first.is_enabled(), "the restart plan",
                   timeout=PLAN_S, slow=None)
        before = self._line()
        self.answered(lambda: self.click(take, "Take"), self.base + "/drivers/race/stint-laps", "Take")
        text = self._status("Take", "Restart lobby set to", before)
        found = re.search(r"set to (\d+) laps", text)
        if not found:
            raise StepError(f"Take: the desk did not name the lap count: {text!r}")
        return {"laps": int(found.group(1)), "status": text}

    def act_estimate(self, args):
        self.area("corrections")
        self.choose(self.sel("select", label=DESK["estimate_entry"]), "Quiet entry", value=args.get("entry"))
        self.choose(self.sel("select", label=DESK["estimate_side"]), "Behind or ahead",
                    label=DESK["side_ahead"] if args.get("ahead") else DESK["side_behind"])
        self.choose(self.sel("select", label=DESK["estimate_ref"]), "Reference entry", value=args.get("ref"))
        self.fill(self.sel("input", label=DESK["estimate_seconds"]), args.get("seconds", ""), "Seconds")
        return self._press(DESK["estimate"], "/drivers/race/estimate", "Estimate set")

    def act_correction(self, args):
        """Correct an entry's distance (`metres`) or pit stop count (`pits`), with a reason."""
        self.area("corrections")
        self.choose(self.sel("select", label=DESK["correct_entry"]), "Entry to correct", value=args.get("entry"))
        if "metres" in args:
            self.fill(self.sel("input", label=DESK["correct_metres"]), args["metres"], "metres")
        if "pits" in args:
            self.fill(self.sel("input", label=DESK["correct_pits"]), args["pits"], "pit stops")
        self.fill(self.sel("input", label=DESK["correct_reason"]), args.get("reason", ""), "Reason")
        return self._press(DESK["correct"], "/drivers/race/correct", "Correction applied")

    def act_set_time(self, args):
        """Set an entry's qualifying time (`time` as m:ss.mmm)."""
        self.area("qualifying")
        self.choose(self.sel("select", label=DESK["quali_entry"]), "Qualifying entry", value=args.get("entry"))
        self.fill(self.sel("input", label=DESK["quali_time"]), args.get("time", ""), "Qualifying time")
        return self._press(DESK["set_time"], "/drivers/race/quali", "Qualifying time set")

    def act_note(self, args):
        """Send a note to the commentators (`target`: the Send to label, default as shown)."""
        self.area("note")
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
        self.area("qualifying")
        self.choose(self.sel("select", label=DESK["quali_entry"]), "Qualifying entry", value=args.get("entry"))
        self.fill(self.sel("input", label=DESK["quali_time"]), args.get("time", ""), "Qualifying time")
        return self._press(DESK["add_lap"], "/drivers/race/quali-lap", "Qualifying lap added")

    def facts(self):
        text, error = self._line()
        return {"waiting": self.count(self.sel("waiting")), "status": text, "status_error": error,
                "lap_line": self.text(self.sel("lap_line"))}


@register
class CommentatorRole(LiveColumn, CrewChat, Surface):
    """A commentator in the cockpit (`commentator:<crew name>`): `submit_link` (url,
    stint; it opens My stint), `chat` (text), `view` (area), `fit` (with `rows`, the
    timing cars). `page(role)` reads the tally and counts the timing rows."""
    kind = "commentator"
    vocab_stint_actions = ("submit_link",)   # their `stint` is a label of the Configuration Stints column
    by_commentator = True
    path, ready = "/cockpit", selectors.READY["COMMENTATOR"]
    sources = selectors.SOURCES["COMMENTATOR"]
    live_required = selectors.LIVE_REQUIRED["COMMENTATOR"]
    selectors = selectors.COMMENTATOR

    def open(self):
        super().open()
        if not self.instance:
            raise StepError("a commentator is `commentator:<crew name>`")

    def act_submit_link(self, args):
        url, stint = str(args.get("url", "")), stint_label(args.get("stint", ""))
        self.area("stint")
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

    def facts(self):
        tally = self.sel("tally")
        return {"tally": self.text(tally),
                "on_air": "onair" in self.attr(tally, "class").split(),
                "pending": self.text(self.sel("pending")), "message": self.text(self.sel("message")),
                "timing_rows": self.count(self.sel("rows"))}


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


@register
class DriverRole(Surface):
    """A driver on the event's invite page in a phone viewport: `register` (entry, name,
    stint when the event registers per stint). The page takes no console identity. The
    code the page shows goes to `ctx.driver_codes` for the drivers' field, never a result."""
    kind = "driver"
    ready = selectors.READY["DRIVER"]
    sources = selectors.SOURCES["DRIVER"]
    selectors = selectors.DRIVER
    masked = ("commands",)
    viewport = {"width": 390, "height": 844}

    def open(self):
        super().open()
        if self.instance:
            raise StepError("a driver is `driver`: the invite page has no console identity to open it as")

    def target(self):
        return "/console/drive?i=" + quote(self.ctx.event_link("invite"), safe="")

    def act_register(self, args):
        entry, name = str(args.get("entry", "")), str(args.get("name", ""))
        if not entry or not name:
            raise StepError("register needs args.entry and args.name")
        try:
            self.until(lambda: self.count(self.sel("entry_option", entry=entry)), f"entry {entry} in the list")
        except StepError:
            why = self.shown(self.sel("error"))
            raise StepError(f"the invite page offers no entry {entry!r}" + (f": {why}" if why else "")) from None
        self.choose(self.sel("entry"), "Entry", value=entry)
        stint = ""
        if self.pg.locator(self.sel("segment")).first.is_visible():
            if not args.get("stint"):
                raise StepError("the event registers per stint: register needs args.stint")
            stint = self._segment(args["stint"])
            self.choose(self.sel("segment"), "Stint or qualifying segment", value=stint)
        elif args.get("stint"):
            raise StepError("the invite page asks for no stint")
        self.fill(self.sel("name"), name, "Your name")
        try:
            resp = self.answered(lambda: self.click(self.sel("register"), "Get my sender command"),
                                 "/console/drive/register", "Get my sender command")
        except StepError as exc:
            try:
                why = self.until(lambda: self.shown(self.sel("error")), "the page's reason", timeout=ERROR_S, slow=None)
            except StepError:
                raise exc from None
            raise StepError(f"{exc}: {why}") from None
        try:
            code = (resp.json() or {}).get("code")
        except Exception:  # noqa: BLE001  an answer without a body is checked below
            code = None
        if not isinstance(code, str) or not code:
            raise StepError("register: the relay issued no sender code")
        self.ctx.secrets.add(code)
        # The commands of an earlier registration stay on screen: wait for this code.
        self.until(lambda: self.shown(self.sel("error")) or code in self.text(self.sel("command")),
                   "the sender command")
        if self.shown(self.sel("error")):
            raise StepError(f"register: {self.shown(self.sel('error'))}")
        self.ctx.driver_codes[(entry, stint)] = code
        return {"entry": entry, "name": name, "stint": stint}

    def _segment(self, stint):
        """The invite's label of `stint` (counted from 1, or a label), as the drivers role keys it."""
        labels = self.texts(self.sel("segments"))
        if type(stint) is int or str(stint).isdigit():
            n = int(stint)
            if not 1 <= n <= len(labels):
                raise StepError(f"the invite page has no stint {n} (it offers {', '.join(labels)})")
            return labels[n - 1]
        if stint not in labels:
            raise StepError(f"the invite page has no stint {stint!r} (it offers {', '.join(labels)})")
        return stint

    def facts(self):
        return {"registered": bool(self.shown(self.sel("command"))), "error": self.shown(self.sel("error"))}

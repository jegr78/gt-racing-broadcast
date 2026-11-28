#!/usr/bin/env python3
"""The event simulation's role drivers (#1233): the party roles on a fake page, the
drivers role on a fake relay client, the selectors against the shipped pages and the
stack's helpers for them. No browser, no socket, nothing spawned.
Run: python3 tests/test_event_sim_roles.py"""
import contextlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SIM = os.path.join(ROOT, "tools", "event-sim")
for _p in (SIM, os.path.join(SIM, "fakes"), os.path.join(ROOT, "src", "scripts"), os.path.join(ROOT, "tools")):
    sys.path.insert(0, _p)
import drive  # noqa: E402
import expect  # noqa: E402
import engine  # noqa: E402
import gt7_tracks  # noqa: E402
import roles  # noqa: E402
import scenario  # noqa: E402
import stack  # noqa: E402

drive.PACKETS_PER_TICK = 1   # the cipher is pure Python: one packet a tick keeps this quick
REG = roles.default_registry()
builtin, drivers, party = roles.builtin, roles.drivers, roles.party
BASE = "http://127.0.0.1:9"


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class El:
    def __init__(self, text="", visible=True, enabled=True, value="", checked=False, attrs=None,
                 options=(), on_click=None, on_change=None, box=None, count=1, texts=None):
        self.text, self.visible, self.enabled, self.value, self.checked = text, visible, enabled, value, checked
        self.attrs = dict(attrs or {})
        self.options, self.on_click, self.on_change = list(options), on_click, on_change
        self.box = box or {"x": 10, "y": 10, "width": 80, "height": 20}
        self.count, self.texts = count, texts


class Response:
    def __init__(self, url, status=200, body=None, at=None):
        self.url, self.status, self.body, self.at = url, status, body, at

    def json(self):
        return self.body


class Expect:
    def __init__(self, page, pred, timeout):
        self.page, self.pred, self.value, self.timeout = page, pred, None, timeout or 0

    def __enter__(self):
        self.mark, self.begin = len(self.page.responses), self.page.clock.t
        return self

    def __exit__(self, kind, exc, tb):
        if kind is None:
            while True:   # Playwright's timeout runs from the start, the act's time included
                hits = [r for r in self.page.responses[self.mark:] if self.pred(r)
                        and (r.at is None or (r.at - self.begin) * 1000 < self.timeout)]
                if hits or (self.page.clock.t - self.begin) * 1000 >= self.timeout:
                    break
                self.page.wait_for_timeout(100)
            if not hits:
                raise TimeoutError("Timeout exceeded while waiting for a response")
            self.value = hits[0]
        return False


class Locator:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector
        self.first = self

    def _el(self):
        el = self.page.els.get(self.selector)
        if el is None:
            raise TimeoutError(f"no element {self.selector}")
        return el

    def count(self):
        el = self.page.els.get(self.selector)
        return el.count if el is not None else 0

    def wait_for(self, state="visible", timeout=None):
        el = self.page.els.get(self.selector)
        if el is None or not el.visible or not el.count:
            raise TimeoutError(f"Timeout waiting for {self.selector}")

    def is_visible(self):
        el = self.page.els.get(self.selector)
        return bool(el and el.visible and el.count)

    def is_enabled(self):
        return self._el().enabled

    def is_checked(self):
        return self._el().checked

    def bounding_box(self):
        return self._el().box

    def focus(self, timeout=None):
        self.page.focused = self.selector

    def scroll_into_view_if_needed(self, timeout=None):
        el = self.page.els.get(self.selector)
        if el is not None and getattr(el, "rerenders", 0):
            el.rerenders -= 1
            raise RuntimeError("Locator.scroll_into_view_if_needed: Element is not attached to the DOM")
        self.page.scrolled.append(self.selector)

    def click(self, timeout=None):
        self.page.clicks.append(self.selector)
        if self._el().on_click:
            self._el().on_click()

    def fill(self, text, timeout=None):
        self._el().value = text

    def press(self, key, timeout=None):
        if self._el().on_change:
            self._el().on_change()

    def select_option(self, label=None, value=None, timeout=None):
        want = label if label is not None else value
        if want not in self._el().options:
            raise TimeoutError(f"no option {want}")
        self._el().value = want
        if self._el().on_change:
            self._el().on_change()

    def set_checked(self, on, timeout=None):
        self._el().checked = on
        if self._el().on_change:
            self._el().on_change()

    def inner_text(self):
        return self._el().text

    def all_inner_texts(self):
        el = self.page.els.get(self.selector)
        return list(el.texts if el and el.texts is not None else [])

    def input_value(self):
        return self._el().value

    def get_attribute(self, name):
        return self._el().attrs.get(name)

    def screenshot(self, path=None, mask=(), timeout=None):
        self.page.shots.append((path, self.selector, [m.selector for m in mask]))


class Page:
    def __init__(self, clock):
        self.clock, self.els = clock, {}
        self.responses, self.clicks, self.scrolled, self.shots, self.gotos, self.later = [], [], [], [], [], []
        self.url = ""
        self.handlers, self.listening = {}, []
        self.fit = {}            # live column selector -> what FIT_SCRIPT measured there
        self.size = (1440, 900)
        self.focused = None
        self.closed = False

    def add(self, selector, **kw):
        self.els[selector] = El(**kw)
        return self.els[selector]

    def respond(self, path, status=200, body=None):
        self.arrive(Response(BASE + path, status, body, at=self.clock.t))

    def arrive(self, resp):
        self.responses.append(resp)
        for fn in list(self.listening):
            fn(resp)

    def after(self, seconds, fn):
        self.later.append((self.clock.t + seconds, fn))

    def on(self, event, fn):
        if event == "response":   # the role's network record and a pending request listen at once
            self.listening.append(fn)
        else:
            self.handlers[event] = fn

    def once(self, event, fn):
        self.handlers["once:" + event] = fn

    def remove_listener(self, event, fn):
        if fn in self.listening:
            self.listening.remove(fn)
        for key in (event, "once:" + event):
            if self.handlers.get(key) is fn:
                del self.handlers[key]

    def _dialog_handler(self):
        return self.handlers.pop("once:dialog", None) or self.handlers.get("dialog")

    def confirm(self):
        """The page's native confirm(): True when a handler accepted it, as Playwright
        dismisses a dialog nobody handles."""
        fn = self._dialog_handler()
        if fn is None:
            return False
        answer = []
        fn(type("Dialog", (), {"accept": lambda self, text=None: answer.append(True)})())
        return bool(answer)

    def prompt(self):
        """The page's native prompt(): the text a handler accepted it with, else None."""
        fn = self._dialog_handler()
        if fn is None:
            return None
        answer = []
        fn(type("Dialog", (), {"accept": lambda self, text=None: answer.append(text)})())
        return answer[0] if answer else None

    def close(self):
        self.closed = True

    def wait_for_load_state(self, state="load", timeout=None):
        pass

    def goto(self, url, timeout=None):
        self.url = url
        self.gotos.append(url)
        return Response(url)

    def title(self):
        return "fake"

    def wait_for_selector(self, selector, state="visible", timeout=None):
        Locator(self, selector).wait_for()

    def wait_for_timeout(self, ms):
        self.clock.t += ms / 1000
        due = [item for item in self.later if item[0] <= self.clock.t]
        self.later = [item for item in self.later if item[0] > self.clock.t]
        for _, fn in due:
            fn()

    def locator(self, selector):
        return Locator(self, selector)

    def evaluate(self, script, arg=None):
        assert script in (roles.builtin.FIT_SCRIPT, roles.builtin.ROWS_SCRIPT), \
            "the only scripts a role runs measure the live column and the rows"
        found = self.fit.get(arg)
        return found(self.size) if callable(found) else found

    def set_viewport_size(self, size):
        self.size = (size["width"], size["height"])

    def expect_response(self, pred, timeout=None):
        return Expect(self, pred, timeout)

    def screenshot(self, path=None, mask=(), timeout=None, full_page=False):
        self.shots.append((path, "", [m.selector for m in mask]))


class PageWait:
    def __init__(self, context):
        self.context = context

    def __enter__(self):
        self.mark = len(self.context.opened)
        return self

    def __exit__(self, kind, exc, tb):
        if kind is None:
            if len(self.context.opened) <= self.mark:
                raise TimeoutError("Timeout exceeded while waiting for event \"page\"")
            self.value = self.context.opened[self.mark]
        return False


class Context:
    def __init__(self, page):
        self.page, self.closed, self.opened = page, False, []

    def expect_page(self, timeout=None):
        return PageWait(self)

    def new_page(self):
        return self.page

    def add_init_script(self, script):
        self.page.init_scripts = [*getattr(self.page, "init_scripts", []), script]

    def close(self):
        self.closed = True


class Browser:
    """Stands in for browser.Browser: `get()` is the Playwright browser or None."""

    def __init__(self, page=None):
        self.page, self.problem = page, "" if page else "Playwright is not installed"
        self.contexts, self.viewports = [], []

    def get(self):
        return self if self.page else None

    def new_context(self, viewport=None):
        self.viewports.append(viewport)
        self.contexts.append(Context(self.page))
        return self.contexts[-1]


def make(cls, name=None, page=None, event=None, links=None, browser=True):
    """(role, page, ctx): an opened role of `cls` on a fake page with its ready control."""
    clock = Clock()
    page = page or Page(clock)
    page.clock = clock
    ctx = engine.RunContext(urls={"relay": BASE, "ui": BASE}, token=lambda who: "tok-" + who,
                            browser=Browser(page if browser else None), event=event or {},
                            event_link=(links or {}).get)
    role = cls(ctx, name or cls.kind)
    role.open()
    role.clock = clock
    page.add(getattr(cls, "ready", "body"))
    return role, page, ctx


def fails(fn, *needles):
    try:
        fn()
    except roles.StepError as exc:
        for needle in needles:
            assert needle in str(exc), f"{needle!r} not in {exc}"
        return str(exc)
    raise AssertionError("a StepError was expected")


# -- every role, every selector ---------------------------------------------------
def t_the_registry_has_every_party_and_its_actions():
    want = {"producer": {"open", "close", "hold", "pick_track", "race_settings", "start_event", "stop_event", "relay_restart"},
            "director": {"open", "close", "hold", "approve_link", "next", "scene", "red_flag",
                         "show_dashboard", "cue", "chat", "graphic", "pov", "hud", "mode", "start_part",
                         "end_part", "fit", "view", "play", "timer", "timer_key"},
            "race_control": {"open", "close", "hold", "approve_driver", "approve_drivers", "final_lap", "formation_lap",
                             "finish", "red_flag",
                             "release", "retire", "rejoin", "estimate", "add_lap", "set_time", "correction",
                             "note", "fit", "confirm_dc", "class_laps", "stint_laps", "take_plan", "view", "reject_driver",
                             "correct_driver", "compound", "compounds", "race_start", "approve_all",
                             "show_standings"},
            "commentator": {"open", "close", "hold", "submit_link", "chat", "fit", "view"},
            "viewer": {"open", "close", "hold", "read"},
            "driver": {"open", "close", "hold", "register"},
            "drivers": {"register", "register_stint", "start", "pit", "box", "lobby", "release", "drop", "rejoin", "stop",
                        "chequer", "next_lobby", "change"},
            "probe": {"watch", "mark", "kit", "race_reset", "spectator", "stop_spectator", "check", "report"}}
    for kind, actions in want.items():
        assert kind in REG, f"role {kind} is not registered"
        assert set(REG[kind].action_names()) == actions, f"{kind}: {sorted(REG[kind].action_names())}"


def t_shared_actions_have_one_implementation():
    for name in ("area", "act_view", "act_fit"):
        for kind in ("race_control", "commentator"):
            assert getattr(REG["director"], name) is getattr(REG[kind], name), f"director and {kind} share {name}"
    assert REG["director"].act_chat is REG["commentator"].act_chat, "director and commentator share act_chat"


_TOKENS = re.compile(r'#([A-Za-z][\w-]*)|\.([A-Za-z][\w-]*)|\[[\w-]+="([^"]*)"\]|has-text\("([^"]*)"\)')


def selector_tokens(selector):
    """The ids, classes, attribute values and texts a selector names; a `{placeholder}`
    cuts a token at its fixed part."""
    out = []
    for groups in _TOKENS.findall(selector):
        token = next(g for g in groups if g) if any(groups) else ""
        fixed = max(token.split("{")[0], token.rsplit("}", 1)[-1], key=len) if "{" in token else token
        if fixed.strip():
            out.append(fixed.strip())
    return out


def t_selector_tokens_are_read_from_a_selector():
    assert selector_tokens('#driverCard .req.waiting select[aria-label="Entry of {name}"]') == \
        ["driverCard", "req", "waiting", "Entry of"]
    assert selector_tokens('#hoUpcoming .hrow:has-text("{stint}") button:has-text("APPROVE")') == \
        ["hoUpcoming", "hrow", "APPROVE"]


def t_every_selector_names_something_the_shipped_page_has():
    for kind, cls in sorted(REG.items()):
        if not issubclass(cls, party.Surface):
            continue
        assert cls.sources, f"{kind} names no source files"
        text = ""
        for rel in cls.sources:
            with open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8") as fh:
                text += fh.read()
        for key, selector in cls.selectors.items():
            for token in selector_tokens(selector):
                assert token in text, f"{kind}.{key}: {token!r} is not in {', '.join(cls.sources)}"
        for token in selector_tokens(cls.ready):
            assert token in text, f"{kind}.ready: {token!r} is not in {', '.join(cls.sources)}"
        for key in cls.masked:
            assert key in cls.selectors, f"{kind} masks an unknown selector {key!r}"


def t_desk_labels_the_actions_press_exist_in_the_desk():
    with open(os.path.join(ROOT, "src", "console", "driver-desk.js"), encoding="utf-8") as fh:
        desk = fh.read()
    for key, label in roles.selectors.DESK.items():
        assert f'"{label}"' in desk, f"the driver desk has no {label!r} ({key}) any more"
    with open(os.path.join(SIM, "roles", "party.py"), encoding="utf-8") as fh:
        code = fh.read()
    assert not re.search(r"""['"]#[A-Za-z]|data-label|aria-label""", code), \
        "selectors and desk labels live in roles/selectors.py only (#1267 will move the desks)"


# -- the page helpers ---------------------------------------------------------------
def t_without_a_browser_every_action_is_unavailable():
    role, _, _ = make(party.DirectorRole, browser=False)
    for action in ("open", "next"):
        try:
            role.do(action, {})
        except roles.Unavailable as exc:
            assert "browser" in str(exc)
        else:
            raise AssertionError(f"{action} must be unavailable without a browser")


def t_the_engine_skips_an_unavailable_step_and_what_depends_on_it():
    class Probe(roles.Role):
        kind = "probe"

        def act_go(self, args):
            raise roles.Unavailable("no browser here")

        def act_fine(self, args):
            return {"ok": True}

    reg = {"probe": Probe}
    scn = scenario.from_dict({"step": [
        {"id": "a", "role": "probe", "do": "go", "expect": ["result('a').x == 1"], "shot": "a-shot"},
        {"id": "b", "role": "probe", "do": "fine", "at": "after:a"},
        {"id": "c", "role": "probe", "do": "fine"}]}, reg)
    clock = Clock()

    def sleep(s):
        clock.t += max(s, 0.001)
    data = engine.Engine(scn, engine.RunContext(), {}, registry=reg, clock=clock, sleep=sleep).run()
    by = {s["id"]: s for s in data["steps"]}
    assert by["a"]["status"] == "skipped" and by["a"]["checks"] == [], "a skipped step runs no checks"
    assert by["b"]["status"] == "skipped" and by["c"]["status"] == "ok", by
    assert data["ok"], "a missing browser never fails the run"
    soft = [f["message"] for f in data["findings"] if f["severity"] == "soft"]
    assert any("no browser here" in m for m in soft), soft
    assert not any("shot" in m for m in soft), "a skipped step asks for no screenshot"


class Boom(roles.Role):
    kind = "boom"

    def act_go(self, args):
        raise RuntimeError(f"Page.goto: net::ERR at http://127.0.0.1:1/console/timing?l={self.ctx.event_link('timing')}"
                           f" and /cockpit?t={self.ctx.token('Comms 1')} code D1:abcDEF123.sig_456 key {args.get('k')}")

    def act_echo(self, args):
        return {"url": "http://127.0.0.1:1/console/drive?i=" + self.ctx.event_link("invite"),
                "nested": [{"token": self.ctx.token("Comms 1")}], "n": 3}

    def page(self):
        return {"link": self.ctx.event_link("timing")}

    def shot(self, path, element=""):
        raise RuntimeError("no shot for ?t=" + self.ctx.token("Comms 1"))

    def close(self):
        return [("soft", "closing with " + self.ctx.token("Comms 1"))]


def t_no_credential_reaches_what_the_engine_writes():
    reg = {"boom": Boom}
    ctx = engine.RunContext(token=lambda who: "TOKENCANARY", event_link=lambda kind: "LINKCANARY" + kind,
                            secrets=["SECRETCANARY"])
    scn_ = scenario.from_dict({"step": [
        {"id": "a", "role": "boom", "do": "go", "args": {"k": "SECRETCANARY"}},
        {"id": "b", "role": "boom", "do": "echo", "expect": ["page('boom').link == 'x'"], "timeout": "1s",
         "shot": "b-shot"}]}, reg)
    clock = Clock()

    def sleep(s):
        clock.t += max(s, 0.001)
    tmp = tempfile.mkdtemp(prefix="racecast-roles-test-")
    try:
        data = engine.Engine(scn_, ctx, {}, registry=reg, clock=clock, sleep=sleep, shots_dir=tmp).run()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    text = json.dumps(data)
    for canary in ("TOKENCANARY", "LINKCANARY", "SECRETCANARY", "abcDEF123"):
        assert canary not in text, f"{canary} reached the report: {text[:600]}"
    by = {s["id"]: s for s in data["steps"]}
    assert by["a"]["status"] == "failed" and "RuntimeError" in by["a"]["error"], "the error itself stays readable"
    assert by["b"]["result"]["n"] == 3 and "REDACTED" in by["b"]["result"]["url"], by["b"]["result"]
    assert engine.scrub("x ?t=abc.def y", ()) == "x ?t=REDACTED y"


def t_a_bare_link_id_in_a_result_is_redacted_even_if_no_role_asked_for_it_and_after_a_renewal():
    links = {"invite": "INVITEid-0001", "timing": "TIMINGid-0001"}

    class Api(roles.Role):
        kind = "apiish"

        def act_get(self, args):
            return {"id": links["timing"], "active": True, "invite": links["invite"]}

        def act_renew(self, args):
            links["timing"] = "TIMINGid-0002"
            return {"id": links["timing"]}

    reg = {"apiish": Api}
    ctx = engine.RunContext(event_link=links.get)
    scn_ = scenario.from_dict({"step": [{"id": "a", "role": "apiish", "do": "get"},
                                        {"id": "b", "role": "apiish", "do": "renew", "at": "after:a"},
                                        {"id": "c", "role": "apiish", "do": "get", "at": "after:b"}]}, reg)
    clock = Clock()

    def sleep(s):
        clock.t += max(s, 0.001)
    data = engine.Engine(scn_, ctx, {}, registry=reg, clock=clock, sleep=sleep).run()
    text = json.dumps(data)
    for canary in ("TIMINGid-0001", "TIMINGid-0002", "INVITEid-0001"):
        assert canary not in text, f"{canary} reached the report: {text[:400]}"
    assert data["steps"][0]["result"]["active"] is True
    none = engine.RunContext(event_link=lambda kind: None)
    assert none.scrub({"x": "plain"}) == {"x": "plain"}, "no link yet is no error"

    def broken(kind):
        raise OSError("unreadable")
    assert engine.RunContext(event_link=broken).scrub("still ?t=abc") == "still ?t=REDACTED"
    reads = []
    counted = engine.RunContext(event_link=lambda kind: reads.append(kind) or "LINKID-" + kind, clock=lambda: 5.0)
    for _ in range(50):
        counted.scrub("x")
    assert len(reads) == 2, f"the ids are read once per moment, not per string: {len(reads)}"


def t_a_failed_navigation_is_a_step_error_without_the_link():
    role, page, _ = make(party.ViewerRole, links={"timing": "LINKCANARY"})

    def refuse(url, timeout=None):
        raise RuntimeError(f'Page.goto: net::ERR_CONNECTION_REFUSED at {url}\nCall log:\n  - navigating to "{url}"')
    page.goto = refuse
    msg = fails(lambda: role.do("read", {}), "net::ERR_CONNECTION_REFUSED")
    assert "LINKCANARY" not in msg and "Call log" not in msg, msg
    role2, page2, _ = make(party.CommentatorRole, "commentator:Comms 1")
    page2.goto = refuse
    assert "tok-" not in fails(lambda: role2.do("open", {}), "open /cockpit")


def t_a_plain_page_without_a_browser_is_skipped_and_masks_the_links_in_a_shot():
    role, _, _ = make(builtin.PageRole, "page:x", browser=False)
    try:
        role.do("open", {"path": "/panel"})
    except roles.Unavailable as exc:
        assert "browser" in str(exc)
    else:
        raise AssertionError("a page without a browser is a skip, which --no-skips can fail")
    for cls in (builtin.PageRole, party.ProducerRole):
        role, _, _ = make(cls, "page:x" if cls is builtin.PageRole else None, browser=False)
        try:
            role.do("close", {})
        except roles.Unavailable as exc:
            assert "browser" in str(exc), exc
        else:
            raise AssertionError(f"{cls.__name__}: close without a browser is a skip too")
    role, page, _ = make(builtin.PageRole, "page:x")
    role.do("open", {"path": "/panel"})
    role.shot("p.png")
    assert page.shots == [("p.png", "", list(roles.selectors.LINKS))], page.shots


def t_no_skips_also_fails_a_missing_screenshot():
    class Probe(roles.Role):
        kind = "probe"

        def act_go(self, args):
            return {}

    reg = {"probe": Probe}
    raw = {"step": [{"id": "a", "role": "probe", "do": "go", "shot": "a-shot"}]}
    for strict in (False, True):
        clock = Clock()

        def sleep(s, clock=clock):
            clock.t += max(s, 0.001)
        tmp = tempfile.mkdtemp(prefix="racecast-roles-test-")
        try:
            data = engine.Engine(scenario.from_dict(raw, reg), engine.RunContext(), {}, registry=reg, clock=clock,
                                 sleep=sleep, shots_dir=tmp, no_skips=strict).run()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        shot = [f for f in data["findings"] if "shot a-shot" in f["message"]]
        assert len(shot) == 1 and shot[0]["severity"] == ("hard" if strict else "soft"), (strict, shot)
        assert data["ok"] is not strict


def t_no_skips_turns_a_skipped_step_into_a_hard_failure():
    class Probe(roles.Role):
        kind = "probe"

        def act_go(self, args):
            raise roles.Unavailable("no browser here")

    reg = {"probe": Probe}
    raw = {"step": [{"id": "a", "role": "probe", "do": "go"}, {"id": "b", "role": "probe", "do": "go", "at": "after:a"}]}
    for strict, ok in ((False, True), (True, False)):
        clock = Clock()

        def sleep(s, clock=clock):
            clock.t += max(s, 0.001)
        eng = engine.Engine(scenario.from_dict(raw, reg), engine.RunContext(), {}, registry=reg, clock=clock,
                            sleep=sleep, no_skips=strict)
        data = eng.run()
        hard = [f for f in data["findings"] if f["severity"] == "hard"]
        assert data["ok"] is ok and len(hard) == (2 if strict else 0), (strict, data["findings"])
        assert len([f for f in eng.data([]) ["findings"] if f["severity"] == "hard"]) == 0, \
            "the findings are not added twice"
    assert hard[0]["step"] == "a" and "allows no skips" in hard[0]["message"], hard


def t_a_role_opens_its_own_page_with_its_token():
    role, page, _ = make(party.RaceControlRole)
    out = role.do("open", {})
    assert page.gotos == [BASE + "/console/race-control?t=tok-Sim%20Race%20Control"], page.gotos
    assert "tok-" not in json.dumps(out) and "REDACTED" in out["url"], out
    role2, page2, _ = make(party.CommentatorRole, "commentator:Comms 2")
    role2.do("open", {})
    assert page2.gotos == [BASE + "/cockpit?t=tok-Comms%202"], page2.gotos
    fails(lambda: party.CommentatorRole(engine.RunContext(), "commentator").open(), "crew name")


def t_an_action_opens_the_page_first():
    role, page, _ = make(party.CommentatorRole, "commentator:Comms 1")
    page.add("#chatin")
    page.add("#chatlog", text="")
    page.add("#chatsend", on_click=lambda: setattr(page.els["#chatlog"], "text", "Comms 1: hi"))
    assert role.do("chat", {"text": "hi"}) == {"text": "hi"}
    assert len(page.gotos) == 1, "the first action opens the page"


def t_a_missing_hidden_or_disabled_control_fails_hard():
    role, page, _ = make(party.DirectorRole)
    role.do("open", {})
    page.add('.navbtn[data-area="handover"]')
    fails(lambda: role.do("approve_link", {"stint": 2}), "APPROVE Stint 2", "missing or hidden")
    key = '#hoUpcoming .hrow:has-text("Stint 2") button:has-text("APPROVE")'
    page.add(key, visible=False)
    fails(lambda: role.do("approve_link", {"stint": 2}), "missing or hidden")
    page.add(key, enabled=False)
    fails(lambda: role.do("approve_link", {"stint": 2}), "disabled")
    assert key not in page.clicks, "a control that fails the check is never clicked"


def t_a_control_outside_the_viewport_is_a_soft_finding_once():
    role, page, ctx = make(party.CommentatorRole, "commentator:Comms 1")
    page.add("#chatin", box={"x": 10, "y": 1200, "width": 80, "height": 20})
    page.add("#chatlog", text="")
    page.add("#chatsend", on_click=lambda: setattr(page.els["#chatlog"], "text", "x again"))
    role.do("chat", {"text": "x"})
    role.do("chat", {"text": "again"})
    notes = [m for sev, m in ctx.findings if "outside the 1440x900 viewport" in m]
    assert len(notes) == 1 and ctx.findings[0][0] == "soft", ctx.findings
    assert "#chatin" in page.scrolled, "the control is scrolled into view before it is used"


def t_an_action_waits_for_the_page_to_show_its_result():
    role, page, ctx = make(party.CommentatorRole, "commentator:Comms 1")
    page.add("#chatin")
    page.add("#chatlog", text="")
    page.add("#chatsend", on_click=lambda: page.after(3.0, lambda: setattr(page.els["#chatlog"], "text", "late")))
    start = page.clock.t
    role.do("chat", {"text": "late"})
    assert 3.0 <= page.clock.t - start < 3.5, "the action returns once the page shows the message"
    assert any("took 3." in m for _, m in ctx.findings), f"a slow page is a soft finding: {ctx.findings}"
    page.els["#chatsend"].on_click = None
    msg = fails(lambda: role.do("chat", {"text": "never"}), "not shown within")
    assert "crew chat" in msg


def t_the_pages_own_request_must_be_answered_without_an_error():
    role, page, _ = make(party.DirectorRole)
    page.add("#pgmBus button.redflag", attrs={"class": "k redflag"})
    fails(lambda: role.do("red_flag", {}), "sent no request to /obs/source")
    page.els["#pgmBus button.redflag"].on_click = lambda: page.respond("/obs/source", 502)
    fails(lambda: role.do("red_flag", {}), "HTTP 502")

    def ok():
        page.respond("/obs/source")
        page.els["#pgmBus button.redflag"].attrs["class"] = "k redflag on"
    page.els["#pgmBus button.redflag"].on_click = ok
    assert role.do("red_flag", {}) == {"on": True}
    assert role.page()["red_flag"] is True


def t_red_flag_with_on_waits_for_a_light_that_lags():
    role, page, _ = make(party.DirectorRole)
    key = page.add("#pgmBus button.redflag", attrs={"class": "k redflag on"})
    pressed = []

    def press():
        pressed.append(key.attrs["class"])
        page.respond("/obs/source")
        key.attrs["class"] = "k redflag on" if "on" not in key.attrs["class"].split() else "k redflag"
    key.on_click = press
    page.after(2.0, lambda: (key.attrs.update({"class": "k redflag"}), page.respond("/obs/state")))
    page.after(1.0, lambda: page.respond("/obs/state"))
    assert role.do("red_flag", {"on": True}) == {"on": True}
    assert pressed == ["k redflag"], "the press waits until the light reads off"
    page.after(0.5, lambda: page.respond("/obs/state"))
    fails(lambda: role.do("red_flag", {"on": True}), "RED FLAG reading off before the press")
    assert len(pressed) == 1, "a light that already reads on is not toggled off"
    fails(lambda: role.do("red_flag", {"on": False}), "the page's next poll", "/obs/state")
    assert len(pressed) == 1, "without a fresh poll the key would toggle from stale state: no press"


def t_a_graphic_key_is_told_apart_by_its_scene_and_toggled():
    role, page, _ = make(party.DirectorRole)
    page.add('.navbtn[data-area="graphics"]')
    buses = ":is(#gfxBus, #gfxPreRaceBus, #gfxGridTopBus, #gfxGridBus) "
    page.add(buses + 'button.k:has-text("HUD")', count=2)
    fails(lambda: role.do("graphic", {"name": "hud"}), "graphic HUD", "2 matching keys")
    key = page.add(buses + 'button.k:has(.tag:text-is("Stint")):has-text("HUD")', attrs={"class": "k"})

    def press():
        page.respond("/obs/source")
        key.attrs["class"] = "k on" if "on" not in key.attrs["class"].split() else "k"
    key.on_click = press
    assert role.do("graphic", {"name": "hud", "scene": "Stint"}) == {"on": True, "name": "HUD"}
    page.after(0.3, lambda: page.respond("/obs/state"))
    assert role.do("graphic", {"name": "hud", "scene": "Stint", "on": False}) == {"on": False, "name": "HUD"}
    assert page.clicks[0] == '.navbtn[data-area="graphics"]'
    pov = page.add('#scnVisBus button.k:has-text("POV")', attrs={"class": "k"})
    pov.on_click = lambda: (page.respond("/pov/toggle"), pov.attrs.update({"class": "k on"}))
    assert role.do("pov", {"on": True}) == {"on": True}, "the relay keeps the POV state: no poll to wait for"


def t_hud_sets_and_clears_a_field_through_its_select():
    role, page, _ = make(party.DirectorRole)
    page.add('.navbtn[data-area="hud"]')
    box = page.add('select[data-setup="racecontrol"]', options=["", "GREEN", "Safety Car (lap 2)"])
    routes = {"GREEN": "/setup/set/racecontrol/GREEN", "": "/setup/clear/racecontrol",
              "Safety Car (lap 2)": "/setup/set/racecontrol/Safety%20Car%20(lap%202)"}
    box.on_change = lambda: page.respond(routes[box.value])
    for value in ("GREEN", "Safety Car (lap 2)", ""):
        assert role.do("hud", {"field": "racecontrol", "value": value}) == {"field": "racecontrol", "value": value}
    fails(lambda: role.do("hud", {"field": "weather", "value": "x"}), "hud field is one of")
    fails(lambda: role.do("hud", {"field": "racecontrol", "value": "Unknown"}), "no option")
    box.on_change = lambda: page.respond("/setup/set/racecontrol/other")
    fails(lambda: role.do("hud", {"field": "racecontrol", "value": "GREEN"}), "sent no request to /setup/set/racecontrol/GREEN")


def t_mode_accepts_the_pages_confirm_dialog():
    role, page, _ = make(party.DirectorRole)
    page.add('.navbtn[data-area="schedule"]')
    page.add("#modeChip", text="RACE")
    switch = page.add("#modeSwitch", text="switch → QUALIFYING")

    def click():
        if page.confirm():
            page.respond("/mode/qualifying")
            page.els["#modeChip"].text, switch.text = "QUALIFYING", "switch → RACE"
    switch.on_click = click
    fails(lambda: role.do("mode", {"mode": "race"}), "the switch offers", "not race")
    fails(lambda: role.do("mode", {"mode": "practice"}), "'race' or 'qualifying'")
    assert role.do("mode", {"mode": "qualifying"}) == {"mode": "qualifying"}, \
        "a dialog nobody accepts is dismissed and the switch does nothing"


def t_a_dialog_answer_never_outlives_its_action():
    role, page, _ = make(party.DirectorRole)
    for area in ("hud", "schedule"):
        page.add(f'.navbtn[data-area="{area}"]')
    page.add("#timerInfo", text="Timer: READY · duration 6:00:00")
    fails(lambda: role.do("timer", {"duration": "0:38:00"}), "Timer SET")
    assert page.prompt() is None, "a SET that never clicked leaves no answer for the next dialog"
    page.add('#timerBus button.k:has-text("SET…")', on_click=lambda: None)
    fails(lambda: role.do("timer", {"duration": "0:38:00"}), "/timer/set/0:38:00")
    assert page.prompt() is None, "a SET that opened no prompt leaves no answer for the next dialog"
    page.add("#modeChip", text="RACE")
    page.add("#modeSwitch", text="switch → QUALIFYING", on_click=lambda: None)
    fails(lambda: role.do("mode", {"mode": "qualifying"}), "/mode/qualifying")
    assert page.confirm() is False, "nor a mode switch that opened no confirm"

def t_a_part_is_started_and_ended_with_the_typed_phrase():
    role, page, _ = make(party.DirectorRole)
    page.add('.navbtn[data-area="broadcast"]')
    button = page.add("#partActionBtn", text="Start Part 1")
    page.add("#partStatus", text="Part 1 of 2 — OFFLINE")
    page.add("#partModalPhrase", text="", visible=False)
    typed = page.add("#partModalInput")
    button.on_click = lambda: page.add("#partModalPhrase", text="START PART 1" if button.text.startswith("Start") else "END PART 1")

    def confirm():
        want = page.els["#partModalPhrase"].text
        if typed.value != want:
            return
        live = want.startswith("START")
        page.respond("/parts/start" if live else "/parts/end")
        page.els["#partStatus"].text = "● LIVE — Part 1 of 2" if live else "Part 1 ended — Next: Part 2 of 2"
        button.text = "End Part 1" if live else "Start Part 2"
    page.add("#partModalConfirm", on_click=confirm)
    fails(lambda: role.do("end_part", {}), "reads 'Start Part 1', not End Part")
    assert role.do("start_part", {}) == {"part": "Start Part 1", "status": "● LIVE — Part 1 of 2"}
    assert typed.value == "START PART 1", "the phrase the page shows is typed"
    assert role.do("end_part", {})["status"].startswith("Part 1 ended")


def t_a_screenshot_blacks_out_the_event_links():
    role, page, _ = make(party.RaceControlRole)
    fails_shot = None
    try:
        role.shot("x.png")
    except roles.ShotUnavailable as exc:
        fails_shot = str(exc)
    assert fails_shot, "no page, no screenshot"
    role.do("open", {})
    role.shot("desk.png")
    assert page.shots == [("desk.png", "", [".dd .mono.grow"])], page.shots
    cc, page, _ = producer()
    cc.do("open", {})
    cc.shot("cc.png")
    assert page.shots == [("cc.png", "", ["#evt-invite-text", "#evt-timing-text"])], \
        "the Control Center shows both links in simulation"


def t_a_screenshot_notes_its_window_for_the_film():
    role, page, ctx = make(party.RaceControlRole)
    shots = []

    class Rec:
        @contextlib.contextmanager
        def shooting(self, view):
            shots.append(view)
            yield

    role.do("open", {})
    ctx.recorder = Rec()
    role.shot("desk.png", ".area")
    role.shot("page.png")
    assert shots == ["race_control"], \
        f"an element shot, which may resize the page, tells the recorder; a viewport shot does not: {shots}"


def t_redact_hides_tokens_invites_and_timing_links():
    text = builtin.redact("http://127.0.0.1:1/console/timing?l=abc-DEF&x=1 /drive?i=zz9 /cockpit?t=tok.sig")
    assert "abc-DEF" not in text and "zz9" not in text and "tok.sig" not in text and "x=1" in text, text


def t_repeated_and_stopped_service_errors_fold_into_one_finding_each():
    role, _, _ = make(builtin.PageRole, "page:x")
    role.errors = [f"Failed to load resource: 503 (http://127.0.0.1:1/preview/feed/B?ts={i})" for i in range(40)]
    role.errors += ["Failed to load resource: net::ERR_CONNECTION_REFUSED (http://127.0.0.1:1/status)"] * 9
    role.errors += ["TypeError: x is undefined"]
    out = role.close()
    assert ("soft", "console error: Failed to load resource: 503 (http://127.0.0.1:1/preview/feed/B) (40x)") in out
    assert ("soft", "9 requests of the page found its service stopped") in out, out
    assert ("hard", "console error: TypeError: x is undefined") in out and len(out) == 3, out


# -- producer -------------------------------------------------------------------------
def stream_ended(page, job_id):
    """The page's event stream of job `job_id` has ended (Playwright's requestfinished)."""
    page.handlers["requestfinished"](Response(f"{BASE}/api/jobs/{job_id}/stream?profile=demo"))


def producer(job="done", log="relay: up", stale=None):
    """A Control Center whose job hides the chip when it starts and shows `job` a second later."""
    role, page, ctx = make(party.ProducerRole, event={"track": "6e378b"})
    page.add("#jobchip", text=stale or "", visible=stale is not None)
    page.ops = []

    def confirm():
        last = page.clicks[-2]
        op = "event-start" if "Start event" in last else "relay-restart" if "relay-restart" in last else "event-stop"
        page.ops.append(op)
        job_id = f"j{len(page.ops)}"
        page.respond("/api/op/" + op, body={"ok": True, "job_id": job_id})
        page.after(0.2, lambda: page.add("#jobchip", visible=False))

        def ended():
            page.add("#jobchip", text=job)
            stream_ended(page, job_id)
        page.after(1.2, ended)
    page.add("#cmodal-ok", on_click=confirm)
    page.add("#joblog", text=log)
    for view in ("home", "services"):
        page.add(f'.navitem[data-nav="{view}"]')
    page.add('button:has-text("Start event")')
    page.add('button:has-text("Stop event")')
    return role, page, ctx


def t_start_event_confirms_and_waits_for_the_job():
    role, page, ctx = producer()
    assert role.do("start_event", {}) == {"job": "done"}
    assert page.clicks == ['.navitem[data-nav="home"]', 'button:has-text("Start event")', "#cmodal-ok"], page.clicks
    assert not ctx.findings, "a job is slow by nature: no soft finding for its duration"
    assert role.do("stop_event", {}) == {"job": "done"}
    assert page.clicks[-2:] == ['button:has-text("Stop event")', "#cmodal-ok"]
    assert page.ops == ["event-start", "event-stop"]
    page.add("""[data-view="services"] button[onclick="op('relay-restart', true)"]""")
    assert role.do("relay_restart", {}) == {"job": "done"} and page.ops[-1] == "relay-restart"
    assert page.clicks[-3] == '.navitem[data-nav="services"]', "Restart lives in the Relay view"


def t_an_earlier_jobs_chip_is_not_this_jobs_result():
    role, _, _ = producer(job="failed (exit 1)", stale="done")
    fails(lambda: role.do("stop_event", {}), "failed (exit 1)")
    role, page, _ = producer(stale="done")
    page.els["#cmodal-ok"].on_click = lambda: page.respond("/api/op/event-start", body={"ok": True, "job_id": "j9"})
    fails(lambda: role.do("start_event", {}), "the job's end")
    role, page, _ = producer(stale="done")
    page.els["#cmodal-ok"].on_click = lambda: page.respond("/api/op/event-start", body={"ok": False})
    fails(lambda: role.do("start_event", {}), "started no job")
    role, page, _ = producer()
    page.els["#cmodal-ok"].on_click = lambda: page.after(0.2, lambda: page.add("#jobchip", text="done"))
    fails(lambda: role.do("start_event", {}), "sent no request to /api/op/event-start")


def t_a_job_that_ends_before_the_first_look_is_still_its_result():
    role, page, _ = producer(stale="done")

    def quick():
        page.respond("/api/op/event-stop", body={"ok": True, "job_id": "j5"})
        page.add("#jobchip", text="done")       # hidden and shown again before the role looks
        stream_ended(page, "j5")
    page.els["#cmodal-ok"].on_click = quick
    assert role.do("stop_event", {}) == {"job": "done"}, "the chip is hidden only while the job runs"

    class Closed(Response):
        method, failure = "GET", "net::ERR_ABORTED"

    def closed_by_the_page():
        page.respond("/api/op/event-stop", body={"ok": True, "job_id": "j6"})
        page.add("#jobchip", text="done")
        page.handlers["requestfailed"](Closed(f"{BASE}/api/jobs/j6/stream"))
    page.els["#cmodal-ok"].on_click = closed_by_the_page
    assert role.do("stop_event", {}) == {"job": "done"}, \
        "the page's done handler may close the stream before the server does: that ends it too"


def t_a_failed_job_fails_the_step_with_its_log():
    role, _, _ = producer(job="failed (exit 1)", log="Pre-flight gate: cannot start /cockpit?t=tok.sig")
    msg = fails(lambda: role.do("start_event", {}), "failed (exit 1)", "Pre-flight gate")
    assert "tok.sig" not in msg, "a job log is redacted"
    role, _, _ = producer(job="failed (exit 1)", log="see /cockpit?t=" + "A" * 700)
    assert "AAAA" not in fails(lambda: role.do("start_event", {}), "failed (exit 1)"), \
        "a token cut by the log tail is still a token: redact first, then cut"


def t_start_event_takes_stint_and_qualifying():
    role, page, _ = producer()
    page.add("#stint")
    page.add("#event-qualifying")
    role.do("start_event", {"stint": 2, "qualifying": True})
    assert page.els["#stint"].value == "2" and page.els["#event-qualifying"].checked


def t_pick_track_selects_the_layout_and_reads_an_error():
    role, page, _ = producer()
    track = page.els["#evt-track"]
    track.options = ["6e378b"]
    track.on_change = lambda: page.respond("/api/event-track")
    page.add('#evt-track option[value="6e378b"]')
    assert role.do("pick_track", {}) == {"track": "6e378b"}, "the default is [event] track"
    page.add("#evt-track-err", text="unknown layout")
    fails(lambda: role.do("pick_track", {"track": "6e378b"}), "unknown layout")
    fails(lambda: role.do("pick_track", {"track": "ffffff"}), "ffffff", "not shown")


def t_race_settings_change_only_what_is_asked():
    role, page, _ = producer()
    sent = []
    page.add("#evt-race-formation", checked=True, on_change=lambda: (sent.append("formation"),
                                                                    page.respond("/api/event-race")))
    page.add("#evt-race-mode", options=["laps", "time"], on_change=lambda: (sent.append("mode"),
                                                                        page.respond("/api/event-race")))
    page.add("#evt-race-laps", on_change=lambda: (sent.append("laps"), page.respond("/api/event-race")))
    assert role.do("race_settings", {"formation": True}) == {"formation": True} and sent == [], \
        "a box that already reads right is left alone"
    assert role.do("race_settings", {"formation": False, "laps": 12}) == {"formation": False, "laps": 12}
    assert sent == ["formation", "laps"] and page.els["#evt-race-laps"].value == "12", sent
    fails(lambda: role.do("race_settings", {"finish_mode": "distance"}), "no option")


# -- director -------------------------------------------------------------------------
def t_approve_link_waits_for_link_ready():
    role, page, _ = make(party.DirectorRole)
    page.add('.navbtn[data-area="handover"]')
    row = page.add('#hoUpcoming .hrow:has-text("Stint 2")', text="Stint 2 Comms 2 sim://comms2 APPROVE")

    def approve():
        page.respond("/submissions/approve")
        page.after(0.5, lambda: setattr(row, "text", "Stint 2 Comms 2 link ready"))
    page.add('#hoUpcoming .hrow:has-text("Stint 2") button:has-text("APPROVE")', on_click=approve)
    assert role.do("approve_link", {"stint": 2}) == {"stint": "Stint 2"}
    assert "link ready" in row.text, "the action returns once the row reads link ready"
    assert page.clicks[0] == '.navbtn[data-area="handover"]', "the director opens the Handover area first"


def t_approve_link_in_the_schedule_area_takes_a_link_the_handover_does_not_list():
    role, page, _ = make(party.DirectorRole)
    page.add('.navbtn[data-area="schedule"]')
    row = page.add('#subsBody tr:has(td.rn:text-is("Qualifying"))', count=0)
    page.after(0.5, lambda: setattr(row, "count", 1))

    def approve():
        page.respond("/submissions/approve")
        page.after(0.5, lambda: setattr(row, "count", 0))
    page.add('#subsBody tr:has(td.rn:text-is("Qualifying")) button.approve', on_click=approve)
    assert role.do("approve_link", {"stint": "Qualifying", "area": "schedule"}) == {"stint": "Qualifying"}
    assert row.count == 0, "the action returns once the submission left the pending list"
    assert page.clicks[0] == '.navbtn[data-area="schedule"]', "the director opens the Schedule area first"
    fails(lambda: role.do("approve_link", {"stint": 2, "area": "setup"}), "area is handover or schedule")


def t_next_needs_a_serving_feed_and_shows_the_new_stint():
    role, page, _ = make(party.DirectorRole)
    page.add("#nextSub", text="arm stint 2 first")
    page.add("#liveOnAir", text="ON AIR\nStint 1 · Feed A")
    msg = fails(lambda: role.do("next", {}), "NEXT is not ready", "arm stint 2 first")
    assert "#nextBtn" not in page.clicks, f"NEXT is never pressed while it is not ready: {msg}"
    page.add("#nextBtn.ready")

    def go():
        page.respond("/next")
        page.els["#liveOnAir"].text = "ON AIR\nStint 2 · Feed B"
    page.els["#nextBtn"].on_click = go
    assert role.do("next", {}) == {"on_air": "ON AIR Stint 2 · Feed B"}


def t_every_control_action_is_timed_with_what_it_pressed():
    role, page, _ = make(party.DirectorRole)
    page.add("#nextSub", text="ready")
    page.add("#liveOnAir", text="ON AIR\nStint 1 · Feed A")
    page.add("#nextBtn.ready")

    def go():
        page.respond("/next")
        page.after(0.5, lambda: setattr(page.els["#liveOnAir"], "text", "ON AIR\nStint 2 · Feed B"))
    page.add("#nextBtn", on_click=go)
    role.do("next", {})
    acts = [(a["kind"], a["what"], a["depth"]) for a in role.take_actions()]
    assert acts == [("open", "/panel", 0), ("wait", "NEXT ready", 0), ("request", "NEXT", 0), ("click", "NEXT", 1), ("control", "NEXT", 2),
                    ("wait", "the next stint on air", 0)], acts
    assert role.take_actions() == [], "each action is handed over once"
    page.els["#nextBtn"].visible = False
    fails(lambda: role.do("next", {}), "missing or hidden")
    failed = [a for a in role.take_actions() if a["kind"] == "control"]
    assert failed and not failed[0]["ok"], f"a control that never showed is recorded as failed: {failed}"


def t_scene_names_its_key():
    role, page, _ = make(party.DirectorRole)
    fails(lambda: role.do("scene", {"scene": "Nowhere"}), "0 matching scene keys")
    key = '#pgmBus button.k:has-text("STINT")'

    def cut():
        page.respond("/obs/scene")
        page.els[key].attrs["class"] = "k air"
    page.add(key, attrs={"class": "k"}, on_click=cut)
    assert role.do("scene", {"scene": "Stint"}) == {"scene": "Stint"}


def t_show_dashboard_opens_the_desk_and_reads_the_button():
    role, page, _ = make(party.DirectorRole)
    page.add('.navbtn[data-area="race"]')
    button = '#driverBox button[data-label="Show dashboard"]'
    tab = '#deskTabs button[data-sub="dashboard"]'
    page.add(button, text="Show dashboard", visible=False)
    page.add(tab, on_click=lambda: setattr(page.els[button], "visible", True))

    def toggle():
        page.respond("/drivers/race/settings")
        page.els[button].text = "Hide dashboard"
    page.els[button].on_click = toggle
    assert role.do("show_dashboard", {}) == {"on": True}
    assert page.clicks[:2] == ['.navbtn[data-area="race"]', tab], "the Race area and its Dashboard tab first"
    fails(lambda: role.do("show_dashboard", {}), "does not read 'Show dashboard'")


def t_cue_and_chat_send_what_was_typed():
    role, page, _ = make(party.DirectorRole)
    page.add('.navbtn[data-area="cues"]')
    page.add("#cueTarget", options=["All", "On air"])
    page.add("#cueLevel", options=["Info", "Critical"])
    page.add("#cueText")
    page.add("#cueSend", on_click=lambda: page.respond("/cues/send"))
    assert role.do("cue", {"text": "wrap", "target": "All", "level": "Critical"}) == {"text": "wrap"}
    assert (page.els["#cueTarget"].value, page.els["#cueLevel"].value, page.els["#cueText"].value) == \
        ("All", "Critical", "wrap")
    fails(lambda: role.do("cue", {"text": "x", "target": "Nobody"}), "no option")
    page.add("#chatName")
    page.add("#chatInput")
    page.add("#chatLog", text="")
    page.add("#chatSendBtn", on_click=lambda: setattr(page.els["#chatLog"], "text", "Director: go"))
    assert role.do("chat", {"text": "go"}) == {"text": "go"} and page.els["#chatName"].value == "Director"


def t_hold_keeps_the_page_in_view_for_its_seconds():
    role, page, _ = make(party.DirectorRole)
    start = page.clock.t
    assert role.do("hold", {"seconds": 15}) == {"seconds": 15}
    assert page.clock.t - start >= 15, "the action itself lasts the hold, so the film plays it in real time"
    for bad in (0, -1, "15", True, 301):
        fails(lambda bad=bad: role.do("hold", {"seconds": bad}), "hold: seconds")


def t_the_director_sets_the_race_duration_through_the_set_prompt():
    role, page, _ = make(party.DirectorRole)
    page.add('.navbtn[data-area="hud"]')
    info = page.add("#timerInfo", text="Timer: READY · duration 6:00:00 · sheet ok")
    answers = []

    def click():
        text = page.prompt()
        answers.append(text)
        if text:
            page.respond("/timer/set/" + text)
            info.text = f"Timer: READY · duration {text} · sheet ok"
    page.add('#timerBus button.k:has-text("SET…")', on_click=click)
    assert role.do("timer", {"duration": "0:30:00"}) == {"duration": "0:30:00"}
    assert answers == ["0:30:00"], "the prompt is answered with the duration, not its 6:00:00 default"
    assert '.navbtn[data-area="hud"]' in page.clicks, "the timer keys are in the HUD area"
    for bad in ("30:00", "0:3:00", "", "0:30:00/x"):
        fails(lambda bad=bad: role.do("timer", {"duration": bad}), "timer: duration")
    info.text = "Timer: RUNNING · 0:29:00 left · sheet ok"
    page.add('#timerBus button.k:has-text("SET…")', on_click=lambda: page.respond("/timer/set/" + page.prompt()))
    fails(lambda: role.do("timer", {"duration": "0:45:00"}), "duration 0:45:00")

def t_the_director_presses_the_timer_keys_start_and_reset():
    role, page, _ = make(party.DirectorRole)
    page.add('.navbtn[data-area="hud"]')
    for key, path in (("START", "/timer/start"), ("RESET", "/timer/reset")):
        page.add(f'#timerBus button.k:has-text("{key}")', on_click=lambda path=path: page.respond(path))
        assert role.do("timer_key", {"key": key}) == {"key": key}
        assert f'#timerBus button.k:has-text("{key}")' in page.clicks, f"{key} is the panel's own key"
    for bad in ("PAUSE", "", None, "start"):
        fails(lambda bad=bad: role.do("timer_key", {"key": bad}), "timer_key: key")
    page.add('#timerBus button.k:has-text("START")', on_click=lambda: None)
    fails(lambda: role.do("timer_key", {"key": "START"}), "/timer/start")


# -- race control ---------------------------------------------------------------------
RC_AREAS = ("race", "drivers", "corrections", "qualifying", "dashboard", "note")


def desk_areas(page):
    for area in RC_AREAS:
        page.add(f'.navbtn[data-area="{area}"]')


def desk():
    role, page, ctx = make(party.RaceControlRole)
    page.add('.dd .msg.cur[role="status"]', text="", attrs={"class": "msg"})
    desk_areas(page)
    return role, page, ctx


def desk_button(page, label, route, says, cls="msg", armed=True):
    """A desk button; an armed one acts on its second click only."""
    key = f'.dd button[data-label="{label}"]'
    el = page.add(key, text=label)

    def click():
        if armed and el.text == label:
            el.text = "Confirm " + label
            return
        el.text = label
        page.respond("/console/race-control" + route)
        status = page.els['.dd .msg.cur[role="status"]']
        status.text, status.attrs["class"] = says, cls
    el.on_click = click
    return key


def t_a_control_the_page_renders_anew_is_found_again():
    role, page, _ = desk()
    key = desk_button(page, "Final lap", "/drivers/race/final-lap", "Final lap", armed=False)
    page.els[key].rerenders = 2
    assert role.do("final_lap", {}) == {"status": "Final lap"}, "the desk re-renders a row between find and click"
    page.els[key].rerenders = 10 ** 6
    fails(lambda: role.do("final_lap", {}), "not attached")


def t_final_lap_is_one_click_and_an_armed_button_two():
    role, page, _ = desk()
    key = desk_button(page, "Final lap", "/drivers/race/final-lap", "Final lap", armed=False)
    assert role.do("final_lap", {}) == {"status": "Final lap"}
    assert page.clicks == [key], f"Final lap is one click: {page.clicks}"
    key = desk_button(page, "Formation Lap", "/drivers/race/formation-lap", "Formation Lap on the HUD", armed=False)
    assert role.do("formation_lap", {}) == {"status": "Formation Lap on the HUD"}
    assert page.clicks[-1] == key, f"Formation Lap is one click: {page.clicks}"
    desk_button(page, "Finish", "/drivers/race/finish", "Race finished")
    desk_button(page, "Restart released", "/drivers/race/release", "Restart released")
    assert role.do("finish", {}) == {"status": "Race finished"}
    assert role.do("release", {}) == {"status": "Restart released"}


def t_an_error_on_the_desks_status_line_fails_the_step():
    role, page, _ = desk()
    desk_button(page, "RED FLAG", "/red-flag", "RED FLAG held the race; banner: not in the vocabulary", "msg err")
    fails(lambda: role.do("red_flag", {}), "banner: not in the vocabulary")
    desk_button(page, "RED FLAG", "/red-flag", "RED FLAG: banner, cover and race held")
    assert role.do("red_flag", {})["status"].startswith("RED FLAG")
    assert role.page()["status_error"] is False
    desk_button(page, "Final lap", "/drivers/race/final-lap", "Something else", armed=False)
    fails(lambda: role.do("final_lap", {}), "Something else", "not 'Final lap'")


def t_the_desks_line_of_the_action_before_is_not_this_actions_result():
    role, page, _ = desk()
    status = page.els['.dd .msg.cur[role="status"]']
    status.text, status.attrs["class"] = "RED FLAG held the race; banner: missing", "msg err"
    key = '.dd button[data-label="Final lap"]'
    el = page.add(key, text="Final lap")

    def click():
        page.respond("/console/race-control/drivers/race/final-lap")
        page.after(0.3, lambda: (setattr(status, "text", "Final lap"), status.attrs.update({"class": "msg"})))
    el.on_click = click
    assert role.do("final_lap", {}) == {"status": "Final lap"}, "the old error line is not read as the result"
    start = page.clock.t
    assert role.do("final_lap", {}) == {"status": "Final lap"}, "the same line twice is accepted once it stays"
    assert page.clock.t - start >= party.STALE_S


def _desk_change(page, key, route, says, **kw):
    """A desk select or input that sends `route` on its change and shows `says`."""
    def change():
        page.respond("/console/race-control" + route)
        status = page.els['.dd .msg.cur[role="status"]']
        status.text, status.attrs["class"] = says, "msg"
    return page.add(key, on_change=change, **kw)


def t_race_control_confirms_a_dc_and_sets_a_class_lap_count():
    role, page, _ = desk()
    key = '.dd tr:has(div:text-is("B #2")) button[data-label="Confirm DC"]:not([hidden])'
    desk_button(page, "Confirm DC", "/drivers/race/recording/event", "DC confirmed", armed=False)
    page.els[key] = page.els.pop('.dd button[data-label="Confirm DC"]')
    assert role.do("confirm_dc", {"entry": "B #2"}) == {"status": "DC confirmed"}
    assert page.clicks[-2:] == ['.navbtn[data-area="race"]', key], "the events list is in the Race area"
    fails(lambda: role.do("confirm_dc", {"entry": "A #1"}), "no open event of 'A #1'")
    laps = _desk_change(page, '.dd input[aria-label="Lap count of GT4 in Stint 2"]', "/drivers/race/stint-laps",
                        "GT4 lap count of Stint 2 saved")
    args = {"class": "GT4", "stint": "Stint 2", "laps": 5}
    assert role.do("class_laps", args) == {"status": "GT4 lap count of Stint 2 saved"}
    assert laps.value == "5"
    assert role.do("class_laps", args) == {"kept": True}, \
        "a count the field already holds sends no change, so the step keeps it without a request"
    fails(lambda: role.do("class_laps", dict(args, stint="Stint 1")), "Lap count of GT4 in Stint 1")
    fails(lambda: role.do("class_laps", {"class": "GT4", "laps": 5}), "needs args.class and args.stint")


def t_race_control_sets_each_stints_lap_count_on_the_desk():
    role, page, _ = desk()
    one = _desk_change(page, '.dd input[aria-label="Lap count of Stint 1"]', "/drivers/race/stint-laps",
                       "Stint 1 lap count saved")
    two = _desk_change(page, '.dd input[aria-label="Lap count of Stint 2"]', "/drivers/race/stint-laps",
                       "Stint 2 lap count saved")
    out = role.do("stint_laps", {"laps": {"Stint 1": 7, "Stint 2": 8}})
    assert out == {"set": {"Stint 1": 7, "Stint 2": 8}, "kept": []}, out
    assert (one.value, two.value) == ("7", "8"), "each stint's field holds its count"
    out = role.do("stint_laps", {"laps": {"Stint 1": 7, "Stint 2": 9}})
    assert out == {"set": {"Stint 2": 9}, "kept": ["Stint 1"]}, \
        f"a count the field already holds is kept without a request: {out}"
    assert page.clicks[0] == '.navbtn[data-area="race"]', "the stint laps are in the Race area"
    fails(lambda: role.do("stint_laps", {"laps": {"Stint 3": 5}}), "Lap count of Stint 3")
    fails(lambda: role.do("stint_laps", {"laps": {"Stint 1": 0}}), "a whole number from 1")
    fails(lambda: role.do("stint_laps", {}), "stint_laps needs args.laps")


def t_race_control_takes_the_restart_plan_after_waiting_for_it():
    role, page, _ = desk()
    line = page.add(".dd .plan", text="Red flag: no lap time of the leader yet")
    buffer = page.add('.dd input[aria-label="Buffer until the restart, minutes"]')
    take = page.els[desk_button(page, "Take", "/drivers/race/stint-laps", "Restart lobby set to 4 laps", armed=False)]
    take.enabled = False
    calls = []

    def plan_arrives():
        calls.append(1)
        if len(calls) == 3:
            line.text, take.enabled = "Behind plan 3:20.0 \u00b7 1.9 laps \u00b7 Restart laps 4", True
    poll = page.wait_for_timeout

    def wait(ms):
        plan_arrives()
        poll(ms)
    page.wait_for_timeout = wait
    out = role.do("take_plan", {"buffer": 2})
    assert out == {"laps": 4, "status": "Restart lobby set to 4 laps"}, out
    assert buffer.value == "2", "the buffer goes in before the plan is taken"
    assert page.clicks[-1] == '.dd button[data-label="Take"]', page.clicks


def t_the_desk_and_the_panel_open_an_area_to_show_it():
    role, page, _ = desk()
    assert role.do("view", {"area": "corrections"}) == {"area": "corrections"}
    assert page.clicks[-1] == '.navbtn[data-area="corrections"]'
    fails(lambda: role.do("view", {"area": "nowhere"}), "area nowhere")
    role, page, _ = make(party.DirectorRole)
    page.add('.navbtn[data-area="race"]')
    assert role.do("view", {"area": "race"}) == {"area": "race"}
    assert page.clicks[-1] == '.navbtn[data-area="race"]'


def t_desk_forms_pick_the_entry_before_the_button():
    role, page, _ = desk()
    page.add('.dd select[aria-label="Entry to retire"]', options=["A #1", "B #2"])
    desk_button(page, "Retire (DNF)", "/drivers/race/retire", "Retired: B #2")
    assert role.do("retire", {"entry": "B #2"}) == {"status": "Retired: B #2"}
    assert page.els['.dd select[aria-label="Entry to retire"]'].value == "B #2"
    fails(lambda: role.do("retire", {"entry": "Z #9"}), "Entry to retire", "no option")
    page.add('.dd select[aria-label="Team to rejoin"]', options=["A #1"])
    desk_button(page, "Mark rejoin", "/drivers/race/rejoin", "Rejoin marked", armed=False)
    assert role.do("rejoin", {"entry": "A #1"}) == {"status": "Rejoin marked"}
    for label, opts in (("Quiet entry to estimate", ["A #1"]), ("Reference entry", ["B #2"]),
                        ("Behind or ahead of the reference", ["behind", "ahead of"])):
        page.add(f'.dd select[aria-label="{label}"]', options=opts)
    page.add('.dd input[aria-label="Seconds from the reference"]')
    desk_button(page, "Set estimate", "/drivers/race/estimate", "Estimate set", armed=False)
    role.do("estimate", {"entry": "A #1", "ref": "B #2", "seconds": 6, "ahead": True})
    assert page.els['.dd select[aria-label="Behind or ahead of the reference"]'].value == "ahead of"
    assert page.els['.dd input[aria-label="Seconds from the reference"]'].value == "6"
    page.add('.dd select[aria-label="Qualifying entry"]', options=["A #1"])
    page.add('.dd input[aria-label="Qualifying time"]')
    desk_button(page, "Add lap", "/drivers/race/quali-lap", "Qualifying lap added", armed=False)
    assert role.do("add_lap", {"entry": "A #1", "time": "1:45.312"}) == {"status": "Qualifying lap added"}


def t_corrections_times_and_notes_go_through_their_forms():
    role, page, _ = desk()
    page.add('.dd select[aria-label="Entry to correct"]', options=["A #1"])
    for label in ("Distance correction in metres", "Pit stop correction", "Reason"):
        page.add(f'.dd input[aria-label="{label}"]')
    desk_button(page, "Apply correction", "/drivers/race/correct", "Correction applied", armed=False)
    assert role.do("correction", {"entry": "A #1", "pits": 1, "reason": "missed stop"}) == {"status": "Correction applied"}
    assert page.els['.dd input[aria-label="Pit stop correction"]'].value == "1"
    assert page.els['.dd input[aria-label="Distance correction in metres"]'].value == "", \
        "a value nobody asked for is left alone"
    assert page.els['.dd input[aria-label="Reason"]'].value == "missed stop"
    page.add('.dd select[aria-label="Qualifying entry"]', options=["A #1"])
    page.add('.dd input[aria-label="Qualifying time"]')
    desk_button(page, "Set time", "/drivers/race/quali", "Qualifying time set", armed=False)
    assert role.do("set_time", {"entry": "A #1", "time": "1:45.312"}) == {"status": "Qualifying time set"}
    page.add("#rcTarget", options=["On air", "All commentators"])
    page.add("#rcText")
    message = page.add("#rcMsg", text="", attrs={"class": "rcmsg"})
    send = page.add("#rcSend")
    send.on_click = lambda: (page.respond("/console/race-control/cues"),
                             setattr(message, "text", "Sent."), message.attrs.update({"class": "rcmsg ok"}))
    assert role.do("note", {"text": "car 5", "target": "All commentators"}) == {"text": "car 5"}
    assert (page.els["#rcTarget"].value, page.els["#rcText"].value) == ("All commentators", "car 5")
    send.on_click = lambda: (page.respond("/console/race-control/cues"),
                             setattr(message, "text", "no commentator on air"), message.attrs.update({"class": "rcmsg err"}))
    fails(lambda: role.do("note", {"text": "x"}), "no commentator on air")


def waiting_desk(names, multi=False):
    """A desk with waiting requests: Approve takes the row's entry and stint selects,
    Reject is armed and acts on its second click, and an approved row lists what it got."""
    role, page, ctx = desk()
    left = list(names)
    page.entries, page.stints, page.rejected, page.approved = {}, {}, [], {}
    page.keep_registered = None    # (entry, stint) the page approves whatever the selects say
    status = page.els['.dd .msg.cur[role="status"]']

    def render():
        page.add(".dd .req.waiting", count=len(left))
        page.add(".dd .req.waiting strong", text=left[0] if left else "", count=len(left))
        for name in set(names):
            n = left.count(name)
            row = f'.dd .req.waiting:has(strong[title="{name}"])'
            page.add(row, count=n)
            entry = page.els.get(f'.dd .req.waiting select[aria-label="Entry of {name}"]')
            stint = page.els.get(f'.dd .req.waiting select[aria-label="Stint of {name}"]')

            def approve(name=name):
                page.respond("/console/race-control/drivers/requests/approve")
                got = page.els[f'.dd .req.waiting select[aria-label="Entry of {name}"]'].value
                seg = page.els[f'.dd .req.waiting select[aria-label="Stint of {name}"]'].value if multi else ""
                if page.keep_registered:
                    got, seg = page.keep_registered
                page.entries[name], page.stints[name] = got, seg
                page.approved.setdefault(name, []).append(" \u00b7 ".join(filter(None, [got, seg])))
                status.text, status.attrs["class"] = "Driver approved", "msg"
                left.remove(name)
                render()

            def reject(name=name):
                button = page.els[f'.dd .req.waiting:has(strong[title="{name}"]) button[data-label="Reject"]']
                if button.text == "Reject":
                    button.text = "Confirm Reject"
                    return
                page.respond("/console/race-control/drivers/requests/reject")
                status.text, status.attrs["class"] = "Driver rejected: the code is revoked", "msg"
                page.rejected.append(name)
                left.remove(name)
                render()
            page.add(row + ' button[data-label="Approve"]', count=n, on_click=approve)
            page.add(row + ' button[data-label="Reject"]', count=n, text="Reject", on_click=reject)
            page.add(f'.dd .req.waiting select[aria-label="Entry of {name}"]', count=n,
                     options=["A #1", "A #12", "B #2"], value=entry.value if entry and n else "")
            if multi:
                page.add(f'.dd .req.waiting select[aria-label="Stint of {name}"]', count=n,
                         options=["Stint 1", "Stint 2"], value=stint.value if stint and n else "")
            done = page.approved.get(name, [])
            page.add(f'.dd .reqs-ok .req:has(strong[title="{name}"]) span.grow', count=len(done), texts=done)
    render()
    return role, page, left


def t_race_control_approves_one_driver_or_every_waiting_one():
    role, page, left = waiting_desk(["Ann", "Bo", "Ann"])
    role.do("open", {})
    assert role.page()["waiting"] == 3
    assert role.do("approve_driver", {"name": "Bo", "entry": "B #2"}) == {"name": "Bo"}
    assert left == ["Ann", "Ann"] and page.entries["Bo"] == "B #2", "the corrected entry is approved"
    fails(lambda: role.do("approve_driver", {"name": "Bo"}), "no waiting request of 'Bo'")
    assert role.do("approve_drivers", {}) == {"approved": 2, "names": ["Ann", "Ann"]}, \
        "a second request of the same name is approved as its own click"
    assert role.page()["waiting"] == 0


def t_race_control_rejects_one_waiting_request_with_a_confirmed_click():
    role, page, left = waiting_desk(["Ann", "Bo", "Ann"])
    key = '.dd .req.waiting:has(strong[title="Bo"]) button[data-label="Reject"]'
    assert role.do("reject_driver", {"name": "Bo"}) == {"name": "Bo", "status": "Driver rejected: the code is revoked"}
    assert page.clicks.count(key) == 2, "Reject is armed: arm, then confirm"
    assert left == ["Ann", "Ann"] and page.rejected == ["Bo"] and "Bo" not in page.entries, \
        "Bo is rejected, not approved"
    fails(lambda: role.do("reject_driver", {"name": "Bo"}), "no waiting request of 'Bo'")
    assert role.do("reject_driver", {"name": "Ann"})["name"] == "Ann", "one of two requests of a name is rejected"
    assert left == ["Ann"], "a second request of the same name stays waiting"


def t_race_control_corrects_a_waiting_request_before_it_approves_it():
    role, page, left = waiting_desk(["Ann", "Bo", "Cy"], multi=True)
    out = role.do("correct_driver", {"name": "Ann", "entry": "B #2", "stint": 2})
    assert out == {"name": "Ann", "entry": "B #2", "stint": "Stint 2", "status": "Driver approved"}, \
        f"the correction as the desk approved it: {out}"
    assert (page.entries["Ann"], page.stints["Ann"]) == ("B #2", "Stint 2"), "the corrected entry and stint are approved"
    assert left == ["Bo", "Cy"], "only the corrected request left the waiting list"
    fails(lambda: role.do("correct_driver", {"name": "Bo"}), "args.entry or args.stint")
    fails(lambda: role.do("correct_driver", {"name": "Bo", "entry": "Z #9"}), "Entry of Bo", "no option")
    page.keep_registered = ("A #12", "Stint 1")
    fails(lambda: role.do("correct_driver", {"name": "Cy", "entry": "A #1", "stint": 1}), "Cy approved as", "A #1")
    assert left == ["Bo"], "the page approved Cy, though not as corrected"
    role, page, left = waiting_desk(["Ann", "Ann"], multi=True)
    role.do("correct_driver", {"name": "Ann", "entry": "B #2", "stint": 2})
    page.keep_registered = ("A #1", "Stint 1")
    fails(lambda: role.do("correct_driver", {"name": "Ann", "entry": "B #2", "stint": 2}), "Ann approved as"), \
        "an older approved row of the same name is not this correction's result"
    role, page, left = waiting_desk(["Dee"])
    fails(lambda: role.do("correct_driver", {"name": "Dee", "stint": 2}), "Stint of Dee")
    assert left == ["Dee"], "a correction that cannot be made approves nothing"


def t_race_control_sets_a_tyre_compound_in_the_dashboard_area():
    role, page, _ = desk()
    key = '.dd select[aria-label="Tyre compound of B #2"]'
    box = _desk_change(page, key, "/drivers/race/compound", "Compound set", options=["", "RS", "RM", "RH"])
    assert role.do("compound", {"entry": "B #2", "code": "RM"}) == \
        {"entry": "B #2", "code": "RM", "status": "Compound set"}
    assert box.value == "RM", "the entry's select holds the compound"
    assert '.navbtn[data-area="dashboard"]' in page.clicks, "the tyre table is in the Dashboard area"
    assert page.focused == key, "the desk answers in the part that holds the focus"
    fails(lambda: role.do("compound", {"entry": "B #2", "code": "XX"}), "no option")
    fails(lambda: role.do("compound", {"entry": "Z #9", "code": "RS"}), "Tyre compound of Z #9")
    box.on_change = lambda: page.respond("/console/race-control/drivers/race/compound", status=400)
    fails(lambda: role.do("compound", {"entry": "B #2", "code": "RS"}), "HTTP 400")


def t_a_choice_slower_than_the_request_window_still_counts_its_request():
    role, page, _ = desk()
    key = '.dd select[aria-label="Tyre compound of B #2"]'
    box = _desk_change(page, key, "/drivers/race/compound", "Compound set", options=["", "RS", "RM", "RH"])
    sent = box.on_change
    box.on_change = lambda: (page.wait_for_timeout(party.CONTROL_MS + 1000), sent())
    assert role.do("compound", {"entry": "B #2", "code": "RH"})["status"] == "Compound set", \
        "the request window opens when the choice is made, not before a slow choice"
    box.on_change = lambda: page.wait_for_timeout(party.CONTROL_MS + 1000)
    begin = page.clock.t
    fails(lambda: role.do("compound", {"entry": "B #2", "code": "RS"}), "sent no request to /race-control/drivers/race/compound")
    assert page.clock.t - begin >= 2 * party.CONTROL_MS / 1000, "a choice that sends nothing waits a full window"
    assert len(page.listening) == 1, "only the role's network record still listens after a request wait"


def t_race_start_is_armed_then_confirmed_in_the_race_area():
    role, page, _ = desk()
    key = desk_button(page, "Race start", "/drivers/race/start", "Race start armed: the lobby laps are cleared")
    assert role.do("race_start", {}) == {"status": "Race start armed: the lobby laps are cleared"}
    assert page.clicks[-3:] == ['.navbtn[data-area="race"]', key, key], "the Race area, arm, then confirm"
    desk_button(page, "Race start", "/drivers/race/start", "Race start refused", cls="msg err")
    fails(lambda: role.do("race_start", {}), "Race start refused")


def t_approve_all_takes_every_waiting_request_with_one_confirmed_press():
    role, page, _ = desk()
    page.add(".dd .req.waiting", count=3)
    key = '.dd button[data-label="Approve all waiting"]'
    button = page.add(key, text="Approve all waiting")
    status = page.els['.dd .msg.cur[role="status"]']
    answer = {"ok": True, "approved": [{"name": "Ann"}, {"name": "Bo"}], "skipped": [{"name": "Cy", "reason": "x"}]}

    def click():
        if button.text == "Approve all waiting":
            button.text = "Confirm Approve all waiting"
            return
        button.text = "Approve all waiting"
        page.arrive(Response(BASE + "/console/race-control/drivers/requests/approve-pending", 200, answer))
        status.text, status.attrs["class"] = "2 drivers approved; 1 still waiting: Cy (x)", "msg"
        page.els[".dd .req.waiting"].count = 1
    button.on_click = click
    out = role.do("approve_all", {})
    assert out == {"approved": 2, "skipped": 1, "names": ["Ann", "Bo"],
                   "status": "2 drivers approved; 1 still waiting: Cy (x)"}, out
    assert page.clicks[-3:] == ['.navbtn[data-area="drivers"]', key, key], "the Drivers area, arm, then confirm"
    page.els[".dd .req.waiting"].count = 0
    fails(lambda: role.do("approve_all", {}), "no waiting driver request")


def t_show_standings_opens_the_frozen_standings_of_the_entry_in_a_new_tab():
    role, page, _ = desk()
    key = '.dd tr:has(div:text-is("B #2")) button[data-label="Show standings"]'
    popup = Page(page.clock)
    popup.add("#rows tr", count=14)

    def click():
        popup.url = BASE + "/console/race-control/drivers/snapshot?time=1790000000.0&t=secret"
        role.context.opened.append(popup)
    page.add(key, on_click=click)
    out = role.do("show_standings", {"entry": "B #2"})
    assert out == {"entry": "B #2", "rows": 14}, f"the tab's rows, never its tokened URL: {out}"
    assert page.clicks[-2:] == ['.navbtn[data-area="race"]', key], "the events list is in the Race area"
    assert popup.closed, "the tab is closed again, the desk stays the role's page"
    fails(lambda: role.do("show_standings", {"entry": "A #1"}), "no event of 'A #1'")
    popup.closed, popup.els["#rows tr"].count = False, 0
    fails(lambda: role.do("show_standings", {"entry": "B #2"}), "the frozen standings")
    assert popup.closed, "a tab that shows nothing is closed too"


def t_compounds_enters_every_entrys_compound_and_keeps_one_already_set():
    role, page, _ = desk()
    entries = ["A #1", "B #2", "C #3", "D #4"]
    page.add('.dd tr:has(select[aria-label^="Tyre compound of "]) > td:first-child', texts=entries)
    boxes = {e: _desk_change(page, f'.dd select[aria-label="Tyre compound of {e}"]', "/drivers/race/compound",
                             "Compound set", options=["", "RS", "RM", "RH"]) for e in entries}
    boxes["B #2"].value = "RM"
    out = role.do("compounds", {"code": "RM", "skip": ["D #4"]})
    assert out == {"code": "RM", "changed": ["A #1", "C #3"], "kept": ["B #2"]}, out
    assert [b.value for b in boxes.values()] == ["RM", "RM", "RM", ""], "a skipped entry is left as it was"
    assert '.navbtn[data-area="dashboard"]' in page.clicks, "the tyre table is in the Dashboard area"
    fails(lambda: role.do("compounds", {"code": "RM", "skip": ["Z #9"]}), "skip: no entry 'Z #9'")
    fails(lambda: role.do("compounds", {"code": "XX"}), "no option")
    page.els['.dd tr:has(select[aria-label^="Tyre compound of "]) > td:first-child'].texts = []
    fails(lambda: role.do("compounds", {"code": "RS"}), "the tyre table lists no entry")

class MediaObs:
    """The run's OBS as `play` reads it: one clip's GetMediaInputStatus."""

    def __init__(self, duration=9000, cursor=2000, state="OBS_MEDIA_STATE_PLAYING"):
        self.state, self.duration, self.cursor, self.media_state, self.asked = self, duration, cursor, state, []

    def request(self, kind, data=None, origin="ws"):
        assert kind == "GetMediaInputStatus", f"play only reads the media status, not {kind}"
        self.asked.append(data["inputName"])
        return {"mediaState": self.media_state, "mediaDuration": self.duration, "mediaCursor": self.cursor}


def media_panel(source="Intro Video", lag=0.3):
    role, page, ctx = make(party.DirectorRole)
    key = f'#pgmBus button.k[title="{source}"]'
    el = page.add(key, text="Season opener", attrs={"class": "k"})

    def take():
        page.respond("/obs/scene")
        page.after(lag, lambda: el.attrs.update({"class": "k air"}))
    el.on_click = take
    ctx.obs = MediaObs()
    return role, page, ctx, key


def t_play_takes_a_clip_by_its_source_and_returns_at_the_end_of_its_loop():
    role, page, ctx, key = media_panel()
    start = page.clock.t
    out = role.do("play", {"media": "intro"})
    assert out == {"media": "intro", "source": "Intro Video", "length_s": 9.0, "left_s": 7.0}, out
    assert page.clicks == [key], "the key is found by the source it plays, not by its caption"
    took = page.clock.t - start
    assert 0.3 + 7.0 <= took < 0.3 + 7.5, f"play waits what is left of the clip after the air light, not {took:.1f}s"
    assert set(ctx.obs.asked) == {"Intro Video"}, "the end is read from the clip the key plays"
    ctx.obs.duration, ctx.obs.cursor = 60000, 125000
    assert role.do("play", {"media": "Intro"})["left_s"] == 55.0, "a cursor past the length counts within its loop"
    fails(lambda: role.do("play", {"media": "credits"}), "intro, trailer or outro")
    fails(lambda: role.do("play", {"media": "outro"}), "outro key", "0 matching keys")
    ctx.obs.media_state = "OBS_MEDIA_STATE_ENDED"
    fails(lambda: role.do("play", {"media": "intro"}), "Intro Video playing")
    ctx.obs.media_state, ctx.obs.duration = "OBS_MEDIA_STATE_PLAYING", None
    fails(lambda: role.do("play", {"media": "intro"}), "Intro Video playing")
    ctx.obs = None
    fails(lambda: role.do("play", {"media": "intro"}), "no OBS")


def t_the_stand_in_obs_plays_each_clip_of_the_collection_with_a_length():
    import obs_state
    role, _, ctx = make(party.DirectorRole)
    ctx.obs = type("Sim", (), {"state": obs_state.ObsState(
        collection=os.path.join(ROOT, "src", "obs", "GT_Racing_Endurance.json"))})()
    for source in roles.selectors.MEDIA.values():
        length, left = role._playing(source)
        assert length == obs_state.FILE_DURATION / 1000 and 0 < left <= length, \
            f"the stand-in OBS plays {source} as a clip with a length, so play ends: {length}, {left}"


def t_the_media_sources_are_the_ones_the_panel_titles_its_program_keys_with():
    with open(os.path.join(ROOT, "src", "director", "director-panel.html"), encoding="utf-8") as fh:
        panel = fh.read()
    found = re.search(r"const MEDIA_SCENE_ASSETS = \{([^}]*)\}", panel)
    assert found, "the panel maps its media scenes to their sources"
    assets = dict(re.findall(r"(\w+):'([^']+)'", found.group(1)))
    assert {scene.lower(): source for scene, source in assets.items()} == roles.selectors.MEDIA, \
        f"play's media map is the panel's: {assets}"
    for scene in assets:
        assert f'{{label:"{scene.upper()}", scene:"{scene}"' in panel, f"a program macro takes the {scene} scene"
    assert 'b._m = m; macroKeys.push(b); $("#pgmBus").appendChild(b);' in panel, "the macros are the #pgmBus keys"
    assert "macroKeys.forEach(b => { const source = MEDIA_SCENE_ASSETS[b._m.scene]; " \
           "if(source) { b._caption.nodeValue = assetLabel(source,b._m.label); b.title = source; } });" in panel, \
        "relabelAssetControls titles each media macro key with its source"


def drive_page(segments=()):
    """The invite page as drive.html behaves: a click hides the old error, the answer
    arrives, and a moment later the page shows the new code or the error; the commands
    stay visible after a registration."""
    role, page, ctx = make(party.DriverRole, links={"invite": "INV id/1"})
    page.add('#entry option[value="B #2"]')
    page.add("#entry", options=["A #1", "B #2"])
    page.add("#segment", options=list(segments), visible=bool(segments))
    page.add('#segment option:not([value=""])', count=len(segments), texts=list(segments))
    page.add("#name")
    err = page.add("#err", text="", visible=False)
    command = page.add("#cmd", text="", visible=False)
    page.issued, page.answer = [], {"status": 200, "error": ""}

    def register():
        err.visible = False
        if page.answer["status"] >= 400:
            page.respond("/console/drive/register", status=page.answer["status"], body={"error": page.answer["error"]})
            page.after(0.2, lambda: (setattr(err, "text", page.answer["error"]), setattr(err, "visible", True)))
            return
        code = page.answer.get("code", f"D1:code{len(page.issued) + 1}.sig")
        page.issued.append(code)
        page.respond("/console/drive/register", body={"ok": True, "code": code} if code else {"ok": True})
        page.after(0.2, lambda: (setattr(command, "text", f"python3 racecast-sender.py --code {code}"),
                                 setattr(command, "visible", True)))
    page.add("#register", on_click=register)
    return role, page, ctx


def t_a_driver_registers_through_the_invite_page_on_a_phone():
    role, page, ctx = drive_page()
    out = role.do("register", {"entry": "B #2", "name": "Ann"})
    assert out == {"entry": "B #2", "name": "Ann", "stint": ""}, f"the registration as the form sent it: {out}"
    assert page.gotos == [BASE + "/console/drive?i=INV%20id%2F1"], "the invite page, no console token"
    assert ctx._browser.viewports == [{"width": 390, "height": 844}], "a phone viewport"
    assert (page.els["#entry"].value, page.els["#name"].value) == ("B #2", "Ann"), "the form carries entry and name"
    assert "D1:" not in json.dumps(out), "the driver code never reaches a result"
    assert ctx.driver_codes == {("B #2", ""): "D1:code1.sig"}, "the page's code is handed to the drivers' field"
    assert "D1:code1.sig" in ctx.secrets, "the code is a credential the run redacts"
    role.shot("x.png")
    assert page.shots[-1][2] == ["#runSec code"], "a screenshot blacks out the sender commands"
    assert "#runSec code" in roles.selectors.LINKS, "a plain page's screenshot blacks out the sender commands too"
    role.do("register", {"entry": "B #2", "name": "Bo"})
    assert ctx.driver_codes[("B #2", "")] == "D1:code2.sig", \
        "a second registration keeps the code the page shows for it, not the one still on screen"
    assert "D1:code2.sig" in page.els["#cmd"].text, \
        "register returns once the page shows this registration's code, not the one still on screen"
    page.answer = {"status": 429, "error": "Too many registrations from this device."}
    fails(lambda: role.do("register", {"entry": "B #2", "name": "Cy"}), "HTTP 429", "Too many registrations")
    assert ctx.driver_codes[("B #2", "")] == "D1:code2.sig", "a refused registration hands over no code"
    page.answer = {"status": 200, "code": ""}
    fails(lambda: role.do("register", {"entry": "B #2", "name": "Cy"}), "issued no sender code")
    fails(lambda: role.do("register", {"entry": "B #2", "name": "Ann", "stint": 1}), "asks for no stint")
    fails(lambda: role.do("register", {"entry": "Z #9", "name": "Ann"}), "no entry 'Z #9'")
    fails(lambda: role.do("register", {"entry": "B #2"}), "entry and args.name")
    fails(lambda: make(party.DriverRole, "driver:Ann"), "no console identity")


def t_a_driver_registers_for_a_stint_under_the_invites_own_label():
    role, page, ctx = drive_page(["Heat A", "Heat B"])
    fails(lambda: role.do("register", {"entry": "B #2", "name": "Bo"}), "needs args.stint")
    out = role.do("register", {"entry": "B #2", "name": "Bo", "stint": 2})
    assert out["stint"] == "Heat B" and page.els["#segment"].value == "Heat B", \
        f"stint 2 is the invite's second segment, whatever its label: {out}"
    assert ctx.driver_codes == {("B #2", "Heat B"): "D1:code1.sig"}, "the code is kept under the invite's label"
    fails(lambda: role.do("register", {"entry": "B #2", "name": "Bo", "stint": 3}), "no stint 3")


# -- commentator and viewer -------------------------------------------------------------
def t_a_commentator_submits_a_link_and_gets_the_submission_id():
    role, page, _ = make(party.CommentatorRole, "commentator:Comms 2")
    page.els["#tally"].text, page.els["#tally"].attrs = "UP NEXT · Stint 2", {"class": "tally next"}
    form = [page.add("#subStint", options=["Stint 2"], visible=False), page.add("#subUrl", visible=False),
            page.add("#subBtn", visible=False)]
    page.add('.navbtn[data-area="stint"]', on_click=lambda: [setattr(e, "visible", True) for e in form])
    page.add("#subMsg", text="")
    page.add("#subPending", text="")
    page.add("#timing tbody tr:not(.cls)", count=3)

    def submit():
        page.respond("/cockpit/submit", body={"ok": True, "id": 7})
        page.els["#subMsg"].text = "Submitted ✓ — see status below."
        page.els["#subPending"].text = "Stint 2 — awaiting director approval"
    form[2].on_click = submit
    assert role.do("submit_link", {"url": "sim://comms2", "stint": 2}) == {"id": 7, "stint": "Stint 2"}
    assert page.els["#subUrl"].value == "sim://comms2"
    facts = role.page()
    assert facts["tally"] == "UP NEXT · Stint 2" and facts["on_air"] is False and "awaiting" in facts["pending"]
    assert facts["timing_rows"] == 3, facts
    fails(lambda: role.do("submit_link", {"url": "sim://x", "stint": "Stint 9"}), "Your stint", "no option")
    page.els["#tally"].attrs = {"class": "tally onair"}
    assert role.page()["on_air"] is True


def t_a_viewer_opens_the_timing_link_and_reads_the_page():
    role, page, _ = make(party.ViewerRole, links={"timing": "LINK id/1"})
    page.add("#timing tbody tr", count=2, texts=["1  11\nDriver 11-1", "2  22 Driver 22-1"])
    page.add("#session", text="Lap 2\nStint 1")
    page.add("#state", text="Waiting for timing data…", visible=False)
    page.add("#fastest", text="Fastest lap 0:21.6")
    out = role.do("read", {})
    assert page.gotos == [BASE + "/console/timing?l=LINK%20id%2F1"], page.gotos
    assert out == {"session": "Lap 2 Stint 1", "state": "", "fastest": "Fastest lap 0:21.6",
                   "rows": ["1 11 Driver 11-1", "2 22 Driver 22-1"], "cars": 2}, out
    assert "LINK" not in json.dumps(role.page()), "the timing link never reaches a result"
    late, _, _ = make(party.ViewerRole)
    fails(lambda: late.do("read", {}), "no timing link yet")


def t_a_freshly_opened_page_is_read_once_it_has_rendered():
    role, page, _ = make(party.ViewerRole, links={"timing": "L"})
    page.add("#session", text="")
    page.add("#state", text="Waiting for timing data…")
    page.add("#fastest", text="", visible=False)
    page.after(1.5, lambda: (page.add("#timing tbody tr", count=4, texts=["1", "2", "3", "4"]),
                             page.add("#state", text="", visible=False)))
    assert role.do("read", {"cars": 4})["cars"] == 4, "read waits for the page's first poll"
    assert role.do("read", {"cars": 0})["cars"] == 4
    fails(lambda: role.do("read", {"cars": 5}), "5 cars in the timing")

    rc, page, left = waiting_desk(["Ann"])
    shown = dict(page.els)
    for key in [k for k in page.els if ".req.waiting" in k]:
        del page.els[key]
    page.after(1.5, lambda: page.els.update(shown))
    assert rc.do("approve_drivers", {}) == {"approved": 1, "names": ["Ann"]}, "the desk lists requests after its first poll"
    fails(lambda: rc.do("approve_drivers", {}), "a waiting driver request")

    rc, page, left = waiting_desk(["Bo"])
    shown = dict(page.els)
    for key in [k for k in page.els if ".req.waiting" in k]:
        del page.els[key]
    page.after(1.5, lambda: page.els.update(shown))
    assert rc.do("approve_driver", {"name": "Bo"}) == {"name": "Bo"}

    director, page, _ = make(party.DirectorRole)
    key = '#pgmBus button.k:has-text("STINT")'

    def cut():
        page.respond("/obs/scene")
        page.els[key].attrs["class"] = "k air"
    page.after(1.5, lambda: page.add(key, attrs={"class": "k"}, on_click=cut))
    assert director.do("scene", {"scene": "Stint"}) == {"scene": "Stint"}, "the scene keys are built after load"


# -- drivers ----------------------------------------------------------------------------
class Relay:
    """The relay's driver routes as `drive.Client` offers them, in memory."""
    made = []

    def __init__(self, base):
        self.base, self.registered, self.batches = base, [], {}
        self.got = threading.Event()
        Relay.made.append(self)

    def data(self, invite):
        return {"entries": drive.roster(3), "segments": ["Stint 1", "Stint 2", "Stint 3"]}

    def register(self, invite, entry, name, segment=None, device=None):
        self.registered.append((invite, entry, name, segment, device))
        return f"D1:code-{len(self.registered)}.sig"

    def ingest(self, code, body):
        self.batches[code] = self.batches.get(code, 0) + 1
        self.got.set()


class Manual:
    """A runner the test steps by hand."""

    def __init__(self, field):
        self.field, self.lock, self.started, self.t = field, threading.Lock(), False, 0.0

    def start(self):
        self.started = True

    def run(self, seconds):
        for _ in range(int(seconds / drive.TICK_S)):
            self.t += drive.TICK_S
            self.field.step(self.t, drive.TICK_S)

    def close(self):
        return True


def drivers_role(event=None, manual=True, links=True):
    ctx = engine.RunContext(urls={"relay": BASE}, event=event or {"track": "6e378b", "track_length_m": 1200,
                                                                    "seed": 7},
                            event_link={"invite": "INVITE"}.get if links else None)
    role = drivers.DriversRole(ctx, "drivers")
    role.client_factory = Relay
    if manual:
        role.runner_factory = Manual
    role.open()
    return role


def t_drivers_register_each_stint_as_its_own_device_and_keep_the_codes():
    role = drivers_role()
    out = role.do("register", {"stints": 2})
    assert out == {"registered": 6, "skipped": 0, "entries": drive.roster(3), "stints": 2}, out
    assert "code" not in json.dumps(out) and "INVITE" not in json.dumps(out), "codes and invite stay inside"
    relay = Relay.made[-1]
    assert relay.registered[0][:4] == ("INVITE", drive.roster(3)[0], "Driver 11-1", "Stint 1")
    assert relay.registered[1][2:4] == ("Driver 11-2", "Stint 2")
    devices = [r[4] for r in relay.registered]
    assert len(set(devices)) == 6 and all(d.startswith("198.51.100.") for d in devices), devices
    assert role.page()["registered"] == 6 and role.page()["sending"] == 3
    fails(lambda: role.do("register", {}), "registered already")
    assert "code" not in json.dumps(role.page())


def t_the_later_stints_register_when_their_lobby_comes():
    role = drivers_role()
    teams = drive.roster(3)
    out = role.do("register", {"stints": 3, "now": [1], "skip": [{"entry": teams[1], "stint": 2}]})
    assert out["registered"] == 3, f"only the stint 1 drivers register before the race: {out}"
    relay = Relay.made[-1]
    assert {r[3] for r in relay.registered} == {"Stint 1"}, relay.registered
    role.do("start", {})
    fails(lambda: role.do("lobby", {"stint": 2}), "register_stint first")
    out = role.do("register_stint", {"stint": 2})
    assert out == {"stint": 2, "registered": 2}, f"stint 2 registers before its lobby, the page's seat left out: {out}"
    assert [r[3] for r in relay.registered[3:]] == ["Stint 2", "Stint 2"], relay.registered
    assert role.page()["registered"] == 5
    fails(lambda: role.do("register_stint", {"stint": 2}), "stint 2 registered already")
    fails(lambda: role.do("register_stint", {"stint": 4}), "no stint 4")
    fails(lambda: drivers_role().do("register", {"stints": 3, "now": [4]}), "now")
    late = drivers_role()
    late.do("register", {"stints": 3, "now": [2]})
    fails(lambda: late.do("start", {}), "stint 1 drivers have not registered")
    role.do("register_stint", {"stint": 3})
    fails(lambda: role.do("register_stint", {"stint": 3}), "stint 3 registered already")
    early = drivers_role()
    early.do("register", {"stints": 3, "now": [1, 2]})
    early.do("start", {})
    fails(lambda: early.do("lobby", {"stint": 4}), "stint 3 drivers have not registered")


def t_the_next_lobby_opens_while_the_old_races_and_each_car_changes_at_its_box():
    role = drivers_role()
    role.do("register", {"stints": 2})
    role.do("start", {})
    runner = role.runner
    runner.run(30.0)
    role.do("next_lobby", {"stint": 2})
    page = role.page()
    assert page["next_waiting"] == 3 and page["driving"] == 3, f"both lobbies drive: {page}"
    fails(lambda: role.do("next_lobby", {"stint": 0}), "counted from 1")
    role.do("box", {"order": "grid"})
    role.do("change", {})
    runner.run(600.0)
    page = role.page()
    assert page["next_waiting"] == 0 and {c["driver"] for c in page["cars"]} == \
        {"Driver 11-2", "Driver 22-2", "Driver 33-2"}, page


class HeatRelay(Relay):
    def data(self, invite):
        return {"entries": drive.roster(3), "segments": ["Heat A", "Heat B", "Heat C"]}


def t_drivers_leave_out_the_seat_the_driver_page_registers_and_take_its_code():
    role = drivers_role()
    role.client_factory = HeatRelay
    a, b, _ = drive.roster(3)
    out = role.do("register", {"stints": 2, "skip": [{"entry": b, "stint": 2}]})
    assert (out["registered"], out["skipped"]) == (5, 1), f"one seat is left to the driver page: {out}"
    relay = Relay.made[-1]
    assert (b, "Heat B") not in [(r[1], r[3]) for r in relay.registered], "the page's seat gets no HTTP request"
    role.do("start", {})
    fails(lambda: role.do("lobby", {"stint": 2}), b, "stint 2", "no sender code")
    role.ctx.driver_codes[(b, "Heat B")] = "D1:page.code"   # the driver role's key: the invite's label
    role.do("lobby", {"stint": 2})
    assert role.field.car(b).senders[1].code == "D1:page.code", "the seat sends with the code the page showed"
    assert "D1:page.code" not in json.dumps(role.page()), "the code never reaches a result"
    early = drivers_role()
    early.do("register", {"skip": [{"entry": a, "stint": 1}]})
    fails(lambda: early.do("start", {}), a, "stint 1", "no sender code")
    for bad, needle in (([{"entry": "Z #9", "stint": 1}], "no entry 'Z #9'"), ([{"entry": a, "stint": 4}], "stint 4"),
                        ("x", "skip is a list")):
        fails(lambda bad=bad: drivers_role().do("register", {"stints": 3, "skip": bad}), needle)


def t_drivers_register_for_one_named_segment_such_as_qualifying():
    role = drivers_role()
    role.client_factory = HeatRelay
    a, b, c = drive.roster(3)
    out = role.do("register", {"segment": "Heat C", "skip": [{"entry": b}]})
    assert (out["registered"], out["skipped"], out["stints"]) == (2, 1, 1), out
    relay = Relay.made[-1]
    assert [(r[1], r[3]) for r in relay.registered] == [(a, "Heat C"), (c, "Heat C")], \
        f"every driver registers once, for the named segment: {relay.registered}"
    role.ctx.driver_codes[(b, "Heat C")] = "D1:page.code"
    role.do("start", {})
    assert role.field.car(b).senders[0].code == "D1:page.code", "the page's seat drives with its code"
    fails(lambda: drivers_role().do("register", {"segment": "Heat D"}), "no segment 'Heat D'", "Stint 1")
    fails(lambda: drivers_role().do("register", {"segment": "Stint 1", "stints": 2}), "segment", "one")


def t_drivers_drive_the_line_the_stack_gave_the_relay():
    line = drive.trace([[x + 500.0, z] for x, z in drive.synthetic_path(2000.0)])
    role = drivers_role()
    role.ctx.track_trace = line
    role.do("register", {"stints": 1})
    assert role.field.line.trace is line, "the cars drive the app home's line, not a stadium of track_length_m"
    plain = drivers_role()
    plain.do("register", {"stints": 1})
    assert abs(plain.field.line.length - 1200) < 30, "without a line from the stack: the synthetic lap"


def t_drivers_need_the_invite_and_a_track():
    fails(lambda: drivers_role(links=False).do("register", {}), "no invite link yet")
    fails(lambda: drivers_role(event={"seed": 1}).do("register", {}), "[event] track")
    fails(lambda: drivers_role().do("start", {}), "not registered yet")


def t_the_probe_role_needs_its_watch_and_the_invite():
    probe_role = roles.probe
    with tempfile.TemporaryDirectory() as d:
        ctx = engine.RunContext(urls={"relay": BASE}, out_dir=d, event={"track_length_m": 1200},
                                event_link=lambda kind: None)
        role = probe_role.ProbeRole(ctx, "probe")
        role.open()
        try:
            fails(lambda: role.do("report", {}), "watch never started")
            fails(lambda: role.do("check", {"entry": "Apex Works #11"}), "no invite link")
            role.do("mark", {"text": "block 1"})
            sys.path.insert(0, os.path.join(ROOT, "tools"))
            import probe_stage2
            _polls, marks = probe_stage2.load(os.path.join(d, probe_role.RESULTS))
            assert [m["text"] for m in marks] == ["block 1"], marks
        finally:
            left = role.close()
        assert left == [], left


def t_the_simulated_spectator_laps_the_synthetic_line_with_its_0x84_position():
    import gt7_telemetry
    probe_role = roles.probe
    replay = probe_role.synthetic_packets(1200.0, position=4, cars=9, seconds=70.0)
    pkts = [gt7_telemetry.parse_packet(plain) for _t, plain in replay]
    assert len(pkts) == 70 * probe_role.HZ and replay[probe_role.HZ - 1][0] == 1.0, "60 packets a second"
    assert [pkts[probe_role.HZ * s].lap for s in (15, 45, 65)] == [0, 1, 2], "the counter steps once a lap"
    assert {(p.race_position, p.car_count) for p in pkts} == {(4, 9)}, "0x84 as asked"


def t_a_leader_first_box_call_names_the_first_of_the_order():
    role = drivers_role()
    role.do("register", {"stints": 1})
    role.do("start", {})
    order = list(reversed(drive.roster(3)))
    assert role.do("box", {"order": order, "leader_first": True}) == {"order": order}
    assert role.field.box_lead == order[0], "no car enters the pit lane before the first of the order"
    fails(lambda: role.do("box", {"order": "grid", "leader_first": "yes"}), "leader_first")
    role.do("box", {"order": "grid", "laps": 7})
    assert {r["lap"] for c in role.field.cars for r in c.requests if r.get("lap") is not None} == {6}, \
        "a lobby of 7 laps boxes the cars at the end of lap 6"
    out = role.do("box", {"order": "grid", "laps": 7})
    assert out["laps"] == 7, out
    assert role.do("box", {"order": "grid", "laps": 1})["laps"] == 1, "a restart plan of one lap boxes at its end"
    fails(lambda: role.do("box", {"order": "grid", "laps": 0}), "laps is a whole number from 1")


def t_driver_actions_reach_the_field():
    role = drivers_role()
    role.do("register", {"stints": 2})
    role.do("start", {})
    runner = role.runner
    assert runner.started and role.page()["driving"] == 3
    runner.run(5.0)
    relay = Relay.made[-1]
    assert relay.batches.get("D1:code-1.sig", 0) > 0, "the stint 1 driver of car 1 sends"
    assert "D1:code-2.sig" not in relay.batches, "the stint 2 driver waits for its lobby"
    role.do("pit", {"entry": 3, "seconds": 2})
    role.do("drop", {"entry": drive.roster(3)[1]})
    assert [c["mode"] for c in role.page()["cars"]][1] == "dropped"
    fails(lambda: role.do("drop", {"entry": "Nobody #0"}), "no car 'Nobody #0'")
    fails(lambda: role.do("rejoin", {"entry": 1}), "not dropped")
    assert role.do("box", {"order": "grid"}) == {"order": drive.roster(3)}
    assert role.field.box_lead is None, "a plain box call lets every car in at its next pit entry"
    fails(lambda: role.do("box", {"order": 5}), "'race', 'grid' or a list")
    fails(lambda: role.do("lobby", {"stint": 0}), "counted from 1")
    runner.run(80.0)
    assert role.page()["standing"] == 2, role.page()["cars"]
    role.do("lobby", {"stint": 2})
    role.do("rejoin", {"entry": 2, "seconds": 2})
    fails(lambda: role.do("release", {"gaps": [{"entry": drive.roster(3)[0], "gap_s": None}]}),
          "no gap to release by")
    fails(lambda: role.do("release", {"gaps": "race"}), "rows of a race reading")
    teams = drive.roster(3)
    role.do("release", {"gaps": [{"entry": teams[0], "gap_s": None}, {"entry": teams[2], "gap_s": 4.5}]})
    assert role.field.car(teams[2]).pit["delay"] == 0.0, "a gap counts from the first car's"
    assert role.field.car(teams[0]).pit["delay"] == drivers.UNGAPPED_STEP_S, \
        "a lapped car without a gap leaves after the last, in the reading's order"
    runner.run(60.0)
    page = role.page()
    assert page["driving"] == 3 and {c["driver"] for c in page["cars"]} == \
        {"Driver 11-2", "Driver 22-2", "Driver 33-2"}, page
    role.do("stop", {"entries": [1]})
    assert role.page()["sending"] == 2
    role.do("stop", {})
    assert role.page()["sending"] == 0
    leftover = role.close()
    assert leftover == [], leftover


def t_the_chequered_flag_stops_the_finished_cars_now_and_the_rest_at_the_line():
    role = drivers_role()
    role.do("register", {"stints": 1})
    role.do("start", {})
    teams = drive.roster(3)
    rows = [{"entry": teams[0], "finished": True}, {"entry": teams[1], "finished": False}, {"entry": "Gone #9"}]
    saved = drivers.probes.http_json
    drivers.probes.http_json = lambda base, path: (200, {"entries": rows}) if path == "/drivers/race" else (404, {})
    try:
        out = role.do("chequer", {})
    finally:
        drivers.probes.http_json = saved
    assert out == {"finished": [teams[0]]}, out
    assert role.field.chequered and role.field.car(teams[0]).mode == "off", "the winner stops at once"
    assert role.field.car(teams[1]).mode != "off", "a car still on its last lap drives on to the line"


def t_the_drivers_thread_sends_and_ends_with_the_role():
    role = drivers_role(manual=False)
    role.do("register", {"stints": 1})
    role.do("start", {})
    try:
        assert Relay.made[-1].got.wait(30), "the sender thread posted a batch"
    finally:
        out = role.close()
    assert out == [] and not role.runner.thread.is_alive(), "close() joins the sender thread"


def t_refused_telemetry_is_a_soft_finding_at_the_end():
    role = drivers_role()
    role.do("register", {"stints": 1})

    def refuse(code, body):
        raise OSError("ingest: HTTP 403")
    role.field.send = refuse
    role.do("start", {})
    role.runner.run(1.0)
    out = role.close()
    assert len(out) == 1 and out[0][0] == "soft" and "telemetry posts" in out[0][1], out


def t_an_unexpected_error_in_the_field_is_a_clear_step_error():
    role = drivers_role()
    role.do("register", {"stints": 1})
    role.do("start", {})

    def boom():
        raise TypeError("'<' not supported between 'float' and 'NoneType'")
    role.field.release = boom
    fails(lambda: role.do("release", {}), "the drivers could not release", "TypeError")


def t_a_failed_registration_leaves_nothing_half_done():
    role = drivers_role()

    class Flaky(Relay):
        def register(self, invite, entry, name, segment=None, device=None):
            if len(self.registered) == 2:
                raise drive.DriveError("register 'Driver 33-1' on 'Cedar Motorsport #33': HTTP 429")
            return super().register(invite, entry, name, segment, device)
    role.client_factory = Flaky
    msg = fails(lambda: role.do("register", {"stints": 1}), "HTTP 429", "2 registrations")
    assert "stay waiting" in msg, "the requests the relay already holds are named"
    assert role.registered == 0 and role.field is None and role.page() == {"registered": 0, "cars": []}
    fails(lambda: role.do("start", {}), "not registered yet")


def t_a_dead_sender_thread_is_a_hard_finding():
    role = drivers_role()
    role.do("register", {"stints": 1})
    role.do("start", {})
    role.runner.error = "ZeroDivisionError: division by zero"
    late = role.close()
    assert ("hard", "the drivers stopped sending: ZeroDivisionError: division by zero") in late, late


# -- the stack's part -------------------------------------------------------------------
def scn(event):
    return scenario.from_dict({"event": event, "step": [{"role": "api", "do": "get", "args": {"path": "/status"}}]}, REG)


def t_the_sheet_carries_the_roster_and_the_red_flag_banner():
    tabs = stack.sheet_tabs(scn({"stints": 3, "cars": 4}))
    config = tabs["Configuration"]
    assert [row[0] for row in config[1:5]] == drive.roster(4), config
    banners = [row[config[0].index("Race Control")] for row in config[1:]]
    assert "Red Flag - Race Suspended" in banners, "Race Control's RED FLAG sets this banner"
    assert [row[0] for row in stack.sheet_tabs(scn({"stints": 3}))["Configuration"][1:]] == [""] * len(stack.RACE_CONTROL), \
        "without cars the roster column is empty, one row per Race Control text"
    try:
        stack.sheet_tabs(scn({"cars": 99}))
    except ValueError as exc:
        assert "1-15" in str(exc)
    else:
        raise AssertionError("more cars than the grid holds must be refused")


def _hud(event):
    """/hud/data as the relay builds it from the Sheet the stack serves for `event`."""
    import sheet_sim
    relay = scenario._relay()
    sheet = sheet_sim.Sheet(tabs=stack.sheet_tabs(scn(event)))
    return relay.build_hud_data(relay.parse_overlay(sheet.csv("Overlay")),
                                relay.parse_config_roster(sheet.csv("Configuration")))


def t_the_hud_shows_its_round_block_and_team_tiles():
    hud = _hud({"stints": 3, "cars": 4, "track": "6e378b"})
    assert hud["round"]["top"], f"the round block has its title: {hud['round']}"
    assert hud["round"]["country"] == "Japan", f"the country is the picked layout's: {hud['round']}"
    assert os.path.isfile(os.path.join(ROOT, "src", "assets", "flags", hud["round"]["flagKey"] + ".svg")), \
        f"the round flag is a bundled one: {hud['round']}"
    assert [t["label"] for t in hud["teams"]] == drive.roster(3), f"the three team tiles: {hud['teams']}"
    assert _hud({"stints": 3})["round"]["country"] == "", "no track, no country"


def t_the_roster_has_team_colours_and_bundled_brands():
    hud = _hud({"stints": 3, "cars": 15})
    roster = scenario._relay().parse_config_roster(scenario._csv(stack.sheet_tabs(scn({"cars": 15}))["Configuration"]))
    assert sorted(roster) == sorted(drive.roster(15)), roster
    for team, info in roster.items():
        assert os.path.isfile(os.path.join(ROOT, "src", "assets", "brands", info["brandKey"] + ".png")), \
            f"{team}: a brand of src/assets/brands, not {info['brandKey']!r}"
        assert info["bgColor"] and info["textColor"], f"{team}: a tile colour the relay accepts: {info}"
    assert len({i["bgColor"] for i in roster.values()}) == 15, "every team its own colour"
    assert all(t["brandName"] for t in hud["teams"]), f"the HUD tiles name the brand: {hud['teams']}"


def t_the_sheet_carries_every_hud_text_the_panel_macros_set():
    with open(os.path.join(ROOT, "src", "director", "director-panel.html"), encoding="utf-8") as fh:
        texts = {t for t in re.findall(r'\brc:"([^"]*)"', fh.read()) if t}
    assert texts, "the panel's scene macros set a HUD race control text"
    config = stack.sheet_tabs(scn({"stints": 3, "cars": 4}))["Configuration"]
    vocab = {row[config[0].index("Race Control")] for row in config[1:]}
    assert texts <= vocab, f"a macro text missing from the Race Control vocabulary is refused: {texts - vocab}"


def t_event_classes_give_each_team_a_class_in_turn():
    config = stack.sheet_tabs(scn({"stints": 3, "cars": 4, "classes": ["GT3", "GT4"]}))["Configuration"]
    col = config[0].index("Class")
    assert [row[col] for row in config[1:5]] == ["GT3", "GT4", "GT3", "GT4"], config
    assert "Class" not in stack.sheet_tabs(scn({"cars": 4}))["Configuration"][0], "no classes, no Class column"
    for bad in ([], "GT3", [""], [3]):
        try:
            stack.event_classes({"classes": bad}, ["A"])
        except ValueError:
            continue
        raise AssertionError(f"classes {bad!r} must be refused")


def t_parts_seed_the_producer_and_channel_tabs():
    assert "Producer" not in stack.sheet_tabs(scn({})), "no parts, no Part control on the panel"
    tabs = stack.sheet_tabs(scn({"parts": 2, "qualifying_part": True}))
    assert [row[0] for row in tabs["Producer"][1:]] == ["1", "2", "Q"], tabs["Producer"]
    assert all(row[3] == stack.PART_KEY_REF.format(row[0]) and row[1] and row[2] for row in tabs["Producer"][1:]), \
        "each Part names a producer, a host and a stream key reference"
    assert tabs["Channel"][1][0] == "youtube", "starting a Part needs the event's platform"
    assert stack.broadcast_channels(tabs) == ["broadcast"], "the broadcast chat follows the Channel tab's channel"
    assert stack.broadcast_channels(stack.sheet_tabs(scn({}))) == ["broadcast"], \
        "the chat stand-in serves a run without parts too (desk-fit)"
    for bad in (-1, 10, "2", True):
        try:
            stack.event_parts({"parts": bad})
        except ValueError:
            continue
        raise AssertionError(f"parts {bad!r} must be refused")


def t_the_event_track_is_a_layout_id():
    assert stack.event_track({}) is None
    assert stack.event_track({"track": "6e378b"}) == ("6e378b", drive.DEFAULT_TRACK_M)
    assert stack.event_track({"track": "6e378b", "track_length_m": 1500}) == ("6e378b", 1500.0)
    for bad in ("../x", "6E378B", 7):
        try:
            stack.event_track({"track": bad})
        except ValueError:
            continue
        raise AssertionError(f"track {bad!r} must be refused: it names a file's row")


def t_the_app_home_has_a_graphic_and_the_tracks_line():
    tmp = tempfile.mkdtemp(prefix="racecast-roles-test-")
    try:
        src, profile = os.path.join(tmp, "src"), os.path.join(tmp, "demo")
        os.makedirs(src)
        os.makedirs(profile)
        home = os.path.join(tmp, "home")
        stack.make_home(home, src=src, profile=profile, track=("6e378b", 1200.0))
        with open(os.path.join(home, "runtime", "demo", "graphics", "sim-placeholder.png"), "rb") as fh:
            assert fh.read(8) == b"\x89PNG\r\n\x1a\n", "Start event needs one graphic on the machine"
        db = gt7_tracks.TrackDB.load(os.path.join(home, "runtime"), os.path.join(ROOT, "src", "assets", "gt7"))
        assert db.reference_trace("6e378b"), "the relay finds the picked layout's line"
        host = os.path.join(tmp, "host")
        os.makedirs(os.path.join(host, "gt7"))
        with open(os.path.join(host, "gt7", "signatures.json"), "w", encoding="utf-8") as fh:
            json.dump(drive.signatures_doc("6e378b", [[x + 500.0, z] for x, z in drive.synthetic_path(2000.0)]), fh)
        hosted = os.path.join(tmp, "hosted")
        stack.make_home(hosted, src=src, profile=profile, track=("6e378b", 2000.0), host=host)
        with open(os.path.join(host, "gt7", "signatures.json"), "rb") as a, \
                open(os.path.join(hosted, "runtime", "gt7", "signatures.json"), "rb") as b:
            assert a.read() == b.read(), "the stack hands the host's line to the app home"
        assert stack.HOST_RUNTIME == os.path.join(ROOT, "runtime"), "the host's line comes from the checkout's runtime"
        bare = os.path.join(tmp, "bare")
        stack.make_home(bare, src=src, profile=profile)
        assert not os.path.exists(os.path.join(bare, "runtime", "gt7")), "no track, no track data"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t_event_links_come_from_the_app_home_and_never_before_start_event():
    tmp = tempfile.mkdtemp(prefix="racecast-roles-test-")
    try:
        st = stack.Stack(scn({}), tmp)
        assert st.event_link("invite") is None and st.event_link("timing") is None
        target = os.path.join(tmp, "app", "runtime", "demo")
        os.makedirs(target)
        with open(os.path.join(target, "driver-invite.json"), "w", encoding="utf-8") as fh:
            json.dump({"id": "abc", "key": "k"}, fh)
        with open(os.path.join(target, "timing-link.json"), "w", encoding="utf-8") as fh:
            fh.write("{broken")
        assert st.event_link("invite") == "abc" and st.event_link("timing") is None
        ctx = engine.RunContext(event_link=st.event_link)
        assert ctx.event_link("invite") == "abc"
        fails(lambda: ctx.event_link("timing"), "no timing link yet")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t_the_runs_ports_reach_every_relay_start_as_simulation_variables():
    import simulation
    env = dict(stack.port_env((41001, 41002, 41003), 41000, 41004), RACECAST_SIMULATION="1")
    assert simulation.feed_ports(env) == (41001, 41002, 41003) and simulation.relay_port(env) == 41000
    assert not [p for p in simulation.problems(env) if "PORT" in p], simulation.problems(env)
    with open(os.path.join(SIM, "stack.py"), encoding="utf-8") as fh:
        assert '"--ports"' not in fh.read(), "the feed ports travel in the environment, so a restart keeps them"


def t_the_stack_stops_its_league_service_with_the_other_fakes():
    tmp = tempfile.mkdtemp(prefix="racecast-roles-test-")
    try:
        import league_sim
        st = stack.Stack(scn({}), tmp)
        st.league = league_sim.LeagueSim().start()
        url = st.league.http_url
        assert st.stop() == []
        try:
            league_sim.fetch(url, "/_sim/state", timeout=1.0)
            raise AssertionError("the league service still answers after stop()")
        except OSError:
            pass  # stopped
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t_the_stack_stops_a_relay_the_control_center_restarted():
    tmp = tempfile.mkdtemp(prefix="racecast-roles-test-")
    try:
        st = stack.Stack(scn({}), tmp)
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            return type("Done", (), {"returncode": 0, "stdout": ""})()
        assert st.stop_relay(run) == "" and calls == [], "nothing started, nothing to stop"
        st.racecast, st.env, st.home = os.path.join(tmp, "app", "src", "racecast.py"), {"X": "1"}, os.path.join(tmp, "app")
        assert st.stop_relay(run) == "" and calls == [], "no PID file, no relay"
        os.makedirs(os.path.join(st.home, "runtime"))
        with open(os.path.join(st.home, "runtime", "relay.pid"), "w", encoding="utf-8") as fh:
            fh.write("1")
        assert st.stop_relay(run) == ""
        argv, kw = calls[0]
        assert argv[1:] == [st.racecast, "--simulation", "relay", "stop"] and kw["env"] == {"X": "1"}, \
            "the app home's own CLI stops it, in simulation mode"
        assert kw["timeout"] and kw["errors"] == "replace"
        bad = lambda argv, **kw: type("Done", (), {"returncode": 1, "stdout": "port held"})()  # noqa: E731
        assert "port held" in st.stop_relay(bad)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _controls(labels, cut=0, clip=0):
    return [{"label": label, "cut": cut, "clip": clip} for label in labels]


def t_fit_fails_when_a_live_control_needs_scrolling():
    for cls in (party.RaceControlRole, party.DirectorRole, party.CommentatorRole):
        role, page, _ = make(cls, *(["commentator:Comms 1"] if cls is party.CommentatorRole else []))
        need = list(cls.live_required)
        fails(lambda role=role: role.do("fit", {}), "has no live column (#liveCol)")
        page.fit["#liveCol"] = []
        fails(lambda role=role: role.do("fit", {}), "the live column shows no control")
        page.fit["#liveCol"] = _controls(need[1:])
        fails(lambda role=role, need=need: role.do("fit", {}), f"the live column lacks {need[0]}")
        page.fit["#liveCol"] = _controls(need) + [{"label": "Extra", "cut": 1, "clip": 0}]
        assert role.do("fit", {}) == {"controls": len(need) + 1}, "a 1 px sub-pixel overhang still fits"
        fails(lambda role=role: role.do("fit", {"require": ["Restart released"]}), "lacks Restart released")
        page.fit["#liveCol"].append({"label": "CUT 2", "cut": 37, "clip": 0})
        fails(lambda role=role: role.do("fit", {}), f"1 of {len(need) + 2} live controls need scrolling: CUT 2 37px")


def t_fit_measures_the_rows_a_page_must_show_whole():
    role, page, _ = make(party.CommentatorRole, "commentator:Comms 1")
    need = list(party.CommentatorRole.live_required)
    rows = party.CommentatorRole.selectors["rows"]
    page.fit["#liveCol"] = _controls(need)
    fails(lambda: role.do("fit", {"rows": 14}), "shows 0 of 14 rows")
    page.fit[rows] = _controls([f"P{i}" for i in range(13)])
    fails(lambda: role.do("fit", {"rows": 14}), "shows 13 of 14 rows")
    page.fit[rows] = _controls([f"P{i}" for i in range(14)])
    assert role.do("fit", {"rows": 14}) == {"controls": len(need), "rows": 14}, "every row in view"
    page.fit[rows][13]["cut"] = 30
    fails(lambda: role.do("fit", {"rows": 14}), "1 of 14 rows need scrolling: P13 30px")
    page.fit[rows][13]["cut"] = 0
    page.fit[rows][2]["side"] = 25
    fails(lambda: role.do("fit", {"rows": 14}), "1 of 14 rows need scrolling: P2 25px")
    fails(lambda: role.do("fit", {"rows": 0}), "rows is a whole number of at least 1")
    role, page, _ = make(party.RaceControlRole)
    page.fit["#liveCol"] = _controls(party.RaceControlRole.live_required)
    fails(lambda: role.do("fit", {"rows": 3}), "has no rows to measure")


def t_fit_lets_the_layout_settle_before_it_fails():
    role, page, _ = make(party.DirectorRole)
    need = list(party.DirectorRole.live_required)
    calls = []

    def later(size):
        calls.append(size)
        return _controls(need, cut=22 if len(calls) < 3 else 0)
    page.fit["#liveCol"] = later
    assert role.do("fit", {}) == {"controls": len(need)}, "the panel's tile shrinks a frame after a request shows"
    assert len(calls) == 3, calls


def t_fit_in_a_short_window_asks_only_that_scrolling_reaches_every_control():
    role, page, _ = make(party.RaceControlRole)
    need = list(party.RaceControlRole.live_required)
    page.fit["#liveCol"] = lambda size: _controls(need, cut=0 if size[1] >= 900 else 80,
                                                  clip=0 if size != (1366, 650) else 0)
    fails(lambda: role.do("fit", {"viewport": [1366, 650]}), "need scrolling")
    assert role.do("fit", {"viewport": [1366, 650], "reachable": True}) == {"controls": len(need)}
    assert page.size == (1440, 900), "the role's own window comes back after the measure"
    page.fit["#liveCol"] = lambda size: _controls(need, cut=80, clip=40)
    fails(lambda: role.do("fit", {"viewport": [1366, 650], "reachable": True}), "are clipped")
    fails(lambda: role.do("fit", {"viewport": [1366]}), "viewport is [width, height]")


def t_fit_script_measures_a_real_page():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  (Playwright unavailable; FIT_SCRIPT runs in the event simulation)")
        return
    page = """<body style="margin:0"><div id="col" style="height:300px;overflow-y:auto">
      <div style="height:200px;overflow:hidden"><button style="margin-top:250px">Hidden</button></div>
      <button data-label="Below" style="margin-top:100px">x</button></div><button>Outside</button></body>"""
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001  no browser binary on this machine
            print(f"  (no Chromium: {type(exc).__name__})")
            return
        try:
            tab = browser.new_page(viewport={"width": 400, "height": 400})
            tab.set_content(page)
            got = {c["label"]: (c["cut"] > 0, c["clip"] > 0) for c in tab.evaluate(roles.builtin.FIT_SCRIPT, "#col")}
            missing = tab.evaluate(roles.builtin.FIT_SCRIPT, "#none")
            rows = {c["label"]: c["cut"] > 0 for c in tab.evaluate(roles.builtin.ROWS_SCRIPT, "body > button, #col > button")}
            tab.set_content('<div style="width:100px;overflow-x:auto"><button style="width:300px">Wide</button></div>')
            side = [c["side"] for c in tab.evaluate(roles.builtin.ROWS_SCRIPT, "button")]
            no_rows = tab.evaluate(roles.builtin.ROWS_SCRIPT, "#none tr")
        finally:
            browser.close()
    assert got == {"Hidden": (True, True), "Below": (True, False)}, \
        f"a box that cannot scroll clips, the scrolling column only hides: {got}"
    assert missing is None
    assert side == [200], f"a row wider than its scrolling box is cut at the side: {side}"
    assert rows == {"Below": True, "Outside": False} and no_rows == [], \
        f"ROWS_SCRIPT measures every visible match the same way: {rows} {no_rows}"


def t_the_smoke_scenario_checks_both_live_columns():
    loaded = scenario.load(os.path.join(SIM, "scenarios", "smoke.toml"), REG)
    fit = {(s.role, s.id) for s in loaded.steps if s.do == "fit"}
    assert {r.split(":")[0] for r, _ in fit} == {"race_control", "director", "commentator"}, \
        f"each live column is measured: {fit}"
    order = [s.id for s in loaded.steps]
    waiting = [i for r, i in fit if r == "race_control" and order.index(i) < order.index("approve_drivers")]
    assert waiting, "the desk is measured while driver requests wait"


def t_the_desk_fit_scenario_measures_the_tallest_states():
    loaded = scenario.load(os.path.join(SIM, "scenarios", "desk-fit.toml"), REG)
    order = [s.id for s in loaded.steps]
    fits = [(s.role, order.index(s.id)) for s in loaded.steps if s.do == "fit"]
    states = ["red_flag", "ask_graphic", "qualifying"]
    for n, (state, roles_wanted) in enumerate(zip(states, ({"race_control"}, {"director"},
                                                           {"race_control", "director"}), strict=True)):
        end = order.index(states[n + 1]) if n + 1 < len(states) else len(order)
        during = {r for r, i in fits if order.index(state) < i < end}
        assert roles_wanted <= during, f"a fit runs while {state} holds: {fits}"
    assert loaded.event.get("classes"), "the class lap row shows only with classes"
    laptop = {s.role.split(":")[0] for s in loaded.steps if s.do == "fit" and s.args.get("viewport") == [1366, 650]
              and s.args.get("reachable")}
    assert laptop == {"race_control", "director", "commentator"}, \
        f"a laptop window still reaches every live control: {laptop}"
    timed = [s.id for s in loaded.steps if s.at.kind == "time" or s.at.seconds]
    assert not timed, f"a fit waits for its state, not for a fixed time: {timed}"
    on_laps = re.compile(r"\blaps?\b|\blap\.")
    laps = [s.id for s in loaded.steps if (s.at.kind == "race" and "lap" in s.at.condition)
            or any(on_laps.search(e) for e in s.expect)]
    assert not laps, f"no step waits on a lap, by its trigger or a lap count in an expect: {laps}"
    cockpit = [s for s in loaded.steps if s.role.startswith("commentator") and s.do == "fit" and not s.args.get("viewport")]
    assert cockpit and cockpit[0].args.get("rows") == loaded.event["cars"] == 14, \
        f"the cockpit shows all 14 cars of the timing at 1440x900: {[s.args for s in cockpit]}"


def t_the_smoke_scenario_uses_every_party_once():
    path = os.path.join(SIM, "scenarios", "smoke.toml")
    loaded = scenario.load(path, REG)
    kinds = {s.role.split(":")[0] for s in loaded.steps}
    assert {"producer", "director", "race_control", "commentator", "viewer", "drivers"} <= kinds, kinds
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    free = [s.get("id") for s in raw["step"]
            if s["role"].split(":")[0] not in ("api", "obs", "stream") and not str(s.get("at", "")).startswith("after:")]
    assert free == [], f"without a browser these steps would not be skipped with their chain: {free}"
    assert loaded.steps[-1].do == "stop_event", "Stop event ends the relay, so it is the last step"


EVENT_SCENARIOS = ("quick", "full", "multiclass", "late-link", "dc-rejoin", "relay-crash", "red-flag")


def t_the_bundled_events_run_three_lobbies_and_check_each_commentator_on_program():
    for name in EVENT_SCENARIOS:
        path = os.path.join(SIM, "scenarios", name + ".toml")
        loaded = scenario.load(path, REG)
        assert loaded.event.get("stints") == 3, f"{name}: three stints, each its own lobby"
        stints = sorted(c.stint for c in loaded.commentators)
        assert stints == [1, 2, 3], f"{name}: one commentator per stint, not {stints}"
        lobbies = [s.args.get("stint") for s in loaded.steps if s.role == "drivers" and s.do in ("lobby", "next_lobby")]
        assert {2, 3} <= set(lobbies), f"{name}: the drivers change into the lobbies of stints 2 and 3: {lobbies}"
        checks = " ".join(e for s in loaded.steps for e in s.expect)
        for c in loaded.commentators:
            assert f"'{c.channel}' in obs.program_channels" in checks, \
                f"{name}: no check that program shows {c.name}'s picture"
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
        free = [s.get("id") for s in raw["step"] if s["role"].split(":")[0] not in ("api", "obs", "stream", "relay")
                and not str(s.get("at", "")).startswith("after:")]
        assert free == [], f"{name}: without a browser these steps would not be skipped with their chain: {free}"
        last = loaded.steps[-1]
        assert last.do == "stop_event" or last.do == "end_part" and "not relay.up" in last.expect, \
            f"{name}: the step that ends the event is the last step, an end_part only when it stops the relay"
        assert all(s.expect for s in loaded.steps if s.role == "api"), f"{name}: every probe step asserts something"


FULL_SHOTS = {   # the deck's images as `full` takes them: name -> (step id, role, element)
    'approval-requests-1': ('desk_requests', 'race_control', '.area[data-area="drivers"]'),
    'approval-approved-1': ('approve_drivers', 'race_control', '.area[data-area="drivers"]'),
    'quali-desk-1': ('quali_desk', 'race_control', '.area[data-area="qualifying"]'),
    'quali-public-1': ('quali_timing', '', ''),
    'quali-cockpit-1': ('shot_quali_cockpit_1', 'commentator:Comms 1', '#timingCard'),
    'quali-tower-gap-1': ('shot_quali_tower_gap_1', 'page:overlay', '#drivers-tower'),
    'formation-1': ('shot_formation_1', 'page:overlay', ''),
    'formation-2': ('shot_formation_2', 'viewer', ''),
    'start-1': ('shot_start_1', 'race_control', '.area[data-area="race"] .tbl'),
    'start-2': ('shot_start_2', 'page:overlay', ''),
    'new-cockpit-timing-1': ('shot_new_cockpit_timing_1', 'commentator:Comms 1', '#timingCard'),
    'pits-1': ('shot_pits_1', 'race_control', '.area[data-area="race"] .tbl'),
    'pits-2': ('shot_pits_2', 'page:overlay', ''),
    'tower-full-1': ('shot_tower_full_1', 'page:overlay', '#drivers-tower'),
    'dc-suspected-1': ('shot_dc_suspected_1', 'race_control', '.area[data-area="race"] .tbl'),
    'dc-suspected-2': ('shot_dc_suspected_2', 'race_control', '.area[data-area="race"] .tbl >> nth=1'),
    'dc-tower-1': ('shot_dc_tower_1', 'page:overlay', '#drivers-tower'),
    'dc-decided-1': ('desk_events', 'race_control', '.area[data-area="race"] .tbl >> nth=1'),
    'dc-tower-2': ('shot_dc_tower_2', 'page:overlay', '#drivers-tower'),
    'dc-onair-3': ('dc_public', '', ''),
    'est-onair-1': ('shot_est_onair_1', 'page:overlay', '#drivers-tower'),
    'est-desk-1': ('estimate', 'race_control', '.area[data-area="corrections"]'),
    'est-onair-2': ('shot_est_onair_2', 'page:overlay', '#drivers-tower'),
    'est-onair-3': ('shot_est_onair_3', 'viewer', ''),
    'est-onair-4': ('shot_est_onair_4', 'page:overlay', '#drivers-map'),
    'new-dnf-1': ('desk_dnf', 'race_control', '.area[data-area="race"] .tbl'),
    'new-dnf-6': ('shot_new_dnf_6', 'viewer', ''),
    'new-dnf-tower-1': ('shot_new_dnf_tower_1', 'page:overlay', '#drivers-tower'),
    'swaps-1': ('shot_swaps_1', 'race_control', '.area[data-area="race"] .tbl'),
    'swaps-2': ('shot_swaps_2', 'page:overlay', '#drivers-map'),
    'swaps-3': ('shot_swaps_3', 'race_control', '.area[data-area="race"] .tbl'),
    'director-1': ('shot_director_1', 'director', ''),
    'dc-rejoin-desk-1': ('shot_dc_rejoin_desk_1', 'race_control', '.area[data-area="race"] .tbl'),
    'dc-rejoin-tower-1': ('shot_dc_rejoin_tower_1', 'page:overlay', '#drivers-tower'),
    'dc-rejoin-public-1': ('shot_dc_rejoin_public_1', 'viewer', ''),
    'dc-states-3': ('shot_dc_states_3', 'race_control', '.area[data-area="race"] .tbl >> nth=1'),
    'lapped-1': ('shot_lapped_1', 'race_control', '.area[data-area="race"] .tbl'),
    'restart-1': ('red_flag', 'race_control', '.area[data-area="race"] .tbl'),
    'redflag-1': ('shot_redflag_1', 'page:overlay', ''),
    'redflag-2': ('shot_redflag_2', 'viewer', ''),
    'restart-2': ('restart_released', 'race_control', '.area[data-area="race"] .tbl'),
    'restart-3': ('shot_restart_3', 'race_control', '.area[data-area="race"] .tbl'),
    'restart-tower-1': ('shot_restart_tower_1', 'page:overlay', '#drivers-tower'),
    'finallap-2': ('shot_finallap_2', 'page:overlay', ''),
    'finallap-1': ('desk_final', 'race_control', '.area[data-area="race"] .tbl'),
    'chequered-1': ('shot_chequered_1', 'page:overlay', ''),
    'chequered-2': ('shot_chequered_2', 'viewer', ''),
    'finish-due-1': ('desk_finish_due', 'race_control', ''),
    'finished-1': ('shot_finished_1', 'page:overlay', ''),
    'finished-2': ('shot_finished_2', 'viewer', ''),
    'new-cockpit-finished': ('shot_new_cockpit_finished', 'commentator:Comms 3', '#timingCard'),
}
FULL_CHAPTERS = ["Qualifying (the day before)", "On air: qualifying (the day before)", "Race day: start the event",
                 "Drivers sign up", "Race start", "Stint 1 at the desk", "Next commentator", "Driver swap 1",
                 "Stint 2 at the desk", "Driver swap 2", "The finish", "Off air"]


def _full():
    return scenario.load(os.path.join(SIM, "scenarios", "full.toml"), REG)


def t_no_captioned_action_follows_an_element_shot_of_its_page_at_once():
    with open(os.path.join(SIM, "scenarios", "full.toml"), "rb") as fh:
        steps = tomllib.load(fh)["step"]
    by = {s["id"]: s for s in steps}
    close = []
    for s in steps:
        at = str(s.get("at", ""))
        if not at.startswith("after:") or not s.get("caption"):
            continue
        ref, _, off = at[len("after:"):].partition("+")
        shot = by[ref].get("shot")
        if isinstance(shot, dict) and shot.get("element") and (shot.get("role") or by[ref]["role"]) == s["role"]:
            if float(off.rstrip("s") or 0) < 6:
                close.append(s["id"])
    assert close == [], f"an element shot disturbs its page's recording for a moment; leave 6 s: {close}"


def t_the_full_scenario_keeps_the_decks_51_shots():
    shots = {s.shot.name: (s.id, s.shot.role, s.shot.element) for s in _full().steps if s.shot}
    assert len(FULL_SHOTS) == 51, "the deck's race-day subset"
    assert shots == FULL_SHOTS, \
        f"each name stays at its step, role and element: {sorted(set(shots.items()) ^ set(FULL_SHOTS.items()))}"


def t_the_full_scenario_closes_pages_while_the_film_does_not_need_them():
    steps = _full().steps
    by = {s.id: s for s in steps}
    closed = {}
    for s in steps:
        if s.do == "close":
            closed[s.role] = s.index
        elif s.role in closed and s.do != "close":
            closed.pop(s.role)
        for e in s.expect:
            for role in list(closed):
                assert f"page('{role}')" not in e, f"{s.id} checks {role}, which is closed"
        if s.shot:
            assert s.shot.role not in closed, f"{s.id} shoots {s.shot.role}, which is closed"
    away = {s.role for s in steps if s.do == "close"}
    assert {"producer", "driver", "commentator:Comms 1", "commentator:Comms 2", "commentator:Comms 3"} <= away, away
    assert _after(by, "next3", "cockpit3_back"), "Comms 3 is open again before NEXT puts it on air"


def t_the_full_scenario_plays_the_twelve_chapters_of_the_storyboard():
    steps = _full().steps
    at = {s.id: s.index for s in steps}
    assert [s.chapter for s in steps if s.chapter] == FULL_CHAPTERS, [s.chapter for s in steps if s.chapter]
    quiet = [s.id for s in steps if (REG[scenario.role_kind(s.role)].recorded or scenario.role_kind(s.role) == "drivers")
             and s.do not in ("open", "close", "view") and not s.caption]
    assert quiet == [], f"every party step the film shows has a caption: {quiet}"
    assert all(s.caption for s in steps if s.chapter), "each chapter opens on a captioned step"

    def first(pred, what):
        found = [s for s in steps if pred(s)]
        assert found, f"full has no step that {what}"
        return found[0]
    submit = first(lambda s: s.role == "commentator:Comms 1" and s.do == "submit_link", "submits the first link")
    approve = first(lambda s: s.do == "approve_link" and s.args.get("stint") == submit.args["stint"],
                    "approves the first link")
    warm_a = first(lambda s: s.do == "view" and "relay.status.feeds.A.state == 'serving'" in s.expect,
                   "waits for feed A to run warm")
    on_air = first(lambda s: s.do == "scene" and s.args.get("scene") == "Stint", "puts Comms 1 on program")
    assert submit.index < approve.index < warm_a.index < on_air.index, "the first link is approved, then runs warm"
    page = first(lambda s: s.role == "driver" and s.do == "register", "registers on the invite page")
    field = first(lambda s: s.role == "drivers" and s.do == "register", "registers the other drivers")
    assert page.index < field.index, "a driver registers on the invite page, the others follow"
    everyone = at["approve_drivers"]
    for do in ("correct_driver", "reject_driver"):
        assert first(lambda s, do=do: s.do == do, do).index < everyone, f"{do} before approve_drivers takes the rest"
    plays = [(s.args.get("media"), s.index) for s in steps if s.do == "play"]
    assert [m for m, _ in plays] == ["intro", "trailer", "outro"], plays
    starts = [s for s in steps if s.do == "start_part"]
    assert starts[-1].index < plays[0][1], f"{starts[-1].id}: the broadcast starts before the intro plays"
    assert steps[-1].do == "end_part" and steps[-1].id == "off_air" and "not relay.up" in steps[-1].expect, \
        "the director's end of the last Part ends the race event"
    race_on_air = [s for s in steps if s.do == "scene" and s.args.get("scene") == "Stint"][1]
    assert plays[1][1] < race_on_air.index and plays[2][1] > at["final_lap"] and plays[2][1] < at["off_air"], \
        "intro and trailer before the race's program, the outro after the finish"
    pits = [s for s in steps if s.role == "drivers" and s.do == "pit"]
    codes = [s for s in steps if s.do == "compound"]
    assert len(pits) == len(codes) == 2, (pits, codes)
    for pit, code, (lo, hi) in zip(pits, codes, (("racing", "next2"), ("next2", "next3")), strict=True):
        assert at[lo] < pit.index < code.index < at[hi], f"a pit stop and its compound between {lo} and {hi}"
    assert codes[0].args["entry"] == codes[1].args["entry"] and codes[0].args["code"] != codes[1].args["code"], \
        f"stint 2's stop changes the compound: {[c.args for c in codes]}"
    for box, nxt, (out, inn) in (("box2", "next2", ("comms1", "comms2")), ("box3", "next3", ("comms2", "comms3"))):
        split = [s for s in steps if at[box] < s.index < at[nxt] and s.do == "scene" and s.args.get("scene") == "Split"]
        assert split, f"the splitscreen is on program while the cars box, before {nxt}"
        back = [s for s in steps if s.index > at[nxt] and s.do == "scene" and s.args.get("scene") == "Stint"][:1]
        assert back and f"'{inn}' in obs.program_channels" in back[0].expect \
            and f"'{out}' not in obs.program_channels" in back[0].expect, \
            f"after {nxt} the director cuts back to the stint, which shows only {inn}: {back}"


def t_the_full_scenario_shows_each_switch_in_four_quarters_with_both_cockpits_open():
    steps = _full().steps
    four = [s for s in steps if s.focus and len(s.focus) == 4]
    assert [s.id for s in four] == ["next2", "next3"], [s.id for s in four]
    for step, (out, inn) in zip(four, (("Comms 1", "Comms 2"), ("Comms 2", "Comms 3")), strict=True):
        cockpits = [f"commentator:{out}", f"commentator:{inn}"]
        assert set(step.focus) == {"director", "program", *cockpits}, step.focus
        for role in cockpits:
            assert any(s.role == role and s.index < step.index for s in steps), f"{role} is open at {step.id}"


def t_every_focus_of_the_full_scenario_shows_an_open_page():
    steps = _full().steps
    def page(v):
        return v if v.startswith("commentator:") else v.split(":")[0]   # a tab is open with its role's page
    early = [(s.id, v) for s in steps for v in s.focus or () if v != "program"
             and not any(o.role == page(v) and o.index <= s.index for o in steps)]
    assert early == [], f"a focus names a page no step has opened yet: {early}"
    blind = [s.id for s in steps if s.caption and not s.focus and not REG[scenario.role_kind(s.role)].recorded]
    assert blind == [], f"a captioned step of a role without a page names the views to enlarge: {blind}"


def _chain(by, step_id):
    """The steps `step_id` waits for through its `at = "after:..."` refs, nearest first."""
    out, at = [], by[step_id].at
    while at.kind == "after":
        out.append(by[at.ref])
        at = by[at.ref].at
    return out


def _after(by, later, earlier):
    return earlier in [s.id for s in _chain(by, later)]


_ENDS = ("stop_event", "end_part")   # Stop event, or the director ending the last Part


def _events(by, steps):
    """The qualifying event's start and end and the race event's start, in that order."""
    starts = [s for s in steps if s.do == "start_event"]
    stops = [s for s in steps if s.do in _ENDS]
    assert len(starts) == len(stops) == 2, ([s.id for s in starts], [s.id for s in stops])
    return starts[0], stops[0], starts[1]


def t_the_qualifying_timer_holds_the_session_length_before_program_goes_on_air():
    steps = _full().steps
    by = {s.id: s for s in steps}
    quali, _stop, _race = _events(by, steps)
    on_air = next(s for s in steps if s.do == "scene" and _after(by, s.id, quali.id))
    timer = next(s for s in steps if s.do == "timer" and _after(by, s.id, quali.id))
    assert _after(by, on_air.id, timer.id), \
        f"{timer.id} sets the length before {on_air.id}, so the program never shows the 6:00:00 preset"


def t_the_full_scenario_shows_race_control_messages_at_formation_final_lap_and_finish():
    steps = _full().steps
    by = {s.id: s for s in steps}
    assert not [s.id for s in steps if s.do == "hud" and s.args.get("field") == "racecontrol"], \
        "the director no longer sets the race's HUD messages by hand"
    formation = next(s for s in steps if s.do == "formation_lap")
    assert formation.role == "race_control" and formation.caption, formation
    assert _after(by, formation.id, "formation") and _after(by, "racing", formation.id), \
        "Formation Lap shows while the cars run the formation lap"
    for text in ("Formation Lap", "Final Lap", "Chequered Flag"):
        assert text in stack.RACE_CONTROL, f"{text!r} is in the run's Configuration vocabulary"

    def expects(step_id, text):
        return f"relay.get('/setup/data').fields.racecontrol == '{text}'" in by[step_id].expect
    assert expects(formation.id, "Formation Lap")
    assert expects("rc_go", "") and _after(by, "rc_go", "racing"), "it goes by itself when the race starts"
    assert expects("final_lap", "Final Lap") and by["final_lap"].role == "race_control", \
        "Race Control's final lap call shows Final Lap with it"
    assert expects("rc_flag", "Chequered Flag") and _after(by, "rc_flag", "flag") and by["rc_flag"].role == "api", \
        "Chequered Flag replaces it by itself once the leader has taken the flag"


def t_the_full_scenario_plays_qualifying_the_day_before_as_its_own_event():
    steps = _full().steps
    by = {s.id: s for s in steps}
    quali, quali_stop, race = _events(by, steps)
    assert quali.args.get("qualifying") is True and "relay.status.mode == 'qualifying'" in quali.expect, \
        f"{quali.id}: the qualifying event starts in qualifying mode"
    assert race.args.get("qualifying") is False and "relay.status.mode == 'race'" in race.expect, \
        f"{race.id}: the race event starts in race mode, whatever the Control Center's toggle still shows"
    assert _after(by, quali_stop.id, quali.id) and _after(by, race.id, quali_stop.id), \
        "qualifying starts and stops before the race event starts"
    assert not [s.id for s in steps if s.do == "mode"], "each event starts in its own mode: no live switch"
    for step in [s for s in steps if s.do in ("add_lap", "submit_link") and s.args.get("stint") != 2]:
        assert _after(by, quali_stop.id, step.id) and _after(by, step.id, quali.id), \
            f"{step.id}: in the qualifying event"
    for step in [s for s in steps if s.do in ("race_start", "race_settings")
                 or s.role == "drivers" and s.do == "register"]:
        assert _after(by, step.id, race.id), f"{step.id}: in the race event, never in qualifying"
    quali_steps = [s for s in steps if _after(by, quali_stop.id, s.id) and _after(by, s.id, quali.id)]

    def beat(what, pred):
        found = [s for s in quali_steps if pred(s)]
        assert found and all(s.caption and s.focus for s in found), f"qualifying shows {what} under a caption"
        return found[0]
    beat("the cockpit on air", lambda s: s.role == "commentator:Comms 1" and
         "page('commentator:Comms 1').on_air" in s.expect)
    page = beat("a driver on the invite page", lambda s: s.role == "driver" and s.do == "register")
    assert page.args.get("stint") == "Qualifying", page.args
    field = beat("the drivers registering", lambda s: scenario.role_kind(s.role) == "drivers" and s.do == "register")
    assert field.role != "drivers" and field.args.get("segment") == "Qualifying" and \
        field.args.get("skip") == [{"entry": page.args["entry"]}], \
        f"{field.role}: its own field, registered for the qualifying segment, the page's seat left out"
    approve = beat("Race Control approving them", lambda s: s.do == "approve_all")
    drive_ = beat("the cars on track", lambda s: s.role == field.role and s.do == "start")
    laps = beat("Race Control at the qualifying desk", lambda s: s.do == "add_lap")
    silent = [s for s in quali_steps if s.role == field.role and s.do == "drop"]
    assert [s.args["entry"] for s in silent] == [s.args["entry"] for s in quali_steps if s.do == "add_lap"] \
        and _after(by, laps.id, silent[0].id), \
        "telemetry times the qualifying; Race Control adds a lap only for the car whose sender sends nothing"
    stop = beat("the cars stopping", lambda s: s.role == field.role and s.do == "stop")
    assert _after(by, approve.id, field.id) and _after(by, drive_.id, approve.id) and _after(by, stop.id, drive_.id) \
        and _after(by, laps.id, drive_.id), "registered, approved, then driven; Race Control adds laps meanwhile"
    timed = [s for s in quali_steps if any("race.qualifying" in e and "best_ms" in e for e in s.expect)]
    assert timed and _after(by, stop.id, timed[0].id), "the cars stop once the qualifying lists their laps"
    clock = beat("the director setting the qualifying timer", lambda s: s.do == "timer")
    assert clock.args["duration"] == "0:10:00" and "relay.get('/timer/data').duration_s == 600" in clock.expect \
        and _after(by, drive_.id, clock.id), "the 10 minute qualifying timer is set before the session starts"
    go = beat("the timer started by hand", lambda s: s.do == "timer_key" and s.args.get("key") == "START")
    assert go.at.ref == drive_.id and "relay.get('/timer/data').mode == 'running'" in go.expect, \
        f"{go.id}: the director starts the timer as the session starts in the lobby ({drive_.id})"
    over = [s for s in quali_steps if "relay.status.timer.remaining_s == 0" in s.expect]
    assert over and _after(by, over[0].id, timed[0].id) and stop.at.ref == over[0].id \
        and over[0].timeout > 600, "the cars come in once the qualifying timer ran out, the laps inside it"
    assert quali_stop.role == "director" and quali_stop.do == "end_part" and quali_stop.caption \
        and "not relay.up" in quali_stop.expect, \
        f"{quali_stop.id}: the director's end of the Q Part ends the qualifying event on camera"
    live = beat("the director starting the Q Part", lambda s: s.do == "start_part")
    on_air = next(s for s in quali_steps if s.do == "scene")
    assert _after(by, on_air.id, live.id), "the qualifying broadcast starts before program goes on air"
    result = [s for s in quali_steps if s.role == "viewer" and _after(by, s.id, stop.id)
              and _after(by, quali_stop.id, s.id) and s.caption]
    assert result, "the qualifying result stays in view before the broadcast ends"
    assert not [s.id for s in steps if s.do == "stop_event"], "the last Part's end stops each event, not the producer"
    driver_open = [s for s in steps if s.role == "driver" and s.do == "open" and _after(by, s.id, race.id)]
    assert driver_open and all(_after(by, s.id, driver_open[0].id) for s in steps
                               if s.role == "driver" and s.do == "register" and _after(by, s.id, race.id)), \
        "the race event's new invite opens again"
    chapters = [(s.chapter, _after(by, quali_stop.id, s.id) or s.id == quali.id) for s in steps if s.chapter]
    assert all(("the day before" in c) == early for c, early in chapters), \
        f"the qualifying chapters, and only they, say it is the day before: {chapters}"
    viewer = [s for s in steps if s.role == "viewer" and s.do == "open" and _after(by, s.id, race.id)]
    assert viewer and all(_after(by, s.id, viewer[0].id) for s in steps if s.role == "viewer" and s.do == "read"
                          and _after(by, s.id, race.id)), "the race event's new timing link opens again"


def t_the_full_scenario_splits_on_the_leaders_box_entry_and_each_team_changes_at_its_box_stop():
    steps = _full().steps
    by = {s.id: s for s in steps}
    swaps = ((2, ("comms1", "comms2")), (3, ("comms2", "comms3")))
    nexts = [s for s in steps if s.do == "next"]
    assert len(nexts) == 2, [s.id for s in nexts]
    for (stint, (out, inn)), nxt, prev in zip(swaps, nexts, [None, *nexts], strict=False):
        box = [s for s in steps if s.role == "drivers" and s.do == "box" and _after(by, nxt.id, s.id)][-1]
        assert not box.args.get("leader_first") and box.args.get("laps"), \
            f"{box.id}: each car boxes at the end of its own second to last lap, none drives on behind the leader"
        opened = [s for s in steps if s.role == "drivers" and s.do == "next_lobby" and s.args.get("stint") == stint]
        assert len(opened) == 1 and _after(by, box.id, opened[0].id), \
            f"the stint {stint} lobby opens while the old one still races, before {box.id}"
        split = [s for s in _chain(by, nxt.id) if s.do == "scene" and s.args.get("scene") == "Split"]
        assert split and _after(by, split[0].id, box.id), f"{nxt.id}: the splitscreen after {box.id}"
        split = split[0]
        leader = by[split.at.ref]
        assert "race.entries[0].status == 'box'" in leader.expect and _after(by, leader.id, box.id), \
            f"{split.id} goes on program once the race leader stands in the box"
        assert nxt.at.ref == split.id, f"{nxt.id} opens the change window right after the splitscreen"
        warm = [s for s in _chain(by, split.id) if (prev is None or _after(by, s.id, prev.id))
                and f"relay.status.handover_next.stint == {stint}" in s.expect
                and "relay.status.feeds[relay.status.handover_next.feed].state == 'serving'" in s.expect]
        assert warm, f"{split.id}: the incoming feed serves before the splitscreen"
        change = [s for s in steps if s.at.ref == nxt.id and s.role == "drivers" and s.do == "change"]
        assert change, f"the next drivers may leave their box only after {nxt.id}, inside the relay's change window"
        changed = [s for s in steps if s.at.ref == change[0].id]
        assert changed and "page('drivers').next_waiting == 0" in changed[0].expect and changed[0].timeout == 300, \
            "every team changes at its own box stop, bounded to 5 minutes"
        assert changed[0].caption and changed[0].focus, f"{changed[0].id}: the film captions the changes"
        cut = [s for s in steps if s.at.ref == changed[0].id and s.do == "scene" and s.args.get("scene") == "Stint"]
        assert cut and f"'{inn}' in obs.program_channels" in cut[0].expect and \
            f"'{out}' not in obs.program_channels" in cut[0].expect, f"after the changes the cut shows only {inn}"
        compounds = [s for s in steps if s.do == "compounds" and s.at.ref == cut[0].id]
        assert compounds, f"Race Control enters the compounds right after the cut to stint {stint}"
        assert not [s for s in steps if s.role == "drivers" and s.do == "release" and _after(by, s.id, opened[0].id)
                    and _after(by, compounds[0].id, s.id)], "no field-wide release at a swap"


def t_the_full_scenario_sets_the_race_timer_and_calls_the_final_lap_when_it_runs_out():
    steps = _full().steps
    by = {s.id: s for s in steps}
    _, _, race = _events(by, steps)
    timers = [s for s in steps if s.do == "timer" and _after(by, s.id, race.id)]
    starts = [s for s in steps if s.do == "race_start"]
    formation = [s for s in steps if s.role == "drivers" and s.do == "start"][0]
    assert len(timers) == len(starts) == 1, (timers, starts)
    timer, start = timers[0], starts[0]
    reset = [s for s in _chain(by, timer.id) if s.do == "timer_key" and s.args.get("key") == "RESET"]
    assert reset and _after(by, reset[0].id, race.id) and "relay.get('/timer/data').mode == 'prestart'" in \
        reset[0].expect, "the qualifying timer finished: Race start auto-starts only a reset one"
    assert timer.role == "director" and start.role == "race_control" and timer.caption and start.caption
    assert _after(by, timer.id, race.id) and _after(by, start.id, timer.id) and _after(by, formation.id, start.id), \
        "the director sets the duration, Race Control presses Race start with the cars on the grid"
    assert timer.args["duration"] != "6:00:00", "the simulated race's length, not the panel's default"
    h, m, sec = (int(x) for x in timer.args["duration"].split(":"))
    assert f"relay.get('/timer/data').duration_s == {h * 3600 + m * 60 + sec}" in timer.expect, timer.expect
    assert "race.start_armed" in start.expect, start.expect
    assert "relay.get('/timer/data').mode == 'running'" in by["racing"].expect, \
        "the timer runs from the first race lap after Race start"
    final = by["final_lap"]
    due = by[final.at.ref]
    swap3 = [s for s in steps if s.role == "drivers" and s.do == "next_lobby" and s.args.get("stint") == 3][0]
    assert _after(by, due.id, swap3.id), "the final lap is in stint 3"
    assert any("relay.status.timer.remaining_s" in e for e in by["flag"].soft), \
        "the run reports whether the timer ran out during the last lap"


def t_the_full_scenario_runs_each_stint_lobby_to_its_lap_count():
    steps = _full().steps
    by = {s.id: s for s in steps}
    assert by["race_settings"].args["finish_mode"] == "laps", "the final lobby finishes by laps"
    counts = by["stint_laps"].args["laps"]
    assert by["stint_laps"].role == "race_control" and sorted(counts) == ["Stint 1", "Stint 2", "Stint 3"], counts
    assert _after(by, by["formation"].id, "stint_laps"), "Race Control sets the counts before the cars leave the grid"
    boxes = [s for s in steps if s.role == "drivers" and s.do == "box" and s.args.get("laps")]
    assert [b.args.get("laps") for b in boxes] == [counts["Stint 1"], "${take_plan.laps}"], \
        f"the swaps come at the end of the lobby's second to last lap: {[b.args for b in boxes]}"
    sheet = [s for s in by["stint_laps"].soft if "sheet.tab('Schedule')" in s]
    records = [{"URL": "", "Streamer": "", "Stint": f"Stint {n}", "Laps": str(counts[f"Stint {n}"])} for n in (1, 2, 3)]

    class Sheet(expect.Probe):
        calls = ("tab",)

        def tab(self, name):
            return records
    assert sheet and all(expect.truth(s, {"sheet": Sheet()})[0] for s in sheet), \
        f"the Sheet check finds the Schedule row by its header: {sheet}"
    late = by[by["swap2_driver"].at.ref]
    assert "race.lobby_lap >= result('take_plan').laps - 2" in late.expect, \
        f"the swap after the restart waits for the restart lobby's laps, not the race's: {late.expect}"
    take = by["take_plan"]
    assert take.role == "race_control" and _after(by, take.id, "red_flag") \
        and _after(by, "restart_lobby", take.id), "the plan is taken during the red flag, before the restart lobby"
    assert f"race.lobby_lap >= {counts['Stint 3']}" in by[by["final_lap"].at.ref].expect, \
        "Race Control calls the final lap as the leader starts the final lobby's last lap"


def t_the_full_scenario_shows_the_dc_snapshot_the_stint_compounds_crew_chat_and_approve_all():
    steps = _full().steps
    by = {s.id: s for s in steps}
    dc = by["dc_drop"].args["entry"]
    standings = [s for s in steps if s.do == "show_standings"]
    assert len(standings) == 1 and standings[0].args.get("entry") == dc, standings
    snap = standings[0]
    assert _after(by, snap.id, "dc_event") and _after(by, "dc_confirm", snap.id) and snap.caption and snap.focus, \
        "Race Control opens the standings of before the drop while the disconnect is suspected"
    between = [s.id for s in _chain(by, snap.id)]
    suspected = [by[i] for i in reversed(between[:between.index("dc_event")]) if by[i].caption]
    assert suspected and suspected[0].do == "hold" and suspected[0].role == "race_control", \
        f"the suspected phase plays in real time under a caption: {[s.id for s in suspected]}"
    sets = [s for s in steps if s.do == "compounds"]
    bounds = (("race_start", "formation"), ("changed2", "rejoin_dc"), ("changed3", "last_lap"))
    assert len(sets) == len(bounds), [s.id for s in sets]
    for step, (lo, hi) in zip(sets, bounds, strict=True):
        assert _after(by, step.id, lo) and _after(by, hi, step.id) and step.caption, f"{step.id}: at the start of a stint"
        after = [s for s in steps if s.at.kind == "after" and s.at.ref == step.id]
        assert len(after) == 1 and after[0].role == "race_control" and after[0].do == "view" \
            and after[0].args.get("area") == "race", \
            f"{step.id} leaves the desk on Dashboard: the race area comes back before its next shot"
    codes = [s.args["code"] for s in sets]
    assert len(set(codes)) == len(codes), f"each stint starts on its own compound: {codes}"
    assert by["approve_drivers"].do == "approve_all" and \
        "result('approve_drivers').approved == 14" in by["approve_drivers"].expect, by["approve_drivers"]
    chats = [s for s in steps if s.do == "chat"]
    assert len(chats) >= 3 and all(s.caption for s in chats), [s.id for s in chats]
    assert {s.role.split(":")[0] for s in chats} == {"director", "commentator"}, [s.role for s in chats]


def t_the_full_scenarios_driver_page_seats_drive_with_an_approved_code():
    steps = _full().steps
    field = [s for s in steps if s.role == "drivers" and s.do == "register"][0]
    skip = {(seat["entry"], seat["stint"]) for seat in field.args.get("skip", [])}
    assert {st for _, st in skip} == {1, 2} and len(skip) == 2, \
        f"one page seat before the race, one later stint's shown once: {skip}"
    assert field.args.get("now") == [1], "only the stint 1 drivers register before the race"
    for stint in (2, 3):
        reg = [s for s in steps if s.role == "drivers" and s.do == "register_stint" and s.args.get("stint") == stint]
        lobby = [s for s in steps if s.role == "drivers" and s.do in ("lobby", "next_lobby") and s.args.get("stint") == stint]
        assert len(reg) == 1 and lobby and reg[0].index < lobby[0].index, f"stint {stint} registers before its lobby"
        assert [s for s in steps if reg[0].index < s.index < lobby[0].index and s.do == "approve_all"], \
            f"Race Control approves the stint {stint} drivers before their lobby"
    page = [s for s in steps if s.role == "driver" and s.do == "register"]
    names = [s.args["name"] for s in page]
    assert len(set(names)) == len(names), f"the desk finds a request by its name: {names}"
    decided = {s.args["name"]: s for s in steps if s.do in ("correct_driver", "reject_driver")}
    checks = " ".join(e for s in steps for e in s.expect + s.soft)
    for entry, stint in sorted(skip):
        regs = [s for s in page if (s.args["entry"], s.args["stint"]) == (entry, stint)]
        assert len(regs) == 1, (f"{entry} stint {stint}: one page registration, not {len(regs)}; the page keeps "
                                "a code under the seat the form sent, so a corrected or rejected one there drives")
        reg, name = regs[0], regs[0].args["name"]
        assert name == drive.driver_name(entry, stint - 1) and name not in decided, name
        assert entry not in checks, f"{entry} is no team a check reads, so the page seat changes no expectation"
        need = [s for s in steps if s.role == "drivers" and
                (s.do == "start" if stint == 1 else s.do in ("lobby", "next_lobby") and s.args.get("stint") == stint)][0]
        approved = [s for s in steps if reg.index < s.index < need.index and s.role == "race_control" and
                    (s.do in ("approve_drivers", "approve_all") or s.do == "approve_driver" and s.args.get("name") == name)]
        assert approved, f"{name} is approved before {need.id} hands the car its code"
    for name, step in decided.items():
        reg = [s for s in page if s.args["name"] == name]
        assert len(reg) == 1 and reg[0].index < step.index, f"{step.id} decides a request the page sent"


def t_a_cockpit_timing_shot_finds_the_timing_area_open():
    # The cockpit keeps its last area across reloads, and submit_link leaves it on My stint.
    for name in sorted(f[:-5] for f in os.listdir(os.path.join(SIM, "scenarios")) if f.endswith(".toml")):
        area = {}
        for st in scenario.load(os.path.join(SIM, "scenarios", name + ".toml"), REG).steps:
            if st.role.startswith("commentator:") and st.do == "submit_link":
                area[st.role] = "stint"
            elif st.role.startswith("commentator:") and st.do == "view":
                area[st.role] = st.args.get("area")
            if st.shot and st.shot.role.startswith("commentator:") and st.shot.element == "#timingCard":
                assert area.get(st.shot.role, "timing") == "timing", \
                    f"{name}: shot {st.shot.name} of {st.shot.role} while its {area[st.shot.role]} area is open"


def t_the_probe_rehearsal_changes_drivers_twice_and_asserts_the_report():
    path = os.path.join(SIM, "scenarios", "probe-stage2.toml")
    loaded = scenario.load(path, REG)
    assert loaded.event.get("probe") is True, "the spectator needs the relay's probe ingest"
    lobbies = [s.args.get("stint") for s in loaded.steps if s.role == "drivers" and s.do == "lobby"]
    assert lobbies == [2, 2, 3], f"a change into lobby 2, its restart lobby, a change into lobby 3: {lobbies}"
    assert any(s.role == "probe" and s.do == "race_reset" for s in loaded.steps), "a block starts with a race reset"
    report = [s for s in loaded.steps if s.role == "probe" and s.do == "report"]
    checks = " ".join(report[0].expect) if report else ""
    for fact in ("changes == 6", "anchors_carried == 6", "changes_in_window == 6", "probe_senders == 1"):
        assert fact in checks, f"the report step asserts {fact}: {checks}"
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    free = [s.get("id") for s in raw["step"] if s["role"].split(":")[0] not in ("api", "obs", "stream", "relay")
            and not str(s.get("at", "")).startswith("after:")]
    assert free == [], f"without a browser these steps would not be skipped with their chain: {free}"
    assert loaded.steps[-1].do == "stop_event", "Stop event ends the relay, so it is the last step"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")

#!/usr/bin/env python3
"""The event simulation's role drivers (#1233): the party roles on a fake page, the
drivers role on a fake relay client, the selectors against the shipped pages and the
stack's helpers for them. No browser, no socket, nothing spawned.
Run: python3 tests/test_event_sim_roles.py"""
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
    def __init__(self, url, status=200, body=None):
        self.url, self.status, self.body = url, status, body

    def json(self):
        return self.body


class Expect:
    def __init__(self, page, pred, timeout):
        self.page, self.pred, self.value, self.timeout = page, pred, None, timeout or 0

    def __enter__(self):
        self.mark = len(self.page.responses)
        return self

    def __exit__(self, kind, exc, tb):
        if kind is None:
            waited = 0
            while True:
                hits = [r for r in self.page.responses[self.mark:] if self.pred(r)]
                if hits or waited >= self.timeout:
                    break
                self.page.wait_for_timeout(100)
                waited += 100
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

    def scroll_into_view_if_needed(self, timeout=None):
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
        self.handlers = {}

    def add(self, selector, **kw):
        self.els[selector] = El(**kw)
        return self.els[selector]

    def respond(self, path, status=200, body=None):
        self.responses.append(Response(BASE + path, status, body))

    def after(self, seconds, fn):
        self.later.append((self.clock.t + seconds, fn))

    def on(self, event, fn):
        self.handlers[event] = fn

    def once(self, event, fn):
        self.handlers["once:" + event] = fn

    def confirm(self):
        """The page's native confirm(): True when a handler accepted it, as Playwright
        dismisses a dialog nobody handles."""
        fn = self.handlers.pop("once:dialog", None)
        if fn is None:
            return False
        answer = []
        fn(type("Dialog", (), {"accept": lambda self: answer.append(True)})())
        return bool(answer)

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

    def expect_response(self, pred, timeout=None):
        return Expect(self, pred, timeout)

    def screenshot(self, path=None, mask=(), timeout=None, full_page=False):
        self.shots.append((path, "", [m.selector for m in mask]))


class Context:
    def __init__(self, page):
        self.page, self.closed = page, False

    def new_page(self):
        return self.page

    def close(self):
        self.closed = True


class Browser:
    """Stands in for browser.Browser: `get()` is the Playwright browser or None."""

    def __init__(self, page=None):
        self.page, self.problem = page, "" if page else "Playwright is not installed"
        self.contexts = []

    def get(self):
        return self if self.page else None

    def new_context(self, viewport=None):
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
    want = {"producer": {"open", "pick_track", "race_settings", "start_event", "stop_event", "relay_restart"},
            "director": {"open", "approve_link", "arm_feed", "arm", "next", "scene", "red_flag",
                         "show_dashboard", "cue", "chat", "graphic", "pov", "hud", "mode", "start_part",
                         "end_part"},
            "race_control": {"open", "approve_driver", "approve_drivers", "final_lap", "finish", "red_flag",
                             "release", "retire", "rejoin", "estimate", "add_lap", "set_time", "correction",
                             "note"},
            "commentator": {"open", "submit_link", "chat"},
            "viewer": {"open", "read"},
            "drivers": {"register", "start", "pit", "box", "lobby", "release", "drop", "rejoin", "stop"}}
    for kind, actions in want.items():
        assert kind in REG, f"role {kind} is not registered"
        assert set(REG[kind].action_names()) == actions, f"{kind}: {sorted(REG[kind].action_names())}"


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
    assert page.shots == [("desk.png", "", ["#driverCard .mono.grow"])], page.shots
    cc, page, _ = producer()
    cc.do("open", {})
    cc.shot("cc.png")
    assert page.shots == [("cc.png", "", ["#evt-invite-text", "#evt-timing-text"])], \
        "the Control Center shows both links in simulation"


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
def producer(job="done", log="relay: up", stale=None):
    """A Control Center whose job hides the chip when it starts and shows `job` a second later."""
    role, page, ctx = make(party.ProducerRole, event={"track": "6e378b"})
    page.add("#jobchip", text=stale or "", visible=stale is not None)
    page.ops = []

    def confirm():
        last = page.clicks[-2]
        op = "event-start" if "Start event" in last else "relay-restart" if "relay-restart" in last else "event-stop"
        page.ops.append(op)
        page.respond("/api/op/" + op, body={"ok": True, "job_id": len(page.ops)})
        page.after(0.2, lambda: page.add("#jobchip", visible=False))
        page.after(1.2, lambda: page.add("#jobchip", text=job))
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
    page.els["#cmodal-ok"].on_click = lambda: page.respond("/api/op/event-start")
    fails(lambda: role.do("start_event", {}), "the job starting")
    role, page, _ = producer()
    page.els["#cmodal-ok"].on_click = lambda: page.after(0.2, lambda: page.add("#jobchip", text="done"))
    fails(lambda: role.do("start_event", {}), "sent no request to /api/op/event-start")


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


def t_arm_refuses_a_button_that_would_stop_the_feed():
    role, page, _ = make(party.DirectorRole)
    arm = page.add("#armBtn", text="FEED B SERVING · STOP", attrs={"data-feed": "B", "data-on": "0", "class": "k ready"})
    fails(lambda: role.do("arm", {}), "offers no arming", "SERVING")
    assert "#armBtn" not in page.clicks
    arm.text, arm.attrs = "ARM → STINT 2 (FEED B)", {"data-feed": "B", "data-on": "1", "class": "k armkey"}

    def click():
        page.respond("/feed/B/activate")
        arm.attrs["class"] = "k armkey starting"
    arm.on_click = click
    assert role.do("arm", {}) == {"feed": "B", "label": "ARM → STINT 2 (FEED B)"}


def t_arm_feed_and_scene_name_their_key():
    role, page, _ = make(party.DirectorRole)
    fails(lambda: role.do("arm_feed", {"feed": "C"}), "'A' or 'B'")
    page.add('.navbtn[data-area="fault"]')
    page.add("#stA", text="A S1 STOP")

    def arm():
        page.respond("/feed/A/activate")
        page.els["#stA"].text = "A S1 IDLE"
    page.add('#feedQuality .armbtn[data-feed="A"][data-on="1"]', on_click=arm)
    assert role.do("arm_feed", {"feed": "a"}) == {"feed": "A", "state": "A S1 IDLE"}
    fails(lambda: role.do("scene", {"scene": "Nowhere"}), "0 matching scene keys")
    key = '#pgmBus button.k:has-text("STINT")'

    def cut():
        page.respond("/obs/scene")
        page.els[key].attrs["class"] = "k air"
    page.add(key, attrs={"class": "k"}, on_click=cut)
    assert role.do("scene", {"scene": "Stint"}) == {"scene": "Stint"}


def t_show_dashboard_opens_the_desk_and_reads_the_button():
    role, page, _ = make(party.DirectorRole)
    page.add('.navbtn[data-area="setup"]')
    button = '#driverBox button[data-label="Show dashboard"]'
    page.add(button, text="Show dashboard", visible=False)
    page.add("#driverBox > summary", on_click=lambda: setattr(page.els[button], "visible", True))

    def toggle():
        page.respond("/drivers/race/settings")
        page.els[button].text = "Hide dashboard"
    page.els[button].on_click = toggle
    assert role.do("show_dashboard", {}) == {"on": True}
    assert "#driverBox > summary" in page.clicks, "a closed desk is opened first"
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


# -- race control ---------------------------------------------------------------------
def desk():
    role, page, ctx = make(party.RaceControlRole)
    page.add('#driverCard .msg[role="status"]', text="", attrs={"class": "msg"})
    return role, page, ctx


def desk_button(page, label, route, says, cls="msg", armed=True):
    """A desk button; an armed one acts on its second click only."""
    key = f'#driverCard button[data-label="{label}"]'
    el = page.add(key, text=label)

    def click():
        if armed and el.text == label:
            el.text = "Confirm " + label
            return
        el.text = label
        page.respond("/console/race-control" + route)
        status = page.els['#driverCard .msg[role="status"]']
        status.text, status.attrs["class"] = says, cls
    el.on_click = click
    return key


def t_an_armed_desk_button_is_confirmed_with_a_second_click():
    role, page, _ = desk()
    key = desk_button(page, "Final lap", "/drivers/race/final-lap", "Final lap")
    assert role.do("final_lap", {}) == {"status": "Final lap"}
    assert page.clicks == [key, key], "arm, then confirm"
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
    desk_button(page, "Final lap", "/drivers/race/final-lap", "Something else")
    fails(lambda: role.do("final_lap", {}), "Something else", "not 'Final lap'")


def t_the_desks_line_of_the_action_before_is_not_this_actions_result():
    role, page, _ = desk()
    status = page.els['#driverCard .msg[role="status"]']
    status.text, status.attrs["class"] = "RED FLAG held the race; banner: missing", "msg err"
    key = '#driverCard button[data-label="Final lap"]'
    el = page.add(key, text="Final lap")

    def click():
        if el.text == "Final lap":
            el.text = "Confirm Final lap"
            return
        el.text = "Final lap"
        page.respond("/console/race-control/drivers/race/final-lap")
        page.after(0.3, lambda: (setattr(status, "text", "Final lap"), status.attrs.update({"class": "msg"})))
    el.on_click = click
    assert role.do("final_lap", {}) == {"status": "Final lap"}, "the old error line is not read as the result"
    start = page.clock.t
    assert role.do("final_lap", {}) == {"status": "Final lap"}, "the same line twice is accepted once it stays"
    assert page.clock.t - start >= party.STALE_S


def t_desk_forms_pick_the_entry_before_the_button():
    role, page, _ = desk()
    page.add('#driverCard select[aria-label="Entry to retire"]', options=["A #1", "B #2"])
    desk_button(page, "Retire (DNF)", "/drivers/race/retire", "Retired: B #2")
    assert role.do("retire", {"entry": "B #2"}) == {"status": "Retired: B #2"}
    assert page.els['#driverCard select[aria-label="Entry to retire"]'].value == "B #2"
    fails(lambda: role.do("retire", {"entry": "Z #9"}), "Entry to retire", "no option")
    page.add('#driverCard select[aria-label="Team to rejoin"]', options=["A #1"])
    desk_button(page, "Mark rejoin", "/drivers/race/rejoin", "Rejoin marked", armed=False)
    assert role.do("rejoin", {"entry": "A #1"}) == {"status": "Rejoin marked"}
    for label, opts in (("Quiet entry to estimate", ["A #1"]), ("Reference entry", ["B #2"]),
                        ("Behind or ahead of the reference", ["behind", "ahead of"])):
        page.add(f'#driverCard select[aria-label="{label}"]', options=opts)
    page.add('#driverCard input[aria-label="Seconds from the reference"]')
    desk_button(page, "Set estimate", "/drivers/race/estimate", "Estimate set", armed=False)
    role.do("estimate", {"entry": "A #1", "ref": "B #2", "seconds": 6, "ahead": True})
    assert page.els['#driverCard select[aria-label="Behind or ahead of the reference"]'].value == "ahead of"
    assert page.els['#driverCard input[aria-label="Seconds from the reference"]'].value == "6"
    page.add('#driverCard select[aria-label="Qualifying entry"]', options=["A #1"])
    page.add('#driverCard input[aria-label="Qualifying time"]')
    desk_button(page, "Add lap", "/drivers/race/quali-lap", "Qualifying lap added", armed=False)
    assert role.do("add_lap", {"entry": "A #1", "time": "1:45.312"}) == {"status": "Qualifying lap added"}


def t_corrections_times_and_notes_go_through_their_forms():
    role, page, _ = desk()
    page.add('#driverCard select[aria-label="Entry to correct"]', options=["A #1"])
    for label in ("Distance correction in metres", "Pit stop correction", "Reason"):
        page.add(f'#driverCard input[aria-label="{label}"]')
    desk_button(page, "Apply correction", "/drivers/race/correct", "Correction applied", armed=False)
    assert role.do("correction", {"entry": "A #1", "pits": 1, "reason": "missed stop"}) == {"status": "Correction applied"}
    assert page.els['#driverCard input[aria-label="Pit stop correction"]'].value == "1"
    assert page.els['#driverCard input[aria-label="Distance correction in metres"]'].value == "", \
        "a value nobody asked for is left alone"
    assert page.els['#driverCard input[aria-label="Reason"]'].value == "missed stop"
    page.add('#driverCard select[aria-label="Qualifying entry"]', options=["A #1"])
    page.add('#driverCard input[aria-label="Qualifying time"]')
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


def waiting_desk(names):
    role, page, ctx = desk()
    left = list(names)
    page.entries = {}

    def render():
        page.add("#driverCard .req.waiting", count=len(left))
        page.add("#driverCard .req.waiting strong", text=left[0] if left else "", count=len(left))
        for name in set(names):
            n = left.count(name)
            row = f'#driverCard .req.waiting:has(strong[title="{name}"])'
            page.add(row, count=n)

            def approve(name=name):
                page.respond("/console/race-control/drivers/requests/approve")
                page.entries[name] = page.els[f'#driverCard .req.waiting select[aria-label="Entry of {name}"]'].value
                left.remove(name)
                render()
            page.add(row + ' button[data-label="Approve"]', count=n, on_click=approve)
            page.add(f'#driverCard .req.waiting select[aria-label="Entry of {name}"]', count=n,
                     options=["A #1", "B #2"])
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


# -- commentator and viewer -------------------------------------------------------------
def t_a_commentator_submits_a_link_and_gets_the_submission_id():
    role, page, _ = make(party.CommentatorRole, "commentator:Comms 2")
    page.els["#tally"].text, page.els["#tally"].attrs = "UP NEXT · Stint 2", {"class": "tally next"}
    page.add("#subStint", options=["Stint 2"])
    page.add("#subUrl")
    page.add("#subMsg", text="")
    page.add("#subPending", text="")

    def submit():
        page.respond("/cockpit/submit", body={"ok": True, "id": 7})
        page.els["#subMsg"].text = "Submitted ✓ — see status below."
        page.els["#subPending"].text = "Stint 2 — awaiting director approval"
    page.add("#subBtn", on_click=submit)
    assert role.do("submit_link", {"url": "sim://comms2", "stint": 2}) == {"id": 7, "stint": "Stint 2"}
    assert page.els["#subUrl"].value == "sim://comms2"
    facts = role.page()
    assert facts["tally"] == "UP NEXT · Stint 2" and facts["on_air"] is False and "awaiting" in facts["pending"]
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
    assert out == {"registered": 6, "entries": drive.roster(3), "stints": 2}, out
    assert "code" not in json.dumps(out) and "INVITE" not in json.dumps(out), "codes and invite stay inside"
    relay = Relay.made[-1]
    assert relay.registered[0][:4] == ("INVITE", drive.roster(3)[0], "Driver 11-1", "Stint 1")
    assert relay.registered[1][2:4] == ("Driver 11-2", "Stint 2")
    devices = [r[4] for r in relay.registered]
    assert len(set(devices)) == 6 and all(d.startswith("198.51.100.") for d in devices), devices
    assert role.page()["registered"] == 6 and role.page()["sending"] == 3
    fails(lambda: role.do("register", {}), "registered already")
    assert "code" not in json.dumps(role.page())


def t_drivers_need_the_invite_and_a_track():
    fails(lambda: drivers_role(links=False).do("register", {}), "no invite link yet")
    fails(lambda: drivers_role(event={"seed": 1}).do("register", {}), "[event] track")
    fails(lambda: drivers_role().do("start", {}), "not registered yet")


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
    fails(lambda: role.do("box", {"order": 5}), "'race', 'grid' or a list")
    fails(lambda: role.do("lobby", {"stint": 0}), "counted from 1")
    runner.run(80.0)
    assert role.page()["standing"] == 2, role.page()["cars"]
    role.do("lobby", {"stint": 2})
    role.do("rejoin", {"entry": 2, "seconds": 2})
    role.do("release", {})
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
    assert [row[0] for row in stack.sheet_tabs(scn({"stints": 3}))["Configuration"][1:]] == ["", "", ""]
    try:
        stack.sheet_tabs(scn({"cars": 99}))
    except ValueError as exc:
        assert "1-15" in str(exc)
    else:
        raise AssertionError("more cars than the grid holds must be refused")


def t_parts_seed_the_producer_and_channel_tabs():
    assert "Producer" not in stack.sheet_tabs(scn({})), "no parts, no Part control on the panel"
    tabs = stack.sheet_tabs(scn({"parts": 2, "qualifying_part": True}))
    assert [row[0] for row in tabs["Producer"][1:]] == ["1", "2", "Q"], tabs["Producer"]
    assert all(row[3] == stack.PART_KEY_REF.format(row[0]) and row[1] and row[2] for row in tabs["Producer"][1:]), \
        "each Part names a producer, a host and a stream key reference"
    assert tabs["Channel"][1][0] == "youtube", "starting a Part needs the event's platform"
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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")

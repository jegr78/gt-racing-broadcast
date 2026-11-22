"""Built-in roles that need no person's page: plain HTTP, the simulated OBS operator, the
stream channels, and a generic browser page. The party roles of #1233 build on these."""
import http.client
import pathlib
import re
from urllib.parse import quote, urlencode, urlsplit

import expect
import probes
import scenario
from roles import Role, ShotUnavailable, StepError, register

PAGE_TIMEOUT_MS = 15000
LOAD_FAILED = "Failed to load resource"
_TOKEN = re.compile(r"([?&]t=)[^&#\s)]+")


def redact(text):
    """`text` without console tokens, which stay in the run's temporary directory."""
    return _TOKEN.sub(r"\1REDACTED", str(text))


def _ok(status, expected):
    return status == expected if expected else 200 <= status < 300


@register
class ApiRole(Role):
    """HTTP against a service: args `service` (relay, ui, obs, sheet, sink; default
    relay), `path`, optional `as` (a crew name: adds its console token as `t=`),
    `json` (POST body) and `status` (the expected code, default any 2xx)."""
    kind = "api"

    def _call(self, method, args):
        path = args.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            raise StepError("args.path must start with '/'")
        if args.get("as"):
            sep = "&" if "?" in path else "?"
            path += sep + urlencode({"t": self.ctx.token(str(args["as"]))})
        base = self.ctx.url(args.get("service", "relay"))
        try:
            status, data = probes.http_json(base, path, method, args.get("json") if method == "POST" else None,
                                            timeout=float(args.get("timeout", 10)))
        except (OSError, http.client.HTTPException) as exc:
            raise StepError(f"{method} {args['path']}: {exc}") from None
        if not _ok(status, args.get("status")):
            raise StepError(f"{method} {args['path']}: HTTP {status} {expect.short(data)}")
        return data if isinstance(data, dict) else {"data": data}

    def act_get(self, args):
        return self._call("GET", args)

    def act_post(self, args):
        return self._call("POST", args)


@register
class ObsRole(Role):
    """An operator acting in the simulated OBS. `request` sends any request
    (`type`, `data`); `scene` switches the program scene. Its screenshot is the program."""
    kind = "obs"

    def _sim(self):
        if self.ctx.obs is None:
            raise StepError("this run has no simulated OBS")
        return self.ctx.obs

    def act_request(self, args):
        import obs_state
        try:
            return self._sim().state.request(str(args.get("type")), dict(args.get("data") or {}),
                                             origin="http") or {}
        except obs_state.ObsError as exc:
            raise StepError(f"OBS {args.get('type')}: code {exc.code} {exc.comment}") from None

    def act_scene(self, args):
        return self.act_request({"type": "SetCurrentProgramScene",
                                 "data": {"sceneName": str(args.get("scene"))}})

    def page(self):
        return self._sim().state.snapshot()

    def shot(self, path, element=""):
        state = self._sim().state
        image, fmt = state.screenshot(state.snapshot()["scene"], 1280)
        if fmt != "png":
            raise ShotUnavailable(f"OBS answered a {fmt} picture, not png")
        pathlib.Path(path).write_bytes(image)


@register
class StreamRole(Role):
    """The simulated stream channels: `channel` (name, stint, commentator, status),
    `cut`, `stall`/`freeze` (name, optional `for` duration) and `clear`."""
    kind = "stream"

    def _stub(self):
        if self.ctx.stub is None:
            raise StepError("this run has no stream stub")
        return self.ctx.stub

    def act_channel(self, args):
        try:
            return self._stub().channel(str(args.get("name")), stint=args.get("stint"),
                                        commentator=args.get("commentator"), status=args.get("status"))
        except ValueError as exc:
            raise StepError(str(exc)) from None

    def _window(self, args):
        if "for" not in args:
            return None
        return self._stub().clock() + scenario.parse_duration(args["for"])

    def act_cut(self, args):
        self._stub().cut(str(args.get("name")))

    def act_stall(self, args):
        self._stub().stall(str(args.get("name")), until=self._window(args))

    def act_freeze(self, args):
        self._stub().freeze(str(args.get("name")), until=self._window(args))

    def act_clear(self, args):
        self._stub().clear(str(args.get("name")))


@register
class PageRole(Role):
    """A page in its own browser context: `open` (service, path, optional `as` for a
    console token, `wait_for` a selector). A script error on the page is a hard finding
    when the role closes, a failed resource load a soft one. Without Playwright, `open`
    records a soft finding."""
    kind = "page"
    viewport = {"width": 1440, "height": 900}

    def open(self):
        self.context = self.pg = None
        self.errors = []

    def act_open(self, args):
        browser = self.ctx.browser()
        if browser is None:
            self.ctx.finding("soft", f"page not opened: {self.ctx.browser_problem()}")
            return None
        if self.context is None:
            self.context = browser.new_context(viewport=dict(self.viewport))
            self.pg = self.context.new_page()
            self.pg.on("console", self._console)
            self.pg.on("pageerror", lambda e: self.errors.append(redact(e)))
        path = args.get("path", "/")
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") \
                or any(c in path for c in "\\\r\n\t"):
            raise StepError(f"args.path must be a path on the service starting with '/', not {path!r}")
        if args.get("as"):
            path += ("&" if "?" in path else "?") + "t=" + quote(self.ctx.token(str(args["as"])))
        base = self.ctx.url(args.get("service", "relay"))
        if urlsplit(base).hostname not in ("127.0.0.1", "localhost"):
            raise StepError(f"pages open on loopback only, not {base}")
        resp = self.pg.goto(base + path, timeout=PAGE_TIMEOUT_MS)
        if resp is not None and resp.status >= 400:
            raise StepError(f"open {redact(path)}: HTTP {resp.status}")
        if args.get("wait_for"):
            try:
                self.pg.wait_for_selector(str(args["wait_for"]), state="visible", timeout=PAGE_TIMEOUT_MS)
            except Exception as exc:
                raise StepError(f"{args['wait_for']} not visible: {redact(exc)}") from None
        return {"url": redact(self.pg.url), "title": self.pg.title()}

    def _console(self, msg):
        if msg.type == "error":
            where = (msg.location or {}).get("url") or ""
            self.errors.append(redact(f"{msg.text} ({where})" if where else msg.text))

    def page(self):
        if self.pg is None:
            return {"open": False, "console_errors": list(self.errors)}
        return {"open": True, "url": redact(self.pg.url), "title": self.pg.title(),
                "console_errors": list(self.errors)}

    def shot(self, path, element=""):
        if self.pg is None:
            raise ShotUnavailable(f"role {self.name!r} has no open page")
        if element:
            self.pg.locator(element).first.screenshot(path=path, timeout=PAGE_TIMEOUT_MS)
        else:
            self.pg.screenshot(path=path, full_page=False)

    def close(self):
        # A failed image or fetch (an idle feed's preview answers 503) is the browser's
        # network log, not a script error.
        out = [("soft" if e.startswith(LOAD_FAILED) else "hard", f"console error: {e}") for e in self.errors]
        if self.context is not None:
            self.context.close()
        return out

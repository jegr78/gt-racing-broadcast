"""Built-in roles that need no person's page: plain HTTP, the simulated OBS operator, the
stream channels, and a generic browser page. The party roles of #1233 build on these."""
import http.client
import pathlib
import re
from urllib.parse import quote, urlencode, urlsplit

import expect
import probes
import redaction
import scenario
from roles import Role, ShotUnavailable, StepError, Unavailable, register, selectors

PAGE_TIMEOUT_MS = 15000
LOAD_FAILED = "Failed to load resource"
CONNECTION = "net::ERR_CONNECTION_"
_QUERY = re.compile(r"\?[^\s)]*")
redact = redaction.redact


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
class RelayRole(Role):
    """The producer's machine: `kill` ends the relay as a crash would, without `relay
    stop`, so nothing is cleaned up and the event stays unstopped."""
    kind = "relay"

    def act_kill(self, args):
        return {"pid": self.ctx.kill_relay()}


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


# Each visible control under a root: its label, how many px the viewport or a clipping
# ancestor cuts off it (`cut`: 0 means it works without scrolling) and how many px an
# ancestor that cannot scroll cuts off it (`clip`: 0 means scrolling reaches it).
FIT_SCRIPT = """(root) => {
  const box = document.querySelector(root);
  if (!box) return null;
  const out = [];
  for (const el of box.querySelectorAll('button, select, input, textarea, a.btn')) {
    const r = el.getBoundingClientRect();
    if (el.offsetParent === null || !r.width || !r.height) continue;
    let top = 0, bottom = window.innerHeight, ctop = -Infinity, cbottom = Infinity;
    for (let p = el.parentElement; p; p = p.parentElement) {
      const o = getComputedStyle(p).overflowY;
      if (o === 'visible') continue;
      const b = p.getBoundingClientRect();
      top = Math.max(top, b.top); bottom = Math.min(bottom, b.bottom);
      if (o === 'hidden' || o === 'clip') { ctop = Math.max(ctop, b.top); cbottom = Math.min(cbottom, b.bottom); }
    }
    const name = el.dataset.label || el.getAttribute('aria-label') || el.textContent || el.id || el.tagName;
    out.push({label: name.trim().replace(/\\s+/g, ' ').slice(0, 40),
              cut: Math.max(0, Math.ceil(r.bottom - bottom), Math.ceil(top - r.top)),
              clip: Math.max(0, Math.ceil(r.bottom - cbottom), Math.ceil(ctop - r.top))});
  }
  return out;
}"""
# Sub-pixel layout: a control 1 px past its box still shows and takes clicks.
FIT_TOLERANCE_PX = 1


@register
class PageRole(Role):
    """A page in its own browser context: `open` (service, path, optional `as` for a
    console token, `wait_for` a selector, `viewport` [width, height] on the first open). A script error on the page is a hard finding
    when the role closes, a failed resource load a soft one. Without Playwright, `open`
    is skipped. A screenshot blacks out the invite and timing links of any page."""
    kind = "page"
    viewport = {"width": 1440, "height": 900}

    def open(self):
        self.context = self.pg = self.cdp = None
        self.errors = []

    def act_open(self, args):
        browser = self.ctx.browser()
        if browser is None:
            raise Unavailable(f"a page needs a browser: {self.ctx.browser_problem()}")
        if self.context is None:
            size = args.get("viewport")
            if size is not None:
                if not (isinstance(size, list) and len(size) == 2 and all(type(v) is int and v > 0 for v in size)):
                    raise StepError(f"open: viewport is [width, height], not {size!r}")
                self.viewport = {"width": size[0], "height": size[1]}
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
        try:
            resp = self.pg.goto(base + path, timeout=PAGE_TIMEOUT_MS)
        except Exception as exc:  # Playwright's text names the whole URL, token included
            why = redact(str(exc).splitlines()[0] if str(exc) else type(exc).__name__, self.ctx.secrets)
            raise StepError(f"open {redact(path, self.ctx.secrets)}: {why}") from None
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
                "console_errors": list(self.errors), **self._memory()}

    def _memory(self):
        """The page's exact JS heap in MB, DOM nodes and event listeners from Chromium's
        Performance metrics; None for what the browser cannot tell."""
        try:
            if self.cdp is None:
                self.cdp = self.context.new_cdp_session(self.pg)
                self.cdp.send("Performance.enable")
            got = {m["name"]: m["value"] for m in self.cdp.send("Performance.getMetrics")["metrics"]}
        except Exception:  # noqa: BLE001  a crashed page or another browser has no metrics
            return {"heap_mb": None, "dom_nodes": None, "listeners": None}
        heap = got.get("JSHeapUsedSize")
        return {"heap_mb": round(heap / 1048576, 1) if isinstance(heap, (int, float)) else None,
                "dom_nodes": got.get("Nodes"), "listeners": got.get("JSEventListeners")}

    def shot(self, path, element=""):
        if self.pg is None:
            raise ShotUnavailable(f"role {self.name!r} has no open page")
        target = self.pg.locator(element).first if element else self.pg
        target.screenshot(path=path, mask=[self.pg.locator(s) for s in selectors.LINKS], timeout=PAGE_TIMEOUT_MS)

    def fit_report(self, root):
        """FIT_SCRIPT's measure of the visible controls under `root`; None without `root`."""
        return self.pg.evaluate(FIT_SCRIPT, root)

    def close(self):
        # A failed image or fetch (an idle feed's preview answers 503) is the browser's
        # network log, not a script error. A polled resource fails once per poll, and
        # every poll fails once its service stopped.
        counts, gone = {}, 0
        for e in self.errors:
            if e.startswith(LOAD_FAILED) and CONNECTION in e:
                gone += 1
                continue
            key = _QUERY.sub("", e)
            counts[key] = counts.get(key, 0) + 1
        out = [("soft" if e.startswith(LOAD_FAILED) else "hard",
                f"console error: {e}" + (f" ({n}x)" if n > 1 else "")) for e, n in counts.items()]
        if gone:
            out.append(("soft", f"{gone} requests of the page found its service stopped"))
        if self.context is not None:
            self.context.close()
        return out

"""The named roots of `expect` checks: relay, obs, sheet, webhook, race (`page(role)` is
the engine's). Each read is fresh; a service that does not answer is `Unresolved`."""
import ast
import http.client
import json
from urllib.parse import urlsplit

import expect

Probe, Unresolved, View = expect.Probe, expect.Unresolved, expect.View

TIMEOUT_S = 3.0
# The relay GETs that only read; many other relay GETs are controls (`/next`, `/setup/set/...`).
RELAY_READS = frozenset({
    "/status", "/drivers/race", "/drivers/race/recording", "/drivers/data", "/setup/data", "/hud/data",
    "/splitscreen/data", "/intermission/data", "/timer/data", "/chat/data", "/cues/data",
    "/schedule/data", "/crew/data", "/qualifying/data", "/event-notes/data"})


def relay_read(path):
    """`path` when it is one of RELAY_READS, else ValueError."""
    if not isinstance(path, str) or path not in RELAY_READS:
        raise ValueError(f"relay.get reads only {', '.join(sorted(RELAY_READS))}, not {path!r}")
    return path


def check_relay_reads(tree):
    """ValueError unless every `relay.get(...)` in a compiled check names one of RELAY_READS
    as a literal."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" \
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "relay":
            if len(node.args) != 1 or not isinstance(node.args[0], ast.Constant):
                raise ValueError("relay.get takes one path written as a string")
            relay_read(node.args[0].value)


def http_json(base, path, method="GET", body=None, timeout=TIMEOUT_S, headers=None):
    """(status, parsed JSON or {"body": text}) from a loopback service."""
    u = urlsplit(base)
    if u.scheme != "http" or u.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError(f"only loopback http services, not {base!r}")
    data = None if body is None else json.dumps(body).encode()
    head = {"Content-Type": "application/json"} if data is not None else {}
    head.update(headers or {})
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=timeout)
    try:
        conn.request(method, path, body=data, headers=head)
        resp = conn.getresponse()
        raw = resp.read()
        status = resp.status
    finally:
        conn.close()
    try:
        return status, json.loads(raw) if raw else {}
    except ValueError:
        return status, {"body": raw.decode("utf-8", "replace")[:2000]}


def _get(base, path):
    try:
        status, data = http_json(base, path)
    except (OSError, http.client.HTTPException) as exc:
        raise Unresolved(f"GET {path}: {exc}") from None
    if status != 200:
        raise Unresolved(f"GET {path}: HTTP {status}")
    return data


class RelayProbe(Probe):
    """`relay.status` (GET /status), `relay.up`, `relay.port`, `relay.get('/path')`. A check
    may poll many times, so `get` reads only RELAY_READS and refuses any other path
    before it sends a request."""
    fields = ("status", "up", "port")
    calls = ("get",)

    def __init__(self, base):
        self.base = base

    @property
    def status(self):
        return View(_get(self.base, "/status"))

    @property
    def port(self):
        return urlsplit(self.base).port

    @property
    def up(self):
        try:
            _get(self.base, "/status")
            return True
        except Unresolved:
            return False

    def get(self, path):
        try:
            return _get(self.base, relay_read(path))
        except ValueError as exc:
            raise Unresolved(str(exc)) from None


class RaceProbe(Probe):
    """The relay's driver race (GET /drivers/race): `race.state`, `race.lap`, `race.entries`."""

    def __init__(self, base):
        self.base = base

    def resolve(self, name):
        return View(_get(self.base, "/drivers/race")).resolve(name)


class SheetProbe(Probe):
    """`sheet.tab('Schedule')` or `sheet.schedule`: the tab's records (dicts by header,
    index 0 is the first row under the header); `sheet.writes` the script calls,
    `sheet.write_count('schedule_set')` those of one action."""
    fields = ("writes",)
    calls = ("tab", "write_count")

    def __init__(self, sim):
        self.sim = sim

    def tab(self, name):
        for tab in self.sim.sheet.tab_names():
            if tab.lower() == str(name).lower().replace("_", " "):
                return self.sim.records(tab)
        raise Unresolved(f"no tab {name!r}")

    @property
    def writes(self):
        return View(self.sim.writes())

    def write_count(self, action=None):
        return sum(1 for w in self.sim.writes() if action is None or w.get("action") == action)

    def resolve(self, name):
        if name in self.fields or name in self.calls:
            return getattr(self, name)
        return View(self.tab(name))


class WebhookProbe(Probe):
    """`webhook.count('text')` (messages whose text contains it, without case),
    `webhook.messages`, `webhook.rejected`."""
    fields = ("messages", "rejected")
    calls = ("count",)

    def __init__(self, sink):
        self.sink = sink

    @property
    def messages(self):
        return View(self.sink.messages())

    @property
    def rejected(self):
        return self.sink.snapshot().get("rejected", 0)

    def count(self, needle=None):
        return self.sink.count(needle)


class ObsProbe(Probe):
    """`obs.scene`, `obs.preview`, `obs.streaming`, `obs.recording`, `obs.state` (the
    whole snapshot), `obs.program_feeds` (each visible feed on program with the
    channel and stint its picture carries), `obs.program_channels` (their names)."""
    fields = ("scene", "preview", "streaming", "recording", "state", "program_feeds",
              "program_channels")

    def __init__(self, sim, stub=None, decode=None):
        self.sim = sim
        self.stub = stub
        self.decode = decode

    @property
    def state(self):
        return View(self.sim.state.snapshot())

    @property
    def scene(self):
        return self.sim.state.snapshot()["scene"]

    @property
    def preview(self):
        return self.sim.state.snapshot()["preview"]

    @property
    def streaming(self):
        return self.sim.state.snapshot()["stream"]["active"]

    @property
    def recording(self):
        return self.sim.state.snapshot()["record"]["active"]

    def _identity(self, url):
        grabber = self.sim.state.grabber
        if grabber is None or self.decode is None:
            return None
        frame = grabber.frames([url]).get(url)
        return self.decode(*frame) if frame else None

    @property
    def program_feeds(self):
        snap = self.sim.state.snapshot()
        codes = {}
        if self.stub is not None:
            codes = {c.get("code"): name for name, c in self.stub.channels().items()}
        out = []
        for feed in snap["program"]["feeds"]:
            url = feed.get("url") or ""
            row = {"input": feed["input"], "url": url, "occluded": feed["occluded"],
                   "channel": None, "stint": None}
            if url and not feed["occluded"]:
                ident = self._identity(url)
                if ident:
                    row["channel"], row["stint"] = codes.get(ident[0]), ident[1]
            out.append(row)
        return View(out)

    @property
    def program_channels(self):
        return [f["channel"] for f in expect.raw(self.program_feeds) if f["channel"]]

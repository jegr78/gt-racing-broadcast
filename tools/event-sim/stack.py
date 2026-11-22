"""Stand up and tear down everything a scenario runs against.

The fakes run in this process (simulated Sheet, webhook sink, stream stub, OBS); the
relay and the Control Center run from a copy of `src/` and the demo profile in a
temporary app home, so the relay.mode stamp, the auto-provisioned console secret and
every runtime file stay out of the checkout. Each child runs under
`tools/e2e_child.py`, which stops its process tree when this process closes the pipe
or dies. Every port is a free loopback port; 8088, 8089 and 53001-53003 are never used."""
import http.client
import json
import os
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import time
import zlib
from urllib.parse import urlsplit, urlunsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
FAKES = os.path.join(HERE, "fakes")
for _p in (FAKES, os.path.join(ROOT, "src", "scripts"), os.path.join(ROOT, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import console_auth  # noqa: E402
import drive  # noqa: E402
import e2e_safety  # noqa: E402
import obs_grab  # noqa: E402
import obs_sim  # noqa: E402
import ports  # noqa: E402
import probes  # noqa: E402
import simulation  # noqa: E402
import sheet_sim  # noqa: E402
import stream_stub  # noqa: E402
import webhook_sink  # noqa: E402

PROFILE = "demo"
RESERVED_PORTS = frozenset({8088, 8089, 53001, 53002, 53003})
COLLECTION = os.path.join(ROOT, "src", "obs", "GT_Racing_Endurance.json")
CHILD = os.path.join(ROOT, "tools", "e2e_child.py")
TEARDOWN_MARGIN_S = 600           # start, teardown and the report around the steps
READY_TIMEOUT_S = 90
LOG_TAIL = 4000
PART_KEY_REF = "sim-part-{}"     # a Script Property name; its value is made up per run
RACE_CONTROL = ("GREEN", "Red Flag - Race Suspended")    # the second is the RED FLAG banner
EVENT_FILES = {"invite": "driver-invite.json", "timing": "timing-link.json"}


def _any_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def lifetime(scn):
    """Seconds the services and the containment unit may live: the run plus a margin."""
    return e2e_safety.lifetime(int(scn.run_seconds) + TEARDOWN_MARGIN_S)


def port_map(feeds, control):
    """The live relay's ports -> this run's: the three feeds and the control port, so the
    collection's feed inputs and browser sources reach the simulated relay."""
    return {53001: feeds[0], 53002: feeds[1], 53003: feeds[2], 8088: control}


def port_env(feeds, control, ui):
    """The variables that keep every relay start of the app home, the Control Center's
    Restart included, on this run's ports."""
    return {"RACECAST_UI_PORT": str(ui), "RACECAST_SIM_RELAY_PORT": str(control),
            "RACECAST_SIM_FEED_PORTS": ",".join(str(p) for p in feeds)}


def stamp_relay(home, pid):
    """Write the PID file, the profile and mode stamps and the start marker `relay start`
    would, so the Control Center in this app home sees the relay that `relay run` started."""
    runtime = os.path.join(home, "runtime")
    os.makedirs(runtime, exist_ok=True)
    marker = simulation.process_started(pid)
    stamps = [("relay.pid", str(pid)), ("relay.profile", PROFILE), ("relay.mode", "simulation")]
    for name, text in stamps + ([("relay.started", marker)] if marker else []):
        with open(os.path.join(runtime, name), "w", encoding="utf-8") as fh:
            fh.write(text)


def free_port(taken=(), pick=_any_port):
    while True:
        port = pick()
        if port not in RESERVED_PORTS and port not in taken:
            return port


def rewrite_feed_urls(collection, ports):
    """A copy of `collection` whose loopback http URLs on an old port (key of `ports`)
    point at the new one, so the simulated OBS reads the relay's free feed ports."""
    def fix(value):
        if isinstance(value, dict):
            return {k: fix(v) for k, v in value.items()}
        if isinstance(value, list):
            return [fix(v) for v in value]
        if not isinstance(value, str):
            return value
        try:
            u = urlsplit(value)
            port = u.port
        except ValueError:
            return value
        if u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost") and port in ports:
            return urlunsplit((u.scheme, f"{u.hostname}:{ports[port]}", u.path, u.query, u.fragment))
        return value
    return fix(collection)


def sheet_tabs(scn):
    """The Sheet tabs the scenario's event implies, then its own `[sheet.tabs]`."""
    comms = sorted(scn.commentators, key=lambda c: c.stint)
    stints = int(scn.event.get("stints") or max([c.stint for c in comms] or [1]))
    names = [c.name for c in comms]
    teams = event_roster(scn.event)
    schedule = [["URL", "Streamer", "Stint"]]
    for n in range(1, stints + 1):
        c = next((c for c in comms if c.stint == n), None)
        url = f"sim://{c.channel}" if c and c.scheduled else ""
        schedule.append([url, c.name if c else "", f"Stint {n}"])
    vocab_rows = max(stints, len(names), len(teams), len(RACE_CONTROL))
    config = [["Teams", "Stints", "Streamers", "Session", "Race Control", "Flag"]]
    for i in range(vocab_rows):
        config.append([teams[i] if i < len(teams) else "",
                       f"Stint {i + 1}" if i < stints else "", names[i] if i < len(names) else "",
                       "Race" if i == 0 else "", RACE_CONTROL[i] if i < len(RACE_CONTROL) else "",
                       "Green Flag" if i == 0 else ""])
    first = comms[0].name if comms else ""
    tabs = {
        "Schedule": schedule,
        "Qualifying": [["URL", "Streamer", "Stint"]],
        "Setup": [["Stint", "Streamer", "Session", "Race Control", "Flag"],
                  ["Stint 1", first, "Race", "", ""]],
        "Configuration": config,
        "Crew": [["Name", "Commentator", "Director", "Producer", "Race Control", "Discord"],
                 ["Sim Director", "FALSE", "TRUE", "FALSE", "FALSE", ""],
                 ["Sim Race Control", "FALSE", "FALSE", "FALSE", "TRUE", ""]]
        + [[c.name, "TRUE", "FALSE", "FALSE", "FALSE", ""] for c in comms],
        "POV": [["URL", "Name"]],
    }
    parts = event_parts(scn.event)
    if parts:
        tabs["Producer"] = [["Part", "Producer", "MagicDNS", "Stream Key"]] + \
            [[label, "Sim Producer", "sim-producer.invalid", PART_KEY_REF.format(label)] for label in parts]
        tabs["Channel"] = [["Platform", "Channel"], ["youtube", "sim://broadcast"]]
    tabs.update(scn.tabs)
    return tabs


def placeholder_png():
    """A 1x1 transparent PNG: Start event refuses a machine without any broadcast graphic."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(5))) + chunk(b"IEND", b""))


def event_roster(event):
    """The team labels of `[event] cars` (none without it)."""
    cars = event.get("cars")
    return drive.roster(cars) if cars is not None else []


def event_parts(event):
    """The Producer tab's Part labels of `[event] parts`: N race Parts, plus the qualifying
    Part "Q" with `qualifying_part`. None without them: the panel then shows no Part control."""
    parts = event.get("parts", 0)
    if type(parts) is not int or not 0 <= parts <= 9:
        raise ValueError(f"event.parts is a whole number 0-9, not {parts!r}")
    return [str(n) for n in range(1, parts + 1)] + (["Q"] if event.get("qualifying_part") else [])


def event_track(event):
    """(layout id, lap length in metres) of `[event] track`, or None."""
    track = event.get("track")
    if track is None:
        return None
    if not isinstance(track, str) or not re.fullmatch(r"[0-9a-f]{6}", track):
        raise ValueError(f"event.track is a GT7 layout id (six hex digits), not {track!r}")
    return track, float(event.get("track_length_m", drive.DEFAULT_TRACK_M))


def child_env(base, extra):
    """`base` without any inherited RACECAST_* value, plus `extra`."""
    env = {k: v for k, v in base.items() if not k.startswith("RACECAST_")}
    env.update(extra)
    return env


SRC = os.path.join(ROOT, "src")
PROFILE_DIR = os.path.join(ROOT, "profiles", PROFILE)


def make_home(home, src=SRC, profile=PROFILE_DIR, track=None):
    """A throwaway app home: src/, profiles/demo, an empty YouTube cookie jar, one
    placeholder graphic and, for `track` (id, length), that layout's simulated line."""
    shutil.copytree(src, os.path.join(home, "src"), ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(profile, os.path.join(home, "profiles", PROFILE))
    runtime = os.path.join(home, "runtime")
    os.makedirs(runtime, exist_ok=True)
    with open(os.path.join(runtime, "yt-cookies.txt"), "w", encoding="utf-8") as fh:
        fh.write("# Netscape HTTP Cookie File\n")
    graphics = os.path.join(runtime, PROFILE, "graphics")
    os.makedirs(graphics, exist_ok=True)
    with open(os.path.join(graphics, "sim-placeholder.png"), "wb") as fh:
        fh.write(placeholder_png())
    if track:
        drive.write_track_data(runtime, *track)
    return os.path.join(home, "src", "racecast.py")


class Child:
    """One supervised service. `stop()` closes the control pipe; the supervisor then
    stops the whole tree."""

    def __init__(self, name, argv, env, cwd, log_path, seconds):
        self.name, self.log_path = name, log_path
        self.log = open(log_path, "wb")  # noqa: SIM115  closed in stop()
        try:
            self.proc = subprocess.Popen([sys.executable, CHILD, str(seconds), "--long", "--", *argv],
                                         cwd=cwd, env=env, stdin=subprocess.PIPE,
                                         stdout=self.log, stderr=subprocess.STDOUT)
        except BaseException:
            self.log.close()
            raise

    def alive(self):
        return self.proc.poll() is None

    def stop(self):
        """Returns a problem text, or ""."""
        problem = ""
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
            try:
                code = self.proc.wait(timeout=20)
                if code not in (0, 124):
                    problem = f"{self.name} supervisor exited {code}"
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
                problem = f"{self.name} supervisor did not stop; killed"
        finally:
            self.log.close()
        return problem

    def tail(self, limit=LOG_TAIL):
        try:
            with open(self.log_path, "rb") as fh:
                return fh.read()[-limit:].decode("utf-8", "replace")
        except OSError:
            return ""


def wait_http(base, path, child, timeout, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + timeout
    while clock() < deadline:
        if not child.alive():
            break
        try:
            status, _ = probes.http_json(base, path, timeout=2)
            if status == 200:
                return
        except (OSError, http.client.HTTPException):
            pass  # not listening yet
        sleep(0.3)
    raise RuntimeError(f"{child.name} not ready at {base}{path} within {timeout}s\n"
                       f"--- {child.name} log ---\n{child.tail()}")


_STAMP = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:[.,]\d+)? ")


def log_findings(text, name):
    """A hard finding per traceback in a service log, a soft one per distinct ERROR line
    (with its count)."""
    out, errors, lines = [], {}, text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("Traceback (most recent call last)"):
            last = next((ln for ln in lines[i + 1:] if ln and not ln.startswith((" ", "\t"))), "")
            out.append(("hard", f"{name} log: traceback {last.strip()}"))
            continue
        bare = _STAMP.sub("", line).strip()
        if bare.startswith("ERROR"):
            errors[bare] = errors.get(bare, 0) + 1
    for bare, n in errors.items():
        out.append(("soft", f"{name} log: {bare}" + (f" ({n}x)" if n > 1 else "")))
    return out


class Stack:
    def __init__(self, scn, work_dir, python=sys.executable, ready_timeout=READY_TIMEOUT_S):
        self.scn = scn
        self.work = os.path.abspath(work_dir)
        self.python = python
        self.ready_timeout = ready_timeout
        self.secret = secrets.token_hex(24)
        self.sheet = self.sink = self.stub = self.obs = None
        self.children = []
        self.urls = {}
        self.racecast = self.env = self.home = None

    def start(self):
        os.makedirs(self.work, exist_ok=True)
        os.chmod(self.work, 0o700)
        racecast = make_home(os.path.join(self.work, "app"), track=event_track(self.scn.event))
        taken = set()

        def port():
            p = free_port(taken)
            taken.add(p)
            return p
        feed_a, feed_b, pov, http_port, ui_port = (port() for _ in range(5))

        keys = {PART_KEY_REF.format(label): secrets.token_hex(8) for label in event_parts(self.scn.event)}
        self.sheet = sheet_sim.SheetSim(tabs=sheet_tabs(self.scn), stream_keys=keys).start()
        self.sink = webhook_sink.WebhookSink().start()
        self.stub = stream_stub.StreamStub(os.path.join(self.work, "streams")).install()
        for c in self.scn.commentators:
            self.stub.channel(c.channel, stint=c.stint, commentator=c.name)
        with open(COLLECTION, encoding="utf-8") as fh:
            collection = rewrite_feed_urls(json.load(fh), port_map((feed_a, feed_b, pov), http_port))
        grabber = obs_grab.FeedGrabber(ports=(feed_a, feed_b, pov), interval=2.0)
        self.obs = obs_sim.ObsSim(collection=collection, grabber=grabber).start()

        fakes = {}
        for fake in (self.sheet, self.sink, self.stub, self.obs):
            fakes.update(fake.env())
        env = child_env(os.environ, {**fakes, **port_env((feed_a, feed_b, pov), http_port, ui_port),
                                     "RACECAST_PROFILE": PROFILE,
                                     "RACECAST_UPDATE_CHECK": "0",
                                     "RACECAST_CONSOLE_SECRET": self.secret,
                                     "PYTHONDONTWRITEBYTECODE": "1"})
        home = os.path.dirname(os.path.dirname(racecast))
        self.racecast, self.env, self.home = racecast, env, home
        relay = self._child("relay", [self.python, racecast, "--simulation", "relay", "run",
                                      "--bind", "127.0.0.1", "--http-port", str(http_port),
                                      "--gt7-bind", "127.0.0.1", "--gt7-port", "0",
                                      "--gt7-ps-ip", "127.0.0.1"], env, home)
        self.urls = {"relay": f"http://127.0.0.1:{http_port}", "ui": f"http://127.0.0.1:{ui_port}",
                     "obs": self.obs.http_url, "sheet": self.sheet.http_url, "sink": self.sink.http_url}
        wait_http(self.urls["relay"], "/status", relay, self.ready_timeout)
        holders = ports.pids_on_port(http_port)
        if len(holders) != 1:
            raise RuntimeError(f"expected one relay on port {http_port}, found PIDs {holders}")
        stamp_relay(home, holders[0])
        ui = self._child("ui", [self.python, racecast, "--simulation", "ui", "--no-browser"], env, home)
        wait_http(self.urls["ui"], "/api/ping", ui, self.ready_timeout)
        self.feed_ports = (feed_a, feed_b, pov)
        return self

    def _child(self, name, argv, env, cwd):
        logs = os.path.join(self.work, "logs")
        os.makedirs(logs, exist_ok=True)
        child = Child(name, argv, env, cwd, os.path.join(logs, name + ".log"), lifetime(self.scn))
        self.children.append(child)
        return child

    def token(self, name):
        return console_auth.mint_token(self.secret, console_auth.streamer_key(name), version=1)

    def event_link(self, kind):
        """The id of the event's driver invite ("invite") or public timing link ("timing"),
        which Start event creates in the app home; None before that."""
        path = os.path.join(self.work, "app", "runtime", PROFILE, EVENT_FILES[kind])
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        return data.get("id") if isinstance(data, dict) and isinstance(data.get("id"), str) else None

    def probes(self):
        return {"relay": probes.RelayProbe(self.urls["relay"]),
                "race": probes.RaceProbe(self.urls["relay"]),
                "sheet": probes.SheetProbe(self.sheet),
                "webhook": probes.WebhookProbe(self.sink),
                "obs": probes.ObsProbe(self.obs, self.stub, stream_stub.decode_identity)}

    def log_files(self):
        """(name, path) of every service log: the captured output and the relay's own logs."""
        out = [(c.name, c.log_path) for c in self.children]
        logs = os.path.join(self.work, "app", "runtime", PROFILE, "logs")
        if os.path.isdir(logs):
            out += [("relay-" + n[:-4], os.path.join(logs, n)) for n in sorted(os.listdir(logs))
                    if n.endswith(".log")]
        return out

    def log_checks(self):
        out = []
        for name, path in self.log_files():
            try:
                with open(path, "rb") as fh:
                    out += log_findings(fh.read().decode("utf-8", "replace"), name)
            except OSError:
                out.append(("soft", f"{name} log unreadable"))
        return out

    def stop_relay(self, run=subprocess.run):
        """Stop the relay this app home's PID file names: a relay the Control Center
        restarted is detached and no child of this run. Returns a problem text, or ""."""
        if not self.racecast or not os.path.exists(os.path.join(self.home, "runtime", "relay.pid")):
            return ""
        try:
            done = run([self.python, self.racecast, "--simulation", "relay", "stop"], cwd=self.home,
                       env=self.env, capture_output=True, text=True, errors="replace", timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"relay stop: {exc}"
        return "" if done.returncode == 0 else f"relay stop exited {done.returncode}: {done.stdout[-300:]}"

    def stop(self, logs_to=None):
        """Stop everything; copy the service logs to `logs_to`. Returns problem texts."""
        problems = []
        problem = self.stop_relay()
        if problem:
            problems.append(problem)
        for child in reversed(self.children):
            problem = child.stop()
            if problem:
                problems.append(problem)
        for fake in (self.obs, self.sink, self.sheet):
            if fake is not None:
                try:
                    fake.stop()
                except Exception as exc:  # stop the others too
                    problems.append(f"{type(fake).__name__} stop: {exc}")
        if logs_to:
            os.makedirs(logs_to, exist_ok=True)
            for name, path in self.log_files():
                try:
                    shutil.copy(path, os.path.join(logs_to, name + ".log"))
                except OSError as exc:
                    problems.append(f"copy {name} log: {exc}")
        return problems

#!/usr/bin/env python3
"""Stand-ins for yt-dlp and streamlink that serve `sim://<name>` channels as an ffmpeg
test pattern, for the event simulation (#1231).

`StreamStub(directory).install()` writes the two stand-ins into `directory`, the
`RACECAST_SIM_STREAM_BIN` the relay puts first on PATH. yt-dlp resolves a live channel to
`http://sim.invalid/<name>/<height>.m3u8` (never fetchable, so the relay's manifest probe
finds nothing); streamlink maps that URL back to the channel and writes MPEG-TS from
ffmpeg: a moving test pattern with the stint, commentator and channel burned in, an
identity strip `decode_identity` reads from a frame, and a tone per channel. Anything
that is not a `sim://` channel is refused, and the stand-ins open no connection.

Each channel's state lives in `<directory>/channels/<name>.json`, written by the control
methods and read by every running stand-in about five times a second:
- status: live, offline, upcoming or ended. Anything but live ends running pulls, and
  yt-dlp answers with the error text the relay classifies.
- cut(at): every pull running at `at` ends like a finished stream (exit 0); later pulls play.
- stall(at, until): pulls in the window send no bytes and stay alive.
- freeze(at, until): pulls in the window send a still picture, with the tone and the
  identity, at the normal rate.
Times are epoch seconds (`time.time()`), shared by the processes; `at` defaults to now,
`until` to "until clear()"."""
import argparse
import glob
import importlib.util
import io
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import simulation  # noqa: E402

STATUSES = ("live", "offline", "upcoming", "ended")
RESOLVED_HOST = "sim.invalid"
MAX_HEIGHT = 360
FPS = 25
POLL = 0.2
MAX_CUTS = 32
CELLS = 18
YTDLP_VERSION = "2026.01.01"
STREAMLINK_VERSION = "streamlink 8.2.0"
STREAMLINK_HELP = """usage: streamlink [OPTIONS] <URL> [STREAM]

Simulation stand-in. Options:
  --stdout
  --player-external-http
  --player-external-http-port PORT
  --player-external-http-interface INTERFACE
  --http-header KEY=VALUE
  --http-cookies-file FILENAME
  --ringbuffer-size SIZE
  --hls-live-edge SEGMENTS
  --stream-segmented-queue-deadline FACTOR
  --twitch-api-header KEY=VALUE
  --twitch-low-latency
  --retry-streams DELAY
  --retry-open ATTEMPTS
  --json
"""
_YTDLP_VALUE = {"-f": "format", "--print": "prints", "--cookies": "cookies",
                "--cookies-from-browser": "cookies_from_browser", "-o": "output",
                "--extractor-args": None, "--playlist-items": None, "--playlist-end": None,
                "--audio-format": None, "--merge-output-format": None}
_YTDLP_FLAG = {"-g": "get_url", "--flat-playlist": "flat", "-J": "dump_json",
               "--skip-download": "skip_download", "--ignore-no-formats-error": "ignore_no_formats",
               "--simulate": "simulate", "-x": "extract_audio", "--version": "version",
               "--no-warnings": None, "--no-playlist": None, "-v": None,
               "--force-overwrites": None, "--no-continue": None}
_STREAMLINK_VALUE = {"--player-external-http-port": "http_port", "--http-header": "headers",
                     "--player-external-http-interface": None, "--http-cookies-file": None,
                     "--ringbuffer-size": None, "--hls-live-edge": None,
                     "--stream-segmented-queue-deadline": None, "--hls-segment-queue-threshold": None,
                     "--twitch-api-header": None, "--retry-streams": None, "--retry-open": None}
_STREAMLINK_FLAG = {"--stdout": "stdout", "--player-external-http": "http", "--json": "json",
                    "--help": "help", "-h": "help", "--version": "version",
                    "--twitch-low-latency": None}
_YTDLP_ERRORS = {"offline": "The channel is not currently live.",
                 "upcoming": "This live event will begin in a few moments.",
                 "ended": "This live event has ended."}
_LIVE_STATUS = {"live": "is_live", "upcoming": "is_upcoming", "ended": "was_live"}


class Usage(Exception):
    """A command line the real tool would reject (exit 2)."""


class LauncherMissing(RuntimeError):
    """No Windows script launcher found in this Python."""


# --- channel state ---

def _valid_name(name):
    return isinstance(name, str) and bool(simulation.CHANNEL_RE.match("sim://" + name))


def channel_path(directory, name):
    return os.path.join(directory, "channels", name + ".json")


def load_channel(directory, name):
    """The channel's state, or None when it does not exist. A read error raises."""
    try:
        with open(channel_path(directory, name), encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None


def _write_json(path, data):
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    pause = threading.Event()
    for _ in range(100):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:   # Windows: a reader holds the target open
            pause.wait(0.02)
    os.replace(tmp, path)


def _in_window(window, now):
    if not window:
        return False
    until = window.get("until")
    return window["at"] <= now and (until is None or now < until)


def pull_action(channel, started, now):
    """What a pull that started at `started` does at `now`: play, freeze, stall, cut or end."""
    if not channel or channel.get("status") != "live":
        return "end"
    if any(started < c <= now for c in channel.get("cuts") or ()):
        return "cut"
    if _in_window(channel.get("stall"), now):
        return "stall"
    if _in_window(channel.get("freeze"), now):
        return "freeze"
    return "play"


class StreamStub:
    """The control side: installs the stand-ins and sets each channel's state."""

    def __init__(self, directory, ffmpeg=("ffmpeg",), python=None, clock=time.time, windows=None):
        self.directory = os.path.abspath(directory)
        self.ffmpeg, self.python, self.clock = list(ffmpeg), python or sys.executable, clock
        self.windows = os.name == "nt" if windows is None else windows
        self.lock = threading.Lock()

    def install(self):
        os.makedirs(os.path.join(self.directory, "channels"), exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass  # best effort, as for runtime dirs
        _write_json(os.path.join(self.directory, "stub.json"), {"ffmpeg": self.ffmpeg})
        for tool in simulation.STREAM_TOOLS:
            source = stand_in_source(tool, self.directory)
            if self.windows:
                path, body = os.path.join(self.directory, tool + ".exe"), windows_stand_in(source, self.python)
            else:
                path, body = os.path.join(self.directory, tool), posix_stand_in(source, self.python)
            with open(path, "wb") as fh:
                fh.write(body)
            os.chmod(path, 0o700)
        return self

    def env(self):
        return {simulation.FLAG: "1", simulation.STREAM_BIN: self.directory}

    def get(self, name):
        return load_channel(self.directory, name)

    def channels(self):
        out = {}
        for path in sorted(glob.glob(os.path.join(self.directory, "channels", "*.json"))):
            name = os.path.basename(path)[:-5]
            ch = load_channel(self.directory, name) if _valid_name(name) else None
            if ch:
                out[name] = ch
        return out

    def channel(self, name, stint=None, commentator=None, status=None):
        """Create or update `sim://<name>`; None keeps a field (a new channel is live)."""
        if not _valid_name(name):
            raise ValueError(f"not a sim:// channel name: {name!r}")
        if status is not None and status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        if stint is not None and not (isinstance(stint, int) and 0 <= stint <= 255):
            raise ValueError("stint must be 0-255")
        with self.lock:
            ch = self.get(name)
            if ch is None:
                codes = {c.get("code") for c in self.channels().values()}
                code = next((n for n in range(1, 256) if n not in codes), None)
                if code is None:
                    raise ValueError("at most 255 simulated channels")
                ch = {"name": name, "code": code, "stint": 0, "commentator": "", "status": "live",
                      "cuts": [], "stall": None, "freeze": None}
            for key, value in (("stint", stint), ("commentator", commentator), ("status", status)):
                if value is not None:
                    ch[key] = value
            _write_json(channel_path(self.directory, name), ch)
            return ch

    def _update(self, name, change):
        with self.lock:
            ch = self.get(name)
            if ch is None:
                raise ValueError(f"no simulated channel {name!r}")
            change(ch)
            _write_json(channel_path(self.directory, name), ch)
            return ch

    def cut(self, name, at=None):
        at = self.clock() if at is None else at
        self._update(name, lambda ch: ch.update(cuts=(ch["cuts"] + [at])[-MAX_CUTS:]))
        return at

    def stall(self, name, at=None, until=None):
        at = self.clock() if at is None else at
        self._update(name, lambda ch: ch.update(stall={"at": at, "until": until}))
        return at

    def freeze(self, name, at=None, until=None):
        at = self.clock() if at is None else at
        self._update(name, lambda ch: ch.update(freeze={"at": at, "until": until}))
        return at

    def clear(self, name):
        """End a stall and a freeze, running or scheduled."""
        self._update(name, lambda ch: ch.update(stall=None, freeze=None))


# --- the stand-in executables ---

def stand_in_source(tool, directory):
    return ("import sys\n"
            f"sys.path.insert(0, {HERE!r})\n"
            "import stream_stub\n"
            f"sys.exit(stream_stub.main_tool({tool!r}, {directory!r}, sys.argv[1:]))\n")


def posix_stand_in(source, python):
    """A script that is valid sh and Python, so the interpreter path may be any length."""
    if re.search(r"[\"$`\\\n]|'''", python):
        raise ValueError(f"cannot quote the interpreter path {python!r}")
    return f"#!/bin/sh\n'''exec' \"{python}\" \"$0\" \"$@\"\n' '''\n{source}".encode("utf-8")


def launcher_shebang(python):
    """The shebang line distlib writes after its launcher, quoted when the path has a space."""
    if " " in python and not python.startswith('"'):
        python = f'"{python}"'
    return b"#!" + python.encode("utf-8") + b"\n"


def windows_launcher(machine=None, bits=None):
    """distlib's console launcher, the one pip's own `.exe` scripts use, from the pip wheel
    bundled with ensurepip or an installed pip."""
    machine = (machine or platform.machine()).lower()
    bits = bits or struct.calcsize("P") * 8
    name = "t64-arm.exe" if machine in ("arm64", "aarch64") else ("t32.exe" if bits == 32 else "t64.exe")
    member = "pip/_vendor/distlib/" + name
    spec = importlib.util.find_spec("ensurepip")
    if spec and spec.origin:
        for wheel in sorted(glob.glob(os.path.join(os.path.dirname(spec.origin), "_bundled", "pip-*.whl"))):
            try:
                with zipfile.ZipFile(wheel) as zf:
                    return zf.read(member)
            except (OSError, KeyError, zipfile.BadZipFile):
                continue
    spec = importlib.util.find_spec("pip")
    if spec and spec.origin:
        path = os.path.join(os.path.dirname(spec.origin), "_vendor", "distlib", name)
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                return fh.read()
    raise LauncherMissing(f"no {name} in this Python's ensurepip wheel or pip")


def windows_stand_in(source, python):
    """launcher + shebang + a zip holding __main__.py: the launcher runs the interpreter on
    its own file, and Python runs the zip appended to it."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("__main__.py", source)
    return windows_launcher() + launcher_shebang(python) + buf.getvalue()


# --- command lines ---

def _parse(argv, values, flags, multi=()):
    opts = {k: None for k in list(values.values()) + list(flags.values()) if k}
    for k in multi:
        opts[k] = []
    positional, i = [], 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            positional += argv[i + 1:]
            break
        key, _, inline = arg.partition("=") if arg.startswith("--") else (arg, "", "")
        if key in values:
            if inline:
                value = inline
            elif i + 1 < len(argv):
                i += 1
                value = argv[i]
            else:
                raise Usage(f"{key} expects an argument")
            dest = values[key]
            if dest in multi:
                opts[dest].append(value)
            elif dest:
                opts[dest] = value
        elif arg in flags:
            if flags[arg]:
                opts[flags[arg]] = True
        elif arg.startswith("-") and arg != "-":
            raise Usage(f"unrecognized arguments: {arg}")
        else:
            positional.append(arg)
        i += 1
    return opts, positional


def parse_ytdlp(argv):
    opts, positional = _parse(argv, _YTDLP_VALUE, _YTDLP_FLAG, multi=("prints",))
    if len(positional) > 1:
        raise Usage("the stand-in takes one URL")
    opts["url"] = positional[0] if positional else None
    if not (opts["url"] or opts["version"]):
        raise Usage("You must provide at least one URL.")
    return opts


def parse_streamlink(argv):
    opts, positional = _parse(argv, _STREAMLINK_VALUE, _STREAMLINK_FLAG, multi=("headers",))
    if len(positional) > 2:
        raise Usage(f"unrecognized arguments: {' '.join(positional[2:])}")
    opts["target"] = positional[0] if positional else None
    opts["quality"] = positional[1] if len(positional) > 1 else None
    if opts["http_port"] is not None:
        try:
            opts["http_port"] = int(opts["http_port"])
        except ValueError:
            raise Usage("--player-external-http-port: invalid int value") from None
    if not (opts["target"] or opts["help"] or opts["version"]):
        raise Usage("the following arguments are required: URL")
    return opts


def resolved_url(name, height):
    return f"http://{RESOLVED_HOST}/{name}/{height}.m3u8"


def resolved_channel(url):
    """(name, height) of a URL the yt-dlp stand-in issued, or of a bare sim:// channel; else None."""
    url = (url or "").strip()
    if simulation.CHANNEL_RE.match(url):
        return url[len("sim://"):], MAX_HEIGHT
    try:
        u = urlsplit(url)
        port = u.port
    except ValueError:
        return None
    parts = u.path.split("/")
    if (u.scheme != "http" or u.hostname != RESOLVED_HOST or port is not None or u.username is not None
            or u.query or u.fragment or len(parts) != 3 or not _valid_name(parts[1])):
        return None
    m = re.fullmatch(r"(\d{3,4})\.m3u8", parts[2])
    return (parts[1], min(int(m.group(1)), MAX_HEIGHT)) if m else None


def height_for(fmt):
    m = re.search(r"height<=(\d+)", fmt or "")
    return min(int(m.group(1)), MAX_HEIGHT) if m else MAX_HEIGHT


def width_for(height):
    return height * 16 // 9 // 2 * 2


# --- the picture ---

def _bits(code, stint):
    return [1] + [(code >> (7 - i)) & 1 for i in range(8)] + [(stint >> (7 - i)) & 1 for i in range(8)] + [0]


def identity_boxes(width, height, code, stint):
    """(x, y, w, h, white) per cell of the identity strip at the bottom of the picture."""
    cw, bh = width // CELLS, height // 8
    return [(i * cw, height - bh, cw, bh, bool(bit)) for i, bit in enumerate(_bits(code, stint or 0))]


def decode_identity(rgb, width, height):
    """(code, stint) from an rgb24 frame carrying the identity strip, else None."""
    bits = []
    for x, y, w, h, _white in identity_boxes(width, height, 0, 0):
        total = n = 0
        for yy in range(y + h // 2 - 2, y + h // 2 + 3):
            for xx in range(x + w // 2 - 2, x + w // 2 + 3):
                p = (yy * width + xx) * 3
                total += sum(rgb[p:p + 3]) // 3
                n += 1
        level = total / n
        if 55 <= level <= 200:
            return None
        bits.append(1 if level > 200 else 0)
    if bits[0] != 1 or bits[-1] != 0:
        return None
    code = int("".join(map(str, bits[1:9])), 2)
    return (code, int("".join(map(str, bits[9:17])), 2)) if code else None


def tone_hz(code):
    return 300 + 20 * (code % 40)


def label_text(channel):
    lines = [f"STINT {channel['stint']}" if channel.get("stint") else "", channel.get("commentator") or "",
             "sim://" + channel["name"]]
    return "\n".join(line for line in lines if line)


def ffmpeg_argv(ffmpeg, mode, height, code, stint, text, offset):
    """ffmpeg writing the pattern as MPEG-TS to stdout; `freeze` swaps the moving test
    source for still colour bars, and `text` reads label.txt from the working directory."""
    width = width_for(height)
    filters = []
    if text:
        filters.append(f"drawtext=textfile=label.txt:expansion=none:fontcolor=white:fontsize={max(12, height // 12)}"
                       ":box=1:boxcolor=black@0.6:x=16:y=16:line_spacing=6")
    for x, y, w, h, white in identity_boxes(width, height, code, stint):
        filters.append(f"drawbox=x={x}:y={y}:w={w}:h={h}:color={'white' if white else 'black'}:t=fill")
    source = "smptebars" if mode == "freeze" else "testsrc2"
    return [*ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
            "-re", "-f", "lavfi", "-i", f"{source}=size={width}x{height}:rate={FPS}",
            "-re", "-f", "lavfi", "-i", f"sine=frequency={tone_hz(code)}:sample_rate=48000",
            "-map", "0:v", "-map", "1:a", "-vf", ",".join(filters),
            "-c:v", "mpeg2video", "-q:v", "5", "-g", str(FPS), "-bf", "0", "-pix_fmt", "yuv420p",
            "-c:a", "mp2", "-b:a", "96k", "-output_ts_offset", f"{offset:.3f}", "-f", "mpegts", "pipe:1"]


# --- the pull ---

class Pull:
    """One stream read: runs ffmpeg for the channel's current action and copies its bytes
    to `write`, restarting it when the action changes. run() returns the exit code."""

    def __init__(self, directory, name, height, ffmpeg, write, log, started=None, clock=time.time, poll=POLL):
        self.directory, self.name, self.height, self.ffmpeg = directory, name, height, list(ffmpeg)
        self.write, self.log, self.clock, self.poll = write, log, clock, poll
        self.started = clock() if started is None else started
        self.last = None

    def channel(self):
        try:
            self.last = load_channel(self.directory, self.name)
        except (OSError, ValueError):
            pass  # a write in progress: keep the last state
        return self.last

    def action(self):
        return pull_action(self.channel(), self.started, self.clock())

    def run(self):
        workdir = os.path.join(self.directory, "pulls", f"{os.getpid()}-{threading.get_ident()}")
        os.makedirs(workdir, exist_ok=True)
        try:
            return self._loop(workdir)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _loop(self, workdir):
        text, tick = True, threading.Event()
        while True:
            act = self.action()
            if act in ("cut", "end"):
                self.log("[cli][info] Stream ended")
                self.log("[cli][info] Closing currently open stream...")
                return 0
            if act == "stall":
                self.log("[sim][info] stall")
                while self.action() == "stall":
                    tick.wait(self.poll)
                continue
            ch = self.last
            with open(os.path.join(workdir, "label.txt"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(label_text(ch))
            argv = ffmpeg_argv(self.ffmpeg, act, self.height, ch["code"], ch.get("stint"), text,
                               max(0.0, self.clock() - self.started))
            self.log(f"[sim][info] {act}")
            try:
                rc, wrote, changed, broken = self._ffmpeg(argv, act, workdir)
            except OSError as e:
                self.log(f"error: could not start ffmpeg: {e}")
                return 1
            if broken:
                self.log("error: Error when writing to output: broken pipe, exiting")
                return 1
            if changed:
                continue
            if not wrote and text:
                self.log("[sim][warning] ffmpeg failed with the text overlay, continuing without text")
                text = False
                continue
            self.log(f"error: Error when reading from stream: ffmpeg exited with code {rc}, exiting")
            return 1

    def _ffmpeg(self, argv, act, workdir):
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, cwd=workdir)
        changed, done = [], threading.Event()

        def watch():
            while not done.wait(self.poll):
                if self.action() != act:
                    changed.append(True)
                    proc.kill()
                    return

        def errors():
            for raw in iter(proc.stderr.readline, b""):
                self.log("[ffmpeg] " + raw.decode("utf-8", "replace").rstrip())

        threads = [threading.Thread(target=watch, daemon=True), threading.Thread(target=errors, daemon=True)]
        for t in threads:
            t.start()
        wrote = broken = False
        try:
            for chunk in iter(lambda: proc.stdout.read1(65536), b""):
                try:
                    self.write(chunk)
                except OSError:
                    broken = True
                    break
                wrote = True
        finally:
            done.set()
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            for t in threads:
                t.join(5)
            proc.stdout.close()
            proc.stderr.close()
        return proc.returncode, wrote, bool(changed), broken


# --- the tools ---

def _config(directory):
    try:
        with open(os.path.join(directory, "stub.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _render(template, fields):
    return re.sub(r"%\((\w+)\)s", lambda m: "NA" if fields.get(m.group(1)) is None else str(fields[m.group(1)]),
                  template)


def ytdlp_main(directory, argv, out, err):
    try:
        o = parse_ytdlp(argv)
    except Usage as e:
        err.write(f"Usage: yt-dlp [OPTIONS] URL [URL...]\n\nyt-dlp: error: {e}\n")
        return 2
    if o["version"]:
        out.write(YTDLP_VERSION + "\n")
        return 0
    if o["cookies_from_browser"]:
        err.write("ERROR: [sim] the simulation stand-in exports no browser cookies\n")
        return 1
    url = o["url"].strip()
    if not simulation.CHANNEL_RE.match(url):
        err.write(f"ERROR: [sim] the simulation stand-in serves only sim:// channels, not {url}\n")
        return 1
    if o["extract_audio"] or o["output"]:
        err.write(f"ERROR: [sim] {url}: a simulated stream cannot be downloaded\n")
        return 1
    name = url[len("sim://"):]
    try:
        ch = load_channel(directory, name)
    except (OSError, ValueError) as e:
        err.write(f"ERROR: [sim] {url}: channel state unreadable: {e}\n")
        return 1
    status = ch["status"] if ch else "offline"
    if status != "live" and not (o["ignore_no_formats"] and status in _LIVE_STATUS):
        note = "" if ch else " (no such simulated channel)"
        err.write(f"ERROR: [youtube] {name}: {_YTDLP_ERRORS[status]}{note}\n")
        return 1
    live = status == "live"
    height = height_for(o["format"])
    fields = {"id": name, "title": label_text(ch).replace("\n", " - "), "channel": ch.get("commentator") or name,
              "live_status": _LIVE_STATUS[status], "is_live": live, "height": height if live else None,
              "fps": FPS if live else None, "url": resolved_url(name, height) if live else None}
    if o["dump_json"]:
        if o["flat"]:
            entries = [{"id": name, "live_status": fields["live_status"], "is_live": live}]
            out.write(json.dumps({"_type": "playlist", "id": name, "channel_id": None, "entries": entries}) + "\n")
        else:
            out.write(json.dumps(fields) + "\n")
        return 0
    for template in o["prints"]:
        out.write(_render(template, fields) + "\n")
    if o["get_url"] and fields["url"]:
        out.write(fields["url"] + "\n")
    return 0


def serve_http(directory, name, height, ffmpeg, port, log, clock=time.time):
    """--player-external-http: one ffmpeg per client on 127.0.0.1:port until the stream ends."""
    started, ended = clock(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "video/unknown")
            self.end_headers()

            def write(chunk):
                self.wfile.write(chunk)
                self.wfile.flush()
            if Pull(directory, name, height, ffmpeg, write, log, started=started, clock=clock).run() == 0:
                ended.set()

        def log_message(self, *args):
            pass  # streamlink logs no per-request lines

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        log(f"error: Failed to create HTTP server: {e}")
        return 1
    server.daemon_threads = True
    log("[cli][info] Starting server, access with one of:")
    log(f"[cli][info]  http://127.0.0.1:{port}/")
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": POLL}, daemon=True)
    thread.start()
    probe = Pull(directory, name, height, ffmpeg, None, log, started=started, clock=clock)
    try:
        while not ended.wait(POLL):
            if probe.action() in ("cut", "end"):
                break
    finally:
        server.shutdown()
        server.server_close()
    log("[cli][info] Stream ended")
    return 0


def streamlink_main(directory, argv, out, err):
    def log(line):
        err.write(line + "\n")
        err.flush()
    try:
        o = parse_streamlink(argv)
    except Usage as e:
        log(f"usage: streamlink [OPTIONS] <URL> [STREAM]\nstreamlink: error: {e}")
        return 2
    if o["help"]:
        out.write(STREAMLINK_HELP)
        return 0
    if o["version"]:
        out.write(STREAMLINK_VERSION + "\n")
        return 0
    target = resolved_channel(o["target"])
    if target is None:
        log(f"error: No plugin can handle URL: {o['target']}")
        return 1
    name, height = target
    try:
        ch = load_channel(directory, name)
    except (OSError, ValueError) as e:
        log(f"error: channel state unreadable: {e}")
        return 1
    live = bool(ch) and ch.get("status") == "live"
    if o["json"]:
        body = ({"plugin": "sim", "metadata": {"title": label_text(ch), "category": "simulation"},
                 "streams": {"live": {"type": "hls", "url": o["target"]}}} if live
                else {"error": f"No playable streams found on this URL: {o['target']}"})
        out.write(json.dumps(body) + "\n")
        return 0 if live else 1
    if not live:
        log(f"error: No playable streams found on this URL: {o['target']}")
        return 1
    if not (o["stdout"] or o["http"]):
        log("error: the simulation stand-in needs --stdout or --player-external-http")
        return 1
    log(f"[cli][info] Found matching plugin sim for URL {o['target']}")
    log("[cli][info] Available streams: live (worst, best)")
    log("[cli][info] Opening stream: live (hls)")
    ffmpeg = _config(directory).get("ffmpeg") or ["ffmpeg"]
    if o["http"]:
        return serve_http(directory, name, height, ffmpeg, o["http_port"] or 0, log)
    stdout = out.buffer

    def write(chunk):
        stdout.write(chunk)
        stdout.flush()
    return Pull(directory, name, height, ffmpeg, write, log).run()


def main_tool(tool, directory, argv):
    """Entry point of an installed stand-in."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass  # a replaced stream keeps its own handling
    main = ytdlp_main if tool == "yt-dlp" else streamlink_main
    try:
        return main(directory, argv, sys.stdout, sys.stderr)
    except KeyboardInterrupt:
        return 130


def main(argv=None):
    ap = argparse.ArgumentParser(description="Stream stub for the event simulation: install the "
                                 "yt-dlp/streamlink stand-ins and set sim:// channel state.")
    ap.add_argument("directory", help="the stand-in directory (RACECAST_SIM_STREAM_BIN)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    inst = sub.add_parser("install", help="write the stand-ins")
    inst.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg binary the stand-ins run (default: on PATH)")
    ch = sub.add_parser("set", help="create or update a channel")
    ch.add_argument("name")
    ch.add_argument("--stint", type=int)
    ch.add_argument("--commentator")
    ch.add_argument("--status", choices=STATUSES)
    for verb in ("cut", "stall", "freeze"):
        p = sub.add_parser(verb, help=f"{verb} a channel's pulls")
        p.add_argument("name")
        p.add_argument("--at", type=float, help="epoch seconds (default: now)")
        if verb != "cut":
            p.add_argument("--until", type=float, help="epoch seconds (default: until clear)")
    sub.add_parser("clear", help="end a stall and a freeze").add_argument("name")
    sub.add_parser("show", help="print every channel's state as JSON")
    a = ap.parse_args(argv)
    if a.cmd == "install":
        stub = StreamStub(a.directory, ffmpeg=[a.ffmpeg]).install()
        print(json.dumps(stub.env()))
        return 0
    stub = StreamStub(a.directory)
    if a.cmd == "set":
        stub.channel(a.name, stint=a.stint, commentator=a.commentator, status=a.status)
    elif a.cmd == "cut":
        stub.cut(a.name, at=a.at)
    elif a.cmd in ("stall", "freeze"):
        getattr(stub, a.cmd)(a.name, at=a.at, until=a.until)
    elif a.cmd == "clear":
        stub.clear(a.name)
    print(json.dumps(stub.channels(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

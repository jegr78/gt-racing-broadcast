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
- stall(at, until): pulls in the window send no bytes; after `stream_timeout` seconds
  without one a pull gives up with a read timeout (exit 1), as streamlink does.
- freeze(at, until): pulls in the window send a still picture, with the tone and the
  identity, at the normal rate.
Times are epoch seconds (`time.time()`), shared by the processes; `at` defaults to now,
`until` to "until clear()"."""
import argparse
import contextlib
import glob
import importlib.util
import io
import json
import os
import platform
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import zipfile
from collections import namedtuple
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
STREAM_TIMEOUT = 60.0      # streamlink's default --stream-timeout
PLAYER_TIMEOUT = 30.0      # a player that stops reading for this long is dropped
LOCK_WAIT = 10.0
LOCK_STALE = 10.0          # a lock older than this was left by a crashed controller
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

_DEVICE_NAMES = frozenset(["con", "prn", "aux", "nul"] + [f"{d}{n}" for d in ("com", "lpt") for n in range(1, 10)])


def channel_name(name):
    """The stored form of a channel name (names are case-insensitive), or None if invalid.
    Windows device names are refused, since they cannot be file names there."""
    if not isinstance(name, str) or not simulation.CHANNEL_RE.fullmatch("sim://" + name):
        return None
    name = name.lower()
    return None if name in _DEVICE_NAMES else name


def _valid_name(name):
    return channel_name(name) is not None


def channel_path(directory, name):
    return os.path.join(directory, "channels", name.lower() + ".json")


def _retry_permission(fn):
    """fn(), retried for about half a second while Windows reports a sharing violation."""
    pause = threading.Event()
    for _ in range(25):
        try:
            return fn()
        except PermissionError:
            pause.wait(0.02)
    return fn()


def load_channel(directory, name):
    """The channel's state, or None when it does not exist. A read error raises."""
    def read():
        with open(channel_path(directory, name), encoding="utf-8") as fh:
            return json.load(fh)
    try:
        return _retry_permission(read)
    except FileNotFoundError:
        return None


def _write_json(path, data):
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    _retry_permission(lambda: os.replace(tmp, path))


@contextlib.contextmanager
def state_lock(directory, wait=LOCK_WAIT, stale=LOCK_STALE):
    """Serialize read-modify-write of the channel state across processes (O_EXCL lock file)."""
    lock = os.path.join(directory, "channels", ".lock")
    deadline, pause = time.monotonic() + wait, threading.Event()
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - os.path.getmtime(lock) > stale:
                    os.remove(lock)
                    continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"channel state lock {lock} is held") from None
            pause.wait(0.01)
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            os.remove(lock)


def pid_alive(pid):
    """Whether process `pid` runs; a zombie counts as gone."""
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259     # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{int(pid)}/stat", encoding="utf-8", errors="replace") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        pass  # no procfs: ask ps
    try:
        out = subprocess.run(["ps", "-o", "stat=", "-p", str(int(pid))], capture_output=True, text=True,
                             errors="replace", timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return True
    return bool(out) and not out.startswith("Z")


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
    """The control side: installs the stand-ins and sets each channel's state. Every
    change runs under `state_lock`, so several controllers may share a directory."""

    def __init__(self, directory, ffmpeg=("ffmpeg",), python=None, clock=time.time, windows=None,
                 stream_timeout=STREAM_TIMEOUT):
        self.directory = os.path.abspath(directory)
        self.ffmpeg = list(ffmpeg)
        if self.ffmpeg and os.path.dirname(self.ffmpeg[0]):
            self.ffmpeg[0] = os.path.abspath(self.ffmpeg[0])   # the stand-ins run it from their own cwd
        self.python, self.clock = python or sys.executable, clock
        self.stream_timeout = stream_timeout
        self.windows = os.name == "nt" if windows is None else windows
        self.lock = threading.Lock()

    def install(self):
        """Write (or rewrite) the stand-ins; running pulls carry on."""
        os.makedirs(os.path.join(self.directory, "channels"), exist_ok=True)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass  # best effort, as for runtime dirs
        prune_pulls(self.directory)
        _write_json(os.path.join(self.directory, "stub.json"), {"ffmpeg": self.ffmpeg,
                                                                    "stream_timeout": self.stream_timeout})
        for old in glob.glob(os.path.join(self.directory, "*.old")):
            with contextlib.suppress(OSError):
                os.remove(old)                  # a renamed .exe whose process has ended
        for tool in simulation.STREAM_TOOLS:
            source = stand_in_source(tool, self.directory)
            if self.windows:
                path, body = os.path.join(self.directory, tool + ".exe"), windows_stand_in(source, self.python)
            else:
                path, body = os.path.join(self.directory, tool), posix_stand_in(source, self.python)
            _replace_executable(path, body)
        return self

    def env(self):
        return {simulation.FLAG: "1", simulation.STREAM_BIN: self.directory}

    def get(self, name):
        return load_channel(self.directory, name)

    def channels(self):
        out = {}
        for path in sorted(glob.glob(os.path.join(self.directory, "channels", "*.json"))):
            name = channel_name(os.path.basename(path)[:-5])
            ch = load_channel(self.directory, name) if name else None
            if ch:
                out[name] = ch
        return out

    def channel(self, name, stint=None, commentator=None, status=None):
        """Create or update `sim://<name>`; None keeps a field (a new channel is live)."""
        key = channel_name(name)
        if key is None:
            raise ValueError(f"not a sim:// channel name: {name!r}")
        if status is not None and status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        if stint is not None and not (isinstance(stint, int) and 0 <= stint <= 255):
            raise ValueError("stint must be 0-255")
        with self.lock, state_lock(self.directory):
            ch = self.get(key)
            if ch is None:
                codes = {c.get("code") for c in self.channels().values()}
                code = next((n for n in range(1, 256) if n not in codes), None)
                if code is None:
                    raise ValueError("at most 255 simulated channels per directory: "
                                     "the identity strip holds an 8-bit code")
                ch = {"name": key, "code": code, "stint": 0, "commentator": "", "status": "live",
                      "cuts": [], "stall": None, "freeze": None}
            for field, value in (("stint", stint), ("commentator", commentator), ("status", status)):
                if value is not None:
                    ch[field] = value
            _write_json(channel_path(self.directory, key), ch)
            return ch

    def _update(self, name, change):
        key = channel_name(name)
        with self.lock, state_lock(self.directory):
            ch = self.get(key) if key else None
            if ch is None:
                raise ValueError(f"no simulated channel {name!r}")
            change(ch)
            _write_json(channel_path(self.directory, key), ch)
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


def prune_pulls(directory):
    """Remove the work dirs of pulls whose stand-in has ended."""
    for path in glob.glob(os.path.join(directory, "pulls", "*-*")):
        pid = os.path.basename(path).split("-", 1)[0]
        if pid.isdigit() and not pid_alive(int(pid)):
            shutil.rmtree(path, ignore_errors=True)


def _replace_executable(path, body):
    """Write `path` whole: a running stand-in keeps its old file. Windows cannot replace a
    running .exe but can rename it, so the old one is moved aside for a later install to remove."""
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(body)
    os.chmod(tmp, 0o700)
    try:
        os.replace(tmp, path)
    except PermissionError:
        os.replace(path, f"{path}.{time.time_ns()}.old")
        os.replace(tmp, path)


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
    if url.startswith("sim://"):
        name = channel_name(url[len("sim://"):])
        return (name, MAX_HEIGHT) if name else None
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
    return (channel_name(parts[1]), min(int(m.group(1)), MAX_HEIGHT)) if m else None


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


# --- MPEG-TS splicing ---

TS = 188
TsPacket = namedtuple("TsPacket", "at pid cc payload pts pcr")


def parse_ts(data):
    """TsPacket per whole 188-byte packet of `data`, with its byte offset `at`; `pts` on a
    packet that starts an audio or video PES carrying one, `pcr` where the adaptation field
    has one (both the 33-bit 90 kHz base: they wrap after about 26.5 hours, longer than any
    simulated event)."""
    for i in range(0, len(data) - TS + 1, TS):
        pkt = data[i:i + TS]
        if pkt[0] != 0x47:
            continue
        pid, afc = ((pkt[1] & 0x1F) << 8) | pkt[2], (pkt[3] >> 4) & 3
        pts = pcr = None
        if afc & 2 and pkt[4] >= 7 and pkt[5] & 0x10:
            b = pkt[6:11]
            pcr = b[0] << 25 | b[1] << 17 | b[2] << 9 | b[3] << 1 | b[4] >> 7
        start = 4 + (1 + pkt[4] if afc & 2 else 0)
        pes = pkt[start:]
        if (pkt[1] & 0x40 and afc & 1 and len(pes) >= 14 and pes[:3] == b"\x00\x00\x01"
                and 0xC0 <= pes[3] <= 0xEF and pes[7] & 0x80):
            b = pes[9:14]
            pts = ((b[0] >> 1) & 7) << 30 | b[1] << 22 | (b[2] >> 1) << 15 | b[3] << 7 | b[4] >> 1
        yield TsPacket(i, pid, pkt[3] & 0x0F, bool(afc & 1), pts, pcr)


class TsSplicer:
    """Joins the MPEG-TS of successive ffmpeg runs into one stream: it forwards whole
    packets only, continues each PID's continuity counter, and picks each run's
    `-output_ts_offset` so its first PTS follows the last one sent. An ffmpeg run's first
    PTS varies by about a frame, so the offset leaves two frames of room. Bytes that do
    not start with a sync byte pass through unchanged."""

    def __init__(self, fps=FPS):
        self.frame = 1.0 / fps
        self.cc = {}
        self.base = self.last = None      # PTS seconds minus the offset of their run; last PTS sent
        self.offset = 0.0
        self.pending, self.raw = b"", None

    def start(self, offset):
        """A new ffmpeg run with this offset begins; the old run's partial packet is dropped."""
        self.offset, self.pending, self.raw = offset, b"", None

    def next_offset(self, elapsed):
        """The offset for the next run: right after the last PTS, or the wall clock after a gap."""
        if self.last is None:
            return elapsed
        return max(self.last - self.base + 2 * self.frame, elapsed)

    def feed(self, chunk):
        if self.raw is None and chunk:
            self.raw = chunk[0] != 0x47
        if self.raw:
            return chunk
        data = self.pending + chunk
        cut = len(data) - len(data) % TS
        self.pending = data[cut:]
        out = bytearray(data[:cut])
        for p in parse_ts(out):
            if p.pid == 0x1FFF:
                continue
            if p.pts is not None:
                seconds = p.pts / 90000
                run_base = seconds - self.offset
                self.base = run_base if self.base is None else min(self.base, run_base)
                self.last = seconds if self.last is None else max(self.last, seconds)
            prev = self.cc.get(p.pid)
            cc = p.cc if prev is None else ((prev + 1) % 16 if p.payload else prev)
            self.cc[p.pid] = cc
            out[p.at + 3] = (out[p.at + 3] & 0xF0) | cc
        return bytes(out)


# --- the pull ---

class Pull:
    """One stream read: runs ffmpeg for the channel's current action and copies its bytes
    to `write`, restarting it when the action changes. run() returns the exit code."""

    def __init__(self, directory, name, height, ffmpeg, write, log, started=None, clock=time.time, poll=POLL,
                 stream_timeout=STREAM_TIMEOUT, channel=None):
        self.directory, self.name, self.height, self.ffmpeg = directory, name, height, list(ffmpeg)
        self.write, self.log, self.clock, self.poll = write, log, clock, poll
        self.started = clock() if started is None else started
        self.stream_timeout = stream_timeout
        self.last, self.unreadable = channel, False    # the state the pull was opened with
        self.splicer = TsSplicer()

    def channel(self):
        try:
            self.last = load_channel(self.directory, self.name)
        except (OSError, ValueError):
            if not self.unreadable:
                self.unreadable = True
                self.log("[sim][warning] channel state unreadable, keeping the last one")
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
                since = self.clock()
                while self.action() == "stall":
                    if self.clock() - since >= self.stream_timeout:
                        self.log("error: Error when reading from stream: Read timeout, exiting")
                        return 1
                    tick.wait(self.poll)
                continue
            ch = self.last
            os.makedirs(workdir, exist_ok=True)
            with open(os.path.join(workdir, "label.txt"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(label_text(ch))
            offset = self.splicer.next_offset(max(0.0, self.clock() - self.started))
            self.splicer.start(offset)
            argv = ffmpeg_argv(self.ffmpeg, act, self.height, ch["code"], ch.get("stint"), text, offset)
            self.log(f"[sim][info] {act} from {offset:.3f} s")
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
                wrote = True
                data = self.splicer.feed(chunk)
                try:
                    if data:
                        self.write(data)
                except OSError:
                    broken = True
                    break
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
    name = channel_name(url[len("sim://"):]) if url.startswith("sim://") else None
    if name is None:
        err.write(f"ERROR: [sim] the simulation stand-in serves only sim:// channels, not {url}\n")
        return 1
    if o["extract_audio"] or o["output"]:
        err.write(f"ERROR: [sim] {url}: a simulated stream cannot be downloaded\n")
        return 1
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


def listen(port):
    """A socket listening on 127.0.0.1:port that no other process can share, so a busy port
    fails at once as with the real tool. Windows needs SO_EXCLUSIVEADDRUSE for that."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
    except OSError:
        sock.close()
        raise
    return sock


def _request_line(conn):
    data = b""
    while b"\r\n\r\n" not in data and len(data) < 65536:
        chunk = conn.recv(4096)
        if not chunk:
            return None
        data += chunk
    return data.split(b"\r\n", 1)[0]


def serve_http(directory, name, height, ffmpeg, port, log, clock=time.time, stream_timeout=STREAM_TIMEOUT,
               stop=None):
    """--player-external-http: one player at a time on 127.0.0.1:port, each with a fresh
    pull; after a stream ends the server waits for the next player, as streamlink does.
    Runs until killed or `stop` is set."""
    try:
        sock = listen(port)
    except OSError as e:
        log(f"error: Failed to start HTTP server: {e}")
        return 1
    stop = stop or threading.Event()
    log("[cli][info] Starting server, access with one of:")
    log(f"[cli][info]  http://127.0.0.1:{sock.getsockname()[1]}/")
    sock.settimeout(POLL)
    try:
        while not stop.is_set():
            try:
                conn, _addr = sock.accept()
            except TimeoutError:
                continue
            with conn:
                _serve_player(conn, directory, name, height, ffmpeg, log, clock, stream_timeout)
    finally:
        sock.close()
    return 0


def _serve_player(conn, directory, name, height, ffmpeg, log, clock, stream_timeout):
    conn.settimeout(PLAYER_TIMEOUT)
    try:
        if _request_line(conn) is None:
            return
        ch = load_channel(directory, name)
    except (OSError, ValueError):
        return  # the player went away, or the state is unreadable: wait for the next one
    if not ch or ch.get("status") != "live":
        log(f"error: No playable streams found on this URL: {resolved_url(name, height)}")
        return
    log("[cli][info] Got HTTP request from player")
    try:
        conn.sendall(b"HTTP/1.0 200 OK\r\nServer: Streamlink\r\nContent-Type: video/unknown\r\n\r\n")
    except OSError:
        return
    Pull(directory, name, height, ffmpeg, conn.sendall, log, clock=clock, stream_timeout=stream_timeout,
         channel=ch).run()
    log("[cli][info] Player closed or stream ended, waiting for the next player")


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
    cfg = _config(directory)
    ffmpeg, timeout = cfg.get("ffmpeg") or ["ffmpeg"], float(cfg.get("stream_timeout", STREAM_TIMEOUT))
    if o["http"]:
        return serve_http(directory, name, height, ffmpeg, o["http_port"] or 0, log, stream_timeout=timeout)
    stdout = out.buffer

    def write(chunk):
        try:
            stdout.write(chunk)
            stdout.flush()
        except OSError:
            os.dup2(os.open(os.devnull, os.O_WRONLY), stdout.fileno())   # no flush error at exit
            raise
    return Pull(directory, name, height, ffmpeg, write, log, stream_timeout=timeout, channel=ch).run()


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
    inst.add_argument("--stream-timeout", type=float, default=STREAM_TIMEOUT,
                      help="seconds a stalled pull waits for a byte before it gives up (default: 60)")
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
        stub = StreamStub(a.directory, ffmpeg=[a.ffmpeg], stream_timeout=a.stream_timeout).install()
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

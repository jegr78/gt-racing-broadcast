#!/usr/bin/env python3
"""Simulation mode: `RACECAST_SIMULATION=1` or `racecast --simulation`.

A rehearsal of the product against simulated parties. Event start, stop and takeover
skip OBS, Discord, Companion, Tailscale and the Funnel; OBS WebSocket, the Sheet, the
Sheet push webhook, Discord webhooks and stream pulls come from the `RACECAST_SIM_*`
loopback endpoints below. An unset or non-loopback endpoint is off, never the real
one, and `install_network_guard` refuses every other outbound connection. Code that
calls the C-level `_socket.socket` directly bypasses that guard and is out of scope.

Stdlib only: the relay and the standalone asset scripts import it too."""
import ipaddress
import os
import re
import shutil
import socket
import subprocess
import sys
from urllib.parse import quote, urlparse

FLAG = "RACECAST_SIMULATION"
OBS_WS = "RACECAST_SIM_OBS_WS"                      # ws://127.0.0.1:<port>
SHEET_URL = "RACECAST_SIM_SHEET_URL"                # stands in for https://docs.google.com/spreadsheets/d/<id>
SHEET_PUSH_URL = "RACECAST_SIM_SHEET_PUSH_URL"      # stands in for the league SHEET_PUSH_URL
DISCORD_WEBHOOK_URL = "RACECAST_SIM_DISCORD_WEBHOOK_URL"
STREAM_BIN = "RACECAST_SIM_STREAM_BIN"              # directory with yt-dlp and streamlink stand-ins
FORCE = "RACECAST_SIM_FORCE"                        # the one profile a --force run allowed

SCHEMES = {OBS_WS: ("ws",), SHEET_URL: ("http",), SHEET_PUSH_URL: ("http",),
           DISCORD_WEBHOOK_URL: ("http",)}
REQUIRED = (SHEET_URL, STREAM_BIN)
STREAM_TOOLS = ("yt-dlp", "streamlink")
GOOGLE_SHEET = "https://docs.google.com/spreadsheets/d/"
DEMO_PROFILE = "demo"
PROFILE_MARK = "SIMULATION"                         # profile.env key of a tool-made profile
SKIPPED = "skipped (simulation)"
CHANNEL_RE = re.compile(r"^sim://[A-Za-z0-9_-]{1,64}$")
_TRUE = ("1", "true", "yes", "on")
_FALSE = ("", "0", "false", "no", "off")


class OutboundRefused(ConnectionRefusedError):
    """A non-loopback connection attempted in simulation mode."""


def active(environ=None):
    environ = os.environ if environ is None else environ
    return (environ.get(FLAG) or "").strip().lower() in _TRUE


def flag_problem(environ=None):
    """Why the RACECAST_SIMULATION value is unusable, or "". A mistyped value must not
    quietly mean a real run."""
    environ = os.environ if environ is None else environ
    value = (environ.get(FLAG) or "").strip().lower()
    if value in _TRUE or value in _FALSE:
        return ""
    return (f"{FLAG}={environ.get(FLAG)!r} is neither on (1, true, yes, on) "
            f"nor off (0, false, no, off)")


def skipped(what):
    return f"{what}: {SKIPPED}."


def is_loopback_host(host):
    if not host:
        return False
    h = str(host).strip().strip("[]").lower()
    if h == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(h.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return ip.is_loopback or bool(mapped and mapped.is_loopback)


def _checked(var, value):
    """(url, problem): the value when it is a loopback URL of the right scheme."""
    try:
        u = urlparse(value)
        u.port   # noqa: B018  raises on a malformed port
    except ValueError:
        return "", f"{var} is not a URL"
    if u.scheme not in SCHEMES[var]:
        return "", f"{var} must be a {'/'.join(SCHEMES[var])}:// URL"
    if "@" in u.netloc or u.fragment:
        return "", f"{var} must not carry user info or a fragment"
    if not is_loopback_host(u.hostname):
        return "", f"{var} must point at 127.0.0.1 or localhost"
    return value, ""


def endpoint(var, environ=None):
    """The validated loopback URL in `var`, or "" when unset or invalid."""
    environ = os.environ if environ is None else environ
    value = (environ.get(var) or "").strip()
    return _checked(var, value)[0] if value else ""


def sheet_base(sheet_id, environ=None):
    """The origin of every Sheet CSV URL: Google, or the simulated Sheet in simulation.
    Callers append the unchanged `/gviz/tq?...` path."""
    environ = os.environ if environ is None else environ
    if not active(environ):
        return f"{GOOGLE_SHEET}{sheet_id}"
    return endpoint(SHEET_URL, environ).rstrip("/")


def sheet_csv_url(sheet_id, tab, environ=None, headers=False):
    head = "&headers=1" if headers else ""
    return f"{sheet_base(sheet_id, environ)}/gviz/tq?tqx=out:csv{head}&sheet={quote(tab)}"


def obs_target(environ=None):
    """(host, port) of the simulated OBS WebSocket, or None: OBS is off."""
    url = endpoint(OBS_WS, environ)
    if not url:
        return None
    u = urlparse(url)
    return (u.hostname, u.port) if u.port else None


def stand_in_present(path, tool, windows=None):
    """Whether `path` holds a stand-in that a bare `tool` call resolves to. Windows only
    launches an `.exe` by bare name; a `.bat` there would fall through to the real tool."""
    windows = os.name == "nt" if windows is None else windows
    if windows:
        return os.path.isfile(os.path.join(path, tool + ".exe"))
    return bool(shutil.which(tool, path=path))


def _stream_bin_problem(path):
    if not path:
        return f"{STREAM_BIN} is not set (a directory holding stand-ins for yt-dlp and streamlink)"
    if not os.path.isdir(path):
        return f"{STREAM_BIN} is not a directory: {path}"
    missing = [t for t in STREAM_TOOLS if not stand_in_present(path, t)]
    return f"{STREAM_BIN} has no {', '.join(missing)}" if missing else ""


def problems(environ=None):
    """Why simulation mode cannot run with this environment; [] outside the mode."""
    environ = os.environ if environ is None else environ
    if flag_problem(environ):
        return [flag_problem(environ)]
    if not active(environ):
        return []
    out = []
    for var in SCHEMES:
        value = (environ.get(var) or "").strip()
        if value:
            problem = _checked(var, value)[1]
            if problem:
                out.append(problem)
        elif var in REQUIRED:
            out.append(f"{var} is not set")
    stream = _stream_bin_problem((environ.get(STREAM_BIN) or "").strip())
    if stream:
        out.append(stream)
    return out


def with_stream_bin(environ):
    """A copy of `environ` whose PATH starts with the stream stand-ins in simulation."""
    env = dict(environ)
    path = (env.get(STREAM_BIN) or "").strip()
    if not (active(env) and path):
        return env
    parts = env.get("PATH", "").split(os.pathsep) if env.get("PATH") else []
    if parts[:1] != [path]:
        env["PATH"] = os.pathsep.join([path] + parts)
    return env


def config_overrides(environ=None):
    """ResolvedConfig field -> value for simulation: the simulated webhooks, and every
    other league endpoint (Discord OAuth and voice, the timing host) blank."""
    environ = os.environ if environ is None else environ
    if not active(environ):
        return {}
    return {"sheet_push_url": endpoint(SHEET_PUSH_URL, environ),
            "discord_webhook_url": endpoint(DISCORD_WEBHOOK_URL, environ),
            "discord_client_id": "", "discord_client_secret": "", "discord_voice_url": "",
            "timing_host_account_id": "", "timing_host_api_token": "",
            "timing_host_publish_key": "", "timing_host_url": ""}


def env_overrides(environ=None):
    """The league env vars a child process reads, set to their simulated value or blank,
    whichever layer (profile, machine .env, shell) set them."""
    environ = os.environ if environ is None else environ
    if not active(environ):
        return {}
    return {"RACECAST_SHEET_PUSH_URL": endpoint(SHEET_PUSH_URL, environ),
            "RACECAST_DISCORD_WEBHOOK_URL": endpoint(DISCORD_WEBHOOK_URL, environ),
            "RACECAST_DISCORD_CLIENT_ID": "", "RACECAST_DISCORD_CLIENT_SECRET": "",
            "RACECAST_DISCORD_VOICE_URL": "", "RACECAST_TIMING_HOST_URL": "",
            "RACECAST_GCLOUD_TTS_KEY": "", "RACECAST_GCLOUD_STT_KEY": "",
            "RACECAST_TS_API_KEY": ""}


def profile_refusal(name, values, forced=None):
    """None when simulation may run on profile `name`, else the reason. Allowed: the
    demo profile, a profile whose profile.env sets SIMULATION=1, and the profile a
    --force run named."""
    if not name or name == DEMO_PROFILE or name == forced:
        return None
    if (values.get(PROFILE_MARK) or "").strip().lower() in _TRUE:
        return None
    return (f"simulation mode runs on the '{DEMO_PROFILE}' profile or a profile with "
            f"{PROFILE_MARK}=1, not on '{name}'. Run `racecast --simulation --force ...` to "
            f"use '{name}' anyway.")


def is_channel(value, environ=None):
    """A `sim://<name>` stream address, accepted only in simulation mode."""
    return active(environ) and isinstance(value, str) and bool(CHANNEL_RE.match(value.strip()))


def process_started(pid):
    """An opaque start marker for `pid` so a reused pid is not mistaken for it, or None
    where the OS gives none (Windows) or the process is gone."""
    try:
        if sys.platform.startswith("linux"):
            with open(f"/proc/{int(pid)}/stat", encoding="utf-8", errors="replace") as fh:
                return fh.read().rsplit(")", 1)[1].split()[19]
        if sys.platform == "darwin":
            out = subprocess.run(["ps", "-o", "lstart=", "-p", str(int(pid))],
                                 capture_output=True, text=True, errors="replace", timeout=5)
            return out.stdout.strip() or None
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None
    return None


def guarded_argv(python, script, args):
    """argv that runs `script` in a new Python process with the network guard in place."""
    boot = ("import os, runpy, sys; script = sys.argv[2]; "
            "sys.path[0] = os.path.dirname(os.path.abspath(script)); "
            "sys.path.insert(1, sys.argv[1]); import simulation; "
            "simulation.install_network_guard(); sys.argv = sys.argv[2:]; "
            "runpy.run_path(script, run_name='__main__')")
    return [python, "-c", boot, os.path.dirname(os.path.abspath(__file__)), script, *args]


GUARDED = ((socket.socket, "connect"), (socket.socket, "connect_ex"),
           (socket.socket, "sendto"), (socket.socket, "sendmsg"), (socket, "getaddrinfo"),
           (socket, "gethostbyname"), (socket, "gethostbyname_ex"),
           (socket, "gethostbyaddr"))   # getfqdn resolves through gethostbyaddr
GUARDED = tuple((owner, name) for owner, name in GUARDED if hasattr(owner, name))   # no sendmsg on Windows
_original_getaddrinfo = socket.getaddrinfo


def loopback_name_ok(host, lookup=None):
    """A literal loopback address, or `localhost` when every address it resolves to
    is loopback (a hosts file may point it elsewhere)."""
    name = host.decode() if isinstance(host, bytes) else host
    if not name:
        return False
    if str(name).strip("[]").lower() != "localhost":
        return is_loopback_host(name)
    try:
        infos = (lookup or _original_getaddrinfo)("localhost", None)
    except OSError:
        return False
    return bool(infos) and all(is_loopback_host(info[4][0]) for info in infos)


def guard_python_argv(argv, environ=None):
    """In simulation, `[python, script.py, ...]` becomes the guarded form; anything else
    (a native tool, a frozen entrypoint that installs the guard itself) is unchanged."""
    argv = list(argv)
    if (active(environ) and len(argv) >= 2 and argv[0] == sys.executable
            and str(argv[1]).endswith(".py")):
        return guarded_argv(argv[0], str(argv[1]), argv[2:])
    return argv


def _refuse(host, what="connection to"):
    if not loopback_name_ok(host):
        shown = host.decode() if isinstance(host, bytes) else host
        raise OutboundRefused(f"simulation mode: {what} {shown} refused")


def _refuse_address(address):
    if isinstance(address, tuple) and address:
        _refuse(address[0])


def install_network_guard(environ=None):
    """In simulation, make this process refuse every non-loopback socket connection,
    datagram and name lookup. Returns whether the guard is in place."""
    if not active(environ):
        return False
    if getattr(socket, "_racecast_sim_guard", False):
        return True
    orig = {name: getattr(owner, name) for owner, name in GUARDED}

    def connect(self, address):
        _refuse_address(address)
        return orig["connect"](self, address)

    def connect_ex(self, address):
        _refuse_address(address)
        return orig["connect_ex"](self, address)

    def sendto(self, data, *args):
        _refuse_address(args[-1] if args else None)
        return orig["sendto"](self, data, *args)

    def sendmsg(self, buffers, *args, **kwargs):
        _refuse_address(args[2] if len(args) > 2 else kwargs.get("address"))
        return orig["sendmsg"](self, buffers, *args, **kwargs)

    def getaddrinfo(host, *args, **kwargs):
        if host is not None:
            _refuse(host, "lookup of")
        return orig["getaddrinfo"](host, *args, **kwargs)

    def gethostbyname(name):
        _refuse(name, "lookup of")
        return orig["gethostbyname"](name)

    def gethostbyname_ex(name):
        _refuse(name, "lookup of")
        return orig["gethostbyname_ex"](name)

    def gethostbyaddr(address):
        _refuse(address, "reverse lookup of")
        return orig["gethostbyaddr"](address)

    replacements = {"connect": connect, "connect_ex": connect_ex, "sendto": sendto,
                    "sendmsg": sendmsg, "getaddrinfo": getaddrinfo,
                    "gethostbyname": gethostbyname, "gethostbyname_ex": gethostbyname_ex,
                    "gethostbyaddr": gethostbyaddr}
    for owner, name in GUARDED:
        setattr(owner, name, replacements[name])
    socket._racecast_sim_guard = True
    return True

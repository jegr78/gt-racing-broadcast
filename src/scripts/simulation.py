#!/usr/bin/env python3
"""Simulation mode: `RACECAST_SIMULATION=1` or `racecast --simulation`.

A rehearsal of the product against simulated parties. Event start, stop and takeover
skip OBS, Discord, Companion, Tailscale and the Funnel; OBS WebSocket, the Sheet, the
Sheet push webhook, Discord webhooks and stream pulls come from the `RACECAST_SIM_*`
loopback endpoints below. An unset or non-loopback endpoint is off, never the real
one, and `install_network_guard` refuses every other outbound connection.

Stdlib only: the relay and the standalone asset scripts import it too."""
import ipaddress
import os
import re
import shutil
import socket
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


class OutboundRefused(ConnectionRefusedError):
    """A non-loopback connection attempted in simulation mode."""


def active(environ=None):
    environ = os.environ if environ is None else environ
    return (environ.get(FLAG) or "").strip().lower() in _TRUE


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
            "RACECAST_DISCORD_VOICE_URL": "", "RACECAST_TIMING_HOST_URL": ""}


def profile_refusal(name, values, forced=None):
    """None when simulation may run on profile `name`, else the reason. Allowed: the
    demo profile, a profile whose profile.env sets SIMULATION=1, and the profile a
    --force run named."""
    if not name or name == DEMO_PROFILE or name == forced:
        return None
    if (values.get(PROFILE_MARK) or "").strip().lower() in _TRUE:
        return None
    return (f"simulation mode runs on the '{DEMO_PROFILE}' profile or a profile with "
            f"{PROFILE_MARK}=1, not on '{name}'. Pass --force to run it on '{name}' anyway.")


def is_channel(value, environ=None):
    """A `sim://<name>` stream address, accepted only in simulation mode."""
    return active(environ) and isinstance(value, str) and bool(CHANNEL_RE.match(value.strip()))


def guarded_argv(python, script, args):
    """argv that runs `script` in a new Python process with the network guard in place."""
    boot = ("import os, runpy, sys; script = sys.argv[2]; "
            "sys.path[0] = os.path.dirname(os.path.abspath(script)); "
            "sys.path.insert(1, sys.argv[1]); import simulation; "
            "simulation.install_network_guard(); sys.argv = sys.argv[2:]; "
            "runpy.run_path(script, run_name='__main__')")
    return [python, "-c", boot, os.path.dirname(os.path.abspath(__file__)), script, *args]


def _refuse(address):
    if isinstance(address, tuple) and address and not is_loopback_host(address[0]):
        raise OutboundRefused(f"simulation mode: connection to {address[0]} refused")


def install_network_guard(environ=None):
    """In simulation, make this process refuse every non-loopback socket connection,
    datagram and name lookup. Returns whether the guard is in place."""
    if not active(environ):
        return False
    if getattr(socket, "_racecast_sim_guard", False):
        return True
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex
    sendto, getaddrinfo = socket.socket.sendto, socket.getaddrinfo

    def guarded_connect(self, address):
        _refuse(address)
        return connect(self, address)

    def guarded_connect_ex(self, address):
        _refuse(address)
        return connect_ex(self, address)

    def guarded_sendto(self, data, *args):
        _refuse(args[-1] if args else None)
        return sendto(self, data, *args)

    def guarded_getaddrinfo(host, *args, **kwargs):
        name = host.decode() if isinstance(host, bytes) else host
        if name is not None and not is_loopback_host(name):
            raise OutboundRefused(f"simulation mode: lookup of {name} refused")
        return getaddrinfo(host, *args, **kwargs)

    socket.socket.connect, socket.socket.connect_ex = guarded_connect, guarded_connect_ex
    socket.socket.sendto, socket.getaddrinfo = guarded_sendto, guarded_getaddrinfo
    socket._racecast_sim_guard = True
    return True

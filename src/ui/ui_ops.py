"""Control Center operation registry: which `racecast` invocations the web UI may
trigger, and how to build the child argv. The UI server routes /api/op/<name>
through this table and build_argv() only, so the HTTP surface can never run
arbitrary commands or pass free-form args."""
import re
import math

# name -> base racecast argv. Installs always run with --yes: jobs have no stdin
# (DEVNULL), so an interactive prompt would silently read EOF and decline.
OPS = {
    "relay-start": ["relay", "start"],
    "relay-stop": ["relay", "stop"],
    "relay-restart": ["relay", "restart"],
    "companion-start": ["companion", "start"],
    "companion-stop": ["companion", "stop"],
    "companion-restart": ["companion", "restart"],
    "streams-start": ["streams", "start"],
    "streams-stop": ["streams", "stop"],
    "streams-restart": ["streams", "restart"],
    "relay-open-panel": ["relay", "open-panel"],
    "relay-open-hud": ["relay", "open-hud"],
    "relay-open-status": ["relay", "open-status"],
    "companion-open-buttons": ["companion", "open-buttons"],
    "companion-open-admin": ["companion", "open-admin"],
    "tailscale-up": ["tailscale", "up"],
    "tailscale-down": ["tailscale", "down"],
    "tailscale-start": ["app", "launch", "tailscale"],
    "tailscale-stop": ["app", "quit", "tailscale"],
    "obs-start": ["app", "launch", "obs"],
    "obs-stop": ["app", "quit", "obs"],
    "discord-start": ["app", "launch", "discord"],
    "discord-stop": ["app", "quit", "discord"],
    "discord-voice-join": ["discord", "join"],
    "discord-voice-leave": ["discord", "leave"],
    "obs-refresh": ["obs", "refresh"],
    "obs-benchmark": ["obs", "benchmark"],
    "smoketest": ["smoketest"],
    "companion-enable-control": ["companion", "enable-control"],
    "obs-browser": ["obs-browser", "--yes"],
    "obs-collection-set": ["obs", "collection", "set"],
    "event-start": ["event", "start"],
    "event-stop": ["event", "stop"],
    "event-takeover": ["event", "takeover"],   # ip (+ optional stint) appended via PARAMS
    "free-ports": ["freeport"],   # kill orphaned holders of the feed ports (53001-53003)
    "kill-relay": ["freeport", "--force", "8088", "53001", "53002", "53003"],   # recovers a stale relay the Stop button cannot reach
    "cookies": ["cookies"],
    "cookies-twitch": ["cookies", "twitch"],
    "graphics": ["graphics"],
    "media": ["media"],
    "graphics-metadata": ["graphics", "--metadata-only"],
    "media-metadata": ["media", "--metadata-only"],
    "brands": ["brands"],
    "setup": ["setup"],
    "preflight": ["preflight"],
    "speedtest": ["speedtest"],
    "export-companion": ["export", "companion"],
    "install-tools": ["install-tools", "--yes"],
    "install-apps": ["install-apps", "--yes"],
    "update": ["update", "--yes"],   # optional `tag` param installs a preview build
    "chat-clear": ["chat", "clear"],
    "chat-pull": ["chat", "pull"],
    "chat-import": ["chat", "import"],
    "chat-export": ["chat", "export"],
    "health-export": ["health", "export"],
    "health-import": ["health", "import"],
    "health-pull": ["health", "pull"],
    "console-pull-versions": ["console", "pull-versions"],
    "console-setup-funnel": ["console", "setup-funnel"],
    "console-apply-funnel": ["console", "setup-funnel", "--apply"],
    "telemetry-record-start": ["telemetry", "record", "start"],
    "telemetry-record-stop": ["telemetry", "record", "stop"],
    "telemetry-record-status": ["telemetry", "record", "status"],
    "gt7-data-update": ["gt7-data", "update"],
    "radio-provision": ["radio", "provision"],
    "radio-kokoro-install": ["radio", "provision", "--kokoro"],
    "radio-kokoro-smoke": ["radio", "smoke", "--kokoro"],
    "radio-smoke": ["radio", "smoke", "--allow-cloud"],   # the button click is the consent to the billed calls
    "telemetry-replay-start": ["telemetry", "replay", "start"],
    "telemetry-replay-stop": ["telemetry", "replay", "stop"],
    "telemetry-replay-status": ["telemetry", "replay", "status"],
    "telemetry-import": ["telemetry", "import"],
    "telemetry-export": ["telemetry", "export"],
    "telemetry-index": ["telemetry", "index"],
    "shift-reference-update": ["telemetry", "shift-update"],
    "report-generate": ["report", "generate"],   # builds lap indexes, so never in a request
}

# Freeze the broadcast profile before starting operations that transfer or write
# its data. The client must echo that selection; a stale view never starts work.
PROFILE_OPS=frozenset({'chat-clear','chat-pull','chat-import','chat-export',
    'health-export','health-import','health-pull','console-pull-versions',
    'telemetry-record-start','telemetry-record-stop','telemetry-record-status',
    'obs-benchmark','streams-restart','smoketest'})

# Browsers get-cookies can export from (yt-dlp --cookies-from-browser names).
BROWSERS = ("firefox", "chrome", "edge", "brave", "safari")


def _car_arg(value):
    s = str(value)
    if not s.isascii() or not s.isdigit() or not 0 < int(s) <= 9999999:
        raise ValueError('car ID must be a positive integer in range')
    return [str(int(s))]


def _browser_arg(value):
    if value not in BROWSERS:
        raise ValueError(f"browser must be one of: {', '.join(BROWSERS)}")
    return [value]


def _stint_arg(value):
    s = str(value)
    if not s.isascii() or not s.isdigit() or int(s) < 1:
        raise ValueError("stint must be a 1-based stint number")
    return ["--stint", s]


# A Tailscale IP or MagicDNS host for the takeover target. Charset-locked so the
# value (from the device dropdown, or manual entry) can never inject argv tokens.
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-]{1,253}\Z")


def _ip_arg(value):
    s = str(value)
    if s.startswith('-') or not _HOST_RE.match(s):
        raise ValueError("invalid host/IP")
    return [s]


def _update_flag(value):
    return ["--update"] if value else []


def _whisper_flag(value):
    return ["--whisper"] if value else []


def _qualifying_flag(value):
    """`--qualifying` starts the event in qualifying mode (Feed A serves the
    Qualifying tab; the Parts control operates on the Q broadcast part)."""
    return ["--qualifying"] if value else []


def _funnel_flag(value):
    """`--funnel` switches `event takeover` to pull A's handover state over the
    public Tailscale Funnel (host is A's MagicDNS name) instead of the tailnet IP.
    The CLI reads the per-league CONSOLE_SECRET it steps up with server-side from
    the active profile; the UI never collects or sends it."""
    return ["--funnel"] if value else []


# The UI's `update` op only ever installs a preview build by tag; a regular update
# sends no tag and goes to the latest release. Restricting the allowlist to
# preview-* means a crafted /api/op/update {tag: "v1.0.0"} cannot silently
# downgrade to an arbitrary stable release. `racecast update --tag` on the CLI may
# still pin or downgrade; that boundary is the shell, not the UI.
_TAG_RE = re.compile(r"^preview-[\w.-]+\Z")


def _tag_arg(value):
    """A preview release tag the UI may install. Allowlist: preview-* only.
    Defends against argv junk and stable-tag downgrades: the UI only ever sends a
    tag it got from /api/previews, which lists prereleases."""
    s = str(value)
    if not _TAG_RE.match(s):
        raise ValueError(f"invalid preview tag: {value!r}")
    return ["--tag", s]


def _file_arg(value):
    """A local file path the operator typed for `health import`, returned as a
    positional arg. Empty, control chars and a leading '-' are rejected, so a path
    like '--out' can never reach the child as an option."""
    s = str(value).strip()
    if not s or s.startswith("-") or any(ord(c) < 0x20 for c in s):
        raise ValueError(f"invalid file path: {value!r}")
    return [s]


def _recording_arg(value):
    name = str(value)
    if not name.endswith(".gt7rec") or "/" in name or "\\" in name:
        raise ValueError("Select a finalized recording")
    return _file_arg(name)


def _number_arg(flag, value, minimum, maximum):
    number = float(value)
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise ValueError("Replay option outside allowed range")
    return [flag, str(number)]


def _integer_arg(flag,value,minimum,maximum):
    text=str(value)
    if not text.isascii() or not text.isdigit() or not minimum<=int(text)<=maximum:
        raise ValueError(f'{flag} must be a whole number from {minimum} to {maximum}')
    return [flag,str(int(text))]


# op name -> {param name: validator(value) -> argv fragment}. Ops absent here
# accept no parameters at all.
PARAMS = {
    "smoketest":{"minutes":lambda v:_integer_arg("--minutes",v,1,120)},
    "obs-benchmark":{"window":lambda v:_integer_arg('--window',v,10,600),
                     "settle":lambda v:_integer_arg('--settle',v,3,120)},
    "chat-pull":{"ip":_ip_arg,"port":lambda v:_integer_arg('--port',v,1,65535)},
    "health-pull":{"ip":_ip_arg,"port":lambda v:_integer_arg('--port',v,1,65535)},
    "console-pull-versions":{"ip":_ip_arg,"port":lambda v:_integer_arg('--port',v,1,65535)},
    "chat-import":{"file":_file_arg},
    "chat-export":{"out":lambda v:['--out',*_file_arg(v)]},
    "telemetry-replay-start": {"name": _recording_arg,
        "speed": lambda v: _number_arg("--speed", v, .001, 100),
        "from": lambda v: _number_arg("--from", v, 0, 10080),
        "on_air": lambda v: ["--on-air"] if v is True else []},
    "telemetry-import": {"file": _file_arg},
    "telemetry-export": {"name": _recording_arg},
    "shift-reference-update": {"car_id": _car_arg},
    "cookies": {"browser": _browser_arg},
    "cookies-twitch": {"browser": _browser_arg},
    "event-start": {"stint": _stint_arg, "qualifying": _qualifying_flag},
    # order: ip (positional) then --funnel then --stint
    "event-takeover": {"ip": _ip_arg, "funnel": _funnel_flag, "stint": _stint_arg},
    "install-tools": {"update": _update_flag},
    "install-apps": {"update": _update_flag},
    "update": {"tag": _tag_arg},
    "radio-provision": {"whisper": _whisper_flag},
    "health-import": {"file": _file_arg},
}

# op name -> tuple of param names that must be present (non-empty). Params not
# listed here are optional; build_argv skips an absent or empty one.
REQUIRED = {
    "chat-pull":("ip",),"health-pull":("ip",),"console-pull-versions":("ip",),
    "chat-import":("file",),
    "telemetry-replay-start": ("name",),
    "telemetry-import": ("file",),
    "telemetry-export": ("name",),
    "shift-reference-update": ("car_id",),
    "health-import": ("file",),
}


def build_argv(name, params=None):
    """Base argv + validated optional params. Raises ValueError on an unknown
    op, an unknown param, or an invalid value. Empty-string/None values are
    treated as 'not provided' (the UI sends blank inputs as empty strings)."""
    if name not in OPS:
        raise ValueError(f"unknown operation: {name}")
    argv = list(OPS[name])
    spec = PARAMS.get(name, {})
    if params is None:
        params = {}
    unknown = set(params) - set(spec)
    if unknown:
        raise ValueError(f"unexpected parameter(s): {', '.join(sorted(unknown))}")
    missing = [k for k in REQUIRED.get(name, ()) if not params.get(k)]
    if missing:
        raise ValueError(f"missing required parameter(s): {', '.join(missing)}")
    for key, validate in spec.items():
        if key in params and params[key] not in (None, ""):
            argv += validate(params[key])
    return argv


def job_argv(op_args, frozen, executable, rc_script):
    """argv to run `racecast <op_args...>` as a child process: the frozen binary
    re-invokes itself (same mechanism as the daemon spawns); repo/package mode
    runs racecast.py with this interpreter."""
    if frozen:
        return [executable] + list(op_args)
    return [executable, rc_script] + list(op_args)

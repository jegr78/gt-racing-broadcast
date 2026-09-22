"""Control Center operation registry: which `racecast` invocations the web UI may
trigger, and how to build the child argv. Pure data + pure helpers (no I/O) —
the UI server routes /api/op/<name> through this table and build_argv() only,
so the HTTP surface can never run arbitrary commands or pass free-form args."""
import re

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
    "obs-collection-set": ["obs", "collection", "set"],
    "event-start": ["event", "start"],
    "event-stop": ["event", "stop"],
    "event-takeover": ["event", "takeover"],   # ip (+ optional stint) appended via PARAMS
    "free-ports": ["freeport"],   # kill orphaned holders of the feed ports (53001-53003)
    "kill-relay": ["freeport", "--force", "8088", "53001", "53002", "53003"],   # force-free the relay control + feed ports: recover a stale/orphaned relay the Stop button can't reach
    "cookies": ["cookies"],
    "cookies-twitch": ["cookies", "twitch"],
    "graphics": ["graphics"],
    "media": ["media"],
    "brands": ["brands"],
    "setup": ["setup"],
    "preflight": ["preflight"],
    "speedtest": ["speedtest"],
    "export-companion": ["export", "companion"],
    "install-tools": ["install-tools", "--yes"],
    "install-apps": ["install-apps", "--yes"],
    "update": ["update", "--yes"],   # optional `tag` param installs a preview build
    "chat-clear": ["chat", "clear"],
    "health-export": ["health", "export"],
    "health-import": ["health", "import"],
}

# Browsers get-cookies can export from (yt-dlp --cookies-from-browser names).
BROWSERS = ("firefox", "chrome", "edge", "brave", "safari")


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
    if not _HOST_RE.match(s):
        raise ValueError("invalid host/IP")
    return [s]


def _update_flag(value):
    return ["--update"] if value else []


def _qualifying_flag(value):
    """`--qualifying` starts the event in qualifying mode (Feed A serves the
    Qualifying tab; the Parts control operates on the Q broadcast part)."""
    return ["--qualifying"] if value else []


def _funnel_flag(value):
    """`--funnel` switches `event takeover` to pull A's handover state over the
    public Tailscale Funnel (host is A's MagicDNS name) instead of the tailnet IP.
    The per-league CONSOLE_SECRET it steps up with is read server-side from the
    active profile by the CLI — it is never collected or sent from the UI."""
    return ["--funnel"] if value else []


# The UI's `update` op only ever installs a PREVIEW build by tag (a regular
# update sends no tag and goes to the latest release). Restricting the allowlist
# to preview-* means a crafted /api/op/update {tag: "v1.0.0"} cannot silently
# downgrade to an arbitrary stable release. (`racecast update --tag <vX.Y.Z>` on the
# CLI is still free to pin/downgrade — that boundary is the shell, not the UI.)
_TAG_RE = re.compile(r"^preview-[\w.-]+\Z")


def _tag_arg(value):
    """A preview release tag the UI may install. Allowlist: preview-* only.
    Defends against argv junk and stable-tag downgrades (the UI only ever sends
    a tag it got from /api/previews, which lists prereleases)."""
    s = str(value)
    if not _TAG_RE.match(s):
        raise ValueError(f"invalid preview tag: {value!r}")
    return ["--tag", s]


def _file_arg(value):
    """A local file path the operator typed for `health import`. Reject empty /
    control chars / a leading '-' (argv flag-smuggling: a path like '--out' must
    never reach the child as an option); returned as a positional arg."""
    s = str(value).strip()
    if not s or s.startswith("-") or any(ord(c) < 0x20 for c in s):
        raise ValueError(f"invalid file path: {value!r}")
    return [s]


# op name -> {param name: validator(value) -> argv fragment}. Ops absent here
# accept no parameters at all.
PARAMS = {
    "cookies": {"browser": _browser_arg},
    "cookies-twitch": {"browser": _browser_arg},
    "event-start": {"stint": _stint_arg, "qualifying": _qualifying_flag},
    # order: ip (positional) then --funnel then --stint
    "event-takeover": {"ip": _ip_arg, "funnel": _funnel_flag, "stint": _stint_arg},
    "install-tools": {"update": _update_flag},
    "install-apps": {"update": _update_flag},
    "update": {"tag": _tag_arg},
    "health-import": {"file": _file_arg},
}

# op name -> tuple of param names that must be present (non-empty). Params not
# listed here are optional (the existing build_argv contract: absent/empty = skipped).
REQUIRED = {
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

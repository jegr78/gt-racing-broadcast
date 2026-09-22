"""Shared pure logic for the `racecast companion` adapter (src/racecast.py).

These helpers bind Bitfocus Companion's admin/web-buttons server to this machine's
Tailscale IP (plug & play) so a tablet can open http://<tailscale-ip>:<port>/tablet
over the tailnet, without exposing Companion on the local LAN the way 0.0.0.0 would.

Tailscale detection lives in scripts/tailscale.py; racecast.py passes the detected
IP into desired_bind_ip(). This module holds Companion logic only.

NOTE: this binds *where* Companion listens; it does NOT separate /tablet from the
admin GUI (Companion serves both on one port + one shared socket API). Restrict WHO
reaches the port with a Tailscale ACL.

Platform support: Windows and macOS are automated. Linux is manual by design,
because in WSL/Docker setups Companion runs on the HOST and local automation would
target the wrong machine. Linux users set Companion's bind address manually and
start it themselves.
"""
import json, os, socket

COMPANION_DEFAULT_ADMIN_PORT = 8000


def companion_reachable(host="127.0.0.1", port=COMPANION_DEFAULT_ADMIN_PORT, timeout=1.0):
    """True iff a TCP connection to the Companion admin port succeeds. Best-effort:
    any socket error -> False (Companion not running / wrong port)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def desired_bind_ip(bind_arg, tailscale_ip):
    """The single address Companion should bind. 'auto' -> Tailscale IP when present,
    else 127.0.0.1 (local-only fallback). Any explicit value is taken literally."""
    if bind_arg == "auto":
        return tailscale_ip or "127.0.0.1"
    return bind_arg


def config_with_bind_ip(config_text, new_ip):
    """Companion config.json text with only bind_ip replaced; other keys preserved.
    Raises ValueError on invalid JSON."""
    data = json.loads(config_text)
    data["bind_ip"] = new_ip
    return json.dumps(data, indent=2) + "\n"


def plan_companion_action(current_bind_ip, desired_ip, running):
    """Decide the steps to reach `desired_ip` with Companion running.

    edit       : config.json bind_ip must change.
    stop_first : Companion is running and must stop before we edit config.json.
    start      : Companion must be (re)started at the end.
    """
    edit = current_bind_ip != desired_ip
    return {"edit": edit, "stop_first": edit and running, "start": edit or not running}


def companion_config_path(platform, env=None):
    """Default path to Companion's config.json for the given sys.platform value."""
    env = os.environ if env is None else env
    if platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif platform.startswith("win"):
        base = env.get("APPDATA") or os.path.expanduser("~")
    else:
        base = env.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "companion", "config.json")


# Default install locations of Companion.exe, a heuristic that
# RACECAST_COMPANION_EXE overrides.
WINDOWS_COMPANION_CANDIDATES = (
    r"%LOCALAPPDATA%\Programs\companion\Companion.exe",
    r"C:\Program Files\Companion\Companion.exe",
    r"C:\Program Files (x86)\Companion\Companion.exe",
)


def find_companion_exe(env=None, exists=os.path.exists):
    """Path to Companion.exe on Windows, or None. RACECAST_COMPANION_EXE wins."""
    env = os.environ if env is None else env
    override = env.get("RACECAST_COMPANION_EXE")
    if override:
        return override if exists(override) else None
    for cand in WINDOWS_COMPANION_CANDIDATES:
        path = cand.replace("%LOCALAPPDATA%", env.get("LOCALAPPDATA", ""))
        if not path.startswith("\\") and exists(path):
            return path
    return None


def companion_control_commands(platform, exe=None):
    """Start/quit/running argv per platform. Windows needs the discovered exe.
    Returns None for Linux: native-Linux companion-pi control lives in
    companion_linux.py (systemd), so this module stays Windows/macOS only."""
    if platform == "darwin":
        return {
            "start": ["open", "-a", "Companion"],
            "quit": ["osascript", "-e", 'quit app "Companion"'],
            "running": ["pgrep", "-f", "Companion.app/Contents/Resources/main.js"],
        }
    if platform.startswith("win"):
        if not exe:
            return None
        return {
            "start": [exe],
            "quit": ["taskkill", "/IM", "Companion.exe"],  # graceful WM_CLOSE first
            "running": ["tasklist", "/FI", "IMAGENAME eq Companion.exe"],
        }
    return None


def parse_running(platform, returncode, stdout):
    """Interpret the 'running' probe. tasklist exits 0 even with NO match, so on
    Windows the image name must appear in the output; elsewhere rc==0 suffices."""
    if platform.startswith("win"):
        return "Companion.exe" in stdout
    return returncode == 0

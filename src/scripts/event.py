"""Event-day readiness logic behind `racecast event status|start|stop`.

Building blocks wired by racecast.py: process probes for the GUI apps (OBS,
Discord), per-OS launch commands, asset-completeness checks against the Sheet's
Assets tab, and classifiers turning raw facts into preflight Result lines. Reuses
preflight's Result/report model and install_apps' path candidates. Spec:
docs/superpowers/specs/2026-06-05-event-readiness-design.md.
Tests: tests/test_event.py."""
import csv, glob, io, ntpath, os, shutil, subprocess, sys, time

# Plain sibling imports: scripts/ is on sys.path in repo+package mode (racecast.py
# injects it; standalone tests insert it), and the frozen binary ships these
# as real frozen modules (hidden-imports in tools/build-binary.py).
import install_apps
import preflight
import services

PASS, WARN, FAIL, INFO = preflight.PASS, preflight.WARN, preflight.FAIL, preflight.INFO
Result = preflight.Result

# Process image names per app and OS for the running probe. Only the plain GUI
# apps live here: Tailscale is probed via detect_tailscale_ip(), where connected
# beats running, and Companion via racecast.py's own probe.
PROCESS_NAMES = {
    "obs": {"darwin": ("OBS",), "win": ("obs64.exe",), "linux": ("obs",)},
    "discord": {"darwin": ("Discord",), "win": ("Discord.exe",), "linux": ("Discord",)},
}


def _names(app, platform):
    table = PROCESS_NAMES[app]
    if platform.startswith("win"):
        return table["win"]
    return table["darwin"] if platform == "darwin" else table["linux"]


def probe_command(name, platform):
    """argv that probes whether a process image named `name` is running."""
    if platform.startswith("win"):
        return ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"]
    return ["pgrep", "-x", name]


def parse_probe(platform, returncode, stdout, name):
    """Interpret a probe_command() run. Windows tasklist exits 0 even with no
    match, so the image name must appear in the output. Decode with
    errors='replace': the names matched here are pure ASCII, the same
    OEM-codepage caveat as racecast.py's _companion_running."""
    if platform.startswith("win"):
        return name.lower() in (stdout or "").lower()
    return returncode == 0


def app_running(app, platform=None):
    """True iff one of the app's process names is running. Best-effort: a failing
    probe counts as not running, and a known app key never raises."""
    platform = sys.platform if platform is None else platform
    for name in _names(app, platform):
        try:
            out = subprocess.run(probe_command(name, platform), capture_output=True,
                                 text=True, errors="replace", timeout=5,
                                 **services.no_window_kwargs())
        except (OSError, subprocess.SubprocessError):
            continue
        if parse_probe(platform, out.returncode, out.stdout, name):
            return True
    return False


def wait_until_up(probes, timeout=60, interval=5, clock=time.monotonic,
                  sleep=time.sleep):
    """Poll `probes` ({name: callable -> bool}) until all pass or `timeout`
    seconds elapse; returns {name: bool} with the final state. A probe that
    turned True stays True and is not re-polled. Used by `racecast event start` so
    the closing readiness report does not race the just-launched services. Static
    problems such as missing graphics or stale cookies are deliberately NOT waited
    on, because they never self-heal."""
    deadline = clock() + timeout
    status = {name: False for name in probes}
    while True:
        for name, probe in probes.items():
            if not status[name]:
                status[name] = bool(probe())
        if all(status.values()) or clock() >= deadline:
            return status
        sleep(interval)


# macOS app names for `open -a`. Callers gate on install_apps.app_present first,
# because `open -a` on a missing app errors.
_DARWIN_OPEN_NAMES = {"obs": "OBS", "discord": "Discord", "tailscale": "Tailscale"}
# Windows: the Tailscale tray GUI app. install_apps probes tailscale.exe, the CLI,
# for PRESENCE, but exec'ing the CLI bare does nothing, so launch the GUI.
_WIN_TAILSCALE_GUI = (r"C:\Program Files\Tailscale\tailscale-ipn.exe",)
_LINUX_PATH_NAMES = {"obs": "obs", "discord": "discord"}


def launch_command(app, platform, env=None, exists=os.path.exists, which=shutil.which):
    """(argv, cwd) that launches `app` on `platform`, or None when there is
    nothing to exec: the binary was not found, or the app is Linux tailscale,
    a daemon whose hint is `sudo tailscale up`. Windows obs64.exe must run with
    cwd at its bin directory or it cannot find its bundled resources."""
    env = os.environ if env is None else env
    if platform == "darwin":
        return ["open", "-a", _DARWIN_OPEN_NAMES[app]], None
    if platform.startswith("win"):
        cands = (_WIN_TAILSCALE_GUI if app == "tailscale"
                 else install_apps.app_path_candidates(app, platform, env))
        path = next((p for p in cands if p and not p.startswith("\\") and exists(p)), None)
        if path is None:
            return None
        if app == "obs":
            return [path], ntpath.dirname(path)
        if app == "discord":
            return [path, "--processStart", "Discord.exe"], None
        return [path], None
    if app == "tailscale":
        return None
    name = _LINUX_PATH_NAMES.get(app)
    if not name:
        return None
    exe = which(name)
    return ([exe], None) if exe else None


def session_runtime_dir(env, uid, exists=os.path.exists):
    """The XDG per-user runtime directory of the login session ('/run/user/<uid>'),
    or '' when it cannot be established. An inherited XDG_RUNTIME_DIR always wins.
    The '/' is explicit because this is a Linux-only path: os.path.join would
    inject a backslash on the Windows runner."""
    inherited = (env.get("XDG_RUNTIME_DIR") or "").rstrip("/")
    if inherited:
        return inherited
    if uid is None:
        return ""
    cand = "/run/user/" + str(uid)
    return cand if exists(cand) else ""


def launch_env(app, platform, env=None, exists=os.path.exists, uid=None,
               glob_paths=glob.glob):
    """Environment overrides for a headless GUI launch, or {} when none are
    needed. On Linux, launching obs or discord from a shell with no DISPLAY, such
    as a bare SSH session, cannot reach the autologin X session, so point it there
    and `racecast event start` works over SSH. RACECAST_DISPLAY overrides the
    display (default ':0').

    Three things are handed over, all rooted in the login session's runtime dir:
    - XAUTHORITY, the X cookie. ~/.Xauthority first; on an SDDM/GDM host that file
      does not exist and the cookie lives at $XDG_RUNTIME_DIR/xauth_* instead.
      Without it OBS does not start at all over SSH on a KDE/GNOME box. The glob
      is sorted so repeated runs pick the same file.
    - XDG_RUNTIME_DIR, which is how PipeWire finds its socket
      ($XDG_RUNTIME_DIR/pipewire-0) and how discord_rpc.ipc_candidates finds the
      Discord IPC endpoint. Missing it costs the broadcast its Discord audio and
      the voice auto-join, both of which fail silently on every surface.
    - DBUS_SESSION_BUS_ADDRESS, the session bus, when its socket is really there.

    Each variable is emitted only once the path behind it is confirmed to exist.
    A non-Linux platform, a non-GUI app, or an already-set DISPLAY, which means we
    are inside a real session, leaves the env untouched and returns {}."""
    env = os.environ if env is None else env
    if not platform.startswith("linux"):
        return {}
    if app not in ("obs", "discord"):
        return {}
    if env.get("DISPLAY"):
        return {}
    if uid is None and hasattr(os, "getuid"):
        uid = os.getuid()
    out = {"DISPLAY": env.get("RACECAST_DISPLAY") or ":0"}
    runtime = session_runtime_dir(env, uid, exists)
    xauth = env.get("XAUTHORITY")
    if not xauth:
        home = env.get("HOME") or ""
        # Explicit '/': a Linux-only path, so os.path.join would inject a
        # backslash on the Windows runner.
        cand = home + "/.Xauthority" if home else ""
        if cand and exists(cand):
            xauth = cand
    if not xauth and runtime:
        matches = sorted(glob_paths(runtime + "/xauth_*"))
        if matches:
            xauth = matches[0]
    if xauth:
        out["XAUTHORITY"] = xauth
    if runtime:
        if not env.get("XDG_RUNTIME_DIR"):
            out["XDG_RUNTIME_DIR"] = runtime
        bus = runtime + "/bus"
        if not env.get("DBUS_SESSION_BUS_ADDRESS") and exists(bus):
            out["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + bus
    return out


# Windows process image names to taskkill (the GUI app, mirroring launch_command).
_WIN_PROC_NAMES = {"obs": "obs64.exe", "discord": "Discord.exe",
                   "tailscale": "tailscale-ipn.exe"}


def quit_command(app, platform):
    """argv that asks GUI app `app` to quit, or None when there is nothing to
    exec on this platform. Graceful where the OS allows it: macOS AppleScript
    `quit`, Windows taskkill by image name, Linux pkill. Covers the launchable GUI
    apps obs, discord and tailscale; on Linux Tailscale is a daemon with no GUI to
    quit. Companion has its own stop path (companion_common) and the Tailscale
    *tunnel* is controlled separately by `tailscale down`."""
    if app not in _DARWIN_OPEN_NAMES:        # not a GUI app we launch -> nothing to quit
        return None
    if platform == "darwin":
        return ["osascript", "-e",
                f'tell application "{_DARWIN_OPEN_NAMES[app]}" to quit']
    if platform.startswith("win"):
        proc = _WIN_PROC_NAMES.get(app)
        return ["taskkill", "/IM", proc] if proc else None
    name = _LINUX_PATH_NAMES.get(app)        # tailscale has no Linux GUI -> None
    return ["pkill", "-f", name] if name else None


def check_assets(required_files, directory):
    """Sorted names from `required_files` missing in `directory` (an absent or
    unreadable directory misses everything)."""
    try:
        have = set(os.listdir(directory))
    except OSError:
        have = set()
    return sorted(f for f in required_files if f not in have)


def local_count(directory):
    """Number of entries in `directory` (0 when absent/unreadable)."""
    try:
        return len(os.listdir(directory))
    except OSError:
        return 0


def fetch_assets_rows(gg, sheet_id, timeout=5, tab="Assets"):
    """Assets-tab CSV rows via get-graphics' fetcher, or None when there is no
    sheet id or the fetch fails (callers fall back to the local-only check)."""
    if not sheet_id:
        return None
    try:
        return list(csv.reader(io.StringIO(gg.fetch_assets_csv(sheet_id, tab,
                                                               timeout=timeout))))
    except Exception:
        return None


def required_graphics(gg, rows):
    """Filenames the Assets tab demands (Sheet label IS the filename).
    `rows` may be None/empty -> []."""
    if not rows:
        return []
    names = (gg.safe_filename(lbl) for lbl in gg.graphics_from_csv(rows))
    return sorted(n for n in names if n)


def required_media(gm, rows):
    """intro.mp4, outro.mp4 and trailer.mp4 for each media row found in the Assets
    tab, and all three when the sheet defines none or is unreadable, because the
    OBS Intro, Outro and Trailer scenes reference them."""
    if rows is None:
        return ["intro.mp4", "outro.mp4", "trailer.mp4"]
    keys = sorted(gm.media_urls_from_csv(rows)) or ["intro", "outro", "trailer"]
    return [f"{k}.mp4" for k in keys]


def classify_app(app, running, web=False):
    """OBS is broadcast-critical (FAIL); Discord only carries interview audio.
    On a web-variant host, one without native Discord such as ARM64 Linux,
    interview audio comes from Discord-web in a browser, so report an
    informational note instead of a 'Discord not running' warning."""
    if app == "obs":
        return (Result(PASS, "OBS", "running") if running else
                Result(FAIL, "OBS", "not running. Launch OBS (or `racecast event start`)"))
    if app == "discord" and web:
        return Result(INFO, "Discord",
                      "interview audio via Discord-web in the browser. Open it "
                      "and join the voice channel manually")
    return (Result(PASS, "Discord", "running") if running else
            Result(WARN, "Discord", "not running; interview audio unavailable, launch Discord"))


def classify_tailscale(ip):
    if ip:
        return Result(PASS, "Tailscale", f"connected ({ip})")
    return Result(WARN, "Tailscale",
                  "Tailscale not connected; directors cannot reach the panel or "
                  "tablet remotely. Sign in to Tailscale")


def classify_relay(alive, http_ok, port=8088):
    if alive and http_ok:
        return Result(PASS, "Relay", f"running, control http://127.0.0.1:{port}/status OK")
    if alive:
        return Result(FAIL, "Relay",
                      f"process alive but port {port} not responding. Check `racecast relay logs`")
    return Result(FAIL, "Relay", "not running. `racecast relay start` (or `racecast event start`)")


def classify_companion(running, supported, unsupported_detail=""):
    """Companion is WARN-level: the broadcast works without the buttons."""
    if not supported:
        return Result(WARN, "Companion",
                      unsupported_detail or "no automated probe on this OS, check manually")
    if running:
        return Result(PASS, "Companion", "running")
    return Result(WARN, "Companion", "not running. `racecast companion start`")


def classify_scene_collection(status, note):
    """OBS scene-collection readiness. WARN-level by design: a wrong collection is
    fixable in one click, with `racecast obs collection set` or the Control Center
    OBS row, and a best-effort live probe must not turn the report red on its own.
    `status` is obs_ws.scene_collection_status(...), or None when the probe
    failed."""
    if status is None:
        return Result(WARN, "OBS scene collection", f"check skipped: {note}")
    if status["match"]:
        return Result(PASS, "OBS scene collection", f"{status['expected']} active")
    if status["expected_present"]:
        return Result(WARN, "OBS scene collection",
                      f"'{status['current']}' active. Switch with "
                      f"`racecast obs collection set`")
    if status["renamed_variant"]:
        return Result(WARN, "OBS scene collection",
                      f"'{status['current']}' active and looks renamed. Switch to "
                      f"{status['expected']} manually")
    return Result(WARN, "OBS scene collection",
                  f"{status['expected']} collection not found. Import it "
                  f"(`racecast setup`)")


def classify_assets(label, missing, count, severity, fix):
    """`missing` is the check_assets() list when the sheet was readable, or None
    when only the local fallback could run. `severity` is the not-OK level for this
    asset kind: FAIL for graphics, where a missing file is a black source in OBS,
    and WARN for media."""
    if missing is None:
        if count:
            return Result(WARN, label,
                          f"sheet unreachable, {count} local file(s) present, "
                          f"completeness not verified")
        return Result(severity, label, f"none present. {fix}")
    if missing:
        return Result(severity, label, f"missing: {', '.join(missing)} — {fix}")
    return Result(PASS, label, f"complete ({count} file(s))")


def classify_env(sheet_id, push_url):
    """RACECAST_SHEET_ID is required (FAIL). The sheet-write webhook is optional;
    without it there is no timer handover sync and the panel Setup row is
    read-only (WARN)."""
    if not sheet_id:
        return Result(FAIL, ".env", "missing: RACECAST_SHEET_ID. Set SHEET_ID "
                      "in the active profile (profiles/<name>/profile.env)")
    if not push_url:
        return Result(WARN, ".env", "RACECAST_SHEET_PUSH_URL unset, so race-timer "
                      "handover sync and panel sheet controls are disabled "
                      "(see the Sheet-Webhook wiki page)")
    return Result(PASS, ".env", "RACECAST_SHEET_ID and RACECAST_SHEET_PUSH_URL set")


def gate_blockers(results):
    """The FAIL-level Results among `racecast event start`'s *static*
    preconditions, the ones bringing services up cannot fix: SHEET_ID, graphics,
    cookies. The returned list aborts bring-up unless `--force` is given; WARN and
    INFO are advisory and never block. The caller supplies the already-classified
    Results. The launchable services (relay, OBS, Companion, Tailscale) are
    deliberately not among them, since event start is what launches those and they
    would always fail a pre-launch gate."""
    return [r for r in results if r.level == FAIL]


def director_urls(ts_ip, companion_port=8000, relay_port=8088):
    """Printable 'Share with your directors' block for `racecast event start`.
    The caller supplies the detected Tailscale IP (or None) and Companion's web
    port (config.json `http_port`, default 8000)."""
    lines = ["Share with your directors:"]
    if not ts_ip:
        lines.append("  Tailscale not connected, so directors cannot connect "
                     "remotely (racecast tailscale up).")
        return lines
    lines += [
        f"  Director panel:     http://{ts_ip}:{relay_port}/panel",
        f"  Companion buttons:  http://{ts_ip}:{companion_port}/tablet",
        "  (panel scene/audio control also needs the OBS WebSocket password: "
        "OBS > Tools > WebSocket Server Settings)",
    ]
    return lines


GO_LIVE_REMINDER = Result(
    INFO, "HUD overlay",
    "Before going LIVE: refresh the HUD overlay browser source in OBS once "
    "(right-click the source -> Refresh). The HUD, which includes the race "
    "timer, auto-refreshes, but that is not fully reliable.")

#!/usr/bin/env python3
"""`racecast install-apps` installs the producer APPLICATIONS (OBS Studio, Bitfocus
Companion, Tailscale) via winget (Windows) / brew casks (macOS) / official vendor
paths (Linux, apt-based distros). Linux is automated after an explicit operator
confirmation (sudo prompts surface to the operator); other distros get the manual
guide. It never elevates privileges itself. The vendor installers and package
managers prompt for sudo on their own. The required CLI tools live in
install_tools.py."""
import os, shutil, subprocess, sys
import http_util

_COMMON = None


def _common():
    """Load installer_common.py from the sibling path (repo + frozen bundle)."""
    global _COMMON
    if _COMMON is None:
        import importlib.util
        here = os.path.dirname(os.path.abspath(__file__))
        spec = importlib.util.spec_from_file_location(
            "installer_common", os.path.join(here, "installer_common.py"))
        _COMMON = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_COMMON)
    return _COMMON

APPS = ("obs", "companion", "tailscale", "discord")

WINGET_APP_IDS = {"obs": "OBSProject.OBSStudio",
                  "companion": "Bitfocus.Companion",
                  "tailscale": "Tailscale.Tailscale",
                  "discord": "Discord.Discord"}
# The tailscale-app CASK is the GUI app; the plain `tailscale` formula is the
# bare daemon, and producers need the app.
BREW_CASKS = {"obs": "obs", "companion": "companion",
              "tailscale": "tailscale-app", "discord": "discord"}

# Default install locations per app, heuristics like companion_common's
# WINDOWS_COMPANION_CANDIDATES. Keep the Companion entries in sync with it.
_WINDOWS_APP_PATHS = {
    # OBS lands in Program Files (x86) when a 32-bit installer registered it.
    "obs": (r"%ProgramFiles%\obs-studio\bin\64bit\obs64.exe",
            r"%ProgramFiles(x86)%\obs-studio\bin\64bit\obs64.exe"),
    "companion": (r"%LOCALAPPDATA%\Programs\companion\Companion.exe",
                  r"C:\Program Files\Companion\Companion.exe",
                  r"C:\Program Files (x86)\Companion\Companion.exe"),
    "tailscale": (r"C:\Program Files\Tailscale\tailscale.exe",),
    # Discord is a Squirrel per-user install: the versioned app-x.y.z\Discord.exe
    # folder moves on every update, so Update.exe is the version-stable path.
    "discord": (r"%LOCALAPPDATA%\Discord\Update.exe",),
}
_DARWIN_APP_PATHS = {
    "obs": ("/Applications/OBS.app",),
    "companion": ("/Applications/Companion.app",),
    "tailscale": ("/Applications/Tailscale.app",),
    "discord": ("/Applications/Discord.app",),
}
# companion-pi installs a systemd service, not a `companion` binary on PATH;
# without these candidates the post-install re-check would call a successful
# install "still missing".
_LINUX_APP_PATHS = {
    "companion": ("/opt/companion", "/etc/systemd/system/companion.service"),
    "discord": ("/usr/share/discord", "/usr/bin/discord"),
}

# Official Linux install paths. The two installer scripts are downloaded over
# HTTPS (cert-verified) to a temp file and executed VISIBLY, never via a shell
# pipe, after an explicit operator confirmation.
OBS_PPA = "ppa:obsproject/obs-studio"
TAILSCALE_INSTALLER = "https://tailscale.com/install.sh"          # escalates itself
COMPANION_INSTALLER = \
    "https://raw.githubusercontent.com/bitfocus/companion-pi/main/install.sh"  # needs root
# Discord's official Linux .deb (the snap is community-maintained, not Discord Inc.)
DISCORD_DEB = "https://discord.com/api/download?platform=linux&format=deb"
# Discord ships an amd64 .deb only, and the download API ignores an arch param.
# On arm64 that .deb is unsatisfiable (its amd64 deps aren't installable), so it
# is skipped.
DISCORD_NO_ARM64_NOTE = (
    "Discord: no official ARM64 Linux .deb (stable and canary are amd64-only), "
    "skipping. Use the web app (https://discord.com/app) or a browser.")
AMD64_MACHINES = ("x86_64", "amd64")


def _expand_windows(path, env):
    path = path.replace("%ProgramFiles(x86)%", env.get("ProgramFiles(x86)", ""))
    path = path.replace("%ProgramFiles%", env.get("ProgramFiles", ""))
    path = path.replace("%LOCALAPPDATA%", env.get("LOCALAPPDATA", ""))
    return path


def app_path_candidates(app, platform, env=None):
    """Expanded well-known install paths for `app` on `platform`. May be empty:
    Linux mostly relies on the PATH fallback in app_present."""
    env = os.environ if env is None else env
    if platform.startswith("win"):
        return [_expand_windows(p, env) for p in _WINDOWS_APP_PATHS.get(app, ())]
    if platform == "darwin":
        return list(_DARWIN_APP_PATHS.get(app, ()))
    return list(_LINUX_APP_PATHS.get(app, ()))


def app_present(app, platform, env=None, exists=os.path.exists, which=shutil.which):
    """True iff the app is already installed (well-known paths, then PATH)."""
    for path in app_path_candidates(app, platform, env):
        if not path.startswith("\\") and exists(path):
            return True
    return bool(which(app))  # CLI fallback (e.g. tailscale on PATH)


def _read_plist(path):
    import plistlib
    with open(path, "rb") as fh:
        return plistlib.load(fh)


def darwin_app_version(app, exists=os.path.exists, read_plist=_read_plist):
    """Installed version of a macOS .app from its bundle Info.plist
    (CFBundleShortVersionString, then CFBundleVersion), or None when the bundle
    or the keys are absent / the plist is unreadable. The reader is injected so
    the logic is unit-tested without a real .app on disk."""
    for bundle in _DARWIN_APP_PATHS.get(app, ()):
        # macOS bundle path -> always forward slashes (os.path.join would inject
        # backslashes when this helper is exercised cross-platform, e.g. in CI).
        plist = bundle + "/Contents/Info.plist"
        if not exists(plist):
            continue
        try:
            data = read_plist(plist)
        except Exception:   # noqa: BLE001 — unreadable/corrupt plist -> no version, never raise
            return None
        return (data.get("CFBundleShortVersionString")
                or data.get("CFBundleVersion") or None)
    return None


def _read_text(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _run(argv, run=None, timeout=8):
    """subprocess.run() wrapper that hides the Windows console window and turns
    any spawn failure into None. Used only for TRUE CLIs (tailscale, dpkg-query),
    never GUI app binaries, because exec'ing those could pop a window."""
    run = subprocess.run if run is None else run
    try:
        import services
        nw = services.no_window_kwargs()
    except Exception:   # noqa: BLE001 — services optional (standalone) / probe is best-effort
        nw = {}
    try:
        return run(argv, capture_output=True, text=True, errors="replace",
                   timeout=timeout, **nw)
    except (OSError, subprocess.SubprocessError):
        return None


def cli_version(argv, run=None):
    """First non-empty stdout line of a CLI's version output (e.g.
    `tailscale version` -> '1.98.5'), or None on non-zero exit / spawn failure."""
    out = _run(argv, run=run)
    if out is None or out.returncode != 0:
        return None
    for line in (out.stdout or "").splitlines():
        line = line.strip()
        if line:
            return line
    return None


def dpkg_version(pkg, run=None):
    """Installed Debian package version via `dpkg-query`, or None when the
    package is not installed / dpkg is unavailable (Linux)."""
    out = _run(["dpkg-query", "-W", "-f=${Version}", pkg], run=run)
    if out is None or out.returncode != 0:
        return None
    return (out.stdout or "").strip() or None


def build_info_version(path, read_text=_read_text):
    """`version` from a Discord build_info.json (Linux/macOS), or None."""
    import json
    try:
        data = json.loads(read_text(path))
    except (OSError, ValueError):
        return None
    return data.get("version") or None


def _version_key(v):
    """Sort key for dotted version folders: leading-numeric of each segment."""
    key = []
    for chunk in v.split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        key.append(int(num) if num else 0)
    return key


def discord_squirrel_version(local_appdata, listdir=os.listdir):
    """Highest 'app-X.Y.Z' folder version under %LOCALAPPDATA%\\Discord, or None.
    Discord's per-user Windows install names its version folder this way; reading
    it needs no subprocess and never launches Discord."""
    try:
        entries = listdir(os.path.join(local_appdata, "Discord"))
    except OSError:
        return None
    versions = [n[4:] for n in entries
                if n.startswith("app-") and n[4:5].isdigit()]
    return max(versions, key=_version_key) if versions else None


def windows_file_version(path):
    """Numeric FileVersion from a Windows PE binary's VERSIONINFO resource
    (e.g. obs64.exe -> '32.1.2.0'), or None. Reads metadata only and never
    executes the binary. No-op (None) off Windows."""
    try:
        import ctypes
        ver = ctypes.windll.version       # AttributeError off Windows -> None
        size = ver.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(path, 0, size, buf):
            return None
        block = ctypes.c_void_p()
        length = ctypes.c_uint()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(block),
                                  ctypes.byref(length)) or not length.value:
            return None
        words = ctypes.cast(
            block, ctypes.POINTER(ctypes.c_uint * (length.value // 4))).contents
        ms, ls = words[2], words[3]       # dwFileVersionMS, dwFileVersionLS
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:   # noqa: BLE001 — any API/format hiccup -> no version, never raise
        return None


def _first_existing(paths, exists):
    for p in paths:
        if p and not p.startswith("\\") and exists(p):
            return p
    return None


def _windows_app_version(app, env, exists, listdir, run, file_version):
    if app == "discord":
        local = env.get("LOCALAPPDATA", "")
        return discord_squirrel_version(local, listdir=listdir) if local else None
    cands = app_path_candidates(app, "win32", env)
    if app == "tailscale":   # a real CLI, so `tailscale version` is safe
        exe = _first_existing(cands, exists) or "tailscale"
        return cli_version([exe, "version"], run=run)
    exe = _first_existing(cands, exists)   # obs64.exe / Companion.exe
    return file_version(exe) if exe else None


# Discord's bundled build_info.json: the .deb (/usr/share) and tarball (/opt).
_LINUX_DISCORD_BUILD_INFO = ("/usr/share/discord/resources/build_info.json",
                             "/opt/discord/resources/build_info.json")


def _linux_app_version(app, exists, read_text, run):
    if app == "tailscale":
        return cli_version(["tailscale", "version"], run=run)
    if app == "obs":
        return dpkg_version("obs-studio", run=run)
    if app == "discord":
        for bi in _LINUX_DISCORD_BUILD_INFO:
            if exists(bi):
                v = build_info_version(bi, read_text=read_text)
                if v:
                    return v
        return dpkg_version("discord", run=run)
    # companion-pi (service install) exposes no stable version file, so the
    # running web server is the source instead (companion_http_version, tried as
    # a fallback in app_version).
    return None


def _http_fetch(url, range_bytes):
    """GET `url` (optionally only its first `range_bytes`) and return the decoded
    body. Short timeout; raises on any failure (callers treat that as 'unknown')."""
    headers = {"Range": f"bytes=0-{range_bytes - 1}"} if range_bytes else None
    with http_util.open_url(url, headers=headers, timeout=4) as resp:
        body = resp.read(range_bytes) if range_bytes else resp.read()
    return body.decode("utf-8", "replace")


def companion_http_version(base_url="http://127.0.0.1:8000", fetch=_http_fetch):
    """Installed Companion version from its running web server, or None. Companion
    serves no version REST endpoint, but its built frontend embeds the release as
    `SENTRY_RELEASE={id:"<ver>+<build>-<channel>-<sha>"}` in the first ~1 KB of
    its main bundle (Companion v5 switched the quote to a backtick template literal,
    `id:`<ver>+…``; both are accepted). Fetch the SPA shell to find the
    content-hashed bundle name, then a small Range GET of its head to read the
    marker. This is the only version source for the companion-pi Linux service and
    for WSL/Docker setups where Companion runs on another host.
    `fetch(url, range_bytes)` returns the body (range_bytes=None = whole shell)
    and raises on failure; the default uses urllib with a short timeout."""
    import re
    base = base_url.rstrip("/")
    try:
        shell = fetch(base + "/", None)
        scripts = re.findall(r'src="(/assets/index-[^"]+\.js)"', shell)
        bundle = next((s for s in scripts if "legacy" not in s), None)
        if not bundle:
            return None
        head = fetch(base + bundle, 65536)
        marker = re.search(r'SENTRY_RELEASE=\{id:["\'`](\d+\.\d+\.\d+)', head)
        return marker.group(1) if marker else None
    except Exception:   # noqa: BLE001 — Companion unreachable / markup changed -> unknown
        return None


def app_version(app, platform=None, *, exists=os.path.exists, read_plist=_read_plist,
                read_text=_read_text, run=None, listdir=os.listdir, env=None,
                file_version=windows_file_version,
                companion_url="http://127.0.0.1:8000", companion_fetch=_http_fetch):
    """Best-effort installed version string for `app`, or None. Per platform,
    using only non-launching sources: macOS reads the .app Info.plist; Windows
    reads obs64.exe/Companion.exe file-version metadata, the Discord Squirrel
    folder, and `tailscale version`; Linux uses dpkg-query (OBS), Discord's
    build_info.json, and `tailscale version`. Companion has no local version file
    on Linux, so when the local probe comes back empty its running web server is
    queried (companion_http_version). Anything unavailable -> None, so the surfaces
    show presence without a version, never an error (#91)."""
    platform = sys.platform if platform is None else platform
    env = os.environ if env is None else env
    if platform == "darwin":
        version = darwin_app_version(app, exists=exists, read_plist=read_plist)
    elif platform.startswith("win"):
        version = _windows_app_version(app, env, exists, listdir, run, file_version)
    else:
        version = _linux_app_version(app, exists, read_text, run)
    if version is None and app == "companion" and companion_url:
        version = companion_http_version(companion_url, fetch=companion_fetch)
    return version


def installed_apps_report(apps, version_fn):
    """Aligned 'name  version' lines for already-installed `apps` (version_fn(app)
    -> str|None). Apps with no probed version show '(version unavailable)' rather
    than an empty column (#91)."""
    width = max((len(a) for a in apps), default=0)
    return [f"  {a.ljust(width)}  {version_fn(a) or '(version unavailable)'}"
            for a in apps]


# Apps whose winget SILENT install is broken: Companion's NSIS installer writes
# NOTHING without admin yet exits 0, so winget reports success while nothing was
# installed. --interactive runs the UI wizard, whose UAC prompt the operator can
# answer.
WINGET_INTERACTIVE = ("companion",)


def app_install_commands(manager, apps, brew_path="brew"):
    """The argv list(s) to install `apps` with `manager`. apt: none (manual)."""
    if manager == "winget":
        return [["winget", "install", "--id", WINGET_APP_IDS[a], "-e",
                 "--accept-source-agreements", "--accept-package-agreements"]
                + (["--interactive"] if a in WINGET_INTERACTIVE else [])
                for a in apps]
    if manager == "brew":
        casks = [BREW_CASKS[a] for a in apps]
        return [[brew_path, "install", "--cask"] + casks] if casks else []
    return []


def app_update_commands(manager, apps, brew_path="brew"):
    """The argv list(s) to UPGRADE already-installed `apps` with `manager`.
    brew skips self-updating casks (Discord/Tailscale update themselves), which
    is fine, not a failure. Linux: see apps_update_guide()."""
    if manager == "winget":
        return [["winget", "upgrade", "--id", WINGET_APP_IDS[a], "-e",
                 "--accept-source-agreements", "--accept-package-agreements"]
                + (["--interactive"] if a in WINGET_INTERACTIVE else [])
                for a in apps]
    if manager == "brew":
        casks = [BREW_CASKS[a] for a in apps]
        return [[brew_path, "upgrade", "--cask"] + casks] if casks else []
    return []


def partition_brew_updatable(present, managed_casks):
    """Split `present` apps into (homebrew-managed, installed-elsewhere) by whether
    their cask token appears in `managed_casks` (installer_common.brew_installed_casks).
    `brew upgrade --cask` only works on casks brew tracks; an app present on disk
    but installed manually (or a self-updating cask brew never recorded) is not
    one, so it is reported and left alone instead of failing the whole upgrade
    batch (#92). managed_casks=None means the probe failed, so every present app
    is treated as managed."""
    if managed_casks is None:
        return list(present), []
    managed = [a for a in present if BREW_CASKS[a] in managed_casks]
    elsewhere = [a for a in present if BREW_CASKS[a] not in managed_casks]
    return managed, elsewhere


def apps_update_guide():
    """Per-app Linux update paths (no single manager covers all four)."""
    return ("Linux app updates (manual):\n"
            "  OBS:       sudo apt-get update && sudo apt-get install --only-upgrade -y obs-studio\n"
            "  Tailscale: sudo apt-get install --only-upgrade -y tailscale\n"
            "             (the installer added Tailscale's apt repo)\n"
            "  Companion: sudo companion-update   (companion-pi service install)\n"
            "  Discord:   re-download the official .deb:\n"
            "             curl -fsSL 'https://discord.com/api/download?platform=linux&format=deb' \\\n"
            "               -o /tmp/discord.deb && sudo apt-get install -y /tmp/discord.deb")


def apps_manual_guide(platform, manager=None):
    if manager == "pacman":
        return "\n".join([
            "Install the apps manually:",
            "  OBS Studio, the CEF-enabled build, NOT plain obs-studio:",
            f"    sudo pacman -S {PACMAN_APP_PACKAGES['obs']}",
            "    (plain obs-studio has no Browser Source, so the HUD/timer stay black)",
            "  Tailscale:",
            f"    sudo pacman -S {PACMAN_APP_PACKAGES['tailscale']}",
            "    sudo systemctl enable --now tailscaled && sudo tailscale up",
            "  Discord:",
            f"    sudo pacman -S {PACMAN_APP_PACKAGES['discord']}",
            "  Companion, not packaged:",
            "    " + PACMAN_COMPANION_NOTE.split(". ", 1)[-1],
        ])
    lines = ["Install the apps manually:"]
    if platform not in ("darwin",) and not platform.startswith("win"):
        lines.append("  (Linux/WSL: install them on the HOST machine that runs OBS)")
        lines.append("  OBS Studio  (https://obsproject.com/download):")
        lines.append("    sudo add-apt-repository -y ppa:obsproject/obs-studio")
        lines.append("    sudo apt-get update && sudo apt-get install -y obs-studio")
        lines.append("    (Debian without add-apt-repository: sudo apt-get install -y obs-studio)")
        lines.append("  Tailscale  (https://tailscale.com/download):")
        lines.append("    curl -fsSL https://tailscale.com/install.sh | sh")
        lines.append("    sudo tailscale up")
        lines.append("  Companion  (https://bitfocus.io/companion): headless/service, Debian/Ubuntu x64/arm64:")
        lines.append("    sudo apt-get install -y libatomic1   # companion-pi's node needs it (missing on minimal 24.04)")
        lines.append("    curl -fsSL https://raw.githubusercontent.com/bitfocus/companion-pi/main/install.sh | sudo bash")
        lines.append("  Discord  (https://discord.com/download):")
        lines.append("    curl -fsSL 'https://discord.com/api/download?platform=linux&format=deb' -o /tmp/discord.deb")
        lines.append("    sudo apt-get install -y /tmp/discord.deb")
    else:
        lines.append("  OBS Studio : https://obsproject.com/download")
        lines.append("  Companion  : https://bitfocus.io/companion")
        lines.append("  Tailscale  : https://tailscale.com/download")
        lines.append("  Discord    : https://discord.com/download")
    if platform.startswith("win"):
        lines.append("NOTE: approve the UAC (admin) prompts. Companion's installer "
                     "writes nothing without admin yet still reports success.")
    return "\n".join(lines)


def linux_install_steps(apps, which=shutil.which, machine=None):
    """Ordered (kind, ...) steps to install `apps` on an apt-based distro.
    ('run', argv) executes argv; ('script', url, runner) downloads url and runs
    it with runner + [path]; ('deb', url) downloads url and installs it with
    apt-get; ('note', text) just prints text (an app we can't auto-install here).
    OBS uses the official PPA when add-apt-repository exists (Ubuntu), else plain
    apt (Debian ships obs-studio). `machine` defaults to this host's arch."""
    if machine is None:
        import platform as _pf
        machine = _pf.machine()
    steps = []
    updated = [False]   # queue `apt-get update` at most once, before the first install

    def _ensure_update():
        # A fresh image (empty/stale index) can't locate a package otherwise
        # (#408); refresh once and let later apt-get installs reuse it.
        if not updated[0]:
            steps.append(("run", ["sudo", "apt-get", "update"]))
            updated[0] = True

    if "obs" in apps:
        if which("add-apt-repository"):
            steps.append(("run", ["sudo", "add-apt-repository", "-y", OBS_PPA]))
        # update after add-apt-repository so it also picks up the newly-added PPA.
        _ensure_update()
        steps.append(("run", ["sudo", "apt-get", "install", "-y", "obs-studio"]))
    if "tailscale" in apps:
        steps.append(("script", TAILSCALE_INSTALLER, ["sh"]))
    if "companion" in apps:
        # companion-pi's bundled node needs libatomic.so.1, which is absent on a
        # fresh minimal Ubuntu 24.04, so its node can't start and no service is
        # created. Install libatomic1 BEFORE the vendor installer (#413).
        _ensure_update()
        steps.append(("run", ["sudo", "apt-get", "install", "-y", "libatomic1"]))
        steps.append(("script", COMPANION_INSTALLER, ["sudo", "bash"]))
    if "discord" in apps:
        if (machine or "").lower() in AMD64_MACHINES:
            steps.append(("deb", DISCORD_DEB))
        else:
            steps.append(("note", DISCORD_NO_ARM64_NOTE))   # no arm64 .deb exists
    return steps


# Arch (pacman). Three of the four apps are packaged; Companion is not, see
# PACMAN_COMPANION_NOTE. `obs` deliberately maps to obs-studio-browser: the plain
# obs-studio package is built WITHOUT CEF, so every Browser Source is missing and
# the relay's HUD/timer overlays render black with no error anywhere.
PACMAN_APP_PACKAGES = {"obs": "obs-studio-browser", "tailscale": "tailscale",
                       "discord": "discord"}
PACMAN_COMPANION_NOTE = (
    "Companion is in no Arch repository and not in the AUR under a usable name "
    "(`companion-satellite` is a different product). Install it by hand into the "
    "companion-pi layout racecast controls. The wiki page 'Arch Linux — the "
    "CachyOS example' has the exact steps, including the trap that "
    "`racecast companion enable-control` must run BEFORE `companion start`.")


def pacman_install_steps(apps):
    """Ordered (kind, ...) steps to install `apps` with pacman, in the same step
    shapes as linux_install_steps so _install_linux can execute either plan.

    Repository packages go in ONE `pacman -S` call. Deliberately no `-Sy`:
    refreshing the package list without upgrading the system is Arch's
    partial-upgrade trap, and forcing a full `-Syu` during an app install is the
    operator's call, not ours (see the wiki's rolling-release section)."""
    steps = []
    pkgs = [PACMAN_APP_PACKAGES[a] for a in apps if a in PACMAN_APP_PACKAGES]
    if pkgs:
        steps.append(("run", ["sudo", "pacman", "-S", "--needed", "--noconfirm"] + pkgs))
    if "tailscale" in apps:
        # Parity with the apt path, where tailscale's own install.sh enables the
        # daemon. The Arch package ships the unit disabled, so without this the
        # tailnet is down after the next reboot, and the relay's `--bind auto`
        # would silently fall back to localhost-only.
        steps.append(("run", ["sudo", "systemctl", "enable", "--now", "tailscaled"]))
    if "companion" in apps:
        steps.append(("note", PACMAN_COMPANION_NOTE))
    return steps


def should_enable_companion_control(installed, failed):
    """True iff Companion was just installed on Linux without a failed step, so
    `racecast companion enable-control` should run to wire up the Start/Stop button."""
    return "companion" in installed and not failed


def confirmed(answer):
    return _common().confirmed(answer)


def _run_remote_script(url, runner):
    return _common().run_remote_script(url, runner)


def _install_linux(missing, assume_yes):
    # apt wins when both are present, mirroring install_tools.pick_manager.
    if shutil.which("apt-get"):
        manager, steps = "apt", linux_install_steps(missing)
    elif shutil.which("pacman"):
        manager, steps = "pacman", pacman_install_steps(missing)
    else:
        print("No supported package manager detected. Install manually:")
        print(apps_manual_guide(sys.platform))
        return 0
    if not steps:
        print("Nothing to install here. See the manual guide:")
        print(apps_manual_guide(sys.platform, manager))
        return 0
    print("Planned steps (sudo will prompt for your password; any installer")
    print("scripts are official vendor installers, downloaded over HTTPS):")
    for step in steps:
        if step[0] == "run":
            print("  $", " ".join(step[1]))
        elif step[0] == "deb":
            print("  $ sudo apt-get install -y <downloaded .deb>   #", step[1])
        elif step[0] == "note":
            print("  #", step[1])
        else:
            print("  $", " ".join(step[2]), "<", step[1])
    if not assume_yes and not confirmed(input("Proceed? [y/N] ")):
        print("aborted.")
        return 0
    failed = []
    for step in steps:
        if step[0] == "note":          # informational only, not an install or a failure
            print(step[1])
            continue
        label = " ".join(step[1]) if step[0] == "run" else step[1]  # argv vs URL
        try:
            if step[0] == "run":
                print("Running:", label)
                rc = subprocess.call(step[1])
            elif step[0] == "deb":
                rc = _common().install_remote_deb(step[1])
            else:
                rc = _run_remote_script(step[1], step[2])
        except Exception as exc:                      # noqa: BLE001
            # A download/timeout/HTTP error on ONE vendor (e.g. Discord's CDN)
            # must not abort the remaining apps or crash with a traceback, since
            # the other installs already ran. Record it and carry on.
            print(f"  ! step failed: {exc}")
            rc = 1
        if rc != 0:
            failed.append(label)
    if "tailscale" in missing:
        print("Tailscale installed? Finish with:  sudo tailscale up")
    # Companion is only ever installed by the apt plan; on pacman it stayed a note,
    # so neither the companion-pi line nor enable-control applies (there is no
    # service to wire up yet).
    if "companion" in missing and manager == "apt":
        print("Companion: this is the headless/service install (companion-pi).")
    if manager == "apt" and should_enable_companion_control(missing, failed):
        print("Enabling passwordless Companion start/stop (systemd bind helper + sudoers)…")
        try:
            import companion_linux as cl
            cl.enable_control()
        except Exception as exc:                      # noqa: BLE001
            print(f"  ! enable-control skipped: {exc} "
                  "(run `racecast companion enable-control` later).")
    _obs_browser_notice()
    _pipewire_audio_setup(failed)
    if failed:
        print("\nThese steps failed; re-run `racecast install-apps` to retry them:")
        for f in failed:
            print("  -", f)
    return 1 if failed else 0


def _pipewire_audio_setup(failed):
    """On Linux, install the obs-pipewire-audio-capture plugin (the Discord audio
    source's backend) into the per-user OBS plugins dir when OBS is present, the
    plugin is missing, and this is a prebuilt (x86_64, non-flatpak) target. Prints a
    Flathub / source-build hint otherwise. Best-effort: a download failure is
    recorded in `failed` and never crashes the install."""
    if not sys.platform.startswith("linux"):
        return
    import platform
    try:
        import obs_pipewire_linux as opw
        home = os.path.expanduser("~")
        obs_present = app_present("obs", sys.platform)
        machine = platform.machine()
        # ANY location OBS loads from, not just the per-user one: a distro
        # package already satisfies this, and downloading a second copy would
        # leave OBS with two builds of the same plugin.
        plugin_present = opw.plugin_present(home, machine)
        flatpak = opw.is_flatpak_obs(home)
        if obs_present and not plugin_present and not flatpak and opw.is_prebuilt_arch(machine):
            print(f"Installing OBS PipeWire audio plugin v{opw.PLUGIN_VERSION} "
                  "(Discord audio source) …")
            opw.install_pipewire_audio(home)
            print("  obs-pipewire-audio installed.")
        else:
            hint = opw.install_hint(machine, obs_present, plugin_present, flatpak)
            if hint:
                print("\n" + hint)
    except Exception as exc:                          # noqa: BLE001
        failed.append(f"obs-pipewire-audio ({exc})")


def _obs_browser_notice():
    """On a supported Linux arch where OBS is installed but its Browser Source
    plugin is missing (the distro/PPA ships none on aarch64), point at the
    source-build command; the relay HUD/timer overlays need a Browser Source."""
    if not sys.platform.startswith("linux"):
        return
    import platform
    try:
        import obs_browser_linux as obl
        arch = obl.normalize_arch(platform.machine()) or "x86_64"
        hint = obl.install_hint(
            platform.machine(),
            obs_present=app_present("obs", sys.platform),
            # Check every plugin dir, not only Debian's multiarch one: Arch keeps
            # them in a plain /usr/lib/obs-plugins.
            browser_present=obl.browser_plugin_present(obl.obs_plugins_dirs(arch)),
        )
    except Exception:                                  # noqa: BLE001
        hint = None
    if hint:
        print("\n" + hint)


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="install-apps", add_help=True)
    ap.add_argument("--yes", action="store_true",
                    help="skip confirmation prompts: Linux install steps and macOS Homebrew bootstrap")
    ap.add_argument("--update", action="store_true",
                    help="also upgrade the already-installed apps "
                         "(winget/brew; Linux prints the per-app update guide)")
    a = ap.parse_args()

    missing = [app for app in APPS if not app_present(app, sys.platform)]
    if not missing and not a.update:
        print("All apps already installed:")
        for line in installed_apps_report(list(APPS),
                                          lambda x: app_version(x, sys.platform)):
            print(line)
        print("  (run `racecast install-apps --update` to upgrade them)")
        return
    if missing:
        print("Missing apps:", ", ".join(missing))
    if not (sys.platform.startswith("win") or sys.platform == "darwin"):
        if a.update:
            print(apps_update_guide())
        if not missing:
            return
        rc = _install_linux(missing, a.yes)
        still = [x for x in APPS if not app_present(x, sys.platform)]
        if rc != 0 or (still and shutil.which("apt-get")):
            sys.exit("Some app installs did not complete. Still missing: "
                     + ", ".join(still) + "\n" + apps_manual_guide(sys.platform))
        return
    if sys.platform == "darwin":
        brew = _common().find_brew()
        if not brew:
            brew = _common().bootstrap_brew(a.yes)
        if not brew:
            sys.exit("brew not available.\n" + apps_manual_guide(sys.platform))
        manager = "brew"
        brew_path = brew
    else:
        manager = "winget"
        brew_path = "brew"
        if not shutil.which(manager):
            sys.exit(f"{manager} not found.\n" + apps_manual_guide(sys.platform))
    cmds = []
    if a.update:
        present = [x for x in APPS if x not in missing]
        if present and manager == "brew":
            # brew can only upgrade casks it tracks. An app present on disk but
            # installed outside Homebrew makes `brew upgrade --cask` error and
            # fail the whole batch, so those are skipped with a note (#92).
            managed = _common().brew_installed_casks(brew_path)
            to_update, elsewhere = partition_brew_updatable(present, managed)
            if to_update:
                print("Updating Homebrew-managed apps:", ", ".join(to_update))
                cmds += app_update_commands(manager, to_update, brew_path=brew_path)
            if elsewhere:
                print("Not updating (installed outside Homebrew; they self-update "
                      "or were installed manually):", ", ".join(elsewhere))
        elif present:
            print("Updating installed apps:", ", ".join(present))
            cmds += app_update_commands(manager, present, brew_path=brew_path)
    cmds += app_install_commands(manager, missing, brew_path=brew_path)
    failed = []
    for cmd in cmds:
        print("Running:", " ".join(cmd))
        # winget "already installed / no upgrade" exit codes are not failures
        # (an app the path heuristics in app_present() missed lands here).
        if not _common().install_exit_ok(manager, subprocess.call(cmd)):
            failed.append(" ".join(cmd))
    still = [a for a in APPS if not app_present(a, sys.platform)]
    if failed or still:
        parts = ["Some app installs did not complete."]
        if failed:
            parts.append("Failed: " + "; ".join(failed))
        if still:
            parts.append("Still missing: " + ", ".join(still))
        sys.exit("\n".join(parts) + "\n" + apps_manual_guide(sys.platform))
    if not missing:
        print("All apps up to date:")
        for line in installed_apps_report(list(APPS),
                                          lambda x: app_version(x, sys.platform)):
            print(line)
        return
    print("All apps installed. First-run setup still needed:")
    print("  Tailscale: sign in and join the team's private Tailscale "
          "network (your invited account).")
    print("  Companion: launch once, then `racecast export companion` + import the config.")
    print("  OBS: run `racecast setup` and import the localized collection.")
    print("  Discord: sign in; it carries the interview audio (OBS app-audio capture).")


if __name__ == "__main__":
    main()

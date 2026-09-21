#!/usr/bin/env python3
"""`racecast install-tools` installs the external runtime tools (yt-dlp, streamlink,
ffmpeg, deno) via the platform's package manager: winget (Windows), brew (macOS),
apt (Linux). It never elevates privileges itself. The package managers prompt for
sudo on their own, and a failed install ends with a manual guide. The decision
helpers up top are pure; main() performs the installs."""
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

TOOLS = ("yt-dlp", "streamlink", "ffmpeg", "deno")

# deno needs 2.35 to run at all, so install-tools hard-fails below it; the frozen
# racecast binary needs 2.38, which preflight only warns about. (#409)
MIN_GLIBC_TOOLS = (2, 35)
MIN_GLIBC_BINARY = (2, 38)

WINGET_IDS = {"yt-dlp": "yt-dlp.yt-dlp", "streamlink": "Streamlink.Streamlink",
              "ffmpeg": "Gyan.FFmpeg", "deno": "DenoLand.Deno"}
APT_PACKAGES = {"ffmpeg": "ffmpeg"}
# Arch carries all four tools current in the official `extra` repo, so none of the
# managed installs below apply there.
PACMAN_PACKAGES = {"yt-dlp": "yt-dlp", "streamlink": "streamlink",
                   "ffmpeg": "ffmpeg", "deno": "deno"}
# deno, yt-dlp and streamlink get managed installs on Linux rather than apt: the
# first two have no usable apt package, and apt's streamlink predates
# --http-cookies-file, which the relay's YouTube serve needs. (#350)

# The Ookla speedtest CLI stays OUT of TOOLS so its absence never turns the
# preflight tool-chain into a FAIL; a bandwidth check is advisory.
# Windows takes it from winget; mac/Linux download the official tarball directly
# because Homebrew refuses the teamookla tap as untrusted.
SPEEDTEST_WINGET_ID = "Ookla.Speedtest.CLI"
SPEEDTEST_BIN_NAME = "speedtest"
SPEEDTEST_VERSION = "1.2.0"
SPEEDTEST_URL_TMPL = "https://install.speedtest.net/app/cli/ookla-speedtest-{ver}-{tag}.tgz"
# tag -> sha256 of the official tarball.
SPEEDTEST_DOWNLOADS = {
    "macosx-universal": "c9f8192149ebc88f8699998cecab1ce144144045907ece6f53cf50877f4de66f",
    "linux-x86_64":     "5690596c54ff9bed63fa3732f818a05dbc2db19ad36ed68f21ca5f64d5cfeeb7",
    "linux-aarch64":    "3953d231da3783e2bf8904b6dd72767c5c6e533e163d3742fd0437affa431bd3",
}


def speedtest_asset_tag(platform, machine):
    """Map (sys.platform, platform.machine()) to a SPEEDTEST_DOWNLOADS tag, or
    None for Windows (winget handles it) and unsupported arches."""
    if platform == "darwin":
        return "macosx-universal"   # covers Intel and Apple Silicon
    if platform.startswith("linux"):
        m = (machine or "").lower()
        if m in ("x86_64", "amd64"):
            return "linux-x86_64"
        if m in ("aarch64", "arm64"):
            return "linux-aarch64"
    return None


def speedtest_download_url(tag, ver=SPEEDTEST_VERSION):
    return SPEEDTEST_URL_TMPL.format(ver=ver, tag=tag)


def speedtest_install_commands(manager):
    """Package-manager commands to install speedtest (Windows winget only). On
    mac/Linux the install is a direct download; see install_speedtest_binary()."""
    if manager == "winget":
        return [["winget", "install", "--id", SPEEDTEST_WINGET_ID, "-e",
                 "--accept-package-agreements", "--accept-source-agreements"]]
    return []


def speedtest_update_commands(manager):
    if manager == "winget":
        return [["winget", "upgrade", "--id", SPEEDTEST_WINGET_ID, "-e",
                 "--accept-package-agreements", "--accept-source-agreements"]]
    return []


def install_speedtest_binary(dest_dir, tag, opener=None, downloads=None):
    """Download Ookla's CLI tarball for `tag`, verify its SHA-256 against the
    pinned value, extract the `speedtest` binary into dest_dir and make it
    executable. Returns the binary path. Raises on a checksum mismatch or an
    unexpected archive layout. `opener` (url -> bytes) is injectable for tests."""
    import hashlib
    import io
    import tarfile
    downloads = downloads or SPEEDTEST_DOWNLOADS
    want = downloads[tag]
    if opener is None:
        def opener(url):
            return http_util.get_bytes(url, timeout=60)   # nosec - pinned Ookla host, checksum-verified
    blob = opener(speedtest_download_url(tag))
    got = hashlib.sha256(blob).hexdigest()
    if got != want:
        raise RuntimeError(
            f"speedtest download checksum mismatch for {tag}: {got} != {want}")
    os.makedirs(dest_dir, exist_ok=True)
    binpath = os.path.join(dest_dir, SPEEDTEST_BIN_NAME)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
        member = tf.getmember(SPEEDTEST_BIN_NAME)   # KeyError if the layout changes
        if not member.isfile():
            raise RuntimeError("unexpected speedtest archive layout")
        src = tf.extractfile(member)
        with open(binpath, "wb") as out:
            shutil.copyfileobj(src, out)
    os.chmod(binpath, 0o700)   # owner rwx only; racecast runs it as the producer
    return binpath


# deno has no apt package, so Linux downloads a pinned, SHA-256-verified release
# into the managed bin dir that _ensure_tool_path() puts on PATH.
DENO_VERSION = "2.8.3"
DENO_BIN_NAME = "deno"
DENO_URL_TMPL = ("https://github.com/denoland/deno/releases/download/"
                 "v{ver}/deno-{tag}.zip")
# tag -> sha256 of the official linux release zip.
DENO_DOWNLOADS = {
    "x86_64-unknown-linux-gnu":  "30455b845ffa6082209c3590269c910ad3b7efdf28c9879afd4006c47ae54197",
    "aarch64-unknown-linux-gnu": "d4589cc1ffcbf1995c92a0127d932aaf832ac70cfdcc6d5b7bf38043cf303575",
}


def deno_asset_tag(platform, machine):
    """Map (sys.platform, platform.machine()) to a DENO_DOWNLOADS tag, or None for
    Windows/macOS (their package managers handle deno) and unsupported arches."""
    if platform.startswith("linux"):
        m = (machine or "").lower()
        if m in ("x86_64", "amd64"):
            return "x86_64-unknown-linux-gnu"
        if m in ("aarch64", "arm64"):
            return "aarch64-unknown-linux-gnu"
    return None


def deno_download_url(tag, ver=DENO_VERSION):
    return DENO_URL_TMPL.format(ver=ver, tag=tag)


def install_deno_binary(dest_dir, tag, opener=None, downloads=None):
    """Download deno's release zip for `tag`, verify its SHA-256 against the pinned
    value, extract the single `deno` executable into dest_dir, and make it
    executable. Returns the binary path. Raises on a checksum mismatch or an
    unexpected archive layout. `opener` (url -> bytes) is injectable for tests."""
    import hashlib
    import io
    import zipfile
    downloads = downloads or DENO_DOWNLOADS
    want = downloads[tag]
    if opener is None:
        def opener(url):
            return http_util.get_bytes(url, timeout=120)   # nosec - pinned GitHub host, checksum-verified
    blob = opener(deno_download_url(tag))
    got = hashlib.sha256(blob).hexdigest()
    if got != want:
        raise RuntimeError(
            f"deno download checksum mismatch for {tag}: {got} != {want}")
    os.makedirs(dest_dir, exist_ok=True)
    binpath = os.path.join(dest_dir, DENO_BIN_NAME)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        member = next((n for n in zf.namelist()
                       if os.path.basename(n) == DENO_BIN_NAME), None)
        if member is None:
            raise RuntimeError("unexpected deno archive layout")
        with zf.open(member) as src, open(binpath, "wb") as out:
            shutil.copyfileobj(src, out)
    os.chmod(binpath, 0o700)   # owner rwx only; racecast runs it as the producer
    return binpath


# apt's yt-dlp lags upstream and cannot pass YouTube's bot-check, so Linux pulls a
# pinned release binary instead. The asset is bare, so there is no extraction step.
YTDLP_VERSION = "2026.06.09"
YTDLP_BIN_NAME = "yt-dlp"
YTDLP_URL_TMPL = ("https://github.com/yt-dlp/yt-dlp/releases/download/"
                  "{ver}/yt-dlp_{tag}")
# tag -> sha256 of the official release asset.
YTDLP_DOWNLOADS = {
    "linux":         "bf8aac79b72287a6d2043074415132558b43743a8f9461a22b0141e90f16ce66",
    "linux_aarch64": "cabd246445bdfde0eda0dfe68bbe90354be83f3fdbbf077df11a2ea55f41cdbd",
}


def ytdlp_asset_tag(platform, machine):
    """Map (sys.platform, platform.machine()) to a YTDLP_DOWNLOADS tag, or None for
    Windows/macOS (their package managers ship yt-dlp) and unsupported arches."""
    if platform.startswith("linux"):
        m = (machine or "").lower()
        if m in ("x86_64", "amd64"):
            return "linux"
        if m in ("aarch64", "arm64"):
            return "linux_aarch64"
    return None


def ytdlp_download_url(tag, ver=YTDLP_VERSION):
    return YTDLP_URL_TMPL.format(ver=ver, tag=tag)


def install_ytdlp_binary(dest_dir, tag, opener=None, downloads=None):
    """Download yt-dlp's standalone Linux binary for `tag`, verify its SHA-256
    against the pinned value, write it to dest_dir/yt-dlp, and make it executable.
    Returns the binary path. Raises on a checksum mismatch. `opener` (url ->
    bytes) is injectable for tests."""
    import hashlib
    downloads = downloads or YTDLP_DOWNLOADS
    want = downloads[tag]
    if opener is None:
        def opener(url):
            return http_util.get_bytes(url, timeout=120)   # nosec - pinned GitHub host, checksum-verified
    blob = opener(ytdlp_download_url(tag))
    got = hashlib.sha256(blob).hexdigest()
    if got != want:
        raise RuntimeError(
            f"yt-dlp download checksum mismatch for {tag}: {got} != {want}")
    os.makedirs(dest_dir, exist_ok=True)
    binpath = os.path.join(dest_dir, YTDLP_BIN_NAME)
    with open(binpath, "wb") as out:
        out.write(blob)
    os.chmod(binpath, 0o700)   # owner rwx only; racecast runs it as the producer
    return binpath


# apt's streamlink lacks --http-cookies-file (added in 8.2.0), which the relay's
# YouTube serve needs; an older build aborts the feed with "unrecognized arguments"
# (#350). streamlink ships only as a PyPI package, so Linux installs the pin into an
# isolated venv and links its entrypoint into the managed bin dir.
# The pin must stay at or above preflight.PF_MIN_STREAMLINK.
STREAMLINK_VERSION = "8.4.0"
STREAMLINK_SPEC = "streamlink==" + STREAMLINK_VERSION


def streamlink_needs_managed_install(manager):
    """True only for apt (Linux): its streamlink is too old. brew/winget ship 8.x."""
    return manager == "apt"


def streamlink_venv_dir(runtime_dir):
    """Where the isolated streamlink venv lives: a sibling of the managed bin dir,
    which holds loose executables and symlinks only."""
    return os.path.join(runtime_dir, "streamlink-venv")


def system_python():
    """A real (non-frozen) python3 to build the streamlink venv. Under PyInstaller
    sys.executable is the frozen racecast binary (no venv/pip module), so resolve a
    python3 on PATH instead. From source, sys.executable already is a real python."""
    if not getattr(sys, "frozen", False):
        return sys.executable
    return shutil.which("python3") or shutil.which("python")


def _relink(src, link):
    """Point `link` at `src`, replacing any existing file/symlink (idempotent so a
    re-run or --update re-links cleanly)."""
    try:
        if os.path.islink(link) or os.path.exists(link):
            os.remove(link)
    except OSError:
        pass  # nothing to remove; os.symlink below is the real step
    os.symlink(src, link)


def install_streamlink_venv(managed_bin, venv_dir, spec=STREAMLINK_SPEC,
                            python=None, run=None, symlink=None):
    """Build (or refresh) an isolated venv holding the pinned streamlink and link
    its entrypoint into the managed bin dir, which racecast puts on PATH. Returns
    the link path. `python`/`run`/`symlink` are injectable seams for tests. Raises
    RuntimeError when no system python3 is available (needs python3-venv)."""
    python = python or system_python()
    if not python:
        raise RuntimeError("no system python3 found to build the streamlink venv "
                           "(install python3-venv / python3-pip)")
    run = run or subprocess.check_call
    symlink = symlink or _relink
    run([python, "-m", "venv", venv_dir])
    venv_py = os.path.join(venv_dir, "bin", "python")
    run([venv_py, "-m", "pip", "install", "--upgrade", spec])
    os.makedirs(managed_bin, exist_ok=True)
    link = os.path.join(managed_bin, "streamlink")
    symlink(os.path.join(venv_dir, "bin", "streamlink"), link)
    return link


def glibc_version(libc_ver_output):
    """Parse platform.libc_ver()'s (lib, version) tuple into a (major, minor)
    int pair, or None when the C library is not glibc or the version does not
    parse. None means 'cannot tell', so callers must not block."""
    lib, ver = (libc_ver_output or ("", ""))
    if lib != "glibc" or not ver:
        return None
    parts = ver.split(".")
    try:
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except (ValueError, IndexError):
        return None


def min_os_error(libc_tuple, floor=MIN_GLIBC_TOOLS):
    """An 'unsupported OS' message when `libc_tuple` is below `floor`, else None.
    An undeterminable `libc_tuple` returns None so it never blocks."""
    if libc_tuple is None or libc_tuple >= floor:
        return None
    have = f"{libc_tuple[0]}.{libc_tuple[1]}"
    need = f"{floor[0]}.{floor[1]}"
    return (f"Unsupported OS: glibc {have} < {need}.\n"
            "deno requires glibc >= 2.35 (Ubuntu 22.04+); the racecast binary needs "
            "2.38 (Ubuntu 24.04).\nUse Ubuntu 24.04 LTS. Aborting.")


def pick_manager(platform, which=shutil.which):
    """Package manager for this platform, or None (-> manual guide).
    On Linux apt wins when both are present, so a Debian box that happens to carry
    a pacman port keeps the path it has always used."""
    if platform.startswith("win"):
        return "winget" if which("winget") else None
    if platform == "darwin":
        return "brew" if which("brew") else None
    if which("apt-get"):
        return "apt"
    return "pacman" if which("pacman") else None


def missing_tools(which=shutil.which):
    return [t for t in TOOLS if not which(t)]


def windows_fresh_path(read_values=None):
    """The PATH a new shell would get, read from the registry. Installers update
    the registry, not running processes, so this process's PATH predates anything
    installed during this run. Returns None on non-Windows."""
    if read_values is None:
        if not sys.platform.startswith("win"):
            return None
        read_values = _registry_path_values
    parts = [os.path.expandvars(v) for v in read_values() if v]
    return os.pathsep.join(parts) or None


def _registry_path_values():
    import winreg
    values = []
    for root, key in ((winreg.HKEY_LOCAL_MACHINE,
                       r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                      (winreg.HKEY_CURRENT_USER, "Environment")):
        try:
            with winreg.OpenKey(root, key) as k:
                values.append(winreg.QueryValueEx(k, "Path")[0])
        except OSError:
            pass  # key absent (e.g. no user Path); skip that hive
    return values


def install_commands(manager, tools, brew_path="brew", sudo=False):
    """The argv list(s) to install `tools` with `manager`. `sudo` prepends sudo to
    the apt commands: apt-get needs root and, unlike winget/brew, does not prompt
    for it."""
    if manager == "winget":
        return [["winget", "install", "--id", WINGET_IDS[t], "-e",
                 "--accept-source-agreements", "--accept-package-agreements"]
                for t in tools]
    if manager == "brew":
        return [[brew_path, "install"] + list(tools)] if tools else []
    if manager == "apt":
        pkgs = [APT_PACKAGES[t] for t in tools if t in APT_PACKAGES]
        if not pkgs:
            return []
        pre = ["sudo"] if sudo else []
        # A stale index cannot locate the packages. (#408)
        return [pre + ["apt-get", "update"],
                pre + ["apt-get", "install", "-y"] + pkgs]
    if manager == "pacman":
        pkgs = [PACMAN_PACKAGES[t] for t in tools if t in PACMAN_PACKAGES]
        if not pkgs:
            return []
        pre = ["sudo"] if sudo else []
        # Not `-Sy`: refreshing the list without upgrading is Arch's partial-upgrade
        # trap. A stale database makes pacman say "target not found", and the fix is
        # the operator's own `pacman -Syu`, never ours to force.
        return [pre + ["pacman", "-S", "--needed", "--noconfirm"] + pkgs]
    return []


def update_commands(manager, tools, brew_path="brew", sudo=False):
    """The argv list(s) to upgrade already-installed `tools` with `manager`.
    winget's "no applicable update" exit code is whitelisted in
    installer_common.install_exit_ok; brew exits 0 for up-to-date formulae."""
    if manager == "winget":
        return [["winget", "upgrade", "--id", WINGET_IDS[t], "-e",
                 "--accept-source-agreements", "--accept-package-agreements"]
                for t in tools]
    if manager == "brew":
        return [[brew_path, "upgrade"] + list(tools)] if tools else []
    if manager == "apt":
        pkgs = [APT_PACKAGES[t] for t in tools if t in APT_PACKAGES]
        if not pkgs:
            return []
        pre = ["sudo"] if sudo else []
        # Refresh the index before upgrading. (#408)
        return [pre + ["apt-get", "update"],
                pre + ["apt-get", "install", "-y", "--only-upgrade"] + pkgs]
    if manager == "pacman":
        # Upgrading single packages on a rolling release is the partial upgrade we
        # must avoid, and `pacman -Syu` upgrades the whole machine, which is the
        # operator's call. main() prints that pointer instead.
        return []
    return []


def manual_guide(platform, manager=None):
    if manager == "pacman":
        pkgs = " ".join(PACMAN_PACKAGES[t] for t in TOOLS)
        return ("Install manually:  sudo pacman -S --needed " + pkgs + "\n"
                "  (if pacman reports 'target not found', its package list is stale;\n"
                "   run `sudo pacman -Syu` first; never `pacman -Sy` on its own)\n"
                "bandwidth speed test (Ookla CLI): the distro package `speedtest-cli`\n"
                "  installs a DIFFERENT tool under the same name. Remove it and take\n"
                "  the Linux build from https://www.speedtest.net/apps/cli")
    if platform.startswith("win"):
        return ("Install manually with winget (one per line):\n"
                + "\n".join(f"  winget install --id {WINGET_IDS[t]} -e" for t in TOOLS)
                + f"\n  winget install --id {SPEEDTEST_WINGET_ID} -e   # bandwidth speed test")
    if platform == "darwin":
        return ("Install manually:  brew install yt-dlp streamlink ffmpeg deno\n"
                "  bandwidth speed test (Ookla CLI): download the macOS build from\n"
                "  https://www.speedtest.net/apps/cli and put `speedtest` on your PATH")
    return ("Install manually:  sudo apt-get update && sudo apt-get install -y ffmpeg\n"
            "yt-dlp, deno, and streamlink are managed installs (apt's are too old);\n"
            "install-tools sets them up automatically. Manually:\n"
            "  yt-dlp:     https://github.com/yt-dlp/yt-dlp#installation\n"
            "  deno:       https://docs.deno.com/runtime/getting_started/installation/\n"
            "  streamlink: python3 -m venv sl && sl/bin/pip install 'streamlink>=8.2.0'\n"
            "              (apt's 6.6.2 lacks --http-cookies-file; needs >=8.2.0)\n"
            "bandwidth speed test (Ookla CLI): download the Linux build from\n"
            "  https://www.speedtest.net/apps/cli and put `speedtest` on your PATH")


def _which_with_managed_bin(managed_dir, brew=None):
    """which() that also looks in the racecast-managed bin dir, which is never on
    the user's shell PATH, and on macOS in brew's bin dir, which is not on PATH
    right after a fresh bootstrap. _ensure_tool_path() puts managed_dir on PATH for
    the real runs; this probe lets install-tools confirm the install without it."""
    prefix_bin = os.path.dirname(brew) if brew else None

    def probe(name):
        hit = shutil.which(name)
        if hit:
            return hit
        for d in (managed_dir, prefix_bin):
            if d:
                cand = os.path.join(d, name)
                if os.path.exists(cand):
                    return cand
        return None
    return probe


def _which_with_fresh_path(fresh_path):
    """which() that falls back to the registry PATH on Windows: a just-installed
    tool is not on this process's PATH yet, but a new shell will see it."""
    def probe(name):
        hit = shutil.which(name)
        if hit:
            return hit
        return shutil.which(name, path=fresh_path) if fresh_path else None
    return probe


def _note_new_terminal(which=shutil.which):
    """Installed tools may not be on this shell's PATH yet, because installers
    update the registry or shell profile, not running shells. Tools resolved via
    the managed bin dir do not trigger the note, since racecast puts that dir on
    PATH itself."""
    not_on_path = [t for t in TOOLS if not which(t)]
    if not_on_path:
        print("NOTE: open a NEW terminal before `racecast preflight` / `racecast relay start`.")
        print("      not on this shell's PATH yet:", ", ".join(not_on_path))


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="install-tools", add_help=True)
    ap.add_argument("--yes", action="store_true",
                    help="skip the Homebrew bootstrap confirmation (macOS)")
    ap.add_argument("--update", action="store_true",
                    help="also upgrade the already-installed tools to their "
                         "latest versions (recommended before every event)")
    ap.add_argument("--runtime-dir", default=None,
                    help="base runtime dir for the managed speedtest binary "
                         "(default: the project runtime dir)")
    a = ap.parse_args()

    import platform as _platform
    # A clear message beats a cryptic loader error mid-download. (#409)
    if sys.platform.startswith("linux"):
        err = min_os_error(glibc_version(_platform.libc_ver()))
        if err:
            sys.exit(err)

    import speedtest as st
    runtime_dir = a.runtime_dir or st.default_runtime_dir(
        os.path.dirname(os.path.abspath(__file__)))

    missing = missing_tools(which=_which_with_fresh_path(windows_fresh_path()))
    # find_binary() looks on PATH and in the managed bin dir, so a setup whose core
    # tools are already present still gets speedtest.
    speedtest_missing = st.find_binary(runtime_dir) is None
    if not missing and not speedtest_missing and not a.update:
        print("All external tools already installed:", ", ".join(TOOLS) + ", speedtest")
        print("  (run `racecast install-tools --update` to upgrade them)")
        _note_new_terminal()
        return
    if missing:
        print("Missing tools:", ", ".join(missing))

    brew = None
    if sys.platform == "darwin":
        brew = _common().find_brew()
        if not brew:
            brew = _common().bootstrap_brew(a.yes)
        if not brew:
            sys.exit("No supported package manager found.\n" + manual_guide(sys.platform))
        manager = "brew"
    else:
        manager = pick_manager(sys.platform)
        if manager is None:
            sys.exit("No supported package manager found.\n" + manual_guide(sys.platform))

    # apt and pacman need root and, unlike winget/brew, do not prompt for it.
    sudo = manager in ("apt", "pacman") and hasattr(os, "geteuid") and os.geteuid() != 0
    cmds = []
    if a.update:
        present = [t for t in TOOLS if t not in missing]
        if present:
            print("Updating installed tools:", ", ".join(present))
            cmds += update_commands(manager, present, brew_path=brew or "brew", sudo=sudo)
        if manager == "pacman" and present:
            # A per-package upgrade would be the partial upgrade; say so rather
            # than silently doing nothing.
            print("NOTE: on Arch these are repository packages. Upgrade them with the")
            print("      system:  sudo pacman -Syu   (a per-package upgrade would leave")
            print("      the machine in a partial-upgrade state)")
    cmds += install_commands(manager, missing, brew_path=brew or "brew", sudo=sudo)
    # mac/Linux take the direct download after the command loop. Best-effort: this
    # never blocks the core tools.
    if manager == "winget":
        if speedtest_missing:
            cmds += speedtest_install_commands("winget")
        elif a.update:
            cmds += speedtest_update_commands("winget")

    failed = []
    for cmd in cmds:
        print("Running:", " ".join(cmd))
        if not _common().install_exit_ok(manager, subprocess.call(cmd)):
            failed.append(" ".join(cmd))

    # Windows already got speedtest via winget above.
    if manager != "winget" and (speedtest_missing or a.update):
        tag = speedtest_asset_tag(sys.platform, _platform.machine())
        if tag is None:
            print("NOTE: no prebuilt Ookla speedtest CLI for this OS/arch. See")
            print("      https://www.speedtest.net/apps/cli")
        else:
            dest = st.managed_bin_dir(runtime_dir)
            print(f"Installing Ookla speedtest CLI v{SPEEDTEST_VERSION} -> {dest} ...")
            try:
                install_speedtest_binary(dest, tag)
                print("  speedtest installed.")
            except Exception as exc:   # report, don't crash
                failed.append(f"speedtest download ({exc})")

    # deno has no apt package; winget and brew already installed it above.
    if manager == "apt" and "deno" in missing:
        tag = deno_asset_tag(sys.platform, _platform.machine())
        if tag is None:
            print("NOTE: no prebuilt deno for this OS/arch. Install it manually:")
            print("  https://docs.deno.com/runtime/getting_started/installation/")
        else:
            dest = st.managed_bin_dir(runtime_dir)
            print(f"Installing deno v{DENO_VERSION} -> {dest} ...")
            try:
                install_deno_binary(dest, tag)
                print("  deno installed.")
            except Exception as exc:   # report, don't crash
                failed.append(f"deno download ({exc})")

    # Refreshed on --update too, so the pre-event run bumps the pin. (#409)
    if manager == "apt" and ("yt-dlp" in missing or a.update):
        tag = ytdlp_asset_tag(sys.platform, _platform.machine())
        if tag is None:
            print("NOTE: no prebuilt yt-dlp for this OS/arch. Install it manually:")
            print("  https://github.com/yt-dlp/yt-dlp#installation")
        else:
            dest = st.managed_bin_dir(runtime_dir)
            print(f"Installing yt-dlp v{YTDLP_VERSION} -> {dest} ...")
            try:
                install_ytdlp_binary(dest, tag)
                print("  yt-dlp installed.")
            except Exception as exc:   # report, don't crash
                failed.append(f"yt-dlp download ({exc})")

    # Refreshed on --update too, alongside yt-dlp. (#350)
    if streamlink_needs_managed_install(manager) and ("streamlink" in missing or a.update):
        dest = st.managed_bin_dir(runtime_dir)
        venv = streamlink_venv_dir(runtime_dir)
        print(f"Installing streamlink v{STREAMLINK_VERSION} (venv) -> {venv} ...")
        try:
            install_streamlink_venv(dest, venv)
            print("  streamlink installed.")
        except Exception as exc:   # report, don't crash
            failed.append(f"streamlink venv ({exc})")

    managed_bin = st.managed_bin_dir(runtime_dir)
    if manager == "winget":
        # The installs just changed the registry PATH; re-read it for the check.
        still = missing_tools(which=_which_with_fresh_path(windows_fresh_path()))
    else:
        # runtime/bin holds the direct downloads and is never on the shell PATH.
        still = missing_tools(which=_which_with_managed_bin(managed_bin, brew))
    if failed or still:
        parts = ["Some installs did not complete."]
        if failed:
            parts.append("Failed: " + "; ".join(failed))
        if still:
            parts.append("Still missing: " + ", ".join(still))
        sys.exit("\n".join(parts) + "\n" + manual_guide(sys.platform, manager))
    # Tools may sit in brew's prefix, the registry or the managed bin dir without
    # being on this shell's PATH.
    _note_new_terminal(
        _which_with_fresh_path(windows_fresh_path()) if manager == "winget"
        else _which_with_managed_bin(managed_bin, brew))
    print("All tools " + ("up to date" if a.update else "installed")
          + ". Run `racecast preflight` to verify.")


if __name__ == "__main__":
    main()

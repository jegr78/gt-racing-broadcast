#!/usr/bin/env python3
"""Pre-flight readiness check for the GT Racing broadcast setup.

Run before an event to confirm this machine can run OBS + the relay:
hardware, tool chain, ports, and YouTube cookies. Prints a traffic-light
report; exit code is 0 if nothing FAILs, 1 otherwise.

Usage:  python3 scripts/preflight.py
Pure Python 3 standard library — no third-party dependencies.
"""
import argparse
import csv
import ctypes
import io
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote
import http_util

from services import external_tool_env  # de-PyInstaller the env for spawned tool probes

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"


@dataclass
class Result:
    level: str
    name: str
    detail: str


# --------------------------------------------------------------------------
# Classifiers (pure — raw number -> Result). Single source of truth for the
# system-requirement thresholds.
# --------------------------------------------------------------------------
# Nominal 16/32 GB modules report ~0.1-1.5 GB lower (firmware/iGPU
# reservations) — without slack a physical 32 GB machine could never PASS.
RAM_SLACK_GB = 1.5


def classify_ram(gb):
    # The measured production footprint (OBS + up to three relay feeds + Discord +
    # the control browser + a cloud desktop) is ~6-9 GB, so 12 GB is comfortable
    # headroom and 8 GB the practical floor. A GPU cloud box with 16 GB is green.
    if gb < 8 - RAM_SLACK_GB:
        return Result(FAIL, "RAM", f"{gb:.1f} GB — below the 8 GB minimum")
    if gb < 12 - RAM_SLACK_GB:
        return Result(WARN, "RAM",
                      f"{gb:.1f} GB — works; 12 GB recommended "
                      f"(OBS + the relay feeds are memory-heavy)")
    return Result(PASS, "RAM", f"{gb:.1f} GB")


def classify_cpu(n, has_gpu=False):
    # The core hog is OBS's software (x264) encode. When an NVENC GPU is present
    # the encode is offloaded to the GPU, so the CPU floor drops by 2: a
    # g2-standard-4 (4 cores + L4) validated a real broadcast (spike #395).
    fail_floor, pass_floor = (2, 4) if has_gpu else (4, 6)
    reason = ("hardware NVENC offloads the encode"
              if has_gpu else "software x264 encode + multiple feeds")
    if n < fail_floor:
        return Result(FAIL, "CPU cores",
                      f"{n} logical cores — below the {fail_floor}-core minimum")
    if n < pass_floor:
        return Result(WARN, "CPU cores",
                      f"{n} logical cores — works; {pass_floor}+ recommended ({reason})")
    return Result(PASS, "CPU cores", f"{n} logical cores")


def detect_nvidia_gpu(run=subprocess.run, which=shutil.which, os_name=None):
    """Best-effort: is an NVIDIA GPU (NVENC hardware encoder) present? A GPU
    offloads OBS's H.264/HEVC encode off the CPU, so classify_cpu relaxes the
    core floor when this is True. Returns False on any error — an undetected
    encoder simply falls back to the stricter software-encode thresholds, and
    non-NVIDIA hardware encoders (Apple VideoToolbox, Intel QSV) are not probed
    here. `run`/`which`/`os_name` are injectable seams for the unit test."""
    os_name = os.name if os_name is None else os_name
    # nvidia-smi (driver + GPU) works on both Linux and Windows once the driver
    # is installed — the production signal on the provisioned cloud box.
    if which("nvidia-smi"):
        try:
            r = run(["nvidia-smi", "-L"], capture_output=True, timeout=5,
                    **no_window_kwargs(os_name))
            if r.returncode == 0 and b"GPU" in (r.stdout or b""):
                return True
        except Exception:  # noqa: BLE001 — detection is best-effort, never fatal
            pass
    # Linux fallback: the card is on the PCI bus even before the driver loads.
    if os_name == "posix" and which("lspci"):
        try:
            r = run(["lspci"], capture_output=True, timeout=5)
            if r.returncode == 0 and b"nvidia" in (r.stdout or b"").lower():
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


def classify_disk(gb):
    if gb < 2:
        return Result(FAIL, "Free disk", f"{gb:.1f} GB free — below the 2 GB minimum")
    if gb < 5:
        return Result(WARN, "Free disk", f"{gb:.1f} GB free — low; 5 GB+ recommended")
    return Result(PASS, "Free disk", f"{gb:.1f} GB free")


def classify_swap(gb):
    if gb > 1:
        return Result(WARN, "Swap in use",
                      f"{gb:.1f} GB swapped — not a fresh boot / under memory "
                      f"pressure; reboot before the event")
    return Result(PASS, "Swap in use", f"{gb:.1f} GB swapped")


# --------------------------------------------------------------------------
# Platform readers (isolated per-OS; return raw numbers)
# --------------------------------------------------------------------------
class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def _win_memstatus():
    stat = _MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
    return stat


def read_ram_bytes():
    if sys.platform == "darwin":
        return int(subprocess.check_output(["sysctl", "-n", "hw.memsize"]).strip())
    if sys.platform.startswith("linux"):
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024  # kB -> bytes
        return 0
    if sys.platform.startswith("win"):
        return _win_memstatus().ullTotalPhys
    raise OSError(f"unsupported platform: {sys.platform}")


def read_swap_used_bytes():
    if sys.platform == "darwin":
        out = subprocess.check_output(["sysctl", "-n", "vm.swapusage"]).decode()
        match = re.search(r"used\s*=\s*([\d.]+)([KMG])", out)
        if not match:
            return 0
        mult = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}[match.group(2)]
        return int(float(match.group(1)) * mult)
    if sys.platform.startswith("linux"):
        total = free = 0
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("SwapTotal:"):
                    total = int(line.split()[1]) * 1024
                elif line.startswith("SwapFree:"):
                    free = int(line.split()[1]) * 1024
        return max(0, total - free)
    if sys.platform.startswith("win"):
        stat = _win_memstatus()
        return max(0, stat.ullTotalPageFile - stat.ullAvailPageFile)
    return 0


def disk_free_bytes(path):
    return shutil.disk_usage(path).free


# --------------------------------------------------------------------------
# Probes
# --------------------------------------------------------------------------
def port_free(port, host="127.0.0.1"):
    """True if nothing is bound to host:port (a fresh socket can bind it)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def port_reachable(host, port, timeout=0.5):
    """True if a TCP connection to host:port succeeds (a service is listening)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def companion_probe_hosts(bind_ip=None, tailscale_ip=None):
    """Ordered, de-duplicated hosts to probe for a running Companion.

    `racecast companion start` binds Companion to the Tailscale IP (tailnet-only, NOT
    loopback), so a 127.0.0.1-only reachability probe false-negatives — Companion is up
    but preflight reports "not reachable yet". Probe the config's `bind_ip` and the
    Tailscale IP as well. An empty or 0.0.0.0 (wildcard) bind maps to loopback, and
    127.0.0.1 is always kept as a fallback so behaviour is unchanged when neither is known.
    """
    hosts = []
    for h in (bind_ip, tailscale_ip, "127.0.0.1"):
        h = (h or "").strip()
        if not h or h == "0.0.0.0":
            h = "127.0.0.1"
        if h not in hosts:
            hosts.append(h)
    return hosts


def no_window_kwargs(os_name=None):
    """Popen/run kwargs that stop a console child from flashing its own terminal
    window on Windows. tool_version() is called IN-PROCESS by the Control Center's
    `tools`/`preflight` status providers, and racecast-ui.exe is a --windowed
    (console-less) app, so every `<tool> --version` probe otherwise pops a
    transient terminal — one per tool (issue #23's class, missed for the version
    probes). CREATE_NO_WINDOW gives the child a hidden console; capture_output
    keeps the version text either way, so the flag is safe. Empty (no-op) off
    Windows so the same call site stays cross-platform. Mirrors
    services.no_window_kwargs — preflight imports nothing from its siblings (its
    test loads it standalone), so the flag is duplicated here, like the relay's
    racecast-feeds._no_window_kwargs copy."""
    os_name = os.name if os_name is None else os_name
    if os_name == "nt":
        CREATE_NO_WINDOW = 0x08000000
        return {"creationflags": CREATE_NO_WINDOW}
    return {}


def tool_version(name, run=subprocess.run, which=shutil.which):
    """Return the first line of `<name> --version`, or None if not on PATH.
    `run`/`which` are injectable seams for the unit test."""
    path = which(name)
    if not path:
        return None
    try:
        out = run([name, "--version"], capture_output=True,
                  text=True, errors="replace", timeout=10,
                  env=external_tool_env(), **no_window_kwargs())
        lines = (out.stdout or out.stderr).strip().splitlines()
        return lines[0] if lines else path
    except Exception:
        return path


# --------------------------------------------------------------------------
# Cookies
# --------------------------------------------------------------------------
COOKIE_MARKERS = ("SAPISID", "__Secure-3PSID", "__Secure-1PSID", "LOGIN_INFO")


def resolve_cookies_path(preflight_file, runtime_dir=None, cookies_opt=None):
    """Locate yt-cookies.txt the way the relay does.

    Priority: explicit --cookies, then --runtime-dir/yt-cookies.txt, then the
    first existing candidate (package layout scripts/+relay/, repo layout
    src/scripts/+runtime/, or next to this script). Falls back to the
    package-expected path so the report names a sensible location.
    """
    if cookies_opt:
        return cookies_opt
    if runtime_dir:
        yt_ck = os.path.join(runtime_dir, "yt-cookies.txt")
        if os.path.isfile(yt_ck):
            return yt_ck
        legacy = os.path.join(runtime_dir, "cookies.txt")
        if os.path.isfile(legacy):
            return legacy
        return yt_ck  # neither exists: report the canonical name
    here = os.path.dirname(os.path.abspath(preflight_file))
    candidates = [
        os.path.join(here, "..", "relay", "yt-cookies.txt"),          # package layout
        os.path.join(here, "..", "..", "runtime", "yt-cookies.txt"),  # repo layout
        os.path.join(here, "yt-cookies.txt"),
    ]
    legacy_candidates = [
        os.path.join(here, "..", "relay", "cookies.txt"),
        os.path.join(here, "..", "..", "runtime", "cookies.txt"),
        os.path.join(here, "cookies.txt"),
    ]
    for cand in candidates:
        if os.path.isfile(cand):
            return os.path.abspath(cand)
    for cand in legacy_candidates:
        if os.path.isfile(cand):
            return os.path.abspath(cand)
    return os.path.abspath(candidates[0])


def cookies_status(path, max_age_hours=12, now=None):
    now = time.time() if now is None else now
    if not os.path.isfile(path):
        return Result(WARN, "yt-cookies.txt",
                      f"not found at {path} — run `racecast cookies firefox` before the event")
    age_h = (now - os.path.getmtime(path)) / 3600
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        text = ""
    has_login = any(marker in text for marker in COOKIE_MARKERS)
    if age_h > max_age_hours:
        return Result(WARN, "yt-cookies.txt",
                      f"{age_h:.0f} h old — cookies rotate; re-run `racecast cookies firefox`")
    if not has_login:
        return Result(WARN, "yt-cookies.txt",
                      "present but no logged-in YouTube session markers found")
    return Result(PASS, "yt-cookies.txt",
                  f"present, fresh ({age_h:.0f} h old), logged-in markers found")


# --------------------------------------------------------------------------
# Google Sheet (the schedule/HUD source — a shared production resource)
# --------------------------------------------------------------------------
SHEET_TAB = "Schedule"   # keep in sync with the relay's DEFAULT_SHEET_TAB


def fetch_sheet_csv(sheet_id, tab=SHEET_TAB, timeout=10):
    """Network probe, kept apart from the pure classifier. Returns (kind, payload):
    ('ok', body) | ('network', why) for a timeout/connection error (NOT a sharing
    problem) | ('forbidden', why) for 401/403 | ('not_found', why) for 404 |
    ('error', why) for anything else."""
    url = (f"https://docs.google.com/spreadsheets/d/{sheet_id}"
           f"/gviz/tq?tqx=out:csv&sheet={quote(tab)}")
    try:
        with http_util.open_url(url, timeout=timeout) as resp:
            return "ok", resp.read().decode("utf-8", "replace")
    except HTTPError as exc:
        if exc.code in (401, 403):
            return "forbidden", f"HTTP {exc.code}"
        if exc.code == 404:
            return "not_found", f"HTTP {exc.code}"
        return "error", f"HTTP {exc.code}"
    except TimeoutError:
        return "network", "the read operation timed out"
    except URLError as exc:                  # DNS failure, connection refused, no route…
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError):
            return "network", "the read operation timed out"
        return "network", f"{reason}"
    except Exception as exc:  # noqa: BLE001 — anything else is a generic read failure
        return "error", f"{type(exc).__name__}: {exc}"


def classify_sheet(sheet_id, outcome=None, payload=""):
    """Pure classifier over the fetch outcome. An HTML body is Google's
    sign-in page — the classic 'sheet not shared' case. A timeout/connection
    error is a NETWORK problem (WARN), not a sharing one — don't conflate them."""
    if not sheet_id:
        return Result(WARN, "Google Sheet",
                      "RACECAST_SHEET_ID not set — set SHEET_ID in the active profile")
    if outcome == "network":
        return Result(WARN, "Google Sheet",
                      f"could not reach Google Sheets ({payload}) — slow or no "
                      f"network; retry. Sharing is fine if it loaded before.")
    if outcome == "forbidden":
        return Result(FAIL, "Google Sheet",
                      f"access denied ({payload}) — check sharing: Share -> "
                      f"'Anyone with the link: Viewer'")
    if outcome == "not_found":
        return Result(FAIL, "Google Sheet",
                      f"not found ({payload}) — wrong Sheet ID in the active profile?")
    if outcome == "error":
        return Result(FAIL, "Google Sheet",
                      f"not readable ({payload}) — check sharing "
                      f"('Anyone with the link: Viewer') or your network")
    head = (payload or "").lstrip("﻿ \t\r\n")[:200].lower()
    if head.startswith("<!doctype") or head.startswith("<html"):
        return Result(FAIL, "Google Sheet",
                      "not readable (got a sign-in page) — check sharing: "
                      "Share -> 'Anyone with the link: Viewer'")
    rows = [r for r in csv.reader(io.StringIO(payload)) if any(c.strip() for c in r)]
    if not rows:
        return Result(FAIL, "Google Sheet",
                      f"reachable but tab '{SHEET_TAB}' is empty — correct tab name?")
    return Result(PASS, "Google Sheet",
                  f"reachable ({len(rows)} row(s) in '{SHEET_TAB}')")


# --------------------------------------------------------------------------
# Applications installed? (presence only — `racecast event status` covers running)
# --------------------------------------------------------------------------
# (app key, display name, level when missing, consequence)
APP_CHECKS = (
    ("obs", "OBS Studio", FAIL, "no broadcast without OBS"),
    ("companion", "Companion", WARN, "Stream Deck buttons unavailable"),
    ("tailscale", "Tailscale", WARN, "no remote access for director/tablet"),
    ("discord", "Discord", WARN, "interview audio unavailable"),
)


def _install_apps_module(here):
    """Load sibling install_apps.py by path — works in repo, package and
    frozen bundled-data modes alike (same pattern as install_apps' own
    installer_common loader)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "install_apps", os.path.join(here, "install_apps.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def apps_section(present, web=False):
    """Classify each producer app given `present(app) -> bool`. On a web-variant
    host (no native Discord — e.g. ARM64 Linux) a missing Discord client is
    informational: interview audio comes from Discord-web in a browser."""
    results = []
    for app, pretty, miss_level, consequence in APP_CHECKS:
        if present(app):
            results.append(Result(PASS, pretty, "installed"))
        elif app == "discord" and web:
            results.append(Result(INFO, pretty,
                                  "native client not installed — interview audio via "
                                  "Discord-web in the browser (open it and join the "
                                  "voice channel manually)"))
        else:
            results.append(Result(miss_level, pretty,
                                  f"not installed — {consequence}; run `racecast install-apps`"))
    return results


# The Discord audio source uses the obs-pipewire-audio-capture plugin on Linux
# (pipewire_audio_application_capture — for native Discord AND the Discord-web
# fallback). It is NOT part of OBS core, so it must be installed separately on
# every Linux box, any architecture.
#
# Where its .so may live is owned by obs_pipewire_linux (the module that installs
# it) so there is ONE list: preflight reads it, install-apps checks against it
# before downloading a copy the machine may already have from a distro package.
from obs_pipewire_linux import (          # noqa: E402  (grouped with its users)
    pipewire_audio_candidates,
    pipewire_audio_present,
)


def classify_pipewire_audio(platform_name, present):
    """Linux-only OBS-plugin gate (None on macOS/Windows — they use their own
    native Discord capture source)."""
    if not platform_name.startswith("linux"):
        return None
    if present:
        return Result(PASS, "OBS PipeWire audio plugin", "installed")
    return Result(WARN, "OBS PipeWire audio plugin",
                  "not found — the obs-pipewire-audio-capture plugin backs the Discord "
                  "audio source on Linux; without it interview audio can't capture. "
                  "Install it (see the OBS Setup wiki).")


# streamlink floor mirrors install_tools.MIN_STREAMLINK (kept in sync deliberately;
# preflight must not import the installer module). 8.2.0 (2026-02-09) is the release
# that added --http-cookies-file, which the relay's YouTube serve uses to pass
# yt-dlp's session cookies to streamlink's manifest re-fetch (#350). An older
# streamlink (e.g. Ubuntu 24.04's apt 6.6.2) makes every cookie'd YouTube feed abort
# with "unrecognized arguments: --http-cookies-file".
PF_MIN_STREAMLINK = (8, 2, 0)

_STREAMLINK_VER_RE = re.compile(r"streamlink\s+(\d+)\.(\d+)(?:\.(\d+))?")


def parse_streamlink_version(version_line):
    """Parse `streamlink --version` output ("streamlink X.Y.Z", a package build may
    append "-N") into an (major, minor, patch) tuple. None if unrecognizable (a bare
    path fallback, empty, or None)."""
    if not version_line:
        return None
    match = _STREAMLINK_VER_RE.search(version_line)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def classify_streamlink_version(version_line):
    """Gate streamlink against the PF_MIN_STREAMLINK floor. FAIL below it (the
    relay's cookie'd YouTube serve can't work), PASS at/above. None when the version
    can't be parsed — the tool loop's plain PASS row stands, no second guess."""
    have = parse_streamlink_version(version_line)
    if have is None:
        return None
    want = ".".join(str(n) for n in PF_MIN_STREAMLINK)
    if have < PF_MIN_STREAMLINK:
        shown = ".".join(str(n) for n in have)
        return Result(FAIL, "streamlink version",
                      f"{shown} — below {want}; the relay's YouTube feed passes cookies "
                      f"via --http-cookies-file (added in streamlink {want}). Update it: "
                      "`racecast install-tools --update`.")
    return Result(PASS, "streamlink version", ".".join(str(n) for n in have))


# glibc floors mirror install_tools.MIN_GLIBC_TOOLS / MIN_GLIBC_BINARY (kept in
# sync deliberately — preflight must not import the installer module).
PF_MIN_GLIBC_TOOLS = (2, 35)
PF_MIN_GLIBC_BINARY = (2, 38)


def classify_glibc(libc_tuple):
    """Linux glibc gate. FAIL below 2.35 (deno won't run), WARN below 2.38 (the
    racecast binary needs Ubuntu 24.04), else PASS. None (undeterminable / non-glibc)
    -> None (no row)."""
    if libc_tuple is None:
        return None
    have = f"{libc_tuple[0]}.{libc_tuple[1]}"
    if libc_tuple < PF_MIN_GLIBC_TOOLS:
        return Result(FAIL, "glibc", f"{have} — below 2.35; deno/the toolchain "
                      "won't run. Use Ubuntu 24.04 LTS.")
    if libc_tuple < PF_MIN_GLIBC_BINARY:
        return Result(WARN, "glibc", f"{have} — works from source; the racecast "
                      "binary needs 2.38 (Ubuntu 24.04).")
    return Result(PASS, "glibc", have)


# --------------------------------------------------------------------------
# Reporter / CLI / orchestration
# --------------------------------------------------------------------------
COLORS = {PASS: "\033[32m", WARN: "\033[33m", FAIL: "\033[31m", INFO: "\033[36m"}
RESET = "\033[0m"

# ports the relay binds (must be FREE) and services we probe (should be REACHABLE)
FEED_PORTS = (53001, 53002, 53003, 8088)
SERVICE_PORTS = ((4455, "OBS WebSocket"), (8000, "Companion"))
REQUIRED_TOOLS = ("streamlink", "yt-dlp", "ffmpeg", "deno")


def enable_color(no_color):
    if no_color or not sys.stdout.isatty():
        return False
    if sys.platform.startswith("win"):
        try:
            kernel = ctypes.windll.kernel32
            kernel.SetConsoleMode(kernel.GetStdHandle(-11), 7)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        except Exception:
            return False
    return True


def fmt_result(result, color):
    tag = f"{COLORS.get(result.level, '')}{result.level}{RESET}" if color else result.level
    return f"  [{tag}] {result.name}: {result.detail}"


def _speedtest_max_age():
    """Staleness window in days for the stored speed-test result.
    RACECAST_SPEEDTEST_MAX_AGE_DAYS overrides; bad/non-positive -> 7."""
    raw = os.environ.get("RACECAST_SPEEDTEST_MAX_AGE_DAYS", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 7.0
    return value if value > 0 else 7.0


def _obs_benchmark_max_age():
    """Staleness window in days for the stored `racecast obs benchmark` result (#584).
    RACECAST_OBS_BENCHMARK_MAX_AGE_DAYS overrides; bad/non-positive -> 30."""
    raw = os.environ.get("RACECAST_OBS_BENCHMARK_MAX_AGE_DAYS", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 30.0
    return value if value > 0 else 30.0


def _obs_benchmark_line(preflight_file, runtime_dir):
    """The last OBS benchmark and its age, or None when it cannot be read. Reported,
    never run here: a 60-second test on every preflight gets clicked away."""
    try:
        import obs_benchmark as ob      # lazy, like speedtest: it defines its own Result
        import speedtest as st
        d = runtime_dir or st.default_runtime_dir(
            os.path.dirname(os.path.abspath(preflight_file)))
        return ob.classify(ob.load_latest(d), time.time(), _obs_benchmark_max_age())
    except Exception:   # never let the benchmark read break the report
        return None


def gather(preflight_file, runtime_dir=None, cookies_opt=None):
    """Run every check and return a list of (section_title, [Result])."""
    hardware = [
        classify_ram(read_ram_bytes() / 1024 ** 3),
        classify_cpu(os.cpu_count() or 0, detect_nvidia_gpu()),
        classify_disk(disk_free_bytes(os.getcwd()) / 1024 ** 3),
        classify_swap(read_swap_used_bytes() / 1024 ** 3),
    ]
    bench = _obs_benchmark_line(preflight_file, runtime_dir)
    if bench is not None:
        hardware.append(bench)
    tools = []
    for name in REQUIRED_TOOLS:
        version = tool_version(name)
        tools.append(Result(PASS, name, version) if version
                     else Result(FAIL, name, "not found on PATH — required by the relay"))
        if name == "streamlink" and version:
            floor = classify_streamlink_version(version)
            if floor is not None:
                tools.append(floor)
    py = sys.version.split()[0]
    tools.append(Result(PASS, "python3", py) if sys.version_info >= (3, 8)
                 else Result(FAIL, "python3", f"{py} — need 3.8+"))
    if sys.platform.startswith("linux"):   # OS floor (deno glibc 2.35 / binary 2.38)
        try:
            import platform as _pf
            import install_tools as _it
            g = classify_glibc(_it.glibc_version(_pf.libc_ver()))
            if g is not None:
                tools.append(g)
        except Exception:
            pass  # never let the glibc probe break the report
    here = os.path.dirname(os.path.abspath(preflight_file))
    try:
        import discord_web
        ia = _install_apps_module(here)
        web = discord_web.use_web(sys.platform, os.environ)
        apps = apps_section(lambda app: ia.app_present(app, sys.platform), web=web)
    except Exception as exc:  # never let a probe break the report
        apps = [Result(WARN, "applications", f"check failed: {exc}")]
    if sys.platform.startswith("linux"):   # OBS PipeWire audio plugin (Discord audio source)
        try:
            pw = classify_pipewire_audio(sys.platform, pipewire_audio_present(
                pipewire_audio_candidates(os.path.expanduser("~"), os.uname().machine)))
            if pw is not None:
                apps.append(pw)
        except Exception:
            pass  # never let the plugin probe break the report
    ports = []
    for port in FEED_PORTS:
        # "In use" is normal once the relay/feeds are running (e.g. after event
        # start) — report it as INFO, not a warning. `racecast status` shows whether
        # the occupant is the relay; only a foreign app is a real conflict.
        ports.append(Result(PASS, f"port {port}", "free") if port_free(port)
                     else Result(INFO, f"port {port}",
                                 "in use — normal if the relay/feeds are running "
                                 "(`racecast status` confirms); a conflict only if another app owns it"))
    for port, svc in SERVICE_PORTS:
        if svc == "OBS WebSocket":
            ports.append(Result(PASS, f"port {port}",
                                "OBS WebSocket reachable — one-button handover ready")
                         if port_reachable("127.0.0.1", port)
                         else Result(WARN, f"port {port}",
                                     "OBS WebSocket not reachable — NEXT can't auto-cut; "
                                     "enable obs-websocket in OBS "
                                     "(Tools -> WebSocket Server Settings)"))
        else:
            # Companion: probe where it ACTUALLY binds. racecast binds Companion to the
            # Tailscale IP (tailnet-only), so a loopback-only probe false-negatives — up
            # but reported "not reachable". Resolve the config bind_ip + the Tailscale IP;
            # any import/read failure degrades to loopback (companion_probe_hosts always
            # keeps 127.0.0.1). Not reachable on ANY host just means it hasn't launched yet
            # (event start does that) — INFO, not a warning the operator must chase down.
            bind_ip = None
            try:
                import json as _json, companion_common as _cc
                with open(_cc.companion_config_path(sys.platform), encoding="utf-8") as fh:
                    bind_ip = (_json.load(fh).get("bind_ip") or "").strip() or None
            except Exception:
                bind_ip = None
            try:
                import tailscale as _ts
                ts_ip = _ts.detect_tailscale_ip()
            except Exception:
                ts_ip = None
            reachable = any(port_reachable(h, port)
                            for h in companion_probe_hosts(bind_ip, ts_ip))
            ports.append(Result(PASS, f"port {port}", f"{svc} reachable")
                         if reachable
                         else Result(INFO, f"port {port}",
                                     f"{svc} not reachable yet — it is launched at event start"))
    cookies = [cookies_status(resolve_cookies_path(preflight_file, runtime_dir, cookies_opt))]
    sheet_id = os.environ.get("RACECAST_SHEET_ID")
    if sheet_id:
        outcome, payload = fetch_sheet_csv(sheet_id)
        sheet = [classify_sheet(sheet_id, outcome, payload)]
    else:
        sheet = [classify_sheet(None)]
    advisory = Result(INFO, "bandwidth",
                      "OBS pushes the program to YouTube WHILE the relay pulls up to "
                      "3 live feeds. Use a wired connection with stable upload headroom "
                      "above your OBS bitrate.")
    try:
        import speedtest as st          # lazy: avoids an import cycle (st imports Result from us)
        st_dir = runtime_dir or st.default_runtime_dir(
            os.path.dirname(os.path.abspath(preflight_file)))
        network = [st.classify(st.load_latest(st_dir), time.time(), _speedtest_max_age()),
                   advisory]
    except Exception:   # never let the speed-test read break the report
        network = [advisory]
    return [
        ("Hardware", hardware),
        ("Tool chain", tools),
        ("Applications", apps),
        ("Ports", ports),
        ("YouTube cookies", cookies),
        ("Google Sheet", sheet),
        ("Network", network),
    ]


def report(sections, color):
    fails = warns = 0
    for title, results in sections:
        print(f"\n{title}")
        for result in results:
            print(fmt_result(result, color))
            if result.level == FAIL:
                fails += 1
            elif result.level == WARN:
                warns += 1
    print(f"\nSummary: {fails} FAIL, {warns} WARN")
    if fails:
        print("NOT READY — resolve the FAIL items above.")
    elif warns:
        print("Usable, but review the WARN items "
              "(reboot to clear swap; start OBS/Companion; refresh cookies).")
    else:
        print("READY.")
    return 1 if fails else 0


def parse_args(argv):
    ap = argparse.ArgumentParser(
        description="Pre-flight readiness check for the GT Racing broadcast setup.")
    ap.add_argument("--runtime-dir", default=None,
                    help="Directory holding yt-cookies.txt (mirrors the relay's --runtime-dir).")
    ap.add_argument("--cookies", default=None,
                    help="Explicit path to yt-cookies.txt (overrides --runtime-dir).")
    ap.add_argument("--no-color", action="store_true", help="Disable ANSI colors.")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    color = enable_color(args.no_color)
    sections = gather(__file__, args.runtime_dir, args.cookies)
    return report(sections, color)


if __name__ == "__main__":
    sys.exit(main())

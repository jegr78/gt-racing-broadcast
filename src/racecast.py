#!/usr/bin/env python3
"""racecast operator CLI — one entrypoint for every service and setup action.

  python3 src/racecast.py relay start        # repo
  python3 racecast.py     relay start        # shipped package

  racecast relay     start|stop|restart|status|logs|run|open-panel|open-hud|open-status
  racecast companion start|stop|restart|status|logs|enable-control|open-buttons|open-admin
  racecast streams   start|stop|restart|status|logs
  racecast <svc> logs [-f] [--list] [--archive YYYY-MM-DD]   # tail merged live logs; --list archives; --archive reads one (svc: relay|streams|companion|obs|tailscale)
  racecast event     status|start|stop      # event-day readiness: check / bring-up / wind-down
  racecast event start --stint N             # takeover: stint N is on air now — the relay starts there
  racecast event start --qualifying          # bring up in qualifying mode (Feed A serves the Qualifying tab)
  racecast event start --force               # skip the pre-flight gate (start despite missing SHEET_ID/graphics)
  racecast event takeover <A-ip> [--funnel] [--stint N]  # take over from another producer: read A's on-air stint+league, pull chat, bring up at that stint; --funnel <magicdns-host> pulls state over the public Funnel using the league CONSOLE_SECRET
  racecast tailscale up|down|status          # connect / disconnect / inspect Tailscale
  racecast obs refresh                       # force-reload the relay-served OBS browser sources (HUD incl. timer)
  racecast obs collection [set]              # report the active OBS scene collection (set = switch to GT Racing Endurance)
  racecast obs stream-target <part>          # set OBS stream service+key for a Producer Part (OBS must be stopped)
  racecast obs benchmark [--window S] [--settle S] [--scene NAME] [--keep-recording] [--json]   # FULL vs ROBUST on the on-air feed with a recording running; never while streaming
  racecast obs logs | tailscale logs         # tail OBS's log dir / the Tailscale status-snapshot log (same -f/--list/--archive flags)
  racecast sheet     url | open              # print / open the active league's Google Sheet (built from its SHEET_ID)
  racecast app launch|quit obs|discord|tailscale   # start / gracefully quit a GUI app (Control Center buttons)
  racecast discord   join | leave | status   # drive the desktop Discord client into/out of the league's voice channel
  racecast status                            # aggregate health of all services
  racecast profile   list | show [<name>] | use <name> | new <name> [--from <source>] | export <name> [--no-assets] [--out PATH] | import <file> [--force]
  racecast --profile <name> <command>        # run one command against a non-active profile
  racecast chat      clear | pull <ip> [--port N] | import <file> | export [--out PATH]
  racecast report                            # generate the post-event report (last session) -> runtime/<profile>/reports/
  racecast report send [FILE]                # send the newest (or given) report to the league Discord as an attachment
  racecast backup    {create|list|restore|delete} <label>   # named look snapshots (overlay+graphics+media)
  racecast ui [--no-browser]                 # local Control Center web app (port 8089 / RACECAST_UI_PORT)
  racecast freeport [PORT...] [--force]       # free a stuck feed port (default 53001-53003); kills orphaned holders, refuses a running relay/streams
  racecast device-scan [--webcam VAL] [--capture VAL] [--mic VAL]  # enumerate OBS video-capture devices + microphones and save the pick(s) to .env (interactive when no flags given)
  racecast gt7-discover [--save] [--print] [--timeout N] [--pick I]  # find the PS4/PS5 running GT7 and save its IP
  racecast smoketest [--minutes N] [--json] [--force] [--no-report]   # post-update check: real event + director rundown against discovered live streams
  racecast preflight | speedtest [--json] | cookies [twitch] [browser] | graphics | media | brands | setup [--out PATH] | install-tools [--yes] [--update] | install-apps [--yes] [--update]
  racecast obs-browser [--yes]               # Linux/ARM64: build & install OBS's Browser Source plugin from source (needed for the relay HUD)
  racecast export companion [--out PATH]     # write the Companion button config
  racecast init [--browser NAME] [--skip-installs] [--force]   # guided first-time setup
  racecast update [--check] [--yes] [--tag TAG]   # self-update the binary (--tag installs an exact release)
  racecast --version
"""
import glob, hashlib, io, json, os, re, shutil, sys, tempfile, time, webbrowser, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
# Adapters (added in later tasks) import sibling modules from scripts/ at module
# level (e.g. `import services`), so this injection must stay at import time.
sys.path.insert(0, os.path.join(HERE, "scripts"))
import subprocess
import services as sv
import init_setup as ins
import config as pcfg    # 'pcfg' (not 'cfg'): avoids F811 clash with local `cfg = json.loads(...)` dicts elsewhere in this file
import http_util
import profile_admin as pa
import chat_admin as ca
import console_auth as cpa
import console_admin as cpadm
import cue_admin as cue
import health_store as hsmod
import report_build as rbuild
import resources
import notify   # pure Discord payload builders for producer events (#317)
import overlay_build as ob
import fonts_bundle as fb
import ports as pt
import app_version as _app_version

# PyInstaller marks the frozen binary with sys.frozen and unpacks bundled data
# (the whole src/ tree) to sys._MEIPASS. Repo + package mode stay subprocess-based.
IS_FROZEN = bool(getattr(sys, "frozen", False))

def _src_base(frozen, meipass, here):
    """Root of the source tree: bundled data dir when frozen, else this dir."""
    return os.path.join(meipass, "src") if frozen else here

def resource_path(rel):
    """Absolute path of a bundled/checked-out source file, e.g. 'obs/hud.html'."""
    return os.path.join(_src_base(IS_FROZEN, getattr(sys, "_MEIPASS", ""), HERE), rel)

def _app_home(executable):
    """Directory holding a frozen binary's siblings — the other binary, runtime/,
    .env. Normally dirname(executable). But inside a macOS .app bundle the
    executable lives at <home>/<Name>.app/Contents/MacOS/<exe>, so the real home
    (where the sibling `racecast` binary and runtime/.env sit, NEXT TO the .app) is
    three levels up from Contents/MacOS/. A .app bundle is a macOS construct, so
    its layout is always POSIX ('/') — parse with '/' explicitly, never os.sep,
    which would mis-split this path on a Windows test runner."""
    d = os.path.dirname(executable)
    parts = d.split("/")
    if (len(parts) >= 3 and parts[-1] == "MacOS" and parts[-2] == "Contents"
            and parts[-3].endswith(".app")):
        return "/".join(parts[:-3]) or "/"
    return d

def _sec_original_path(path):
    """Map a macOS App-Translocation path back to its original on-disk location
    via Security.framework's SecTranslocateCreateOriginalPathForURL (10.12+).
    Returns the original filesystem path, or None when the API is unavailable or
    the lookup fails (caller falls back to the input). Pure ctypes — no new dep."""
    import ctypes, ctypes.util
    sec_lib = ctypes.util.find_library("Security")
    cf_lib = ctypes.util.find_library("CoreFoundation")
    if not sec_lib or not cf_lib:
        return None
    sec, cf = ctypes.CDLL(sec_lib), ctypes.CDLL(cf_lib)
    if not hasattr(sec, "SecTranslocateCreateOriginalPathForURL"):
        return None
    vp, b, l, cp = ctypes.c_void_p, ctypes.c_bool, ctypes.c_long, ctypes.c_char_p
    cf.CFURLCreateFromFileSystemRepresentation.restype = vp
    cf.CFURLCreateFromFileSystemRepresentation.argtypes = [vp, cp, l, b]
    cf.CFURLGetFileSystemRepresentation.restype = b
    cf.CFURLGetFileSystemRepresentation.argtypes = [vp, b, cp, l]
    cf.CFRelease.argtypes = [vp]
    sec.SecTranslocateCreateOriginalPathForURL.restype = vp
    sec.SecTranslocateCreateOriginalPathForURL.argtypes = [vp, vp]
    raw = path.encode("utf-8")
    url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), False)
    if not url:
        return None
    try:
        original = sec.SecTranslocateCreateOriginalPathForURL(url, None)
        if not original:
            return None
        try:
            buf = ctypes.create_string_buffer(4096)
            if not cf.CFURLGetFileSystemRepresentation(original, True, buf, len(buf)):
                return None
            return buf.value.decode("utf-8")
        finally:
            cf.CFRelease(original)
    finally:
        cf.CFRelease(url)


def _untranslocate(path, frozen=None, platform=None, resolver=None):
    """Guard against macOS App Translocation. A quarantined .app launched from
    Finder runs from a randomized read-only copy under
    .../AppTranslocation/<uuid>/d/, so sys.executable — and every sibling path
    derived from it (.env, runtime/, the sibling racecast binary) — points into that
    throwaway mount instead of the folder where the producer keeps the .app
    (issue #22: Settings showed .env under /private/var/.../AppTranslocation/).
    Map the path back to its real on-disk location. Translocation only affects a
    frozen .app on macOS, so both guards short-circuit elsewhere; best-effort —
    any failure returns `path` unchanged. Pure-by-injection for tests."""
    frozen = IS_FROZEN if frozen is None else frozen
    platform = sys.platform if platform is None else platform
    if not frozen or not platform.startswith("darwin"):
        return path
    resolver = _sec_original_path if resolver is None else resolver
    try:
        return resolver(path) or path
    except Exception:
        return path


_REAL_EXE = None

def _real_executable():
    """sys.executable, mapped out of any macOS App-Translocation mount so sibling
    resolution finds the producer's real folder. Cached (stable per process)."""
    global _REAL_EXE
    if _REAL_EXE is None:
        _REAL_EXE = _untranslocate(sys.executable)
    return _REAL_EXE


def _runtime_base(frozen, executable, here):
    """Machine-local state dir. Frozen: next to the binary (document: keep the
    binary in its own folder). Repo (src/) -> <repo>/runtime ; package -> <pkg>/runtime."""
    if frozen:
        return os.path.join(_app_home(executable), "runtime")
    if os.path.basename(here) == "src":
        return os.path.join(os.path.dirname(here), "runtime")
    return os.path.join(here, "runtime")

def _runtime_base_dir():
    """The un-scoped machine runtime/ dir. The active-profile pointer and the
    shared cookie jar live here directly; per-league state lives under _runtime_dir()."""
    return _runtime_base(IS_FROZEN, _real_executable(), HERE)

def _profile_runtime(base_runtime, profile_name):
    """Profile-scoped runtime dir: <base>/<profile> when a profile is active,
    else the base (fresh machine / no profile yet)."""
    return os.path.join(base_runtime, profile_name) if profile_name else base_runtime

def _active_profile_name():
    """The active profile name (tolerant): RACECAST_PROFILE env / the active
    pointer / the sole profile -- or None if none can be resolved, so commands
    that do not need a profile still work."""
    root = _env_base(IS_FROZEN, _real_executable(), HERE)
    try:
        return pcfg.resolve_active_profile(
            pcfg.list_profiles(root),
            env_value=os.environ.get("RACECAST_PROFILE"),
            pointer=pcfg.read_active_pointer(_runtime_base_dir()))
    except pcfg.ProfileError:
        return None

def _runtime_dir():
    return _profile_runtime(_runtime_base_dir(), _active_profile_name())

def _ui_app_log_dir():
    """Control Center app.log lives machine-wide at runtime/logs (NOT per-profile):
    the UI's actions span profiles (install-tools, preflight, cookies, freeport…),
    and the active profile can change mid-session."""
    return os.path.join(_runtime_base_dir(), "logs")

def _ui_app_log_path():
    return os.path.join(_ui_app_log_dir(), "app.log")

def _profile_env_vars(rc):
    """The league values from a ResolvedConfig to push into the child env, as a
    dict of the non-empty ones. These are exactly what the relay / one-shots /
    probes read (RACECAST_SHEET_ID etc.)."""
    pairs = (("RACECAST_SHEET_ID", rc.sheet_id),
             ("RACECAST_SHEET_PUSH_URL", rc.sheet_push_url),
             ("RACECAST_INTRO_URL", rc.intro_url),
             ("RACECAST_OUTRO_URL", rc.outro_url),
             ("RACECAST_TRAILER_URL", rc.trailer_url),
             ("RACECAST_DISCORD_WEBHOOK_URL", rc.discord_webhook_url),
             ("RACECAST_OBS_COLLECTION", rc.obs_collection),
             ("RACECAST_CONSOLE_SECRET", rc.console_secret),
             ("RACECAST_DISCORD_CLIENT_ID", rc.discord_client_id),
             ("RACECAST_DISCORD_CLIENT_SECRET", rc.discord_client_secret),
             ("RACECAST_DISCORD_VOICE_URL", rc.discord_voice_url),
             ("RACECAST_EVENT_TITLE", rc.event_title),
             ("RACECAST_PROFILE_NAME", rc.name),
             ("RACECAST_LOGO", rc.logo_path),
             ("RACECAST_KIND", rc.kind),   # endurance|solo — relay's --solo default
             ("RACECAST_TEMPLATE", rc.template))  # solo starter template (commentary|pov)
    return {k: v for k, v in pairs if v}

def _apply_active_profile_env():
    """Resolve the active profile and inject its league values into os.environ so
    every downstream consumer (relay daemon, one-shots, event probes) inherits
    them. Tolerant: no profile -> no-op. Returns the profile name or None."""
    name = _active_profile_name()
    if not name:
        return None
    root = _env_base(IS_FROZEN, _real_executable(), HERE)
    try:
        rc = pcfg.resolve_config(root, override=name,
                                 runtime_root=_runtime_base_dir())
    except pcfg.ProfileError:
        return None
    os.environ.update(_profile_env_vars(rc))
    return name

def _env_base(frozen, executable, here):
    """Directory whose .env configures this run (mirrors _runtime_base):
    frozen -> next to the binary; repo (src/) -> repo root; package -> here."""
    if frozen:
        return _app_home(executable)
    if os.path.basename(here) == "src":
        return os.path.dirname(here)
    return here

def _env_file():
    return os.path.join(_env_base(IS_FROZEN, _real_executable(), HERE), ".env")

def parse_env_text(text):
    """Minimal .env parser (KEY=VALUE, '#' comments, optional quotes) — matches the
    semantics of the bounded load_dotenv() copies in the src/ scripts."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        if key:
            out[key] = val.strip().strip("'\"")
    return out

def ensure_env_file(exe_dir, frozen=None):
    """First run of the frozen binary: the release archives ship .env.example
    next to the binary but never a real .env (an upgrade extract must not
    clobber filled-in secrets). Copy the template once so the operator only
    fills in values. Returns True iff .env was created."""
    frozen = IS_FROZEN if frozen is None else frozen
    if not frozen:
        return False
    env_path = os.path.join(exe_dir, ".env")
    example = os.path.join(exe_dir, ".env.example")
    if os.path.exists(env_path) or not os.path.exists(example):
        return False
    try:
        shutil.copyfile(example, env_path)
    except OSError as exc:
        print(f"warning: could not create .env next to the binary ({exc}) — "
              "copy .env.example to .env manually.", file=sys.stderr)
        return False
    print("created .env next to the binary — fill in the required values "
          "(see the comments inside).", file=sys.stderr)
    return True


def _bundled_example_profile():
    """Path of the profiles/example/ league template bundled inside the frozen
    binary (build-binary.py --add-data). None when not frozen / not bundled."""
    if not IS_FROZEN:
        return None
    return os.path.join(getattr(sys, "_MEIPASS", ""), "profiles", "example")


def ensure_example_profile(exe_dir, frozen=None, bundled=None):
    """First run of the frozen binary: seed profiles/example/ next to the binary
    from the template bundled inside it, so `racecast profile new <name>` (which
    copies profiles/example/) works out of the box. The release archives ship only
    the binaries + .env.example, and `racecast update` swaps just the binary, so the
    template has to travel inside the binary and be unpacked here once. Never
    clobbers an existing profiles/example/. Returns True iff it was created."""
    frozen = IS_FROZEN if frozen is None else frozen
    if not frozen:
        return False
    src = _bundled_example_profile() if bundled is None else bundled
    target = os.path.join(exe_dir, "profiles", "example")
    if (os.path.exists(target) or not src
            or not os.path.isfile(os.path.join(src, "profile.env"))):
        return False
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copytree(src, target)
    except OSError as exc:
        print(f"warning: could not seed profiles/example next to the binary "
              f"({exc}) — `racecast profile new` will not find a template.",
              file=sys.stderr)
        return False
    print("seeded profiles/example next to the binary (league template for "
          "`racecast profile new`).", file=sys.stderr)
    return True


def _bundled_fonts_zip():
    """Path of the fonts.zip carrying the curated overlay-font set: bundled inside
    the frozen binary (_MEIPASS/fonts.zip) or at the repo root in dev. None when
    absent (e.g. a dev who never ran tools/fetch-fonts.py)."""
    if IS_FROZEN:
        p = os.path.join(getattr(sys, "_MEIPASS", ""), "fonts.zip")
    else:
        p = os.path.join(os.path.dirname(HERE), "fonts.zip")
    return p if os.path.isfile(p) else None


def ensure_bundled_fonts():
    """Seed the machine-wide overlay font library (runtime/fonts/) from the bundled
    fonts.zip on start, so every install has the curated baseline set without a
    manual download. Stamp-gated + only-if-absent + zip-slip-safe (see
    fonts_bundle.extract_bundled); fully best-effort. Returns True iff anything was
    extracted."""
    zip_path = _bundled_fonts_zip()
    if not zip_path:
        return False
    try:
        res = fb.extract_bundled(zip_path, _machine_fonts_dir())
    except Exception as exc:
        print(f"warning: could not seed bundled fonts ({exc}).", file=sys.stderr)
        return False
    if res.get("extracted"):
        print(f"seeded {len(res['extracted'])} overlay font(s) into runtime/fonts/.",
              file=sys.stderr)
        return True
    return False


def cleanup_old_binary(exe_dir, frozen=None, platform=None):
    """Best-effort removal of the *-old.exe leftovers that `racecast update` leaves
    behind on Windows (a running exe can only be renamed, not deleted, during the
    swap): racecast-old.exe for the CLI itself, plus racecast-ui-old.exe when the
    running Control Center was renamed aside so the new launcher could land.
    Returns True iff at least one leftover existed and was removed."""
    frozen = IS_FROZEN if frozen is None else frozen
    platform = sys.platform if platform is None else platform
    if not frozen or not platform.startswith("win"):
        return False
    removed = False
    for leftover in ("racecast-old.exe", "racecast-ui-old.exe"):
        old = os.path.join(exe_dir, leftover)
        try:
            if os.path.exists(old):
                os.remove(old)
                removed = True
        except OSError:
            pass  # still locked by a lingering process — retried on the next run
    return removed


def _force_utf8_io(streams=None):
    """Make console output UTF-8 so the non-ASCII glyphs in our messages
    (-> arrows U+2192, em dashes, ellipses) and German subprocess text never
    crash or mojibake. Not Windows-only: whenever stdout is redirected (the
    Control Center captures a job's output through a pipe) Python uses the
    locale/ANSI encoding instead of UTF-8 — cp1252 on Windows, but equally
    ASCII under a POSIX/`LANG=C` locale on Linux — and printing '\\u2192' then
    dies with UnicodeEncodeError (issue #24); the captured bytes also reach the
    UTF-8 web UI garbled. Reconfiguring to UTF-8 fixes both; errors='replace' is
    a backstop so an un-encodable char degrades to '?' instead of raising.
    Best-effort: a stream that is None (a --windowed build has no stdout),
    predates reconfigure() (py<3.7), or rejects it is silently skipped."""
    for stream in (streams if streams is not None else (sys.stdout, sys.stderr)):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass    # stream missing/old/non-reconfigurable — leave it as-is


def _load_env_frozen():
    """Frozen binary: load <exe-dir>/.env into os.environ (existing env wins).
    The scripts' own load_dotenv() can't find it — their marker walk starts in
    the throwaway _MEIPASS dir — but they all let real env vars take precedence."""
    if not IS_FROZEN:
        return
    # _app_home (not dirname): a macOS .app nests the exe under Contents/MacOS/,
    # so .env lives next to the bundle, not inside it; _real_executable also maps
    # out of any App-Translocation mount (issue #22).
    path = os.path.join(_app_home(_real_executable()), ".env")
    try:
        with open(path, encoding="utf-8") as fh:
            pairs = parse_env_text(fh.read())
    except OSError:
        return
    for key, val in pairs.items():
        os.environ.setdefault(key, val)

# Known system CA bundle locations (macOS ships /etc/ssl/cert.pem; the Linux
# paths cover Debian/Ubuntu and RHEL/Fedora).
CA_BUNDLES = ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt",
              "/etc/pki/tls/certs/ca-bundle.crt")


def pick_ca_bundle(cafile, capath, candidates, exists=os.path.exists):
    """The CA bundle SSL_CERT_FILE should point at, or None when the build's
    own OpenSSL default paths work (or no candidate exists). Pure for tests."""
    if (cafile and exists(cafile)) or (capath and exists(capath)):
        return None
    for path in candidates:
        if exists(path):
            return path
    return None


def _ensure_ssl_certs():
    """Frozen on macOS/Linux: the bundled OpenSSL looks for CA certs at the
    BUILD machine's compile-time paths, which usually don't exist on the
    producer's machine -> every in-process HTTPS call dies with
    CERTIFICATE_VERIFY_FAILED. Point SSL_CERT_FILE at the system bundle
    instead (Windows uses the OS cert store natively). Must run before any
    ssl context is created; children inherit the variable."""
    if not IS_FROZEN or sys.platform.startswith("win") or os.environ.get("SSL_CERT_FILE"):
        return
    import ssl
    paths = ssl.get_default_verify_paths()
    bundle = pick_ca_bundle(paths.openssl_cafile, paths.openssl_capath, CA_BUNDLES)
    if bundle:
        os.environ["SSL_CERT_FILE"] = bundle

# Where `racecast install-tools` (brew) drops yt-dlp/streamlink/ffmpeg/deno on macOS:
# Apple-silicon Homebrew, then Intel Homebrew. A Finder/Dock launch omits these
# from PATH (issue #38).
TOOL_PATH_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


def augment_path(current, candidates, exists=os.path.isdir):
    """The PATH `current` should become so the tool dirs in `candidates` are
    reachable: each candidate that exists on disk but is missing from PATH,
    prepended (candidate order preserved) ahead of the existing entries. Returns
    None when nothing needs adding (every existing candidate is already on PATH,
    or none exist) so the caller can leave os.environ untouched. Pure for tests."""
    have = current.split(os.pathsep) if current else []
    add = [d for d in candidates if exists(d) and d not in have]
    if not add:
        return None
    return os.pathsep.join(add + have)


def _ensure_tool_path():
    """Prepend the tool dirs `racecast install-tools` writes to but that aren't on
    this process's PATH, so preflight AND the spawned relay resolve them.

    Two sources:
    * The racecast-managed bin dir (runtime/bin) — where install-tools drops the
      direct-download tools (deno on Linux, the Ookla speedtest CLI on mac/Linux);
      it is NEVER on the user's shell PATH, so add it on every platform.
    * Frozen on macOS only: a binary launched from Finder/Dock inherits a truncated
      PATH (/usr/bin:/bin:/usr/sbin:/sbin) that omits Homebrew, so the brew-installed
      yt-dlp/streamlink/ffmpeg/deno look missing (issue #38) — prepend the Homebrew
      bin dirs too.

    Both only ever add genuinely-missing dirs that exist on disk (augment_path),
    so a terminal launch with a full PATH is left untouched. Binary-relative paths
    (settings/assets) never went through PATH, which is why those already work."""
    candidates = [os.path.join(_runtime_base_dir(), "bin")]  # == speedtest.managed_bin_dir
    if IS_FROZEN and sys.platform == "darwin":
        candidates += list(TOOL_PATH_DIRS)
    new = augment_path(os.environ.get("PATH", ""), candidates)
    if new:
        os.environ["PATH"] = new


def _script_invocation(rel, args, frozen, base=None):
    """How to run a src/ script: subprocess in repo/package mode; in-process when
    frozen (the .py files ship as bundled data, there is no python3 to exec)."""
    if frozen:
        base = base if base is not None else _src_base(True, getattr(sys, "_MEIPASS", ""), HERE)
        return ("inprocess", os.path.join(base, *rel.split("/")), list(args))
    return ("subprocess", [sys.executable, os.path.join(HERE, *rel.split("/"))] + list(args), None)

def _run_module(path, args):
    """Load a bundled script by file path and run its main() with patched argv.
    Returns an exit code (SystemExit from argparse/sys.exit is translated; an int
    return value from main() is honored — e.g. preflight returns 1 on failure)."""
    import importlib.util
    name = os.path.splitext(os.path.basename(path))[0].replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    old_argv = sys.argv
    sys.argv = [os.path.basename(path)] + list(args)
    try:
        spec.loader.exec_module(mod)
        fn = getattr(mod, "main", None)
        if fn is None:
            print(f"racecast: {os.path.basename(path)} has no main()", file=sys.stderr)
            return 1
        result = fn()
        return result if isinstance(result, int) else 0
    except SystemExit as e:
        if e.code is None:
            return 0
        if isinstance(e.code, int):
            return e.code
        print(e.code, file=sys.stderr)  # mirror Python: sys.exit(str) -> stderr + 1
        return 1
    finally:
        sys.argv = old_argv

def _run_script(rel, args):
    kind, target, extra = _script_invocation(rel, args, IS_FROZEN)
    if kind == "subprocess":
        return subprocess.call(target)
    return _run_module(target, extra)

def _relay_daemon_argv(rest, frozen):
    """Detached relay child: frozen -> the binary re-invokes itself in foreground
    mode (relay_run adds the runtime args there); otherwise python3 runs the
    script directly with this profile's runtime + the shared cookie jar."""
    if frozen:
        return [sys.executable, "relay", "run"] + list(rest)
    return [sys.executable, _relay_script()] + _relay_runtime_args() + list(rest)

def _setup_import_name(kind=None):
    """Filename of the localized OBS import for `kind` (default: RACECAST_KIND env).
    Solo profiles get GT_Racing_Solo.import.json; endurance/unknown get
    GT_Racing_Endurance.import.json."""
    if kind is None:
        kind = pcfg.normalize_kind(os.environ.get("RACECAST_KIND", ""))
    return "GT_Racing_Solo.import.json" if kind == "solo" else "GT_Racing_Endurance.import.json"

def _oneshot_extra(command, rest, runtime_dir, base_dir, overlay_css=None, kind=None,
                   profile_graphics=None):
    """Extra argv for a one-shot. The asset writers (graphics/media/setup) get a
    profile-scoped --out (+ setup's --media/--graphics) so their output lands
    under runtime/<profile>/ in every run mode -- those are baked into the OBS
    collection as absolute paths. The machine-level one-shots that take
    --runtime-dir (preflight, cookies) get the un-scoped BASE runtime, so the
    shared cookie jar stays at runtime/yt-cookies.txt. The user's own --out wins.
    overlay_css: when given and the file exists, injected as --overlay-css for
    setup (so the OBS collection bakes the active POV-box geometry). kind: the
    active profile kind (endurance|solo) picks setup's default --out basename --
    defaults to RACECAST_KIND from the environment (the CLI applies the active
    profile's env, incl. RACECAST_KIND, before dispatching a one-shot -- the same
    source setup-assets itself reads), so callers rarely need to pass it explicitly."""
    if kind is None:
        kind = pcfg.normalize_kind(os.environ.get("RACECAST_KIND", ""))
    extra = []
    if command in RUNTIME_DIR_ONESHOTS:
        extra += ["--runtime-dir", base_dir]
    if "--out" not in rest:
        setup_name = _setup_import_name(kind)
        out = {"graphics": os.path.join(runtime_dir, "graphics"),
               "media": os.path.join(runtime_dir, "media"),
               "brands": os.path.join(runtime_dir, "brands"),
               "setup": os.path.join(runtime_dir, setup_name)}.get(command)
        if out:
            extra += ["--out", out]
    # `racecast media` downloads Intro/Outro from YouTube -> it needs the shared
    # cookie jar (bot-check parity with the relay). get-media's own here-relative
    # fallback points into the PyInstaller bundle on a frozen binary and 403s, so
    # hand it the REAL top-level jar (same one the relay gets via _cookies_path).
    # Injected only when a jar exists and the user didn't pass their own.
    if command == "media" and "--cookies" not in rest:
        for name in ("yt-cookies.txt", "cookies.txt"):
            ck = os.path.join(base_dir, name)
            if os.path.isfile(ck):
                extra += ["--cookies", ck]
                break
    if command == "setup":
        for flag, sub in (("--media", "media"), ("--graphics", "graphics")):
            if flag not in rest:
                extra += [flag, os.path.join(runtime_dir, sub)]
    if (command == "setup" and overlay_css and "--overlay-css" not in rest
            and os.path.isfile(overlay_css)):
        extra += ["--overlay-css", overlay_css]
    if (command == "setup" and profile_graphics and "--profile-graphics" not in rest
            and os.path.isdir(profile_graphics)):
        extra += ["--profile-graphics", profile_graphics]
    return extra

def _relay_script():
    return os.path.join(HERE, "relay", "racecast-feeds.py")

def _relay_pid_path():
    # Singleton: the relay binds the SHARED control port (8088) + feed ports, so
    # only ONE can run per machine. Its PID therefore lives at the un-scoped
    # runtime/ TOP LEVEL (like the active-profile pointer) — NOT under
    # runtime/<profile>/ — so stop/status/restart find the one running relay
    # regardless of which profile is active. A per-profile PID let a `profile use`
    # while the relay ran orphan it on port 8088 (#273).
    return os.path.join(_runtime_base_dir(), "relay.pid")

def _relay_profile_path():
    """Records which profile the running relay was started under (top-level, next
    to relay.pid). Lets `relay logs`/`status` resolve the relay's OWN per-profile
    logs dir even after `profile use` changed the active profile (#273)."""
    return os.path.join(_runtime_base_dir(), "relay.profile")

def _running_relay_profile():
    """The profile slug the running relay was started under, or "" when no stamp
    exists (no relay, or a relay from before the stamp was written)."""
    try:
        with open(_relay_profile_path(), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""

def _running_relay_dir():
    """The runtime dir of the profile the relay is RUNNING under (from the
    relay.profile stamp), or the active profile's dir when no stamp exists. The
    relay daemon writes its logs under this dir, so logs/status must read from
    the same place even after the active profile switched (#273)."""
    slug = _running_relay_profile()
    return _profile_runtime(_runtime_base_dir(), slug) if slug else _runtime_dir()

def _write_relay_profile_stamp():
    """Stamp the active profile as the relay's running profile, just before spawn."""
    path = _relay_profile_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_active_profile_name() or "")

def _clear_relay_profile_stamp():
    try:
        os.remove(_relay_profile_path())
    except OSError:
        pass  # no stamp to clear — already gone

def _event_title_path():
    """The active profile's persisted event-title file (#207). The relay's
    EventTitleStore loads this on startup, so writing it before a takeover bring-up
    makes the new relay adopt producer A's on-air title."""
    return os.path.join(_runtime_dir(), "event.json")

def _part_index(rest):
    """Extract + validate a --part flag ("--part 2" / "--part=2"), mirroring
    _stint_args. Default 1. Exits on an invalid value (fail fast before the
    detached relay is spawned)."""
    for i, tok in enumerate(rest):
        val = None
        if tok == "--part" and i + 1 < len(rest):
            val = rest[i + 1]
        elif tok.startswith("--part="):
            val = tok.split("=", 1)[1]
        if val is not None:
            if not val.isdigit() or int(val) < 1:
                sys.exit("--part must be a 1-based Part number (got {!r}).".format(val))
            return int(val)
    return 1


def _part_path():
    """The active profile's persisted broadcast-Part pointer. The relay's
    PartStore loads this on construction, so writing it before bring-up sets the
    Part the relay comes up on."""
    return os.path.join(_runtime_dir(), "part.json")


def _write_part_reset(index):
    """Reset the broadcast-Part pointer to Part `index`, not-live. `event start`
    is the one reliable reset point (every event begins with it; a clean
    last-Part-stop / event stop can't be detected). A fresh relay adopts this on
    construction; writing while a relay already runs is inert (PartStore loads
    once at start) — so a mid-event re-run does not disturb the live pointer, and
    recovery is `event start --part N` + a relay restart. Best-effort."""
    path = _part_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"index": int(index), "live": False}, fh)
    except OSError as exc:
        print("note: could not reset part.json ({}) — continuing.".format(exc))


def _relay_log_path():
    return os.path.join(_running_relay_dir(), "logs", "relay.console.log")

def _relay_boot_log_path():
    """Where start_detached captures the daemon's raw stdout/stderr (crashes/tracebacks
    BEFORE logging is configured). MUST differ from _relay_log_path(): the relay's own
    TimedRotatingFileHandler owns relay.console.log, and a second writer on the same
    file would corrupt rotation (the inherited fd would keep writing to the renamed
    inode at midnight). Mirrors the static-streams feed_<port>.boot.log split."""
    return os.path.join(_running_relay_dir(), "logs", "relay.boot.log")

def _tailscale_snapshot_path():
    # Appended on relay start (and on `tailscale status`); grouped with the
    # running relay's logs so the aggregate log view stays coherent after a
    # profile switch. Falls back to the active profile when no relay runs (#273).
    return os.path.join(_running_relay_dir(), "logs", "tailscale.snapshot.log")

def _append_tailscale_snapshot():
    """Best-effort: append a timestamped `tailscale status` block to the snapshot log."""
    try:
        import tailscale as _ts
        # tailscale_backend() probes `status --json`; if that hangs it times out and
        # returns binary=None, so the second `status` call below never runs (no
        # compounding block). When the binary IS found the daemon is responsive, so
        # the second call returns promptly.
        binary, _state, _ip = _ts.tailscale_backend()
        if binary is None:
            text = "tailscale binary not found"   # module present, CLI binary absent
        else:
            out = subprocess.run([binary, "status"], capture_output=True, text=True,
                                 errors="replace", timeout=5,
                                 env=sv.external_tool_env(),
                                 **sv.no_window_kwargs())
            text = (out.stdout or out.stderr or "").strip() or "no output"
        ts_str = time.strftime("%Y-%m-%d %H:%M:%S")
        path = _tailscale_snapshot_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # append-only; small per entry — no rotation needed (prune_old_logs is
        # mtime-based and won't touch it while the relay is in regular use).
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(_ts.status_snapshot_text(text, ts_str))
    except Exception:  # noqa: BLE001
        pass


def _relay_feed_logs():
    """The relay's per-feed logs (feed_A/B/POV.log) under the RUNNING relay's
    profile logs dir (#273 — follows the relay, not the active profile)."""
    d = os.path.join(_running_relay_dir(), "logs")
    return sorted(glob.glob(os.path.join(d, "feed_*.log")))

def _relay_files():
    files = [_relay_log_path()] + _relay_feed_logs()
    return [f for f in files if os.path.exists(f)]

def _app_files():
    p = _ui_app_log_path()
    return [p] if os.path.exists(p) else []

def _streams_files():
    d = os.path.join(_streams_static_dir(), "logs")
    return sorted(glob.glob(os.path.join(d, "feed_*.log")))

def _obs_files():
    import logsetup
    d = logsetup.obs_log_dir(sys.platform)
    newest = logsetup.newest_log(d)
    return [newest] if newest else []

def _companion_files():
    p = _companion_log_path()
    return [p] if p else []

def _tailscale_files():
    p = _tailscale_snapshot_path()
    return [p] if os.path.exists(p) else []

def _read_dated(dirpath, files, date):
    """Concatenate the rotated archives for `date` across a racecast source's files,
    each line source-prefixed. Empty string if none / bad date (guarded by
    resolve_archive)."""
    import logsetup
    chunks = []
    for f in files:
        arch = logsetup.resolve_archive(dirpath, os.path.basename(f), date)
        if arch:
            with open(arch, encoding="utf-8", errors="replace") as fh:
                label = os.path.basename(f).split(".log")[0]
                chunks += [f"[{label}] {ln.rstrip(chr(10))}" for ln in fh]
    return "\n".join(chunks)

def _read_named(dirpath, token):
    """Read one external log file by basename, guarded to dirpath. None if invalid."""
    if not token or "/" in token or "\\" in token or os.sep in token or ".." in token:
        return None
    root = os.path.realpath(dirpath)
    full = os.path.realpath(os.path.join(dirpath, token))
    try:
        if os.path.commonpath([root, full]) != root or not os.path.isfile(full):
            return None
    except ValueError:
        return None
    with open(full, encoding="utf-8", errors="replace") as fh:
        return fh.read()

def _log_sources():
    """Registry: source name -> {files, dir, archives, read}. Archives are opaque
    TOKENS: racecast sources use rotation dates (YYYY-MM-DD); external apps
    (obs/companion) use the older filenames in their dir (they do not follow our
    rotation naming). `read(token)` resolves a token to text per source. The UI and
    CLI both consume this registry."""
    relay_dir = os.path.join(_running_relay_dir(), "logs")   # follows the relay (#273)
    streams_dir = os.path.join(_streams_static_dir(), "logs")
    app_dir = _ui_app_log_dir()                              # machine-wide Control Center log
    import logsetup
    def rc_src(files_fn, dirpath):
        return {"files": files_fn, "dir": dirpath,
                "archives": (lambda: logsetup.archive_dates(
                    dirpath, [os.path.basename(f) for f in files_fn()])),
                "read": (lambda tok: _read_dated(dirpath, files_fn(), tok))}
    def ext_src(files_fn, dirpath):
        def archives():
            cur = set(os.path.basename(f) for f in files_fn())   # exclude the live/newest
            return [os.path.basename(f) for f in logsetup.list_logs(dirpath)
                    if os.path.basename(f) not in cur]
        return {"files": files_fn, "dir": dirpath, "archives": archives,
                "read": (lambda tok: _read_named(dirpath, tok))}
    reg = {
        "relay": rc_src(_relay_files, relay_dir),
        "streams": rc_src(_streams_files, streams_dir),
        "tailscale": rc_src(_tailscale_files, relay_dir),
        "obs": ext_src(_obs_files, logsetup.obs_log_dir(sys.platform)),
        "companion": ext_src(_companion_files,
                             os.path.dirname(_companion_log_path() or "") or "."),
        "app": rc_src(_app_files, app_dir),
    }
    def _agg_files():
        out = []
        for n in ("relay", "streams", "obs", "companion", "tailscale"):
            out += reg[n]["files"]()
        return out
    reg["aggregate"] = {"files": _agg_files, "dir": relay_dir,
                        "archives": (lambda: []),       # aggregate is live-only
                        "read": (lambda _tok: "")}
    return reg

def _cookies_path():
    """The YouTube cookie jar -- SHARED across leagues, at the un-scoped runtime/
    root. Canonical name is yt-cookies.txt; a legacy cookies.txt is migrated once."""
    base = _runtime_base_dir()
    new = os.path.join(base, "yt-cookies.txt")
    legacy = os.path.join(base, "cookies.txt")
    if not os.path.isfile(new) and os.path.isfile(legacy):
        try:
            os.replace(legacy, new)
        except OSError:
            return legacy   # migration failed -> keep using legacy this run
    return new

def _active_overlay_dir():
    """profiles/<active>/overlay for the active profile, or None when no profile
    resolves. (Does not check existence — callers decide.)"""
    active = _active_profile_name()
    if not active:
        return None
    root = _env_base(IS_FROZEN, _real_executable(), HERE)
    return os.path.join(pcfg.profiles_dir(root), active, "overlay")

def _active_profile_graphics_dir():
    """profiles/<active>/graphics for the active profile, or None when no profile
    resolves. Committed per-profile graphics (e.g. a demo Overlay.png) that
    setup-assets seeds into the runtime graphics dir as a fallback. (Does not
    check existence — callers decide.)"""
    active = _active_profile_name()
    if not active:
        return None
    root = _env_base(IS_FROZEN, _real_executable(), HERE)
    return os.path.join(pcfg.profiles_dir(root), active, "graphics")

def _overlay_relay_args(overlay_dir):
    """['--overlay-dir', DIR] when DIR exists, else [] (pure for tests)."""
    if overlay_dir and os.path.isdir(overlay_dir):
        return ["--overlay-dir", overlay_dir]
    return []

def _relay_runtime_args():
    """Runtime args every relay invocation gets: its profile-scoped runtime dir
    plus the shared cookie jar (see _cookies_path), and --overlay-dir when the
    active profile ships an overlay/ dir. Placed before the caller's rest so an
    explicit flag in rest still wins."""
    return (["--runtime-dir", _runtime_dir(), "--cookies", _cookies_path()]
            + _overlay_relay_args(_active_overlay_dir()))

RELAY_PORT = 8088
STANDBY_SCENE = "Standby"   # canonical scene name in src/obs/GT_Racing_Endurance.json

# The relay-served pages OBS renders as browser sources (panel is tablet-only).
# The override.css is hashed too, so a per-profile CSS edit advances the
# staleness gate and triggers an OBS browser-source refresh.
OBS_PAGE_PATHS = ("/hud", "/hud/override.css",
                  "/splitscreen", "/splitscreen/override.css",
                  "/intermission", "/intermission/override.css")


def _fetch_relay_page(path):
    return http_util.get_bytes(f"http://127.0.0.1:{RELAY_PORT}{path}", timeout=3)


def served_pages_hash(fetch=None, paths=OBS_PAGE_PATHS):
    """SHA-256 over the page bytes the relay actually serves to OBS. Hashing
    what OBS would load (not the files on disk) means a still-running OLD
    relay can never advance the staleness gate past pages OBS has not seen.
    None when any page cannot be fetched (relay down, --no-hud)."""
    fetch = fetch or _fetch_relay_page
    h = hashlib.sha256()
    for path in paths:
        try:
            h.update(fetch(path))
        except Exception:
            return None
    return h.hexdigest()


def refresh_decision(served, stored, force=False):
    """Should the OBS page-refresh hook act? Pure for tests: 'skip-no-pages'
    (relay down / pages disabled), 'skip-unchanged' (no on-air flicker), or
    'refresh'."""
    if served is None:
        return "skip-no-pages"
    if not force and served == stored:
        return "skip-unchanged"
    return "refresh"


def read_pages_hash(path):
    """Hash of the pages OBS last confirmed loading, or None (never refreshed)."""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def write_pages_hash(path, value):
    parent = os.path.dirname(path)
    if parent:                       # a bare filename needs no directory
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(value + "\n")


def wait_for(probe, wait, clock=time.monotonic, sleep=time.sleep):
    """Poll probe() until truthy or `wait` seconds elapsed; checks at least
    once, so wait=0 means 'probe now, no retries'."""
    deadline = clock() + wait
    while True:
        if probe():
            return True
        if clock() >= deadline:
            return False
        sleep(0.5)


SERVICES = ("relay", "companion", "streams")
SERVICE_VERBS = ("start", "stop", "restart", "status", "logs")
# Per-service verbs beyond the common set (relay foreground + browser-open shortcuts).
EXTRA_VERBS = {
    "relay": ("run", "open-panel", "open-hud", "open-status"),
    "companion": ("open-buttons", "open-admin", "enable-control"),
}
# Internal verbs: routed but never advertised (frozen feed children use run-feed).
HIDDEN_VERBS = {"streams": ("run-feed",)}
ONESHOTS = ("preflight", "speedtest", "cookies", "graphics", "media", "brands", "setup", "install-tools", "install-apps", "obs-browser", "update")
EVENT_VERBS = ("status", "start", "stop", "takeover")
TAILSCALE_VERBS = ("up", "down", "status", "logs")
OBS_VERBS = ("refresh", "collection", "logs", "stream-target", "benchmark")
SHEET_VERBS = ("url", "open")           # active league's Google Sheet (from SHEET_ID)
APP_VERBS = ("launch", "quit")          # GUI app control for the Control Center
APP_CONTROLLED = ("obs", "discord", "tailscale")   # GUI apps racecast can launch + quit

USAGE = __doc__


def route(argv):
    """Resolve argv into an action dict WITHOUT executing. Raises ValueError on bad
    usage. This is the unit-test seam; main() executes the result."""
    if not argv or argv[0] in ("-h", "--help", "help"):
        return {"kind": "help"}
    if argv[0] in ("--version", "-V"):
        return {"kind": "version"}
    cmd, rest = argv[0], argv[1:]
    if cmd == "status" and not rest:
        return {"kind": "aggregate"}
    if cmd in SERVICES:
        verb = rest[0] if rest else None
        valid = SERVICE_VERBS + EXTRA_VERBS.get(cmd, ())
        if verb not in valid + HIDDEN_VERBS.get(cmd, ()):
            raise ValueError(f"usage: racecast {cmd} {{{'|'.join(valid)}}}")
        return {"kind": "service", "command": cmd, "verb": verb, "rest": rest[1:]}
    if cmd == "event":
        verb = rest[0] if rest else None
        if verb not in EVENT_VERBS:
            raise ValueError(f"usage: racecast event {{{'|'.join(EVENT_VERBS)}}}")
        return {"kind": "service", "command": "event", "verb": verb, "rest": rest[1:]}
    if cmd == "tailscale":
        verb = rest[0] if rest else None
        if verb not in TAILSCALE_VERBS:
            raise ValueError(f"usage: racecast tailscale {{{'|'.join(TAILSCALE_VERBS)}}}")
        return {"kind": "service", "command": "tailscale", "verb": verb, "rest": rest[1:]}
    if cmd == "obs":
        verb = rest[0] if rest else None
        if verb not in OBS_VERBS:
            raise ValueError(f"usage: racecast obs {{{'|'.join(OBS_VERBS)}}}")
        return {"kind": "service", "command": "obs", "verb": verb, "rest": rest[1:]}
    if cmd == "sheet":
        verb = rest[0] if rest else None
        if verb not in SHEET_VERBS:
            raise ValueError(f"usage: racecast sheet {{{'|'.join(SHEET_VERBS)}}}")
        return {"kind": "service", "command": "sheet", "verb": verb, "rest": rest[1:]}
    if cmd == "app":
        verb = rest[0] if rest else None
        if verb not in APP_VERBS:
            raise ValueError(f"usage: racecast app {{{'|'.join(APP_VERBS)}}} {{obs|discord}}")
        return {"kind": "service", "command": "app", "verb": verb, "rest": rest[1:]}
    if cmd == "ui":
        return {"kind": "ui", "rest": rest}
    if cmd == "freeport":
        return {"kind": "freeport", "rest": rest}
    if cmd == "device-scan":
        return {"kind": "device-scan", "rest": rest}
    if cmd == "gt7-discover":
        return {"kind": "gt7-discover", "rest": rest}
    if cmd == "init":
        return {"kind": "init", "rest": rest}
    if cmd == "profile":
        return {"kind": "profile", "rest": rest}
    if cmd == "chat":
        return {"kind": "chat", "rest": rest}
    if cmd == "discord":
        return {"kind": "discord", "rest": rest}
    if cmd == "health":
        return {"kind": "health", "rest": rest}
    if cmd == "report":
        return {"kind": "report", "rest": rest}
    if cmd == "console":
        if not rest or rest[0] not in CONSOLE_VERBS:
            raise ValueError(f"usage: racecast console {{{'|'.join(CONSOLE_VERBS)}}}")
        return {"kind": "console", "rest": rest}
    if cmd == "funnel":
        return {"kind": "funnel", "rest": rest}
    if cmd == "links":
        return {"kind": "links", "rest": rest}
    if cmd == "smoketest":
        return {"kind": "smoketest", "rest": rest}
    if cmd == "backup":
        return {"kind": "backup", "rest": rest}
    if cmd in ONESHOTS:
        return {"kind": "oneshot", "command": cmd, "rest": rest}
    if cmd == "export":
        if rest[:1] != ["companion"]:
            raise ValueError("usage: racecast export companion [--out PATH]")
        return {"kind": "export", "target": "companion", "rest": rest[1:]}
    raise ValueError(f"unknown command: {cmd}")


def profile_switch_block_reason(relay_alive, streams_alive, force):
    """Which RUNNING services would be left serving the OLD league after a
    `profile use` switch (they bind the shared control/feed ports). Returns the
    list of running service names, or [] when nothing blocks (or --force). The
    relay survives the switch — its PID is the singleton (#273) — but it keeps
    serving the previous profile, which is rarely what the operator intends, so
    refuse unless --force."""
    if force:
        return []
    running = []
    if relay_alive:
        running.append("relay")
    if streams_alive:
        running.append("static streams")
    return running


def profile_cmd(rest):
    """`racecast profile list|show|use|new|export|import` -- manage league profiles. Resolves the
    project root + runtime dir the same way the rest of the CLI does, so it
    sees profiles/ and runtime/active-profile consistently with config.py."""
    try:
        opts = pa.parse_profile_args(rest)
    except ValueError as e:
        sys.exit(f"racecast: {e}")
    root = _env_base(IS_FROZEN, _real_executable(), HERE)
    runtime_root = _runtime_base_dir()   # the active-profile pointer is un-scoped
    active = pcfg.read_active_pointer(runtime_root)
    verb = opts["verb"]
    if verb == "list":
        print(pa.format_profile_list(pcfg.list_profiles(root), active))
        return None
    if verb == "new":
        try:
            target = pa.create_profile(root, opts["name"], opts["source"],
                                       kind=opts["kind"], template=opts["template"])
        except ValueError as e:
            sys.exit(f"racecast: {e}")
        slug = os.path.basename(target)        # typed name -> directory slug
        env_path = os.path.join(target, pcfg.PROFILE_ENV_NAME)
        if opts["kind"] == "solo":
            print(f"created solo profile '{slug}' "
                  f"(template: {opts['template']}) at {target}")
            print(f"  edit {env_path}, then: racecast profile use {slug}")
        else:
            print(f"created profile '{slug}' at {target}")
            print(f"  edit {env_path} (fill in SHEET_ID), then: "
                  f"racecast profile use {slug}")
        return None
    if verb == "export":
        res = profile_export_data(opts["name"],
                                  include_assets=not opts["no_assets"],
                                  dest=opts["out"] or os.getcwd())
        if not res.get("ok"):
            sys.exit(f"racecast: {res['error']}")
        print(f"exported profile '{opts['name']}' -> {res['path']}")
        return None
    if verb == "import":
        res = profile_import_data(opts["file"], force=opts["force"])
        if not res.get("ok"):
            sys.exit(f"racecast: {res['error']}")
        print(f"imported profile '{res['name']}' ({res['display']})")
        print(f"  switch to it: racecast profile use {res['name']}")
        return None
    if verb == "use":
        blocking = profile_switch_block_reason(
            _relay_is_alive(), _streams_is_alive(), opts["force"])
        if blocking:
            svc = " and ".join(blocking)
            sys.exit(
                f"racecast: a running {svc} would be left serving the current "
                f"profile after the switch.\n"
                f"  stop it first (racecast relay stop / racecast streams stop), "
                f"or re-run: racecast profile use {opts['name']} --force")
        try:
            pa.set_active_profile(root, runtime_root, opts["name"])
        except ValueError as e:
            sys.exit(f"racecast: {e}")
        print(f"active profile: {opts['name']}")
        return None
    # verb == "show"
    try:
        rcfg = pcfg.resolve_config(root, override=opts["name"],
                                   runtime_root=runtime_root)
    except pcfg.ProfileError as e:
        sys.exit(f"racecast: {e}")
    print(pa.format_profile_show(rcfg, active))
    return None


CHAT_VERBS = ("clear", "pull", "import", "export")


def _chat_path():
    return os.path.join(_runtime_dir(), "chat.json")


def _chat_reload_if_running():
    """Best-effort: tell a running local relay to re-read chat.json. A relay
    that is down is fine — it loads the file on next start."""
    try:
        _fetch_relay_page("/chat/reload")
        return True
    except Exception:
        return False


def _health_db_path():
    return os.path.join(_runtime_dir(), "health-history.db")


HEALTH_VERBS = ("export", "import", "pull")


def health_cmd(rest):
    """`racecast health export|import|pull` — move health history across events/machines."""
    verb = rest[0] if rest else None
    if verb not in HEALTH_VERBS:
        sys.exit(f"usage: racecast health {{{'|'.join(HEALTH_VERBS)}}}")
    args = rest[1:]
    db = _health_db_path()

    if verb == "export":
        out, frm = "health-export.jsonl", 0
        if "--out" in args:
            i = args.index("--out")
            if i + 1 >= len(args):
                sys.exit("usage: racecast health export [--from TS] [--out PATH]")
            out = args[i + 1]
        if "--from" in args:
            i = args.index("--from")
            try:
                frm = float(args[i + 1])
            except (IndexError, ValueError):
                sys.exit("racecast: --from requires a numeric epoch value")
        conn = hsmod.open_db(db)
        hsmod.migrate(conn)
        lines = hsmod.export_jsonl(conn, frm)
        conn.close()
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + ("\n" if lines else ""))
        print(f"Exported {len(lines)} samples -> {out}")
        return None

    if verb == "import":
        if not args:
            sys.exit("usage: racecast health import <file.jsonl>")
        try:
            with open(args[0], encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except OSError as e:
            sys.exit(f"racecast: could not read {args[0]}: {e}")
        conn = hsmod.open_db(db)
        hsmod.migrate(conn)
        n = hsmod.import_jsonl(conn, lines)
        conn.close()
        print(f"Imported {n} new samples into {db}")
        return None

    if verb == "pull":
        if not args:
            sys.exit("usage: racecast health pull <tailscale-ip> [--port N] [--from TS]")
        host = args[0]
        port = RELAY_PORT
        if "--port" in args[1:]:
            i = args.index("--port", 1)
            try:
                port = int(args[i + 1])
            except (IndexError, ValueError):
                sys.exit("racecast: --port must be an integer")
        frm = 0
        if "--from" in args[1:]:
            i = args.index("--from", 1)
            try:
                frm = float(args[i + 1])
            except (IndexError, ValueError):
                sys.exit("racecast: --from requires a numeric epoch value")
        url = f"http://{host}:{port}/health/raw?from={frm}"
        try:
            with http_util.open_url(url, timeout=5) as resp:
                if resp.status != 200:
                    sys.exit(f"racecast: pull failed — HTTP {resp.status} from {host}")
                body = resp.read().decode("utf-8")
        except Exception as e:
            sys.exit(f"racecast: pull failed — {type(e).__name__}: {e} (local history unchanged)")
        conn = hsmod.open_db(db)
        hsmod.migrate(conn)
        n = hsmod.import_jsonl(conn, body.splitlines())
        conn.close()
        print(f"Pulled {n} new samples from {host}.")
        return None


def _discord_voice_client():
    """Build a DiscordVoiceClient from the active profile's env, or exit with a hint."""
    import discord_rpc
    cid = os.environ.get("RACECAST_DISCORD_CLIENT_ID", "")
    sec = os.environ.get("RACECAST_DISCORD_CLIENT_SECRET", "")
    if not cid or not sec:
        sys.exit("racecast: this league has no DISCORD_CLIENT_ID/SECRET in profile.env")
    cache = os.path.join(_runtime_dir(), "discord-rpc-token.json")
    return discord_rpc.DiscordVoiceClient(cid, sec, cache)


def _discord_voice_target():
    """(guild, channel) from the Sheet `Discord Voice` override then the env fallback."""
    import discord_rpc
    sheet_val = ""
    sid = os.environ.get("RACECAST_SHEET_ID", "")
    if sid:
        from urllib.parse import quote
        url = ("https://docs.google.com/spreadsheets/d/{}/gviz/tq?tqx=out:csv&sheet={}"
               .format(sid, quote(discord_rpc.CONFIG_TAB)))
        try:
            sheet_val = discord_rpc.discord_voice_from_csv(
                http_util.get_bytes(url, timeout=8).decode("utf-8"))
        except Exception:  # noqa: BLE001 — Sheet unreachable -> fall back to env
            sheet_val = ""
    return discord_rpc.resolve_voice_target(
        sheet_val, os.environ.get("RACECAST_DISCORD_VOICE_URL", ""))


DISCORD_VERBS = ("join", "leave", "status")


def discord_cmd(rest):
    """`racecast discord join|leave|status` — drive the desktop client's voice channel."""
    verb = rest[0] if rest else "status"
    if verb not in DISCORD_VERBS:
        sys.exit("usage: racecast discord {%s}" % "|".join(DISCORD_VERBS))
    if verb == "status":
        # Read-only: never build the client (so it works without CLIENT_ID/SECRET).
        target = _discord_voice_target()
        print("discord voice target: " + ("#".join(target) if target else "none configured"))
        if not (os.environ.get("RACECAST_DISCORD_CLIENT_ID") and
                os.environ.get("RACECAST_DISCORD_CLIENT_SECRET")):
            print("discord app: no DISCORD_CLIENT_ID/SECRET in this league's profile.env")
        return 0
    client = _discord_voice_client()
    if verb == "leave":
        ok, note = client.leave()
        print("discord: " + note)
        return 0 if ok else 1
    target = _discord_voice_target()
    if not target:
        sys.exit("racecast: no voice channel configured (Sheet `Discord Voice` or DISCORD_VOICE_URL)")
    ok, note = client.join(*target)
    print("discord: " + note)
    return 0 if ok else 1


def _discord_autojoin_enabled(env):
    """Auto-join is default-on; RACECAST_DISCORD_AUTOJOIN=0 disables it."""
    return env.get("RACECAST_DISCORD_AUTOJOIN", "1") != "0"


def _discord_autojoin():
    """Best-effort voice join during event start — never fatal, secrets stay quiet."""
    if not _discord_autojoin_enabled(os.environ):
        return
    if not (os.environ.get("RACECAST_DISCORD_CLIENT_ID") and
            os.environ.get("RACECAST_DISCORD_CLIENT_SECRET")):
        return
    try:
        target = _discord_voice_target()
        if not target:
            return
        client = _discord_voice_client()
        # allow_consent=False: auto-join uses only a cached/refreshable token and
        # never opens the interactive consent popup, so an unattended box can't hang.
        ok, note = client.join(*target, allow_consent=False)
        print("discord: " + note if ok
              else "discord: voice auto-join skipped — " + note)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — auto-join must never break event start
        print("discord: voice auto-join skipped ({})".format(type(exc).__name__))


def _discord_autoleave_enabled(env):
    """Auto-leave is default-on; RACECAST_DISCORD_AUTOLEAVE=0 disables it."""
    return env.get("RACECAST_DISCORD_AUTOLEAVE", "1") != "0"


def _discord_autoleave():
    """Best-effort voice leave during event stop — the counterpart to the event-start
    auto-join. Never fatal. Unlike join it needs no channel target (leave just sends
    SELECT_VOICE_CHANNEL with a null channel)."""
    if not _discord_autoleave_enabled(os.environ):
        return
    if not (os.environ.get("RACECAST_DISCORD_CLIENT_ID") and
            os.environ.get("RACECAST_DISCORD_CLIENT_SECRET")):
        return
    try:
        client = _discord_voice_client()
        # allow_consent=False: leave uses only a cached/refreshable token and never
        # opens the interactive consent popup, so an unattended stop can't hang.
        ok, note = client.leave(allow_consent=False)
        print("discord: " + note if ok
              else "discord: voice auto-leave skipped — " + note)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — auto-leave must never break event stop
        print("discord: voice auto-leave skipped ({})".format(type(exc).__name__))


REPORT_VERBS = ("generate", "send")


def _reports_dir():
    return os.path.join(_runtime_dir(), "reports")


def _report_event_title():
    """Active profile's event title from runtime/<profile>/event.json ('' if absent)."""
    try:
        with open(os.path.join(_runtime_dir(), "event.json"), encoding="utf-8") as fh:
            return (json.load(fh).get("title") or "").strip()
    except (OSError, ValueError):
        return ""


def _report_host():
    """This producer machine's hostname, surfaced in the report so it is clear which
    box produced it. Best-effort — '' when the OS can't answer."""
    import socket
    try:
        return (socket.gethostname() or "").strip()
    except OSError:
        return ""


def _session_start_path():
    """The active profile's event-session-start marker (CLI-owned, gitignored)."""
    return os.path.join(_runtime_dir(), "session.json")


def _write_session_start(now=None):
    """Record when THIS event began, so the post-event report window starts at this
    event. Without it, a quick event restart within report_build.SESSION_GAP_S merges
    the previous event's health samples into this report's window (the window START
    never resets). Overwritten on every `event start`. Best-effort — a write failure
    just falls back to the sample-contiguity heuristic."""
    try:
        os.makedirs(_runtime_dir(), exist_ok=True)
        with open(_session_start_path(), "w", encoding="utf-8") as fh:
            json.dump({"start": time.time() if now is None else now}, fh)
    except OSError as exc:
        print("note: could not write session.json ({}) — continuing.".format(exc))


def _is_continuation_start(rest):
    """True when this `event start` continues an in-progress broadcast rather than
    beginning a fresh one: any --stint/--part flag marks a mid-event bring-up (a local
    recovery restart or a stint-corrected takeover). A continuation must NOT reset the
    report session window — only a fresh `event start` (and never a takeover) does."""
    for tok in rest:
        if tok in ("--stint", "--part") or tok.startswith(("--stint=", "--part=")):
            return True
    return False


def _read_session_start():
    """The current event's start ts (float) from session.json, or None."""
    try:
        with open(_session_start_path(), encoding="utf-8") as fh:
            v = json.load(fh).get("start")
        return float(v) if v is not None else None
    except (OSError, ValueError, TypeError):
        return None


# A source that logged during THIS session has an mtime >= the session start; the
# grace absorbs a line written just before `event start` and small clock skew, while
# still excluding a source that has not run in hours/days.
REPORT_LOG_FRESHNESS_GRACE_S = 300


def _report_log_is_fresh(mtime, since, grace_s=REPORT_LOG_FRESHNESS_GRACE_S):
    """Whether a bundled log file (mtime) belongs to the report session: True when
    `since` is None (no window -> keep everything, back-compat) or mtime is within
    `since - grace`. Excludes a stale leftover from a source that did not run this
    session — e.g. a weeks-old runtime/static/logs/feed_*.log from a one-off `racecast
    streams` test, which otherwise rode along in every unrelated event's report bundle
    (#519). Pure — unit-tested."""
    if since is None:
        return True
    return mtime >= since - grace_s


def _report_log_files(since=None):
    """Current (non-archive) log files to bundle with the report, as (source, path)
    pairs across ALL Control Center log sources (relay, streams, obs, companion,
    tailscale, app) — the same live set the Control Center's "All (live)" view shows.
    When `since` (the report window start) is given, a file whose mtime predates the
    session (by more than the grace) is dropped, so a stale leftover from a source that
    did not run this session can't ride along (#519). Best-effort: [] when the registry
    can't be built, a single source that raises is skipped rather than aborting the
    rest, and an unstattable file is kept (never silently dropped). The source name lets
    the caller lay the files out under logs/<source>/ so relay and streams (both
    feed_*.log) don't collide. Archives (rotated older files) are left out."""
    try:
        reg = _log_sources()
    except Exception:  # noqa: BLE001 — bundling logs must never break the report send
        return []
    pairs = []
    for source in ("relay", "streams", "obs", "companion", "tailscale", "app"):
        entry = reg.get(source)
        if not entry:
            continue
        try:
            for fp in entry["files"]():
                if not (fp and os.path.isfile(fp)):
                    continue
                if since is not None:
                    try:
                        if not _report_log_is_fresh(os.path.getmtime(fp), since):
                            continue
                    except OSError:
                        pass  # can't stat -> keep it (conservative, matches old behavior)
                pairs.append((source, fp))
        except Exception:  # noqa: BLE001 — one flaky source must not drop the others
            continue
    return pairs


def _relay_mode():
    """The live relay's schedule mode ('race'/'qualifying') from /status, or None
    when the relay is unreachable. Best-effort — the report title marker degrades
    gracefully. Reuses the loopback JSON helper (no bare urllib)."""
    try:
        data = _relay_fetch_json(f"http://127.0.0.1:{RELAY_PORT}/status")
        mode = data.get("mode")
        return mode if mode in ("race", "qualifying") else None
    except Exception:  # noqa: BLE001 — best-effort
        return None


def qualifying_mode_mismatch_note(requested_qualifying, actual_mode):
    """Warning when `event start --qualifying` was requested but the live relay came up in
    a different mode (actual_mode read from /status) — usually a missing Qualifying tab.
    On 2026-07-10 this silently left the relay in race mode, so the Parts control pushed
    the wrong stream key and the stream went nowhere. Pure → unit-tested. None when the
    mode is consistent, was not requested, or the relay is unreachable (actual_mode None)."""
    if requested_qualifying and actual_mode is not None and actual_mode != "qualifying":
        return (f"event start --qualifying was requested but the relay came up in "
                f"{actual_mode.upper()} mode — the Qualifying tab may be missing/unreadable. "
                f"The Parts control will select the wrong stream key. Fix the Sheet's "
                f"Qualifying tab, then re-run event start --qualifying.")
    return None


def _qualifying_title(base):
    """Append ' — Qualifying' to a report/event title when the live relay is in
    qualifying mode; an empty base becomes just 'Qualifying'. Unchanged otherwise."""
    if _relay_mode() == "qualifying":
        return f"{base} — Qualifying" if base else "Qualifying"
    return base


def _report_name_map():
    """{stint_index (1-based): commentator name} from the LOCAL relay's /schedule/data
    (which runs the canonical schedule parser). Only the `name` field is used — the
    `url` is ignored (redaction). Empty dict when the relay is unreachable."""
    try:
        data = _relay_fetch_json(f"http://127.0.0.1:{RELAY_PORT}/schedule/data")
        return {r["row"]: (r.get("name") or "").strip()
                for r in (data.get("rows") or [])
                if isinstance(r.get("row"), int) and (r.get("name") or "").strip()}
    except Exception:  # noqa: BLE001 — best-effort; names degrade gracefully
        return {}


def _report_backlog_thresholds():
    """The relay's fan-out reserve and backlog threshold from the machine .env, so the
    report counts "behind live" exactly as the relay's yellow reason did (#586)."""
    env = {k: _machine_env_value(k) for k in ("RACECAST_FEED_PREBUFFER_S",
                                              "RACECAST_FEED_BACKLOG_WARN_S")}
    return {"prebuffer_s": hsmod.feed_prebuffer_s(env),
            "backlog_warn_s": hsmod.feed_backlog_warn_s(env)}


def _build_report_file(frm=None, to=None, gap=None, out=None):
    """Core generator. Returns {'path','html','summary'}. Raises ValueError when the
    selected window has no samples."""
    gap = rbuild.SESSION_GAP_S if gap is None else gap
    conn = hsmod.open_db(_health_db_path())
    hsmod.migrate(conn)
    try:
        if frm is None or to is None:
            all_ts = [r["ts"] for r in
                      conn.execute("SELECT ts FROM samples ORDER BY ts ASC").fetchall()]
            # Floor the window at THIS event's start so a quick restart within
            # SESSION_GAP_S no longer merges the previous event's samples.
            s, e = rbuild.select_session(all_ts, gap, floor=_read_session_start())
            frm = s if frm is None else frm
            to = e if to is None else to
        if frm is None or to is None:
            raise ValueError("no health data for that window")
        samples = hsmod.query_range(conn, frm, to)
        # Events (part_end, obs_stream_stop) are recorded during teardown, a few
        # seconds AFTER the last health sample that sets `to`. Query the event tail
        # up to `to + gap` so they still land in the timeline — the next session is
        # >= gap away by construction, so this can't pull in a later session's events.
        events = hsmod.query_events(conn, frm, to + gap)
    finally:
        conn.close()
    if not samples:
        raise ValueError("no health data for that window")
    bucketed = rbuild.bucket_samples(samples)
    title = _qualifying_title(_report_event_title())
    report = rbuild.build_report(bucketed, events, _report_name_map(), title,
                                 (frm, to), time.time(), host=_report_host(),
                                 **_report_backlog_thresholds())
    html = rbuild.render_html(report)
    os.makedirs(_reports_dir(), exist_ok=True)
    date_str = time.strftime("%Y-%m-%d", time.localtime(frm))
    path = out or os.path.join(_reports_dir(), rbuild.report_filename(title, date_str))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return {"path": path, "html": html, "summary": rbuild.render_summary_text(report),
            "report": report, "window": (frm, to)}


def _latest_report():
    try:
        files = [os.path.join(_reports_dir(), f) for f in os.listdir(_reports_dir())
                 if f.endswith(".html")]
    except OSError:
        return None
    return max(files, key=os.path.getmtime) if files else None


def _send_report_core(path, report=None, window=None):
    """Post the report to the league Discord as a GT-Racecast embed with the HTML —
    plus the session's relay logs, clipped to the report window — zipped as a
    download-only attachment. Raises on any failure."""
    if not path:
        raise ValueError("no report found — run `racecast report` first")
    with open(path, "rb") as fh:
        content = fh.read()
    webhook, league = _active_discord_webhook()
    if not webhook:
        raise ValueError("No DISCORD_WEBHOOK_URL configured for this league")
    title = _qualifying_title(_report_event_title() or league or "Event")
    fields_kv = rbuild.report_discord_fields(report) if report else []
    host = (report.get("header", {}) if report else {}).get("host") or _report_host()
    payload = notify.report_discord_payload(
        title, fields_kv, host=host,
        description=rbuild.report_finding_text(report) if report else "")
    frm, to = window if window else (None, None)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(os.path.basename(path), content)
        for source, fp in _report_log_files(since=frm):
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as lf:
                    # logs/<source>/<basename>: relay and streams both use feed_*.log,
                    # so the source subdir keeps them from colliding in the archive.
                    zf.writestr(f"logs/{source}/{os.path.basename(fp)}",
                                rbuild.slice_log_by_window(lf.read(), frm, to))
            except OSError:
                pass  # a log that vanished/rotated mid-send is not fatal
    zip_name = os.path.splitext(os.path.basename(path))[0] + ".zip"
    fields = {"payload_json": json.dumps(payload)}
    files = [("files[0]", zip_name, buf.getvalue(), "application/zip")]
    http_util.post_multipart(webhook, fields=fields, files=files, timeout=15)


def report_generate_data():
    try:
        r = _build_report_file()
        return {"ok": True, "path": r["path"], "html": r["html"], "summary": r["summary"]}
    except Exception as exc:  # noqa: BLE001 — surface the message to the UI
        return {"ok": False, "error": str(exc)}


def report_send_data(path=None):
    try:
        _send_report_core(path or _latest_report())
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001 — surface the message to the UI
        return {"ok": False, "error": str(exc)}


def _report_parse_args(args):
    frm = to = out = None
    gap = None

    def _num(flag):
        i = args.index(flag)
        try:
            return args[i + 1]
        except IndexError:
            sys.exit(f"racecast: {flag} requires a value")

    if "--from" in args:
        try:
            frm = float(_num("--from"))
        except ValueError:
            sys.exit("racecast: --from requires a numeric epoch value")
    if "--to" in args:
        try:
            to = float(_num("--to"))
        except ValueError:
            sys.exit("racecast: --to requires a numeric epoch value")
    if "--session-gap" in args:
        try:
            gap = float(_num("--session-gap"))
        except ValueError:
            sys.exit("racecast: --session-gap requires a numeric seconds value")
    if "--out" in args:
        out = _num("--out")
    return frm, to, gap, out


def report_cmd(rest):
    """`racecast report [generate] [--from TS --to TS --session-gap S --out PATH]`
    | `racecast report send [FILE]` — generate/send the post-event report."""
    verb = rest[0] if rest and rest[0] in REPORT_VERBS else "generate"
    args = rest[1:] if (rest and rest[0] in REPORT_VERBS) else rest

    if verb == "send":
        path = args[0] if args and not args[0].startswith("--") else _latest_report()
        try:
            _send_report_core(path)
        except (OSError, ValueError) as exc:
            sys.exit(f"racecast: {exc}")
        except Exception as exc:  # noqa: BLE001 — network/HTTP
            sys.exit(f"racecast: Discord send failed — {type(exc).__name__}: {exc}")
        print(f"Sent {os.path.basename(path)} to the league Discord.")
        return None

    frm, to, gap, out = _report_parse_args(args)
    try:
        result = _build_report_file(frm, to, gap, out)
    except (OSError, ValueError) as exc:
        sys.exit(f"racecast: {exc} — nothing to report.")
    print(result["summary"])
    print(f"Report written -> {result['path']}")
    return None


def _cues_path():
    return os.path.join(_runtime_dir(), "cues.json")


def _cues_reload_if_running():
    """Best-effort: tell a running local relay to re-read cues.json (handover
    while it is up). A relay that is down loads the file on next start."""
    try:
        _fetch_relay_page("/cues/reload")
        return True
    except Exception:
        return False


def chat_cmd(rest):
    """`racecast chat clear|pull|import|export` — manage the crew-chat history."""
    verb = rest[0] if rest else None
    if verb not in CHAT_VERBS:
        sys.exit(f"usage: racecast chat {{{'|'.join(CHAT_VERBS)}}}")
    path = _chat_path()
    args = rest[1:]

    if verb == "clear":
        ca.write_messages(path, [])
        running = _chat_reload_if_running()
        print("Chat cleared." + ("" if running else
                                 " (relay not running — applies on next start.)"))
        return None

    if verb == "export":
        out = None
        if args[:1] == ["--out"]:
            if len(args) < 2:
                sys.exit("usage: racecast chat export [--out PATH]  (--out requires a value)")
            out = args[1]
        try:                                  # prefer the live relay, fall back to the file
            body = _fetch_relay_page("/chat/data")
            payload = json.loads(body)
        except Exception:
            payload = {"messages": ca.load_messages(path)}
        out = out or "chat-export.json"
        msgs = ca.validate_payload(payload)
        ca.write_messages(out, msgs)
        print(f"Exported {len(msgs)} messages -> {out}")
        return None

    if verb == "import":
        if not args:
            sys.exit("usage: racecast chat import <file>")
        try:
            with open(args[0], encoding="utf-8") as fh:
                payload = json.load(fh)
        except FileNotFoundError:
            sys.exit(f"racecast: file not found: {args[0]}")
        except (OSError, ValueError) as e:
            sys.exit(f"racecast: could not read {args[0]}: {e}")
        try:
            n = ca.apply_pulled(path, payload)    # validates before writing
        except ValueError as e:
            sys.exit(f"racecast: import failed — {e} (local chat unchanged)")
        running = _chat_reload_if_running()
        print(f"Imported {n} messages." + ("" if running else " (relay not running.)"))
        return None

    if verb == "pull":
        if not args:
            sys.exit("usage: racecast chat pull <tailscale-ip> [--port N]")
        host = args[0]
        port = RELAY_PORT
        if "--port" in args[1:]:
            port_idx = args.index("--port", 1)
            if port_idx + 1 >= len(args):
                sys.exit("racecast: --port requires an integer value")
            port_str = args[port_idx + 1]
            try:
                port = int(port_str)
            except ValueError:
                sys.exit(f"racecast: --port must be an integer, got {port_str!r}")
        url = f"http://{host}:{port}/chat/data"
        try:
            with http_util.open_url(url, timeout=5) as resp:
                if resp.status != 200:
                    sys.exit(f"racecast: pull failed — HTTP {resp.status} from {host}")
                payload = json.loads(resp.read())
        except Exception as e:
            sys.exit(f"racecast: pull failed — {type(e).__name__}: {e}"
                     " (local chat unchanged)")
        try:
            n = ca.apply_pulled(path, payload)
        except ValueError as e:
            sys.exit(f"racecast: pull failed — {e} (local chat unchanged)")
        running = _chat_reload_if_running()
        print(f"Pulled {n} messages from {host}." +
              ("" if running else " (relay not running — applies on next start.)"))
        return None


CONSOLE_VERBS = ("setup-funnel", "token", "pull-versions")


def _console_versions_path():
    """runtime/<active-profile>/console-versions.json — same dir the relay reads,
    matching _chat_path()."""
    return os.path.join(_runtime_dir(), "console-versions.json")


def _set_env_key(path, key, value):
    """Set ONE key in a .env / profile.env, preserving every other key + comments.
    _write_env_file()/merge_env_text() treat their `entries` as the COMPLETE
    desired key set (any existing key NOT passed is dropped), so we must read the
    full file, update/add the one key, and write the whole set back. {ok}|{error}."""
    entries = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            entries = [{"key": k, "value": v}
                       for k, v in parse_env_text(fh.read()).items()]
    for e in entries:
        if e["key"] == key:
            e["value"] = value
            break
    else:
        entries.append({"key": key, "value": value})
    return _write_env_file(path, entries)


def _console_roster():
    """Distinct streamer names from the active schedule (first-seen order), read
    from the running relay's /schedule/data. Raises on an unreachable relay."""
    data = _relay_fetch_json(f"http://127.0.0.1:{RELAY_PORT}/schedule/data")
    seen, roster = set(), []
    for row in (data or {}).get("rows", []):
        name = (row.get("name") or "").strip()
        key = cpa.streamer_key(name)
        if key and key not in seen:
            seen.add(key)
            roster.append(name)
    return roster


def _crew_roster():
    """Distinct crew names (Director/Producer) from the running relay's
    /crew/data (first-seen order). Raises on an unreachable relay — mirrors
    _console_roster. Empty list when the league has no Crew tab."""
    data = _relay_fetch_json(f"http://127.0.0.1:{RELAY_PORT}/crew/data")
    seen, roster = set(), []
    for row in (data or {}).get("rows", []):
        name = (row.get("name") or "").strip()
        key = cpa.streamer_key(name)
        if key and key not in seen:
            seen.add(key)
            roster.append(name)
    return roster


def _crew_roster_safe():
    """_crew_roster() that returns [] instead of raising (Control Center poll)."""
    try:
        return _crew_roster()
    except Exception:
        return []


def _links_roster():
    """People to mint console links for = live Schedule roster ∪ Crew tab,
    deduped by streamer_key (pinned == asset_key, the key resolve_roles uses),
    schedule first so commentators keep their existing order. Raises if the
    schedule is unreadable (relay down); crew is best-effort."""
    roster = list(_console_roster())            # may raise if relay is down
    seen = {cpa.streamer_key(n) for n in roster}
    for name in _crew_roster_safe():
        key = cpa.streamer_key(name)
        if key and key not in seen:
            seen.add(key)
            roster.append(name)
    return roster


def _post_chat_message(text):
    """Best-effort POST of one crew-chat message to the local relay."""
    http_util.post_json(f"http://127.0.0.1:{RELAY_PORT}/chat/send",
                        {"user": "Producer", "text": text}, timeout=3)


def funnel_cmd(rest):
    """`racecast funnel on|off` — public ingress for ONLY /console via Tailscale
    Funnel (the role-adaptive crew launcher; #216). Requires MagicDNS + HTTPS +
    the 'funnel' nodeAttr (one-time tailnet-admin step); funnel() surfaces the
    verbatim error if missing. Only /console is mounted — root control endpoints
    stay tailnet/loopback-only (the security boundary)."""
    import tailscale as ts
    if not rest or rest[0] not in ("on", "off"):
        sys.exit("usage: racecast funnel {on|off} [--force]")
    enable = rest[0] == "on"
    binary, _state, _ip = ts.tailscale_backend()
    if not binary:
        sys.exit("racecast: Tailscale CLI not found / backend not running.")
    # Fail fast on the one-time prerequisite: without the 'funnel' nodeAttr the
    # `tailscale funnel` CLI blocks on an interactive enable prompt (a 20 s hang
    # with no stdin). Detect it and print the exact admin steps instead.
    if enable and "--force" not in rest and not ts.funnel_capable():
        sys.exit(
            "racecast: this node is not authorized for Tailscale Funnel yet.\n"
            "One-time tailnet-admin setup at https://login.tailscale.com/admin :\n"
            "  1. DNS -> enable MagicDNS AND HTTPS Certificates\n"
            "  2. Access Controls -> grant the 'funnel' nodeAttr, e.g.:\n"
            '       "nodeAttrs": [{ "target": ["autogroup:member"], "attr": ["funnel"] }]\n'
            "Then re-run 'racecast funnel on'. (Use --force to skip this check.)")
    ok, detail = ts.funnel(binary, path="/console", target_port=RELAY_PORT,
                           enable=enable)
    if not ok:
        sys.exit(f"racecast: funnel {'on' if enable else 'off'} failed: {detail}\n"
                 "Hint: enable MagicDNS + HTTPS and add the 'funnel' nodeAttr in the "
                 "tailnet policy (one-time admin step).")
    print(f"funnel {'enabled' if enable else 'disabled'}. {detail}".strip())
    return None


def links_cmd(rest):
    """`racecast links [--post]` — print one /console launcher link per person
    (Crew tab ∪ live Schedule). Each link carries a signed identity token; the
    relay resolves the person's roles server-side, so one link adapts to
    commentator/director/producer. --post drops them into crew chat. (#216)"""
    _apply_active_profile_env()
    secret = _ensure_active_console_secret()
    if not secret:
        sys.exit("racecast: no active league profile — create or select one first.")
    try:
        roster = _links_roster()
    except Exception:
        sys.exit("racecast: could not read the schedule (is the relay running?).")
    if not roster:
        sys.exit("racecast: no crew or streamers found (is the relay running?).")
    host = _tailscale_ip() or "<tailscale-ip>"
    magic = _tailscale_magicdns() or "<your-magicdns-host>"
    versions = cpadm.load_versions(_console_versions_path())
    post = "--post" in rest
    lines = []
    for name in roster:
        key = cpa.streamer_key(name)
        tok = cpa.mint_token(secret, key, cpadm.current_version(versions, key))
        url = f"https://{magic}/console?t={tok}"                # Funnel host
        lan = f"http://{host}:{RELAY_PORT}/console?t={tok}"      # tailnet fallback
        print(f"{name}:\n  funnel:  {url}\n  tailnet: {lan}")
        lines.append(f"{name}: {url}")
    print()
    print("Share this ONE link with the whole crew (Discord login resolves their role):")
    print(f"  https://{magic}/console")
    print("Discord OAuth redirect URI to register in the league's Discord app:")
    print(f"  https://{magic}/console/oauth/callback")
    if post:
        try:
            _post_chat_message("Console links:\n" + "\n".join(lines))
            print("posted links into crew chat.")
        except Exception:
            print("note: could not post to crew chat (relay not running?).")
    return None


def _console_token(args):
    """`racecast console token revoke <streamer>` — bump that streamer's version
    so their current link stops validating; re-issue with 'racecast links'.
    The relay reads console-versions.json per request, so the bump is immediate —
    no relay reload needed."""
    if len(args) < 2 or args[0] != "revoke":
        sys.exit("usage: racecast console token revoke <streamer-name>")
    key = cpa.streamer_key(args[1])
    if not key:
        sys.exit("racecast: empty streamer name.")
    new_ver = cpadm.bump_version(_console_versions_path(), key)
    print(f"revoked '{args[1]}' (key {key}) -> version {new_ver}. "
          "Re-issue with 'racecast links'.")
    return None


def _console_pull_versions(args):
    """`racecast console pull-versions <ip> [--port N]` — fetch producer A's
    console-versions over the tailnet and adopt them locally (takeover). Mirrors
    `chat pull`: tailnet trust, best-effort."""
    if not args or args[0].startswith("-"):
        sys.exit("usage: racecast console pull-versions <A-tailscale-ip> [--port N]")
    host = args[0]
    port = _takeover_port(args[1:])
    # The endpoint authenticates on the shared league secret (same league = same
    # secret, which travels with the profile). We send OUR secret; A validates it.
    _apply_active_profile_env()
    secret = os.environ.get("RACECAST_CONSOLE_SECRET") or ""
    try:
        payload = http_util.get_json(f"http://{host}:{port}/cockpit/versions",
                                     headers={"X-Console-Secret": secret}, timeout=5)
    except Exception as exc:
        sys.exit(f"racecast: could not fetch console versions from {host}:{port} "
                 f"({type(exc).__name__})")
    if not isinstance(payload, dict):
        sys.exit(f"racecast: bad console versions response from {host}:{port}")
    try:
        count = cpadm.apply_pulled(_console_versions_path(), payload)
    except ValueError as exc:
        sys.exit(f"racecast: bad console versions payload: {exc}")
    print(f"pulled {count} console version record(s) from {host}.")
    return None


def _ensure_active_console_secret():
    """Zero-config console: make sure the active league has a CONSOLE_SECRET so the
    relay can mint/verify tokens without an explicit setup step, and mirror it into
    os.environ so a spawned relay inherits it. Generates a random per-league secret
    in profile.env on first use; idempotent; respects an already-set secret (so an
    exported/imported league keeps its tokens). Only provisions into a real, existing
    profile — never the shipped 'example' profile and never a non-existent one.
    Best-effort: returns the secret or None and never raises."""
    try:
        env_val = (os.environ.get("RACECAST_CONSOLE_SECRET") or "").strip()
        if env_val:
            return env_val
        import secrets
        name, ppath = _active_profile_env_strict()
        if not name or name == "example" or not ppath or not os.path.exists(ppath):
            return None
        with open(ppath, encoding="utf-8") as fh:
            parsed = parse_env_text(fh.read())
            existing = parsed.get("CONSOLE_SECRET", "")
        if existing:                       # already provisioned (or exported) -> reuse
            os.environ["RACECAST_CONSOLE_SECRET"] = existing
            return existing
        fresh = secrets.token_hex(32)      # first use on this league -> generate + persist
        if not _set_env_key(ppath, "CONSOLE_SECRET", fresh).get("ok"):
            return None
        os.environ["RACECAST_CONSOLE_SECRET"] = fresh
        return fresh
    except Exception:
        return None


def console_cmd(rest):
    """`racecast console token|setup-funnel|pull-versions` — manage the
    Commentator Cockpit (issue #191). The console is zero-config: a per-league
    CONSOLE_SECRET is auto-generated on first relay start and the relay serves
    /cockpit whenever one exists (token-gated). PUBLIC exposure is the top-level
    `racecast funnel` command. Console links (Crew ∪ Schedule) are issued via the
    top-level `racecast links` command (#216)."""
    verb, args = rest[0], rest[1:]

    if verb == "setup-funnel":
        return _console_setup_funnel(args)
    if verb == "token":
        return _console_token(args)
    if verb == "pull-versions":
        return _console_pull_versions(args)
    sys.exit(f"usage: racecast console {{{'|'.join(CONSOLE_VERBS)}}}")


BACKUP_VERBS = ("create", "list", "restore", "delete")


def _backup_sources():
    """The four dirs a look backup spans for the active profile."""
    overlay = _active_overlay_dir()              # profiles/<active>/overlay
    g_dir, m_dir = _asset_dirs()                 # runtime/<active>/graphics|media
    return {"overlay": overlay, "graphics": g_dir, "media": m_dir,
            "backups": os.path.join(_runtime_dir(), "backups")}


def backup_cmd(rest):
    """`racecast backup create|list|restore|delete <label>` — named look snapshots
    (overlay CSS + graphics + media) for the active profile."""
    import backup_admin as ba
    verb = rest[0] if rest else None
    if verb not in BACKUP_VERBS:
        sys.exit(f"usage: racecast backup {{{'|'.join(BACKUP_VERBS)}}} [<label>]")
    args = rest[1:]
    src = _backup_sources()
    profile = _active_profile_name() or ""

    if verb == "list":
        items = ba.list_backups(src["backups"])
        if not items:
            print("No backups yet. Create one: racecast backup create <label>")
            return None
        for it in items:
            c = it["counts"]
            print(f"  {it['label']}  ({it['created']}, {it['bytes']} bytes, "
                  f"overlay {c.get('overlay',0)} / graphics {c.get('graphics',0)} "
                  f"/ media {c.get('media',0)})")
        return None

    if verb == "create":
        if not args:
            sys.exit("usage: racecast backup create <label> [--force]")
        force = "--force" in args
        label = " ".join(a for a in args if a != "--force").strip()
        if not label:
            sys.exit("racecast: backup create needs a label")
        try:
            path = ba.create_backup(label, src, profile=profile, force=force)
        except FileExistsError as e:
            sys.exit(f"racecast: {e}")
        except ValueError as e:
            sys.exit(f"racecast: {e}")
        print(f"Saved look '{label}' -> {path}")
        return None

    if verb == "delete":
        if not args:
            sys.exit("usage: racecast backup delete <label>")
        try:
            removed = ba.delete_backup(src["backups"], args[0])
        except ValueError as e:
            sys.exit(f"racecast: {e}")
        print("Deleted." if removed else "racecast: no such backup.")
        return None

    # verb == "restore"
    if not args:
        sys.exit("usage: racecast backup restore <label>")
    try:
        slug = ba.sanitize_label(args[0])
    except ValueError as e:
        sys.exit(f"racecast: {e}")
    zip_path = os.path.join(src["backups"], f"{slug}.zip")
    try:
        ba.restore_backup(zip_path, src)
    except ValueError as e:
        sys.exit(f"racecast: restore failed — {e} (live look unchanged)")
    print(f"Restored look '{args[0]}'.")
    _refresh_obs_pages(force=True)   # best-effort: reload the overlay browser sources
    print("Note: OBS graphics/media sources reload on the next scene activation "
          "(or right-click → Refresh).")
    return None


def _tailscale_ip():
    try:
        import tailscale
        return tailscale.detect_tailscale_ip()
    except Exception:
        return None


def _tailscale_magicdns():
    """This machine's MagicDNS name (the public Funnel host), or '' if unavailable."""
    try:
        import tailscale
        return tailscale.detect_magicdns_name()
    except Exception:
        return ""


def _tailscale_peers():
    """Tailnet peers for the Control Center takeover dropdown ([] best-effort)."""
    try:
        import tailscale
        return tailscale.tailscale_peers()
    except Exception:
        return []

def _relay_http_ok():
    """True iff the relay control server answers on localhost."""
    try:
        # .read() drains the socket; we only care whether the request succeeds
        http_util.get_bytes(f"http://127.0.0.1:{RELAY_PORT}/status", timeout=3)
        return True
    except Exception:
        return False


def _relay_fetch_json(url, timeout=3):
    """GET a relay control-server endpoint and parse its JSON body."""
    return http_util.get_json(url, timeout=timeout)


def _relay_telemetry_source():
    """The running relay's GET /telemetry/data payload (a dict) so the caller can read
    its latched-console `source` field. None on any failure (no relay, telemetry
    disabled -> 404, timeout, non-dict body)."""
    try:
        data = http_util.get_json(
            f"http://127.0.0.1:{RELAY_PORT}/telemetry/data", timeout=2)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _discover_consoles(**kwargs):
    # Lazy peer import (see build-binary.py hidden-imports). Guard it so a broken/
    # partial install degrades to an empty result instead of crashing resolve_console's
    # never-raise contract (the CLI has no outer wrapper; the CC endpoint does).
    try:
        import gt7_discovery
    except ImportError as exc:
        return {"consoles": [], "note": f"discovery unavailable: {exc}"}
    return gt7_discovery.discover_consoles(**kwargs)


def resolve_console(timeout=4.0, *, discover=None, relay_get=None):
    """Resolve GT7 console IP(s) two ways: prefer a running relay's already-latched
    console (no second 33740 bind, instant); else broadcast-scan the LAN. Returns
    {consoles, note, from_relay}. Never raises.

    Note: tier-1 (from_relay) returns whatever the relay latched, and the relay latches
    the first responder BEFORE decrypting (racecast-feeds.py _telemetry_loop), so on a
    noisy LAN it can be a non-GT7 host; the tier-2 scanner is decrypt-gated. Same LAN
    trust boundary either way (spec non-goal)."""
    relay_get = relay_get or _relay_telemetry_source
    relayed = relay_get()
    src = relayed.get("source") if isinstance(relayed, dict) else None
    if src:
        return {"consoles": [src], "note": "", "from_relay": True}
    discover = discover or _discover_consoles
    res = discover(timeout=timeout)
    return {"consoles": res.get("consoles", []), "note": res.get("note", ""),
            "from_relay": False}


def _active_console_secret():
    """The active league's CONSOLE_SECRET from the resolved profile env ('' if unset).
    Same league = same secret (it travels with `profile export`), so producer B already
    holds A's secret — no typing needed for a same-league takeover."""
    _apply_active_profile_env()
    return (os.environ.get("RACECAST_CONSOLE_SECRET") or "").strip()


def _funnel_takeover_base(host):
    """`https://<magicdns-host>/console/takeover` from a MagicDNS host or a pasted URL.
    Strips any scheme and trailing path (e.g. '.../console') the operator pasted."""
    host = (host or "").strip()
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0].rstrip("/")
    return "https://%s/console/takeover" % host


def _takeover_get(url, secret=None, timeout=5):
    """GET a (funnel) takeover endpoint with the step-up secret header. Raises
    HTTPError on 401/403 (bad secret) so the caller can distinguish auth
    rejection from a network failure."""
    headers = {"X-Console-Secret": secret} if secret else None
    return http_util.get_json(url, headers=headers, timeout=timeout)


def _takeover_get_text(url, secret=None, timeout=5):
    """GET a (funnel) takeover endpoint that returns text (NDJSON), with step-up."""
    headers = {"X-Console-Secret": secret} if secret else None
    with http_util.open_url(url, headers=headers, timeout=timeout) as r:
        return r.read().decode("utf-8")


def _relay_post_json(url, payload, timeout=3):
    """POST a JSON body to a relay control-server endpoint and parse its JSON
    reply (the write sibling of _relay_fetch_json)."""
    return json.loads(http_util.post_json(url, payload, timeout=timeout).decode("utf-8"))


_EVENT_TITLE_SANITIZER = None


def _event_title_sanitizer():
    """The relay's single sanitize_event_title rule (#207), loaded once and cached.
    One source of truth for EVENT_TITLE_MAX/normalization — the Control Center must
    not duplicate it (CLAUDE.md: keep the rule un-forked)."""
    global _EVENT_TITLE_SANITIZER
    if _EVENT_TITLE_SANITIZER is None:
        _EVENT_TITLE_SANITIZER = \
            _load_relay_module("relay/racecast-feeds.py").sanitize_event_title
    return _EVENT_TITLE_SANITIZER


def relay_status_data(read_pid=None, alive=None, http_ok=None):
    """Structured relay state — one source for `racecast status` (text) and the
    Control Center's /api/status (JSON). Injection points are for tests."""
    read_pid = read_pid or sv.read_pid
    alive = alive or sv.pid_alive
    http_ok = http_ok or _relay_http_ok
    pid = read_pid(_relay_pid_path())
    is_alive = alive(pid)
    return {"pid": pid, "alive": is_alive, "port": RELAY_PORT,
            "http_ok": http_ok() if is_alive else False}


def _relay_extra_text(data, tailscale_ip):
    """The CLI's extra column for a live relay, from relay_status_data()."""
    parts = [f"control http://127.0.0.1:{data['port']}/status OK" if data["http_ok"]
             else f"(port {data['port']} not responding)"]
    if tailscale_ip:
        parts.append(f"tablet/panel http://{tailscale_ip}:{data['port']}/panel")
    return "  ".join(parts)

def _companion_tablet_port():
    """Companion's web/tablet port from its config.json (best effort, 8000)."""
    try:
        cc = _companion()
        with open(cc.companion_config_path(sys.platform), encoding="utf-8") as fh:
            return int(json.load(fh).get("http_port", 8000))
    except Exception:
        return 8000


def _frozen_child_env():
    """Env for daemon children spawned from the frozen --onefile binary.
    PyInstaller >= 6.10 treats a child running the SAME executable as a worker
    that shares the parent's _MEIPASS extraction dir — which the parent deletes
    on exit, killing the daemon ('Failed to import encodings module'). Setting
    PYINSTALLER_RESET_ENVIRONMENT=1 is the documented way to spawn an
    independent instance: the child extracts its own bundle and outlives us.

    The child also gets its extraction dir moved OUT of the OS temp dir into
    runtime/bundle. A daemon runs for days, and every OS reaps its temp dir on a
    schedule without asking whether anything is still using it — a relay that
    stays up between two events otherwise loses its HUD pages mid-life while
    looking healthy (see services.daemon_bundle_env)."""
    if not IS_FROZEN:
        return None
    env = os.environ.copy()
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    bundle_dir = os.path.join(_runtime_base_dir(), "bundle")
    try:
        os.makedirs(bundle_dir, exist_ok=True)
    except OSError:
        return env  # unwritable install dir — keep the OS default, don't fail
    return sv.daemon_bundle_env(env, bundle_dir)


def relay_start_plan(*, port_pids, feed_pids, pidfile_pid, pidfile_alive,
                     running_profile, active_profile, http_ok):
    """Pure: decide what `relay start` must do, from the gathered signals.

    Returns (action, kill_pids, reason):
      action  "start"   control port free -> just start
              "running" exactly one healthy active-profile relay we own -> no-op
              "heal"    a defect (orphan / split-brain / wrong-profile / dead-PID /
                        not-responding) -> kill kill_pids, then start fresh
      kill_pids  sorted union of the 8088 + feed-port holders (heal only, else [])
      reason     short plain-language defect string (heal only, else "")

    An EMPTY/unknown running_profile counts as a mismatch (heal): a current-binary
    relay always writes its stamp, so a stampless holder is a pre-stamp/old daemon.
    """
    port_set = set(port_pids)
    if not port_set:
        return ("start", [], "")
    single = len(port_set) == 1
    ours = single and pidfile_alive and (pidfile_pid in port_set)
    if ours and http_ok and running_profile and running_profile == active_profile:
        return ("running", [], "")
    kill = sorted(port_set | set(feed_pids))
    holders = ", ".join(str(p) for p in sorted(port_set))
    if len(port_set) > 1:
        reason = f"split-brain: {len(port_set)} listeners on port {RELAY_PORT}"
    elif not pidfile_alive:
        reason = f"dead pidfile but port {RELAY_PORT} held by PID {holders}"
    elif pidfile_pid not in port_set:
        reason = f"foreign holder PID {holders}"
    elif not http_ok:
        reason = f"relay not responding on port {RELAY_PORT}"
    else:
        reason = f"serving profile {running_profile or '(none)'!r}, active is {active_profile!r}"
    return ("heal", kill, reason)


RELAY_START_VERIFY_S = 15   # seconds to confirm the freshly spawned relay bound its control port


def _spawn_relay_verified(argv, attempts=2, verify_s=RELAY_START_VERIFY_S):
    """Spawn the relay daemon and CONFIRM it actually bound the control port, retrying
    once. start_detached returns a PID the instant the child is forked — but the child
    fail-fast aborts if a just-killed holder's port is still clearing (the 2026-07-10
    event-start race), so a returned PID is NOT proof the relay is up. Poll /status; if
    it never answers, respawn once — the port has had time to clear by then. Returns the
    live PID, or None after giving up. Never lets the caller announce 'relay started' for
    a dead child (which made the failed qualifying bring-up look green)."""
    for attempt in range(1, attempts + 1):
        pid = sv.start_detached(argv, _relay_boot_log_path(), _relay_pid_path(),
                                env=_frozen_child_env())
        if wait_for(_relay_http_ok, verify_s):
            return pid
        if attempt < attempts:
            print(f"  relay not responding on port {RELAY_PORT} yet — the port may still "
                  f"be clearing; retrying ({attempt + 1}/{attempts})…")
    print(f"  ERROR: relay failed to come up on port {RELAY_PORT} after {attempts} "
          f"attempt(s) — check: racecast relay logs")
    return None


def relay_start(rest):
    stint = _stint_args(rest)   # validate early: fail fast BEFORE spawning the daemon
    # Gather the signals for the pure plan. The PID file is the un-scoped singleton
    # (_relay_pid_path), so it finds the one tracked relay even across a profile
    # switch; pids_on_port finds EVERY listener (incl. an untracked orphan / a
    # Windows dual-bind split-brain) the PID file cannot see.
    port_pids = pt.pids_on_port(RELAY_PORT)
    feed_pids = sorted({p for fp in pt.FEED_PORTS for p in pt.pids_on_port(fp)})
    pid = sv.read_pid(_relay_pid_path())
    action, kill_pids, reason = relay_start_plan(
        port_pids=port_pids, feed_pids=feed_pids,
        pidfile_pid=pid, pidfile_alive=sv.pid_alive(pid),
        running_profile=_running_relay_profile(),
        active_profile=_active_profile_name() or "",
        http_ok=_relay_http_ok() if port_pids else False)
    if action == "running":
        print(f"relay already running (pid {pid}).")
        if stint:
            print(f"  --stint ignored (relay keeps its position) — to reposition the "
                  f"running relay open http://127.0.0.1:{RELAY_PORT}/set/stint/{stint[1]}")
        relay_status([])
        return None
    if action == "heal":
        # Self-heal a defect (orphan / split-brain / wrong-profile / dead-PID /
        # unresponsive). Kill BY PORT — not via the PID file or `freeport` (which
        # refuses while "a relay is alive") — so a cross-profile/old-binary orphan
        # is actually cleared instead of deadlocking the start (#273 follow-up).
        print(f"relay: clearing stale holder(s) of port {RELAY_PORT} "
              f"({reason}) — killing PID {', '.join(map(str, kill_pids))}, then restarting.")
        for kpid in kill_pids:
            pt.kill_pid(kpid)
        left = sorted({p for p in pt.pids_on_port(RELAY_PORT)})
        if left:
            print(f"  WARNING: port {RELAY_PORT} STILL held by PID "
                  f"{', '.join(map(str, left))} after kill — start may fail.")
        if os.path.exists(_relay_pid_path()):
            os.remove(_relay_pid_path())
        _clear_relay_profile_stamp()
    # action in {"start", "heal"} -> fresh start.
    # A feed port may still LISTEN even with 8088 free (a leaked static-streams
    # streamlink) -> warn rather than letting Feed A loop silently in "connecting".
    busy = [p for p in pt.FEED_PORTS if pt.pids_on_port(p)]
    if busy:
        print(f"WARNING: feed port(s) {', '.join(map(str, busy))} already in use — "
              f"that feed may fail to bind. Free them first: racecast freeport")
    _ensure_active_console_secret()   # zero-config console: provision + inject the secret
    # Resolve + inject the producer name (#317) so the relay can tag its OBS-stream
    # events and /status; the child inherits it via the environment.
    os.environ["RACECAST_PRODUCER_NAME"] = _resolve_producer_name()
    _write_relay_profile_stamp()      # record the running profile before spawn (#273)
    argv = _relay_daemon_argv(rest, IS_FROZEN)
    newpid = _spawn_relay_verified(argv)
    if newpid is None:
        return None                   # honest failure already reported — never claim success
    print(f"relay started (pid {newpid}). Watch it: racecast relay logs -f")
    _append_tailscale_snapshot()
    _refresh_obs_pages(wait=0)    # relay already confirmed up -> refresh OBS pages now
    # Post-start verification: exactly one process should now hold 8088. More than
    # one means a residual dual-bind split-brain survived -> surface it, don't hide it.
    holders = sorted({p for p in pt.pids_on_port(RELAY_PORT)})
    if len(holders) > 1:
        print(f"  WARNING: {len(holders)} processes listen on port {RELAY_PORT} "
              f"(PID {', '.join(map(str, holders))}) — possible split-brain; "
              f"re-run 'racecast relay start' to reconcile.")
    return None

def _obs_pages_hash_path():
    return os.path.join(_runtime_dir(), "obs-pages.hash")


def _sync_pov_transform(set_transform=None):
    """Best-effort live sibling of the setup-time POV/webcam bake: push every
    mapped overlay slot's box position/size onto its OBS scene item (see
    overlay_build.OVERLAY_SLOT_OBS_SOURCES — 'pov' -> Stint/'Feed POV', 'webcam'
    -> Program/'Solo Webcam'). Reads the hud.html base and the profile override
    CSS ONCE, then loops the slots, merging override over base per slot (so an
    override of only some props keeps the rest at the base) and calling
    SetSceneItemTransform. Silent per-slot on any miss — OBS unreachable, no
    overlay, no base rule for that slot, missing scene/source (e.g. no 'Solo
    Webcam' in an endurance collection). `set_transform` is a test seam
    (defaults to obs_ws.set_scene_item_transform)."""
    import overlay_build
    try:
        with open(os.path.join(HERE, "obs", "hud.html"), encoding="utf-8") as fh:
            base_style = overlay_build.base_style(fh.read())
    except OSError:
        return
    override_css = ""
    od = _active_overlay_dir()
    css = os.path.join(od, "hud.css") if od else None
    if css and os.path.isfile(css):
        try:
            with open(css, encoding="utf-8") as fh:
                override_css = fh.read()
        except OSError:
            override_css = ""
    try:
        import obs_ws
        if set_transform is None:
            set_transform = obs_ws.set_scene_item_transform
        for slot_id, tgt in overlay_build.OVERLAY_SLOT_OBS_SOURCES.items():
            base = overlay_build.box_from_css(base_style, slot_id)
            if not base:                # base page lost this slot's rule -> nothing to anchor
                continue
            overrides = overlay_build.box_from_css(override_css, slot_id)
            box = {**base, **overrides}
            transform = obs_ws.pov_scene_item_transform(box)
            ok, note = set_transform(tgt["scene"], tgt["source"], transform)
            if ok:
                print(f"obs: {slot_id} box synced to '{tgt['source']}' "
                      f"({box['left']},{box['top']} {box['width']}x{box['height']}).")
    except Exception as exc:  # noqa: BLE001 — best-effort contract
        print(f"obs: box sync skipped ({exc}).")
        return


def _refresh_obs_pages(force=False, wait=0):
    """Refresh the relay-served OBS browser sources (HUD, which includes the race timer) when
    the pages changed since the last successful refresh — replaces the manual
    right-click → 'Refresh cache of current page' (OBS's CEF caches the page
    JS until then; a producer updating the package must never go on air with
    a stale page). Best effort like _release_obs_feeds: one notice, never an
    exception. wait: seconds to allow a just-spawned relay to open its control
    port — never refresh against a closed port (the source would load a CEF
    error page that does not self-recover)."""
    if not wait_for(_relay_http_ok, wait):
        print(f"obs: page refresh skipped — relay not responding on port {RELAY_PORT}.")
        return
    served = served_pages_hash()
    decision = refresh_decision(served, read_pages_hash(_obs_pages_hash_path()), force)
    if decision == "skip-no-pages":
        print("obs: page refresh skipped — could not read /hud from the relay.")
        return
    if decision == "skip-unchanged":
        return                              # unchanged pages -> no on-air flicker
    try:
        import obs_ws
        names, note = obs_ws.refresh_browser_inputs(needle=f"127.0.0.1:{RELAY_PORT}")
        if note:
            print(f"obs: page refresh skipped — {note}")
            return                          # hash kept -> retried on the next start
        write_pages_hash(_obs_pages_hash_path(), served)   # only confirmed refreshes advance the gate
    except Exception as exc:                # a start must never fail on this
        print(f"obs: page refresh skipped ({exc}).")
        return
    print(f"obs: refreshed browser sources {', '.join(names)}." if names
          else "obs: no relay browser sources in OBS — nothing to refresh.")
    _sync_pov_transform()              # live POV-box position sibling (best effort)
    # Feed A/B ship close_when_inactive=False; fan-out needs True (OBS disconnects
    # off-air so no stale backlog forms → kills the ~2 s stale-on-activation glitch).
    # Set it to track the flag UNCONDITIONALLY so a fallback to direct-serve (flag
    # off) reverts A/B to False — the coexistence guarantee. POV ships True (correct
    # in both modes) and is deliberately left untouched. Best effort.
    # Default ON, set =0 to fall back (mirror of fanout_enabled in racecast-feeds.py).
    _fanout = _machine_env_value("RACECAST_FEED_FANOUT").strip().lower() not in {"0", "false", "no", "off"}
    try:
        note = obs_ws.set_feed_close_when_inactive(list(obs_ws.FEED_SOURCES.values()), _fanout)
        if note:
            print("obs: " + note)
    except Exception as exc:  # noqa: BLE001 — best-effort contract
        print(f"obs: close_when_inactive skipped ({exc}).")


def app_launch_cmd(rest):
    """Launch a GUI app (obs|discord) detached — the same mechanism `racecast event
    start` uses, exposed as a button so the Control Center can start each app
    individually. Best effort: a missing app or spawn error exits non-zero."""
    name = rest[0] if rest else None
    if name not in APP_CONTROLLED:
        sys.exit(f"usage: racecast app launch {{{'|'.join(APP_CONTROLLED)}}}")
    ev = _event_modules()[0]
    cmd = ev.launch_command(name, sys.platform)
    if cmd is None:
        if name == "tailscale" and sys.platform.startswith("linux"):
            sys.exit("app: Tailscale on Linux has no GUI app to launch — it runs as "
                     "a daemon. Connect with `racecast tailscale up` (first time: "
                     f"{_tailscale_login_hint()}).")
        sys.exit(f"app: cannot launch {name} on this system — is it installed?")
    argv, cwd = cmd
    # Same environment handling as `event start`: this is the documented manual
    # fallback for launching OBS, so it must not fail in the ways event start no
    # longer does (#572) — the frozen library path, and no session overrides
    # when run over SSH.
    child_env, _overrides = _gui_child_env(name, ev)
    try:
        _gui_spawn(argv, cwd, child_env, name)
    except OSError as exc:
        sys.exit(f"app: failed to launch {name} ({exc}).")
    print(f"Launched {name}.")


def app_quit_cmd(rest):
    """Ask a GUI app (obs|discord) to quit — graceful where possible (macOS
    AppleScript quit, Windows taskkill, Linux pkill). The Control Center wraps
    this in a confirm dialog; quitting OBS mid-broadcast is the operator's call."""
    name = rest[0] if rest else None
    if name not in APP_CONTROLLED:
        sys.exit(f"usage: racecast app quit {{{'|'.join(APP_CONTROLLED)}}}")
    ev = _event_modules()[0]
    cmd = ev.quit_command(name, sys.platform)
    if cmd is None:
        sys.exit(f"app: cannot quit {name} on this system.")
    try:
        rc = subprocess.run(cmd, capture_output=True).returncode
    except OSError as exc:
        sys.exit(f"app: failed to quit {name} ({exc}).")
    if rc == 0:
        print(f"Asked {name} to quit.")
    else:
        sys.exit(f"app: {name} did not quit (exit {rc}) — it may not be running.")


def obs_refresh_cmd(_rest):
    """Force-refresh every relay-served browser source — the scriptable
    right-click → Refresh (no staleness gate)."""
    # Upfront probe for a real exit code + directive message; _refresh_obs_pages
    # re-probes internally (best-effort, exit 0) — accepted localhost double GET.
    if not _relay_http_ok():
        sys.exit(f"obs: relay not responding on port {RELAY_PORT} — start it first "
                 "(refreshing against a dead relay loads an error page in OBS).")
    _refresh_obs_pages(force=True)


def obs_collection_cmd(rest):
    """`racecast obs collection` reports the active OBS scene collection; add `set` to
    switch OBS to the GT Racing Endurance collection. Best effort — OBS must be running
    with obs-websocket reachable. A mismatch exits non-zero so scripts/CI notice;
    `set` exits non-zero on failure so the Control Center job shows red."""
    import obs_ws
    expected = _active_obs_collection()
    if rest[:1] == ["set"] and len(rest) == 1:
        ok, note = obs_ws.set_scene_collection(name=expected)
        if not ok:
            sys.exit(f"obs: scene collection switch failed — {note}")
        print(f"obs: {note or 'scene collection switched to ' + expected}.")
        return
    if rest:
        sys.exit("usage: racecast obs collection [set]")
    status, note = obs_ws.get_scene_collection(expected=expected)
    if status is None:
        sys.exit(f"obs: scene collection check skipped — {note}")
    if status["match"]:
        print(f"obs: scene collection '{status['current']}' active — correct.")
        return
    if status["expected_present"]:
        sys.exit(f"obs: scene collection '{status['current']}' active — expected "
                 f"'{status['expected']}'. Run `racecast obs collection set`.")
    if status["renamed_variant"]:
        sys.exit(f"obs: scene collection '{status['current']}' active — looks renamed "
                 f"from '{status['expected']}'; switch manually in OBS.")
    sys.exit(f"obs: '{status['expected']}' collection not found in OBS — import it "
             f"with `racecast setup`.")


CHANNEL_TAB = "Channel"               # single-event platform (YT/Twitch)


def _gviz_csv_url(sheet_id, tab):
    from urllib.parse import quote
    return ("https://docs.google.com/spreadsheets/d/%s/gviz/tq?tqx=out:csv&sheet=%s"
            % (sheet_id, quote(tab)))


def _apply_stream_target(part, fetch=None, post=None, apply_obs=None,
                         refresh_env=None):
    """Resolve a Producer Part -> (platform from Channel tab, key from the
    get_stream_key webhook) and apply it to OBS via set_stream_service. Returns
    (ok, note); `note` NEVER contains the key. Seams (fetch/post/apply_obs/
    refresh_env) are test hooks — production uses http_util + obs_ws. The OBS
    apply is only reached after a key is obtained; the stopped-stream guard lives
    in set_stream_service."""
    import producer as prod
    import broadcast_chat as bc
    import stream_target as st
    import obs_ws
    (refresh_env or _apply_active_profile_env)()
    sheet_id = os.environ.get("RACECAST_SHEET_ID") or ""
    if not sheet_id:
        return False, "no SHEET_ID set for the active profile"
    push_url = os.environ.get("RACECAST_SHEET_PUSH_URL") or ""
    if not push_url:
        return False, ("no SHEET_PUSH_URL in the active profile — the stream-key "
                       "webhook is required")
    fetch = fetch or (lambda u: http_util.get_bytes(u, timeout=15)
                      .decode("utf-8", "replace"))
    post = post or (lambda u, o: http_util.post_json(u, o, timeout=15))
    apply_obs = apply_obs or obs_ws.set_stream_service
    try:
        prod_rows = prod.parse_producer_rows(fetch(_gviz_csv_url(sheet_id, PRODUCER_TAB)))
        chan_rows = bc.parse_channel_tab(fetch(_gviz_csv_url(sheet_id, CHANNEL_TAB)))
    except Exception as exc:                           # noqa: BLE001 — tolerant fetch
        return False, f"sheet fetch failed: {type(exc).__name__}"
    ref = st.resolve_part_ref(prod_rows, part)
    if not ref:
        return False, f"no stream-key reference for Part {part!r} (Producer tab)"
    platform = st.event_platform(chan_rows)
    if not platform:
        return False, "no channel/platform configured (Channel tab)"
    try:
        body = post(push_url, {"action": "get_stream_key", "ref": ref})
    except Exception as exc:                           # noqa: BLE001 — tolerant webhook
        return False, f"stream-key webhook failed: {type(exc).__name__}"
    key, err = st.parse_stream_key_response(body)
    if err:
        return False, err
    ok, note = apply_obs(platform, key)
    del key  # drop our last named reference to the key (do not carry it into the return path)
    if not ok:
        return False, note
    return True, f"stream target set for Part {part} on {platform} — stream key set"


def obs_stream_target_cmd(rest):
    """`racecast obs stream-target <part>`: set OBS's stream service+key for a
    Producer Part. Only works while OBS is NOT streaming; the key is fetched from
    the Sheet webhook and never printed."""
    if len(rest) != 1:
        sys.exit("usage: racecast obs stream-target <part>")
    ok, note = _apply_stream_target(rest[0])
    if not ok:
        sys.exit(f"obs: stream target not set — {note}")
    print(f"obs: {note} ✓")


class _BenchmarkRelay:
    """The two relay calls `obs benchmark` needs, over the loopback control port."""

    def __init__(self, base=None):
        self.base = base or f"http://127.0.0.1:{RELAY_PORT}"

    def status(self):
        return _relay_fetch_json(self.base + "/status")

    def set_quality(self, feed, tier):
        reply = _relay_post_json(f"{self.base}/feed/{feed}/quality", {"tier": tier})
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error") or f"relay refused tier {tier!r}")
        return reply


def _parse_benchmark_args(rest):
    """`obs benchmark` flags -> dict, or ValueError with the usage line."""
    import obs_benchmark as ob
    usage = ("usage: racecast obs benchmark [--window S] [--settle S] [--scene NAME] "
             "[--keep-recording] [--json]")
    opts = {"window_s": ob.DEFAULT_WINDOW_S, "settle_s": ob.DEFAULT_SETTLE_S,
            "scene": "Stint", "keep_recording": False, "json": False}
    it = iter(rest)
    for arg in it:
        if arg in ("--keep-recording", "--json"):
            opts[arg[2:].replace("-", "_")] = True
        elif arg in ("--window", "--settle"):
            try:
                val = int(next(it))
            except (StopIteration, ValueError):
                raise ValueError(usage) from None
            if val < (10 if arg == "--window" else 0):
                raise ValueError(f"{arg} must be at least {10 if arg == '--window' else 0} s")
            opts["window_s" if arg == "--window" else "settle_s"] = val
        elif arg == "--scene":
            opts["scene"] = next(it, "") or ""
            if not opts["scene"]:
                raise ValueError(usage)
        else:
            raise ValueError(usage)
    return opts


def obs_benchmark_cmd(rest):
    """`racecast obs benchmark` (#584): measure the on-air feed at FULL and at ROBUST
    with a recording running, then persist the result for `racecast preflight`.
    Refuses while OBS streams or records; restores scene, tier and recording after."""
    import obs_benchmark as ob
    import obs_ws
    try:
        opts = _parse_benchmark_args(rest)
    except ValueError as exc:
        sys.exit(str(exc))
    if not _relay_http_ok():
        sys.exit(f"obs: relay not responding on port {RELAY_PORT} — start it with a "
                 "live stint first.")
    session, note = obs_ws._connect("127.0.0.1", None, None, 10.0)
    if session is None:
        sys.exit(f"obs: benchmark needs OBS — {note}")
    relay_mod = _load_relay_module("relay/racecast-feeds.py")
    flags = {"youtube": (relay_mod.STREAMLINK_SERVE, relay_mod.STREAMLINK_SERVE_ROBUST),
             "twitch": (relay_mod.STREAMLINK_TWITCH, relay_mod.STREAMLINK_TWITCH_ROBUST)}
    total = 2 * (opts["window_s"] + opts["settle_s"])
    print(f"obs: benchmark on the on-air feed — about {total} s plus two reconnects. "
          "The feed reconnects twice; do not run this on air.")
    try:
        record = ob.run(_BenchmarkRelay(), session, _runtime_base_dir(), flags=flags,
                        scene=opts["scene"], window_s=opts["window_s"],
                        settle_s=opts["settle_s"], keep_recording=opts["keep_recording"],
                        progress=print)
    except ob.BenchmarkRefused as exc:
        sys.exit(f"obs: benchmark refused — {exc}")
    except Exception as exc:                          # noqa: BLE001 — operator-facing exit
        sys.exit(f"obs: benchmark failed — {exc}")
    finally:
        session.close()
    print(json.dumps(record, indent=2) if opts["json"] else ob.render(record, time.time()))


def resolve_device_selection(devices, token):
    """Map a user token to a device value. token: "" -> (None,None) (skip/leave);
    a 1-based index; a case-insensitive name substring; or an exact value. Returns
    (value, None) or (None, error)."""
    token = (token or "").strip()
    if not token:
        return None, None
    if token.isdigit():
        i = int(token)
        if 1 <= i <= len(devices):
            return devices[i - 1]["value"], None
        return None, f"index {i} out of range (1..{len(devices)})"
    for d in devices:                                   # exact value first
        if d["value"] == token:
            return d["value"], None
    matches = [d for d in devices if token.lower() in d.get("name", "").lower()]
    if len(matches) == 1:
        return matches[0]["value"], None
    if not matches:
        return None, f"no device matches {token!r}"
    return None, f"{token!r} is ambiguous ({len(matches)} matches)"


def _parse_device_scan_args(rest):
    """(webcam_token_or_None, capture_token_or_None, mic_token_or_None,
    tyres_token_or_None). Only --webcam/--capture/--mic/--tyres are recognized;
    anything else is a usage error."""
    webcam = None
    capture = None
    mic = None
    tyres = None
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--webcam" and i + 1 < len(rest):
            webcam = rest[i + 1]
            i += 2
        elif arg == "--capture" and i + 1 < len(rest):
            capture = rest[i + 1]
            i += 2
        elif arg == "--mic" and i + 1 < len(rest):
            mic = rest[i + 1]
            i += 2
        elif arg == "--tyres" and i + 1 < len(rest):
            tyres = rest[i + 1]
            i += 2
        else:
            raise ValueError(
                "usage: racecast device-scan [--webcam VAL] [--capture VAL] "
                "[--mic VAL] [--tyres VAL]")
    return webcam, capture, mic, tyres


def device_scan_cmd(rest):
    """`racecast device-scan [--webcam VAL] [--capture VAL] [--mic VAL] [--tyres VAL]`
    — enumerate the OBS video-capture devices and microphones available to this
    machine (via a throwaway-scene OBS probe, no collection needs importing), then
    write the operator's picks to the machine .env as RACECAST_WEBCAM/RACECAST_CAPTURE/
    RACECAST_MIC/RACECAST_TYRES_CAPTURE (#304, mic added in #307, tyres added in the
    solo Commentary HUD plan). VAL is a 1-based list index (into its own list — video
    indices for --webcam/--capture/--tyres, mic indices for --mic), a case-insensitive
    name substring, or an exact device value; blank/omitted leaves that slot untouched.
    --tyres resolves against the SAME enumerated video device list as --webcam/--capture.
    Leave it unset, or pick the capture card itself, when one card carries the game:
    setup-assets then crops the tyres/fuel widget from the capture card's source (#597);
    only a different device creates a second input. Without flags
    and on a TTY it prompts interactively; headless it just lists the devices and hints
    at the flags (never blocks on input())."""
    import obs_ws
    try:
        webcam_tok, capture_tok, mic_tok, tyres_tok = _parse_device_scan_args(rest)
    except ValueError as exc:
        sys.exit(str(exc))
    res = obs_ws.probe_device_options()
    devices, note = res["devices"], res["note"]
    mics, mic_note = res["mic"], res["mic_note"]
    if not devices and not mics:
        sys.exit(f"device-scan: {note or mic_note or 'no devices found'} — "
                 "start OBS with obs-websocket enabled, then retry.")
    if devices:
        print("Available video devices:")
        for i, d in enumerate(devices, start=1):
            print(f"  {i}. {d['name']}")
    if mics:
        print("Available microphones:")
        for i, d in enumerate(mics, start=1):
            print(f"  {i}. {d['name']}")
    if webcam_tok is None and capture_tok is None and mic_tok is None and tyres_tok is None:
        if not sys.stdin.isatty():
            print("racecast: pass --webcam VAL, --capture VAL, --mic VAL, and/or "
                 "--tyres VAL to select one (non-interactive session).")
            return None
        webcam_tok = input("Webcam [index/name, blank=skip]: ")
        capture_tok = input("Capture [index/name, blank=skip]: ")
        mic_tok = input("Mic [index/name, blank=skip]: ")
        tyres_tok = input("Tyres/fuel capture, only for a second card "
                          "[index/name, blank=skip; unset = crop of the capture card]: ")
    errors = []
    webcam_val, werr = resolve_device_selection(devices, webcam_tok or "")
    if werr:
        errors.append(f"webcam — {werr}")
    capture_val, cerr = resolve_device_selection(devices, capture_tok or "")
    if cerr:
        errors.append(f"capture — {cerr}")
    mic_val, mierr = resolve_device_selection(mics, mic_tok or "")
    if mierr:
        errors.append(f"mic — {mierr}")
    tyres_val, terr = resolve_device_selection(devices, tyres_tok or "")
    if terr:
        errors.append(f"tyres — {terr}")
    if errors:
        sys.exit("device-scan: " + "; ".join(errors))
    updates = {}
    if webcam_val is not None:
        updates["RACECAST_WEBCAM"] = webcam_val
    if capture_val is not None:
        updates["RACECAST_CAPTURE"] = capture_val
    if mic_val is not None:
        updates["RACECAST_MIC"] = mic_val
    if tyres_val is not None:
        updates["RACECAST_TYRES_CAPTURE"] = tyres_val
    if not updates:
        print("device-scan: nothing to write (no selection made).")
        return None
    res = env_upsert_data(updates)
    if not res.get("ok"):
        sys.exit(f"device-scan: {res.get('error')}")
    print(f"device-scan: wrote {', '.join(sorted(updates))} to .env.")
    print("Re-run `racecast setup` to bake these into the OBS collection.")
    return None


_PS_HOST_RE = re.compile(r"^[A-Za-z0-9.\-]{1,253}\Z")   # mirrors ui_ops._HOST_RE


def ps_ip_write_data(ip, path=None):
    """Validate + upsert the PS4/PS5 IP into the machine .env as
    RACECAST_GT7_PS_IP. `path` is a test seam (mirrors env_upsert_data's)."""
    ip = (ip or "").strip()
    if not _PS_HOST_RE.match(ip):
        return {"ok": False, "error": f"invalid host/IP: {ip!r}"}
    return env_upsert_data({"RACECAST_GT7_PS_IP": ip}, path=path)


def _parse_gt7_discover_args(rest):
    """Return (save, print_only, timeout, pick). Raises ValueError on bad input."""
    usage = ("usage: racecast gt7-discover [--save] [--print] "
             "[--timeout N] [--pick INDEX]")
    save = print_only = False
    timeout = 4.0
    pick = None
    it = iter(rest)
    for tok in it:
        if tok == "--save":
            save = True
        elif tok == "--print":
            print_only = True
        elif tok == "--timeout":
            try:
                timeout = float(next(it))
            except (StopIteration, ValueError):
                raise ValueError(f"--timeout needs a number. {usage}") from None
        elif tok == "--pick":
            try:
                pick = next(it)
            except StopIteration:
                raise ValueError(f"--pick needs an index. {usage}") from None
        else:
            raise ValueError(f"unknown flag {tok!r}. {usage}")
    return save, print_only, timeout, pick


def gt7_discover_cmd(rest):
    """`racecast gt7-discover [--save] [--print] [--timeout N] [--pick I]` — find the
    PS4/PS5 running GT7 on the LAN (reuses a running relay's latched console when up,
    else a broadcast scan) and write its IP to RACECAST_GT7_PS_IP. --save skips the
    prompt (for non-TTY); --print never writes; --pick selects when several are found."""
    try:
        save, print_only, timeout, pick = _parse_gt7_discover_args(rest)
    except ValueError as exc:
        sys.exit(str(exc))
    res = resolve_console(timeout=timeout)
    consoles, note = res["consoles"], res["note"]
    if res["from_relay"]:
        print(f"Using console latched by the running relay: {consoles[0]}")
    if not consoles:
        print(f"gt7-discover: {note}")
        return None
    if len(consoles) == 1:
        chosen = consoles[0]
        print(f"Found PlayStation: {chosen}")
    else:
        print("Found PlayStations:")
        for i, ip in enumerate(consoles, start=1):
            print(f"  {i}. {ip}")
        if pick is None:
            if not sys.stdin.isatty():
                print("gt7-discover: pass --pick INDEX to choose one "
                      "(non-interactive session).")
                return None
            pick = input(f"Pick [1-{len(consoles)}, blank=cancel]: ").strip()
        if not pick:
            print("gt7-discover: cancelled.")
            return None
        try:
            chosen = consoles[int(pick) - 1]
        except (ValueError, IndexError):
            sys.exit(f"gt7-discover: invalid selection {pick!r}.")
    if print_only:
        return None
    if not save and sys.stdin.isatty():
        ans = input(f"Save {chosen} to RACECAST_GT7_PS_IP? [Y/n]: ").strip().lower()
        if ans in ("n", "no"):
            print("gt7-discover: not saved.")
            return None
    elif not save:
        print(f"gt7-discover: found {chosen} (pass --save to write it to .env).")
        return None
    out = ps_ip_write_data(chosen)
    if not out.get("ok"):
        sys.exit(f"gt7-discover: {out.get('error')}")
    print(f"gt7-discover: wrote RACECAST_GT7_PS_IP={chosen} to .env.")
    print("Restart the relay (`racecast relay restart`) to apply.")
    return None


def _active_sheet_url():
    """The active league's Google-Sheet edit URL, or '' when no profile resolves
    or its SHEET_ID is unset. Tolerant: any resolution failure -> ''."""
    root = _env_base(IS_FROZEN, _real_executable(), HERE)
    sheet_id = _active_sheet_id(root, _runtime_base_dir(), _active_profile_name())
    return pcfg.sheet_edit_url(sheet_id)


def _sheet_url_or_exit():
    url = _active_sheet_url()
    if not url:
        sys.exit("sheet: no SHEET_ID set for the active profile — set it in "
                 "profiles/<name>/profile.env (racecast profile show).")
    return url


def sheet_url_cmd(_rest):
    """Print the active league's Google-Sheet edit URL (built from SHEET_ID)."""
    print(_sheet_url_or_exit())


def sheet_open_cmd(_rest):
    """Open the active league's Google Sheet in the default browser."""
    _open_url(_sheet_url_or_exit())


def _release_obs_feeds():
    """Make OBS (via obs-websocket) drop its connections to the just-killed
    feeds. Otherwise OBS keeps the half-dead connections and the kernel pins
    the feed ports in FIN_WAIT_1 until OBS restarts — the next preflight then
    warns "port in use". Must run AFTER the kill: the rebuild would reconnect
    to a still-live relay. Best effort: OBS closed, auth missing, anything —
    print one notice and keep going."""
    try:
        import obs_ws
        names, note = obs_ws.release_feed_inputs()
    except Exception as exc:                # a stop must never fail on this
        print(f"obs: feed release skipped ({exc}).")
        return
    if names:
        print(f"obs: released media inputs {', '.join(names)} "
              f"(frees the feed ports; they restart on scene activation).")
    elif note:
        print(f"obs: feed release skipped — {note}")

def relay_stop(rest):
    pid = sv.read_pid(_relay_pid_path())
    if not sv.pid_alive(pid):
        if os.path.exists(_relay_pid_path()):
            os.remove(_relay_pid_path())
        _clear_relay_profile_stamp()        # no relay -> no running-profile stamp
        print("relay is not running.")
        return
    if sv.stop_pid(pid, _relay_pid_path(), is_target=sv.pid_is_relay):
        _clear_relay_profile_stamp()
        print("relay stopped.")
        _release_obs_feeds()                # AFTER the kill — see docstring
    else:
        print("relay may still be running.")

def relay_restart(rest):
    relay_stop([])
    relay_start(rest)

def relay_status(rest):
    d = relay_status_data()
    extra = _relay_extra_text(d, _tailscale_ip()) if d["alive"] else ""
    print(sv.status_line("relay", d["pid"], d["alive"], extra))

def _logs_cmd(source_name, rest):
    """Shared `<service> logs` handler over the _log_sources() registry. Supports
    `--list` (archive tokens), `--archive TOKEN` (read one archive), and a live
    merged tail of the source's files (-f/--follow to follow)."""
    src = _log_sources().get(source_name)
    if src is None:
        print(f"(unknown log source: {source_name})"); return
    if "--list" in rest:
        toks = src["archives"]()
        print("\n".join(toks) if toks else "(no archives)")
        return
    if "--archive" in rest:
        i = rest.index("--archive")
        if i + 1 >= len(rest):
            print("(--archive needs a token — run with --list to see available ones)")
            return
        tok = rest[i + 1]
        text = src["read"](tok)
        print(text if text else f"(no archive '{tok}')")
        return
    sv.tail_merged(src["files"](), follow=("-f" in rest or "--follow" in rest))

def relay_logs(rest):      _logs_cmd("relay", rest)

def relay_run(rest):
    _ensure_active_console_secret()   # zero-config console: provision + inject the secret
    raise SystemExit(_run_script("relay/racecast-feeds.py",
                                 _relay_runtime_args() + rest))


def _companion():
    import companion_common as cc
    return cc

def _companion_cmds(cc):
    if sys.platform.startswith("win"):
        return cc.companion_control_commands(sys.platform, cc.find_companion_exe())
    if sys.platform == "darwin":
        return cc.companion_control_commands(sys.platform)
    import companion_linux as cl
    unit = cl.detect_unit()
    return cl.control_commands(unit) if unit else None

def _companion_unsupported_msg():
    if sys.platform.startswith("win"):
        return ("companion: Companion.exe not found. Set RACECAST_COMPANION_EXE in .env "
                "to its full path and retry.")
    if sys.platform == "darwin":
        return "companion: Companion control is unavailable on this macOS setup."
    return ("companion: no companion.service found (WSL/host or manual install) — "
            "run and bind Companion yourself.")

def _companion_running(cc):
    cmds = _companion_cmds(cc)
    if not cmds:
        return False
    # errors="replace": tasklist writes OEM-codepage console output (e.g. German
    # "ausgeführt" = 0x81), which is NOT decodable as the ANSI codepage Python
    # uses for text=True. The matched token (Companion.exe) is pure ASCII.
    # env=external_tool_env(): on Linux cmds["running"] is a bare `systemctl
    # is-active` (no sudo to reset the env), so the frozen binary must not leak
    # its _MEIPASS onto LD_LIBRARY_PATH — else systemctl loads our bundled
    # libcrypto, exits non-zero, and Companion is misreported as stopped.
    probe = subprocess.run(cmds["running"], capture_output=True, text=True,
                           errors="replace", env=sv.external_tool_env(),
                           **sv.no_window_kwargs())
    return cc.parse_running(sys.platform, probe.returncode, probe.stdout or "")

def _companion_start_linux(cc, cl, unit, rest):
    """Linux companion-pi: set the bind via the root helper, which restarts the
    service. No config.json editing (headless ignores it; the bind is the
    --admin-address flag injected via the systemd drop-in)."""
    if not os.path.exists(cl.HELPER_PATH):
        sys.exit("companion: control not set up yet — run `racecast companion "
                 "enable-control` once (installs the systemd bind helper + sudoers).")
    bind_arg = rest[0] if rest else "auto"
    ts = _tailscale_ip()
    ip = cc.desired_bind_ip(bind_arg, ts)
    if bind_arg == "auto" and not ts:
        print("companion: no Tailscale IP — binding 127.0.0.1 (this machine only).")
    if subprocess.run(["sudo", "-n", cl.HELPER_PATH, ip]).returncode != 0:
        sys.exit("companion: passwordless start failed. Run `racecast companion "
                 "enable-control`, or start manually: `sudo systemctl start companion`.")
    print(f"companion: started, admin/tablet bound to {ip}:8000.")
    print("  Admin GUI shares this port — restrict who reaches it with a Tailscale ACL.")


def _companion_stop_linux(cc, cl, unit):
    if not _companion_running(cc):
        print("companion is not running.")
        return
    if subprocess.run(["sudo", "-n", "systemctl", "stop", unit]).returncode != 0:
        sys.exit("companion: passwordless stop failed. Run `racecast companion "
                 "enable-control`, or stop manually: `sudo systemctl stop companion`.")
    print("companion stopped.")


def companion_start(rest):
    cc = _companion()
    import companion_linux as cl
    if not sys.platform.startswith("win") and sys.platform != "darwin":
        unit = cl.detect_unit()
        if unit:
            _companion_start_linux(cc, cl, unit, rest)
            return
    cmds = _companion_cmds(cc)
    if cmds is None:
        sys.exit(_companion_unsupported_msg())
    bind_arg = rest[0] if rest else "auto"
    cfg_path = cc.companion_config_path(sys.platform)
    if not os.path.exists(cfg_path):
        # First launch: Companion creates its config on startup — start it
        # plainly now, bind on the next run (the bind edit needs the file).
        print(f"companion: first launch (no config at {cfg_path} yet) — starting Companion as-is.")
        print("  When it is up, run `racecast companion restart` to bind it to the Tailscale IP.")
        # Same frozen-environment handling as the other GUI launches (#572):
        # Companion links the system libraries too.
        _gui_spawn(cmds["start"], None, _gui_child_env("companion")[0],
                   "companion")
        return
    with open(cfg_path, encoding="utf-8") as fh:
        text = fh.read()
    cfg = json.loads(text)
    current, port = cfg.get("bind_ip", "127.0.0.1"), cfg.get("http_port", 8000)
    ts = _tailscale_ip()
    desired = cc.desired_bind_ip(bind_arg, ts)
    if bind_arg == "auto" and not ts:
        print("companion: no Tailscale IP — the tablet will be reachable on this machine only.")
    plan = cc.plan_companion_action(current, desired, _companion_running(cc))
    if plan["stop_first"]:
        print("Stopping Companion to change its bind address…")
        subprocess.run(cmds["quit"], capture_output=True)
        for _ in range(30):
            if not _companion_running(cc):
                break
            time.sleep(0.5)
        else:
            sys.exit("companion: did not stop in time; aborting (config untouched).")
    if plan["edit"]:
        shutil.copy2(cfg_path, cfg_path + ".racecast-bak")
        tmp = cfg_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(cc.config_with_bind_ip(text, desired))
        os.replace(tmp, cfg_path)
        print(f"Set Companion bind_ip {current} -> {desired} (backup: {cfg_path}.racecast-bak)")
    if plan["start"]:
        print("Starting Companion…")
        # Same frozen-environment handling as the other GUI launches (#572):
        # Companion links the system libraries too.
        _gui_spawn(cmds["start"], None, _gui_child_env("companion")[0],
                   "companion")
    else:
        print(f"Companion already bound to {desired} and running.")
    host = desired if desired != "0.0.0.0" else (ts or "<this-machine-ip>")
    print(f"Companion buttons (tablet): http://{host}:{port}/tablet")
    print("  Admin GUI shares this port — restrict who reaches it with a Tailscale ACL.")
    return

def companion_stop(rest):
    cc = _companion()
    import companion_linux as cl
    if not sys.platform.startswith("win") and sys.platform != "darwin":
        unit = cl.detect_unit()
        if unit:
            _companion_stop_linux(cc, cl, unit)
            return
    cmds = _companion_cmds(cc)
    if cmds is None:
        sys.exit(_companion_unsupported_msg())
    if not _companion_running(cc):
        print("companion is not running.")
        return
    print("Stopping Companion…")
    subprocess.run(cmds["quit"], capture_output=True)
    for _ in range(30):
        if not _companion_running(cc):
            print("companion stopped.")
            return
        time.sleep(0.5)
    hint = ("taskkill /F /IM Companion.exe" if sys.platform.startswith("win")
            else "pkill -f Companion")
    print(f"companion may still be running. Force-quit: {hint}")
    return

def companion_restart(rest):
    companion_stop([])
    companion_start(rest)


def companion_enable_control(rest):
    import companion_linux as cl

    raise SystemExit(cl.enable_control())


def companion_status_payload(supported, running, cfg, why=""):
    """Pure: shape the companion status dict from probed facts."""
    url = None
    if running and cfg:
        url = f"http://{cfg.get('bind_ip', '127.0.0.1')}:{cfg.get('http_port', 8000)}/tablet"
    return {"supported": supported, "running": running, "url": url, "why": why}


def companion_status_data():
    """Probe Companion and shape the result (best effort — a broken probe
    reports as unsupported, never raises)."""
    try:
        cc = _companion()
        cmds = _companion_cmds(cc)
        if cmds is None:
            why = ("(Companion.exe not found — set RACECAST_COMPANION_EXE in .env)"
                   if sys.platform.startswith("win") else f"(manual on {sys.platform})")
            return companion_status_payload(False, False, None, why)
        running = _companion_running(cc)
        cfg = None
        if running:
            try:
                with open(cc.companion_config_path(sys.platform), encoding="utf-8") as fh:
                    cfg = json.load(fh)
            except Exception:
                cfg = None
        return companion_status_payload(True, running, cfg)
    except Exception as exc:
        return companion_status_payload(False, False, None, f"check failed: {exc}")


def companion_status(rest):
    d = companion_status_data()
    print(sv.status_line("companion", "?" if d["running"] else None,
                         d["running"], d["url"] or d["why"]))

def _companion_log_path():
    """Newest Companion log file, or None (no logs / unsupported platform)."""
    try:
        cc = _companion()
        logdir = os.path.join(os.path.dirname(cc.companion_config_path(sys.platform)), "logs")
        logs = sorted(glob.glob(os.path.join(logdir, "*")), key=os.path.getmtime)
        return logs[-1] if logs else None
    except Exception:
        return None


def companion_logs(rest):  _logs_cmd("companion", rest)
def obs_logs(rest):        _logs_cmd("obs", rest)
def tailscale_logs(rest):  _logs_cmd("tailscale", rest)


def _streams_static_dir():
    return os.path.join(_runtime_dir(), "static")

def streams_start(rest):
    _append_tailscale_snapshot()
    raise SystemExit(_run_script("scripts/start-streams.py",
                                 ["--state-dir", _streams_static_dir()] + rest))

def streams_stop(rest):
    # Static feeds serve the same OBS media sources as the relay (same ports),
    # so OBS must drop them here too — but only if feeds actually ran.
    had_feeds = bool(glob.glob(os.path.join(_streams_static_dir(), "feed_*.pid")))
    # No SystemExit: streams_restart() must continue into streams_start().
    _run_script("scripts/stop-streams.py",
                ["--state-dir", _streams_static_dir()] + rest)
    if had_feeds:
        _release_obs_feeds()                # AFTER the kill — see the helper

def streams_run_feed(rest):
    raise SystemExit(_run_script("scripts/loopstream.py", rest))

def streams_restart(rest):
    streams_stop([])
    streams_start(rest)

def streams_status_data(pidfiles=None):
    """Structured per-feed state of the static-streams mode."""
    if pidfiles is None:
        pidfiles = sorted(glob.glob(os.path.join(_streams_static_dir(), "feed_*.pid")))
    feeds = []
    for pf in pidfiles:
        pid = sv.read_pid(pf)
        feeds.append({"label": os.path.basename(pf)[len("feed_"):-len(".pid")],
                      "pid": pid, "alive": sv.pid_alive(pid)})
    return feeds


def streams_status(rest):
    feeds = streams_status_data()
    if not feeds:
        print(sv.status_line("streams", None, False, "(no feeds started)"))
        return
    for f in feeds:
        print(sv.status_line("streams:" + f["label"], f["pid"], f["alive"]))


def streams_logs(rest):    _logs_cmd("streams", rest)


def _http_url(host, port, path):
    return f"http://{host}:{port}{path}"

# The URL openers we will spawn ourselves, in preference order. `gio` takes the
# URL after an `open` verb; the others take it directly.
URL_OPENERS = ("xdg-open", "gio", "kde-open", "gnome-open")
URL_OPENER_SCHEMES = ("http://", "https://")
URL_OPENER_GRACE_S = 1.5      # long enough to catch a dynamic-linker death


def url_opener_argv(platform, url, which=shutil.which):
    """argv for the OS's URL opener, or None when there is nothing to spawn.

    A frozen build cannot use `webbrowser.open()`: it starts the browser as a
    child of THIS process, which inherits the PyInstaller extraction dir on
    LD_LIBRARY_PATH and dies in the dynamic linker before main() — the #572
    failure class, at a site #573 did not cover. Measured on the broadcast box:
    under `LD_LIBRARY_PATH=/tmp/_MEI…` even `/bin/bash` dies with "undefined
    symbol: rl_print_keybinding", so the browser never starts, and `webbrowser`
    sends the child's stderr to devnull so nothing says why. Spawning the opener
    ourselves is what lets us hand it a de-PyInstaller environment.

    Only ever for a real http(s) URL: `open` and `gio open` launch a FILE with
    its default application, so a caller handing over a path — or a `-`-leading
    string an opener would read as a flag — must never reach them. Windows has
    no library-path problem, so it returns None and keeps `webbrowser`, as does
    a box with none of these openers installed.
    """
    if not url.startswith(URL_OPENER_SCHEMES):
        return None
    if platform.startswith("win"):
        return None
    if platform == "darwin":
        return [which("open") or "open", url]
    for tool in URL_OPENERS:
        path = which(tool)
        if path:
            # `--` ends option parsing before the URL, as CPython's webbrowser does.
            return [path, "open", "--", url] if tool == "gio" else [path, url]
    return None


def _spawn_url_opener(argv, env, popen=None, sleep=None):
    """Run the URL opener; return its complaint, or "" when it worked.

    NOT `_gui_spawn`, deliberately. That helper reads "gone after the grace
    period" as death, which is right for a GUI app and wrong here: a URL opener
    HANDS THE URL OVER AND EXITS, so every success would be reported as a death
    and would trigger a second browser. The exit CODE is the signal instead.

    stderr is captured for the same reason `_gui_spawn` captures it — a silent
    dynamic-linker death on stderr=DEVNULL is exactly what kept #572 invisible,
    and this function exists because of that bug. Raises OSError like Popen.
    """
    popen = subprocess.Popen if popen is None else popen
    sleep = time.sleep if sleep is None else sleep
    with tempfile.TemporaryFile() as fh:
        proc = popen(argv, env=env, stdout=subprocess.DEVNULL, stderr=fh,
                     **sv.spawn_kwargs(os.name))
        sleep(URL_OPENER_GRACE_S)
        rc = None if proc is None else proc.poll()
        if rc in (None, 0):
            return ""              # still running, or handed the URL over cleanly
        fh.seek(0)
        text = fh.read().decode("utf-8", "replace")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:3]
    return " / ".join(lines)[:400] or f"exit {rc}"


def _open_url(url, which=shutil.which, platform=None, popen=None, sleep=None,
              browser=None):
    """Open `url` in the producer's browser. `which`/`platform`/`popen`/`sleep`/
    `browser` are test seams (the `_gui_spawn` pattern), so a unit test never has
    to patch the stdlib modules this reaches for."""
    print(f"Opening {url}")
    env = sv.external_tool_env()            # None off the frozen binary
    plat = sys.platform if platform is None else platform
    argv = url_opener_argv(plat, url, which=which) if env is not None else None
    if argv is not None:
        try:
            note = _spawn_url_opener(argv, env, popen=popen, sleep=sleep)
        except OSError as exc:
            note = str(exc)
        if not note:
            return
        print(f"  {argv[0]}: {note}")
        # Last resort only. webbrowser starts the browser as OUR child, which is
        # the very thing that fails on a frozen build — so promise nothing.
        print(f"  falling back to the default browser — if no window appears, "
              f"open {url} yourself")
    (browser or webbrowser.open)(url)


def relay_open_panel(rest):
    _open_url(_http_url("127.0.0.1", RELAY_PORT, "/panel"))

def relay_open_hud(rest):
    _open_url(_http_url("127.0.0.1", RELAY_PORT, "/hud"))

def relay_open_status(rest):
    _open_url(_http_url("127.0.0.1", RELAY_PORT, "/status"))

def _companion_open(path):
    # Companion listens on its bind_ip (the Tailscale IP), not 127.0.0.1 — open that.
    cc = _companion()
    cfg_path = cc.companion_config_path(sys.platform)
    if not os.path.exists(cfg_path):
        sys.exit(f"companion: config not found at {cfg_path}. Launch Companion once, then retry.")
    with open(cfg_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    _open_url(_http_url(cfg.get("bind_ip", "127.0.0.1"), cfg.get("http_port", 8000), path))

def companion_open_buttons(rest):
    _companion_open("/tablet")

def companion_open_admin(rest):
    _companion_open("/")


def _stint_args(rest):
    """Extract + validate a --stint flag ("--stint 4" or "--stint=4") from an
    argv. Returns the fragment to forward to the relay launch; exits on an
    invalid value (fail fast BEFORE a detached daemon is spawned — its own
    error would only land in the log file)."""
    for i, tok in enumerate(rest):
        val = None
        if tok == "--stint" and i + 1 < len(rest):
            val = rest[i + 1]
        elif tok.startswith("--stint="):
            val = tok.split("=", 1)[1]
        if val is not None:
            if not val.isdigit() or int(val) < 1:
                sys.exit(f"--stint must be a 1-based stint number (got {val!r}).")
            return ["--stint", val]
    return []


def _qualifying_args(rest):
    """['--qualifying'] when the flag is present in argv, else [] — forwarded to
    the relay launch so 'event start --qualifying' brings the stack up in
    qualifying mode (Feed A serves the Qualifying tab). Switch live afterwards via
    the panel / /mode endpoints."""
    return ["--qualifying"] if "--qualifying" in rest else []


def _title_args(rest):
    """['--event-title', VALUE] when `event start` was given --title (or --title=),
    else [] — forwarded to the relay launch so 'event start --title "…"' brings the
    stack up with that free-text event title and persists it (#207). Free text: no
    validation beyond presence (the relay sanitizes); an explicit empty value clears
    the title. A bare '--title' whose next token is another flag is NOT consumed."""
    for i, tok in enumerate(rest):
        if tok.startswith("--title="):
            return ["--event-title", tok.split("=", 1)[1]]
        if tok == "--title" and i + 1 < len(rest) and not rest[i + 1].startswith("--"):
            return ["--event-title", rest[i + 1]]
    return []


def _event_modules():
    """event/preflight are plain sibling modules of services (scripts/ is on
    sys.path; frozen: hidden-imports in tools/build-binary.py)."""
    import event as ev
    import preflight as pf
    return ev, pf


def _load_relay_module(rel):
    """Load a relay script (hyphenated filename) as a module, repo + package +
    frozen alike — module-level code only defines functions, no side effects."""
    import importlib.util
    path = resource_path(rel)
    name = os.path.splitext(os.path.basename(path))[0].replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _asset_dirs():
    """Where `racecast graphics`/`racecast media` write: always the active profile's
    runtime dir (the one-shot injection points --out there in every run mode --
    see _oneshot_extra)."""
    return (os.path.join(_runtime_dir(), "graphics"),
            os.path.join(_runtime_dir(), "media"))


def _asset_state(ev):
    """Sheet-driven asset facts shared by `racecast event status` and `racecast init`:
    (g_dir, m_dir, missing_g, missing_m). missing_* follow ev.check_assets()
    semantics and are None when the sheet could not be read (fetch_assets_rows
    absorbs fetch errors into None — it never raises). Only module-load /
    directory-resolution failures raise — callers classify/fall back.

    Note: the CLI injects RACECAST_SHEET_ID from the active profile before this
    runs (get-graphics' load_dotenv still fills machine vars from .env)."""
    gg = _load_relay_module("relay/get-graphics.py")
    gm = _load_relay_module("relay/get-media.py")
    gg.load_dotenv(os.path.dirname(os.path.abspath(gg.__file__)))
    g_dir, m_dir = _asset_dirs()
    rows = ev.fetch_assets_rows(gg, os.environ.get("RACECAST_SHEET_ID"))
    missing_g = ev.check_assets(ev.required_graphics(gg, rows), g_dir) if rows else None
    missing_m = ev.check_assets(ev.required_media(gm, rows), m_dir) if rows else None
    return g_dir, m_dir, missing_g, missing_m


def _event_sections(ev, pf):
    """Gather all event-day facts and classify them into report sections."""
    # Apps
    import discord_web
    obs_running = ev.app_running("obs")
    discord_web_mode = discord_web.use_web(sys.platform, os.environ)
    apps = [ev.classify_app("obs", obs_running),
            ev.classify_app("discord", ev.app_running("discord"), web=discord_web_mode),
            ev.classify_tailscale(_tailscale_ip())]
    # Scene-collection line — only probe obs-websocket when OBS is actually up
    # (no point paying the connect timeout otherwise). Best effort: a broken
    # probe must never traceback the readiness report.
    if obs_running:
        try:
            import obs_ws
            status, note = obs_ws.get_scene_collection(expected=_active_obs_collection())
            apps.append(ev.classify_scene_collection(status, note))
        except Exception as exc:                     # noqa: BLE001 — best effort
            apps.append(ev.Result(ev.WARN, "OBS scene collection",
                                  f"check failed: {exc}"))
    # Services
    pid = sv.read_pid(_relay_pid_path())
    alive = sv.pid_alive(pid)
    services = [ev.classify_relay(alive, _relay_http_ok() if alive else False, RELAY_PORT)]
    try:
        cc = _companion()
        supported = _companion_cmds(cc) is not None
        services.append(ev.classify_companion(
            _companion_running(cc) if supported else False, supported,
            "" if supported else _companion_unsupported_msg()))
    except Exception as exc:
        services.append(ev.Result(ev.WARN, "Companion", f"check failed: {exc}"))
    # Assets — a broken probe must never traceback the report (spec: error behaviour).
    assets = [pf.cookies_status(_cookies_path())]
    try:
        g_dir, m_dir, missing_g, missing_m = _asset_state(ev)
        assets += [ev.classify_assets("Graphics", missing_g, ev.local_count(g_dir),
                                      ev.FAIL, "run `racecast graphics`"),
                   ev.classify_assets("Media", missing_m, ev.local_count(m_dir),
                                      ev.WARN, "run `racecast media`")]
    except Exception as exc:
        assets.append(ev.Result(ev.WARN, "Graphics/Media", f"check failed: {exc}"))
    config = [ev.classify_env(os.environ.get("RACECAST_SHEET_ID"),
                              os.environ.get("RACECAST_SHEET_PUSH_URL"))]
    return [("Apps", apps), ("Services", services), ("Assets", assets),
            ("Config", config), ("Go-live reminders", [ev.GO_LIVE_REMINDER])]


def event_status(rest):
    ev, pf = _event_modules()
    color = pf.enable_color("--no-color" in rest)
    raise SystemExit(pf.report(_event_sections(ev, pf), color))


def takeover_plan(status, stint_override=None, qualifying_flag=False):
    """Derive event-start params for a producer takeover from A's /status (a dict,
    or None when A was unreachable) plus operator overrides. Pure. Returns
    {stint, qualifying, source}: --stint always wins (source 'override'); else A's
    live block (source 'relay'); else — no override and A unreachable / an older
    relay without a live block — stint is None (source 'sheet') and the CLI asks
    for --stint rather than silently starting at stint 1 mid-race. --qualifying
    forces qualifying regardless of A."""
    if stint_override is not None:
        return {"stint": stint_override, "qualifying": bool(qualifying_flag),
                "source": "override"}
    live = status.get("live") if isinstance(status, dict) else None
    if isinstance(live, dict) and live.get("stint") is not None:
        return {"stint": live["stint"],
                "qualifying": bool(qualifying_flag) or live.get("mode") == "qualifying",
                "source": "relay"}
    return {"stint": None, "qualifying": bool(qualifying_flag), "source": "sheet"}


def league_guard(a_sheet_id, b_sheet_id, force):
    """Block a takeover into the wrong league: a message when both league ids
    (SHEET_ID) are known, differ, and not force; else None (match, forced, or one
    id unknown -> cannot verify, allow). Pure."""
    if force or not a_sheet_id or not b_sheet_id or a_sheet_id == b_sheet_id:
        return None
    return (f"league mismatch: producer A is league {a_sheet_id}, but your active "
            f"profile is league {b_sheet_id} — wrong profile? re-run with --force "
            f"to take over anyway")


def _takeover_event_title(status):
    """Producer A's on-air event title (#207) to adopt at takeover, or None to leave
    the local title untouched — A unreachable (status None) or an older relay whose
    /status omits the field. An empty string is a valid value (A has no title -> clear
    ours to match). Pure."""
    if not isinstance(status, dict) or "event_title" not in status:
        return None
    title = status.get("event_title")
    return title if isinstance(title, str) else None


def _announce_takeover(status, plan, a_title):
    """Announce a producer takeover (#317): a Discord push + a Health-Monitor marker
    on THIS machine, both naming the producers (B = us via _resolve_producer_name,
    A = the outgoing one from /status). Fully best-effort — any failure prints one
    note and returns, never blocking the bring-up. Own function so the takeover
    tests stub its network/DB I/O."""
    try:
        b_name = _resolve_producer_name()
        a_name = _takeover_producer(status)
        mode_label = "qualifying mode" if plan["qualifying"] else "race mode"
        webhook, _league = _active_discord_webhook()
        if webhook:
            _post_discord_webhook(webhook, notify.takeover_discord_payload(
                b_name, a_name, plan["stint"], mode_label,
                event_title=(a_title or "")))
        conn = hsmod.open_db(_health_db_path()); hsmod.migrate(conn)
        hsmod.record_event(conn, time.time(), "takeover",
                           label=(f"{b_name} took over from {a_name}" if a_name
                                  else f"{b_name} took over"),
                           producer=b_name,
                           metadata={"from": a_name, "stint": plan["stint"]})
        conn.close()
    except Exception as exc:  # noqa: BLE001 — best-effort, never blocks the bring-up
        print(f"note: takeover announcement failed ({type(exc).__name__}) — continuing.")


def _event_gate_results(ev, pf):
    """The static preconditions `racecast event start` cannot fix by launching
    services: the active league's .env/SHEET_ID, the broadcast graphics/media,
    and the YouTube cookies. (Relay/OBS/Companion/Tailscale are exactly what
    event start brings up, so they are deliberately excluded — gating on them
    would abort every bring-up.) Mirrors the classifiers used in
    _event_sections so the gate and the readiness report agree."""
    results = [ev.classify_env(os.environ.get("RACECAST_SHEET_ID"),
                               os.environ.get("RACECAST_SHEET_PUSH_URL")),
               pf.cookies_status(_cookies_path())]
    try:
        g_dir, m_dir, missing_g, missing_m = _asset_state(ev)
        results += [ev.classify_assets("Graphics", missing_g, ev.local_count(g_dir),
                                       ev.FAIL, "run `racecast graphics`"),
                    ev.classify_assets("Media", missing_m, ev.local_count(m_dir),
                                       ev.WARN, "run `racecast media`")]
    except Exception as exc:                          # noqa: BLE001 — best effort
        results.append(ev.Result(ev.WARN, "Graphics/Media", f"check failed: {exc}"))
    return results


GUI_LAUNCH_GRACE_S = 1.5      # long enough to catch a dynamic-linker death


def _gui_child_env(app, ev=None):
    """Environment for spawning a GUI app: the de-PyInstaller base, plus the
    headless session overrides on top.

    A GUI app links the SYSTEM libraries, so it must not inherit the frozen
    binary's LD_LIBRARY_PATH — it points at our extracted _MEI bundle and the app
    loads our libssl instead, dying with "version `OPENSSL_x.y.z' not found"
    (#572). `external_tool_env()` returns None off the frozen binary, so a source
    run is unchanged. Returns (env, overrides).
    """
    ev = _event_modules()[0] if ev is None else ev
    overrides = ev.launch_env(app, sys.platform)
    env = sv.external_tool_env()
    if overrides:
        env = dict(os.environ if env is None else env)
        env.update(overrides)
    return env, overrides


def _gui_spawn(argv, cwd, env, app, popen=None, sleep=None):
    """Launch a GUI app and surface an IMMEDIATE death in its own words.

    stderr used to go straight to DEVNULL, so an app that died in the dynamic
    linker left no trace on any surface and the bring-up could only report
    "still not up" — which is why #572 stayed invisible. Capture into a temp
    file and, if the process is already gone a moment later, print what it said.
    A survivor's buffer is dropped: a live GUI app's stderr is not ours to keep.
    Raises OSError like Popen; returns the note it printed ("" when the app is
    still alive).
    """
    popen = subprocess.Popen if popen is None else popen
    sleep = time.sleep if sleep is None else sleep
    with tempfile.TemporaryFile() as fh:
        proc = popen(argv, cwd=cwd, env=env, stdout=subprocess.DEVNULL,
                     stderr=fh, **sv.spawn_kwargs(os.name))
        sleep(GUI_LAUNCH_GRACE_S)
        if proc is None or proc.poll() is None:
            return ""                    # still running — nothing to report
        fh.seek(0)
        text = fh.read().decode("utf-8", "replace")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:3]
    note = " / ".join(lines)[:400]
    print(f"{app}: exited immediately"
          + (f" — {note}" if note else " (no output)."))
    return note


def _event_launch(ev, app, popen=None, sleep=None):
    """Best-effort GUI-app launch: report and continue on every failure path.
    Returns True iff a launch was actually attempted."""
    import install_apps
    if not install_apps.app_present(app, sys.platform):
        print(f"{app}: not installed — run `racecast install-apps`.")
        return False
    cmd = ev.launch_command(app, sys.platform)
    if cmd is None:
        hint = ("run `sudo tailscale up`" if app == "tailscale"
                else "launch it manually")
        print(f"{app}: cannot launch automatically — {hint}.")
        return False
    argv, cwd = cmd
    child_env, overrides = _gui_child_env(app, ev)
    if overrides:
        print("{}: targeting the autologin session ({}).".format(
            app, ", ".join("{}={}".format(k, v) for k, v in sorted(overrides.items()))))
    print(f"{app}: launching…")
    try:
        _gui_spawn(argv, cwd, child_env, app, popen=popen, sleep=sleep)
    except OSError as exc:
        print(f"{app}: launch failed ({exc}).")
        return False
    return True


def _tailscale_login_hint(platform=None):
    """How to complete a first-time Tailscale sign-in, per OS. Linux has no GUI
    app: the one-time auth is `sudo tailscale up`, which prints a
    https://login.tailscale.com/… URL to open in a browser. macOS/Windows have
    the GUI app that drives the login."""
    platform = sys.platform if platform is None else platform
    if platform.startswith("linux"):
        return ("run `sudo tailscale up` in a terminal, then open the printed "
                "https://login.tailscale.com/… URL in a browser to sign in")
    return "open the Tailscale app and sign in"


def _tailscale_operator_hint(verb, platform=None):
    """Suffix for a failed Linux `tailscale up/down`: those need root to write
    prefs ("Access denied: prefs write access denied"). The one-time fix that
    ALSO makes the Control Center Connect/Disconnect buttons work without sudo is
    `sudo tailscale set --operator=$USER`. Empty off Linux (no suffix needed)."""
    platform = sys.platform if platform is None else platform
    if not platform.startswith("linux"):
        return ""
    return (f" — Linux needs root for this. One-time fix so up/down (and the "
            f"Control Center buttons) work WITHOUT sudo: `sudo tailscale set "
            f"--operator=$USER`. Or run `sudo tailscale {verb}` now.")


def _tailscale_connect(ev=None):
    """Best-effort connect: argument-less `tailscale up` keeps all settings
    ("the opposite of tailscale down"). Launches the app first when no backend
    answers (macOS: the backend only lives while the app runs); never runs `up`
    in NeedsLogin — that would trigger the interactive browser login. Shared by
    `racecast tailscale up` and `racecast event start`; returns the tailnet IP or None."""
    import tailscale as ts
    binary, state, ip = ts.tailscale_backend()
    if ts.plan_tailscale_up(state) == "launch-app":
        ev = ev or _event_modules()[0]
        if not _event_launch(ev, "tailscale"):
            return None  # _event_launch already printed the actionable hint
        for _ in range(20):  # ~10 s for the backend to come up
            time.sleep(0.5)
            binary, state, ip = ts.tailscale_backend()
            if state:
                break
    action = ts.plan_tailscale_up(state)
    if action == "connected":
        print(f"tailscale: already connected ({ip or 'no IPv4 yet'}).")
        return ip
    if action == "needs-login":
        print(f"tailscale: logged out — {_tailscale_login_hint()}.")
        return None
    if action == "launch-app":  # the backend never came up
        print("tailscale: not running — start the Tailscale app manually.")
        return None
    ok, detail = ts.tailscale_up(binary)
    if not ok:
        print(f"tailscale: `up` failed: {detail}{_tailscale_operator_hint('up')}")
        return None
    for _ in range(20):  # ~10 s for the tailnet to come up
        ip = ts.detect_tailscale_ip()
        if ip:
            break
        time.sleep(0.5)
    print(f"tailscale: connected ({ip})." if ip
          else "tailscale: `up` succeeded but no tailnet IP yet.")
    return ip


def tailscale_up_cmd(_rest):
    raise SystemExit(0 if _tailscale_connect() else 1)


def tailscale_down_cmd(_rest):
    import tailscale as ts
    binary, state, _ip = ts.tailscale_backend()
    if state != "Running":
        print("tailscale: not connected.")
        return
    ok, detail = ts.tailscale_down(binary)
    if not ok:
        sys.exit(f"tailscale: `down` failed: {detail}{_tailscale_operator_hint('down')}")
    print("tailscale: disconnected.")


def tailscale_status_cmd(_rest):
    import tailscale as ts
    _binary, state, ip = ts.tailscale_backend()
    if state is None:
        print("Tailscale: backend not running — `racecast tailscale up` starts and connects it.")
    elif state == "Running":
        print(f"Tailscale: connected ({ip or 'no IPv4 yet'}).")
    elif state in ("NeedsLogin", "NeedsMachineAuth"):
        print(f"Tailscale: {state} — {_tailscale_login_hint()}.")
    else:
        print(f"Tailscale: {state} — run `racecast tailscale up` to connect.")
    _append_tailscale_snapshot()


OBS_READY_TIMEOUT_S = 30      # OBS loading a big scene collection is not quick


def _wait_for_obs_ready(timeout=OBS_READY_TIMEOUT_S):
    """Give a just-launched OBS time to answer obs-websocket. Best effort: a
    timeout is a note, never a blocked bring-up (OBS may simply not be there)."""
    try:
        import obs_ws
        ok, note = obs_ws.wait_until_ready(timeout=timeout)
    except Exception as exc:                         # noqa: BLE001 — best effort
        print(f"obs: readiness check skipped ({exc}).")
        return
    if not ok:
        print(f"obs: not answering obs-websocket after {timeout:.0f}s — "
              f"the scene collection, page refresh and Standby switch may be "
              f"skipped ({note}).")


def _check_scene_collection():
    """At `event start`, align OBS's scene collection with the active profile.
    Default-on auto-switch (RACECAST_OBS_COLLECTION_SWITCH=0 -> warn-only, the old
    behaviour). Best-effort: never blocks bring-up. Safe to automate here because OBS
    refuses a collection switch while an output is active (set_scene_collection returns
    (False, ...)), and no output is active during bring-up. The manual fallback stays
    `racecast obs collection set` / the Control Center OBS row."""
    try:
        import obs_ws
        status, note = obs_ws.get_scene_collection(expected=_active_obs_collection())
    except Exception as exc:                         # noqa: BLE001 — best effort
        print(f"obs: scene collection check skipped ({exc}).")
        return
    action, detail = obs_ws.scene_collection_action(
        status, note, _collection_switch_enabled())
    if action == "skip":
        print(f"obs: scene collection check skipped — {detail}.")
    elif action == "ok":
        print(f"obs: scene collection '{detail}' active — correct.")
    elif action == "switch":
        ok, snote = obs_ws.set_scene_collection(name=detail)
        if ok:
            print(f"obs: scene collection switched to '{detail}' "
                  f"(was '{status['current']}').")
        else:
            print(f"obs: WARNING — could not switch to scene collection '{detail}' — "
                  f"{snote}. Switch with `racecast obs collection set` (or the OBS row "
                  f"in the Control Center) before going live.")
    elif action == "warn_present":
        print(f"obs: WARNING — scene collection '{detail['current']}' active, expected "
              f"'{detail['expected']}'. Switch with `racecast obs collection set` (or the "
              f"OBS row in the Control Center) before going live.")
    else:  # warn_absent
        print(f"obs: WARNING — scene collection '{detail['current']}' active, expected "
              f"'{detail['expected']}' not found in OBS — import it with `racecast setup` "
              f"before going live.")


def _switch_to_standby():
    """After bring-up, park OBS on the Standby scene (Director Guide: start on
    Standby, then Start Streaming). Default-on; RACECAST_OBS_STANDBY_ON_START=0
    disables. Best-effort: never blocks bring-up, and NEVER cuts a live program
    (switch_to_scene_if_idle skips when OBS output is active)."""
    if not _standby_on_start_enabled():
        return
    try:
        import obs_ws
    except Exception as exc:                          # noqa: BLE001 — best effort
        print(f"obs: standby switch skipped ({exc}).")
        return
    action, note = obs_ws.switch_to_scene_if_idle(STANDBY_SCENE)
    if action == "switched":
        print(f"obs: switched to the '{STANDBY_SCENE}' scene — ready to Start Streaming.")
    elif action == "live":
        print(f"obs: '{STANDBY_SCENE}' switch skipped — {note}.")
    else:  # error
        print(f"obs: '{STANDBY_SCENE}' switch skipped — {note}. "
              f"Switch to Standby manually before going live.")


def event_start(rest, _autojoin=True, _new_session=True):
    """Bring the event stack up. Order matters: Tailscale first (the Companion
    bind needs its IP), relay before OBS (the HUD browser source then connects
    against a live relay on OBS's first load). Every step is best effort.

    `_autojoin` gates the Discord voice auto-join (best-effort) at the end of
    the success path: True for the top-level `event start` verb, False when
    `event_takeover` reuses this function internally (auto-join there would
    fire twice across a handover and is not part of the takeover contract).

    `_new_session` gates the report session-window reset: True marks a fresh
    broadcast (record a new session start), False keeps the existing window.
    `event_takeover` passes False so a handover continues A's report window on B
    rather than resetting it; a mid-event recovery restart (--stint/--part) keeps
    it too (see `_is_continuation_start`)."""
    ev, pf = _event_modules()
    # 0. Pre-flight gate — refuse to bring the stack up when a static
    # precondition is broken (missing SHEET_ID, missing graphics): those never
    # self-heal and would otherwise surface as black sources / unresolved feeds
    # mid-broadcast. WARNs (stale cookies, missing media) do not block. `--force`
    # skips the gate for a deliberate degraded start.
    if "--force" not in rest:
        blockers = ev.gate_blockers(_event_gate_results(ev, pf))
        if blockers:
            color = pf.enable_color("--no-color" in rest)
            print("Pre-flight gate: cannot start the event — these must be fixed "
                  "first (or re-run with --force to start anyway):")
            for r in blockers:
                print(pf.fmt_result(r, color))
            raise SystemExit(1)
    # 1. Tailscale — connect a stopped backend; launch the app when needed.
    if _tailscale_connect(ev) is None:
        print("tailscale: continuing local-only (OBS keeps working).")
    # 2. Discord
    if ev.app_running("discord"):
        print("discord: already running.")
    else:
        _event_launch(ev, "discord")
    # 3. Relay (before OBS — see docstring). A takeover bring-up forwards
    # --stint so the feeds start at the stint that is on air right now;
    # --qualifying brings the stack up in qualifying mode (Feed A on the
    # Qualifying tab); --title sets the free-text event title (#207). Reset the
    # broadcast-Part pointer to Part 1 (or --part N for a mid-event recovery
    # restart) BEFORE the relay starts, so its PartStore comes up on the right
    # Part.
    _write_part_reset(_part_index(rest))
    # Mark THIS event's start so the post-event report window begins here — a quick
    # event restart within report_build.SESSION_GAP_S must not merge the previous
    # event's health samples into this report (the window START used to never reset).
    # A fresh broadcast ONLY: a takeover (_new_session=False) or a mid-event recovery
    # restart (--stint/--part) keeps the existing window so the report stays continuous.
    if _new_session and not _is_continuation_start(rest):
        _write_session_start()
    relay_start(_stint_args(rest) + _qualifying_args(rest) + _title_args(rest))
    if _qualifying_args(rest):   # verify the relay actually came up in qualifying mode
        _mm = qualifying_mode_mismatch_note(True, _relay_mode())
        if _mm:
            print("WARNING: " + _mm)
    # 4. OBS
    if ev.app_running("obs"):
        print("obs: already running.")
    else:
        _event_launch(ev, "obs")
    # 5. Companion (companion_start sys.exits on unsupported setups — keep going)
    try:
        companion_start(["auto"])
    except SystemExit as exc:
        print(exc.code if isinstance(exc.code, str)
              else f"companion: start failed (exit {exc.code}).")
    # Give the launches time to settle: OBS and the relay take a few seconds,
    # and a too-early report shows FAILs that are already resolving. Only the
    # dynamic probes are waited on — static problems never self-heal.
    import install_apps
    probes = {"relay": _relay_http_ok}
    if install_apps.app_present("obs", sys.platform):
        probes["obs"] = lambda: ev.app_running("obs")
    # Companion is an Electron app — its HTTP server takes a few seconds to
    # come up. Wait for it too, or the readiness report below races the launch
    # and prints a spurious "Companion: not running" right after starting it.
    cc = _companion()
    if _companion_cmds(cc) is not None:   # controllable on this OS (not Linux)
        probes["companion"] = lambda: _companion_running(cc)
    print("\nWaiting for the launched services to come up (max 60 s)…")
    for name, up in sorted(ev.wait_until_up(probes).items()):
        print(f"  {name}: {'up' if up else 'still not up — see the report below'}")
    # Funnel (opt-OUT via RACECAST_FUNNEL=false): publish /console publicly once
    # the relay is up. On by default — the Funnel is the preferred produce path.
    # Best-effort — a funnel failure (e.g. missing nodeAttr) must never abort the
    # event; print one concise line.
    if _funnel_auto_enabled():
        try:
            funnel_cmd(["on"])
        except SystemExit as exc:
            msg = exc.code if isinstance(exc.code, str) else "failed"
            print("funnel: skipped — " + msg.splitlines()[0])
    # OBS may not have been running when relay_start's refresh hook fired
    # (event start launches OBS AFTER the relay) — retry now that both sides
    # are up. Forced (not hash-gated): a re-run / takeover where the served
    # page bytes are unchanged must still clear OBS's cached browser sources,
    # otherwise stale HUD/overlay pages survive the bring-up. Also guarantees
    # _sync_pov_transform runs (it is nested inside the refresh).
    # OBS accepts the WebSocket several seconds before it can answer a request
    # (obs-websocket replies 207 "not ready" meanwhile). Running the three steps
    # below inside that window skips all three silently — which is exactly what
    # happened once launching OBS actually started working (#572).
    _wait_for_obs_ready()
    _check_scene_collection()
    _refresh_obs_pages(force=True)
    _switch_to_standby()          # park OBS on Standby, ready to Start Streaming
    print()
    for line in ev.director_urls(_tailscale_ip(), _companion_tablet_port(),
                                 relay_port=RELAY_PORT):
        print(line)
    # Discord voice auto-join (default on, RACECAST_DISCORD_AUTOJOIN=0 kills it) —
    # best-effort, only for the top-level `event start` verb (see _autojoin docstring).
    if _autojoin:
        _discord_autojoin()
    print("\nEvent readiness:")
    event_status(rest)  # exit code: 0 = ready, 1 = FAILs remain


def event_stop(rest):
    """Stop racecast-managed services only — never the GUI apps (a mistyped command
    must not be able to kill a live broadcast). Generates + sends the post-event
    report BEFORE the teardown (default-on; --no-report skips) — while the relay is
    still up, so commentator names resolve. Report failure is non-fatal.

    Idempotent (#524): if the relay is already gone the event was already stopped —
    e.g. the last-part auto-stop (STOP PART Q) already ran the report + teardown, and
    the operator then clicks Control Center "Stop Event" too. Regenerating the report
    now would resolve NO commentator names and drop the qualifying marker (both need
    the live relay), overwriting/re-sending a strictly worse report. So a stop with no
    live relay is a no-op — the good report stands."""
    if not _relay_is_alive():
        print("Event already stopped — nothing to do "
              "(the last-part auto-stop already ran the report + teardown; the "
              "existing report stands). Stop any lingering app with `racecast "
              "companion stop` / `racecast relay stop`.")
        return
    if "--no-report" not in rest:
        try:
            r = _build_report_file()
            print(r["summary"])
            try:
                _send_report_core(r["path"], report=r.get("report"),
                                  window=r.get("window"))
                print("Report sent to Discord.")
            except Exception as exc:  # noqa: BLE001 — best-effort; still tear down
                print(f"report: Discord send failed ({exc}).")
        except Exception as exc:  # noqa: BLE001 — no health data etc.; still tear down
            print(f"report: skipped ({exc}).")
    # Leave the Discord voice channel we auto-joined at event start (default on,
    # RACECAST_DISCORD_AUTOLEAVE=0 kills it) — best-effort, never blocks teardown.
    _discord_autoleave()
    # Tear down companion + streams BEFORE the relay. On Windows the panel-spawned
    # `event stop` is a child of the relay process, and relay_stop runs
    # `taskkill /F /T` which walks the parent-PID tree — that would kill this very
    # process mid-teardown. Stopping the relay LAST means companion/streams cleanup
    # has already run; report generation above still saw the relay up (for names).
    try:
        companion_stop([])
    except SystemExit as exc:
        print(exc.code if isinstance(exc.code, str)
              else f"companion: stop failed (exit {exc.code}).")
    if glob.glob(os.path.join(_streams_static_dir(), "feed_*.pid")):
        streams_stop([])
    relay_stop([])
    print("OBS/Discord/Tailscale keep running — quit them manually if needed.")


def _takeover_port(args):
    """--port N from a takeover arg list, default RELAY_PORT."""
    if "--port" in args:
        i = args.index("--port")
        if i + 1 >= len(args):
            sys.exit("racecast: --port requires an integer value")
        try:
            return int(args[i + 1])
        except ValueError:
            sys.exit(f"racecast: --port must be an integer, got {args[i + 1]!r}")
    return RELAY_PORT


def event_takeover(rest):
    """`racecast event takeover <A-ip> [--stint N] [--qualifying] [--port N] [--force]`
    — take the broadcast over from another producer (A) in one step: read A's
    on-air stint + league from /status, refuse a wrong-league takeover (unless
    --force), warn if the timer will not carry, pull A's chat, then bring the
    stack up at that stint via `event start`. The broadcast-output switch (stream
    key) stays a crew procedure."""
    if not rest or rest[0].startswith("-"):
        sys.exit("usage: racecast event takeover <A-tailscale-ip> [--funnel] [--stint N] "
                 "[--qualifying] [--port N] [--force]")
    host, args = rest[0], rest[1:]
    force = "--force" in args
    qualifying_flag = "--qualifying" in args
    funnel = "--funnel" in args
    port = _takeover_port(args)
    stint_tokens = _stint_args(args)        # validates 1-based int (sys.exit on bad)
    stint_override = int(stint_tokens[1]) if stint_tokens else None

    secret = _active_console_secret() if funnel else None
    if funnel and not secret:
        sys.exit("racecast: --funnel takeover needs the league CONSOLE_SECRET in your "
                 "active profile (same league as A). Set it, or use the tailnet IP.")
    base = _funnel_takeover_base(host) if funnel else None

    status = None
    if funnel:
        try:
            fetched = _takeover_get(base + "/status", secret)
            status = fetched if isinstance(fetched, dict) else None
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code == 403:
                sys.exit("racecast: producer A rejected the step-up secret (HTTP 403) "
                         "— the league CONSOLE_SECRET in your active profile does not "
                         "match A's. Re-export the profile from A and import it here.")
            if code == 401:
                sys.exit("racecast: producer A returned HTTP 401 for the takeover pull "
                         "— A is running an older relay that still requires a console "
                         "token. Update A to this version, or take over from the "
                         "tailnet IP instead (racecast event takeover <100.x-ip>).")
            status = None                 # network/unreachable -> fall back to --stint
    else:
        try:
            fetched = _relay_fetch_json(f"http://{host}:{port}/status")
            status = fetched if isinstance(fetched, dict) else None
        except Exception:
            status = None

    a_sheet = (status.get("league") or {}).get("sheet_id") if status else None
    block = league_guard(a_sheet, os.environ.get("RACECAST_SHEET_ID"), force)
    if block:
        sys.exit(f"racecast: {block}")
    if status is None:
        if funnel:
            print(f"note: producer A at {host} not reachable via Funnel — relying on "
                  f"--stint and the shared sheet.")
        else:
            print(f"note: producer A at {host}:{port} not reachable — relying on "
                  f"--stint and the shared sheet.")
    elif not a_sheet:
        print("note: could not verify A's league (older relay?) — proceeding.")

    if not os.environ.get("RACECAST_SHEET_PUSH_URL"):
        print("WARNING: no SHEET_PUSH_URL in the active profile — the race timer "
              "will NOT carry over from producer A. Set it for handover-safe timing.")

    plan = takeover_plan(status, stint_override, qualifying_flag)
    if plan["stint"] is None:
        sys.exit("racecast: producer A is unreachable and no --stint was given — "
                 "read the on-air stint off A's panel and re-run with --stint N.")

    # best-effort: a chat failure must not abort the takeover. Per branch so the
    # tailnet path keeps its original `except SystemExit`-only contract (chat_cmd
    # exits on failure); the funnel pull raises real exceptions (HTTP/URL/ValueError).
    if funnel:
        try:
            payload = _takeover_get(base + "/chat", secret)
            n = ca.apply_pulled(_chat_path(), payload)
            _chat_reload_if_running()
            print(f"Pulled {n} messages from A (funnel).")
        except Exception as exc:
            print(f"note: chat pull failed ({type(exc).__name__}) — continuing takeover.")
    else:
        try:
            chat_cmd(["pull", host, "--port", str(port)])
        except SystemExit:
            print("note: chat pull failed — continuing takeover.")

    # best-effort: a console-versions failure must not abort (same per-branch split).
    if funnel:
        try:
            payload = _takeover_get(base + "/versions", secret)
            count = cpadm.apply_pulled(_console_versions_path(), payload)
            print(f"pulled {count} console version record(s) from A (funnel).")
        except Exception as exc:
            print(f"note: console-versions pull failed ({type(exc).__name__}) — continuing.")
    else:
        try:
            console_cmd(["pull-versions", host, "--port", str(port)])
        except SystemExit:
            print("note: console-versions pull failed — continuing takeover.")

    # Adopt A's active cues (#243), like the chat pull — best-effort, never aborts.
    try:
        if funnel:
            payload = _takeover_get(base + "/cues", secret)
        else:
            payload = _takeover_get("http://%s:%d/cues/data" % (host, port))
        n = cue.apply_pulled(_cues_path(), payload, time.time())
        _cues_reload_if_running()
        print(f"Pulled {n} cue(s) from A.")
    except Exception as exc:
        print(f"note: cue pull failed ({type(exc).__name__}) — continuing takeover.")

    # Adopt A's health history (#health), like the chat pull — best-effort, never aborts.
    try:
        if funnel:
            body = _takeover_get_text(base + "/health", secret)
        else:
            with http_util.open_url("http://%s:%d/health/raw" % (host, port), timeout=5) as r:
                body = r.read().decode("utf-8")
        conn = hsmod.open_db(_health_db_path()); hsmod.migrate(conn)
        n = hsmod.import_jsonl(conn, body.splitlines()); conn.close()
        print(f"Pulled {n} health samples from A.")
    except Exception as exc:
        print(f"note: health pull failed ({type(exc).__name__}) — continuing takeover.")

    # Adopt A's on-air event title (#207), persisted to event.json BEFORE bring-up
    # so the new relay loads it (mirrors the chat pull). Best-effort, never aborts.
    a_title = _takeover_event_title(status)
    if a_title is not None:
        try:
            path = _event_title_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"title": a_title}, fh)
            print(f"Adopted A's event title: “{a_title}”." if a_title
                  else "Cleared the event title to match producer A.")
        except OSError as exc:
            print(f"note: could not persist A's event title ({exc}) — continuing.")

    print(f"Taking over at stint {plan['stint']} (from A's "
          f"{plan['source']})" + (" — qualifying mode" if plan["qualifying"] else "") + ".")
    print("When ready, switch the broadcast output (stream key) to this machine "
          "per your crew procedure.\n")

    # Announce the takeover (#317) BEFORE event_start (which exits with the readiness
    # code). Its own helper so the takeover tests stub the network/DB I/O cleanly.
    _announce_takeover(status, plan, a_title)

    es_args = ["--stint", str(plan["stint"])]
    if plan["qualifying"]:
        es_args.append("--qualifying")
    # _new_session=False: a takeover continues A's broadcast — keep the report window
    # (B's local health DB starts fresh, so its contiguity heuristic covers B's part).
    event_start(es_args, _autojoin=False, _new_session=False)   # bring-up; exits with readiness code


def _relay_is_alive():
    """True when the relay daemon is up (its PID file names a live process)."""
    pid = sv.read_pid(_relay_pid_path())
    return bool(pid and sv.pid_alive(pid))


def _streams_is_alive():
    """True when any tracked static-streams feed is currently running."""
    return any(f["alive"] for f in streams_status_data())


def parse_freeport_args(rest, default_ports=pt.FEED_PORTS):
    """`freeport [PORT...] [--force]` -> (ports, force). No ports given = the three
    feed ports. Raises ValueError on a bad option or a non-port token."""
    force = False
    chosen = []
    for arg in rest:
        if arg in ("--force", "-f"):
            force = True
        elif arg.startswith("-"):
            raise ValueError(f"unknown option: {arg}")
        elif not arg.isdigit() or not (1 <= int(arg) <= 65535):
            raise ValueError(f"not a valid port: {arg}")
        else:
            chosen.append(int(arg))
    return (chosen or list(default_ports), force)


def freeport_owner(port, relay_alive, static_alive_ports):
    """Which RUNNING racecast service legitimately owns `port` right now (so freeing
    it would cut a live feed): 'relay' (it binds the feed ports + control port),
    'streams' (a tracked static feed is alive on it), or None (orphan/foreign)."""
    if relay_alive and (port in pt.FEED_PORTS or port == RELAY_PORT):
        return "relay"
    if port in static_alive_ports:
        return "streams"
    return None


def freeport_cmd(rest):
    """Free stuck feed ports: kill whatever LISTENS on each, unless a running relay
    or static-streams owns it (then refuse — stop that service, or --force). Default
    targets 53001-53003. Exit 1 if any port was refused, else 0."""
    try:
        chosen, force = parse_freeport_args(rest)
    except ValueError as exc:
        sys.exit(f"racecast: {exc}")
    relay_alive = _relay_is_alive()
    static_alive = {int(f["label"]) for f in streams_status_data()
                    if f["alive"] and str(f["label"]).isdigit()}
    refused = False
    for port in chosen:
        pids = pt.pids_on_port(port)
        owner = freeport_owner(port, relay_alive, static_alive)
        action, found = pt.decide_free(pids, owned=bool(owner), force=force)
        shown = ", ".join(str(p) for p in found)
        if action == "clear":
            print(f"port {port}: already free")
        elif action == "refuse":
            refused = True
            who = "relay" if owner == "relay" else "static streams"
            stop = "racecast relay stop" if owner == "relay" else "racecast streams stop"
            print(f"port {port}: held by the running {who} (PID {shown}) — "
                  f"{stop}, or re-run with --force")
        else:
            for pid in found:
                pt.kill_pid(pid)
            left = pt.pids_on_port(port)
            if left:
                print(f"port {port}: STILL in use after kill (PID {', '.join(map(str, left))})")
            else:
                print(f"port {port}: freed (was PID {shown})")
    raise SystemExit(1 if refused else 0)


DISPATCH = {
    ("relay", "start"): relay_start, ("relay", "stop"): relay_stop,
    ("relay", "restart"): relay_restart, ("relay", "status"): relay_status,
    ("relay", "logs"): relay_logs, ("relay", "run"): relay_run,
    ("relay", "open-panel"): relay_open_panel, ("relay", "open-hud"): relay_open_hud,
    ("relay", "open-status"): relay_open_status,
    ("companion", "start"): companion_start, ("companion", "stop"): companion_stop,
    ("companion", "restart"): companion_restart, ("companion", "status"): companion_status,
    ("companion", "logs"): companion_logs,
    ("companion", "open-buttons"): companion_open_buttons,
    ("companion", "open-admin"): companion_open_admin,
    ("companion", "enable-control"): companion_enable_control,
    ("streams", "start"): streams_start, ("streams", "stop"): streams_stop,
    ("streams", "restart"): streams_restart, ("streams", "status"): streams_status,
    ("streams", "logs"): streams_logs, ("streams", "run-feed"): streams_run_feed,
    ("event", "status"): event_status, ("event", "start"): event_start,
    ("event", "stop"): event_stop, ("event", "takeover"): event_takeover,
    ("tailscale", "up"): tailscale_up_cmd, ("tailscale", "down"): tailscale_down_cmd,
    ("tailscale", "status"): tailscale_status_cmd,
    ("obs", "refresh"): obs_refresh_cmd, ("obs", "collection"): obs_collection_cmd,
    ("obs", "stream-target"): obs_stream_target_cmd, ("obs", "benchmark"): obs_benchmark_cmd,
    ("obs", "logs"): obs_logs, ("tailscale", "logs"): tailscale_logs,
    ("sheet", "url"): sheet_url_cmd, ("sheet", "open"): sheet_open_cmd,
    ("app", "launch"): app_launch_cmd, ("app", "quit"): app_quit_cmd,
}

ONESHOT_MAP = {
    "preflight":     "scripts/preflight.py",
    "speedtest":     "scripts/speedtest.py",
    "cookies":       "relay/get-cookies.py",
    "graphics":      "relay/get-graphics.py",
    "media":         "relay/get-media.py",
    "brands":        "relay/get-brands.py",
    "setup":         "setup-assets.py",
    "install-tools": "scripts/install_tools.py",
    "install-apps":  "scripts/install_apps.py",
    "obs-browser":   "scripts/obs_browser_linux.py",
    "update":        "scripts/update.py",
}

# Forward --runtime-dir only to one-shot scripts whose argparse defines it.
# Verified against each script: preflight.py + get-cookies.py accept it; get-graphics.py
# and get-media.py (they use --out) and setup-assets.py do not. install-tools takes it
# for the machine-level managed speedtest bin dir (<runtime>/bin).
RUNTIME_DIR_ONESHOTS = ("preflight", "speedtest", "cookies", "install-tools")


def _cookies_oneshot_args(rest):
    """Translate `cookies` subcommand args. A leading 'twitch' selects the Twitch
    export (--platform twitch); anything else is the YouTube browser as before."""
    rest = list(rest)
    if rest and rest[0] == "twitch":
        return ["--platform", "twitch"] + rest[1:]
    return rest


def _oneshot_code(command, rest):
    """Run a one-shot and return its exit code (the seam `racecast init` uses to
    chain steps — oneshot() below keeps the exit-the-CLI behavior)."""
    if command == "preflight":
        # The sheet check reads RACECAST_SHEET_ID from the environment. Frozen mode
        # already loads .env (_load_env_frozen); in repo/package mode preflight
        # runs as a subprocess, which inherits os.environ — merge the .env file
        # in (real environment wins, same semantics as the scripts' load_dotenv).
        for key, val in _read_env_file().items():
            os.environ.setdefault(key, val)
    if command == "cookies":
        rest = _cookies_oneshot_args(rest)
    _od = _active_overlay_dir()
    extra = _oneshot_extra(command, rest, _runtime_dir(), _runtime_base_dir(),
                           overlay_css=os.path.join(_od, "hud.css") if _od else None,
                           profile_graphics=_active_profile_graphics_dir())
    if command == "update" and "--current" not in rest:
        extra += ["--current", version()]
    return _run_script(ONESHOT_MAP[command], list(rest) + extra)


def oneshot(command, rest):
    raise SystemExit(_oneshot_code(command, rest))


def version():
    """Build version via the shared app_version helper (single source of truth).
    A VERSION file is stamped into the bundle by tools/build-binary.py; a repo
    checkout has none -> 'dev'."""
    return _app_version.read_version(resource_path(""))


def update_check_data(fetch=None, current=None, platform=None, frozen=None):
    """Check-only view of the self-updater for the Home dashboard: is a newer
    GitHub release out? Thin wrapper over scripts/update.py — the single source
    of truth for the version compare and release lookup (the `racecast update`
    command installs it). Never downloads or replaces anything here. Network
    call; served on demand via /api/update (cached), never from the status poll.
    Never raises; {"ok": False} when offline / rate-limited / the tag is
    malformed. A non-frozen 'dev' checkout reports ok with no update; a frozen
    binary with a non-semver version (a preview build, or a local 'dev' build) is
    a real installable artifact, so — like the CLI — it gets the latest release
    offered (the `frozen` flag is what tells the two apart; see #70). `fetch`/
    `current`/`platform`/`frozen` are test seams."""
    import update as upd
    frozen = IS_FROZEN if frozen is None else frozen
    cur = current or version()
    out = {"ok": True, "current": cur, "latest": None, "update_available": False,
           "notes": "",
           "releases_url": f"https://github.com/{upd.REPO}/releases/latest"}
    if upd.parse_version(cur) is None and not frozen:   # source checkout — `git pull`
        out["note"] = "development build — update check skipped"
        return out
    try:
        release = (fetch or upd.fetch_latest)()
    except Exception:
        out["ok"] = False
        return out
    try:
        kind, detail, _url = upd.classify(release, platform or sys.platform, cur, frozen)
    except Exception:
        out["ok"] = False
        return out
    if kind == "error":
        out["ok"] = False
        return out
    out["latest"] = detail                    # tag for up-to-date / update / building
    out["update_available"] = kind in ("update", "building")
    # Release notes are GitHub-authored (untrusted); the dialog renders them as
    # plaintext (#101), so return the raw body and never pre-render HTML here.
    out["notes"] = release.get("body") or ""
    return out


def preview_list_data(fetch=None, platform=None):
    """On-demand list of installable preview builds for the Control Center's
    Help view. Network call (the GitHub releases list); never downloads an
    asset. Thin wrapper over scripts/update.py's pure classifier. {"ok": False}
    when offline / rate-limited. `fetch`/`platform` are test seams."""
    import update as upd
    out = {"ok": True, "previews": []}
    try:
        releases = (fetch or upd.fetch_releases)()
    except Exception:
        out["ok"] = False
        return out
    try:
        rows = upd.classify_prereleases(releases, platform or sys.platform)
    except Exception:
        out["ok"] = False
        return out
    # Preview notes are untrusted; the Help view shows them as plaintext (#101).
    out["previews"] = rows
    return out


def export_companion(rest):
    """Write the bundled (password-stripped) Companion config for import.
    Default: runtime/ — the same home as the localized OBS collection."""
    out = None
    if rest[:1] == ["--out"] and len(rest) == 2:
        out = rest[1]
    elif rest:
        sys.exit("usage: racecast export companion [--out PATH]")
    dst = out or os.path.join(_runtime_dir(), "racecast-buttons.companionconfig")
    if os.path.isdir(dst):
        dst = os.path.join(dst, "racecast-buttons.companionconfig")
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    shutil.copyfile(resource_path("companion/racecast-buttons.companionconfig"), dst)
    print(f"Wrote {dst} — import it in Companion (Import / Export -> Import).")


def aggregate_status(_rest=None):
    relay_status([])
    companion_status([])
    streams_status([])


def running_apps_data(probe=None):
    """OBS/Discord process running-state for the Event overview (cheap
    pgrep/tasklist per app). Never raises — both False on any failure."""
    try:
        if probe is None:
            probe = _event_modules()[0].app_running
        return {"obs": bool(probe("obs")), "discord": bool(probe("discord"))}
    except Exception:
        return {"obs": False, "discord": False}


def relay_live_data(fetch=None, started=None):
    """Screenshot-safe live relay stats for the Home dashboard: race-timer
    state and each feed's stint + coarse phase, pulled from the relay control
    server on localhost. Deliberately omits stream URLs/channels — only the
    1-based stint index and the state label leave the process. Network call to
    localhost; served on demand via /api/relay-live, never from the status poll.
    Never raises; {"ok": False} when the relay is unreachable. `fetch`/`started`
    are test seams."""
    fetch = fetch or _relay_fetch_json
    try:
        status = fetch(f"http://127.0.0.1:{RELAY_PORT}/status")
        timer = fetch(f"http://127.0.0.1:{RELAY_PORT}/timer/data")
    except Exception:
        return {"ok": False}
    if not isinstance(status, dict):
        return {"ok": False}
    feeds = []
    for name in ("A", "B"):
        f = (status.get("feeds") or {}).get(name)
        if isinstance(f, dict):
            feeds.append({"feed": name, "stint": f.get("stint"),
                          "state": f.get("state"), "down": bool(f.get("down"))})
    t = timer if isinstance(timer, dict) else {}
    try:
        get = started or (lambda: os.path.getmtime(_relay_pid_path()))
        uptime = max(0, int(time.time() - get()))
    except Exception:
        uptime = None
    health = status.get("health") if isinstance(status.get("health"), dict) else None
    return {"ok": True, "schedule_len": status.get("schedule_len"),
            "uptime_s": uptime, "feeds": feeds, "health": health,
            "timer": {"mode": t.get("mode"), "visible": t.get("visible"),
                      "remaining_s": t.get("remaining_s"),
                      "duration_s": t.get("duration_s"),
                      "end": t.get("end"), "server_now": t.get("server_now")}}


def event_title_read_data(alive=None, fetch=None, path=None, default=None):
    """Current free-text event title (#207) for the Control Center Home. Relay up
    -> the authoritative live value from /status. Relay down (or unreachable) ->
    runtime/<profile>/event.json, falling back to the active profile's EVENT_TITLE
    default. Never raises. alive/fetch/path/default are test seams."""
    alive = alive or _relay_is_alive
    fetch = fetch or _relay_fetch_json
    path = path or _event_title_path()
    is_alive = bool(alive())
    if is_alive:
        try:
            st = fetch(f"http://127.0.0.1:{RELAY_PORT}/status")
            if isinstance(st, dict) and isinstance(st.get("event_title"), str):
                return {"ok": True, "title": st["event_title"],
                        "source": "relay", "relay_alive": True}
        except Exception:  # noqa: BLE001 — relay reachable check is best-effort
            pass           # fall through to the persisted file / default
    try:
        with open(path, encoding="utf-8") as fh:
            saved = json.load(fh)
        if isinstance(saved, dict) and isinstance(saved.get("title"), str):
            return {"ok": True, "title": saved["title"],
                    "source": "file", "relay_alive": is_alive}
    except (OSError, ValueError):
        pass               # no/corrupt file -> the profile default
    dflt = _profile_event_default() if default is None else default
    return {"ok": True, "title": dflt or "", "source": "default",
            "relay_alive": is_alive}


def _profile_event_default():
    """The active profile's EVENT_TITLE default ("" when unset/unresolvable).
    Best-effort — the Home field degrades to empty, never errors."""
    try:
        root = _env_base(IS_FROZEN, _real_executable(), HERE)
        rc = pcfg.resolve_config(root, runtime_root=_runtime_base_dir())
        return rc.event_title or ""
    except Exception:  # noqa: BLE001 — best effort
        return ""


def event_title_write_data(value, alive=None, post=None, path=None, sanitize=None):
    """Set the live event title (#207) from the Control Center Home. Relay up ->
    POST /event/title (updates the live store AND persists event.json in one place).
    Relay down -> write event.json directly (the next `event start` adopts it via
    EventTitleStore precedence; mirrors takeover()). Sanitized with the relay's one
    rule. Returns {"ok", "title", "applied"} or {"ok": False, "error"}; never raises.
    alive/post/path/sanitize are test seams."""
    alive = alive or _relay_is_alive
    path = path or _event_title_path()
    sanitize = sanitize or _event_title_sanitizer()
    title = sanitize(value)
    if alive():
        post = post or _relay_post_json
        try:
            res = post(f"http://127.0.0.1:{RELAY_PORT}/event/title", {"title": title})
            stored = res.get("title", title) if isinstance(res, dict) else title
            return {"ok": True, "title": stored, "applied": "relay"}
        except Exception as exc:  # noqa: BLE001 — surface as a clean error to the UI
            return {"ok": False, "error": f"relay rejected the title: {exc}"}
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"title": title}, fh)
        return {"ok": True, "title": title, "applied": "file"}
    except OSError as exc:
        return {"ok": False, "error": f"could not write event.json: {exc}"}


def obs_collection_data(get=None):
    """Live OBS scene-collection check for the Control Center Apps view (on-demand
    /api/obs-collection). Best effort: {"ok": True, **status} when OBS answered,
    else {"ok": False, "note": reason}. Never raises (the route wraps it too).
    `get` is a test seam (defaults to obs_ws.get_scene_collection)."""
    if get is None:
        try:
            import obs_ws
            expected = _active_obs_collection()
            def get():
                return obs_ws.get_scene_collection(expected=expected)
        except Exception as exc:                     # noqa: BLE001 — best effort
            return {"ok": False, "note": str(exc)}
    status, note = get()
    if status is None:
        return {"ok": False, "note": note}
    return {"ok": True, **status}


def obs_stream_target_data(part):
    """Control Center action: set OBS's stream service+key for a Producer Part.
    Wraps _apply_stream_target -> {"ok", "note"} (note never carries the key)."""
    ok, note = _apply_stream_target(part or "")
    return {"ok": ok, "note": note}


_LOGO_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg")


def servable_logo_path(logo_path):
    """Return `logo_path` only when it is a web-image file (by extension),
    else "". Pure extension gate: keeps the /api/profile/logo route from
    serving a non-image file someone put in LOGO (e.g. profile.env). Existence
    is validated upstream in config.py (ResolvedConfig.logo_path)."""
    if logo_path and os.path.splitext(logo_path)[1].lower() in _LOGO_EXTS:
        return logo_path
    return ""


def profile_logo():
    """Absolute path to the ACTIVE profile's logo when it is a servable web
    image, else None. Best-effort (never raises) -- the header logo is optional.
    Served by GET /api/profile/logo."""
    try:
        root = _env_base(IS_FROZEN, _real_executable(), HERE)
        rc = pcfg.resolve_config(root, runtime_root=_runtime_base_dir())
        return servable_logo_path(rc.logo_path) or None
    except Exception:  # noqa: BLE001 — best effort
        return None


def profiles_data():
    """Control Center profile switcher data: the effective active profile plus
    every available profile with its display NAME and whether SHEET_ID is set.
    {ok, active, logo, profiles:[{name, display, sheet_set, kind}]} or
    {ok:false, error}. Never raises."""
    try:
        root = _env_base(IS_FROZEN, _real_executable(), HERE)
        runtime_root = _runtime_base_dir()
        active = _active_profile_name()
        out = []
        logo = False
        for n in pcfg.list_profiles(root):
            try:
                rc = pcfg.resolve_config(root, override=n, runtime_root=runtime_root)
                out.append({"name": n, "display": rc.name,
                            "sheet_set": bool(rc.sheet_id),
                            "kind": rc.kind})
                if n == active:
                    logo = bool(servable_logo_path(rc.logo_path))
            except pcfg.ProfileError:
                out.append({"name": n, "display": n, "sheet_set": False,
                            "kind": pcfg.DEFAULT_KIND})
        return {"ok": True, "active": active, "logo": logo, "profiles": out}
    except Exception as exc:
        return {"ok": False, "error": f"could not read profiles: {exc}"}


def profile_use_data(name, set_active=None):
    """Switch the active profile (synchronous pointer write, like env_write_data).
    {ok, active} or {ok:false, error}. `set_active` is a test seam."""
    try:
        root = _env_base(IS_FROZEN, _real_executable(), HERE)
        runtime_root = _runtime_base_dir()
        (set_active or pa.set_active_profile)(root, runtime_root, name)
        return {"ok": True, "active": name}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"could not switch profile: {exc}"}


def profile_new_data(name, source="example", create=None, kind=None,
                     template=None):
    """Create a new profile by copying `source` (endurance) or generating a
    solo profile.env (kind="solo"). Does NOT switch to it.
    {ok, name, path} or {ok:false, error}. `create` seam."""
    try:
        root = _env_base(IS_FROZEN, _real_executable(), HERE)
        target = (create or pa.create_profile)(
            root, name, source or "example",
            kind=kind or pcfg.DEFAULT_KIND, template=template)
        # return the directory slug (what `profile use` / the active pointer need),
        # which may differ from a typed display name like "Demo League" -> "demo-league".
        return {"ok": True, "name": os.path.basename(target), "path": target}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"could not create profile: {exc}"}


def ui_status_payload(relay=None, companion=None, streams=None, tailscale=None,
                      cookies=None, apps_running=None):
    """Aggregate health for the Control Center dashboard (/api/status).
    Each parameter is an optional zero-arg callable override (None = real
    probe). Cheap, local-only probes — the sheet-fetching asset check lives
    in assets_status_data() behind the on-demand /api/assets.
    apps_running: OBS/Discord running-state used by the Event overview."""
    return {"version": version(),
            "os": sys.platform,            # lets the UI hide OS-inapplicable app actions
            "relay": (relay or relay_status_data)(),
            "companion": (companion or companion_status_data)(),
            "streams": (streams or streams_status_data)(),
            "tailscale_ip": (tailscale or _tailscale_ip)(),
            "cookies": (cookies or cookies_status_data)(),
            "apps_running": (apps_running or running_apps_data)()}


_resource_monitor = None


def resources_data():
    """Latest machine-resource snapshot + color levels for the Control Center System card.
    {available:false} until the monitor has a first sample; never raises."""
    if _resource_monitor is None:
        return {"available": False}
    snap = _resource_monitor.latest()
    if snap is None:
        return {"available": False}
    return {"available": True,
            "cpu_pct": snap["cpu_pct"],
            "cpu_level": resources.cpu_level(snap["cpu_pct"]),
            "mem_used": snap["mem_used"],
            "mem_total": snap["mem_total"],
            "mem_pct": snap["mem_pct"],
            "mem_level": resources.mem_level(snap["mem_pct"]),
            "net_up_bps": snap["net_up_bps"],
            "net_down_bps": snap["net_down_bps"],
            "disk_free": snap["disk_free"],
            "disk_level": resources.disk_level(snap["disk_free"])}


def cookies_status_data(status=None):
    """Local cookie-jar freshness (no network — safe for the 3 s poll;
    never raises — a broken probe must not 500 the status poll)."""
    try:
        if status is None:
            pf = _event_modules()[1]
            path = _cookies_path()

            def status():
                return pf.cookies_status(path)
        res = status()
        return {"level": res.level, "detail": res.detail}
    except Exception as exc:
        return {"level": "WARN", "detail": f"check failed: {exc}"}


PRODUCER_TAB = "Producer"   # read-only league Sheet tab: Part | Producer | MagicDNS


def _producer_fetch(url):
    """Fetch the Producer-tab CSV as text. Covered side -> http_util (UA-stamped),
    never a bare urllib call (tests/test_http_util.py enforces this)."""
    return http_util.get_bytes(url, timeout=15).decode("utf-8", "replace")


def producer_schedule_data(fetch=None, self_name=None, refresh_env=None):
    """Read-only producer handover schedule from the active league Sheet's
    `Producer` tab (`Part | Producer | MagicDNS`), for the Control Center Home
    view. Network: a gviz CSV fetch (seconds) — served on demand via
    /api/producer-schedule, never from the status poll (like assets_status_data).

    Each row is tagged `self` (exact-FQDN match of its MagicDNS against this
    machine's own MagicDNS name) so the Home view disables takeover against this
    machine. `self_known` is False when our own MagicDNS can't be detected
    (Tailscale off/logged out) — the UI then locks ALL takeover actions.

    Tolerant: any fetch/parse failure returns empty rows (the card hides), never
    raises. `fetch`/`self_name`/`refresh_env` are test seams."""
    from urllib.parse import quote
    import producer as prod
    import tailscale as ts
    (refresh_env or _apply_active_profile_env)()
    own = ts.detect_magicdns_name() if self_name is None else self_name
    base = {"rows": [], "self_name": own, "self_known": bool(own)}
    sheet_id = os.environ.get("RACECAST_SHEET_ID") or ""
    if not sheet_id:
        return base
    # headers=1 pins sheet row 1 as the header. Without it gviz auto-detects and,
    # when the Part column mixes text + numbers (an empty/"Q" qualifying label
    # beside numbered parts), merges the header into row 1 -> parse_producer_rows
    # finds no header and returns [] -> this card silently hides. (erf-nls: 4 rows.)
    url = ("https://docs.google.com/spreadsheets/d/%s/gviz/tq?tqx=out:csv&headers=1&sheet=%s"
           % (sheet_id, quote(PRODUCER_TAB)))
    try:
        text = (fetch or _producer_fetch)(url)
        rows = prod.parse_producer_rows(text)
    except Exception:
        return base
    for r in rows:
        r["self"] = ts.magicdns_is_self(r.get("magicdns", ""), own)
    base["rows"] = rows
    return base


def assets_status_data(state=None, refresh_env=None):
    """Sheet-driven graphics/media readiness (network: sheet fetch, takes
    seconds — served on demand via /api/assets, never from the status poll).
    Re-injects the active profile's league env first (RACECAST_SHEET_ID etc.) so a
    profile changed while the Control Center runs is reflected — see preflight_data."""
    try:
        (refresh_env or _apply_active_profile_env)()
        ev = _event_modules()[0]
        g_dir, m_dir, missing_g, missing_m = (state or _asset_state)(ev)
    except Exception as exc:
        return {"ok": False, "error": f"asset check failed: {exc}"}
    g = ev.classify_assets("Graphics", missing_g, ev.local_count(g_dir), ev.FAIL,
                           "run `racecast graphics`")
    m = ev.classify_assets("Media", missing_m, ev.local_count(m_dir), ev.WARN,
                           "run `racecast media`")
    return {"ok": True,
            "graphics": {"level": g.level, "detail": g.detail},
            "media": {"level": m.level, "detail": m.detail}}


def assets_files_data(roots=None, profile=None):
    """Local graphics/media files actually present in runtime/ (cheap listdir —
    no sheet, no network). Returns {"ok": True, "profile": name, "graphics":
    [{name, v}], "media": [{name, v}]} with sorted basenames, or {"ok": False,
    "error": ...}; never raises. Each `v` is a per-profile, per-mtime cache token
    (`<profile>-<mtime>`) the Control Center appends to the gallery <img>/<video>
    src so the browser's decode-cache busts on a profile switch or a re-download
    (#274 — profiles typically share filenames like Overlay.png with different
    bytes). `roots` (a {"graphics": dir, "media": dir} dict) and `profile` are the
    test seams."""
    IMG = (".png", ".jpg", ".jpeg", ".webp", ".gif")
    VID = (".mp4", ".webm", ".mov")
    # Media is not only video: the Intermission Music track is an MP3 (#398).
    # List audio too and tag each media item kind=audio|video so the Control
    # Center renders an <audio> vs a <video> tile.
    AUD = (".mp3", ".m4a", ".aac", ".ogg", ".oga", ".wav", ".flac")
    try:
        if profile is None:
            profile = _active_profile_name() or ""
        if roots is None:
            rt = _runtime_dir()
            roots = {"graphics": os.path.join(rt, "graphics"),
                     "media": os.path.join(rt, "media"),
                     "brands": os.path.join(rt, "brands")}

        def listing(d, exts, media=False):
            if not os.path.isdir(d):
                return []
            out = []
            for f in sorted(os.listdir(d)):
                full = os.path.join(d, f)
                if f.lower().endswith(exts) and os.path.isfile(full):
                    item = {"name": f,
                            "v": f"{profile}-{int(os.path.getmtime(full))}"}
                    if media:
                        item["kind"] = ("audio" if f.lower().endswith(AUD)
                                        else "video")
                    out.append(item)
            return out
        return {"ok": True,
                "profile": profile,
                "graphics": listing(roots["graphics"], IMG),
                "media": listing(roots["media"], VID + AUD, media=True),
                "brands": listing(roots.get("brands", ""), IMG)}
    except Exception as exc:
        return {"ok": False, "error": f"asset listing failed: {exc}"}


def asset_roots_data():
    """The active profile's graphics/media dirs the asset-file route serves from,
    resolved LIVE on every call (NOT snapshotted when the Control Center starts).
    /api/assets/files lists from the same _runtime_dir(); freezing this one at
    startup let the two diverge — the gallery LISTED files (live, correct) that
    serving then 404'd from a stale root. That bit a Finder-launched (App-
    Translocated) .app, where early-startup path resolution differs from the
    settled per-request value, and would also bite a runtime profile switch (#55)."""
    rt = _runtime_dir()
    return {"graphics": os.path.join(rt, "graphics"),
            "media": os.path.join(rt, "media"),
            "brands": os.path.join(rt, "brands")}


_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def env_entries_data(path=None):
    """The active .env as ordered {key, value} entries for the Settings editor.
    Missing file -> empty list (not an error). Never raises. Writes nothing —
    `path` is a test seam; production resolves _env_file()."""
    try:
        p = path or _env_file()
        text = ""
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                text = fh.read()
        entries = [{"key": k, "value": v} for k, v in parse_env_text(text).items()]
        return {"ok": True, "path": p, "entries": entries}
    except Exception as exc:
        return {"ok": False, "error": f"could not read .env: {exc}"}


def _validate_env_entries(entries):
    """(cleaned [(key,value)], None) on success, (None, error_str) on failure.
    Blank rows (no key) are dropped; keys must be valid env identifiers, unique;
    values must not contain line breaks."""
    seen, pairs = set(), []
    for e in entries or []:
        key = str(e.get("key", "")).strip()
        val = str(e.get("value", ""))
        if not key:
            continue
        if not _ENV_KEY_RE.match(key):
            return None, (f"invalid key: {key!r} — use letters, digits and "
                          "underscore, not starting with a digit")
        if "\n" in val or "\r" in val:
            return None, f"value for {key} must not contain line breaks"
        if key in seen:
            return None, f"duplicate key: {key}"
        seen.add(key)
        pairs.append((key, val.strip()))
    return pairs, None


def merge_env_text(original, pairs):
    """Rewrite .env `original` text with `pairs` (ordered [(key,value)]):
    update each existing key line in place (keeping its position and the
    comments/blank lines around it), drop keys the user removed, append brand
    new keys at the end. Comment and blank lines are always preserved."""
    wanted = dict(pairs)
    written = set()
    out = []
    for line in original.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in wanted:
                out.append(f"{key}={wanted[key]}")
                written.add(key)
                continue
            if _ENV_KEY_RE.match(key):          # a real key the user removed
                continue
        out.append(line)                        # comment / blank / non-key kept
    extra = [f"{k}={v}" for k, v in pairs if k not in written]
    if extra:
        if out and out[-1].strip():
            out.append("")
        out.extend(extra)
    return "\n".join(out) + "\n"


def _write_env_file(path, entries):
    """Validate `entries`, merge into the file at `path` (preserving comments),
    write atomically (tmp + os.replace). {ok, path} or {ok:false, error}. Never
    raises. Shared by the machine .env and profile.env editors."""
    try:
        pairs, err = _validate_env_entries(entries)
        if err:
            return {"ok": False, "error": err}
        original = ""
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                original = fh.read()
        text = merge_env_text(original, pairs)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
        return {"ok": True, "path": path}
    except Exception as exc:
        return {"ok": False, "error": f"could not write {os.path.basename(path)}: {exc}"}


MACHINE_ENV_PREFIX = "RACECAST_"


def env_write_data(entries, path=None):
    """Validate the Settings editor's entries and persist them to the machine
    .env, preserving comments. Atomic. Writes ONLY the server-resolved path (or
    the test-supplied `path`), never a client value. {ok,path} or {ok:false,error}.

    Machine .env keys are restricted to the RACECAST_ prefix (defense-in-depth
    for #1): the file is documented to hold only RACECAST_* knobs, so the editor
    must not write a process-loader var (LD_PRELOAD / DYLD_INSERT_LIBRARIES /
    PATH) that spawned children would inherit. The shared validator runs first so
    a malformed key still reports the precise syntax error; the profile.env
    editor (un-prefixed league keys) goes through profile_env_write_data and is
    unaffected."""
    pairs, err = _validate_env_entries(entries)
    if err:
        return {"ok": False, "error": err}
    foreign = [k for k, _ in pairs if not k.startswith(MACHINE_ENV_PREFIX)]
    if foreign:
        return {"ok": False, "error": (f"machine .env keys must start with "
                                       f"{MACHINE_ENV_PREFIX}: {foreign[0]!r} is not allowed")}
    return _write_env_file(path or _env_file(), entries)


def env_upsert_data(updates, path=None):
    """Set each key in `updates` (dict) in the machine .env WITHOUT dropping any other
    key. env_write_data treats its entries as the COMPLETE set (unlisted real keys are
    removed), so read the current entries, overlay `updates`, and write the union.
    {ok,path} or {ok:false,error}. Never raises beyond the underlying helpers' contract."""
    target = path or _env_file()
    read = env_entries_data(path=target)          # {ok, path, entries:[{key,value}]}
    if not read.get("ok"):
        return read
    merged = {e["key"]: e["value"] for e in read["entries"]}
    merged.update({k: str(v) for k, v in updates.items()})
    entries = [{"key": k, "value": v} for k, v in merged.items()]
    res = env_write_data(entries, path=target)
    if not res.get("ok") and "must start with" in (res.get("error") or ""):
        return {"ok": False, "error": (
            "the machine .env contains a non-RACECAST_ key, so the device selection "
            "could not be saved (nothing was written); the machine .env must hold only "
            f"RACECAST_* keys — fix it in the .env editor, then retry. [{res['error']}]")}
    return res


def devices_enumerate_data():
    """Control Center General-Settings data: the OBS video-capture devices
    (webcam/capture share one device list) and microphones (#307), each
    enumerated by a throwaway-scene OBS probe (no collection needs importing).
    {ok, devices:[{name,value}], note, mic:[{name,value}], mic_note}. ok reflects
    the video enumeration only (webcam/capture, unchanged contract); mic_note
    explains a failed/empty mic list independently — the front-end disables each
    dropdown on its own signal. Never raises (obs_ws.probe_device_options is
    best-effort)."""
    import obs_ws
    res = obs_ws.probe_device_options()
    return {"ok": not res["note"],
            "devices": [{"name": d["name"], "value": d["value"]} for d in res["devices"]],
            "note": res["note"],
            "mic": [{"name": d["name"], "value": d["value"]} for d in res["mic"]],
            "mic_note": res["mic_note"]}


def ps_discover_data():
    """Control Center General-Settings data: discover the PS4/PS5 running GT7 on the
    LAN (a running relay's latched console, else a broadcast scan). Never raises."""
    res = resolve_console()
    return {"ok": bool(res["consoles"]), "consoles": res["consoles"],
            "note": res["note"], "from_relay": res["from_relay"]}


def devices_write_data(webcam, capture, mic=None, tyres=None, path=None):
    """Upsert the chosen webcam/capture/mic/tyres device ids into the machine .env
    (RACECAST_WEBCAM/RACECAST_CAPTURE/RACECAST_MIC/RACECAST_TYRES_CAPTURE; mic added
    #307, tyres/fuel capture added in the commentary-HUD work; unset or equal to the
    capture card = the crop reuses the capture card's source, #597). A blank/None
    value leaves that key unchanged; all blank -> {ok:false, error} (nothing to
    save). `path` is a test seam (mirrors env_upsert_data's), unused in production
    (resolves the real machine .env)."""
    updates = {}
    if (webcam or "").strip():
        updates["RACECAST_WEBCAM"] = webcam.strip()
    if (capture or "").strip():
        updates["RACECAST_CAPTURE"] = capture.strip()
    if (mic or "").strip():
        updates["RACECAST_MIC"] = mic.strip()
    if (tyres or "").strip():
        updates["RACECAST_TYRES_CAPTURE"] = tyres.strip()
    if not updates:
        return {"ok": False, "error": "no device selected"}
    return env_upsert_data(updates, path=path)


def _active_profile_env_strict():
    """(active_name, profile.env path) for the active profile, or (None, None)
    when no profile resolves. Distinct from _active_profile_env_path(), which
    falls back to the machine .env — the Profile editor must never edit .env."""
    active = _active_profile_name()
    if not active:
        return None, None
    root = _env_base(IS_FROZEN, _real_executable(), HERE)
    return active, os.path.join(pcfg.profiles_dir(root), active, pcfg.PROFILE_ENV_NAME)


def profile_env_entries_data():
    """The active profile's profile.env as {key,value} entries for the Profile
    editor. {ok, path, active, entries} or {ok:false, error}. Never raises."""
    try:
        active, path = _active_profile_env_strict()
        if not active:
            return {"ok": False, "error": "no active profile — create or select one first"}
        text = ""
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        entries = [{"key": k, "value": v} for k, v in parse_env_text(text).items()]
        return {"ok": True, "path": path, "active": active, "entries": entries}
    except Exception as exc:
        return {"ok": False, "error": f"could not read profile.env: {exc}"}


def profile_env_write_data(entries):
    """Persist the Profile editor entries to the active profile's profile.env
    (validate + comment-preserving merge, atomic). {ok,path} or {ok:false,error}.
    Server resolves the path from the active profile, never a client value."""
    active, path = _active_profile_env_strict()
    if not active:
        return {"ok": False, "error": "no active profile — create or select one first"}
    return _write_env_file(path, entries)


def _machine_env_value(name):
    """A machine .env value (real env wins, then the .env file). '' when unset."""
    v = os.environ.get(name)
    if v:
        return v
    path = _env_file()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return parse_env_text(fh.read()).get(name, "")
    return ""


def _collection_switch_enabled():
    """Opt-OUT: `event start` auto-switches OBS to the active profile's scene
    collection by default. Setting the machine flag RACECAST_OBS_COLLECTION_SWITCH to a
    falsey value (0/false/no/off) restores the old warn-only behaviour; absent/empty
    means enabled. Mirrors the RACECAST_FEED_FANOUT parse convention."""
    return _machine_env_value("RACECAST_OBS_COLLECTION_SWITCH").strip().lower() \
        not in {"0", "false", "no", "off"}


def _standby_on_start_enabled():
    """Opt-OUT: `event start` parks OBS on the Standby scene by default. Setting the
    machine flag RACECAST_OBS_STANDBY_ON_START to a falsey value (0/false/no/off)
    disables it; absent/empty means enabled. Mirrors _collection_switch_enabled."""
    return _machine_env_value("RACECAST_OBS_STANDBY_ON_START").strip().lower() \
        not in {"0", "false", "no", "off"}


def _ts_api_err(exc):
    """Compact message for a Tailscale API failure, including the HTTP error body."""
    import urllib.error
    if isinstance(exc, urllib.error.HTTPError):
        try:
            detail = exc.read().decode("utf-8", "replace").strip()[:300]
        except Exception:
            detail = ""
        return f"HTTP {exc.code} {detail}".strip()
    return f"{type(exc).__name__}: {exc}"


def _console_setup_funnel(args):
    """`racecast console setup-funnel [--apply] [--target T]` — automate the
    one-time tailnet prerequisites via the Tailscale Admin API: enable MagicDNS +
    add the 'funnel' nodeAttr. Auth via a Tailscale API access token
    (RACECAST_TS_API_KEY). Dry-run unless --apply. HTTPS certificate enablement has
    no API — reminder only."""
    import funnel_setup as fset
    api_key = _machine_env_value("RACECAST_TS_API_KEY")
    if not api_key:
        sys.exit(
            "racecast: no Tailscale API access token configured.\n"
            "Admin console -> Settings -> Keys -> 'API access tokens' -> Generate "
            "access token, then set in .env:\n  RACECAST_TS_API_KEY=tskey-api-...\n"
            "(One-off: revoke it + clear the line after setup-funnel succeeds.)")
    apply = "--apply" in args
    target = fset.DEFAULT_TARGET
    if "--target" in args:
        i = args.index("--target")
        if i + 1 < len(args):
            target = args[i + 1]
    try:
        token = api_key
        prefs = fset.get_dns_prefs(token)
        acl, etag = fset.get_acl(token)
    except (OSError, ValueError, KeyError) as exc:
        sys.exit(f"racecast: Tailscale API error: {_ts_api_err(exc)}")
    plan = fset.setup_plan(prefs, acl)
    if not plan:
        print("Funnel prerequisites already satisfied: MagicDNS on, 'funnel' nodeAttr "
              "present.\nReminder: also enable HTTPS Certificates (DNS page) — no API "
              "for that.\nThen: racecast funnel on")
        return None
    print("Funnel setup — changes needed:")
    for step in plan:
        print(f"  - {step}")
    if not apply:
        print("\n(dry-run) re-run with --apply to perform these via the Tailscale API.")
        print("Note: applying the nodeAttr rewrites the policy via the API, which "
              "drops HuJSON comments — the current policy is backed up first.")
        return None
    try:
        if not fset.magicdns_enabled(prefs):
            fset.enable_magicdns(token)
            print("  ✓ MagicDNS enabled")
        if not fset.acl_has_funnel(acl):
            backup = os.path.join(_runtime_base_dir(),
                                  f"ts-acl-backup-{int(time.time())}.json")
            with open(backup, "w", encoding="utf-8") as fh:
                json.dump(acl, fh, indent=2)
            new_acl, _changed = fset.add_funnel_nodeattr(acl, target=target)
            fset.put_acl(token, new_acl, etag)
            print(f"  ✓ 'funnel' nodeAttr added (target {target}); "
                  f"previous policy saved to {backup}")
    except (OSError, ValueError, KeyError) as exc:
        sys.exit(f"racecast: Tailscale API write failed: {_ts_api_err(exc)}\n"
                 "(MagicDNS may have applied; the policy was not changed if the ACL "
                 "step failed. Re-run, or finish in the admin console.)")
    print("\nDone. Reminder: enable HTTPS Certificates in the admin console (DNS "
          "page) if not already.\nThen: racecast funnel on")
    return None


def _funnel_auto_enabled():
    """Opt-OUT: bring the public /console Funnel up on `event start` by default.
    The Funnel is the preferred way to produce, so it auto-enables unless the
    machine flag RACECAST_FUNNEL (legacy RACECAST_COCKPIT_FUNNEL still honored) is
    explicitly set to a falsey value (false/0/no/off); an absent/empty key means
    enabled. Still requires the cockpit actually usable (a per-league secret
    exists) — reads on-disk truth via console_status_data()."""
    flag = ""
    epath = _env_file()
    if os.path.exists(epath):
        with open(epath, encoding="utf-8") as fh:
            env = parse_env_text(fh.read())
        flag = env.get("RACECAST_FUNNEL", env.get("RACECAST_COCKPIT_FUNNEL", ""))
    if flag.strip().lower() in ("0", "false", "no", "off"):
        return False
    st = console_status_data()
    # Zero-config console has no separate "enable" flag — usability == a league
    # secret exists. (console_status_data() returns ok/has_secret, never an
    # "enabled" key; gating on the latter silently dead-ended this path. #216.)
    return bool(st.get("ok") and st.get("has_secret"))


def _console_roster_safe():
    """_console_roster() that returns [] instead of raising when the relay is
    down (the Control Center status poll must never 500)."""
    try:
        return _console_roster()
    except Exception:
        return []


def _console_internal_host(ip):
    """Host for the 'internal' console link the Control Center offers alongside the
    public Funnel link: the producer's Tailscale IP when the tailnet is up, else
    loopback. Mirrors the relay panel's own link rule (relay --bind auto binds the
    Tailscale IP + 127.0.0.1)."""
    return ip or "127.0.0.1"


def crew_entries_data():
    """Crew roster for the Control Center, read live from the running relay's
    /crew/data. {ok, entries:[{row,name,director,producer}]} or {ok:false, error}."""
    try:
        data = _relay_fetch_json("http://127.0.0.1:%d/crew/data" % RELAY_PORT)
    except Exception as exc:
        return {"ok": False,
                "error": "relay not reachable (start the relay): %s" % exc}
    # Each entry carries its 1-based crew DATA-row index (the Crew tab order, header
    # excluded) so the editor can address it on Save/Delete — the relay's /crew/data
    # is index-free, and crew_set/crew_delete are keyed by this row.
    entries = [{"row": i,
                "name": row.get("name", ""),
                "director": bool(row.get("director")),
                "producer": bool(row.get("producer")),
                "commentator": bool(row.get("commentator")),
                "race_control": bool(row.get("race_control")),
                "discord": row.get("discord") or ""}
               for i, row in enumerate(data.get("rows") or [], start=1)]
    return {"ok": True, "entries": entries}


def crew_write_data(row, name, director, producer, commentator=False,
                    race_control=False, discord=""):
    """Write one crew row via the relay's /crew/set (the relay holds the webhook
    URL — the Control Center never POSTs to SHEET_PUSH_URL directly)."""
    try:
        return _relay_post_json(
            "http://127.0.0.1:%d/crew/set" % RELAY_PORT,
            {"row": row, "name": name,
             "director": bool(director), "producer": bool(producer),
             "commentator": bool(commentator), "race_control": bool(race_control),
             "discord": (discord or "").strip()})
    except Exception as exc:
        return {"ok": False,
                "error": "relay not reachable (start the relay): %s" % exc}


def crew_delete_data(row):
    """Delete one crew row via the relay's /crew/delete."""
    try:
        return _relay_post_json("http://127.0.0.1:%d/crew/delete" % RELAY_PORT,
                                {"row": row})
    except Exception as exc:
        return {"ok": False,
                "error": "relay not reachable (start the relay): %s" % exc}


def console_status_data():
    """Console state for the Control Center: per-league secret presence and the
    per-commentator links. The console is zero-config — the secret is auto-provisioned
    here so links populate without an explicit enable step. Reads on-disk truth so a
    profile switch reflects without a Control Center restart. {ok, ...}; never raises."""
    try:
        menv = {}
        epath = _env_file()
        if os.path.exists(epath):
            with open(epath, encoding="utf-8") as fh:
                menv = parse_env_text(fh.read())
        # Opt-OUT: the checkbox is on by default; only an explicit falsey value
        # (false/0/no/off) unchecks it. Mirrors _funnel_auto_enabled().
        funnel_auto = menv.get("RACECAST_FUNNEL", menv.get(
            "RACECAST_COCKPIT_FUNNEL", "")).strip().lower() not in (
            "0", "false", "no", "off")
        secret = _ensure_active_console_secret() or ""
        magic = _tailscale_magicdns()
        links = []
        if secret:
            host = _console_internal_host(_tailscale_ip())
            versions = cpadm.load_versions(_console_versions_path())
            seen_keys = set()
            roster = []
            for name in list(_console_roster_safe()) + list(_crew_roster_safe()):
                key = cpa.streamer_key(name)
                if key and key not in seen_keys:
                    seen_keys.add(key)
                    roster.append(name)
            for name in roster:
                key = cpa.streamer_key(name)
                tok = cpa.mint_token(secret, key, cpadm.current_version(versions, key))
                links.append({
                    "name": name,
                    "internal": f"http://{host}:{RELAY_PORT}/console?t={tok}",
                    "funnel": (f"https://{magic}/console?t={tok}" if magic
                               else f"https://<magicdns-host>/console?t={tok}")})
        funnel_on = funnel_capable = False
        try:
            import tailscale as ts
            funnel_capable = ts.funnel_capable()
            funnel_on = ts.funnel_on() if funnel_capable else False
        except Exception:
            pass  # best-effort: tailnet down / CLI missing -> report both False
        return {"ok": True, "has_secret": bool(secret),
                "funnel_auto": funnel_auto, "funnel_capable": funnel_capable,
                "funnel_on": funnel_on, "links": links,
                "console_url": (f"https://{magic}/console" if magic else "")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def console_set_funnel_auto_data(auto):
    """Persist the 'bring the public Funnel up on event start' flag (machine-local
    RACECAST_FUNNEL). Opt-OUT default: unchecking writes an explicit `false`;
    checking writes `true`. {ok}|{ok:false,error}."""
    try:
        res = _set_env_key(_env_file(), "RACECAST_FUNNEL",
                           "true" if auto else "false")
        return {"ok": True} if res.get("ok") else res
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def console_funnel_data(on):
    try:
        funnel_cmd(["on" if on else "off"])
        return {"ok": True}
    except SystemExit as exc:
        return {"ok": False, "error": str(exc)}


def console_revoke_data(streamer):
    try:
        _console_token(["revoke", streamer])
        return {"ok": True}
    except SystemExit as exc:
        return {"ok": False, "error": str(exc)}


def _active_discord_webhook():
    """(webhook_url, league_name) for the active profile; ("","") on any
    failure. Best-effort — the webhook stays server-side, never in the browser."""
    try:
        root = _env_base(IS_FROZEN, _real_executable(), HERE)
        rc = pcfg.resolve_config(root, runtime_root=_runtime_base_dir())
        return rc.discord_webhook_url or "", rc.name or ""
    except Exception:  # noqa: BLE001 — best effort
        return "", ""


_PRODUCER_NAME_CACHE = None


def _resolve_producer_name():
    """This machine's producer display name for events (#317): the `Producer` Sheet
    tab reverse-resolved from our own MagicDNS name, else a manual
    RACECAST_PRODUCER_NAME override, else the hostname. Best-effort + cached (one
    Sheet fetch per process); never raises. Injected as RACECAST_PRODUCER_NAME into
    the relay child at start, and used for the takeover announcement."""
    global _PRODUCER_NAME_CACHE
    if _PRODUCER_NAME_CACHE is not None:
        return _PRODUCER_NAME_CACHE
    import socket
    name = ""
    try:
        data = producer_schedule_data()
        for r in data.get("rows", []):
            if r.get("self") and r.get("producer"):
                name = r["producer"]
                break
    except Exception:  # noqa: BLE001 — best-effort (no sheet / Tailscale down)
        name = ""
    if not name:
        name = (os.environ.get("RACECAST_PRODUCER_NAME") or "").strip()
    if not name:
        try:
            name = socket.gethostname() or ""
        except OSError:
            name = ""
    _PRODUCER_NAME_CACHE = name
    return _PRODUCER_NAME_CACHE


def _takeover_producer(status):
    """Producer A's display name from its /status (tailnet) or redacted takeover
    status (funnel), or "" when absent (older relay / unreachable)."""
    if not isinstance(status, dict):
        return ""
    return (status.get("producer") or "").strip()


def _post_discord_webhook(url, payload):
    """POST a Discord incoming-webhook JSON body. Raises on HTTP/network error
    (callers catch and report)."""
    # Discord sits behind Cloudflare, which 403s the default urllib
    # "Python-urllib/x.y" User-Agent — without an explicit UA the POST is
    # rejected and the link never arrives (matches the relay's health-alert
    # poster).
    with http_util.open_url(url, data=json.dumps(payload).encode(),
                            headers={"Content-Type": "application/json",
                                     "User-Agent": "racecast/1.0"},
                            method="POST", timeout=5) as r:
        r.read()


def console_post_link_data():
    """Post the shared /console landing-page link to the league's Discord
    webhook (with an @here ping). The link is computed server-side from MagicDNS
    — never supplied by the client. {ok}|{ok:false,error}; never raises."""
    try:
        magic = _tailscale_magicdns()
        if not magic:
            return {"ok": False, "error": "MagicDNS unavailable — is Tailscale up?"}
        webhook, league = _active_discord_webhook()
        if not webhook:
            return {"ok": False,
                    "error": "No DISCORD_WEBHOOK_URL configured for this league"}
        payload = cpadm.console_link_discord_payload(f"https://{magic}/console", league)
        _post_discord_webhook(webhook, payload)
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001 — best effort, surface the message
        return {"ok": False, "error": str(exc)}


def _active_profile_overlay_path(page):
    """(active, abs path to overlay/<page>.css) for the active profile, or
    (None, None) when no profile resolves or `page` is not an overlay page.
    Server-resolved; never a client path. Mirrors _active_profile_env_strict."""
    if page != "hud":
        return None, None
    active = _active_profile_name()
    if not active:
        return None, None
    root = _env_base(IS_FROZEN, _real_executable(), HERE)
    od = os.path.join(pcfg.profiles_dir(root), active, "overlay")
    return active, os.path.join(od, f"{page}.css")


def overlay_read_data(page):
    """The active profile's overlay/<page>.css text for the editor.
    {ok, page, active, css, path} or {ok:false, error}. Never raises."""
    try:
        active, path = _active_profile_overlay_path(page)
        if not active:
            return {"ok": False, "error": "no active profile or invalid page"}
        css = ""
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                css = fh.read()
        return {"ok": True, "page": page, "active": active, "css": css, "path": path}
    except Exception as exc:
        return {"ok": False, "error": f"could not read overlay css: {exc}"}


def overlay_write_data(page, content):
    """Persist editor content to the active profile's overlay/<page>.css
    (creates overlay/ if needed, atomic tmp+replace). {ok,path} or
    {ok:false,error}. Server resolves the path, never a client value."""
    try:
        active, path = _active_profile_overlay_path(page)
        if not active:
            return {"ok": False, "error": "no active profile or invalid page"}
        if not isinstance(content, str):
            return {"ok": False, "error": "content must be a string"}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
        return {"ok": True, "path": path}
    except Exception as exc:
        return {"ok": False, "error": f"could not write overlay css: {exc}"}


# --- Visual overlay builder (issue #114): layout model -> generated override.css.
# The builder OWNS <page>.css (generated); layout-<page>.json is the source of
# truth. The relay serves the generated CSS unchanged, so no relay change. ---

def _atomic_write_text(path, text):
    """Write text to path via tmp+os.replace (mirrors overlay_write_data)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _overlay_base_html(page):
    """Text of the base overlay page (src/obs/<page>.html), or '' on error."""
    if page != "hud":
        return ""
    try:
        with open(resource_path(f"obs/{page}.html"), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def _overlay_layout_path(page):
    """(active, abs path to overlay/layout-<page>.json) or (None, None)."""
    active, css_path = _active_profile_overlay_path(page)
    if not active:
        return None, None
    return active, os.path.join(os.path.dirname(css_path), f"layout-{page}.json")


def _css_has_rules(text):
    """True if `text` has real CSS once comments + whitespace are stripped."""
    return bool(re.sub(r"/\*.*?\*/", "", text or "", flags=re.S).strip())


def overlay_slots_data(page):
    """The base page's editable slots + base <style> + slot markup + sample data,
    so the Control Center renders a same-origin WYSIWYG canvas.
    {ok, page, slots, css, body, sample, flagPresets} or {ok:false, error}."""
    try:
        if page != "hud":
            return {"ok": False, "error": "invalid page"}
        html = _overlay_base_html(page)
        if not html:
            return {"ok": False, "error": "base page not bundled"}
        return {"ok": True, "page": page, "slots": ob.extract_slots(html),
                "css": ob.base_style(html), "body": ob.base_body(html),
                "sample": ob.SAMPLE.get(page, {}),
                "flagPresets": [dict(p) for p in ob.FLAG_PRESETS]}
    except Exception as exc:
        return {"ok": False, "error": f"could not read overlay slots: {exc}"}


def overlay_layout_read_data(page):
    """The active profile's layout-<page>.json for the builder. First use of a
    profile with a hand-written <page>.css and no layout imports that CSS verbatim
    into customCss (migration — never reverse-parsed).
    {ok, page, active, layout, migrated} or {ok:false, error}."""
    try:
        active, lpath = _overlay_layout_path(page)
        if not active:
            return {"ok": False, "error": "no active profile or invalid page"}
        if os.path.exists(lpath):
            with open(lpath, encoding="utf-8") as fh:
                layout = json.load(fh)
            result = {"ok": True, "page": page, "active": active,
                      "layout": layout, "migrated": False}
        else:
            _, css_path = _active_profile_overlay_path(page)
            existing = ""
            if os.path.exists(css_path):
                with open(css_path, encoding="utf-8") as fh:
                    existing = fh.read()
            migrated = bool(existing.strip())
            layout = (ob.migrate_layout(page, existing) if migrated
                      else ob.empty_layout(page))
            result = {"ok": True, "page": page, "active": active,
                      "layout": layout, "migrated": migrated}
        # Fold a legacy timer.css into the HUD layout's customCss so a
        # league's timer styling is not silently dropped after the timer→HUD merge.
        # A comment-only scaffold is ignored.
        if page == "hud" and lpath:
            timer_css = os.path.join(os.path.dirname(lpath), "timer.css")
            if os.path.exists(timer_css):
                with open(timer_css, encoding="utf-8") as fh:
                    legacy = fh.read()
                cur = layout.get("customCss") or ""
                if _css_has_rules(legacy) and legacy.strip() not in cur:
                    layout["customCss"] = (cur + ("\n" if cur else "")
                                           + "/* merged from legacy timer.css */\n"
                                           + legacy)
                    result["migrated"] = True
        return result
    except Exception as exc:
        return {"ok": False, "error": f"could not read overlay layout: {exc}"}


def _validate_layout(layout, page):
    """Structural gate before compile: (ok, error)."""
    if not isinstance(layout, dict):
        return False, "layout must be an object"
    if layout.get("page") not in (page, None):
        return False, "layout page mismatch"
    if not isinstance(layout.get("slots", {}), dict):
        return False, "slots must be an object"
    if not isinstance(layout.get("fonts", []), list):
        return False, "fonts must be a list"
    if not isinstance(layout.get("customCss", ""), str):
        return False, "customCss must be a string"
    return True, None


def _materialize_overlay_fonts(layout):
    """Copy fonts the layout references (slot fontFamily + bodyFont) from the
    machine library into the active profile's overlay/fonts/ when not already
    there, then return the authoritative profile font filename list. Library
    fonts are .woff2; profile uploads keep their own extension and are left as-is."""
    fdir = _overlay_fonts_dir()
    if not fdir:
        return _list_fonts(fdir)
    present_stems = {f.rsplit(".", 1)[0] for f in _list_fonts(fdir)}
    referenced = set()
    for ov in (layout.get("slots") or {}).values():
        if isinstance(ov, dict) and ov.get("fontFamily"):
            referenced.add(ov["fontFamily"])
    if layout.get("bodyFont"):
        referenced.add(layout["bodyFont"])
    lib = _machine_fonts_dir()
    lib_files = _list_fonts(lib)
    for stem in referenced:
        # Copy the base file AND any cut siblings (-Bold/-Italic/-BoldItalic) of the
        # referenced family, so `profile export` carries the full family (true
        # bold/italic offline) and the generated CSS can group them.
        for fn in lib_files:
            cut = ob.font_cut(fn)
            base = cut[0] if cut else ob.font_family(fn)
            if base != stem or ob.font_family(fn) in present_stems:
                continue
            src = _font_path(lib, fn)
            if src:
                os.makedirs(fdir, exist_ok=True)
                shutil.copy2(src, os.path.join(fdir, fn))
    return _list_fonts(fdir)


def overlay_layout_write_data(page, layout):
    """Validate + compile the layout to <page>.css and persist layout-<page>.json
    and <page>.css atomically. The slot list comes from the base page (never the
    client) and the compiler drops unknown slots/props, so only customCss is
    verbatim. {ok, path, css} or {ok:false, error}."""
    try:
        active, lpath = _overlay_layout_path(page)
        if not active:
            return {"ok": False, "error": "no active profile or invalid page"}
        ok, err = _validate_layout(layout, page)
        if not ok:
            return {"ok": False, "error": err}
        layout = dict(layout)
        layout["version"], layout["page"] = 1, page
        # Copy-on-save: any font the design references that lives in the machine
        # library (not yet in the profile) is copied into overlay/fonts/, so the
        # relay serves it and `profile export` stays self-contained. layout.fonts
        # is then made authoritative from the profile dir (client value advisory).
        layout["fonts"] = _materialize_overlay_fonts(layout)
        slots = ob.extract_slots(_overlay_base_html(page))
        css = ob.compile_overlay_css(layout, slots)
        _, css_path = _active_profile_overlay_path(page)
        os.makedirs(os.path.dirname(lpath), exist_ok=True)
        _atomic_write_text(lpath, json.dumps(layout, indent=2))
        _atomic_write_text(css_path, css)
        return {"ok": True, "path": css_path, "css": css}
    except Exception as exc:
        return {"ok": False, "error": f"could not write overlay layout: {exc}"}


def _overlay_fonts_dir():
    od = _active_overlay_dir()
    return os.path.join(od, "fonts") if od else None


def _machine_fonts_dir():
    """Machine-wide overlay font library (runtime/fonts/), shared across profiles.
    Managed in General Settings; the builder copies what a design uses into the
    profile on save (so exports stay self-contained)."""
    return os.path.join(_runtime_base_dir(), "fonts")


def _font_name_ok(name):
    """True for a safe overlay font filename (whitelisted name + extension)."""
    return (isinstance(name, str) and bool(ob.FONT_NAME_RE.match(name))
            and "." in name and name.rsplit(".", 1)[1].lower() in ob.FONT_EXTS)


def _list_fonts(dirpath):
    """Sorted valid font filenames in dirpath (empty when absent)."""
    out = []
    if dirpath and os.path.isdir(dirpath):
        for n in sorted(os.listdir(dirpath)):
            if _font_name_ok(n):
                out.append(n)
    return out


def _font_path(dirpath, name):
    """Resolve an existing font to a contained path, or None when unsafe/missing."""
    if not dirpath or not _font_name_ok(name):
        return None
    base = os.path.realpath(dirpath)
    # Containment its own statement so CodeQL recognizes the traversal barrier.
    path = os.path.realpath(os.path.join(base, name))
    if not path.startswith(base + os.sep) or not os.path.isfile(path):
        return None
    return path


def _write_font(dirpath, name, data):
    """Validate + atomically write a font into dirpath. (ok, name|error)."""
    if not _font_name_ok(name):
        return False, "invalid font name"
    if not isinstance(data, (bytes, bytearray)) or not data:
        return False, "empty font data"
    os.makedirs(dirpath, exist_ok=True)
    base = os.path.realpath(dirpath)
    path = os.path.realpath(os.path.join(base, name))
    if not path.startswith(base + os.sep):
        return False, "invalid font name"
    with open(path + ".tmp", "wb") as fh:
        fh.write(data)
    os.replace(path + ".tmp", path)
    return True, name


def overlay_fonts_list_data():
    """Fonts the builder can pick: the active profile's own overlay/fonts/ plus
    the machine-wide library. {ok, active, fonts, library} or {ok:false, error}."""
    try:
        active = _active_profile_name()
        if not active:
            return {"ok": False, "error": "no active profile"}
        return {"ok": True, "active": active,
                "fonts": _list_fonts(_overlay_fonts_dir()),
                "library": _list_fonts(_machine_fonts_dir())}
    except Exception as exc:
        return {"ok": False, "error": f"could not list fonts: {exc}"}


def overlay_font_upload_data(name, data):
    """Persist an uploaded font into the active profile's overlay/fonts/ after
    whitelist validation. {ok, name} or {ok:false, error}."""
    try:
        if not _active_profile_name():
            return {"ok": False, "error": "no active profile"}
        ok, res = _write_font(_overlay_fonts_dir(), name, data)
        return {"ok": True, "name": res} if ok else {"ok": False, "error": res}
    except Exception as exc:
        return {"ok": False, "error": f"could not upload font: {exc}"}


def overlay_bg_path():
    """The active profile's Overlay.png (canvas background), or None if absent."""
    g_dir, _ = _asset_dirs()
    path = os.path.join(g_dir, "Overlay.png")
    return path if os.path.isfile(path) else None


def overlay_font_serve(name):
    """(path, content_type) for an overlay font the canvas references: the active
    profile's overlay/fonts/ first, then the machine library (so a builder can
    preview a library font before it is copied into the profile on save). None
    when unsafe/missing."""
    if not _font_name_ok(name):
        return None
    path = _font_path(_overlay_fonts_dir(), name) or _font_path(_machine_fonts_dir(), name)
    if not path:
        return None
    return path, ob.FONT_CTYPES[name.rsplit(".", 1)[1].lower()]


# Bundled HUD asset resolution for the offline builder canvas (flags + brand
# logos). Mirrors the relay's resolve_asset: strict key + subdir whitelist +
# realpath containment so a request value can never escape src/assets/.
_OV_ASSET_EXTS = (("png", "image/png"), ("svg", "image/svg+xml"),
                  ("jpg", "image/jpeg"), ("jpeg", "image/jpeg"),
                  ("webp", "image/webp"))
_OV_ASSET_KEY_RE = re.compile(r"^[a-z0-9-]+$")


def overlay_asset_serve(sub, key):
    """(path, content_type) for a bundled HUD asset the builder canvas previews
    offline: src/assets/flags/<key>.<ext> or src/assets/brands/<key>.<ext> (tries
    known image extensions in order). None when the subdir/key is unsafe or no
    file matches — never raises on a bad request."""
    if sub not in ("flags", "brands") or not _OV_ASSET_KEY_RE.match(key or ""):
        return None
    base = os.path.realpath(resource_path(os.path.join("assets", sub)))
    for ext, ctype in _OV_ASSET_EXTS:
        path = os.path.realpath(os.path.join(base, f"{key}.{ext}"))
        if not path.startswith(base + os.sep):
            return None
        if os.path.exists(path):
            return path, ctype
    return None


# A modern-browser UA so Google's css2 endpoint serves woff2 (not legacy ttf).
_GOOGLE_FONT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36")


def _http_get(url, headers=None, binary=False, timeout=15):
    data = http_util.get_bytes(url, headers=headers or None, timeout=timeout)
    return data if binary else data.decode("utf-8", "replace")


def machine_fonts_list_data():
    """The machine-wide font library (runtime/fonts/), pre-seeded from the bundled
    curated set and extendable via the Settings typeahead. Machine-scoped (no active
    profile needed). {ok, fonts}."""
    try:
        return {"ok": True, "fonts": _list_fonts(_machine_fonts_dir())}
    except Exception as exc:
        return {"ok": False, "error": f"could not list fonts: {exc}"}


def machine_font_download_data(name, css_fetch=None, bin_fetch=None):
    """Self-host a Google font (curated or any typed family) into the machine-wide
    library (runtime/fonts/). {ok, name} or {ok:false, error}. SSRF-safe: `name`
    must match ob.GOOGLE_FONT_NAME_RE (letters/digits/spaces only), the css host is
    the fixed googleapis endpoint, and the downloaded woff2 must be on gstatic. The
    fetchers are injectable for tests."""
    try:
        if not ob.is_google_font_name(name):
            return {"ok": False, "error": "invalid font name"}
        css_fetch = css_fetch or (lambda u: _http_get(
            u, headers={"User-Agent": _GOOGLE_FONT_UA}))
        bin_fetch = bin_fetch or (lambda u: _http_get(u, binary=True))
        fdir = _machine_fonts_dir()
        # Preferred: fetch the four overlay cuts (regular/bold/italic/bold-italic) and
        # self-host EACH latin face, so a slot renders TRUE bold/italic instead of the
        # browser synthesizing them from one mislabeled cut (the issue this fixes).
        try:
            cuts_css = css_fetch(ob.google_font_cuts_url(name))
        except Exception:
            cuts_css = ""
        saved = []
        for (style, weight), url in sorted(ob.parse_google_font_cuts(cuts_css or "").items()):
            try:
                data = bin_fetch(url)
            except Exception:
                continue
            if not data:
                continue
            ok, res = _write_font(fdir, ob.google_font_cut_filename(name, style, weight), data)
            if ok:
                saved.append(res)
        if saved:
            base = ob.google_font_filename(name)
            return {"ok": True, "name": base if base in saved else saved[0]}
        # Fallback: a single-cut fetch for families/edge cases the cuts request can't
        # satisfy. Try the bold weight first, then the family's default face.
        m = None
        for url in (ob.google_font_css_url(name),
                    ob.google_font_css_url(name, weight=None)):
            try:
                css = css_fetch(url)
            except Exception:                     # a 400 for a missing weight, etc.
                continue
            # The woff2 must live on the fixed Google CDN host (defense in depth).
            m = re.search(r"url\((https://fonts\.gstatic\.com/[^)]+\.woff2)\)", css or "")
            if m:
                break
        if not m:
            return {"ok": False, "error": "no woff2 in Google CSS (unknown font?)"}
        data = bin_fetch(m.group(1))
        if not data:
            return {"ok": False, "error": "empty font download"}
        ok, res = _write_font(fdir, ob.google_font_filename(name), data)
        return {"ok": True, "name": res} if ok else {"ok": False, "error": res}
    except Exception as exc:
        return {"ok": False, "error": f"google font download failed: {exc}"}


def machine_font_delete_data(name):
    """Remove a font family from the machine-wide library. Deleting a base file also
    removes its cut siblings (-Bold/-Italic/-BoldItalic) so a family is never left
    half-deleted (which would strip a slot's bold/italic faces). {ok, removed} or
    {ok:false, error}."""
    try:
        fdir = _machine_fonts_dir()
        path = _font_path(fdir, name)
        if not path:
            return {"ok": False, "error": "font not found"}
        cut = ob.font_cut(name)
        family = cut[0] if cut else ob.font_family(name)
        removed = []
        for fn in _list_fonts(fdir):
            fcut = ob.font_cut(fn)
            fbase = fcut[0] if fcut else ob.font_family(fn)
            if fbase != family:
                continue
            fp = _font_path(fdir, fn)
            if fp:
                os.remove(fp)
                removed.append(fn)
        return {"ok": True, "removed": removed}
    except Exception as exc:
        return {"ok": False, "error": f"could not delete font: {exc}"}


# Keyless full Google-fonts family list (the metadata endpoint the fonts.google.com
# site itself uses — no API key, so no secret to manage). Powers the Settings
# free-text typeahead; cached by the caller and falling back to the curated list.
_GOOGLE_FONTS_METADATA_URL = "https://fonts.google.com/metadata/fonts"


def google_font_catalog_data(fetch=None):
    """All Google font family names for the typeahead, via the keyless metadata
    endpoint. Falls back to the curated catalog on any failure (so the datalist is
    never empty). {ok, families:[...], source:"google"|"curated"}. `fetch` injectable."""
    try:
        fetch = fetch or (lambda: _http_get(
            _GOOGLE_FONTS_METADATA_URL, headers={"User-Agent": _GOOGLE_FONT_UA}))
        raw = fetch()
        data = json.loads(raw.lstrip(")]}'\n ") if isinstance(raw, str) else raw)
        fams = sorted({f["family"] for f in data.get("familyMetadataList", [])
                       if isinstance(f, dict) and ob.is_google_font_name(f.get("family", ""))})
        if fams:
            return {"ok": True, "families": fams, "source": "google"}
    except Exception:  # network/parse failure -> the curated list still works
        pass
    return {"ok": True, "families": list(ob.GOOGLE_FONTS), "source": "curated"}


def backup_list_data():
    """{ok, active, items:[...]} for the Control Center Looks card."""
    try:
        import backup_admin as ba
        active = _active_profile_name()
        if not active:
            return {"ok": False, "error": "no active profile"}
        return {"ok": True, "active": active,
                "items": ba.list_backups(_backup_sources()["backups"])}
    except Exception as exc:
        return {"ok": False, "error": f"could not list backups: {exc}"}


def backup_create_data(label, force=False):
    """Create a named look backup. {ok, path} or {ok:false, error}."""
    try:
        import backup_admin as ba
        if not _active_profile_name():
            return {"ok": False, "error": "no active profile"}
        if not isinstance(label, str) or not label.strip():
            return {"ok": False, "error": "label required"}
        path = ba.create_backup(label, _backup_sources(),
                                profile=_active_profile_name(), force=bool(force))
        return {"ok": True, "path": path}
    except FileExistsError:
        return {"ok": False, "error": "a backup with that name exists (use force)"}
    except Exception as exc:
        return {"ok": False, "error": f"could not create backup: {exc}"}


def backup_restore_data(slug):
    """Restore a look backup by slug. {ok} or {ok:false, error}."""
    try:
        import backup_admin as ba
        src = _backup_sources()
        zip_path = os.path.join(src["backups"], f"{ba.sanitize_label(slug)}.zip")
        ba.restore_backup(zip_path, src)
        try:
            _refresh_obs_pages(force=True)
        except Exception:
            pass   # OBS refresh is best-effort; the restore already succeeded
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": f"restore failed: {exc} (live look unchanged)"}


def backup_delete_data(slug):
    """Delete a look backup by slug. {ok, removed} or {ok:false, error}."""
    try:
        import backup_admin as ba
        removed = ba.delete_backup(_backup_sources()["backups"], slug)
        return {"ok": True, "removed": removed}
    except Exception as exc:
        return {"ok": False, "error": f"could not delete backup: {exc}"}


def profile_export_data(name=None, include_assets=True, dest=None):
    """Build a portable profile bundle for `name` (default the active profile).
    {ok, path, slug} or {ok:false, error}. `dest` default is a temp .zip the UI
    streams then deletes; the CLI passes a directory or an --out path."""
    try:
        created_tmp = None
        import profile_io as pio
        slug = name or _active_profile_name()
        if not slug:
            return {"ok": False, "error": "no profile to export"}
        root = _env_base(IS_FROZEN, _real_executable(), HERE)
        profile_dir = os.path.join(root, "profiles", slug)
        rt = _profile_runtime(_runtime_base_dir(), slug)
        sources = {"profile_dir": profile_dir,
                   "graphics": os.path.join(rt, "graphics"),
                   "media": os.path.join(rt, "media"),
                   "brands": os.path.join(rt, "brands")}
        if dest is None:
            fd, dest = tempfile.mkstemp(prefix="profexport-", suffix=".zip")
            os.close(fd)
            created_tmp = dest
        path = pio.export_profile(slug, sources, bool(include_assets), dest)
        return {"ok": True, "path": path, "slug": pio.slugify(slug)}
    except Exception as exc:
        if created_tmp:
            try:
                os.unlink(created_tmp)
            except OSError:  # best-effort temp cleanup
                pass
        return {"ok": False, "error": f"could not export profile: {exc}"}


def profile_import_data(src_path, force=False):
    """Import a profile bundle file. {ok, name, display, includes_assets} or
    {ok:false, error}. Does NOT switch the active profile."""
    try:
        import profile_io as pio
        root = _env_base(IS_FROZEN, _real_executable(), HERE)
        roots = {"profiles_root": os.path.join(root, "profiles"),
                 "runtime_root": _runtime_base_dir()}
        info = pio.import_profile(src_path, roots, force=bool(force))
        return {"ok": True, **info}
    except FileExistsError:
        return {"ok": False,
                "error": "a profile with that name exists (use force to replace)"}
    except Exception as exc:
        return {"ok": False, "error": f"could not import profile: {exc}"}


def _streams_config_path():
    return os.path.join(_streams_static_dir(), "streams.json")


def _default_stream_feeds():
    """The built-in FEEDS from start-streams.py as editor entries, so the UI
    opens pre-seeded the first time (before any streams.json is saved)."""
    ss = _load_relay_module("scripts/start-streams.py")
    return [{"label": f"Feed {chr(65 + i)}", "channel": ch, "port": port}
            for i, (ch, port) in enumerate(ss.FEEDS)]


def streams_config_data(path=None, default=None):
    """static-stream feeds for the Control Center: the saved streams.json, or the
    built-in defaults when none exists yet. {"ok": True, "path", "entries":
    [{label, channel, port}]} — never raises. `path`/`default` are test seams."""
    try:
        p = path or _streams_config_path()
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
            entries = [{"label": str(e.get("label", "")),
                        "channel": str(e.get("channel", "")),
                        "port": str(e.get("port", ""))}
                       for e in data if isinstance(e, dict)]
        else:
            entries = (default or _default_stream_feeds)()
        return {"ok": True, "path": p, "entries": entries}
    except Exception as exc:
        return {"ok": False, "error": f"could not read streams config: {exc}"}


def _validate_streams_entries(entries):
    """(cleaned [{label,channel,port}], None) or (None, error). Rows with neither
    channel nor port are dropped; a present row needs a channel and a numeric,
    unique port (ports map 1:1 to OBS media sources)."""
    seen, out = set(), []
    for e in entries or []:
        label = str(e.get("label", "")).strip()
        channel = str(e.get("channel", "")).strip()
        port = str(e.get("port", "")).strip()
        if not channel and not port:
            continue
        if not channel:
            return None, "every feed needs a channel ID"
        if not port.isdigit():
            return None, f"port for {channel} must be a number"
        if port in seen:
            return None, f"duplicate port: {port}"
        seen.add(port)
        out.append({"label": label, "channel": channel, "port": port})
    return out, None


def streams_config_write_data(entries, path=None):
    """Validate and persist the static-stream feed list to streams.json (atomic
    tmp + os.replace). Writes ONLY the server-resolved path. {"ok": True, "path"}
    or {"ok": False, "error"}; never raises."""
    try:
        cleaned, err = _validate_streams_entries(entries)
        if err:
            return {"ok": False, "error": err}
        p = path or _streams_config_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cleaned, fh, indent=2)
        os.replace(tmp, p)
        return {"ok": True, "path": p}
    except Exception as exc:
        return {"ok": False, "error": f"could not write streams config: {exc}"}


def tools_status_data(which=None, version=None):
    """Per-tool install presence (+ version when present). On-demand: the
    version probe shells out once per tool. Returns {"ok": True, "tools":[...]}
    or {"ok": False, "error": ...}; never raises."""
    try:
        import install_tools as it
        pf = _event_modules()[1]
        which = which or shutil.which
        version = version or pf.tool_version
        tools = []
        for name in it.TOOLS:
            present = bool(which(name))
            tools.append({"name": name, "installed": present,
                          "version": version(name) if present else None})
        # speedtest is a first-class tool in the overview, but resolved via its own
        # finder (PATH or the managed bin dir) and never gates readiness.
        import speedtest as st
        st_bin = st.find_binary(_runtime_base_dir(), which)
        # Probe the resolved binary BY PATH (tool_version's which() accepts an
        # absolute path), so a managed-dir install — which is not on PATH — still
        # reports its version, like the PATH-installed tools above.
        tools.append({"name": "speedtest", "installed": bool(st_bin),
                      "version": version(st_bin) if st_bin else None})
        return {"ok": True, "tools": tools}
    except Exception as exc:
        return {"ok": False, "error": f"tool check failed: {exc}"}


def _companion_version_cache_path():
    """Machine-wide cache of the last-seen Companion version (companion-version.json
    next to the cookie jar). Companion has no local version file on Linux/host
    setups, so the running server is the only source — caching it lets the
    Control Center still show the last-known version while Companion is stopped."""
    return os.path.join(_runtime_base_dir(), "companion-version.json")


def _read_companion_version_cache(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("version") or None
    except (OSError, ValueError):
        return None   # absent/corrupt cache -> simply unknown


def _write_companion_version_cache(path, version):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"version": version}, fh)
    except OSError:
        return   # cache is best-effort; never break the apps route over it


def apps_status_data(present=None, version=None):
    """Per-app install presence (+ version when present and probeable). The
    presence probe is instant (filesystem/PATH); the version probe is best-effort
    per OS (macOS Info.plist; Windows exe metadata/Squirrel folder; Linux
    dpkg-query/build_info + `tailscale version`; Companion also via its running
    web server) and may shell out / make one localhost request on Windows/Linux —
    fine for this on-demand route, not the status poll. The Companion version is
    cached so it still shows while Companion is stopped. Returns {"ok": True,
    "apps":[...]} or {"ok": False, "error": ...}; never raises."""
    try:
        import install_apps as ia
        present = present or (lambda app: ia.app_present(app, sys.platform))
        base_version = version or (lambda app: ia.app_version(app, sys.platform))

        def resolve_version(app):
            v = base_version(app)
            if app != "companion":
                return v
            cache = _companion_version_cache_path()
            if v:
                _write_companion_version_cache(cache, v)
                return v
            return _read_companion_version_cache(cache)

        apps = []
        for a in ia.APPS:
            installed = bool(present(a))
            apps.append({"name": a, "installed": installed,
                         "version": resolve_version(a) if installed else None})
        return {"ok": True, "apps": apps}
    except Exception as exc:
        return {"ok": False, "error": f"app check failed: {exc}"}


def preflight_data(gather=None, refresh_env=None):
    """Full preflight checklist as structured sections (on-demand: runs hardware
    probes, per-tool version calls, and a Google-Sheet fetch when configured —
    can take several seconds). Returns {"ok": True, "sections":[{"title","results":
    [{"level","name","detail"}]}]} or {"ok": False, "error": ...}; never raises.

    Re-injects the active profile's league env first: the Control Center process
    holds os.environ for its whole lifetime, but the active profile can change
    underneath it (a `racecast profile import`/`use` from the CLI, or the in-UI
    import/switch). Without this refresh the sheet probe reads the RACECAST_SHEET_ID
    captured at UI startup — empty on a fresh install whose profile was imported
    afterwards — and warns "not set" though SHEET_ID is configured."""
    try:
        (refresh_env or _apply_active_profile_env)()
        pf = _event_modules()[1]
        run = gather or (lambda: pf.gather(resource_path("scripts/preflight.py"),
                                           _runtime_base_dir()))
        sections = [{"title": title,
                     "results": [{"level": r.level, "name": r.name,
                                  "detail": r.detail} for r in results]}
                    for title, results in run()]
        return {"ok": True, "sections": sections}
    except Exception as exc:
        return {"ok": False, "error": f"preflight failed: {exc}"}


def speedtest_data(base_dir=None):
    """Latest + recent speed-test history for the Control Center Preflight view.
    Read-only (the *run* goes through the `speedtest` op/job). Never raises."""
    try:
        import speedtest as st  # noqa: PLC0415 — lazy to mirror preflight_data pattern
        base = base_dir or _runtime_base_dir()
        # Ship the thresholds so the UI badge never drifts from the documented
        # constants (single source: speedtest.py mirrors the wiki table).
        thresholds = {"min_down": st.MIN_DOWN_MBPS, "min_up": st.MIN_UP_MBPS,
                      "rec_down": st.REC_DOWN_MBPS, "rec_up": st.REC_UP_MBPS}
        return {"ok": True, "latest": st.load_latest(base),
                "history": st.load_history(base), "thresholds": thresholds}
    except Exception as exc:  # never let a failed speedtest read surface to the UI as a crash
        return {"ok": False, "error": f"speedtest read failed: {exc}"}


# Bundled operator docs the Control Center's Help page can open (allowlist —
# only these keys map to a file, so the HTTP layer can serve nothing else).
# The role cheat sheet + the visual onboarding decks live on GitHub Pages (one
# central place) and are reached via `decks_url`, not served locally.
DOCS_FILES = {
    "setup-guide":  "docs/Broadcast_Setup_Guide.md",
    "setup-readme": "docs/README_SETUP.md",
}
_DOC_TITLES = {
    "setup-guide":  ("Setup guide", "Full broadcast-PC install & configuration walkthrough."),
    "setup-readme": ("Setup README", "Quick setup notes and command reference."),
}


def _wiki_repo():
    try:
        import update
        return update.REPO
    except Exception:
        return "jegr78/gt-racing-broadcast"


def _pages_url():
    """The GitHub Pages root for the visual onboarding decks (incl. the role cheat
    sheet) — `https://<owner>.github.io/<repo>/`. The decks are the central, always-
    current entry point the Control Center links to instead of serving the cheat
    sheet locally."""
    owner, _, name = _wiki_repo().partition("/")
    return f"https://{owner}.github.io/{name}/"


def _resolve_doc(rel, resolve):
    """Path of a bundled doc, or None. Checks src/docs/<f> (repo + binary, where
    docs keep their docs/ prefix) AND the bare basename (the distributed package:
    build.py copies the doc files to the package root)."""
    for cand in (rel, os.path.basename(rel)):
        try:
            p = resolve(cand)
            if os.path.isfile(p):
                return p
        except Exception:
            pass                # this candidate didn't resolve — try the next
    return None


def docs_data(resolve=None):
    """Help/Docs resources for the Control Center: the bundled local docs that
    are actually present (served via /api/docs/file/<key>) plus the canonical
    GitHub wiki URLs for the rendered guides. Never raises. `resolve` is a test
    seam."""
    resolve = resolve or resource_path
    repo = _wiki_repo()
    wiki = f"https://github.com/{repo}/wiki"
    local = []
    for key, rel in DOCS_FILES.items():
        if _resolve_doc(rel, resolve):
            title, desc = _DOC_TITLES[key]
            local.append({"key": key, "title": title, "desc": desc,
                          "kind": "html" if rel.endswith(".html") else "markdown"})
    # The onboarding decks (incl. the cheat sheet) ship bundled under
    # src/docs/slides, so the Control Center can open them OFFLINE in addition to
    # the always-current GitHub Pages copy.
    decks_local = "/docs/slides/" if docs_slides_serve("index.html", resolve) else None
    return {"ok": True, "wiki_url": wiki,
            "decks_url": _pages_url(),
            "decks_local_url": decks_local,
            "setup_url": f"{wiki}/Set-up-the-broadcast-PC",
            "director_url": f"{wiki}/Director-Setup",
            "event_url": f"{wiki}/Run-an-event",
            "issues_url": f"https://github.com/{repo}/issues",
            "local": local}


def docs_file_path(key, resolve=None):
    """Absolute path of an allowlisted bundled doc, or None for an unknown key
    or a missing file. The HTTP layer serves only what this returns."""
    rel = DOCS_FILES.get(key)
    if not rel:
        return None
    return _resolve_doc(rel, resolve or resource_path)


_SLIDES_CTYPES = {
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
    ".ico": "image/x-icon", ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf",
    ".txt": "text/plain; charset=utf-8",
}


def docs_slides_serve(relpath, resolve=None):
    """(path, content_type) for a file in the bundled onboarding-decks tree
    (`src/docs/slides` — the offline copy of the GitHub Pages hub, incl. the role
    cheat sheet). An empty/`/` path serves `index.html`. Returns None for a missing
    file or any path that escapes the slides dir (traversal guard). Never raises.
    `resolve` is a test seam."""
    resolve = resolve or resource_path
    try:
        base = os.path.realpath(resolve("docs/slides"))
        rel = (relpath or "").strip("/") or "index.html"
        target = os.path.realpath(os.path.join(base, rel))
        if target != base and not target.startswith(base + os.sep):
            return None
        if not os.path.isfile(target):
            return None
        ext = os.path.splitext(target)[1].lower()
        return target, _SLIDES_CTYPES.get(ext, "application/octet-stream")
    except Exception:
        return None


def docs_content(key, resolve=None):
    """(content_type, body_bytes) for an allowlisted Help doc, or None. HTML docs
    are served as-is; Markdown docs are rendered to a styled, self-contained HTML
    page (mdrender) so they read properly in a browser tab instead of as raw
    text. Never raises — returns None on any failure."""
    path = docs_file_path(key, resolve)
    if not path:
        return None
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        if path.lower().endswith((".html", ".htm")):
            return ("text/html; charset=utf-8", raw)
        import mdrender
        title = _DOC_TITLES.get(key, (key, ""))[0]
        doc = mdrender.page(title, mdrender.render(raw.decode("utf-8", "replace")))
        return ("text/html; charset=utf-8", doc.encode("utf-8"))
    except Exception:
        return None


def _read_env_file():
    try:
        with open(_env_file(), encoding="utf-8") as fh:
            return parse_env_text(fh.read())
    except OSError:
        return {}

def _init_pause(message):
    ins.gate_pause(message, sys.stdin.isatty())

def _active_sheet_id(root, base, active):
    """The active profile's SHEET_ID, or '' if no profile / unresolvable."""
    if not active:
        return ""
    try:
        return pcfg.resolve_config(root, override=active,
                                   runtime_root=base).sheet_id
    except pcfg.ProfileError:
        return ""


def _active_obs_collection():
    """The active profile's OBS scene-collection name, or the obs_ws default
    constant when no profile resolves. Tolerant: any resolution failure -> the
    constant, so the check/switch still work on a profile-less machine."""
    import obs_ws
    root = _env_base(IS_FROZEN, _real_executable(), HERE)
    active = _active_profile_name()
    if active:
        try:
            return pcfg.resolve_config(root, override=active,
                                       runtime_root=_runtime_base_dir()).obs_collection
        except pcfg.ProfileError:  # unresolvable profile -> use the default name
            pass
    return obs_ws.EXPECTED_SCENE_COLLECTION


def _active_profile_env_path():
    """profiles/<active>/profile.env for the active profile. Falls back to the
    machine .env when no profile is active -- used by the setup freshness probe;
    in practice the profile step gates before setup, so the fallback is only hit
    on a direct `racecast setup` without a profile (intentional, do not 'fix')."""
    root = _env_base(IS_FROZEN, _real_executable(), HERE)
    active = _active_profile_name()
    if not active:
        return _env_file()
    return os.path.join(pcfg.profiles_dir(root), active, pcfg.PROFILE_ENV_NAME)


def _init_profile_done():
    """Wizard done-probe for the profile step."""
    root = _env_base(IS_FROZEN, _real_executable(), HERE)
    base = _runtime_base_dir()
    active = _active_profile_name()
    return ins.profile_done(active, _active_sheet_id(root, base, active))


def _init_profile_run():
    """Ensure a league profile is active with its SHEET_ID filled. Creates one
    from the example template (prompting for a name) when none exists, pauses
    until SHEET_ID is set, then re-injects the profile's config for the steps
    that follow (graphics/media/setup)."""
    root = _env_base(IS_FROZEN, _real_executable(), HERE)
    base = _runtime_base_dir()
    if not pcfg.list_profiles(root):
        while True:
            name = ins.prompt_value(
                "Name your league profile (e.g. Demo League)", sys.stdin.isatty())
            slug = pa.slugify(name)
            if pa.valid_profile_name(slug) and slug != "example":
                break
            print(f"  '{name}' has no usable profile name (needs at least one "
                  "letter or digit; not 'example')")
        try:
            target = pa.create_profile(root, name)
        except ValueError as e:
            sys.exit(f"racecast: {e}")
        slug = os.path.basename(target)
        try:
            pa.set_active_profile(root, base, slug)
        except ValueError as e:
            sys.exit(f"racecast: profile created at profiles/{slug}/ but could not "
                     f"set it active ({e}). Run: racecast profile use {slug}")
        print(f"  created profile '{slug}' (profiles/{slug}/profile.env)")
    while True:
        active = _active_profile_name()
        if active is None:
            _init_pause("Select a league profile: run `racecast profile use <name>`")
            continue
        if _active_sheet_id(root, base, active):
            break
        path = os.path.join(pcfg.profiles_dir(root), active, pcfg.PROFILE_ENV_NAME)
        _init_pause(f"Fill in SHEET_ID in {path} (SHEET_PUSH_URL is optional)")
    _apply_active_profile_env()
    return 0


def _init_env_run():
    """Machine .env: create it from the template if missing (optional machine
    vars + the default profile). No gate -- league config lives in the profile."""
    path = _env_file()
    example = os.path.join(os.path.dirname(path), ".env.example")
    if not os.path.exists(path) and os.path.exists(example):
        shutil.copyfile(example, path)
        print(f"  created {path} from .env.example")
    for key, val in _read_env_file().items():
        os.environ.setdefault(key, val)
    return 0

def _init_cookies_run(browser):
    """Gate (YouTube login) + the cookies one-shot. The gate only fires when
    the cookies are actually missing/stale — under --force a fresh cookie jar
    skips the pause but still re-exports."""
    _pf = _event_modules()[1]
    res = _pf.cookies_status(_cookies_path())
    if ins.cookies_done(res.level, res.detail) is None:
        _init_pause(f"Log in to YouTube in {browser} — the cookie export "
                    "needs that browser session")
    return _oneshot_code("cookies", [browser])

def _init_assets_done(kind):
    """Done-probe for the graphics/media steps. Any probe failure counts as
    not-done: the step runs and its own error message is the actionable one."""
    try:
        ev = _event_modules()[0]
        g_dir, m_dir, missing_g, missing_m = _asset_state(ev)
    except Exception:
        return None
    if kind == "graphics":
        return ins.assets_done(missing_g, ev.local_count(g_dir))
    return ins.assets_done(missing_m, ev.local_count(m_dir))

def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None

def _init_import_json():
    return os.path.join(_runtime_dir(), _setup_import_name())

def _init_companion_cfg():
    return os.path.join(_runtime_dir(), "racecast-buttons.companionconfig")

def _init_export_run():
    export_companion([])
    return 0

def _init_steps(opts):
    """The full step list — `build_plan()` (honoring --skip-installs) selects
    and orders the subset that runs."""
    pf = _event_modules()[1]
    import install_apps
    cookies_path = _cookies_path()
    def cookies_skip():
        res = pf.cookies_status(cookies_path)
        return ins.cookies_done(res.level, res.detail)
    by_key = {
        "profile": {"done": _init_profile_done,
                    "run": _init_profile_run},
        "env": {"done": lambda: ".env present"
                if os.path.exists(_env_file()) else None,
                "run": _init_env_run},
        "install-tools": {"done": lambda: ins.tools_done(shutil.which,
                                                         pf.REQUIRED_TOOLS),
                          "run": lambda: _oneshot_code("install-tools", ["--yes"])},
        "install-apps": {"done": lambda: ins.apps_done(
                             lambda a: install_apps.app_present(a, sys.platform),
                             install_apps.APPS),
                         "run": lambda: _oneshot_code("install-apps", ["--yes"])},
        "cookies": {"done": cookies_skip,
                    "run": lambda: _init_cookies_run(opts["browser"])},
        "graphics": {"done": lambda: _init_assets_done("graphics"),
                     "run": lambda: _oneshot_code("graphics", [])},
        "media": {"done": lambda: _init_assets_done("media"),
                  "run": lambda: _oneshot_code("media", [])},
        # The import JSON must be newer than .env (its values are baked in). The
        # bundled OBS template is a dependency ONLY in dev (a real, stable file):
        # in the frozen binary it lives in _MEIPASS, re-extracted with a fresh
        # mtime on every launch, which would make `setup` look stale after every
        # start. So frozen compares against .env alone.
        "setup": {"done": lambda: ins.setup_done(
                      _mtime(_init_import_json()),
                      [_mtime(_active_profile_env_path())] if IS_FROZEN else
                      [_mtime(resource_path("obs/GT_Racing_Endurance.json")),
                       _mtime(_active_profile_env_path())]),
                  "run": lambda: _oneshot_code("setup",
                                               ["--out", _init_import_json()])},
        "export-companion": {"done": lambda: ins.export_done(
                                 os.path.exists(_init_companion_cfg())),
                             "run": _init_export_run},
        "preflight": {"done": lambda: None,   # always runs — it IS the verification
                      "run": lambda: _oneshot_code("preflight", [])},
    }
    return [{"key": k, "label": ins.STEP_LABELS[k], **by_key[k]}
            for k in ins.build_plan(opts["skip_installs"])]


def init_plan_data(steps, kinds, browser="firefox", next_steps=None):
    """Wizard plan for the Control Center: each step's current done/skip state
    plus how the UI runs it (kind/op/instruction from ins.STEP_KINDS). Pure +
    never-raise — a broken done-probe reads as 'not done' (the step then runs
    and surfaces its own error), never a 500. `steps` is the _init_steps()-shaped
    list; `next_steps` is the closing manual checklist."""
    out = []
    for st in steps:
        meta = kinds.get(st["key"], {"kind": "action", "op": None})
        try:
            reason = st["done"]()
        except Exception:
            reason = None
        instr = meta.get("instruction")
        if instr:
            instr = instr.replace("{browser}", browser)
        out.append({"key": st["key"], "label": st["label"],
                    "kind": meta["kind"], "op": meta.get("op"),
                    "done": reason is not None, "skip_reason": reason,
                    "instruction": instr})
    return {"ok": True, "steps": out, "next_steps": list(next_steps or [])}


def init_step_action_data(key):
    """Run one non-job wizard step in-process and report its new state.
    Handled here: 'profile' gate (re-probes the active profile's SHEET_ID),
    'env' action (creates .env from template if missing), and
    'export-companion' action. Job steps run through /api/op/<op>.
    Never raises -- returns {ok: False, error} instead."""
    try:
        if key == "profile":
            reason = _init_profile_done()
            return {"ok": True, "key": key, "done": reason is not None,
                    "skip_reason": reason}
        if key == "env":
            path = _env_file()
            example = os.path.join(os.path.dirname(path), ".env.example")
            if not os.path.exists(path) and os.path.exists(example):
                shutil.copyfile(example, path)
            reason = ".env present" if os.path.exists(path) else None
            return {"ok": True, "key": key, "done": reason is not None,
                    "skip_reason": reason}
        if key == "export-companion":
            _init_export_run()
            reason = ins.export_done(os.path.exists(_init_companion_cfg()))
            return {"ok": True, "key": key, "done": reason is not None,
                    "skip_reason": reason}
        return {"ok": False, "error": f"step '{key}' is not a UI action step"}
    except Exception as exc:
        return {"ok": False, "error": f"init step '{key}' failed: {exc}"}


def _init_plan(browser="firefox"):
    """ctx['init_plan'] wrapper: the wizard's view of the init steps. Preflight is
    dropped — the Control Center has a dedicated Preflight page, and as the one
    step with no persistent done-state it only ever read as 'pending' here — and
    surfaced as a closing reminder instead. (The `racecast init` CLI still runs it.)"""
    opts = {"browser": browser or "firefox", "skip_installs": False, "force": False}
    steps = [s for s in _init_steps(opts) if s["key"] != "preflight"]
    nxt = ins.manual_next_steps(_init_import_json(), _init_companion_cfg())
    nxt.append("Open the Preflight page (left menu) to verify hardware, tools, "
               "and ports before going live.")
    return init_plan_data(steps, ins.STEP_KINDS,
                          browser=opts["browser"], next_steps=nxt)


def _ui_modules():
    """src/ui modules — path-inserted like scripts/ (kept out of the module-level
    insert: only `racecast ui` needs them)."""
    ui_dir = resource_path("ui")
    if ui_dir not in sys.path:
        sys.path.insert(0, ui_dir)
    import ui_jobs, ui_ops, ui_server
    return ui_server, ui_jobs, ui_ops


def _rc_job_executable(frozen=IS_FROZEN, executable=None, win=None):
    """Path to the `racecast` binary that runs Control Center jobs. When the
    server is launched by racecast-ui (a sibling binary), jobs must still invoke
    `racecast`, not racecast-ui. Frozen: the sibling racecast/racecast.exe next
    to the running executable. Dev: the interpreter itself (paired with
    racecast.py by job_argv)."""
    executable = _real_executable() if executable is None else executable
    win = (os.name == "nt") if win is None else win
    if frozen:
        # Join with the TARGET platform's separator (driven by `win`), not the
        # host's: os.path.join emits '\' on a Windows runner and would corrupt
        # the POSIX sibling path on a non-Windows target (and the unit tests).
        sep = "\\" if win else "/"
        return _app_home(executable) + sep + ("racecast.exe" if win else "racecast")
    return executable


def ui_cmd(rest):
    """Run the Control Center web server in the foreground (Ctrl+C stops it).
    Spec: docs/superpowers/specs/2026-06-07-control-center-design.md."""
    return run_ui(rest, fail=sys.exit, open_browser="--no-browser" not in rest)


def run_ui(rest, fail=sys.exit, open_browser=True):
    """Shared Control Center server core for both entrypoints. `fail(msg)` is
    called on a fatal startup error (port taken / bind failure): the CLI passes
    sys.exit; racecast_ui passes a native-dialog variant. Returns None when the
    server has stopped."""
    srv, jobs_mod, ops_mod = _ui_modules()
    for key, val in _read_env_file().items():
        os.environ.setdefault(key, val)        # RACECAST_UI_PORT from .env (env wins)
    port = srv.ui_port(os.environ)
    instance = srv.probe_instance("127.0.0.1", port)
    if instance == "ours":
        print(f"Control Center already running on port {port} — opening the browser.")
        if open_browser:
            _open_url(_http_url("127.0.0.1", port, "/"))
        return None
    if instance == "foreign":
        return fail(f"racecast: port {port} is in use by another application — set "
                    "RACECAST_UI_PORT in .env to a free port and retry.")

    # The GitHub release check is one network round-trip — cache a good result
    # for an hour so the Home dashboard can call it freely (and so we never spam
    # the unauthenticated API into a rate limit). Failures aren't cached.
    _upd = {"at": 0.0, "data": None}

    def update_check_cached(force=False):
        now = time.time()
        if not force and _upd["data"] is not None and now - _upd["at"] <= 3600:
            return _upd["data"]
        fresh = update_check_data()
        if fresh.get("ok"):
            _upd["data"], _upd["at"] = fresh, now
            return fresh
        return _upd["data"] or fresh       # keep the last good result on a failed refresh

    _prev = {"at": 0.0, "data": None}

    def preview_list_cached(force=False):
        now = time.time()
        if not force and _prev["data"] is not None and now - _prev["at"] <= 600:
            return _prev["data"]
        fresh = preview_list_data()
        if fresh.get("ok"):
            _prev["data"], _prev["at"] = fresh, now
            return fresh
        return _prev["data"] or fresh

    # The Google-fonts catalog is a ~2.6 MB fetch -> cache the name list for a day.
    _fontcat = {"at": 0.0, "data": None}

    def font_catalog_cached():
        now = time.time()
        if _fontcat["data"] is not None and now - _fontcat["at"] <= 86400:
            return _fontcat["data"]
        fresh = google_font_catalog_data()
        if fresh.get("source") == "google":
            _fontcat["data"], _fontcat["at"] = fresh, now
        return _fontcat["data"] or fresh

    # Control Center action output -> a timestamped, rotating machine-wide app.log
    # (same logsetup machinery as the relay). to_stdout=False: file only, never
    # double-write to the UI's own console. Prune on start, like the daemons.
    import logsetup
    _app_logger = logsetup.configure_logging("racecast.app", _ui_app_log_path(),
                                             to_stdout=False)
    logsetup.prune_old_logs(_ui_app_log_dir())

    ctx = {
        "version": version(),
        "page_path": resource_path("ui/control-center.html"),
        "favicon_path": resource_path("assets/app-icon.svg"),
        "status": ui_status_payload,
        "relay_live": relay_live_data,
        "event_title_read": event_title_read_data,
        "event_title_write": event_title_write_data,
        "tailscale_peers": _tailscale_peers,
        "obs_collection": obs_collection_data,
        "obs_stream_target": obs_stream_target_data,
        "update_check": update_check_cached,
        "previews": preview_list_cached,
        "streams_read": streams_config_data,
        "streams_write": streams_config_write_data,
        "docs": docs_data,
        "docs_content": docs_content,
        "docs_slides_serve": docs_slides_serve,
        "init_plan": _init_plan,
        "init_step": init_step_action_data,
        "ops": ops_mod.OPS,
        "build_argv": ops_mod.build_argv,
        "assets": assets_status_data,
        "producer_schedule": producer_schedule_data,
        "asset_files": assets_files_data,
        "asset_roots": asset_roots_data,
        "tools": tools_status_data,
        "apps": apps_status_data,
        "preflight": preflight_data,
        "speedtest": speedtest_data,
        "env_read": env_entries_data,
        "env_write": env_write_data,
        "devices_enumerate": devices_enumerate_data,
        "devices_write": devices_write_data,
        "ps_discover": ps_discover_data,
        "ps_write": ps_ip_write_data,
        "profiles": profiles_data,
        "profile_logo": profile_logo,
        "profile_use": profile_use_data,
        "profile_new": profile_new_data,
        "profile_env_read": profile_env_entries_data,
        "profile_env_write": profile_env_write_data,
        "crew_read": crew_entries_data,
        "crew_write": crew_write_data,
        "crew_delete": crew_delete_data,
        "console_status": console_status_data,
        "console_funnel": console_funnel_data,
        "console_set_funnel_auto": console_set_funnel_auto_data,
        "console_revoke": console_revoke_data,
        "console_post_link": console_post_link_data,
        "overlay_read": overlay_read_data,
        "overlay_write": overlay_write_data,
        "overlay_slots": overlay_slots_data,
        "overlay_layout_read": overlay_layout_read_data,
        "overlay_layout_write": overlay_layout_write_data,
        "overlay_fonts": overlay_fonts_list_data,
        "overlay_font_upload": overlay_font_upload_data,
        "overlay_bg": overlay_bg_path,
        "overlay_font_serve": overlay_font_serve,
        "overlay_asset_serve": overlay_asset_serve,
        "machine_fonts": machine_fonts_list_data,
        "font_catalog": font_catalog_cached,
        "machine_font_download": machine_font_download_data,
        "machine_font_delete": machine_font_delete_data,
        "backup_list": backup_list_data,
        "backup_create": backup_create_data,
        "backup_restore": backup_restore_data,
        "backup_delete": backup_delete_data,
        "profile_export": profile_export_data,
        "profile_import": profile_import_data,
        "report_generate": report_generate_data,
        "report_send": report_send_data,
        "jobs": jobs_mod.JobManager(
            lambda op_args: ops_mod.job_argv(op_args, IS_FROZEN,
                                             _rc_job_executable(),
                                             os.path.join(HERE, "racecast.py")),
            env=_frozen_child_env(), logger=_app_logger),
        "log_sources": _log_sources(),
        "resources": resources_data,
    }
    global _resource_monitor
    _resource_monitor = resources.ResourceMonitor(interval=2.0)
    _resource_monitor.start()
    try:
        httpd = srv.serve(ctx, "127.0.0.1", port)
    except OSError as exc:
        return fail(f"racecast: could not bind port {port} ({exc}) — set RACECAST_UI_PORT "
                    "in .env to a free port and retry.")
    url = _http_url("127.0.0.1", port, "/")
    print(f"Control Center: {url}  (Ctrl+C or the Quit button stops it)")
    if open_browser:
        _open_url(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass                    # Ctrl+C is the intended way to stop the server
    finally:
        httpd.server_close()
    print("Control Center stopped — relay/companion/streams keep running.")
    return None


def init_cmd(rest):
    """Guided first-time setup: every automatable step in dependency order,
    pausing only at the manual gates. Spec:
    docs/superpowers/specs/2026-06-06-racecast-init-design.md."""
    try:
        opts = ins.parse_init_args(rest)
    except ValueError as e:
        sys.exit(f"racecast: {e}")
    code, finished = ins.run_wizard(_init_steps(opts), opts["force"], print)
    if finished:   # incl. a preflight FAIL — the machine is set up either way
        print("\nManual next steps:")
        for i, line in enumerate(ins.manual_next_steps(
                _init_import_json(), _init_companion_cfg()), 1):
            print(f"  {i}. {line}")
    raise SystemExit(code)


def _bootstrap(argv):
    """Shared process startup for BOTH binaries: the `racecast` CLI (main) and the
    windowed `racecast-ui` launcher (racecast_ui.main). It lives in one place on
    purpose — the two used to duplicate this sequence and drifted, so the launcher
    shipped without _ensure_tool_path (#46, tools shown missing) and then without
    _apply_active_profile_env (#54, the active profile's SHEET_ID was never injected
    so preflight/asset checks read an empty env). Runs UTF-8 IO setup, .env +
    example-profile seeding, stale-binary cleanup, frozen env load, SSL certs, and
    tool PATH; consumes a global --profile; injects the active profile's league env
    for the in-process providers and any children. Returns argv with --profile
    removed. Raises ValueError on a malformed --profile (each entrypoint renders
    that fatally in its own way: CLI -> stderr exit, launcher -> native dialog)."""
    _force_utf8_io()    # UTF-8 stdout/stderr before anything prints (issue #24)
    home = _app_home(_real_executable())   # plain CLI binary: == dirname(exe)
    ensure_env_file(home)
    ensure_example_profile(home)   # seed profiles/example so `profile new` works (#45)
    ensure_bundled_fonts()         # seed runtime/fonts/ from the bundled curated set
    cleanup_old_binary(home)
    _load_env_frozen()
    _ensure_ssl_certs()
    _ensure_tool_path()    # Finder/Dock launch truncates PATH past Homebrew (#38)
    argv, profile = pa.split_profile_flag(argv)
    if profile:
        os.environ["RACECAST_PROFILE"] = profile   # M3 consumers read this
    _apply_active_profile_env()   # inject the active profile's sheet config for children
    return argv


# ==================== post-update smoke test (#570) ====================
# Verifies that the event core still works after a toolchain update (ffmpeg,
# yt-dlp, streamlink, deno). It drives a REAL event through the normal
# event_start/event_stop path rather than re-implementing the relay's tool
# invocations — a re-implementation would only ever test itself. Pure logic
# (acceptance, the rundown table, verdicts) lives in scripts/smoketest.py.
# Design: docs/superpowers/specs/2026-08-27-post-update-smoketest-design.md

SMOKE_TAB = "Smoke"                 # optional sheet tab overriding the vocabulary
SMOKE_ARM_WAIT_S = 60               # a feed must deliver bytes within this after ARM
SMOKE_STEP_PAUSE_S = 1.5            # let OBS settle before the read-back
SMOKE_READBACK_S = 120              # sheet CSV must show the new URLs within this
SMOKE_SEARCH_LIMIT = 10             # candidates pulled per query before filtering


def _sm():
    """scripts/ is on sys.path (frozen: hidden-import in tools/build-binary.py)."""
    import smoketest
    return smoketest


def _smoke_post_json(url, obj, headers=None, timeout=20):
    """POST + parse. `http_util.post_json` returns the RAW body (only `get_json`
    parses), so every POST site has to decode — the sibling of the existing
    `_relay_post_json`. Callers that treated the bytes as a dict silently turned
    every successful write into a reported failure."""
    body = http_util.post_json(url, obj, headers=headers, timeout=timeout)
    return json.loads(body.decode("utf-8"))


def _smoke_capture(argv, timeout=90):
    """(returncode, combined output) for a probe subprocess. Never raises.

    `env=sv.external_tool_env()` is not optional here: yt-dlp and streamlink run
    under the SYSTEM python, and the frozen bootloader leaves our bundled
    libcrypto on their library path, so they die on import. Without it the
    fingerprint reported both as missing on every frozen run — which is every
    run that matters. Returns None off the frozen binary, leaving source runs
    untouched. Same call the preflight tool check and the daemon spawns make."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           check=False, env=sv.external_tool_env())
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, f"{argv[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, f"{argv[0]}: timed out after {timeout}s"
    except OSError as exc:                       # noqa: BLE001 — probe, never fatal
        return 1, f"{argv[0]}: {exc}"


def _smoke_fingerprint():
    """Tool versions. Not a check — the note a red run gets compared against, so
    "what moved since the last green run" is a diff instead of a guess. The
    JS-runtime line is filled in later by `_smoke_js_runtime`."""
    out = {}
    for name, args in (("yt-dlp", ["--version"]), ("streamlink", ["--version"]),
                       ("ffmpeg", ["-version"]), ("deno", ["--version"])):
        rc, txt = _smoke_capture([name] + args, timeout=30)
        first = (txt.strip().splitlines() or [""])[0].strip()
        # Distinguish absent from broken: reporting a tool that IS installed as
        # "not on PATH" sends the operator hunting for the wrong problem.
        out[name] = first if rc == 0 else ("MISSING" if rc == 127
                                           else f"BROKEN (exit {rc})")
    out["js_runtime"] = "unknown"
    return out


def _smoke_js_runtime(url):
    """yt-dlp's `JS runtimes:` line, read off a stream we ALREADY know is live.

    Deno is the only JS challenge provider on the broadcast box, so if that line
    changes or disappears the YouTube bot-check has no fallback — worth
    recording. Reading it off a discovered URL rather than a hardcoded video id
    keeps the run from depending on one stranger's clip staying up, and spends no
    extra request when discovery found nothing."""
    if not _sm().stream_host(url or ""):
        return "unknown"
    _rc, txt = _smoke_capture(
        ["yt-dlp", "-v", "--simulate", "--no-warnings", "--", url], timeout=60)
    return _sm().parse_js_runtimes(txt) or "unknown"


def _smoke_vocab(sheet_id):
    """(queries, categories) — built-in defaults, overridden by an optional
    `Smoke` tab (`Platform | Query`) so the vocabulary can change without a
    release. A missing tab is normal and silent."""
    sm = _sm()
    queries, categories = list(sm.YOUTUBE_QUERIES), list(sm.TWITCH_CATEGORIES)
    import csv
    if not sheet_id:
        return queries, categories
    try:
        body = http_util.get_bytes(_gviz_csv_url(sheet_id, SMOKE_TAB), timeout=15)
        rows = list(csv.reader(io.StringIO(body.decode("utf-8", "replace"))))
    except Exception:                            # noqa: BLE001 — optional tab
        return queries, categories
    yt, tw = [], []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        platform, value = row[0].strip().lower(), row[1].strip()
        if not value:
            continue
        if platform.startswith("you"):
            yt.append(value)
        elif platform.startswith("twi"):
            tw.append(value)
    return (yt or queries), (tw or categories)


def _smoke_yt_candidates(query):
    """Live-filtered YouTube search, flattened. yt-dlp is the reader, so the
    search path uses the same tool the feed path does."""
    sm = _sm()
    rc, txt = _smoke_capture(
        ["yt-dlp", "--flat-playlist", "--no-warnings", "--playlist-end",
         str(SMOKE_SEARCH_LIMIT), "--print", "%(url)s\t%(title)s\t%(channel)s",
         "--", sm.youtube_live_search_url(query)], timeout=120)
    if rc != 0:
        return []
    out = []
    for line in txt.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].startswith("http"):
            out.append({"url": parts[0], "title": parts[1],
                        "channel": parts[2] if len(parts) > 2 else ""})
    return out


def _smoke_yt_probe(url, cookies):
    """Resolve a candidate in the relay's OWN command form. Passing this is the
    proof that the live path works — it is the exact operation the relay runs on
    air. Returns (height, note)."""
    sm = _sm()
    cmd = ["yt-dlp", "-g", "-f", "b[height<=1080]/b", "--no-warnings",
           "--no-playlist", "--print", "rcq %(height)s %(fps)s"]
    if cookies and os.path.exists(cookies):
        cmd += ["--cookies", cookies]
    cmd += ["--", url]
    rc, txt = _smoke_capture(cmd, timeout=120)
    if rc != 0:
        return None, txt.strip().splitlines()[-1][:160] if txt.strip() else "resolve failed"
    rcq = sm.parse_rcq(txt)
    if rcq:
        return rcq[0], ""
    # Exit 0 with no rcq line: an empty note here let the generic "not live"
    # reason win and hid a parser bug behind a plausible-sounding rejection.
    return None, "yt-dlp exited 0 but printed no rcq line"


def _smoke_twitch_candidates(category):
    """Live channels in a Twitch category via the public web-player Client-ID —
    the same one streamlink embeds, so this needs no API key and no new
    dependency. yt-dlp has no Twitch directory extractor."""
    sm = _sm()
    try:
        data = _smoke_post_json(sm.TWITCH_GQL_URL, sm.twitch_category_query(category),
                                headers={"Client-ID": sm.TWITCH_CLIENT_ID})
        game = ((data or {}).get("data") or {}).get("game") or {}
        edges = (game.get("streams") or {}).get("edges") or []
    except Exception:                            # noqa: BLE001 — discovery is best effort
        return []
    out = []
    for edge in edges:
        node = edge.get("node") or {}
        login = ((node.get("broadcaster") or {}).get("login") or "").strip().lower()
        # The login is interpolated into a URL that reaches streamlink and the
        # league sheet, so it is validated against Twitch's own charset first.
        if login and sm.twitch_login_ok(login):
            out.append({"login": login, "title": node.get("title") or "",
                        "viewers": node.get("viewersCount") or 0})
    return out


def _smoke_twitch_probe(login):
    """streamlink's own view of a channel — plugin, quality ladder, category.
    Pulls no bytes. Returns (plugin, qualities, category, note)."""
    rc, txt = _smoke_capture(
        ["streamlink", "--json", f"https://www.twitch.tv/{login}"], timeout=60)
    try:
        data = json.loads(txt)
    except ValueError:
        return None, [], "", (txt.strip()[:160] or f"streamlink exit {rc}")
    if data.get("error"):
        return None, [], "", str(data["error"])[:160]
    meta = data.get("metadata") or {}
    return (data.get("plugin"), list((data.get("streams") or {}).keys()),
            meta.get("category") or "", "")


def _smoke_discover(cookies, queries, categories, say):
    """(youtube_urls, twitch_urls, attempted_youtube) — at most
    MAX_ATTEMPTS_PER_PLATFORM probes per platform. The cap is the brake: an
    uncapped walk down a poor result list is exactly how the IP gets throttled.

    The attempted URLs come back so the JS-runtime line can be read even when
    every candidate was REJECTED: a failing YouTube resolve is precisely when
    "which JS challenge provider did yt-dlp use" is the question."""
    sm = _sm()
    yt_urls, seen, attempted = [], set(), []
    attempts = 0
    for query in queries:
        if len(yt_urls) >= 2 or attempts >= sm.MAX_ATTEMPTS_PER_PLATFORM:
            break
        for cand in _smoke_yt_candidates(query):
            if len(yt_urls) >= 2 or attempts >= sm.MAX_ATTEMPTS_PER_PLATFORM:
                break
            if cand["url"] in seen:
                continue
            seen.add(cand["url"])
            if not sm.stream_host(cand["url"]):
                continue          # host allow-list, same gate the relay applies
            if not sm.topical_match(f"{cand['title']} {cand['channel']}"):
                continue          # a pre-filter, so an attempt is never spent on a webcam
            attempts += 1
            attempted.append(cand["url"])
            height, note = _smoke_yt_probe(cand["url"], cookies)
            ok, why = sm.accept_youtube(height, cand["title"], cand["channel"])
            # The probe's own error beats the generic reason: "Requested format
            # is not available" (the bot check hiding the formats) reads as "not
            # live" otherwise, and that cost a full diagnosis cycle on the box.
            say(f"  youtube {cand['title'][:44]!r}: "
                + (f"accepted ({height}p)" if ok else f"rejected — {(note or why)!r}"))
            if ok:
                yt_urls.append(cand["url"])
    tw_urls, attempts = [], 0
    cands = []
    for category in categories:
        cands += _smoke_twitch_candidates(category)
    for cand in sm.rank_by_viewers(cands):
        if tw_urls or attempts >= sm.MAX_ATTEMPTS_PER_PLATFORM:
            break
        attempts += 1
        plugin, qualities, category, note = _smoke_twitch_probe(cand["login"])
        ok, why = sm.accept_twitch(plugin, qualities, category)
        say(f"  twitch {cand['login']!r}: "
            + (f"accepted ({sm.ladder_max_height(qualities)}p, {category!r})"
               if ok else f"rejected — {(note or why)!r}"))
        if ok:
            url = f"https://www.twitch.tv/{cand['login']}"
            if sm.stream_host(url):
                tw_urls.append(url)
    return yt_urls, tw_urls, attempted


def _smoke_push(push_url, row, url):
    """One `schedule` webhook write. Its HTTP result is a WEAK signal: Apps
    Script answers through a redirect whose target 404s intermittently AFTER the
    script has already run — observed on the box with the URL sitting in the
    sheet afterwards. So callers must not treat a reported failure as proof that
    nothing was written; `_smoke_write_schedule` confirms against the sheet.

    Writes the URL column ONLY — Streamer and Stint are never sent, mirroring the
    panel's CLEAR URL button ("keep Streamer + Stint so the slot survives").
    Those fields are vocabulary-constrained against the Configuration tab, so a
    discovered channel name is not a legal value there."""
    try:
        body = _smoke_post_json(push_url, {"action": "schedule", "row": int(row),
                                           "url": url}, timeout=30)
    except http_util.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except ValueError:
        # A Google error page instead of JSON: its body can carry script ids and
        # would land in stdout, --json and the history file. Report the shape.
        return False, "webhook did not answer with JSON"
    except Exception as exc:                     # noqa: BLE001 — reported, not raised
        # The CLASS only, never str(exc): http.client.InvalidURL puts the whole
        # Apps Script path — the sheet's write capability — into its message, and
        # it does not inherit from ValueError, so the guard above misses it.
        return False, type(exc).__name__
    if isinstance(body, dict) and body.get("ok"):
        return True, ""
    note = (body or {}).get("error") if isinstance(body, dict) else None
    return False, str(note or "webhook rejected the write")[:160]


def _smoke_schedule_rows(sheet_id):
    """The Schedule tab parsed by csv.reader — the shape smoketest's layout
    helpers expect. Physical, header included; they locate the URL column."""
    import csv
    body = http_util.get_bytes(_gviz_csv_url(sheet_id, "Schedule"), timeout=20)
    return list(csv.reader(io.StringIO(body.decode("utf-8", "replace"))))


def _smoke_await_rows(sheet_id, expected, what):
    """Poll the Schedule tab until it serves EXACTLY `expected` ({row: url}).

    Returns (ok, note). Used twice: once after clearing (every target row must
    read back EMPTY) and once after writing (every row must carry ITS url). The
    cleared-then-written transition is the point — a single end-state check
    cannot tell a successful write from a sheet that already happened to hold
    those URLs, which is what a repeat run against a dead webhook looks like.
    """
    sm = _sm()
    deadline = time.time() + SMOKE_READBACK_S
    while True:
        try:
            served = sm.schedule_urls(_smoke_schedule_rows(sheet_id))
            if sm.rows_match(served, expected):
                return True, ""
        except Exception:                        # noqa: BLE001 — transient, keep polling
            pass
        if time.time() >= deadline:
            return False, (f"the sheet did not serve the {what} rows after "
                           f"{SMOKE_READBACK_S}s")
        time.sleep(5)


def _smoke_write_schedule(push_url, sheet_id, rows, clear, say):
    """Clear the URL column, write the discovered URLs, and CONFIRM both against
    the sheet itself.

    A reported push failure never aborts: the webhook's HTTP result does not say
    whether the write happened (Apps Script answers through a redirect that 404s
    intermittently after the script has run). The sheet is the authority, and it
    is read twice — the cleared rows must come back empty before the write, and
    afterwards every row must carry its own URL. Anything less would let a run
    that changed nothing at all look successful.

    Either confirmation timing out is a hard abort: silently testing whatever the
    previous run left behind is worse than not testing.
    """
    push_notes = []
    for row in clear:
        ok, note = _smoke_push(push_url, row, "")
        if not ok:
            # Kept as diagnostic detail, deliberately not retried — the
            # confirmation below decides whether it actually mattered.
            push_notes.append(f"clear row {row}: {note}")
            say(f"  clearing row {row} reported {note!r} — the sheet decides")
    ok, note = _smoke_await_rows(sheet_id, {row: "" for row in clear}, "cleared")
    if not ok:
        return False, _smoke_note_with(note, push_notes), push_notes
    say("  cleared rows confirmed empty")
    for row, url in rows:
        ok, note = _smoke_push(push_url, row, url)
        if not ok:
            push_notes.append(f"write row {row}: {note}")
            say(f"  write of row {row} reported {note!r} — the sheet decides")
    ok, note = _smoke_await_rows(sheet_id, dict(rows), "written")
    if not ok:
        return False, _smoke_note_with(note, push_notes), push_notes
    say("  schedule read back OK"
        + (" (a push had reported an error)" if push_notes else ""))
    return True, "", push_notes


def _smoke_note_with(note, push_notes):
    return note + (f" ({'; '.join(push_notes)})" if push_notes else "")


def _smoke_error_payload(exc):
    """The relay's own JSON error body out of an HTTPError, else the status.

    It answers a dead OBS with 503 + `{"error": "obs unavailable"}`, but urllib
    raises on 5xx, so without reading the body back the run only ever sees
    "HTTP Error 503" — and `step_error_verdict` could not tell an unreachable OBS
    (a machine fact) from a real failure."""
    try:
        payload = json.loads(exc.read().decode("utf-8"))
    except Exception:                            # noqa: BLE001 — not JSON, use the code
        payload = None
    if isinstance(payload, dict) and payload.get("error"):
        return {"error": str(payload["error"])[:160]}
    return {"error": f"HTTP {exc.code}"}


def _smoke_relay_get(path, timeout=15):
    try:
        return http_util.get_json(f"http://127.0.0.1:{RELAY_PORT}/{path}", timeout=timeout)
    except http_util.HTTPError as exc:
        return _smoke_error_payload(exc)
    except Exception as exc:                     # noqa: BLE001 — reported as a check
        return {"error": str(exc)[:160]}


def _smoke_relay_post(path, body, timeout=20):
    try:
        return _smoke_post_json(f"http://127.0.0.1:{RELAY_PORT}/{path}", body,
                                timeout=timeout)
    except http_util.HTTPError as exc:
        return _smoke_error_payload(exc)
    except Exception as exc:                     # noqa: BLE001 — reported as a check
        return {"error": str(exc)[:160]}


def _smoke_program_audio_sample(want=16384, timeout=25):
    """A BOUNDED read of the endless MP3 stream: enough bytes to prove the real
    ffmpeg encoder produced frames, then disconnect. Reading to EOF would never
    return, and `?probe=1` only reports that the endpoint exists — it explicitly
    does not start the encoder, which is the thing an ffmpeg major bump puts in
    doubt. Returns (bytes, note)."""
    url = f"http://127.0.0.1:{RELAY_PORT}/preview/program-audio"
    try:
        with http_util.open_url(url, timeout=timeout) as resp:
            return resp.read(want) or b"", ""
    except http_util.HTTPError as exc:
        return b"", f"HTTP {exc.code}"
    except Exception as exc:                     # noqa: BLE001 — reported as a check
        return b"", str(exc)[:120]


def _smoke_feed_bytes(which):
    """True when the relay reports this feed actually delivering (shape handled
    by the tested smoketest.feed_serving)."""
    return _sm().feed_serving(_smoke_relay_get("status"), which)


def _smoke_wait_bytes(which, say):
    deadline = time.time() + SMOKE_ARM_WAIT_S
    while time.time() < deadline:
        if _smoke_feed_bytes(which):
            return True
        time.sleep(3)
    say(f"  feed {which} delivered no bytes within {SMOKE_ARM_WAIT_S}s")
    return False


def _smoke_apply(step):
    """Send one rundown step. Scene first, then visibility, then audio — the
    order the panel's macros use. A relay-resolved SPLIT (#591) or STINT sends its
    sources as one call, like the panel and the Companion buttons."""
    if step.relay:
        return _smoke_relay_get(step.relay)
    if step.scene and step.kind == "macro":
        _smoke_relay_post("obs/scene", {"scene": step.scene})
    if step.relay_split:
        return _smoke_relay_post("obs/split", {})
    if step.relay_stint:
        return _smoke_relay_post("obs/stint", {"feed": step.relay_stint})
    for scene, source in step.show:
        _smoke_relay_post("obs/source", {"scene": scene, "source": source, "on": True})
    for scene, source in step.hide:
        _smoke_relay_post("obs/source", {"scene": scene, "source": source, "on": False})
    for name in step.unmute:
        _smoke_relay_post("obs/audio", {"input": name, "mute": False})
    for name in step.mute:
        _smoke_relay_post("obs/audio", {"input": name, "mute": True})
    return {"ok": True}


def _smoke_rundown(say):
    """Run the director rundown, reading OBS back after every step."""
    sm = _sm()
    results, on_air = [], "Feed A"
    # Manual arm is a machine setting (`RACECAST_MANUAL_FEED_ARM`). With it off the
    # feeds pre-warm themselves and the relay REFUSES feed/X/activate, so the arm
    # call is skipped and the byte wait alone decides — see sm.arm_verdict.
    manual_arm = bool(_smoke_relay_get("status").get("manual_feed_arm"))
    if not manual_arm:
        say("  note: manual feed arm is off — feeds self-arm, ARM steps skip the call")
    for i, step in enumerate(sm.RUNDOWN, start=1):
        name = f"step{i:02d}_{step.label.lower().replace(' ', '_')}"
        if step.label == "STINT A":
            on_air = "Feed A"
        elif step.label == "STINT B":
            on_air = "Feed B"
        res = {} if step.kind == "arm" and not manual_arm else _smoke_apply(step)
        if isinstance(res, dict) and res.get("error"):
            status, note = sm.step_error_verdict(res["error"])
            results.append(sm.Result(name, status, note))
            say(f"  {step.label}: {status} — {note}")
            continue
        if step.wait_for_bytes:
            which = "B" if "B" in step.label else "A"
            ok = _smoke_wait_bytes(which, say)
            status, note = sm.arm_verdict(manual_arm, ok)
            results.append(sm.Result(name, status, note))
            say(f"  {step.label}: " + ("ok" if ok else "NO BYTES"))
            # An ARM step is fully judged here. Falling through reached the
            # scene-less branch below and appended a SECOND result under the same
            # name — every arm was counted twice, and a failing one would have
            # been half-reported as a PASS.
            continue
        if step.label == "NEXT":
            # The handover moves the on-air feed; confirm the relay agrees rather
            # than assuming the rundown's bookkeeping is right.
            time.sleep(SMOKE_STEP_PAUSE_S)
            live = sm.on_air_feed(_smoke_relay_get("status"))
            moved = live == "Feed B"
            if live:
                on_air = live
            results.append(sm.Result(name, sm.PASS if moved else sm.FAIL,
                                     f"on air: {live or 'unknown'}"))
            say(f"  NEXT: on air is now {live or 'unknown'}")
            continue
        if not step.scene:
            results.append(sm.Result(name, sm.PASS, ""))
            continue
        time.sleep(SMOKE_STEP_PAUSE_S)
        observed = _smoke_relay_post("obs/state", sm.state_probe(step, on_air))
        if observed.get("error"):
            results.append(sm.Result(name, sm.severity_for("obs_standby", False),
                                     f"OBS unreadable: {observed['error']}"))
            continue
        bad = sm.state_mismatches(sm.expected_after(step, on_air), observed)
        results.append(sm.Result(name, sm.PASS if not bad else sm.FAIL,
                                 "; ".join(bad)[:200]))
        say(f"  {step.label}: " + ("ok" if not bad else "MISMATCH — " + "; ".join(bad)))
    return results


def _smoke_observe(minutes, say):
    """Watch the live relay for the observation window. Feed bytes, HUD, program
    audio and the feed logs are toolchain-attributable and fail hard; OBS and
    Companion degrade to a warning (a headless session is not a regression), and a
    missing program-audio endpoint skips (see `program_audio_verdict`)."""
    sm = _sm()
    results = []
    mp3, note = _smoke_program_audio_sample()
    status, detail = sm.program_audio_verdict(len(mp3), note)
    results.append(sm.Result("program_audio", status, detail))
    hud = _smoke_relay_get("hud/data")
    results.append(sm.Result("hud_data", sm.severity_for("hud_data",
                                                         bool(hud.get("teams") is not None)),
                             str(hud.get("error") or "")[:120]))
    deadline = time.time() + minutes * 60
    drops = 0
    say(f"  observing for {minutes} min…")
    while time.time() < deadline:
        if sm.is_drop_sample(_smoke_relay_get("status")):
            drops += 1
        time.sleep(15)
    results.append(sm.Result("health_no_drop", sm.severity_for("health_no_drop",
                                                               drops == 0),
                             "" if drops == 0 else f"{drops} DROP sample(s)"))
    for which in ("A", "B"):
        ok = _smoke_feed_bytes(which)
        results.append(sm.Result(f"feed_{which.lower()}_bytes",
                                 sm.severity_for(f"feed_{which.lower()}_bytes", ok),
                                 "" if ok else "no bytes at end of window"))
    return results


def _smoke_history_path():
    return os.path.join(_runtime_dir(), "smoketest-history.jsonl")


def _smoke_append_history(entry):
    """One JSONL line per run, like runtime/speedtest-history.jsonl. Best effort:
    a run that produced a verdict must not fail because the log is unwritable."""
    try:
        os.makedirs(os.path.dirname(_smoke_history_path()), exist_ok=True)
        with open(_smoke_history_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:                       # noqa: BLE001 — never fatal
        print(f"smoketest: history not written ({exc})")


def _smoke_minutes(rest):
    sm = _sm()
    if "--minutes" not in rest:
        return sm.DEFAULT_MINUTES
    i = rest.index("--minutes")
    if i + 1 >= len(rest):
        raise SystemExit("racecast: --minutes requires an integer value")
    try:
        return max(1, min(120, int(rest[i + 1])))
    except ValueError:
        # `raise` rather than sys.exit(): identical behaviour, but it makes the
        # path explicitly terminating (CodeQL py/mixed-returns).
        raise SystemExit(
            f"racecast: --minutes must be an integer, got {rest[i + 1]!r}") from None


def _smoke_confirm(profile, rest):
    """The typed phrase names the profile on purpose: the accident this guards
    against is the right command in the WRONG league. There is deliberately no
    --yes bypass — a bypass flag lands in a wrapper script and the guard becomes
    ceremony."""
    sm = _sm()
    phrase = sm.confirm_phrase(profile)
    # stderr, not stdout: --json advertises machine-readable output and a prompt
    # in the middle of it is not parseable.
    print(f"\nThis CLEARS the URL column of the '{profile}' Schedule tab and "
          f"overwrites its first stint rows.\nType exactly:  {phrase}",
          file=sys.stderr)
    try:
        print("> ", end="", flush=True, file=sys.stderr)
        typed = input()
    except (EOFError, KeyboardInterrupt):
        typed = ""
    if not sm.phrase_ok(profile, typed):
        sys.exit("racecast: not confirmed — nothing was changed.")


def smoketest_cmd(rest):
    """`racecast smoketest` — post-update verification of the event core.

    Stands up a real event on the active profile with discovered live sources,
    drives the director rundown against it and asserts every step, then tears the
    event down again. See the design spec for why this drives the production path
    instead of re-implementing the tool invocations."""
    sm = _sm()
    rest = list(rest)
    as_json = "--json" in rest
    minutes = _smoke_minutes(rest)
    lines = []

    def say(msg):
        lines.append(msg)
        if not as_json:
            print(msg, flush=True)

    profile = _active_profile_name()
    if not profile:
        # The typed phrase names the profile — that IS the guard. A generic
        # fallback name would let you confirm "CLEAR SCHEDULE default" while
        # standing in a real league, which is the exact accident being guarded
        # against. No resolvable profile, no run.
        sys.exit("racecast: no active league profile — pick one with "
                 "`racecast profile use <name>` or --profile NAME.")
    sheet_id = os.environ.get("RACECAST_SHEET_ID") or ""
    push_url = os.environ.get("RACECAST_SHEET_PUSH_URL") or ""
    if not push_url:
        sys.exit("racecast: this profile has no SHEET_PUSH_URL — the smoke test "
                 "needs the webhook to place its discovered streams "
                 "(wiki: Sheet-Webhook).")
    # Never probe outward while a broadcast is live: the run pulls real streams
    # and a 429 mid-event is the most expensive way to lose trust in a tool.
    if "--force" not in rest and (_relay_is_alive()
                                  or glob.glob(os.path.join(_streams_static_dir(),
                                                            "feed_*.pid"))):
        sys.exit("racecast: a relay or static streams are running — refusing "
                 "(this would pull streams and switch OBS). Use --force if you "
                 "are sure nothing is on air.")
    _smoke_confirm(profile, rest)
    # The confirmation blocks on input() for an unbounded time. Re-check before
    # the destructive write: a broadcast that started in the meantime must not
    # have its schedule cleared.
    if "--force" not in rest and _relay_is_alive():
        sys.exit("racecast: a relay came up while the confirmation was pending "
                 "— refusing to touch the schedule.")

    results = []
    say(f"\nSmoke test — profile '{profile}', {minutes} min observation\n")
    say("Toolchain")
    tools = _smoke_fingerprint()
    for name, ver in tools.items():
        say(f"  {name:<11s} {ver}")
        if ver == "MISSING" or ver.startswith("BROKEN"):
            results.append(sm.Result(f"tool_{name}", sm.FAIL,
                                     "not on PATH" if ver == "MISSING"
                                     else "on PATH but fails to run"))

    say("\nDiscovery")
    cookies = _cookies_path()
    if not os.path.isfile(cookies):
        # Without the jar YouTube's bot check hides every format and each probe
        # fails as "not available" — a setup fact worth naming before the run
        # spends its three attempts discovering it the hard way.
        results.append(sm.Result("cookies", sm.severity_for("cookies", False),
                                 "no YouTube cookie jar — resolves will hit the "
                                 "bot check (racecast cookies firefox)"))
        say("  note: no yt-cookies.txt — YouTube candidates will not resolve")
    queries, categories = _smoke_vocab(sheet_id)
    yt_urls, tw_urls, attempted_yt = _smoke_discover(cookies, queries, categories, say)
    # Read this BEFORE the skip branch: when YouTube found nothing, the JS runtime
    # is the first thing to look at, and reporting it only on success hides it in
    # exactly the case that needs it.
    tools["js_runtime"] = _smoke_js_runtime((yt_urls or attempted_yt or [""])[0])
    say(f"  js_runtime  {tools['js_runtime']}")
    # The stint rows come from the sheet, never from an assumption: a Schedule
    # tab with a `URL` header starts its data at physical row 2, and writing to
    # row 1 overwrites that header (which also drops the tab out of header mode).
    try:
        schedule_rows = _smoke_schedule_rows(sheet_id)
        data_rows = sm.schedule_data_rows(schedule_rows)
    except Exception as exc:                     # noqa: BLE001 — reported as a check
        results.append(sm.Result("sheet_layout", sm.FAIL, str(exc)[:160]))
        return _smoke_finish(results, tools, [], minutes, as_json, lines)
    if len(data_rows) < len(sm.SOURCE_PLAN):
        results.append(sm.Result("sheet_layout", sm.FAIL,
                                 f"the Schedule tab has {len(data_rows)} stint "
                                 f"row(s), need {len(sm.SOURCE_PLAN)}"))
        return _smoke_finish(results, tools, [], minutes, as_json, lines)
    rows = sm.plan_rows(yt_urls, tw_urls, data_rows)
    if rows is None:
        # Not a failure: "nobody suitable is streaming right now" is a fact about
        # the world, and reddening for it teaches the operator to ignore red.
        results.append(sm.Result("discovery", sm.SKIP,
                                 f"found {len(yt_urls)} YouTube + {len(tw_urls)} "
                                 f"Twitch, need 2 + 1"))
        return _smoke_finish(results, tools, [], minutes, as_json, lines)
    sources = [{"row": r, "url": u, "platform": sm.platform_of(u)}
               for r, u in rows]
    results.append(sm.Result("discovery", sm.PASS,
                             ", ".join(f"row {s['row']}: {s['platform']}" for s in sources)))

    say("\nSheet")
    # The webhook writes `colOf('url') || 1`: with a `URL` header its column,
    # WITHOUT one always column A. A tab whose stream column is elsewhere would
    # get column A blanked — real data — while the read-back watches the other
    # column and never matches. Refuse before anything is written.
    layout_note = sm.writable_layout_note(schedule_rows)
    if layout_note:
        results.append(sm.Result("sheet_layout", sm.FAIL, layout_note))
        return _smoke_finish(results, tools, [], minutes, as_json, lines)
    clearing = sm.clear_rows(data_rows)
    # The rows are meant to STAY after a run, so nothing is restored — but the
    # cleared cells are gone for good (one row is cleared and never rewritten).
    # Print them and put them in the history so a manual restore is possible.
    served = sm.schedule_urls(schedule_rows)
    cleared = {r: served.get(r, "") for r in clearing if served.get(r)}
    if cleared:
        say("  previous URLs (kept only in the history file):")
        for row in sorted(cleared):
            say(f"    row {row}: {cleared[row]}")
    ok, note, push_notes = _smoke_write_schedule(push_url, sheet_id, rows,
                                                 clearing, say)
    if not ok:
        results.append(sm.Result("sheet_write", sm.FAIL, note))
        return _smoke_finish(results, tools, sources, minutes, as_json, lines, cleared)
    # The push errors belong in the RESULT, not only in say(): say() is silent
    # under --json, so a machine-readable run would never learn that the webhook
    # complained while the sheet came out right.
    results.append(sm.Result("sheet_write", sm.PASS,
                             "URL column only, rows "
                             + ", ".join(str(r) for r, _u in rows)
                             + (f"; pushes reported: {'; '.join(push_notes)}"
                                if push_notes else "")))

    try:
        import tailscale as _ts
        funnel_was_off = not _ts.funnel_on()
    except Exception:                            # noqa: BLE001 — best effort
        funnel_was_off = False
    title = "Smoketest " + time.strftime("%Y-%m-%d %H:%M")
    say(f"\nEvent — starting as {title!r}")
    # The teardown below is UNCONDITIONAL: `event start` brings services up one by
    # one and exits non-zero when its readiness report has a FAIL, so a partial
    # stack (relay, Companion, an enabled Funnel) is already live by then. Arming
    # the teardown only on a successful bring-up once left all three running —
    # including the PUBLIC Funnel — with no verdict printed at all.
    aborted = ""
    try:
        try:
            event_start(["--title", title])
        except SystemExit as exc:
            # event_start ALWAYS ends through event_status, which exits 0 when the
            # stack is ready and 1 when FAILs remain — so only a NON-ZERO code is
            # an aborted bring-up. Treating every SystemExit as one skipped the
            # entire rundown on a perfectly healthy stack.
            if exc.code not in (0, None):
                aborted = str(exc.code)[:160] or "NOT READY"
        if aborted:
            results.append(sm.Result("event_start", sm.FAIL, aborted))
            say("  event start reported NOT READY — skipping the rundown")
        else:
            say("\nRundown")
            results += _smoke_rundown(say)
            say("\nObservation")
            results += _smoke_observe(minutes, say)
    finally:
        # An aborted run must never leave a live relay, an open Funnel or a
        # switched OBS behind.
        say("\nEvent — stopping")
        try:
            event_stop(["--no-report"] if "--no-report" in rest else [])
        except (Exception, SystemExit) as exc:   # noqa: BLE001 — SystemExit incl.
            # A teardown failure has to reach the VERDICT, not just the console:
            # say() is suppressed under --json, so reporting it there alone let a
            # run print PASS and exit 0 with the relay still pulling streams.
            # KeyboardInterrupt is deliberately NOT caught — Ctrl+C during a
            # hung teardown must abort, not turn into a note.
            results.append(sm.Result("teardown", sm.FAIL, str(exc)[:160]
                                     or "event stop failed"))
            say(f"  teardown FAILED: {exc}")
        # `event start` turns the Funnel on by default, and no stop path turns it
        # off — a maintenance command must not leave public ingress behind that
        # it opened itself. Only revert what THIS run switched on.
        if funnel_was_off:
            try:
                import tailscale as _ts
                if _ts.funnel_on():
                    funnel_cmd(["off"])
                    say("  funnel closed again (this run had opened it)")
            except (Exception, SystemExit) as exc:   # noqa: BLE001 — best effort
                say(f"  funnel note: {exc}")
    return _smoke_finish(results, tools, sources, minutes, as_json, lines, cleared)


def _smoke_finish(results, tools, sources, minutes, as_json, lines, cleared=None):
    """Render the verdict, append the history line, return the exit code."""
    sm = _sm()
    summary = sm.summarize(results)
    entry = sm.history_entry(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), summary["verdict"], tools, sources,
                             minutes, results, cleared=cleared)
    _smoke_append_history(entry)
    if as_json:
        print(json.dumps({**entry, "counts": summary["counts"]}, ensure_ascii=False))
    else:
        print("\nChecks")
        for r in results:
            print(f"  [{r.severity}] {r.name}" + (f" — {r.note}" if r.note else ""))
        c = summary["counts"]
        print(f"\nSummary: {c[sm.FAIL]} FAIL, {c[sm.WARN]} WARN, {c[sm.SKIP]} SKIP, "
              f"{c[sm.PASS]} PASS  ->  {summary['verdict']}")
        print(f"History: {_smoke_history_path()}")
    # main() does not act on a returned value (see `oneshot`, which raises), so a
    # FAIL has to exit non-zero itself — a check that always exits 0 is not a check.
    raise SystemExit(sm.exit_code(summary["verdict"]))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        argv = _bootstrap(argv)
    except ValueError as e:
        sys.exit(f"racecast: {e}")
    try:
        action = route(argv)
    except ValueError as e:
        sys.exit(f"racecast: {e}")
    if action["kind"] == "help":
        print(USAGE)
        return None
    if action["kind"] == "version":
        print(f"racecast {version()}")
        return None
    if action["kind"] == "export":
        export_companion(action["rest"])
        return None
    if action["kind"] == "service":
        fn = DISPATCH.get((action["command"], action["verb"]))
        if not fn:
            sys.exit(f"racecast: {action['command']} {action['verb']} not implemented yet")
        return fn(action["rest"])
    if action["kind"] == "ui":
        return ui_cmd(action["rest"])
    if action["kind"] == "freeport":
        return freeport_cmd(action["rest"])
    if action["kind"] == "device-scan":
        return device_scan_cmd(action["rest"])
    if action["kind"] == "gt7-discover":
        return gt7_discover_cmd(action["rest"])
    if action["kind"] == "init":
        return init_cmd(action["rest"])
    if action["kind"] == "profile":
        return profile_cmd(action["rest"])
    if action["kind"] == "chat":
        return chat_cmd(action["rest"])
    if action["kind"] == "discord":
        return discord_cmd(action["rest"])
    if action["kind"] == "health":
        return health_cmd(action["rest"])
    if action["kind"] == "report":
        return report_cmd(action["rest"])
    if action["kind"] == "console":
        return console_cmd(action["rest"])
    if action["kind"] == "funnel":
        return funnel_cmd(action["rest"])
    if action["kind"] == "links":
        return links_cmd(action["rest"])
    if action["kind"] == "smoketest":
        return smoketest_cmd(action["rest"])
    if action["kind"] == "backup":
        return backup_cmd(action["rest"])
    if action["kind"] == "oneshot":
        return oneshot(action["command"], action["rest"])
    if action["kind"] == "aggregate":
        aggregate_status()
        return None
    sys.exit(f"racecast: {action['kind']} not implemented")


if __name__ == "__main__":
    main()

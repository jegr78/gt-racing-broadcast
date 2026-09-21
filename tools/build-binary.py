#!/usr/bin/env python3
"""Build the standalone `racecast` and `racecast-ui` binaries with PyInstaller
and smoke-test both. One pair of binaries per OS, so run this on the OS you are
targeting; CI runs a 3-OS matrix.
Usage: python3 tools/build-binary.py [--version vX.Y.Z] [--skip-smoke]
Output: dist/bin/racecast + dist/bin/racecast-ui (plus .exe on Windows and
racecast-ui.app on macOS). The producer ZIP package is a separate artifact
built by tools/build.py."""
import argparse, os, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
# Bundled data, laid out under _MEIPASS/src/ so every script's here-relative
# path resolution (hud.html, assets/, OBS template) keeps working unchanged.
DATA = ["relay", "scripts", "obs", "assets", "companion", "director", "cockpit", "console",
        "racecontrol", "ui", "setup-assets.py"]
# The operator docs the Control Center's Help page serves (racecast.DOCS_FILES),
# and only these. The docs/wiki/ subtree stays on GitHub and Help links to it.
DOC_FILES = ["docs/Broadcast_Setup_Guide.md", "docs/README_SETUP.md"]
# The onboarding decks ship as a static tree so the Control Center serves them
# offline at /docs/slides/; the GitHub Pages copy is the online default.
SLIDES_DATA = "docs/slides"

# The bundled scripts are loaded at runtime via importlib, so PyInstaller's static
# analyser cannot see their imports. List every stdlib module they use that
# racecast.py's own imports do not already guarantee.
HIDDEN_STDLIB = [
    # racecast-feeds.py
    "http.server", "ipaddress",
    # racecast-feeds.py + get-graphics.py + get-media.py
    "urllib.parse", "urllib.request",
    # preflight.py
    "ctypes", "dataclasses", "socket",
    # hud.html data endpoint (racecast-feeds.py uses csv, io at module level)
    "csv", "io",
    # ui_jobs.py (loaded via importlib through ui_server.py)
    "uuid",
]


def _icon_arg(platform=None, osname=None, exists=None):
    """PyInstaller --icon for the current OS, or [] when none applies. macOS uses
    the .icns for the .app bundle and Dock, Windows the .ico for the .exe; Linux
    cannot embed an icon into an ELF, so it gets none. A missing committed icon
    file is non-fatal and the binary builds without one; regenerate it with
    tools/make-icons.py. (#58)"""
    platform = sys.platform if platform is None else platform
    osname = os.name if osname is None else osname
    if platform == "darwin":
        path = os.path.join(SRC, "assets", "app-icon.icns")
    elif osname == "nt":
        path = os.path.join(SRC, "assets", "app-icon.ico")
    else:
        return []
    exists = os.path.isfile if exists is None else exists
    return ["--icon", path] if exists(path) else []


def _pyinstaller_cmd():
    """Return the PyInstaller invocation as a list. Prefers the `pyinstaller`
    executable on PATH and falls back to `python3 -m PyInstaller` when the module
    is importable but the wrapper script is not on PATH, which is common after a
    --user pip install on macOS."""
    if shutil.which("pyinstaller"):
        return ["pyinstaller"]
    try:
        import PyInstaller  # noqa: F401 (importability check only)
        return [sys.executable, "-m", "PyInstaller"]
    except ImportError:
        pass
    return None


def build_target(launcher, workdir, version_file, sep, entry, name, windowed):
    """Run PyInstaller for one entrypoint. `windowed` builds a no-console app: no
    console window on Windows, an .app bundle on macOS, ignored on Linux. Returns
    the path to the built executable."""
    cmd = launcher + ["--onefile", "--name", name, "--clean", "--noconfirm",
           "--distpath", os.path.join(ROOT, "dist", "bin"),
           "--workpath", os.path.join(workdir, "build", name),
           "--specpath", workdir,
           # racecast.py imports these modules, several of them only
           # function-locally, which PyInstaller's static scan misses. Without the
           # listing the frozen binary raises ModuleNotFoundError at runtime.
           # Guarded by tests/test_racecast.py::t_function_local_peer_imports_are_frozen.
           "--paths", os.path.join(SRC, "scripts"),
           "--hidden-import", "services", "--hidden-import", "companion_common",
           "--hidden-import", "companion_linux",
           "--hidden-import", "event", "--hidden-import", "preflight",
           "--hidden-import", "speedtest", "--hidden-import", "smoketest",
           "--hidden-import", "obs_benchmark",
           "--hidden-import", "install_apps", "--hidden-import", "obs_ws",
           "--hidden-import", "overlay_build",
           # Imported by src/ui/ui_server.py, which is itself loaded by path, so
           # PyInstaller's scan never sees it. Guarded by
           # tests/test_racecast.py::t_path_loaded_module_imports_are_frozen.
           "--hidden-import", "bundle_cache",
           "--hidden-import", "placeholders",
           "--hidden-import", "tailscale", "--hidden-import", "init_setup",
           "--hidden-import", "native_dialog",
           "--hidden-import", "backup_admin", "--hidden-import", "profile_io",
           "--hidden-import", "install_tools", "--hidden-import", "update",
           "--hidden-import", "discord_web",
           "--hidden-import", "funnel_setup",
           "--hidden-import", "producer",
           "--hidden-import", "broadcast_chat", "--hidden-import", "stream_target",
           "--hidden-import", "logsetup",
           "--hidden-import", "discord_rpc", "--hidden-import", "gt7_discovery",
           "--add-data", f"{version_file}{sep}src"]
    cmd += _icon_arg()      # the racecast "rc" app icon (#58)
    if windowed:
        cmd += ["--windowed"]
    for mod in HIDDEN_STDLIB:
        cmd += ["--hidden-import", mod]
    for rel in DATA:
        path = os.path.join(SRC, rel)
        # --add-data's DEST is always a target directory. A directory source
        # mirrors into src/<rel>, but a file must target "src": "src/<file>" would
        # create a directory named like the file, and the frozen in-process import
        # then dies with EACCES trying to open() it.
        dest = f"src/{rel}" if os.path.isdir(path) else "src"
        cmd += ["--add-data", f"{path}{sep}{dest}"]
    # Bundle the Help page's docs under src/docs/, a real directory DEST the file
    # lands inside, so resource_path("docs/<f>") finds them.
    for rel in DOC_FILES:
        cmd += ["--add-data", f"{os.path.join(SRC, rel)}{sep}src/docs"]
    # The onboarding decks tree mirrors into src/docs/slides, so
    # resource_path("docs/slides") resolves inside the frozen bundle and the
    # Control Center can serve the decks offline.
    cmd += ["--add-data",
            f"{os.path.join(SRC, SLIDES_DATA)}{sep}src/docs/slides"]
    # profiles/example/ is the league template `racecast profile new` copies from.
    # It sits at the repo root rather than under src/, so it is bundled explicitly
    # and racecast.ensure_example_profile() unpacks it next to the binary on first
    # run. The release archive ships only the binaries and .env.example, and
    # `racecast update` swaps just the binary. (#45)
    cmd += ["--add-data",
            f"{os.path.join(ROOT, 'profiles', 'example')}{sep}profiles/example"]
    # fonts.zip carries the curated overlay-font set that
    # racecast.ensure_bundled_fonts() extracts into runtime/fonts/ on first start.
    # It goes to the _MEIPASS root so it travels inside the binary and survives the
    # binary-only swap `racecast update` performs.
    fonts_zip = os.path.join(ROOT, "fonts.zip")
    if os.path.isfile(fonts_zip):
        cmd += ["--add-data", f"{fonts_zip}{sep}."]
    cmd.append(os.path.join(SRC, entry))
    print("Running:", " ".join(cmd), flush=True)
    if subprocess.call(cmd) != 0:
        sys.exit(f"pyinstaller failed for {name}.")
    ext = ".exe" if os.name == "nt" else ""
    binary = os.path.join(ROOT, "dist", "bin", name + ext)
    if not os.path.isfile(binary):
        sys.exit(f"expected binary missing: {binary}")
    print(f"Built {binary} ({os.path.getsize(binary) // (1024 * 1024)} MB)")
    return binary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="dev")
    ap.add_argument("--skip-smoke", action="store_true")
    a = ap.parse_args()
    launcher = _pyinstaller_cmd()
    if launcher is None:
        sys.exit("pyinstaller not found (pip install pyinstaller / brew install pyinstaller).")
    workdir = tempfile.mkdtemp(prefix="racecast-build-")
    version_file = os.path.join(workdir, "VERSION")
    with open(version_file, "w", encoding="utf-8") as fh:
        fh.write(a.version + "\n")
    sep = ";" if os.name == "nt" else ":"
    fonts_zip = os.path.join(ROOT, "fonts.zip")
    if not os.path.isfile(fonts_zip):
        print("fonts.zip missing, fetching the curated overlay-font set...", flush=True)
        if subprocess.call([sys.executable, os.path.join(ROOT, "tools", "fetch-fonts.py"),
                            "--version", a.version]) != 0:
            sys.exit("fetch-fonts failed (network?), cannot bundle overlay fonts.")
    rc_bin = build_target(launcher, workdir, version_file, sep,
                           "racecast.py", "racecast", windowed=False)
    ui_bin = build_target(launcher, workdir, version_file, sep,
                          "racecast_ui.py", "racecast-ui", windowed=True)
    if not a.skip_smoke:
        smoke(rc_bin, a.version)
        smoke_ui(ui_bin)
        # macOS ships the windowed launcher as racecast-ui.app, so smoke its inner
        # executable too. The .app nests the exe three levels deep, which changes
        # sibling-racecast resolution; a job spawn through it is the regression
        # guard for "Contents/MacOS/racecast not found", which ui_bin cannot catch.
        app_exe = os.path.join(ROOT, "dist", "bin", "racecast-ui.app",
                               "Contents", "MacOS", "racecast-ui")
        if os.path.isfile(app_exe):
            smoke_ui(app_exe)


def smoke_ui(binary):
    """The windowed launcher must bind, answer the ping with the Control Center
    signature, run a job through the sibling `racecast` binary, and quit. There is
    no --version check because a windowed Windows build has no stdout. The sibling
    `racecast` sits next to this binary in dist/bin/, so the job spawn exercises
    _rc_job_executable."""
    import json
    import time
    import urllib.request
    env = os.environ.copy()
    env["RACECAST_UI_PORT"] = "8390"
    ui = subprocess.Popen([binary, "--no-browser"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def _get(path):
        with urllib.request.urlopen(f"http://127.0.0.1:8390{path}", timeout=2) as r:
            return r.read()

    def _post(path):
        return urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:8390{path}", method="POST", data=b"{}"),
            timeout=5).read()

    try:
        body = b""
        for _ in range(20):                  # up to ~10 s to bind
            time.sleep(0.5)
            try:
                body = _get("/api/ping")
                break
            except OSError:
                if ui.poll() is not None:
                    break
        if b"racecast-control-center" not in body:
            out = ui.stdout.read().decode("utf-8", "replace") if ui.poll() is not None else ""
            sys.exit(f"smoke racecast-ui FAILED: no Control Center ping on :8390 "
                     f"(rc={ui.poll()}) out={out!r}")
        # Start the read-only preflight job and confirm it spawns and completes,
        # which proves racecast-ui spawns the sibling `racecast` binary, not itself.
        # /api/jobs/<id> returns {"ok": True, **snapshot}, and a job is done when
        # exit_code is not None; there is no "done" key.
        job = json.loads(_post("/api/op/preflight"))
        if not job.get("ok") or not job.get("job_id"):
            sys.exit(f"smoke racecast-ui FAILED: could not start preflight job ({job!r})")
        jid, snap = job["job_id"], {}
        for _ in range(60):                  # up to ~30 s for preflight to finish
            time.sleep(0.5)
            try:
                snap = json.loads(_get(f"/api/jobs/{jid}"))
            except OSError:                  # transient localhost timeout on a busy
                continue                     # runner; retry, don't abort the build
            if snap.get("exit_code") is not None:
                break
        if snap.get("exit_code") is None:
            sys.exit(f"smoke racecast-ui FAILED: preflight job never finished ({snap!r})")
        _post("/api/quit")
        ui.wait(timeout=10)
    finally:
        if ui.poll() is None:
            ui.kill()
    print("Smoke test OK (racecast-ui: ping, sibling-racecast job, quit).")


def smoke(binary, version):
    """The binary must self-report the version, print aggregate status and export
    the Companion config, which proves bundled data and frozen dispatch work."""
    def run(args):
        return subprocess.run([binary] + args, capture_output=True, text=True, timeout=60)

    out = run(["--version"])
    if out.returncode != 0 or version not in out.stdout:
        sys.exit(f"smoke --version FAILED: rc={out.returncode} out={out.stdout!r} err={out.stderr!r}")
    st = run(["status"])
    if st.returncode != 0 or "relay" not in st.stdout:
        sys.exit(f"smoke status FAILED: rc={st.returncode} out={st.stdout!r} err={st.stderr!r}")
    ev = run(["event", "status"])
    if ev.returncode not in (0, 1) or "Go-live" not in ev.stdout:
        sys.exit(f"smoke event status FAILED: rc={ev.returncode} "
                 f"out={ev.stdout!r} err={ev.stderr!r}")
    with tempfile.TemporaryDirectory() as td:
        dst = os.path.join(td, "racecast-buttons.companionconfig")
        ex = run(["export", "companion", "--out", dst])
        if ex.returncode != 0 or not os.path.isfile(dst):
            sys.exit(f"smoke export FAILED: rc={ex.returncode} err={ex.stderr!r}")
        # `setup` loads the bundled setup-assets.py in-process, which catches a
        # bundle-layout regression such as --add-data turning the file into a dir.
        imp = os.path.join(td, "import.json")
        su = run(["setup", "--out", imp, "--sheet-id", "smoke"])
        if su.returncode != 0 or not os.path.isfile(imp):
            sys.exit(f"smoke setup FAILED: rc={su.returncode} out={su.stdout!r} err={su.stderr!r}")
        with open(imp, encoding="utf-8") as fh:
            if "_MEI" in fh.read():
                sys.exit("smoke setup FAILED: the localized collection references "
                         "the throwaway _MEIPASS unpack dir (paths die with the process)")
    # `ui` starts the Control Center server in-process from the bundled src/ui/
    # modules, which catches a missing ui/ in DATA as a ModuleNotFoundError.
    import json
    import time
    import urllib.request
    env = os.environ.copy()
    env["RACECAST_UI_PORT"] = "8389"
    ui = subprocess.Popen([binary, "ui", "--no-browser"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        body = b""
        for _ in range(20):                 # up to ~10 s for the server to bind
            time.sleep(0.5)
            try:
                with urllib.request.urlopen("http://127.0.0.1:8389/api/ping",
                                            timeout=2) as r:
                    body = r.read()
                break
            except OSError:
                if ui.poll() is not None:   # crashed before binding
                    break
        if b"racecast-control-center" not in body:
            out = ui.stdout.read().decode("utf-8", "replace") if ui.poll() is not None else ""
            sys.exit(f"smoke ui FAILED: no Control Center ping on :8389 "
                     f"(rc={ui.poll()}) out={out!r}")
        # The Help page must expose the onboarding-decks link, the Pages hub that
        # holds the cheat sheet and role decks, and bundle the local setup docs.
        with urllib.request.urlopen("http://127.0.0.1:8389/api/docs", timeout=2) as r:
            docs = json.loads(r.read())
        if "github.io" not in (docs.get("decks_url") or ""):
            sys.exit(f"smoke ui FAILED: no onboarding-decks URL (decks_url={docs.get('decks_url')!r})")
        if not any(d.get("key") == "setup-readme" for d in docs.get("local", [])):
            sys.exit(f"smoke ui FAILED: setup docs not bundled (local={docs.get('local')!r})")
        # A markdown doc must come back rendered, proving mdrender is bundled and
        # working, not as raw text. The setup README is a wiki-pointer stub with no
        # tables, so this asserts on the full-page wrapper and a list item.
        with urllib.request.urlopen("http://127.0.0.1:8389/api/docs/file/setup-readme",
                                    timeout=2) as r:
            md = r.read().decode("utf-8", "replace")
        if "<!doctype html>" not in md or "<li>" not in md:
            sys.exit("smoke ui FAILED: setup-readme markdown was not rendered to HTML")
        # The onboarding decks must be bundled and serve offline at /docs/slides/,
        # which catches a slides tree missing from the frozen bundle.
        if "/docs/slides/" not in (docs.get("decks_local_url") or ""):
            sys.exit(f"smoke ui FAILED: no offline decks URL (decks_local_url={docs.get('decks_local_url')!r})")
        with urllib.request.urlopen("http://127.0.0.1:8389/docs/slides/", timeout=2) as r:
            if b"<html" not in r.read().lower():
                sys.exit("smoke ui FAILED: bundled onboarding decks did not serve offline")
        urllib.request.urlopen(urllib.request.Request(
            "http://127.0.0.1:8389/api/quit", method="POST", data=b""), timeout=5).read()
        ui.wait(timeout=10)
    finally:
        if ui.poll() is None:
            ui.kill()
    print("Smoke test OK (--version, status, event status, export companion, setup, ui).")


if __name__ == "__main__":
    main()

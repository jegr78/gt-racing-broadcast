#!/usr/bin/env python3
"""End-to-end / regression harness: stand up the relay + Control Center from
src/ and assert the live HTTP surface. Synthetic mode is the default and runs in
CI with no real Sheet, cookies, OBS or Tailscale; --real-league NAME is local-only.

Maintainer tool, not shipped. Stdlib only."""
import argparse, contextlib, os, shutil, signal, socket, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import e2e_checks as E
import e2e_visual as V
import console_auth


def _csv_server(files):
    """Serve each {path: csv_text} entry over loopback HTTP; returns (server, base_url)."""
    bodies = {p: t.encode() for p, t in files.items()}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            body = bodies.get(self.path.split("?", 1)[0])
            if body is None:
                self.send_response(404); self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/csv"); self.end_headers()
            self.wfile.write(body)
    srv = ThreadingHTTPServer(("127.0.0.1", E.free_port()), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _spawn(argv, env, log, cwd=ROOT):
    """Spawn a child in its own process group so teardown kills the tree.
    stdout and stderr are captured to the file path *log*. *cwd* is ROOT for the
    src/ dev path; binary mode runs from the copied binary's isolated app dir."""
    fh = open(log, "wb")  # noqa: SIM115  handle outlives this fn; closed in _kill
    kw = {}
    if os.name == "posix":
        kw["start_new_session"] = True
    p = subprocess.Popen(argv, cwd=cwd, env=env, stdout=fh, stderr=subprocess.STDOUT, **kw)
    p._logfh = fh  # keep the handle so teardown can close it
    return p


def _resolve_binary(args, tmp):
    """Locate, and optionally build, the frozen racecast binary and copy it into
    an isolated temp app-home so its side-effect files (.env, the seeded
    profiles/example, runtime/) land in the throwaway dir rather than dist/bin.
    Returns the copied executable's path. The binary uses dirname(exe) as its app
    home (racecast._app_home), which is why the copy is what gets driven."""
    src = args.binary or E.default_binary_path(ROOT)
    if args.build:
        print("building the binary (tools/build-binary.py)...", flush=True)
        rc = subprocess.call([sys.executable,
                              os.path.join(ROOT, "tools", "build-binary.py"),
                              "--version", "e2e"])
        if rc != 0:
            raise RuntimeError("tools/build-binary.py failed")
        src = args.binary or E.default_binary_path(ROOT)
    if not os.path.exists(src):
        raise RuntimeError(
            f"binary not found: {src}\n"
            "  Build it first: python3 tools/build-binary.py  (or pass --build).")
    app = os.path.join(tmp, "app")
    os.makedirs(app, exist_ok=True)
    dst = os.path.join(app, E.binary_name())
    shutil.copy2(src, dst)
    os.chmod(dst, 0o700)   # owner rwx only; the harness spawns it as this user
    return dst


def _kill(p):
    if not p or p.poll() is not None:
        with contextlib.suppress(Exception):
            if getattr(p, "_logfh", None): p._logfh.close()
        return
    with contextlib.suppress(Exception):
        if os.name == "posix":
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        else:
            p.terminate()
    with contextlib.suppress(Exception):
        p.wait(timeout=10)
    with contextlib.suppress(Exception):
        if getattr(p, "_logfh", None): p._logfh.close()


def _wait_ready(url, timeout, proc=None, log=None):
    """Poll *url* until HTTP 200 or timeout. On timeout, dump *log* and raise."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            break
        try:
            st, _, _ = E.http_request(url, timeout=2)
            if st == 200:
                return
        except Exception:
            pass  # not up yet; keep polling
        time.sleep(0.3)
    detail = ""
    if log and os.path.exists(log):
        with open(log, "rb") as fh:
            detail = fh.read()[-2000:].decode("utf-8", "replace")
    raise RuntimeError(f"service not ready at {url} within {timeout}s\n--- child log ---\n{detail}")


def _wait_port(port, timeout):
    """Block until 127.0.0.1:*port* accepts a TCP connection or *timeout* seconds pass."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), 0.5):
            return
        time.sleep(0.1)
    raise RuntimeError(f"port {port} not ready after {timeout}s")


SCHEDULE_ROWS = [
    ("https://www.youtube.com/watch?v=aaaaaaaaaaa", "Alice", "Stint 1"),
    ("https://www.twitch.tv/bobcaster", "Bob", "Stint 2"),
]

# Rita is crew only, so her token carries race_control and nothing else.
CREW_ROWS = [("Rita", "", "", "", "x")]

PROBE_JS = os.path.join(ROOT, "tools", "visual-probe.js")
ALLOWLIST = os.path.join(ROOT, "tools", "visual-allowlist.json")
PLAYWRIGHT_HINT = ("pip install playwright==1.63.0 && python -m playwright install chromium "
                   "(see tools/CLAUDE.md)")


# The rendered checks load the cockpit page in a real browser and assert its state
# pills render, which the stdlib HTTP checks cannot see. Playwright is never a hard
# dependency: CI runs tools/e2e.py without --playwright, and when Playwright is
# unavailable the block degrades to SKIP results via E.classify_capability, so the
# exit code stays governed by the API checks alone.

def _playwright_available():
    """True only when Playwright's sync API imports and a Chromium browser
    launches. A missing package, a missing browser binary or any launch failure
    all read as unavailable, so the rendered checks skip."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415  optional, lazy
    except Exception:
        return False
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            browser.close()
        return True
    except Exception:
        return False


def _render_pill(ctx, name, selector, what, headed=False, slowmo=0):
    """Load the authenticated cockpit page in Chromium and assert *selector* is
    attached and visible. Returns a CheckResult. Only ever called when
    _playwright_available() is True, so this body is dead code in a browserless
    environment. *headed* makes the browser a visible window and *slowmo* slows
    each action in milliseconds so the run is watchable. When headed, the page is
    held briefly so it is seen before the browser closes."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415  optional, lazy
    url = ctx.relay_url + "/cockpit?t=" + ctx.token
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not headed, slow_mo=slowmo)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded")
                # The page polls /cockpit/data, then fills the pill in, so wait
                # for the element to be attached and visible.
                page.wait_for_selector(selector, state="visible", timeout=10000)
                if headed:
                    page.wait_for_timeout(2500)   # let a human see the rendered pill
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001  a render failure is a check failure
        return E.CheckResult(name, "fail", f"{what}: {type(exc).__name__}: {exc}")
    return E.CheckResult(name, "pass", "")


def render_tally_pill(ctx, headed=False, slowmo=0):
    """The ON-AIR / UP-NEXT tally pill (#tally) renders on the cockpit page."""
    return _render_pill(ctx, "render_tally_pill", "#tally", "tally pill", headed, slowmo)


def render_funnel_pill(ctx, headed=False, slowmo=0):
    """The funnel-delivered identity pill (#who) renders on the cockpit page,
    confirming the token auth resolved a streamer. The page only shows it once
    /cockpit/data has authenticated the session Funnel delivered."""
    return _render_pill(ctx, "render_funnel_pill", "#who", "funnel-state pill", headed, slowmo)


def render_cockpit_phone(ctx, headed=False, slowmo=0):
    """At phone width the cockpit fits the viewport on every tab, every tab shows a card
    and the crew chat keeps its height."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415  optional, lazy
    name = "render_cockpit_phone"
    url = ctx.relay_url + "/cockpit?t=" + ctx.token
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not headed, slow_mo=slowmo)
            try:
                page = browser.new_page(viewport={"width": 390, "height": 844})
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_selector("#tally", state="visible", timeout=10000)
                scroll_w = page.evaluate("document.documentElement.scrollWidth")
                chat_h = page.evaluate(
                    "document.getElementById('chat').closest('.card').getBoundingClientRect().height")
                empty_tabs = []
                for tab in page.evaluate(
                        "[...document.querySelectorAll('#crewTabs button')].map(b => b.dataset.id)"):
                    page.click(f"#crewTabs button[data-id='{tab}']")
                    shown = page.evaluate("[...document.querySelectorAll('[data-tab]')]"
                                          ".filter(e => e.offsetParent !== null).length")
                    if not shown:
                        empty_tabs.append(tab)
                    scroll_w = max(scroll_w, page.evaluate("document.documentElement.scrollWidth"))
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001  a render failure is a check failure
        return E.CheckResult(name, "fail", f"{type(exc).__name__}: {exc}")
    if scroll_w > 390:
        return E.CheckResult(name, "fail", f"page is {scroll_w} px wide in a 390 px viewport")
    if chat_h < 100:
        return E.CheckResult(name, "fail", f"crew chat card is {chat_h:.0f} px high")
    if empty_tabs:
        return E.CheckResult(name, "fail", f"tabs show no card: {empty_tabs}")
    return E.CheckResult(name, "pass", "")


class _NextRecorder:
    """Collects the /next requests a page sends; the browser fills it from a callback."""

    def __init__(self):
        self.urls = []

    def on_request(self, request):
        if "/next" in request.url.split("?")[0]:
            self.urls.append(request.url)

    def sent(self):
        return list(self.urls)


def render_panel_shortcut_confirm(ctx, headed=False, slowmo=0):
    """Director Panel keyboard shortcuts (#731): with the keys switched on, one N
    press sends no /next, a confirmed second press sends exactly one, and N typed
    into a text field sends none."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415  optional, lazy
    name = "render_panel_shortcut_confirm"
    nexts = _NextRecorder()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not headed, slow_mo=slowmo)
            try:
                page = browser.new_page()
                page.on("request", nexts.on_request)
                page.goto(ctx.relay_url + "/panel", wait_until="domcontentloaded")
                page.wait_for_selector("#nextBtn", state="visible", timeout=10000)
                page.click("#kbdBtn")
                page.keyboard.press("n")
                page.wait_for_timeout(2000)                  # past the confirm window
                if nexts.sent():
                    return E.CheckResult(name, "fail", f"a single N press sent {nexts.sent()}")
                page.focus("#chatInput")                     # always visible in the rail
                if page.evaluate("document.activeElement.id") != "chatInput":
                    return E.CheckResult(name, "fail", "could not focus the chat input")
                page.keyboard.press("n")
                page.keyboard.press("n")
                page.wait_for_timeout(500)
                if nexts.sent():
                    return E.CheckResult(name, "fail", f"N typed into a text field sent {nexts.sent()}")
                page.evaluate("document.activeElement.blur()")
                page.keyboard.press("n")
                page.wait_for_timeout(200)
                page.keyboard.press("n")
                page.wait_for_timeout(1500)
                if len(nexts.sent()) != 1:
                    return E.CheckResult(name, "fail",
                                         f"a confirmed N sent {len(nexts.sent())} /next calls")
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001  a render failure is a check failure
        return E.CheckResult(name, "fail", f"{type(exc).__name__}: {exc}")
    return E.CheckResult(name, "pass", "")


def render_cockpit_graphics(ctx, headed=False, slowmo=0):
    """The cockpit's graphic card shows tiles, a tap previews in the page instead of a
    new tab, and with GRAPHICS_TAKE unset there is no take button."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415  optional, lazy
    name = "render_cockpit_graphics"
    url = ctx.relay_url + "/cockpit?t=" + ctx.token
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not headed, slow_mo=slowmo)
            try:
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_selector("#gfxList .gtile", state="visible", timeout=10000)
                buttons = page.locator("#gfxList .gtake").count()
                page.click("#gfxList .gthumb")
                page.wait_for_selector("dialog.gpreview[open] img", timeout=5000)
                pages = len(context.pages)
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001  a render failure is a check failure
        return E.CheckResult(name, "fail", f"{type(exc).__name__}: {exc}")
    if buttons:
        return E.CheckResult(name, "fail", f"{buttons} take buttons with GRAPHICS_TAKE off")
    if pages != 1:
        return E.CheckResult(name, "fail", f"the preview opened {pages - 1} extra tab(s)")
    return E.CheckResult(name, "pass", "")


RENDERED_CHECKS = [render_tally_pill, render_funnel_pill, render_cockpit_phone,
                   render_cockpit_graphics, render_panel_shortcut_confirm]
# A real league's relay can reach the producer's OBS, so no check that sends /next.
REAL_LEAGUE_RENDERED_CHECKS = [render_tally_pill, render_funnel_pill, render_cockpit_phone,
                               render_cockpit_graphics]


def run_rendered_checks(ctx, headed=False, slowmo=0, checks=None):
    """Run the gated Playwright rendered checks for *ctx*. Returns a list of
    CheckResults to append after the API results. Without Playwright or a browser
    every rendered check is reported as a skip, so a browserless run never fails
    here and the exit code is decided by the API checks alone. *headed* and
    *slowmo* drive a visible, watchable browser, local only."""
    available = _playwright_available()
    results = []
    for fn in RENDERED_CHECKS if checks is None else checks:
        skipped = E.classify_capability(available, fn.__name__)
        if skipped is not None:
            results.append(skipped)
            continue
        try:
            results.append(fn(ctx, headed=headed, slowmo=slowmo))
        except Exception as exc:  # noqa: BLE001  a crashing check is a failure
            results.append(E.CheckResult(fn.__name__, "fail",
                                         f"{type(exc).__name__}: {exc}"))
    return results


def _stub_tools_bin(tmp):
    """A bin dir of no-op stubs for the external tools the relay checks at
    startup. The relay hard-exits if `yt-dlp` or `streamlink` are not on PATH, and
    `ffmpeg`/`deno` are invoked by a feed pull. The synthetic schedule's URLs are
    fake, so no real stream is pulled; the stubs let the startup tool-check pass
    and make feed threads fail instantly on a runner without the real tools. They
    are prepended to PATH so the run is deterministic on a dev box that has them.
    POSIX-only: the synthetic run targets the Linux CI job, and real-league mode
    uses the operator's real PATH."""
    bindir = os.path.join(tmp, "bin")
    os.makedirs(bindir, exist_ok=True)
    for name in ("yt-dlp", "streamlink", "ffmpeg", "deno"):
        p = os.path.join(bindir, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(p, 0o700)   # owner-only: the harness spawns the relay as this user
    return bindir


def _capture_shots(ctx, outdir, headed=False, slowmo=0):
    """Write a screenshot of each visual surface to *outdir* using the same
    Playwright library the rendered checks use, a reproducible MCP-free visual
    tour of a run. Best-effort: a shot failure warns but never fails the run.
    Returns the list of written paths.

    The Control Center Home shows this machine's Tailscale IP, so the output is a
    local artifact and must not be committed."""
    if not _playwright_available():
        print(f"--shots: Playwright/browser unavailable, nothing written to {outdir}.")
        return []
    from playwright.sync_api import sync_playwright  # noqa: PLC0415  optional, lazy
    os.makedirs(outdir, exist_ok=True)
    surfaces = [
        ("control-center", ctx.ui_url + "/"),
        ("cockpit", ctx.relay_url + "/cockpit?t=" + ctx.token),
        ("director-panel", ctx.relay_url + "/panel"),
        ("hud", ctx.relay_url + "/hud"),
    ]
    written = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed, slow_mo=slowmo)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            for name, url in surfaces:
                path = os.path.join(outdir, f"{name}.png")
                try:
                    page.goto(url, wait_until="domcontentloaded")
                    page.wait_for_timeout(1500)   # let the SPA poll its data in
                    page.screenshot(path=path, full_page=True)
                    written.append(path)
                    print(f"--shots: wrote {path}")
                except Exception as exc:  # noqa: BLE001  best-effort artifact
                    print(f"--shots: WARN could not capture {name}: "
                          f"{type(exc).__name__}: {exc}")
        finally:
            browser.close()
    return written


def _visual_one(browser, surface, viewport, urls, outdir, probe, allow, used):
    """Render one surface in one viewport, screenshot it and judge it. Returns a SurfaceResult."""
    w, h = V.VIEWPORTS[viewport]
    shot = f"{surface.name}-{viewport}.png"
    errors = []

    def on_console(msg):
        if msg.type == "error":
            # A failed resource load names no URL in its text, only in its location.
            url = (msg.location or {}).get("url")
            errors.append(f"console.error: {msg.text}" + (f" ({url})" if url else ""))

    page = browser.new_page(viewport={"width": w, "height": h})
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    page.on("console", on_console)
    try:
        page.goto(V.surface_url(surface, urls), wait_until="domcontentloaded")
        if surface.prep:
            page.evaluate(surface.prep)
        page.wait_for_function(surface.ready, timeout=surface.timeout_ms)
        page.wait_for_timeout(V.SETTLE_MS)
        page.screenshot(path=os.path.join(outdir, shot), full_page=True)
        facts = page.evaluate(probe)
    except Exception as exc:  # noqa: BLE001  a surface that does not render is a failure
        return V.SurfaceResult(surface.name, viewport, None, [], 0, f"{type(exc).__name__}: {exc}")
    finally:
        page.close()
    try:
        findings = V.evaluate(facts, errors)
        kept, hit = V.apply_allowlist(findings, allow, surface.name, viewport)
    except Exception as exc:  # noqa: BLE001  facts the rules cannot read fail only this surface
        return V.SurfaceResult(surface.name, viewport, shot, [], 0, f"{type(exc).__name__}: {exc}")
    used.update(hit)
    return V.SurfaceResult(surface.name, viewport, shot, kept, len(findings) - len(kept), None)


def run_visual(urls, outdir, headed=False, slowmo=0):
    """Render every surface of the visual acceptance run, write DIR/report.html and the
    screenshots, print a summary. Returns 0 when clean, 1 on a finding or a broken surface."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415  optional, lazy
    with open(PROBE_JS, encoding="utf-8") as fh:
        probe = fh.read()
    allow = V.load_allowlist(ALLOWLIST)
    os.makedirs(outdir, exist_ok=True)
    results, used = [], set()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed, slow_mo=slowmo)
        try:
            for surface in V.SURFACES:
                for viewport in surface.viewports:
                    results.append(_visual_one(browser, surface, viewport, urls, outdir,
                                               probe, allow, used))
        finally:
            browser.close()
    unused = [e for n, e in enumerate(allow) if n not in used]
    report = os.path.join(outdir, "report.html")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write(V.render_report(results, unused))
    print("visual acceptance:\n" + V.summarize(results, unused))
    print(f"--visual: report at {report}")
    return V.result_code(results)


def _print_live_urls(relay_url, ui_url, token):
    """With --keep the spawned relay and Control Center are left running: they
    started in their own session, so they outlive this process. Print the live
    surfaces so they can be opened in a browser for a visual walk-through."""
    print("\n--- live services (left up by --keep, open these in a browser) ---")
    print(f"  relay status (JSON): {relay_url}/status")
    print(f"  director panel:      {relay_url}/panel")
    print(f"  lower-third HUD:     {relay_url}/hud")
    print(f"  commentator cockpit: {relay_url}/cockpit?t={token}")
    print(f"  Control Center:      {ui_url}/")
    print("  (stop them with:  pkill -f 'racecast.py relay run' ; "
          "pkill -f 'racecast.py ui')")


def run_synthetic(args):
    tmp = tempfile.mkdtemp(prefix="racecast-e2e-")
    procs, servers = [], []
    try:
        # 1. synthetic profile (scaffold from profiles/example) + cockpit secret
        prof_root = os.path.join(tmp, "profiles")
        shutil.copytree(os.path.join(ROOT, "profiles", "example"),
                        os.path.join(prof_root, "e2e"))
        secret = "e2e-secret-0123456789abcdef"
        key = console_auth.streamer_key("Alice")
        token = console_auth.mint_token(secret, key, version=1)
        rc_token = console_auth.mint_token(secret, console_auth.streamer_key("Rita"), version=1)

        # The CLI always injects --cookies <runtime>/yt-cookies.txt and the relay
        # hard-exits if that path is missing. Synthetic runs have no real YouTube
        # session, so hand it an empty jar in the temp dir, appended last so it
        # wins over the CLI-injected path. Feed pulls then fail in best-effort
        # threads, which is fine: only the HTTP control surface is asserted.
        dummy_cookies = os.path.join(tmp, "yt-cookies.txt")
        with open(dummy_cookies, "w", encoding="utf-8") as fh:
            fh.write("# Netscape HTTP Cookie File\n")
        # Isolate all relay state in the temp dir. The CLI injects --runtime-dir
        # <repo>/runtime/..., and this last-wins override keeps a synthetic run out
        # of the real runtime tree.
        relay_runtime = os.path.join(tmp, "runtime")
        os.makedirs(relay_runtime, exist_ok=True)
        # One still for the crew graphic card; any real PNG will do.
        os.makedirs(os.path.join(relay_runtime, "graphics"), exist_ok=True)
        shutil.copy(os.path.join(ROOT, "src", "assets", "brands", "audi.png"),
                    os.path.join(relay_runtime, "graphics", "Standings.png"))

        # Stub the external stream tools so the relay's startup tool-check passes
        # on a clean machine or CI runner.
        stub_bin = _stub_tools_bin(tmp)

        # A stand-in OBS, so the program monitors render a picture instead of their offline state.
        obs_port = E.free_port()
        procs.append(_spawn([sys.executable, os.path.join(ROOT, "tools", "obs-sim.py"),
                             "--image", os.path.join(relay_runtime, "graphics", "Standings.png"),
                             "--port", str(obs_port)],
                            dict(os.environ), os.path.join(tmp, "obs-sim.log")))
        _wait_port(obs_port, args.timeout)

        # Launcher: the frozen binary, or `python src/racecast.py`. Binary mode
        # guards the bugs the src/ dev build hides, a file or import missing from
        # the PyInstaller bundle and frozen path resolution. The subcommand surface
        # is identical, so the same checks run against whichever is driven.
        if args.binary is not None:
            binary = _resolve_binary(args, tmp)
            launcher, run_cwd = E.service_launcher(binary), os.path.dirname(binary)
            print(f"binary mode: driving the frozen binary at {binary}", flush=True)
        else:
            launcher = E.service_launcher(
                None, sys.executable, os.path.join(ROOT, "src", "racecast.py"))
            run_cwd = ROOT

        # 2. schedule + crew CSV server
        csv_srv, csv_base = _csv_server({
            "/schedule.csv": E.build_schedule_csv(SCHEDULE_ROWS),
            "/crew.csv": E.build_crew_csv(CREW_ROWS)})
        servers.append(csv_srv)
        csv_url, crew_url = csv_base + "/schedule.csv", csv_base + "/crew.csv"

        # 3. cockpit relay: a secret in the env makes /cockpit/* served and token-gated
        relay_port = E.free_port()
        env = dict(os.environ)
        # Fan-out is the product default, but the two cockpit relays below share the
        # host default feed ports, so two fan-out relays would collide binding them.
        # Pinning them to direct-serve keeps the fallback path exercised and binds no
        # feed ports; step 6's relay overrides this on its own free ports. It also
        # neutralizes a RACECAST_FEED_FANOUT leaked from the operator's shell.
        env.update(RACECAST_CONSOLE_SECRET=secret, RACECAST_PROFILE="e2e",
                   RACECAST_FEED_FANOUT="0", RACECAST_OBS_WS_HOST="127.0.0.1",
                   RACECAST_OBS_WS_PORT=str(obs_port), RACECAST_OBS_WS_PASSWORD="")
        env["PATH"] = stub_bin + os.pathsep + env.get("PATH", "")
        relay_log = os.path.join(tmp, "relay.log")
        relay = _spawn(launcher + ["relay", "run", "--bind", "127.0.0.1",
                        "--http-port", str(relay_port), "--sheet-csv-url", csv_url,
                        "--crew-csv-url", crew_url,
                        "--cookies", dummy_cookies, "--runtime-dir", relay_runtime],
                       env, relay_log, cwd=run_cwd)
        procs.append(relay)
        relay_url = f"http://127.0.0.1:{relay_port}"
        _wait_ready(relay_url + "/status", args.timeout, relay, relay_log)

        # 4. secret-less relay, where every /cockpit/* must 404. The cockpit is
        # zero-config, so "no cockpit" means "no secret": this relay runs the shipped
        # 'example' profile, the one profile auto-provisioning never touches.
        dis_port = E.free_port()
        env2 = dict(os.environ); env2.update(RACECAST_PROFILE="example")
        env2.pop("RACECAST_CONSOLE_SECRET", None)
        env2["PATH"] = stub_bin + os.pathsep + env2.get("PATH", "")
        dis_log = os.path.join(tmp, "relay-disabled.log")
        dis = _spawn(launcher + ["relay", "run", "--bind", "127.0.0.1",
                      "--http-port", str(dis_port), "--sheet-csv-url", csv_url,
                      "--cookies", dummy_cookies,
                      "--runtime-dir", os.path.join(tmp, "runtime-disabled")],
                     env2, dis_log, cwd=run_cwd)
        procs.append(dis)
        dis_url = f"http://127.0.0.1:{dis_port}"
        _wait_ready(dis_url + "/status", args.timeout, dis, dis_log)

        # 5. Control Center
        ui_port = E.free_port()
        env3 = dict(env); env3["RACECAST_UI_PORT"] = str(ui_port)
        ui_log = os.path.join(tmp, "ui.log")
        ui = _spawn(launcher + ["ui", "--no-browser"], env3, ui_log, cwd=run_cwd)
        procs.append(ui)
        ui_url = f"http://127.0.0.1:{ui_port}"
        _wait_ready(ui_url + "/api/ping", args.timeout, ui, ui_log)

        # 6. fan-out relay: a third relay with RACECAST_FEED_FANOUT=1 on explicit
        #    free feed ports. In fan-out mode the relay itself binds the feed ports,
        #    so once /status is up the feed-A port is already serving HTTP. The two
        #    relays above never bind feed ports, so fresh free ports cannot collide.
        fanout_feed_a = E.free_port()
        fanout_feed_b = E.free_port()
        fanout_pov = E.free_port()
        fanout_http = E.free_port()
        fanout_runtime = os.path.join(tmp, "runtime-fanout")
        os.makedirs(fanout_runtime, exist_ok=True)
        env4 = dict(env); env4["RACECAST_FEED_FANOUT"] = "1"
        fanout_log = os.path.join(tmp, "relay-fanout.log")
        fanout_relay = _spawn(
            launcher + ["relay", "run", "--bind", "127.0.0.1",
                        "--http-port", str(fanout_http),
                        "--sheet-csv-url", csv_url,
                        "--cookies", dummy_cookies,
                        "--runtime-dir", fanout_runtime,
                        "--ports", f"{fanout_feed_a},{fanout_feed_b}",
                        "--pov-port", str(fanout_pov)],
            env4, fanout_log, cwd=run_cwd)
        procs.append(fanout_relay)
        _wait_ready(f"http://127.0.0.1:{fanout_http}/status",
                    args.timeout, fanout_relay, fanout_log)

        # 7. run checks
        ctx = E.Ctx(relay_url=relay_url, disabled_relay_url=dis_url, ui_url=ui_url,
                    token=token, streamer_key=key, own_stint="Stint 1",
                    expect={"schedule_len": 2, "live_stint": 1},
                    fanout_feed_port=fanout_feed_a,
                    fanout_relay_url=f"http://127.0.0.1:{fanout_http}",
                    rc_token=rc_token)
        results, code = E.run_checks(E.SYNTHETIC_CHECKS, ctx)
        if args.playwright:
            # Append the rendered-check results after the API results. A
            # browserless run yields skips that do not touch the exit code; only a
            # real rendered failure can bump it.
            rendered = run_rendered_checks(ctx, headed=args.headed, slowmo=args.slowmo)
            results = results + rendered
            if any(r.status == "fail" for r in rendered):
                code = 1
        print(E.summarize(results))
        if args.visual:
            urls = {"ui": ui_url, "relay": relay_url, "token": token, "rc_token": rc_token}
            code = max(code, run_visual(urls, args.report, headed=args.headed, slowmo=args.slowmo))
        if args.shots:
            _capture_shots(ctx, args.shots, headed=args.headed, slowmo=args.slowmo)
        if args.keep:
            _print_live_urls(relay_url, ui_url, token)
            print("  NOTE: the synthetic schedule was served in-process and stops "
                  "when this command exits, so the relay keeps only its cached schedule.")
        return code
    finally:
        if not args.keep:
            for p in procs: _kill(p)
            for s in servers:
                with contextlib.suppress(Exception): s.shutdown()
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f"--keep: left tmp at {tmp}")


def _resolve_real_profile(name):
    """Resolve the league profile *name* via src/scripts/config.py against the
    repo's profiles/ tree. Returns a ResolvedConfig, or None when the profile is
    absent so the caller can skip gracefully. Every repo profile but example is
    gitignored, so the operator copies it in per the racecast-local-uat skill."""
    sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
    import config as cfg
    if name not in cfg.list_profiles(ROOT):
        return None
    try:
        return cfg.resolve_config(ROOT, override=name)
    except cfg.ProfileError:
        return None


def run_real_league(args):
    """Drive the relay and Control Center against a real copied league profile and
    run the non-mutating REAL_LEAGUE_CHECKS subset. Local only: it refuses under CI
    and returns 0 when the profile is not present in the repo. It never writes to
    the deployed instance, and uses run_synthetic's teardown discipline."""
    if os.environ.get("CI"):
        print("real-league mode is local-only; refusing under CI.")
        return 0

    name = args.real_league
    rc = _resolve_real_profile(name)
    if rc is None:
        print(f"real-league: profile {name!r} not found under {ROOT}/profiles/.")
        print("  Copy it in first (see the racecast-local-uat skill); this is a "
              "graceful skip, not a failure.")
        return 0
    if not rc.console_secret:
        print(f"real-league: profile {name!r} has no CONSOLE_SECRET in profile.env.")
        print("  Start the relay once for that league (it auto-provisions the secret), "
              "then re-run; skipping.")
        return 0

    tmp = tempfile.mkdtemp(prefix="racecast-e2e-real-")
    procs = []
    try:
        # Spawn the relay via the normal CLI path against the real profile, so the
        # CLI injects the league's real runtime-dir, cookie jar and overlay. Only
        # --bind and --http-port are overridden, the latter to a free port so a
        # relay the operator already runs is never disturbed.
        relay_port = E.free_port()
        env = dict(os.environ)
        env["RACECAST_PROFILE"] = name   # /cockpit is served whenever the league has a secret
        relay_log = os.path.join(tmp, "relay.log")
        relay = _spawn([sys.executable, os.path.join(ROOT, "src", "racecast.py"),
                        "relay", "run", "--bind", "127.0.0.1",
                        "--http-port", str(relay_port)],
                       env, relay_log)
        procs.append(relay)
        relay_url = f"http://127.0.0.1:{relay_port}"
        _wait_ready(relay_url + "/status", args.timeout, relay, relay_log)

        # Mint a token for a real streamer from the live schedule. /schedule/data
        # is unauthenticated and reflects the league's actual roster, so no name is
        # hardcoded. The decode and first-streamer pick live in the unit-tested
        # E.first_roster_streamer so this byte-decoding path cannot regress unseen.
        st, sched_body, _ = E.http_request(relay_url + "/schedule/data", timeout=10)
        streamer = E.first_roster_streamer(st, sched_body)
        if not streamer:
            print("real-league: the live schedule is empty (no Sheet/network?).")
            print("  Cockpit-token checks need a real streamer; skipping the run.")
            return 0
        key = console_auth.streamer_key(streamer)
        token = console_auth.mint_token(rc.console_secret, key, version=1)

        # Control Center against the same real profile.
        ui_port = E.free_port()
        env_ui = dict(env)
        env_ui["RACECAST_UI_PORT"] = str(ui_port)
        ui_log = os.path.join(tmp, "ui.log")
        ui = _spawn([sys.executable, os.path.join(ROOT, "src", "racecast.py"),
                     "ui", "--no-browser"], env_ui, ui_log)
        procs.append(ui)
        ui_url = f"http://127.0.0.1:{ui_port}"
        _wait_ready(ui_url + "/api/ping", args.timeout, ui, ui_log)

        print(f"real-league {name!r}: minting cockpit token for {streamer!r} "
              "(first roster streamer from the live schedule).")
        ctx = E.Ctx(relay_url=relay_url, disabled_relay_url=relay_url, ui_url=ui_url,
                    token=token, streamer_key=key, own_stint=None, expect={})
        results, code = E.run_checks(E.REAL_LEAGUE_CHECKS, ctx)
        if args.playwright:
            # Gated: skips without a browser. --headed gives a visible window.
            rendered = run_rendered_checks(ctx, headed=args.headed, slowmo=args.slowmo,
                                           checks=REAL_LEAGUE_RENDERED_CHECKS)
            results = results + rendered
            if any(r.status == "fail" for r in rendered):
                code = 1
        print(E.summarize(results))
        if args.shots:
            _capture_shots(ctx, args.shots, headed=args.headed, slowmo=args.slowmo)
        if args.keep:
            _print_live_urls(relay_url, ui_url, token)
        return code
    finally:
        if not args.keep:
            for p in procs:
                _kill(p)
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f"--keep: left tmp at {tmp}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="racecast e2e/regression harness")
    ap.add_argument("--real-league", metavar="NAME", default=None,
                    help="drive the copied real-league dev build (local only, never CI)")
    ap.add_argument("--binary", nargs="?", const="", default=None, metavar="PATH",
                    help="drive the frozen binary instead of src/, the regression "
                         "guard for binary-only bugs (missing bundled file/import, "
                         "frozen path resolution). PATH defaults to "
                         "dist/bin/racecast; pair with --build to build it first")
    ap.add_argument("--build", action="store_true",
                    help="build the binary (tools/build-binary.py) before a --binary run")
    ap.add_argument("--playwright", action="store_true",
                    help="also run gated rendered checks (skip if unavailable)")
    ap.add_argument("--headed", action="store_true",
                    help="run the --playwright rendered checks in a visible browser "
                         "window (local only; a visual walk-through of the cockpit page)")
    ap.add_argument("--slowmo", type=int, default=0, metavar="MS",
                    help="slow each Playwright action by MS ms so a --headed run is watchable")
    ap.add_argument("--shots", metavar="DIR", default=None,
                    help="write a screenshot of each surface (cockpit/panel/hud/Control "
                         "Center) to DIR via Playwright, a reproducible MCP-free visual "
                         "tour (local only; the Control Center shot shows your Tailscale IP)")
    ap.add_argument("--visual", action="store_true",
                    help="visual acceptance run: render Control Center, Director Panel, cockpit "
                         "and Race Control, apply the layout rules, fail on a finding "
                         "(synthetic mode only, needs Playwright)")
    ap.add_argument("--report", metavar="DIR",
                    default=os.path.join(ROOT, "runtime", "visual-report"),
                    help="where --visual writes report.html and the screenshots "
                         "(default runtime/visual-report)")
    ap.add_argument("--timeout", type=float, default=30.0,
                    help="per-service readiness timeout (s)")
    ap.add_argument("--keep", action="store_true",
                    help="skip teardown: leave relay + Control Center running and print "
                         "their URLs (open them in a browser)")
    args = ap.parse_args(argv)
    # --build implies binary mode even without an explicit --binary.
    if args.build and args.binary is None:
        args.binary = ""
    if args.real_league:
        if args.binary is not None:
            ap.error("--binary is synthetic-only; not supported with --real-league")
        if args.visual:
            ap.error("--visual is synthetic-only; not supported with --real-league")
        return run_real_league(args)
    if args.visual and not _playwright_available():
        print(f"--visual: Playwright + Chromium required: {PLAYWRIGHT_HINT}")
        return 2
    return run_synthetic(args)


if __name__ == "__main__":
    sys.exit(main())

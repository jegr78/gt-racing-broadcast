#!/usr/bin/env python3
"""Build the distributable from src/ (single source of truth).
Produces dist/GT_Racecast_Package/ + dist/GT_Racecast_Package.zip.
Usage: python3 tools/build.py
"""
import filecmp, json, os, re, shutil, subprocess, sys, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
DIST = os.path.join(ROOT, "dist")
PKG = os.path.join(DIST, "GT_Racecast_Package")

sys.path.insert(0, os.path.join(SRC, "scripts"))
import placeholders  # noqa: E402  (pure stdlib helper; seeds neutral package placeholders)


def _is_placeholder(path, ph_path):
    """True when `path` exists and is byte-identical to the bundled placeholder
    `ph_path`, so a placeholder is detected whichever script wrote it."""
    return bool(ph_path) and os.path.isfile(path) and filecmp.cmp(path, ph_path, shallow=False)


# The SHEET_PUSH_URL is the league secret most likely to leak into a committed
# artifact. The verify allowlist below is per-pattern and would miss the class, so
# both shapes are caught explicitly: a /macros/.../exec endpoint and a ?key= query
# (#103). Scanned only against the secret-free artifacts, never the docs, which
# legitimately document the webhook URL format.
_APPSCRIPT_SECRET_RE = re.compile(r"/macros/|/exec\b|[?&]key=", re.IGNORECASE)


def has_appscript_secret(text):
    """True when `text` contains an Apps Script webhook URL pattern: a /macros/
    or /exec endpoint, or a ?key= query."""
    return bool(_APPSCRIPT_SECRET_RE.search(text or ""))


def blank_pass(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("pass", "password") and isinstance(v, str):
                o[k] = ""
            else:
                blank_pass(v)
    elif isinstance(o, list):
        for x in o:
            blank_pass(x)


def cp(srcrel, dstrel):
    s = os.path.join(SRC, srcrel)
    d = os.path.join(PKG, dstrel)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    shutil.copytree(s, d) if os.path.isdir(s) else shutil.copy2(s, d)


def main():
    if not os.path.isdir(SRC):
        sys.exit("ERROR: src/ not found")
    if os.path.exists(PKG):
        shutil.rmtree(PKG)
    os.makedirs(PKG)

    # Top-level docs, director panel and setup-assets. The role cheat sheet is not
    # copied here: it ships inside docs/slides/ and the Control Center Help page
    # links to the published decks rather than serving it locally.
    for f in ("Broadcast_Setup_Guide.md", "README_SETUP.md"):
        cp(f"docs/{f}", f)
    cp("docs/slides", "docs/slides")   # onboarding decks and cheat sheet, vendored Reveal
    cp("director/director-panel.html", "director-panel.html")
    cp("obs/hud.html", "hud.html")
    cp("obs/hud-preview.html", "hud-preview.html")
    cp("obs/splitscreen.html", "splitscreen.html")
    cp("obs/intermission.html", "intermission.html")  # /intermission chat-box overlay
    cp("cockpit/cockpit.html", "cockpit.html")
    cp("racecontrol/race-control.html", "race-control.html")
    cp("console/console.html", "console.html")        # /console launcher (#216)
    cp("console/buttons.html", "buttons.html")         # /console/buttons wrapper (#236)
    cp("console/health-monitor.html", "health-monitor.html")  # /health-monitor dashboard
    cp("setup-assets.py", "setup-assets.py")
    cp("racecast.py", "racecast.py")
    cp("racecast_ui.py", "racecast_ui.py")   # windowed Control Center launcher (racecast-ui)
    cp("assets", "assets")
    cp("scripts", "scripts")
    cp("relay", "relay")  # racecast-feeds.py and get-cookies.py
    cp("ui", "ui")        # Control Center server and page

    # Intro/outro clips: download into the package so the artifact is
    # self-contained. Best-effort, because an offline or code-only build must still
    # succeed; the shipped get-media.py lets a producer re-fetch on site.
    media_dst = os.path.join(PKG, "media")
    os.makedirs(media_dst, exist_ok=True)
    try:
        subprocess.run([sys.executable, os.path.join(SRC, "relay", "get-media.py"),
                        "--out", media_dst], check=True, timeout=600)
    except Exception as e:
        print(f"  [WARN] intro/outro clip fetch skipped: {e}")

    # Broadcast graphics, same best-effort policy as the clips: get-graphics.py
    # lets a producer re-fetch on site when the sheet graphics change.
    graphics_dst = os.path.join(PKG, "graphics")
    os.makedirs(graphics_dst, exist_ok=True)
    try:
        subprocess.run([sys.executable, os.path.join(SRC, "relay", "get-graphics.py"),
                        "--out", graphics_dst], check=True, timeout=600)
    except Exception as e:
        print(f"  [WARN] graphics fetch skipped: {e}")

    # .env template (repo root, not src/) so producers can set their own RACECAST_SHEET_ID
    shutil.copy2(os.path.join(ROOT, ".env.example"), os.path.join(PKG, ".env.example"))

    # profiles/ lives at the repo root, not under src/. Ship both committed
    # leagues: `example`, the template `racecast profile new` copies from and
    # without which the package cannot create a profile (#45), and `demo`, the
    # directly usable public-Sheet league (#206).
    for prof in ("example", "demo"):
        shutil.copytree(os.path.join(ROOT, "profiles", prof),
                        os.path.join(PKG, "profiles", prof))

    # companion: copy and strip the password, defence in depth
    os.makedirs(os.path.join(PKG, "companion"))
    with open(os.path.join(SRC, "companion", "racecast-buttons.companionconfig"), encoding="utf-8") as fh:
        cfg = json.load(fh)
    blank_pass(cfg)
    with open(os.path.join(PKG, "companion", "racecast-buttons.companionconfig"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=1)

    # obs: ship the tokenized collection as .template.json (setup-assets localizes it)
    os.makedirs(os.path.join(PKG, "obs"))
    shutil.copy2(os.path.join(SRC, "obs", "GT_Racing_Endurance.json"),
                 os.path.join(PKG, "obs", "GT_Racing_Endurance.template.json"))
    # solo-mode collections (#303): ship the same way, when present.
    for solo in ("GT_Racing_Solo_Commentary.json", "GT_Racing_Solo_POV.json"):
        solo_src = os.path.join(SRC, "obs", solo)
        if os.path.exists(solo_src):
            shutil.copy2(solo_src, os.path.join(
                PKG, "obs", solo.replace(".json", ".template.json")))
    # obs-browser source-build wrapper CMakeLists, used by `racecast obs-browser`
    # on Linux to compile the Browser Source plugin against the distro libobs.
    cp("obs/obs-browser-build", "obs/obs-browser-build")

    # drop any stray __pycache__ from copied trees
    for root, dirs, _ in os.walk(PKG):
        for d in list(dirs):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(root, d)); dirs.remove(d)

    # zip
    zip_path = os.path.join(DIST, "GT_Racecast_Package.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(PKG):
            for fn in files:
                fp = os.path.join(root, fn)
                z.write(fp, os.path.relpath(fp, DIST))

    # verify
    def has_pw(o):
        if isinstance(o, dict):
            return any((k in ("pass", "password") and v) or has_pw(v) for k, v in o.items())
        if isinstance(o, list):
            return any(has_pw(x) for x in o)
        return False

    with open(os.path.join(PKG, "obs", "GT_Racing_Endurance.template.json"), encoding="utf-8") as fh:
        tpl = fh.read()
    # Seed neutral placeholders for any clip or graphic not fetched above, so the
    # artifact works before a producer downloads real assets. fill_missing writes
    # only the ones still absent, so a real download always wins.
    placeholders.fill_missing(
        ["intro.mp4", "outro.mp4"], media_dst, placeholders.media_placeholder_path())
    placeholders.fill_missing(
        placeholders.expected_graphics_from_template(tpl), graphics_dst,
        placeholders.graphic_placeholder_path())
    with open(os.path.join(PKG, "relay", "racecast-feeds.py"), encoding="utf-8") as fh:
        relay = fh.read()
    # Re-read the shipped companion config from disk rather than the in-memory cfg,
    # so the password check verifies what was written, not what was blanked here.
    with open(os.path.join(PKG, "companion", "racecast-buttons.companionconfig"), encoding="utf-8") as fh:
        written = json.load(fh)
    blob = json.dumps(written)
    with open(os.path.join(PKG, "hud.html"), encoding="utf-8") as fh:
        hud = fh.read()
    with open(os.path.join(PKG, "profiles", "demo", "profile.env"), encoding="utf-8") as fh:
        demo_env = fh.read()
    checks = {
        "companion pov buttons": "pov/reload" in blob,
        "companion password empty": not has_pw(written),
        "obs graphics tokenized": "__RACECAST_GRAPHICS__/" in tpl
            and "GoogleDrive" not in tpl and "drive.google.com" not in tpl,
        # The relay serves /hud, so the collection legitimately has no
        # __RACECAST_SHEET__ token; only a raw sheet URL would be a leak.
        "obs no raw sheet url": not re.search(r"/spreadsheets/d/[A-Za-z0-9_-]{20,}/", tpl),
        # No Apps Script SHEET_PUSH_URL leaked into the secret-free artifacts. (#103)
        "obs no apps-script webhook": not has_appscript_secret(tpl),
        "relay no apps-script webhook": not has_appscript_secret(relay),
        "companion no apps-script webhook": not has_appscript_secret(blob),
        "relay timer endpoint": "/timer/data" in relay,
        "obs media tokenized": "__RACECAST_MEDIA__/" in tpl,
        "relay pov endpoint": "pov/reload" in relay,
        "no .sh/.bat shipped": not any(fn.endswith((".sh", ".bat")) for _, _, fs in os.walk(PKG) for fn in fs),
        "preflight shipped": os.path.isfile(os.path.join(PKG, "scripts", "preflight.py")),
        "hud serves the clock": '<div id="clock"' in hud,
        "hud preview shipped": os.path.isfile(os.path.join(PKG, "hud-preview.html")),
        "splitscreen page shipped": os.path.isfile(os.path.join(PKG, "splitscreen.html")),
        "cockpit page shipped": os.path.isfile(os.path.join(PKG, "cockpit.html")),
        "race-control page shipped": os.path.isfile(os.path.join(PKG, "race-control.html")),
        "console launcher shipped": os.path.isfile(os.path.join(PKG, "console.html")),
        "console buttons page shipped": os.path.isfile(os.path.join(PKG, "buttons.html")),
        "health monitor page shipped": os.path.isfile(os.path.join(PKG, "health-monitor.html")),
        "uplot vendored": os.path.isfile(os.path.join(PKG, "assets", "vendor", "uplot", "uPlot.iife.min.js")),
        "preview backdrop shipped": os.path.isfile(os.path.join(PKG, "assets", "preview-bg.jpg")),
        ".env.example shipped": os.path.isfile(os.path.join(PKG, ".env.example")),
        "example profile shipped": os.path.isfile(
            os.path.join(PKG, "profiles", "example", "profile.env")),
        "demo profile shipped": os.path.isfile(
            os.path.join(PKG, "profiles", "demo", "profile.env")),
        # The public demo league must never carry a write credential, so those
        # key= lines stay blank. Comments may still document the URL format.
        "demo profile secret-free": not re.search(
            r"^[ \t]*(SHEET_PUSH_URL|CONSOLE_SECRET|DISCORD_WEBHOOK_URL)[ \t]*=[ \t]*\S",
            demo_env, re.MULTILINE),
        "no sheet url in relay": not re.search(r"/spreadsheets/d/[A-Za-z0-9_-]{20,}/", relay),
        "racecast cli shipped": os.path.isfile(os.path.join(PKG, "racecast.py")),
        "racecast-ui launcher shipped": os.path.isfile(os.path.join(PKG, "racecast_ui.py")),
        "services helper shipped": os.path.isfile(os.path.join(PKG, "scripts", "services.py")),
        "install-tools shipped": os.path.isfile(os.path.join(PKG, "scripts", "install_tools.py")),
        "install-apps shipped": os.path.isfile(os.path.join(PKG, "scripts", "install_apps.py")),
        "installer-common shipped": os.path.isfile(os.path.join(PKG, "scripts", "installer_common.py")),
        "old entrypoint removed: scripts/start-companion.py": not os.path.isfile(os.path.join(PKG, "scripts", "start-companion.py")),
        "old entrypoint removed: scripts/stop-companion.py": not os.path.isfile(os.path.join(PKG, "scripts", "stop-companion.py")),
        "ui server shipped": os.path.isfile(os.path.join(PKG, "ui", "ui_server.py")),
        "ui page shipped": os.path.isfile(os.path.join(PKG, "ui", "control-center.html")),
        "slides director deck shipped": os.path.isfile(
            os.path.join(PKG, "docs", "slides", "director.html")),
        "slides reveal vendored": os.path.isfile(
            os.path.join(PKG, "docs", "slides", "vendor", "reveal", "dist", "reveal.js")),
        "slides landing shipped": os.path.isfile(
            os.path.join(PKG, "docs", "slides", "index.html")),
        "slides diagram svg shipped": os.path.isfile(os.path.join(
            PKG, "docs", "slides", "assets", "img", "diagrams", "director-event-flow.svg")),
        "slides overlay-designer deck shipped": os.path.isfile(
            os.path.join(PKG, "docs", "slides", "overlay-designer.html")),
        "slides who-does-what diagram shipped": os.path.isfile(os.path.join(
            PKG, "docs", "slides", "assets", "img", "diagrams", "who-does-what.svg")),
        "slides cheat-sheet shipped": os.path.isfile(
            os.path.join(PKG, "docs", "slides", "cheat_sheets.html")),
    }
    # solo-mode collections (#303): the same tokenized, secret-free verification
    # as the endurance template above, when shipped.
    for solo in ("GT_Racing_Solo_Commentary.template.json", "GT_Racing_Solo_POV.template.json"):
        sp = os.path.join(PKG, "obs", solo)
        if os.path.exists(sp):
            with open(sp, encoding="utf-8") as fh:
                stpl = fh.read()
            checks[f"{solo} device tokenized"] = (
                "__RACECAST_CAPTURE__" in stpl and "__RACECAST_WEBCAM__" in stpl)
            checks[f"{solo} no secret"] = not has_appscript_secret(stpl)
    bad = [k for k, v in checks.items() if not v]
    print(f"Built {PKG}")
    print(f"ZIP   {zip_path}  ({os.path.getsize(zip_path)//1024} KB)")
    for k, v in checks.items():
        print(f"  [{'OK' if v else 'FAIL'}] {k}")
    m_ph = placeholders.media_placeholder_path()
    for clip in ("intro.mp4", "outro.mp4"):
        path = os.path.join(PKG, "media", clip)
        if _is_placeholder(path, m_ph):
            print(f"  [placeholder] media {clip} (neutral placeholder, real clip not bundled)")
        elif os.path.isfile(path):
            print(f"  [OK] media {clip} present")
        else:
            print(f"  [warn] media {clip} MISSING (run get-media.py before release)")
    g_ph = placeholders.graphic_placeholder_path()
    for fn in placeholders.expected_graphics_from_template(tpl):
        path = os.path.join(PKG, "graphics", fn)
        if _is_placeholder(path, g_ph):
            print(f"  [placeholder] graphic {fn} (neutral placeholder, real asset not bundled)")
        elif os.path.isfile(path):
            print(f"  [OK] graphic {fn} present")
        else:
            print(f"  [warn] graphic {fn} MISSING (run get-graphics.py before release)")
    if bad:
        sys.exit("BUILD VERIFY FAILED: " + ", ".join(bad))


if __name__ == "__main__":
    main()

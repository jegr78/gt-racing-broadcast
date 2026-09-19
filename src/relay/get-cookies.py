#!/usr/bin/env python3
"""Export YouTube OR Twitch cookies from a logged-in browser via yt-dlp.

YouTube (default): exports to <runtime>/yt-cookies.txt against a YouTube probe URL
  (bypasses "Sign in to confirm you're not a bot" checks).
Twitch (--platform twitch): exports to <runtime>/twitch-cookies.txt against twitch.tv
  (captures auth-token for gated/private feeds; public Twitch streams do not need this).

Usage: python3 get-cookies.py [browser] [--runtime-dir DIR] [--platform youtube|twitch]
  browser: firefox | chrome | safari | edge | brave   (default: firefox)
Default runtime dir auto-detects: repo -> <repo>/runtime, distributed package -> next to relay/.
"""
import argparse, os, re, subprocess, sys

# src/scripts holds the shared external_tool_env(); add it to sys.path the way
# racecast-feeds.py does so both a bare `python3 src/relay/get-cookies.py` (source,
# never frozen) and the frozen in-process run resolve it.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (os.path.join(_HERE, "..", "scripts"),
              os.path.join(getattr(sys, "_MEIPASS", _HERE), "src", "scripts")):
    if os.path.isdir(_cand) and _cand not in sys.path:
        sys.path.insert(0, _cand)
from services import external_tool_env  # de-PyInstaller the env for the yt-dlp spawn
import cookie_jar  # the platform-domain filter + the shared login rule (#615, #616)


def default_runtime_dir(here):
    """Match racecast-feeds.py: repo layout (src/relay/) -> <repo>/runtime ; dist (relay/) -> here."""
    if os.path.basename(here) == "relay" and os.path.basename(os.path.dirname(here)) == "src":
        return os.path.join(os.path.dirname(os.path.dirname(here)), "runtime")
    return here


def cookie_target(platform, runtime_dir):
    """(out_path, probe_url) for a cookie export. Pure."""
    if platform == "twitch":
        return os.path.join(runtime_dir, "twitch-cookies.txt"), "https://www.twitch.tv"
    return os.path.join(runtime_dir, "yt-cookies.txt"), "https://www.youtube.com/watch?v=jNQXAC9IVRw"


def failure_hint(stderr_text, browser, platform="youtube"):
    """Actionable hint for a failed yt-dlp cookie export (pure, from stderr)."""
    service = "Twitch" if platform == "twitch" else "YouTube"
    refresh = "racecast cookies twitch firefox" if platform == "twitch" else "racecast cookies firefox"
    s = (stderr_text or "").lower()
    if "could not copy" in s and "cookie database" in s:
        # Chromium locks its cookie DB while running (yt-dlp issue #7271).
        return (f"close {browser} COMPLETELY (all windows; also quit its tray/"
                f"background mode) — it locks its cookie database while running "
                f"— then re-run.")
    if "could not find" in s:
        return f"no {browser} profile found — is {browser} installed on this machine?"
    if "decrypt" in s or "dpapi" in s:
        return (f"{browser}'s cookie encryption blocked the export — log into "
                f"{service} in Firefox and run: {refresh}")
    return f"is {browser} installed and logged in to {service}?"


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("browser", nargs="?", default="firefox")
    ap.add_argument("--runtime-dir", default=default_runtime_dir(here))
    ap.add_argument("--platform", default="youtube", choices=["youtube", "twitch"])
    a = ap.parse_args()
    os.makedirs(a.runtime_dir, exist_ok=True)
    try: os.chmod(a.runtime_dir, 0o700)   # runtime holds the cookie jar — keep it private
    except OSError: pass                  # best-effort hardening; never block the export
    out, url = cookie_target(a.platform, a.runtime_dir)
    print(f"Exporting {a.platform} cookies from '{a.browser}' ...")
    try:
        proc = subprocess.run(["yt-dlp", "--cookies-from-browser", a.browser, "--cookies", out,
                               "--skip-download", "--no-warnings", url],
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120,
                              env=external_tool_env())
    except FileNotFoundError:
        sys.exit("ERROR: yt-dlp not found (brew install yt-dlp / pip install -U yt-dlp).")
    except subprocess.TimeoutExpired:
        sys.exit("ERROR: cookie export timed out (approve the Keychain prompt?).")
    if os.path.exists(out):
        try: os.chmod(out, 0o600)   # live session — owner-only
        except OSError: pass        # best-effort hardening; never block the export
        # yt-dlp writes the whole browser profile; keep only this platform's cookies.
        dropped = cookie_jar.filter_jar(out, a.platform)
        if dropped:
            domains = ", ".join(cookie_jar.PLATFORM_COOKIE_DOMAINS[a.platform])
            print(f"Kept only {domains} cookies (dropped {dropped} from other sites).")
        elif dropped is None:
            print(f"WARNING: could not strip other sites' cookies from {out}.")
        with open(out, encoding="utf-8", errors="replace") as fh:
            txt = fh.read()
        if a.platform == "twitch":
            if re.search(r"auth-token", txt):
                print(f"OK -> {out}  (logged-in session detected)")
            else:
                print(f"WARNING: cookies written but no login found — log into Twitch in "
                      f"'{a.browser}' and re-run (racecast cookies twitch {a.browser}).")
        else:
            if cookie_jar.text_has_login(txt):
                print(f"OK -> {out}  (logged-in session detected)")
            else:
                print(f"WARNING: cookies written but no login found — log into YouTube in "
                      f"'{a.browser}' and re-run.")
    else:
        err = (proc.stderr or b"").decode("utf-8", errors="replace")
        for line in [l for l in err.splitlines() if l.strip()][-3:]:
            print(line, file=sys.stderr)   # the real yt-dlp reason, not a guess
        sys.exit(f"FAILED to export from '{a.browser}' — "
                 + failure_hint(err, a.browser, a.platform))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Launch one backgrounded streamlink server per channel in static public mode,
each with a log and PID file so stop-streams.py can shut them down.
Feeds come from the Control-Center-managed <state-dir>/streams.json when present,
else from the built-in FEEDS default below, as (CHANNEL_ID_or_URL, PORT). Each
channel may be a YouTube channel ID (UC…) or a full youtube.com or twitch.tv URL.
Ports must match the OBS media sources.
PUBLIC channels only. The unlisted flow is the relay (`racecast relay start`).
"""
import argparse, json, os, re, shutil, subprocess, sys
from urllib.parse import urlparse


def state_dir(here):
    """Where PID and log files live: from the repo (src/scripts/) it is
    <repo>/runtime/static, from the distributed package next to the script."""
    if os.path.basename(here) == "scripts" and os.path.basename(os.path.dirname(here)) == "src":
        return os.path.join(os.path.dirname(os.path.dirname(here)), "runtime", "static")
    return here

def feed_argv(frozen, executable, loop_path, channel, port, log_path):
    """Child argv for one feed. The feed OWNS log_path via its own rotating logger;
    start_detached captures only the boot/crash fd to a separate *.boot.log."""
    if frozen:
        return [executable, "streams", "run-feed", channel, port, "--log", log_path]
    return [executable, loop_path, channel, port, "--log", log_path]


def feed_env(frozen, base_env):
    """Env for frozen feed children. They re-run the racecast --onefile binary, and
    PYINSTALLER_RESET_ENVIRONMENT=1 gives each its own _MEIPASS so it outlives this
    parent. racecast.py's _frozen_child_env() does the same; keep the two in sync.
    Repo mode returns None and the child inherits."""
    if not frozen:
        return None
    env = dict(base_env)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def _spawn_kwargs():
    """services.py lives next to this script in both the repo and the bundle.
    Import it lazily so loading this file from a test needs no path setup."""
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("services", os.path.join(here, "services.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.spawn_kwargs(os.name)


# keep in sync with src/relay/racecast-feeds.py
CHANNEL_RE = re.compile(r"^UC[A-Za-z0-9_\-]{20,}$")


# keep in sync with src/relay/racecast-feeds.py
def _is_stream_url(v: str) -> bool:
    """True iff `v` is an http(s) URL on a supported streaming host (YouTube or
    Twitch). The host allow-list blocks SSRF: a tailnet peer (POST /schedule/set,
    /pov/set) or a sheet editor cannot point a feed at an internal/link-local
    address (169.254.169.254, localhost, a LAN box) or a file:// path. A
    userinfo trick (https://youtube.com@evil.com/) resolves to host 'evil.com'
    and is rejected. Twitch is allowed because leagues occasionally run Twitch
    feeds/POVs; broader Twitch handling elsewhere is a separate follow-up."""
    try:
        p = urlparse(v)
    except ValueError:
        return False
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower()
    if not host:
        return False
    return (host == "youtu.be"
            or host == "youtube.com" or host.endswith(".youtube.com")
            or host == "twitch.tv" or host.endswith(".twitch.tv"))


# keep in sync with src/relay/racecast-feeds.py
def is_channel(v: str) -> bool:
    v = v.strip()
    return bool(CHANNEL_RE.match(v)) or _is_stream_url(v)


# Channels as (CHANNEL_ID, PORT).
FEEDS = [
    ("UCNye-wNBqNL5ZzHSJj3l8Bg", "53001"),   # Feed A - TEST: Al Jazeera English (24/7)
    ("UCknLrEdhRCp1aegoMqRaCZg", "53002"),   # Feed B - TEST: DW News (24/7)
    # Replace TEST IDs with the real streamer channel IDs before the event.
]

STREAMS_CONFIG = "streams.json"


def load_feeds(state_dir):
    """Feeds to serve: the Control-Center-managed <state_dir>/streams.json when it
    exists and parses, else the built-in FEEDS default. Returns a list of
    (channel, port) string pairs. An entry missing a channel or port is skipped. An
    entry whose channel is neither a UC… id nor an allowed youtube or twitch URL is
    logged to stderr and skipped, which is the SSRF gate. A malformed or empty file
    falls back to FEEDS so a bad edit never serves nothing."""
    path = os.path.join(state_dir, STREAMS_CONFIG)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        feeds = []
        for e in data:
            ch, port = str(e.get("channel", "")).strip(), str(e.get("port", "")).strip()
            if not (ch and port):
                continue
            if not is_channel(ch):
                print(f"WARN: skipping feed with invalid channel {ch!r}. "
                      f"Use a YouTube channel ID or a youtube/twitch URL", file=sys.stderr)
                continue
            feeds.append((ch, port))
        return feeds or FEEDS
    except (OSError, ValueError, AttributeError, TypeError):
        return FEEDS


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", default=state_dir(here),
                    help="PID/log dir (racecast passes <runtime>/static explicitly).")
    a = ap.parse_args()
    sdir = a.state_dir
    logdir = os.path.join(sdir, "logs")
    os.makedirs(logdir, exist_ok=True)
    if not shutil.which("streamlink"):
        sys.exit("streamlink not found (brew install streamlink / pip install -U streamlink).")
    loop = os.path.join(here, "loopstream.py")
    frozen = bool(getattr(sys, "frozen", False))
    for i, (ch, port) in enumerate(load_feeds(sdir), 1):
        feed_log = os.path.join(logdir, f"feed_{port}.log")
        boot_log = os.path.join(logdir, f"feed_{port}.boot.log")
        with open(boot_log, "ab") as boot:
            p = subprocess.Popen(
                feed_argv(frozen, sys.executable, loop, ch, port, feed_log),
                stdout=boot, stderr=subprocess.STDOUT,
                env=feed_env(frozen, os.environ), **_spawn_kwargs())
        with open(os.path.join(sdir, f"feed_{port}.pid"), "w") as fh:
            fh.write(str(p.pid))
        print(f"Started Feed {i} -> channel {ch} on http://127.0.0.1:{port} (log: {feed_log})")
    print("\nAll feeds launched. Point each OBS media source at its http://127.0.0.1:PORT.")
    print("Stop everything with:  racecast streams stop")


if __name__ == "__main__":
    main()

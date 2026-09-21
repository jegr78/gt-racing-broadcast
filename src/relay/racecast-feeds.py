#!/usr/bin/env python3
"""
racecast-feeds.py — Relay mode for the GT Racing Broadcast (2-feed ping-pong)
with a remotely-maintainable stint schedule from a Google Sheet.

Concept: exactly one commentator stream per stint. Two fixed OBS feeds
(Feed A = port 53001, Feed B = port 53002) "walk" along a stint schedule.
Feed A serves the odd stints (1,3,5…), Feed B the even ones (2,4,6…) when
starting from stint 1 — with `--stint N` (producer takeover) the same
ping-pong simply starts at stint N on Feed A. At each handover the feed
that is currently NOT on air advances to its next commentator.

OBS stays untouched: the media sources keep pointing at
http://127.0.0.1:53001 / :53002 — only the channel behind them changes.

SCHEDULE SOURCE (Google Sheet, editable remotely by anyone):
  The "Schedule" tab of the shared HUD sheet is read via CSV export
  (no API key). Any column holds the channel IDs (UC…) OR full URLs in
  stint order — the channel column is auto-detected (other columns such
  as stint number / commentator name are ignored).
  Requirement: the sheet is shared "Anyone with the link: Viewer"
  (same as the HUD sheet).

  Safe live behavior: a RUNNING feed is NOT torn off mid-stint by sheet
  edits. Changed entries take effect on the next /next (handover), where
  fresh data is fetched. To swap the CURRENT stream immediately, use /reload.

  If the sheet is unreachable, the last working list is kept (last-good +
  local cache); without a sheet the local schedule.txt is used.

COOKIES:
  YouTube (required for unlisted/private streams): put a yt-cookies.txt
  (Netscape format, exported from a logged-in YouTube browser via
  `racecast cookies firefox`) next to this script — it is auto-detected
  and passed to yt-dlp. A legacy cookies.txt is migrated automatically.
  Or pass it explicitly: --cookies /path/to/yt-cookies.txt
  Twitch (optional): gated/follower-only feeds use twitch-cookies.txt
  (exported via `racecast cookies twitch firefox`); public Twitch streams
  need no cookies at all. The relay picks it up automatically for Twitch feeds.

SCHEDULE ENTRIES — use a full watch URL for each stint:
  YouTube: https://www.youtube.com/watch?v=VIDEOID  (bare UC… channel IDs
    also work as a shorthand for YouTube public /live streams; unlisted streams
    MUST use the full watch URL — the channel /live URL only works for public).
  Twitch:  https://www.twitch.tv/<channel>  (no bare-ID short form for Twitch).

Controls (HTTP, for Companion Generic-HTTP / browser / curl):
  GET /status          -> state of both feeds + source/sheet health (JSON)
  GET /next            -> fetch fresh & advance the off-air feed by 1 stint
  GET /next/A | /B      -> advance Feed A or B specifically
  GET /prev/A | /B      -> step back one (correction)
  GET /set/A/<n> | /B   -> pin Feed A/B to schedule index <n>
  GET /set/stint/<n>   -> producer takeover: stint <n> (1-based!) is on air now —
                           Feed A serves it, Feed B preloads <n+1> (tears running feeds)
  GET /reload          -> reconnect both feeds (applies a sheet change to the
                           CURRENT channel immediately)
  GET /reload/A | /B    -> reconnect only Feed A or B
  GET /pov/reload      -> re-read the POV sheet cell & (re)connect the POV PiP feed
  GET /pov/stop        -> stop the POV PiP pull (close port 53003, free bandwidth)
  GET /timer/data      -> timer state JSON (anchor, mode, sync health)
  GET /timer/start | /timer/stop | /timer/reset | /timer/show | /timer/hide
                          (start resumes a paused timer; stop = pause;
                           reset = back to the full duration)
  GET /timer/set/<H:MM:SS>     -> set the race duration (next start)
  GET /timer/adjust/<±seconds> -> shift a RUNNING timer (correction)
  Stop: Ctrl+C
"""

import argparse, collections, csv, datetime, hmac, html, io, ipaddress, json, logging, os, random, re, secrets, shutil, signal, socket, ssl, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, quote, unquote, parse_qs, urlencode
from urllib.request import Request, urlopen

# OBS reflection (best effort). obs_ws lives in src/scripts (repo) or the
# bundled tree (frozen). A missing client just disables reflection — it must
# never break the relay.
_REL_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (os.path.join(_REL_HERE, "..", "scripts"),
              os.path.join(getattr(sys, "_MEIPASS", _REL_HERE), "src", "scripts")):
    if os.path.isdir(_cand) and _cand not in sys.path:
        sys.path.insert(0, _cand)
try:
    import obs_ws as _obs_ws
except Exception:                                # noqa: BLE001 — reflection is optional
    _obs_ws = None
# #537: a stable reference to the real module, captured at import time. Tests swap
# the module-level `_obs_ws` name above to fakes/None to stub OBS calls; capturing a
# second name here means the persistent-connection classes (_ObsConn etc.) are
# always available even when `_obs_ws` itself currently points at a fake.
_OBS_WS_MODULE = _obs_ws


class _RelayObsFacade:
    """Routes Relay/handler obs_ws.* calls (#537) through two persistent connection
    holders — but ONLY when the module-level `_obs_ws` name currently refers to the
    real obs_ws module (recognisable by its `route_kind` helper). Every lookup is
    late-bound to `_obs_ws` at CALL time (not frozen at Relay construction), so a
    test that monkeypatches `_obs_ws` to a lightweight fake — before or after a Relay
    already exists — still gets a direct, unwrapped call to that fake, exactly like
    the pre-#537 `_obs_ws.<fn>(...)` call sites (no surprise `session=` kwarg, no
    real socket connect attempt from a unit test). Only the real module's routed
    functions ever go through `_ObsConn`/`_PassthroughConn.run()`."""

    def __init__(self, shot_conn, ctrl_conn):
        self._shot, self._ctrl = shot_conn, ctrl_conn

    def __getattr__(self, name):
        target = _obs_ws                    # re-read the CURRENT module-level name
        if target is None:
            raise AttributeError(name)
        fn = getattr(target, name)
        # Route only when the current target is the real module (it exposes route_kind);
        # a test's fake obs_ws has none -> the call goes straight through (no session=).
        route_kind = getattr(target, "route_kind", None)
        kind = route_kind(name) if route_kind is not None else None
        if kind is not None:
            conn = self._shot if kind == "shot" else self._ctrl
            return lambda *a, **k: conn.run(fn, *a, **k)
        return fn                           # constants / pure helpers / an unrouted fake

    def close(self):
        self._shot.close()
        self._ctrl.close()

import chat_admin  # required (ChatStore); src/scripts is on sys.path via the block above
import broadcast_chat  # read-only YouTube broadcast-chat reader (#294); pure parsers
import event_notes   # noqa: E402  (pure Event Notes tab parser)
import resources  # noqa: E402, F401 - machine resource sampler (health history)
import cue_admin   # director text-cue channel (#243)
import flag_graphic   # flag-status graphics: value->source + persisted store (#flag-graphic)
import health_store  # health-history SQLite store (task 7; src/scripts on sys.path)
_HEALTH_CONST = health_store  # stable module alias: make_handler's `health_store`
# PARAMETER shadows the module name inside its closure, so the constants
# (DEFAULT_MAX_POINTS / LIVE_WINDOW_S) are reached via this un-shadowed alias.
import console_auth   # commentator-cockpit token auth (#191); pure, src/scripts on sys.path
import console_admin  # commentator-cockpit revocation version store (#191)
import app_version   # shared build-version helper (single source of truth)
import discord_oauth  # Discord OAuth2 helpers for /console/login
import stream_target   # noqa: E402 — pure stream-target resolver (ref/platform/key response)
import producer as producer_mod   # noqa: E402 — pure Producer-tab parser (name 'producer' is used as a local elsewhere)
import parts as parts_mod         # noqa: E402 — pure Part view-model + validators (name 'parts' is a local elsewhere)

# Running build version, resolved like racecast.py: the VERSION file is stamped
# into the source-tree root by tools/build-binary.py (frozen: <_MEIPASS>/src),
# absent in a repo checkout -> 'dev'. Injected into served pages as __RC_VERSION__.
_SRC_BASE = (os.path.join(sys._MEIPASS, "src") if getattr(sys, "frozen", False)
             else os.path.join(_REL_HERE, ".."))
APP_VERSION = app_version.read_version(_SRC_BASE)
VERSION_LABEL = ("v" + APP_VERSION) if APP_VERSION != "dev" else "dev"
import cockpit_submissions  # commentator stream-link submission store (#193)
import notify   # pure Discord payload builders for producer events (#317)
import console_policy  # /console authorization matrix + decision (#216)
import console_proxy      # pure /console/buttons proxy plumbing (#236)
import install_apps       # companion_http_version for the buttons health probe (#236)
import companion_common   # companion config.json path for bind-address resolution (#236)
import tailscale          # detect_tailscale_ip fallback for bind-address resolution (#236)
import logsetup  # rotating per-feed/console loggers + streamlink pump (src/scripts on sys.path)
import av_sync   # pure parser/classifier for the A/V sync disturbances OBS logs (#619)
import cookie_jar  # the shared "jar holds a YouTube login" rule, same as preflight (#615)
import placeholders  # transparent-graphic placeholder path -> hide pure-placeholder assets from the browser
import gt7_crypto      # GT7 UDP telemetry: Salsa20 decrypt (solo/POV only, #324)
import gt7_telemetry   # GT7 UDP telemetry: packet parser + TelemetryStore (solo/POV only, #324)
from services import external_tool_env  # de-PyInstaller the env for spawned external tools

# Module-level relay logger. main() attaches the file/console handlers via
# logsetup.configure_logging("racecast.relay", …); logging.getLogger returns the
# same instance, so call sites here log to the configured handlers without a
# threaded parameter. Safe to use before configuration (no handler -> no output).
LOG = logging.getLogger("racecast.relay")

# ---------- Configuration ----------
# Pipeline: yt-dlp resolves the live HLS URL (passes YouTube's bot-check via
# cookies + JS-challenge solving) -> streamlink serves that direct URL to OBS
# (no YouTube plugin involved, so no bot-check on the serving side).
YTDLP_FORMAT = "b[height<=1080]/b"   # prefer <=1080p, auto-fall back to lower

# Formats are unioned across these clients: the default one sometimes offers no MUXED
# format at all, and the relay needs one url for one streamlink. `mweb` over `android`,
# which YouTube bot-flags most readily.
YTDLP_PLAYER_CLIENTS = "default,mweb"
STREAMLINK_SERVE = ["--ringbuffer-size", "64M", "--hls-live-edge", "4"]
# "Stop early on missing live segments" tolerance. Default 3 gave up at ~6 s
# (targetduration ~2 s) — BELOW the relay's own 8 s byte-stall watchdog (FANOUT_STALL_S) —
# so a brief upstream hiccup made streamlink quit prematurely and forced a full re-serve
# (the 2026-07-10 cascade). QUEUE_DEADLINE_FACTOR=5 pushes streamlink's give-up past the
# watchdog: a sub-8 s gap self-heals with NO restart, a genuine stall is still caught by
# the watchdog. The CLI flag was RENAMED in streamlink 8.1.0
# (--hls-segment-queue-threshold -> --stream-segmented-queue-deadline), so the exact flag
# is chosen per installed streamlink at serve time (queue_deadline_args) — an unknown flag
# would make streamlink exit and the feed never serve.
QUEUE_DEADLINE_FACTOR = "5"


def queue_deadline_args(help_text, factor=QUEUE_DEADLINE_FACTOR):
    """Pick streamlink's 'stop early on missing live segments' flag by CAPABILITY, from
    `streamlink --help` text — never by hardcoded version. Prefer the modern
    --stream-segmented-queue-deadline (streamlink 8.1.0+), fall back to the older
    --hls-segment-queue-threshold, and OMIT entirely when neither exists (an unknown flag
    would abort streamlink → the feed never serves). Pure → unit-tested."""
    if "--stream-segmented-queue-deadline" in help_text:
        return ["--stream-segmented-queue-deadline", factor]
    if "--hls-segment-queue-threshold" in help_text:
        return ["--hls-segment-queue-threshold", factor]
    return []


_STREAMLINK_HELP = None


def _streamlink_help():
    """`streamlink --help` text, run once and cached. Best-effort: any failure yields ""
    so queue_deadline_args degrades to omitting the flag (never a hard feed failure)."""
    global _STREAMLINK_HELP
    if _STREAMLINK_HELP is None:
        try:
            # errors="replace" like every other subprocess.run in this file: on a
            # German Windows the console codepage is cp1252, streamlink's help text
            # carries a byte it cannot decode, and the failure lands in subprocess's
            # reader THREAD — so the `except` below never sees it and the relay just
            # prints a traceback and loses the help text (and with it the queue
            # deadline flag). Seen on the producer host 2026-09-20.
            _STREAMLINK_HELP = subprocess.run(
                ["streamlink", "--help"], capture_output=True, text=True,
                errors="replace", timeout=10, env=external_tool_env()).stdout or ""
        except Exception:                     # noqa: BLE001 — best-effort probe
            _STREAMLINK_HELP = ""
    return _STREAMLINK_HELP
# yt-dlp resolves the YouTube manifest with a browser UA; streamlink then re-fetches
# that URL in a SEPARATE process. YouTube 403s the bare re-fetch of a protected live
# manifest unless it carries the same browser UA (and, for unlisted/members streams,
# the same cookies). This is the canonical streamlink+YouTube remedy and matches the
# UA the Innertube poller already uses (_YT_CHAT_UA). (#345 — first-live-event 403.)
STREAMLINK_YT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
# Twitch is served DIRECTLY by Streamlink's twitch plugin (no yt-dlp hop), so its
# plugin options apply: low-latency prefetch + a tighter live edge. Ad filtering is
# automatic in current Streamlink (the old --twitch-disable-ads is deprecated).
STREAMLINK_TWITCH = ["--ringbuffer-size", "64M", "--hls-live-edge", "2", "--twitch-low-latency"]

# --- Quality profiles (#493): each tier = a rendition cap + a streamlink profile ---
# FULL = best available up to 1080p (never forces 1080p; bounded by the source's max).
# ROBUST = 720p floor (the automatic step-down target). EMERGENCY = sub-720p, operator-only.
QUALITY_TIERS = ("full", "robust", "emergency")
ROBUST_STEP_DOWN_AFTER = 2       # consecutive dead (short) serves before an auto FULL->ROBUST

# Robust streamlink profile: more buffered segments at the live edge -> rides out short
# source jitter, trading latency for stability. Applied at ROBUST and EMERGENCY.
STREAMLINK_SERVE_ROBUST = ["--ringbuffer-size", "128M", "--hls-live-edge", "6"]
STREAMLINK_TWITCH_ROBUST = ["--ringbuffer-size", "128M", "--hls-live-edge", "2",
                            "--twitch-low-latency"]

_QUALITY_YTDLP = {"full": "b[height<=1080]/b",
                  "robust": "b[height<=720]/b",
                  "emergency": "b[height<=480]/w"}
_QUALITY_TWITCH = {"full": "best",
                   "robust": "720p60,720p",
                   "emergency": "480p,360p,worst"}
_QUALITY_HEIGHT_RE = re.compile(r"(\d{3,4})p")


def quality_ytdlp_fmt(tier):
    """yt-dlp -f string for a quality tier. Pure → unit-tested."""
    return _QUALITY_YTDLP.get(tier, _QUALITY_YTDLP["full"])


def quality_twitch_selector(tier):
    """Streamlink quality positional for a quality tier (Twitch). Pure → unit-tested."""
    return _QUALITY_TWITCH.get(tier, _QUALITY_TWITCH["full"])


def streamlink_serve_flags(tier):
    """YouTube streamlink buffer/live-edge flags for a tier (robust at <=ROBUST). Pure."""
    return STREAMLINK_SERVE_ROBUST if tier in ("robust", "emergency") else STREAMLINK_SERVE


def streamlink_twitch_flags(tier):
    """Twitch streamlink buffer/live-edge flags for a tier. Pure."""
    return STREAMLINK_TWITCH_ROBUST if tier in ("robust", "emergency") else STREAMLINK_TWITCH


def serve_flags(platform, tier):
    """The streamlink buffer/live-edge flags a serve actually runs with. THE one place
    that decides it: both command builders and the rejoin's prefetch wait read it here,
    so a new platform or tier can never hand the command one answer and the wait
    another — and the wait's drift would be silent, since a wrong wait produces a
    backlog rather than an error. Pure -> unit-tested."""
    return streamlink_twitch_flags(tier) if platform == "twitch" else streamlink_serve_flags(tier)


def parse_quality_tier(value):
    """Normalise an operator-supplied tier to one of full|robust|emergency|auto, else
    None (so the endpoint can 400). `auto` = release a manual pin. Pure → unit-tested."""
    if not value:
        return None
    v = value.strip().lower()
    return v if v in ("full", "robust", "emergency", "auto") else None


def quality_height(token):
    """Numeric vertical resolution of a streamlink quality token ('720p60' -> 720),
    or None for non-heighted tokens ('best', 'audio_only', None). Pure → unit-tested."""
    if not token:
        return None
    m = _QUALITY_HEIGHT_RE.search(token)
    return int(m.group(1)) if m else None


def quality_step_down_due(tier, pinned, dead_serves, source_state,
                          *, threshold=ROBUST_STEP_DOWN_AFTER):
    """True when a feed should auto-step-down FULL->ROBUST: only while not manually
    pinned, only from FULL (720p is the hard floor), only for a live-but-degraded
    source (source_state None — an offline/not-live/ended source has no picture at any
    rendition, so a lower cap cannot help), once dead (short) serves reach `threshold`.
    Pure → unit-tested."""
    return (not pinned) and tier == "full" and source_state is None \
        and dead_serves >= threshold

RESOLVE_RETRY = 15  # seconds between yt-dlp resolve attempts while a stint isn't live
COOKIE_MAX_AGE_H = 12   # keep in sync with preflight.py cookies_status(max_age_hours)
RETRY_SLEEP = 10    # seconds after a stream ends / manifest expires before re-resolving
FEED_FAST_EXIT_S = 3.0  # a serve proc dying faster than this (non-zero) = a bind/launch failure, not a stint
DEAD_SERVE_BACKOFF_CAP = 300   # s — max delay between re-attempts of a fast-dying ("was live, now dead") serve
DEAD_SERVE_IDLE_AFTER = 5      # consecutive dead serves -> go idle until the operator advances/reloads


def dead_serve_backoff(count, base=RETRY_SLEEP, cap=DEAD_SERVE_BACKOFF_CAP):
    """Escalating sleep (seconds) before re-attempting a stint whose serve keeps
    dying fast (the 403/expired-manifest 'was live, now dead' case): base, 2x base,
    4x base ... capped at `cap`. `count` is the number of consecutive dead serves so
    far (>= 1); a count below 1 returns `base`. Pure so the schedule is unit-tested
    without sleeping."""
    return min(base * (2 ** max(count - 1, 0)), cap)


def should_idle_dead_serves(count, limit=DEAD_SERVE_IDLE_AFTER):
    """True once consecutive dead serves reach `limit` -> the feed stops re-spawning
    and idles until the operator advances (/next) or reloads (/reload)."""
    return count >= limit


def feed_fast_exit_error(elapsed_s, returncode, fast_exit_s=FEED_FAST_EXIT_S):
    """A short last_error when a feed's streamlink exited almost immediately with a
    non-zero code — almost always a failed --player-external-http bind (an orphan
    still holds the port; the #133 case). Returns None for a clean (rc 0), unknown
    (rc None), or long-lived exit, so the normal serving path never sets a spurious
    error. Pure → unit-tested in tests/test_pov.py (issue #143)."""
    if returncode in (0, None):
        return None
    if elapsed_s is None or elapsed_s >= fast_exit_s:
        return None
    return "feed exited immediately — port in use? see feed log"


def serve_exit_is_drop(stopped, advancing):
    """A serving feed's streamlink process exited. It is an unexpected DROP — the
    live picture was lost — UNLESS the exit was intentional: a relay shutdown
    (`stopped`) or a director handover/reload (`advancing`). Drives the panel's
    feed-down alert so it fires on a real loss, not on every normal handover.
    Pure → unit-tested in tests/test_pov.py."""
    return not stopped and not advancing


# Source-not-live signatures (#495) — matched case-insensitively as substrings against
# the yt-dlp/streamlink diagnostic text. ENDED is checked first (more specific).
# The "live_status …" entries match the suffix resolve_hls appends to a 'Requested
# format is not available' error (#621): an ended broadcast in Post-Live Manifestless
# mode fails with that text, like a logged-out jar, and only the live status differs.
_SOURCE_ENDED = (
    "this live event has ended",
    "live_status post_live",         # yt-dlp: broadcast over, recording not processed yet
    "live_status was_live",          # yt-dlp: broadcast over, recording available
)
_SOURCE_NOT_LIVE_YET = (
    "no playable streams found",     # Twitch: channel offline / not live yet
    "not currently live",            # yt-dlp YouTube: channel not live
    "will begin in",                 # YouTube: scheduled premiere not started
    "live_status is_upcoming",       # yt-dlp: scheduled, not started
)


def classify_source_state(text):
    """Classify a feed's yt-dlp/streamlink diagnostic *text* into a source-not-live
    state, or None for a generic drop/error (#495). Case-insensitive substring match.
    Pure → unit-tested.
      "not_live_yet" — source offline / not started (Twitch 'No playable streams
                       found', yt-dlp 'not currently live').
      "ended"        — source's live broadcast is over (YouTube 'This live event has
                       ended', or a post-live / was-live status from the resolve).
      None           — anything else (429/403/network/generic) — unchanged behaviour."""
    if not text:
        return None
    low = text.lower()
    if any(s in low for s in _SOURCE_ENDED):
        return "ended"
    if any(s in low for s in _SOURCE_NOT_LIVE_YET):
        return "not_live_yet"
    return None


# ---------- Live health heartbeat (aggregate status + Discord alerts) --------
# Spec: docs/superpowers/specs/2026-06-16-live-heartbeat-design.md
HEARTBEAT_INTERVAL_S = 30        # how often the relay re-evaluates health
HEALTH_CONNECTING_S = 45         # a feed connecting longer than this (not down) = yellow
HEALTH_DROP_GRACE_S = 30         # a dropped feed must stay down this long (one heartbeat) before it escalates to red
HEALTH_SERVED_OK_S = 10          # a serve must last this long to count as a stable live picture (turns served_ok sticky)
HEALTH_CONNECTING_SETTLE_S = 15  # a just-dropped served feed must stay down this long before it's a NOTIFIABLE "stuck connecting" — a quicker reconnect (e.g. a fan-out EOF/stall re-serve) is a silent blip, no @here. Below the red grace so a longer stall still surfaces yellow before red.
HEALTH_COLORS = {                # Discord embed sidebar colour per level
    "green": 0x2ECC71, "yellow": 0xF1C40F, "red": 0xE74C3C}
_HEALTH_LABEL = {"green": "OK", "yellow": "DEGRADED", "red": "CRITICAL"}

# ---------- Feed fan-out stall detection (relay feed multiplexing, #358) --------
# Superseded + nothing completed for this long = abandoned. Must outlast the slowest
# legitimate sendall to a slow consumer, hence well above RACECAST_FEED_STALL_S.
FANOUT_STALE_GRACE_S = 30.0
FANOUT_STALL_S = 8.0   # seconds without a byte from streamlink before a fan-out reader is "stalled"
FANOUT_RING_BYTES = 16 * 1024 * 1024  # per-feed ring window (bounded; ≈12 s at 10 Mbps). Not a safety lever (#581): a larger ring only delays a slow consumer's overflow and hides it longer.
MARK_MIN_INTERVAL_S = 0.1        # #533: throttle the FeedRing time index to ~1 mark/100 ms
DEFAULT_FEED_PREBUFFER_S = health_store.DEFAULT_FEED_PREBUFFER_S   # #533: seconds a broadcast consumer joins behind the fan-out live edge
REBUILD_GUARD_MAX_ATTEMPTS = 3  # #582: consecutive ineffective automatic OBS rebuilds before the relay stands down
_FANOUT_FALSEY = {"0", "false", "no", "off"}


def fanout_enabled(environ):
    """True unless RACECAST_FEED_FANOUT is an explicit falsey token. Default ON
    (#358, live-verified 2026-06-29); set RACECAST_FEED_FANOUT=0 to fall back to
    the proven direct-serve path. Pure so the switch is unit-testable."""
    return str(environ.get("RACECAST_FEED_FANOUT", "")).strip().lower() not in _FANOUT_FALSEY


def obs_ws_persist_enabled(environ):
    """Reuse two persistent obs-websocket connections instead of connect-per-call
    (#537). Default ON; a falsey RACECAST_OBS_WS_PERSIST restores connect-per-call. Pure."""
    return str(environ.get("RACECAST_OBS_WS_PERSIST", "")).strip().lower() not in _FANOUT_FALSEY


def feed_robust_auto_enabled(environ):
    """#493 auto FULL->ROBUST step-down. Default ON; RACECAST_FEED_ROBUST_AUTO=0 disables
    only the automatic step-down (manual switching always remains). Pure → unit-tested."""
    return (environ.get("RACECAST_FEED_ROBUST_AUTO") or "").strip().lower() not in _FANOUT_FALSEY


feed_prebuffer_s = health_store.feed_prebuffer_s   # #533; shared with the report (#586)


def _env_float(environ, key, default):
    """Parse a positive float env override; fall back to `default` on absent/empty/
    non-numeric/<=0. Pure."""
    try:
        v = float(str(environ.get(key, "")).strip())
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


FEED_STALL_FLOOR_S = 1.0          # #535: min inbound gap (s) that can count as a stall (prebuffer=0 guard)


def feed_stall_floor_s(environ):
    """Floor for the inbound-stall signal (#535). Pure."""
    return _env_float(environ, "RACECAST_FEED_STALL_FLOOR_S", FEED_STALL_FLOOR_S)


def feed_stall_signal_enabled(environ):
    """True unless RACECAST_FEED_STALL_SIGNAL is an explicit falsey token (#535). Default ON.
    When off the gap is still sampled to the DB but never contributes the yellow reason. Pure."""
    return str(environ.get("RACECAST_FEED_STALL_SIGNAL", "")).strip().lower() not in _FANOUT_FALSEY


# #583: the "behind live" rule lives in health_store so the post-event report (#586)
# applies the same definition as the yellow health reason.
FEED_BACKLOG_WARN_S = health_store.FEED_BACKLOG_WARN_S
feed_backlog_warn_s = health_store.feed_backlog_warn_s
feed_backlog_degraded = health_store.feed_backlog_degraded


def fold_backlog_floor(prev, age):
    """Fold one per-cycle backlog sample into the interval floor (#583). None-safe. Pure."""
    if age is None:
        return prev
    return age if prev is None else min(prev, age)


def feed_inbound_degraded(max_gap_s, prebuffer_s, floor_s):
    """#535: an interval's max inbound inter-arrival gap is a stutter risk when it exceeds
    BOTH the #533 reserve (prebuffer_s — a gap the prebuffer absorbed is fine) and the floor
    (so prebuffer=0 still needs a real gap). Pure."""
    return max_gap_s > max(prebuffer_s, floor_s)


def update_max_gap(prev_max, last_byte_ts, now):
    """Fold one inter-arrival gap into the running per-interval max (#535). Pure: no prior
    byte -> unchanged; else max(prev, now-last_byte_ts)."""
    if last_byte_ts is None:
        return prev_max
    return max(prev_max, now - last_byte_ts)


def feed_freeze_detect_enabled(environ):
    """True unless RACECAST_FEED_FREEZE_DETECT is an explicit falsey token. Default ON: the
    relay auto-reconnects a feed's OBS input when OBS's mediaCursor stops advancing — a
    stale-demuxer freeze/stutter the renderSkip signal is blind to (#488, measured live
    2026-07-15). Pure so the switch is unit-testable."""
    return str(environ.get("RACECAST_FEED_FREEZE_DETECT", "")).strip().lower() not in _FANOUT_FALSEY


def feed_backlog_shed_enabled(environ):
    """True unless RACECAST_FEED_BACKLOG_SHED is an explicit falsey token. Default ON: the
    relay rebuilds the on-air feed's OBS input by itself when its consumer stays behind
    the live edge, instead of waiting for a director to press RESET.

    This is its OWN switch rather than a mode of RACECAST_FEED_FREEZE_DETECT, because the
    two detect different faults and a producer turning one off must not silently lose the
    other. Pure so the switch is unit-testable."""
    return str(environ.get("RACECAST_FEED_BACKLOG_SHED", "")).strip().lower() not in _FANOUT_FALSEY


def feed_backlog_shed_ticks(environ):
    """Consecutive heartbeats the on-air feed must be classified backlogged before the shed
    acts. Default 1, because the classified value is the interval FLOOR (a minimum), so one
    tick already means the feed was late for the whole 30 s. Raise it to trade reaction time
    for certainty. Below 1 is meaningless and reads as the default. Pure."""
    return max(1, int(_env_float(environ, "RACECAST_FEED_BACKLOG_SHED_TICKS", 1)))


def feed_freeze_stall_ratio(environ):
    """Cursor-progress ratio (Δcursor/Δwall) below which a sample counts as a STALL tick.
    Default 0.25 (made <25% of real-time progress that tick). #488. Pure."""
    return _env_float(environ, "RACECAST_FEED_FREEZE_STALL_RATIO", 0.25)


def feed_freeze_frac_threshold(environ):
    """Fraction of stalled ticks in the window at/above which the detector reconnects OBS.
    Default 0.30. #488. Pure."""
    return _env_float(environ, "RACECAST_FEED_FREEZE_FRAC", 0.30)


def feed_freeze_window(environ):
    """How many recent cursor samples form the stall-fraction window. Default 10. #488. Pure."""
    return int(_env_float(environ, "RACECAST_FEED_FREEZE_WINDOW", 10))


def feed_freeze_interval_s(environ):
    """Seconds between cursor samples — the freeze sampler cadence, finer than the 30 s
    heartbeat since stutter stalls recur every few seconds. Default 3.0. #488. Pure."""
    return _env_float(environ, "RACECAST_FEED_FREEZE_INTERVAL_S", 3.0)


def feed_freeze_cooldown_s(environ):
    """Min seconds between freeze auto-reconnects (anti-loop). Default 60.0. #488. Pure."""
    return _env_float(environ, "RACECAST_FEED_FREEZE_COOLDOWN_S", 60.0)


def feed_stall_s(environ):
    """Byte-stall hard-kill grace (s) for the fan-out watchdog. #488: raised from the
    hardcoded 8 s so streamlink's internal HLS retry can bridge a recoverable uplink
    micro-stall before a full re-resolve. Soak-tuned."""
    return _env_float(environ, "RACECAST_FEED_STALL_S", 20.0)


# ---------- Auto-failover to the Intermission scene (#378) ------------------
_FAILOVER_TRUTHY = {"1", "true", "yes", "on"}


def auto_failover_enabled(environ):
    """True only when RACECAST_AUTO_FAILOVER is an explicit truthy token. OFF by
    default (opt-in): a frozen/black on-air frame is bad, but silently yanking the
    program is a producer's call to enable. Pure so the switch is unit-testable."""
    return str(environ.get("RACECAST_AUTO_FAILOVER", "")).strip().lower() in _FAILOVER_TRUTHY


_MANUAL_FEED_ARM_FALSEY = {"0", "false", "no", "off"}


def manual_feed_arm_enabled(environ):
    """True unless RACECAST_MANUAL_FEED_ARM is an explicit falsey token. Default ON:
    feed URLs are entered into the schedule but do NOT pull until the director arms
    the feed, and `/next` auto-stops the outgoing feed after a handover cut — the
    durable single-puller workflow (#489/#505). Set RACECAST_MANUAL_FEED_ARM=0 to
    restore the legacy auto-pull + seamless whole-stint pre-roll. Pure so the switch
    is unit-testable."""
    return str(environ.get("RACECAST_MANUAL_FEED_ARM", "")).strip().lower() not in _MANUAL_FEED_ARM_FALSEY


# ---------- On-air program-audio monitor (#program-audio) ------------------
_PROGRAM_AUDIO_FALSEY = {"0", "false", "no", "off"}


def program_audio_enabled(environ):
    """True unless RACECAST_PROGRAM_AUDIO is an explicit falsey token. Default ON:
    the feature is offered (endpoints + toggle live) whenever fan-out runs; the
    encoder is on-demand so it costs nothing until someone listens. Set
    RACECAST_PROGRAM_AUDIO=0 to disable entirely. Pure so the switch is
    unit-testable. Audio being default-muted is a front-end/gesture property, not
    this flag."""
    return str(environ.get("RACECAST_PROGRAM_AUDIO", "")).strip().lower() not in _PROGRAM_AUDIO_FALSEY


# ---------- GT7 UDP telemetry (solo/POV only, #324) -------------------------
_TELEMETRY_FALSEY = {"0", "false", "no", "off"}


def telemetry_enabled(environ):
    """True unless RACECAST_GT7_TELEMETRY is an explicit falsey token. Default ON
    in solo; the listener idles harmlessly if no console answers. Pure so the
    switch is unit-testable."""
    return str(environ.get("RACECAST_GT7_TELEMETRY", "")).strip().lower() not in _TELEMETRY_FALSEY


def telemetry_active(solo, environ):
    """GT7 telemetry (the UDP listener + the /telemetry/* endpoints + the HUD
    telemetry block) is POV-only. It runs only in a solo POV broadcast: the driver
    streams their own console, which is the sole source of telemetry. A solo
    COMMENTARY broadcast spectates someone else's stream and has no telemetry, so
    it must NOT start the listener or serve the endpoints (otherwise the HUD's
    telemetry block would show up empty). Gate = solo AND template 'pov' AND not
    explicitly disabled. Pure so it is unit-testable."""
    return (bool(solo)
            and telemetry_enabled(environ)
            and str(environ.get("RACECAST_TEMPLATE", "")).strip().lower() == "pov")


def should_failover(enabled, on_air_down, program_scene,
                    on_air_scene="Stint", already_failed_over=False):
    """Whether to auto-switch OBS to the Intermission scene right now. Pure →
    unit-tested. Fires iff: the feature is armed, the on-air feed is CONFIRMED
    down (feed_health_state == 'down', i.e. a lost picture past the red grace),
    OBS is STILL on the on-air feed scene (never yank a producer who already cut
    to Intermission/Intro/replay/…), and we have not already failed over this
    outage (fire once; a manual return to the feed scene re-arms it)."""
    if not enabled or already_failed_over or not on_air_down:
        return False
    return program_scene == on_air_scene


# ---------- Auto-cover: raise the Standby Cover on an offline on-air source (#495) ----
AUTO_COVER_POLL_S = 5                     # how often the auto-cover tick evaluates the on-air feed
AUTO_COVER_SETTLE_S = 12                  # source_state must persist this long before the cover raises
STANDBY_COVER_SOURCE = "Standby Cover"    # the #378 cover source in the Stint scene
_AUTO_COVER_FALSEY = {"0", "false", "no", "off"}


def auto_cover_enabled(environ):
    """True unless RACECAST_OBS_AUTO_COVER is an explicit falsey token. Default ON
    (opt-out): the automatic raise/lower is on; setting the flag falsey disables ONLY
    the automation (the manual RED FLAG / Companion toggle still works). Pure so the
    switch is unit-testable."""
    return str(environ.get("RACECAST_OBS_AUTO_COVER", "")).strip().lower() not in _AUTO_COVER_FALSEY


def auto_cover_action(enabled, source_state, offline_since, now, settle_s,
                      cover_shown, auto_owned, cover_fired,
                      program_scene, on_air_scene="Stint"):
    """Return "raise", "lower", or None for the auto-cover tick. Pure → unit-tested.
    - "lower": auto lowers ONLY a cover it owns, and only once the source recovered
      (source_state is None). Runs even when disabled so flipping the flag off
      mid-outage never strands an auto-raised cover.
    - "raise": once per outage, on-air source offline/ended past the settle, cover not
      already shown, and OBS still on the on-air scene.
    - None: everything else (manual covers are never auto-lowered; the manual button
      always works)."""
    if cover_shown and auto_owned and source_state is None:
        return "lower"
    if not enabled:
        return None
    if source_state in ("not_live_yet", "ended") and offline_since is not None \
            and (now - offline_since) >= settle_s \
            and not cover_shown and not cover_fired \
            and program_scene == on_air_scene:
        return "raise"
    return None


# The program monitor is the SAME image for every viewer, yet each open console
# view (Director Panel /preview/program, Cockpit + Race-Control /cockpit/program)
# polls it every ~1.5s and each poll previously opened its own obs-websocket
# connection to screenshot OBS. With several views open that churned OBS at several
# connect/close cycles per second (23k+ in one N24 session, bloating the OBS log).
# A short server-side TTL cache coalesces those polls: OBS is screenshotted at most
# once per TTL regardless of how many views are watching.
PROGRAM_SHOT_TTL_S = 1.0


class ProgramShotCache:
    """Thread-safe TTL cache for the program-monitor screenshot. `fetch(fetcher)`
    returns a recent successful `(data, "")` without calling `fetcher` while the last
    frame is younger than `ttl_s`; otherwise it calls `fetcher()` (an obs-websocket
    screenshot), caches a successful frame, and returns it. A failed fetch is never
    cached (the next caller retries) — same best-effort contract as the raw call.
    The network fetch runs OUTSIDE the lock so pollers never serialize behind a slow
    screenshot; a rare concurrent miss just does one extra fetch (harmless)."""

    def __init__(self, ttl_s=PROGRAM_SHOT_TTL_S):
        self.ttl_s = ttl_s
        self._lock = threading.Lock()
        self._at = None
        self._data = None

    def fetch(self, fetcher, now=None):
        now = time.time() if now is None else now
        with self._lock:
            if self._data is not None and self._at is not None \
                    and (now - self._at) < self.ttl_s:
                return self._data, ""
        data, note = fetcher()
        if data is not None:
            with self._lock:
                # Keep the freshest of concurrent fetches.
                if self._at is None or now >= self._at:
                    self._at, self._data = now, data
        return data, note


# One shared cache instance — the program image is identical for every console view,
# so the Director Panel and the Cockpit/Race-Control monitors all read through it.
_program_shot_cache = ProgramShotCache()


def feed_stalled(last_byte_ts, now, stall_s=FANOUT_STALL_S):
    """True iff a fan-out live reader has produced bytes before but none for
    `stall_s`. A None timestamp (never produced) is NOT a stall — that startup
    case is handled by the existing dead_serves path, not the watchdog."""
    return last_byte_ts is not None and (now - last_byte_ts) > stall_s


def snap_bytes(prev_cursor, new_cursor, data_len):
    """Bytes a fan-out consumer lost to a ring cursor-snap. FeedRing.read returns
    (data, new_cursor) with data == buf[cursor-base:], so the served bytes span
    [new_cursor-data_len, new_cursor). If that start ran past prev_cursor the ring had
    overflowed the consumer and read() snapped it forward, skipping the gap. Pure —
    returns the skipped byte count (0 when the consumer kept up)."""
    skipped = (new_cursor - data_len) - prev_cursor
    return skipped if skipped > 0 else 0


def should_obs_reconnect(fanout, dropped, consumer_attached=False):
    """Whether a re-serve should force OBS to reconnect its feed input. Only in fan-out
    mode — the relay is the persistent server, so OBS's socket survives a streamlink
    restart and the fresh stream is spliced into a stale demuxer. Direct-serve needs
    nothing: OBS holds the socket to streamlink itself and reconnects on its own.

    Within fan-out, two cases splice and therefore rejoin:
      * `dropped` — streamlink died unexpectedly (the original #537 drop-recovery);
      * `consumer_attached` — a consumer was still reading this feed's ring when the
        restart happened. That is the director's restart in place (`/reload`, a tier
        change) and a `set_index` on a feed OBS is on air with, including the solo and
        qualifying single-feed `/next` (#614). Before this, all three left `dropped`
        False (they go through `_clear_drop_health`) and handed OBS no rejoin at all.

    The ping-pong handover stays seamless: with `close_when_inactive` OBS has dropped
    the off-air feed, so no consumer is attached and nothing is rebuilt. The first
    serve is already a fresh OBS connection. Pure → unit-tested."""
    return bool(fanout and (dropped or consumer_attached))


# The OBS rejoin has to land after the new serve's HLS prefetch burst (#614). How long
# that burst takes to ARRIVE is a download duration — it depends on the producer's
# downlink, the source's bitrate and the CDN — so it cannot be a constant measured on one
# machine. The relay therefore MEASURES it per serve: it watches the inbound byte flow and
# treats the first pause as the burst's end. Only two things below are fixed, and both are
# properties of the SOURCE's segment cadence rather than of the connection.

# The inbound idle that marks the burst as over. It has to exceed the gaps that occur
# INSIDE a burst and stay below the steady segment cadence. MEASURED 2026-09-20 with
# tools/prefetch-burst-probe.py on a live YouTube 1080p source and a live Twitch channel:
#
#   platform  intra-burst gaps   steady cadence
#   YouTube   up to 0.78 s       ~5 s
#   Twitch    under 0.5 s        ~1.4-1.9 s   (--twitch-low-latency)
#
# so the usable window is about (0.78, 1.4) and 1.0 sits inside it for both. A slow link
# widens the intra-burst gaps and can trip this early; the rejoin then lands in the tail
# of the burst and sheds most of it — the same graceful degradation as a wait that is
# slightly short, never the full backlog.
BURST_IDLE_S = 1.0
BURST_POLL_S = 0.1          # how often the rejoin thread samples last_byte_ts

# A CEILING, not an estimate: some sources never pause long enough to be detected (a
# Twitch low-latency feed shows no gap at all above ~1.4 s), so the wait for the burst
# end is capped at this many seconds per prefetch segment and the rejoin happens anyway.
# Same 2026-09-20 measurement, worst observed arrival per segment: YouTube FULL 0.46 s,
# YouTube ROBUST 0.83 s, Twitch FULL 0.35 s, Twitch ROBUST 0.90 s. 1.0 covers all four,
# and because it is only a ceiling a producer on a slower link simply hits it and rejoins
# rather than getting a wrong answer.
SEGMENT_FETCH_BUDGET_S = 1.0


def live_edge_segments(flags):
    """The --hls-live-edge count in a streamlink flag list (the prefetch burst's size in
    segments). Returns 0 when the flag is absent, which makes the rejoin immediate — the
    honest answer for a reader with no HLS prefetch. Pure -> unit-tested."""
    try:
        return max(0, int(flags[flags.index("--hls-live-edge") + 1]))
    except (ValueError, IndexError, TypeError):
        return 0


def prefetch_land_s(segments, prebuffer_s, budget_s=SEGMENT_FETCH_BUDGET_S):
    """The CEILING on the OBS rejoin's wait: what it takes if the burst's end is never
    observed (#614).

    The ring's time index is byte ARRIVAL time and OBS rejoins at
    `trailing_offset(prebuffer_s)`, so the join lands past the burst only once the burst
    is older than `prebuffer_s`. Hence the burst's arrival span plus `prebuffer_s`. The
    span is capped at `segments` x the per-segment budget because it is the one part that
    depends on the producer's connection — the relay measures the real span instead and
    only falls back to this bound (see Feed._obs_rejoin_after_prefetch).

    `segments == 0` (the local capture ffmpeg, #592, or a reader with no live-edge flag)
    means no burst and therefore no wait: waiting would only hold a black gap open on
    air. Pure -> unit-tested."""
    if segments <= 0:
        return 0.0
    return segments * budget_s + max(0.0, prebuffer_s)


def rejoin_is_stale(stopped, advancing, gen_at_start, gen_now, serving=True):
    """Whether a scheduled prefetch rejoin must no-op once its wait is over: the feed is
    stopping, a restart is already under way, another serve has taken over since the
    rejoin was scheduled, or the feed is no longer serving.

    The first three protect the NEWEST serve — rebuilding then would drop OBS onto its
    prefetch burst, exactly what the wait exists to avoid. `serving` covers the case none
    of them sees: a serve that delivered bytes and then died inside the wait leaves stop,
    advance and the generation untouched for the whole window, because the next serve
    only starts after dead_serve_backoff (>= RETRY_SLEEP, 10 s). Rebuilding OBS against a
    feed with no bytes cannot help, and #581 rules out automation that acts without
    improving the measured state. Pure → unit-tested."""
    return bool(stopped or advancing or gen_now != gen_at_start or not serving)


def cursor_progress_ratio(prev_cursor_ms, cursor_ms, dt_s):
    """OBS mediaCursor advance rate for one heartbeat: (Δcursor seconds)/(Δwall seconds).
    1.0 = playing at real-time, 0.0 = frozen (no advance). None when it can't be measured: a
    missing sample, dt<=0, or a BACKWARDS cursor (a RESET/demuxer-rebuild jump — recovery, not
    a stall). #488 reliable freeze signal: renderSkippedFrames is a compositor render-timing
    metric, blind to a source-side demuxer freeze (measured live 2026-07-15, renderSkip 0% /
    fps 60 while the picture is frozen). Pure → unit-tested."""
    if prev_cursor_ms is None or cursor_ms is None or dt_s is None or dt_s <= 0:
        return None
    delta_ms = cursor_ms - prev_cursor_ms
    if delta_ms < 0:                       # cursor went backwards (RESET jump) — recovery, not a stall
        return None
    return (delta_ms / 1000.0) / dt_s


def stall_fraction(ratios, *, stall_ratio):
    """Fraction of MEASURED ticks whose cursor-progress ratio is below stall_ratio (a stalled
    tick). Skips None (unmeasurable) entries; returns None when nothing is measurable. This —
    not the window mean — is the stutter signal: a choppy feed averages ~1x but has many
    zero-progress ticks, while a healthy feed has none. Pure → unit-tested."""
    measured = [r for r in ratios if r is not None]
    if not measured:
        return None
    stalled = sum(1 for r in measured if r < stall_ratio)
    return stalled / len(measured)


def freeze_decision(frac, since_last_reset_s, *, frac_threshold, cooldown_s):
    """Whether to auto-reconnect the on-air feed's OBS input: True when the stall fraction
    (stall_fraction) is at/above frac_threshold AND the cooldown since the last reconnect has
    elapsed. A None frac (no data) never trips it (#488). Pure → unit-tested."""
    if since_last_reset_s is not None and since_last_reset_s < cooldown_s:
        return False
    return frac is not None and frac >= frac_threshold


def backlog_shed_decision(streak, since_last_reset_s, *, min_streak, cooldown_s):
    """Whether to auto-rebuild the on-air feed's OBS input: True after `min_streak`
    consecutive backlogged heartbeats, once the shared cooldown has elapsed. Same shape as
    `freeze_decision` — both pull the same control. A None streak never trips it. Pure."""
    if since_last_reset_s is not None and since_last_reset_s < cooldown_s:
        return False
    return streak is not None and streak >= min_streak


def consumer_overflowed(prev_snaps, snaps):
    """True when the fan-out consumer's cumulative cursor-snap count rose since the last
    check: the ring lapped OBS and dropped bytes under it. An incident record only (#582):
    by then the backlog has already destroyed itself, and a rebuild cannot fix a consumer
    that is chronically slower than real time. A shrinking total (a consumer left) is not
    an overflow. Pure → unit-tested."""
    if prev_snaps is None or snaps is None:
        return False
    return snaps > prev_snaps


class RebuildGuard:
    """Effectiveness guard for the automatic OBS-input rebuild (#582). Pure: no I/O, no
    clock → unit-tested. A rebuild counts as ineffective when the first full detector
    window after it still trips the detector (stall fraction at/above the threshold); a
    window below it means the rebuild helped and resets the streak. "Better but still
    stalling" is not an improvement. After `max_attempts` consecutive ineffective rebuilds
    the guard stands down until rearm() (the next stint change or the director)."""

    def __init__(self, max_attempts=REBUILD_GUARD_MAX_ATTEMPTS):
        self.max_attempts = max_attempts
        self.rearm()

    def rearm(self):
        self._ineffective = {}       # reason -> consecutive ineffective rebuilds
        self._stood_down = set()     # reasons that gave up
        self.pending = None          # reason tag of the rebuild awaiting judgement

    def allows(self, reason="freeze"):
        """Per reason: one giving up must not disable the other's remedy."""
        return reason not in self._stood_down

    def ineffective(self, reason="freeze"):
        return self._ineffective.get(reason, 0)

    @property
    def stood_down(self):
        """True when ANY reason gave up — the Director Panel reads this as a plain bool."""
        return bool(self._stood_down)

    @property
    def stood_down_reasons(self):
        return sorted(self._stood_down)

    def on_fire(self, reason="freeze"):
        """Arm the judgement for a rebuild this reason just fired. A still-unjudged one
        that the next rebuild supersedes did not end the trouble, so it counts as
        ineffective rather than being overwritten out of the three-strike budget."""
        if self.pending is not None:
            self._count_ineffective(self.pending)
        self.pending = reason

    def _count_ineffective(self, reason):
        n = self._ineffective.get(reason, 0) + 1
        self._ineffective[reason] = n
        if n >= self.max_attempts:
            self._stood_down.add(reason)
            return True
        return False

    def judge(self, still_bad, *, reason="freeze"):
        """Judge the pending rebuild, but only if THIS reason fired it — the two judges run
        on different threads, and either would otherwise consume the other's. True when
        this call stood the guard down. `still_bad is None` consumes nothing."""
        if self.pending != reason or still_bad is None:
            return False
        self.pending = None
        if not still_bad:
            self._ineffective[reason] = 0
            return False
        return self._count_ineffective(reason)

    def on_window(self, frac, *, frac_threshold):
        """Judge the first full window after a FREEZE rebuild. "Better but still stalling"
        is not an improvement, so the comparison is against the same trip threshold."""
        return self.judge(None if frac is None else frac >= frac_threshold, reason="freeze")


def feed_reset_target(feed_key, valid_keys):
    """Normalize + validate a /obs/feed-reset feed key against the live feed set. Returns
    the upper-cased key when it names a real feed, else None (a 400). Pure → unit-tested."""
    key = str(feed_key or "").strip().upper()
    return key if key in valid_keys else None


def reset_discards_s(backlog_s, prebuffer_s):
    """#587: seconds of output a feed reset throws away right now. The reset rebuilds the
    OBS input and the new consumer joins prebuffer_s behind the live edge, so only the
    backlog beyond that reserve is lost. None when no consumer has a backlog to measure.
    Pure -> unit-tested."""
    if backlog_s is None:
        return None
    return round(max(0.0, backlog_s - prebuffer_s), 1)


# A fan-out/direct drop-recovery is recorded as a discrete health event EVERY time (so a
# self-healed on-air blip is no longer invisible in the post-event report / health monitor).
# A single recovery never pings Discord; sustained CHURN does — ≥ FEED_CHURN_THRESHOLD
# recoveries of the SAME feed within FEED_CHURN_WINDOW_S. FEED_CHURN_COOLDOWN_S then
# suppresses re-paging on every further recovery while the feed keeps churning.
FEED_CHURN_WINDOW_S = 300      # 5 min
FEED_CHURN_THRESHOLD = 3
FEED_CHURN_COOLDOWN_S = 300    # at most one churn @here per feed per 5 min


def feed_recovery_churn(recovery_ts, now, window_s=FEED_CHURN_WINDOW_S,
                        threshold=FEED_CHURN_THRESHOLD):
    """True when at least `threshold` recovery timestamps fall within the last `window_s`
    seconds — i.e. the feed is churning, not a one-off blip. Pure → unit-tested."""
    recent = [t for t in recovery_ts if t is not None and 0 <= (now - t) <= window_s]
    return len(recent) >= threshold


def churn_at_here_suppressed(source_state):
    """True when a feed-recovery-churn @here should be SUPPRESSED because the feed's
    source is a known not-(yet)-live state (#495): a source that is offline or has
    ended and keeps flapping while the relay retries is expected churn, not a relay
    fault. Genuine churn (source_state None) still pages. Pure → unit-tested."""
    return source_state in ("not_live_yet", "ended")


def feed_health_state(dropped, dropped_since, served_ok, now,
                      grace_s=HEALTH_DROP_GRACE_S):
    """Classify one feed's live health as 'down' / 'connecting' / 'ok' from its
    drop flags + timestamps — debouncing the raw `dropped` flag so a brief blip
    or a never-served (demo/startup) feed never pages CRITICAL. Pure →
    unit-tested in tests/test_health.py.

    'ok'         — not dropped (serving, or idle).
    'down'       — dropped, HAD a stable picture (served_ok), and has stayed
                   dropped continuously for at least `grace_s`. A genuine loss.
    'connecting' — dropped but still inside the grace window, OR never delivered
                   a stable picture (served_ok False). You cannot "lose" a stream
                   you never had; a self-healing blip is given time to recover.
                   Surfaced as a quiet DEGRADED hint, never a CRITICAL @here."""
    if not dropped:
        return "ok"
    if not served_ok:
        return "connecting"
    if dropped_since is None or (now - dropped_since) < grace_s:
        return "connecting"
    return "down"


def drop_connecting_notifiable(dropped, dropped_since, now,
                               settle_s=HEALTH_CONNECTING_SETTLE_S):
    """Whether a feed in the 'connecting' health state should surface as a
    NOTIFIABLE 'stuck connecting' (the yellow reason that drives a Discord @here)
    yet. Pure → unit-tested.

    A SERVED feed that just dropped is a silent blip until it has stayed down past
    `settle_s` — so a reconnect that self-heals within a heartbeat (the fan-out
    EOF-churn / byte-stall re-serve on a VOD) never pages the crew. A feed that
    is not dropped (a never-served startup connect) is notifiable immediately, so
    this only changes the just-dropped case. Red escalation is unaffected: a drop
    past the grace window is classified 'down' by feed_health_state, not here."""
    if not dropped:
        return True
    if dropped_since is None:
        return False                 # treat a missing stamp as just-dropped → blip
    return (now - dropped_since) >= settle_s


def qualifying_downgrade_note(requested_qualifying, has_qual_source, qual_tab):
    """A LOUD warning when --qualifying was requested but no qualifying schedule source
    exists, so the relay is silently running in RACE mode. On 2026-07-10 this silent
    downgrade meant the Director-Panel Parts control resolved the numeric race parts (not
    the `Q` part), pushing the wrong Producer stream key → the stream went nowhere. Pure →
    unit-tested. Returns the message, or None when there is nothing to warn about."""
    if requested_qualifying and not has_qual_source:
        return (f"--qualifying was requested but the Qualifying schedule source is "
                f"UNAVAILABLE (tab '{qual_tab}') — the relay is running in RACE mode. The "
                f"Parts control will select the wrong Producer part / stream key. Fix the "
                f"Sheet's '{qual_tab}' tab, then restart.")
    return None


def aggregate_health(facts):
    """Roll up the relay's live facts into one level + human reasons. Pure →
    unit-tested. `facts` keys: feeds_down (list of feed names with a lost live
    serve), feeds_connecting_long (list connecting past HEALTH_CONNECTING_S, not
    down), cookies_stale (bool), obs_reachable (True/False/None — None = not yet
    probed, never an alarm), tailscale_present (bool),
    stream_active (True/False/None — OBS streaming state; None = not yet known),
    stream_expected (bool — OBS has streamed at least once this session; the
        off-air alarm latches on this so a pre-show relay start never pings),
    stream_reconnecting (bool — OBS upstream unstable),
    funnel_down (bool — Funnel was previously seen up but is now down),
    sheet_push_failing (bool — Sheet webhook returning errors),
    feed_source_states (optional dict mapping feed name to source_state),
    feeds_jittery (list of feed names whose last heartbeat interval's max inbound
        gap exceeded the fan-out reserve — a quiet, display-only yellow (#535);
        never included in the notify-level facts, so it never pages Discord),
    rebuilds_stood_down (optional dict feed name -> OBS fps or None: the #582
        effectiveness guard stood the automatic OBS rebuild down on that feed),
    feeds_backlogged (optional dict feed name -> seconds behind live: OBS accepts that
        feed's bytes slower than real time, #583 — a quiet, display-only yellow that
        is excluded from the notify-level facts until #581 stage 4 calibrates it; the
        reason names the RESET for Feed A/B, the only feeds that have one, #588).

    red  = any feed down (a live picture was lost); or obs_reachable truthy and
           stream_active is False AND stream_expected (OBS connected and has
           streamed before but is not streaming now — a live broadcast dropped
           off air). The stream_expected latch means starting the relay pre-show,
           before OBS ever goes live, never alarms.
    yellow = OBS WebSocket unreachable · cookies stale · Tailscale down · a feed
             stuck connecting · stream_reconnecting · funnel_down ·
             sheet_push_failing · an auto-rebuild stood down · a consumer backlog. A red result still lists
             the yellow issues under it.
    green = none of the above."""
    reasons, red, yellow = [], [], []
    sstates = facts.get("feed_source_states") or {}
    for name in facts.get("feeds_down") or []:
        if sstates.get(name) == "ended":
            red.append(f"Feed {name} — source's live stream ENDED "
                       f"(no auto-recovery — switch source)")
        else:
            red.append(f"Feed {name} down — lost the live stream")
    # OBS reachable but not streaming = off air — but only AFTER OBS has streamed
    # at least once this session (stream_expected latch), so starting the relay
    # pre-show, before OBS ever goes live, never fires a CRITICAL ping.
    if (facts.get("obs_reachable") and facts.get("stream_active") is False
            and facts.get("stream_expected")):
        red.append("OBS is not streaming — broadcast is off air")
    if facts.get("obs_reachable") is False:
        yellow.append("OBS WebSocket unreachable — no auto-cut")
    if facts.get("obs_reachable") and facts.get("stream_reconnecting"):
        yellow.append("OBS stream reconnecting — upstream unstable")
    if facts.get("funnel_down"):
        yellow.append("Funnel down — commentators cannot reach the cockpit")
    if facts.get("sheet_push_failing"):
        yellow.append("Sheet webhook failing — panel writes are not saved")
    if facts.get("cookies_stale"):
        yellow.append("YouTube cookies stale — handovers may fail")
    if not facts.get("tailscale_present", True):
        yellow.append("Tailscale not connected — directors cannot reach the panel")
    for name in facts.get("feeds_connecting_long") or []:
        if sstates.get(name) == "not_live_yet":
            yellow.append(f"Feed {name} — commentator source not live yet (connecting)")
        else:
            yellow.append(f"Feed {name} stuck connecting")
    for name in facts.get("feeds_jittery") or []:
        yellow.append(f"Feed {name} inbound stall — a gap exceeded the fan-out reserve (stutter risk)")
    for name, fps in (facts.get("rebuilds_stood_down") or {}).items():
        renders = f" (producer host renders {fps:.0f} fps)" if fps is not None else ""
        yellow.append(f"Feed {name} rebuild ineffective — {REBUILD_GUARD_MAX_ATTEMPTS} OBS "
                      f"rebuilds did not clear the stall{renders}; auto-rebuild paused")
    # #588: the next step is the deliberate RESET (#587), with its cost. Not ROBUST: that
    # tier restarts streamlink without rejoining OBS and, for YouTube, starts two segments
    # further behind the live edge (#614), so it can grow the backlog it is meant to fix.
    for name, behind in (facts.get("feeds_backlogged") or {}).items():
        step = (f"; RESET {name} → LIVE drops it with a short black dropout"
                if name in ("A", "B") else "")
        yellow.append(f"Feed {name} output {behind:.0f} s behind live — OBS reads slower than "
                      f"real time{step}")
    # #619: OBS already repaired this one by the time we read its log, so the reason
    # reports it and asks for eyes instead of naming a fix. Only UNEXPLAINED repairs
    # reach here; one right after a restart is that restart's expected cost.
    for name, ms in (facts.get("feeds_av_disturbed") or {}).items():
        amount = f" by {ms:.0f} ms" if ms is not None else ""
        yellow.append(f"Feed {name} audio timing broke{amount} with no restart to explain "
                      f"it — OBS re-synced itself; check the program picture and sound")
    reasons.extend(red)
    reasons.extend(yellow)
    level = "red" if red else ("yellow" if yellow else "green")
    return {"level": level, "reasons": reasons}


def health_should_notify(prev, cur):
    """Whether a heartbeat tick should push to Discord. Fire only on a level
    CHANGE (degradation and recovery alike) so a multi-hour race is not spammed;
    on the first tick (prev None) announce only a non-green baseline. Pure."""
    if prev is None:
        return cur != "green"
    return prev != cur


def discord_health_payload(level, reasons, prev_level=None, event_title="", producer=""):
    """Discord webhook JSON for a health transition. Pure → unit-tested. A return
    to green reads as a recovery; otherwise it announces the degraded state and
    lists the reasons. Every health change (degraded AND recovery) carries an
    @here ping so the crew is pulled in even if the panel pill goes unnoticed —
    the mention MUST sit in top-level `content` with allowed_mentions permitting
    it, because Discord ignores mentions inside an embed. The footer shows
    `<event_title> · <producer>` (#207/#317 — which host raised it); empty -> no
    footer."""
    if level == "green":
        title = "✅ Broadcast health recovered — all systems green"
        desc = "Recovered from a previous issue." if prev_level and prev_level != "green" \
            else "All systems green."
    else:
        title = f"⚠️ Broadcast health: {_HEALTH_LABEL[level]}"
        desc = "\n".join(f"• {r}" for r in reasons) or _HEALTH_LABEL[level]
    embed = {"title": title, "description": desc, "color": HEALTH_COLORS[level]}
    footer = notify._footer(event_title, producer)
    if footer:
        embed["footer"] = {"text": footer}
    return {"username": "GT Racecast",
            "content": "@here",
            "allowed_mentions": {"parse": ["everyone"]},
            "embeds": [embed]}


def discord_failover_payload(on_air_feed, scene, event_title="", producer=""):
    """Discord webhook JSON for an auto-failover (#378). Pure → unit-tested. Loud
    by design: the @here ping sits in top-level `content` (Discord ignores
    mentions inside an embed) so the crew is pulled in the instant the program is
    auto-switched. The footer shows `<event_title> · <producer>` (#207/#317)."""
    desc = (f"The on-air feed (Feed {on_air_feed}) stayed down past the grace "
            f"window — OBS was automatically switched to the **{scene}** scene. "
            "Return is manual: re-take the feed once it recovers.")
    embed = {"title": "🛟 Auto-failover — switched to Intermission",
             "description": desc, "color": HEALTH_COLORS["red"]}
    footer = notify._footer(event_title, producer)
    if footer:
        embed["footer"] = {"text": footer}
    return {"username": "GT Racecast",
            "content": "@here",
            "allowed_mentions": {"parse": ["everyone"]},
            "embeds": [embed]}


def discord_step_down_payload(feed, stint, from_tier, to_tier, event_title="", producer=""):
    """Discord webhook JSON for an auto quality step-down (#493). @here in top-level
    content — actionable: the source is struggling and the director must decide the
    manual step-up. Pure → unit-tested."""
    desc = (f"Feed {feed} (stint {stint}) could not sustain **{from_tier}** — the relay "
            f"automatically reduced it to **{to_tier}** (720p) to keep a continuous "
            "picture. Step-up is manual: raise it again from the Director Panel once the "
            "source recovers.")
    embed = {"title": "📉 Quality step-down — source struggling",
             "description": desc, "color": HEALTH_COLORS["yellow"]}
    footer = notify._footer(event_title, producer)
    if footer:
        embed["footer"] = {"text": footer}
    return {"username": "GT Racecast",
            "content": "@here",
            "allowed_mentions": {"parse": ["everyone"]},
            "embeds": [embed]}


def stream_transition(prev, cur):
    """The OBS stream start/stop event implied by a stream_active change, or None.
    Only genuine bool transitions count: False->True = 'started', True->False =
    'stopped'. A None on either side (unknown / OBS unreachable / first sample)
    yields None, so a startup baseline or a reachability blip never fires a false
    event. Pure → unit-tested."""
    if prev is False and cur is True:
        return "started"
    if prev is True and cur is False:
        return "stopped"
    return None


def _fmt_uptime(seconds):
    """Compact H/M/S duration for the stream-uptime log line. None/negative -> ''."""
    if seconds is None or seconds < 0:
        return ""
    total = int(seconds)
    h, rem = divmod(total, 3600)
    mins, secs = divmod(rem, 60)
    if h:
        return f"{h}h {mins:02d}m {secs:02d}s"
    if mins:
        return f"{mins}m {secs:02d}s"
    return f"{secs}s"


def stream_event_log_line(started, kbps=None, uptime_s=None):
    """The relay-log line for an OBS stream start/stop transition. Pure so it is
    unit-tested; the caller emits it via LOG.info so the event is greppable in
    `racecast relay logs` alongside the feed events (it complements the Discord
    push + Health-Monitor marker). On start it appends the upstream kbps when
    known — the first sample is a partial-window estimate, so it is an approximate
    'bytes are flowing' signal (hence the '~'), not an exact bitrate. On stop it
    appends the stream uptime when known. Note: OBS-WebSocket only sees bytes going
    to the ingest — it cannot know whether a YouTube/Twitch broadcast is bound, so
    a healthy kbps confirms egress, never that a watchable broadcast exists."""
    if started:
        head = "OBS stream output started"
        if kbps is not None:
            head += f" — upstream ~{kbps:.0f} kbps"
        return head
    head = "OBS stream output stopped"
    dur = _fmt_uptime(uptime_s)
    if dur:
        head += f" after {dur}"
    return head


def event_stop_argv(frozen, exe, rel_here):
    """Argv to spawn a detached `racecast event stop`. Frozen: re-invoke the binary
    itself (like the relay/streams daemons). Source: python runs ../racecast.py
    relative to the relay script dir. Pure — I/O is in Relay._spawn_event_stop."""
    if frozen:
        return [exe, "event", "stop"]
    return [exe, os.path.join(rel_here, os.pardir, "racecast.py"), "event", "stop"]


# Sheet ID is NOT hardcoded — it comes from RACECAST_SHEET_ID (injected by the CLI
# from the active profile). Override per-run with --sheet-id.
DEFAULT_SHEET_TAB = "Schedule"
DEFAULT_POV_TAB = "POV"
# Qualifying runs on its own day from a separate tab with the SAME structure as
# the Schedule tab (URL/Streamer/Stint). It is a single stream served on Feed A;
# the relay switches its active schedule between race and qualifying (issue #124).
DEFAULT_QUALIFYING_TAB = "Qualifying"
# Quali Times tab (issue #555): per-car best lap shown in the race tiles. A
# SEPARATE tab from DEFAULT_QUALIFYING_TAB above, which is the qualifying
# SCHEDULE (URL/Streamer/Stint) — the two must never share a name.
DEFAULT_QUALI_TIMES_TAB = "Quali Times"
# How often the Quali Times tab is re-read, on its OWN thread (quali_poller) and
# entirely off the HUD refresh path. The laps are entered once between qualifying
# and the race, so a slow cadence is plenty -- and because nothing on air waits for
# this fetch, a failing request costs nothing that matters (hence no backoff).
QUALI_TIMES_POLL_S = 60

# ---------- Network bind resolution (auto dual-bind: localhost + Tailscale) ----
# OBS always reaches the control/HUD server on 127.0.0.1 (a fixed, machine-
# independent address — never edit the OBS collection). With --bind auto
# (the default) the server ALSO binds the machine's Tailscale IP so remote
# directors/tablets reach /panel + /hud over the tailnet — without exposing the
# unauthenticated server on the local LAN the way 0.0.0.0 would.
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")  # Tailscale's IPv4 range
# Candidate Tailscale CLI locations (PATH first, then the platform installers).
_TAILSCALE_BINS = [
    "tailscale",
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",  # macOS GUI app
    "/usr/bin/tailscale", "/usr/local/bin/tailscale", "/opt/homebrew/bin/tailscale",
    r"C:\Program Files\Tailscale\tailscale.exe",
]


def _in_cgnat(ip):
    """True iff ip is a valid IPv4 address inside Tailscale's 100.64.0.0/10 range."""
    try:
        return ipaddress.ip_address(ip) in _CGNAT_NET
    except ValueError:
        return False


def parse_tailscale_status(output):
    """Self's first CGNAT IPv4 from `tailscale status --json`, or None.

    Requires BackendState == "Running": a stopped/disconnected node keeps its
    assigned tailnet IP, so `tailscale ip -4` alone reports "connected" even
    after the user toggled Tailscale off."""
    try:
        data = json.loads(output)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("BackendState") != "Running":
        return None
    for ip in (data.get("Self") or {}).get("TailscaleIPs") or []:
        if _in_cgnat(str(ip)):
            return str(ip)
    return None


def _no_window_kwargs(os_name=None):
    """Popen/run kwargs that stop a console child from flashing its own terminal
    window on Windows. The relay is spawned as a daemon with DETACHED_PROCESS
    (services.spawn_kwargs), so it has NO console — every console subprocess it
    starts (yt-dlp, streamlink, the tailscale CLI) otherwise pops a transient
    window, and the per-feed yt-dlp resolve fires every ~15 s, so the desktop
    flickers continuously during an event (issue #30). CREATE_NO_WINDOW gives the
    child a hidden console instead; harmless when a console already exists (the
    foreground `racecast relay run`), and a no-op (empty kwargs) off Windows so the
    same spawn site stays cross-platform. Mirrors services.no_window_kwargs — the
    standalone relay imports nothing from scripts/, so the flag is duplicated here
    (same pattern as detect_tailscale_ip)."""
    os_name = os.name if os_name is None else os_name
    if os_name == "nt":
        CREATE_NO_WINDOW = 0x08000000
        return {"creationflags": CREATE_NO_WINDOW}
    return {}


def detect_tailscale_ip():
    """This machine's connected Tailscale IPv4 via the CLI, or None if the
    Tailscale backend is unavailable, stopped, or logged out.

    Deliberate divergence from scripts/tailscale.py's tailscale_backend():
    this detection-only copy keeps trying further binaries until one reports
    a Running IP, while the state-aware version stops at the first binary
    that answers at all (its callers need Stopped/NeedsLogin, not just the IP)."""
    for binary in _TAILSCALE_BINS:
        try:
            out = subprocess.run([binary, "status", "--json"], capture_output=True,
                                 text=True, errors="replace", timeout=3,
                                 env=external_tool_env(), **_no_window_kwargs())
        except (OSError, subprocess.SubprocessError):
            continue
        ip = parse_tailscale_status(out.stdout)
        if ip:
            return ip
    return None


def resolve_bind_addresses(bind_arg, tailscale_ip):
    """Map the --bind value (+ a detected Tailscale IP) to bind addresses.

    'auto'  -> 127.0.0.1 plus the Tailscale IP when present (localhost only if not).
    localhost/127.0.0.1 -> [127.0.0.1].
    anything else (e.g. 0.0.0.0 or a specific IP) -> taken literally.
    """
    if bind_arg == "auto":
        addrs = ["127.0.0.1"]
        if tailscale_ip and tailscale_ip not in addrs:
            addrs.append(tailscale_ip)
        return addrs
    if bind_arg in ("127.0.0.1", "localhost"):
        return ["127.0.0.1"]
    return [bind_arg]

_LOOPBACK_ADDRS = frozenset(("127.0.0.1", "localhost"))


def loopback_bind_failed(requested, bound):
    """True when a loopback address was requested but did not bind.

    OBS always reaches the relay on 127.0.0.1, so binding the loopback is
    mandatory whenever it was requested (the 'auto'/'127.0.0.1' binds). If a
    stale relay already holds the loopback port, the dual-bind 'auto' path would
    otherwise bind only the Tailscale IP and run on — a silent split-brain where
    127.0.0.1 keeps serving the OLD relay (e.g. 'hud disabled') while the new one
    hides on the tailnet (issue #84). An explicit --bind 0.0.0.0 / specific IP
    never requested loopback, so this rule does not apply there.
    """
    req_loop = _LOOPBACK_ADDRS & set(requested)
    return bool(req_loop) and not (_LOOPBACK_ADDRS & set(bound))


def control_port_available(host, port):
    """True if the mandatory loopback control port can be bound right now (no other
    relay holds it). A throwaway detection probe.

    On POSIX it sets SO_REUSEADDR so it agrees with the authoritative HTTPServer bind
    (which inherits allow_reuse_address=1). Without it, a port merely in TIME_WAIT after
    a prior relay's control-port connections is falsely reported "in use", so a fresh
    start — e.g. `event start` right after clearing a stale holder — aborts a bind that
    would actually succeed (the 2026-07-10 event-start race). A LIVE listener still fails
    the bind with EADDRINUSE even with SO_REUSEADDR on POSIX, so a genuinely running relay
    is still detected. On Windows SO_REUSEADDR is deliberately OMITTED: there it lets a
    bind SUCCEED against a port another socket already holds, so the probe would miss a
    running relay (and the held-port unit test fails on the Windows CI runner). Returns
    False only on a bind error; the socket is always closed."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if not sys.platform.startswith("win"):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()

SCHEDULE_TEMPLATE = (
    "# racecast relay offline fallback schedule — used ONLY if the Google Sheet AND the\n"
    "# last-good cache are both unavailable. One entry per stint, in order:\n"
    "#   a YouTube channel ID (UC...) OR a full watch URL (https://www.youtube.com/watch?v=...).\n"
    "# Lines starting with # are ignored. The real schedule lives in the Sheet tab 'Schedule'.\n"
    "# Example:\n"
    "#   https://www.youtube.com/watch?v=VIDEOID_STINT_1\n"
    "#   UCxxxxxxxxxxxxxxxxxxxxxx\n"
)

CHANNEL_RE = re.compile(r"^UC[A-Za-z0-9_\-]{20,}$")
ASSET_KEY_RE = re.compile(r"^[a-z0-9-]+$")
# Resolution order when a HUD asset is requested by key (no extension).
ASSET_EXTS = (("png", "image/png"), ("svg", "image/svg+xml"),
              ("jpg", "image/jpeg"), ("jpeg", "image/jpeg"), ("webp", "image/webp"))
# Identity whitelist: the handler re-derives the Content-Type header value from
# this constant map, so a request-derived string can never reach send_header().
ASSET_CTYPES = {ctype: ctype for _, ctype in ASSET_EXTS}

# Logo image extensions accepted for /console/logo (#236). Mirrors racecast.py.
_LOGO_EXTS = frozenset((".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"))
_LOGO_CTYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml"}


def servable_logo_path(logo_path):
    """Return `logo_path` when it points at a web-image file (by extension),
    else "". No filesystem check — extension whitelist only."""
    if logo_path and os.path.splitext(logo_path)[1].lower() in _LOGO_EXTS:
        return logo_path
    return ""


def resolve_asset(assets_dir, sub, key):
    """Resolve a HUD asset by key (no extension) to (path, content_type).
    Tries known image extensions in order; returns None if nothing matches or
    inputs are unsafe (subdir whitelist + strict key + realpath containment =
    no path traversal)."""
    if not assets_dir or sub not in ("flags", "brands") or not ASSET_KEY_RE.match(key):
        return None
    base = os.path.realpath(os.path.join(assets_dir, sub))
    for ext, ctype in ASSET_EXTS:
        path = os.path.realpath(os.path.join(base, f"{key}.{ext}"))
        # Belt-and-braces on top of the strict key regex: the resolved path
        # must stay inside the whitelisted asset subdir (also cuts the CodeQL
        # taint path from the request URL into open()).
        if not path.startswith(base + os.sep):
            return None
        if os.path.exists(path):
            return path, ctype
    return None


def resolve_brand_override(brands_dir, key):
    """Resolve a per-league brand-logo override by key (no extension) to
    (path, content_type), or None. Unlike resolve_asset, `brands_dir`
    (runtime/<profile>/brands) is treated DIRECTLY as the base — there is no
    'brands' sub-level. Same safety contract: strict key regex + the ASSET_EXTS
    extension whitelist + realpath containment (no path traversal)."""
    if not brands_dir or not ASSET_KEY_RE.match(key):
        return None
    base = os.path.realpath(brands_dir)
    for ext, ctype in ASSET_EXTS:
        path = os.path.realpath(os.path.join(base, f"{key}.{ext}"))
        if not path.startswith(base + os.sep):
            return None
        if os.path.exists(path):
            return path, ctype
    return None


# Shared contract with get-graphics.py's MANIFEST_NAME (it writes this file). The two
# cannot share code — the relay's downloaders are deliberately dependency-light — so keep
# the literal "manifest.json" in sync.
GRAPHICS_MANIFEST_NAME = "manifest.json"


def _internal_graphic_labels(graphics_dir):
    """Lowercased set of asset labels marked OBS-only (internal) in the graphics manifest
    get-graphics.py writes. Best-effort: missing/unreadable/malformed/wrong-shape ->
    empty set (the browser shows everything; backward compatible)."""
    if not graphics_dir:
        return set()
    try:
        with open(os.path.join(graphics_dir, GRAPHICS_MANIFEST_NAME), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return set()
    internal = data.get("internal") if isinstance(data, dict) else None
    if not isinstance(internal, list):
        return set()
    return {str(x).strip().lower() for x in internal}


def _placeholder_signature():
    """(size, bytes) of the transparent graphic placeholder, or None if it cannot be
    read. A pure-placeholder graphic (written by get-graphics' seed/reset for an
    un-linked or un-Sheeted asset, #387) is byte-identical to this file and renders
    blank, so it is dropped from the browser list. Best-effort: None -> no placeholder
    filtering (the files just stay listed, today's behaviour)."""
    try:
        with open(placeholders.graphic_placeholder_path(), "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    return (len(data), data)


def _is_placeholder_png(path, placeholder_sig):
    """True iff the file at `path` is byte-identical to the graphic placeholder.
    A cheap size pre-check avoids reading real (larger) graphics. placeholder_sig is
    the (size, bytes) tuple from _placeholder_signature(), or None -> never a
    placeholder (no filtering)."""
    if not placeholder_sig:
        return False
    size, data = placeholder_sig
    try:
        if os.path.getsize(path) != size:
            return False
        with open(path, "rb") as fh:
            return fh.read() == data
    except OSError:
        return False


def list_graphics(graphics_dir):
    """Sorted list of the broadcast still-graphics (*.png) in graphics_dir as
    [{"name": <Sheet label>, "file": <label>.png}, ...] for the cockpit browser.
    Tolerant of an unset/missing/unreadable dir (returns []). Names are arbitrary
    Sheet labels (mixed case, spaces) so there is no key regex here — listing is
    a plain directory read; the SECURITY check lives in resolve_graphic. Assets
    flagged internal in the graphics manifest, and pure-placeholder (blank) graphics,
    are omitted."""
    if not graphics_dir:
        return []
    try:
        names = os.listdir(graphics_dir)
    except OSError:
        return []
    internal = _internal_graphic_labels(graphics_dir)
    placeholder_sig = _placeholder_signature()
    out = []
    for fn in names:
        if fn.lower().endswith(".png") and os.path.isfile(os.path.join(graphics_dir, fn)):
            name = fn[:-4]
            if name.strip().lower() in internal:
                continue
            if _is_placeholder_png(os.path.join(graphics_dir, fn), placeholder_sig):
                continue
            out.append({"name": name, "file": fn})
    out.sort(key=lambda e: e["name"].lower())
    return out


def resolve_graphic(graphics_dir, name):
    """Resolve a requested cockpit-graphics filename to (path, "image/png"), or
    None when unsafe or absent. Graphics filenames are arbitrary Sheet labels
    (uppercase/spaces allowed) so ASSET_KEY_RE does NOT apply; safety = reject any
    path separator / traversal component, then realpath containment inside
    graphics_dir (same guarantee as resolve_asset). Content-type is the constant
    "image/png", never request-derived."""
    if not graphics_dir or not name or name in (".", ".."):
        return None
    if "/" in name or "\\" in name:          # no directory components
        return None
    if not name.lower().endswith(".png"):
        return None
    base = os.path.realpath(graphics_dir)
    path = os.path.realpath(os.path.join(base, name))
    if not path.startswith(base + os.sep):   # belt-and-braces containment
        return None
    return (path, "image/png") if os.path.isfile(path) else None


# Vendored uPlot assets (src/assets/vendor/uplot/) served at /health-monitor/assets/.
# Identity allow-list: only these filenames resolve, and the Content-Type is the
# constant map value (never request-derived) — same model as ASSET_CTYPES / FONT_CTYPES.
UPLOT_CTYPES = {"uPlot.iife.min.js": "application/javascript", "uPlot.min.css": "text/css"}
# Identity whitelist (same role as FONT_CTYPES_OUT): the handler re-derives the
# Content-Type header value from this constant map, so a request-derived string
# can never reach send_header() (defense vs. HTTP response splitting).
UPLOT_CTYPES_OUT = {ctype: ctype for ctype in UPLOT_CTYPES.values()}


def resolve_uplot_asset(uplot_dir, name):
    """Resolve a requested uPlot asset filename to (path, content_type), or None
    when unset/unknown/unsafe/absent. Safety mirrors resolve_graphic: an explicit
    filename allow-list, reject any path separator / traversal component, then
    realpath containment inside uplot_dir. Content-type is the constant map value,
    never request-derived."""
    ctype = UPLOT_CTYPES.get(name)
    if not uplot_dir or ctype is None:
        return None
    if "/" in name or "\\" in name or name in (".", ".."):
        return None
    base = os.path.realpath(uplot_dir)
    path = os.path.realpath(os.path.join(base, name))
    if not path.startswith(base + os.sep):   # belt-and-braces containment
        return None
    return (path, ctype) if os.path.isfile(path) else None


# Per-profile overlay overrides (profiles/<name>/overlay/). Override CSS is read
# fresh per request (so a Control Center edit applies on the next OBS refresh
# without a relay restart); fonts reuse the resolve_asset security pattern.
OVERLAY_PAGES = ("hud", "splitscreen", "intermission")
# Paths the relay serves as OBS browser sources (mirrored in src/racecast.py for
# the served-pages hash gate that drives the OBS auto-refresh; keep in sync).
OBS_PAGE_PATHS = ("/hud", "/hud/override.css",
                  "/splitscreen", "/splitscreen/override.css",
                  "/intermission", "/intermission/override.css")
FONT_CTYPES = {"woff2": "font/woff2", "woff": "font/woff",
               "ttf": "font/ttf", "otf": "font/otf"}
# Identity whitelist (same role as ASSET_CTYPES): the handler re-derives the
# Content-Type header value from this constant map, so a request-derived string
# can never reach send_header().
FONT_CTYPES_OUT = {ctype: ctype for ctype in FONT_CTYPES.values()}
FONT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

def read_overlay_css(overlay_dir, page):
    """Bytes of profiles/<name>/overlay/<page>.css, or b'' when the dir is unset,
    the page is not a known overlay page, or the file is absent/unreadable. Read
    per request so editor saves apply without a relay restart."""
    if not overlay_dir or page not in OVERLAY_PAGES:
        return b""
    try:
        with open(os.path.join(overlay_dir, f"{page}.css"), "rb") as fh:
            return fh.read()
    except OSError:
        return b""

def resolve_overlay_font(overlay_dir, name):
    """Resolve overlay/fonts/<name> to (path, content_type); None if unsafe or
    missing. Same containment guarantees as resolve_asset (strict name + ext
    allow-list + realpath inside fonts/ + constant content-type)."""
    if not overlay_dir or not FONT_NAME_RE.match(name) or "." not in name:
        return None
    ext = name.rsplit(".", 1)[1].lower()
    ctype = FONT_CTYPES.get(ext)
    if not ctype:
        return None
    base = os.path.realpath(os.path.join(overlay_dir, "fonts"))
    path = os.path.realpath(os.path.join(base, name))
    if not path.startswith(base + os.sep):
        return None
    return (path, ctype) if os.path.exists(path) else None

# HUD design-preview backdrop: a per-league Gran Turismo lobby/replay screenshot
# at profiles/<name>/overlay/preview-bg.<ext>. Fixed basename (no request input),
# so the only resolution is the extension fallback — content-type from the
# ASSET_CTYPES identity map, never a request-derived string.
PREVIEW_BG_EXTS = (("jpg", "image/jpeg"), ("jpeg", "image/jpeg"),
                   ("png", "image/png"), ("webp", "image/webp"))


def resolve_preview_bg(overlay_dir, assets_dir=None):
    """Resolve the HUD-preview backdrop to (path, content_type): the per-profile
    overlay/preview-bg.<ext> when present (a league override), else the shipped
    shared default assets/preview-bg.<ext>. None only when neither exists. Read
    per request so a swapped screenshot shows on reload."""
    for base in (overlay_dir, assets_dir):
        if not base:
            continue
        for ext, ctype in PREVIEW_BG_EXTS:
            path = os.path.join(base, f"preview-bg.{ext}")
            if os.path.exists(path):
                return path, ctype
    return None


def resolve_preview_frame(graphics_dir):
    """Resolve the broadcast Overlay.png frame (downloaded by get-graphics.py into
    runtime/<profile>/graphics/) to (path, content_type), or None when absent —
    the preview then renders the backdrop + HUD text without the frame."""
    if not graphics_dir:
        return None
    path = os.path.join(graphics_dir, "Overlay.png")
    return (path, "image/png") if os.path.exists(path) else None

def default_runtime_dir(here):
    """Where runtime data lives when --runtime-dir is not given.
    Repo layout (src/relay/) -> <repo>/runtime ; distributed package (relay/) -> next to the script.
    Keeps get-cookies.py and the relay consistent without an explicit flag."""
    if os.path.basename(here) == "relay" and os.path.basename(os.path.dirname(here)) == "src":
        return os.path.join(os.path.dirname(os.path.dirname(here)), "runtime")
    return here


def load_dotenv(start):
    """Load KEY=VALUE pairs from a .env at the script dir or the project root
    into os.environ. Real environment variables win (setdefault). Returns the
    path loaded, or None. Tiny on purpose — no python-dotenv dependency.

    SECURITY: bounded to the project. The project root is the nearest ancestor
    holding a .git/.env.example marker; we only ever read a .env from `start`
    or from that root, never from an unrelated parent (e.g. a stray
    ~/Downloads/.env that could inject HTTPS_PROXY/DYLD_* into the
    cookie-bearing yt-dlp/streamlink subprocesses)."""
    candidates, d = [start], start
    for _ in range(4):
        if any(os.path.exists(os.path.join(d, m)) for m in (".git", ".env.example")):
            candidates.append(d)
            break
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    for c in candidates:
        p = os.path.join(c, ".env")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return p
    return None


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


def is_channel(v: str) -> bool:
    v = v.strip()
    return bool(CHANNEL_RE.match(v)) or _is_stream_url(v)


# #592: the Schedule URL cell `local:` means "this stint comes from the producer
# machine's capture card" (RACECAST_CAPTURE in the machine .env). Only the bare token
# is accepted — the sheet names no device, so nothing from the sheet reaches an argv.
LOCAL_SOURCE_TOKEN = "local:"


def is_local_source(v) -> bool:
    return isinstance(v, str) and v.strip().lower() == LOCAL_SOURCE_TOKEN


def is_feed_source(v) -> bool:
    """What a Schedule/Qualifying row may carry: a remote stream or `local:`. The
    commentator submit and POV paths deliberately stay on is_channel, so only the
    director (panel) or the sheet can point a feed at the capture card."""
    return is_local_source(v) or is_channel(v)


def feed_source_value(v):
    """A feed-source cell as stored: `local:` normalised, anything else stripped."""
    v = (v or "").strip()
    return LOCAL_SOURCE_TOKEN if is_local_source(v) else v


def platform_of(url):
    """Which streaming platform a (possibly bare-ID-wrapped) URL targets.
    Host-based, reusing the userinfo-safe parse from _is_stream_url. Anything
    that is not a Twitch host (including bare UC ids, which channel_url wraps
    into a youtube.com URL) is treated as YouTube -- the default path."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        host = ""
    if host == "twitch.tv" or host.endswith(".twitch.tv"):
        return "twitch"
    return "youtube"

def feed_platform(url):
    """platform_of plus the relay-only `local:` capture source (#592). feed_platform and
    feed_url are kept apart so platform_of/channel_url stay byte-identical to their
    loopstream copies (test_streams)."""
    return "local" if is_local_source(url) else platform_of(url)


def feed_url(entry):
    """channel_url, except that `local:` is never wrapped into a YouTube URL (#592)."""
    return LOCAL_SOURCE_TOKEN if is_local_source(entry) else channel_url(entry)


def asset_key(s):
    """Normalize free text (country/brand) to an asset filename stem."""
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    return re.sub(r"[^a-z0-9-]", "", s)

def resolve_roles(crew_rows, schedule_keys, subject, crew_commentator_keys=frozenset(),
                  crew_race_control_keys=frozenset()):
    """Resolve a verified identity *subject* to its capability set for this event.

    crew_rows: iterable of (name, is_director, is_producer) from CrewSource.get().
    schedule_keys: set of asset_key-normalized streamer names present in the live
        Schedule/Qualifying roster.
    subject: the asset_key-normalized person name from the verified token.
    crew_commentator_keys: set of asset_key-normalized names whose Crew
        Commentator flag is truthy (A1 union source).
    crew_race_control_keys: set of asset_key-normalized names whose Crew Race
        Control flag is truthy (#244).

    Returns a subset of {"commentator", "director", "producer", "race_control"}:
    - "commentator" iff subject is in the live Schedule OR carries the Crew
      Commentator flag (A1 union);
    - "director"/"producer" from any Crew row whose name normalizes to subject;
    - "race_control" iff subject carries the Crew Race Control flag.
    An unknown subject (no crew row, not in the schedule) yields the empty set.
    Roles are additive (a person may be e.g. both director and race_control).
    A producer oversees the whole event, so "producer" IMPLIES "director"
    (broadcast control) and "race_control" (read-only monitoring): a producer can
    follow and, if needed, steer the event from the /console pages without a
    separate Crew flag. The producer-only step-up ops (set/stint, mode, takeover)
    stay independently step-up-gated in console_policy regardless.
    Identity != authorization: this is the only place roles are derived, per the
    role-based-funnel-access spec (#216)."""
    roles = set()
    if subject in schedule_keys or subject in crew_commentator_keys:
        roles.add("commentator")
    if subject in crew_race_control_keys:
        roles.add("race_control")
    for name, is_dir, is_prod in crew_rows:
        if asset_key(name) != subject:
            continue
        if is_dir:
            roles.add("director")
        if is_prod:
            roles.add("producer")
    if "producer" in roles:
        roles.update(("director", "race_control"))
    return roles


def schedule_keys(rows):
    """Set of asset_key-normalized streamer names present in a schedule's rows
    ([(url, name, stint, line)]). This is the implicit commentator roster that
    resolve_roles unions with the Crew tab (#216)."""
    return {asset_key(n) for (_u, n, _s, _l) in rows if (n or "").strip()}

# No `\s*` adjacent to `(.*?)`: the two would both match a space, so a long space
# run with no trailing '#<digits>' backtracks quadratically (CodeQL py/polynomial-
# redos #170). The surrounding whitespace is already handled — the input is
# `.strip()`-ed below and group(1) is `.strip()`-ed on return — so dropping the
# `\s*` neighbours is behaviour-preserving and makes the match linear.
TEAM_NUMBER_RE = re.compile(r"^(.*?)#(\d+)$")

def split_team_label(s):
    """Split a team label into (name, number): a TRAILING '#<digits>' token is
    peeled off ('OVO eSports #111' -> ('OVO eSports', '111')); no trailing number
    -> (stripped, ''). A '#' that is not a trailing all-digit token stays in the
    name. Used to strip the embedded number so it never double-displays, and as
    the backward-compat number source when the Configuration tab has no Number
    column."""
    s = (s or "").strip()
    mtch = TEAM_NUMBER_RE.match(s)
    if mtch:
        return mtch.group(1).strip(), mtch.group(2)
    return s, ""

OVERLAY_LABELS = {
    "stint": "stint", "streamer": "streamer", "session": "session",
    "round top": "round_top", "round bottom": "country",
    "race control": "race_control", "flag": "flag",
}


def _first_value(row, start=2):
    for c in range(start, len(row)):
        v = (row[c] or "").strip()
        if v:
            return v
    return ""


def parse_overlay(text):
    """Overlay tab CSV -> {streamer, session, round_top, country,
    race_control, teams:[p1,p2,p3]}. Label is column B; value is the first
    non-empty cell from column C on."""
    out = {v: "" for v in OVERLAY_LABELS.values()}
    teams = {"teams p1": 0, "teams p2": 1, "teams p3": 2}
    out["teams"] = ["", "", ""]
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2:
            continue
        label = (row[1] or "").strip().lower()
        if label in OVERLAY_LABELS:
            out[OVERLAY_LABELS[label]] = _first_value(row)
        elif label in teams:
            out["teams"][teams[label]] = _first_value(row)
    return out


# Accepted headers for the team's brand TEXT column (priority order). The sheet's
# image columns ("Brand Logo", "Brands") are deliberately NOT in this set.
BRAND_TEXT_HEADERS = ("brand key", "brand name", "brand")
# Optional DISPLAY-name override column. When present and non-empty it overrides
# the brand TEXT shown in the HUD; it NEVER affects the brand logo (brandKey is
# always asset_key(Brand)). Exact whole-cell match -> no collision with
# BRAND_TEXT_HEADERS' "brand name".
BRAND_NAME_OVERRIDE_HEADERS = ("brand name override",)
# Accepted headers locating the team-name and (optional) car-number columns.
TEAM_NAME_HEADERS = ("teams", "team name")
NUMBER_HEADERS = ("number",)

# Schedule tab headers (matched case-insensitively, like the Configuration ones).
# When the URL header is present the columns are located by name (so they may
# move and the Stint label is read); otherwise the parser falls back to the
# positional auto-detect that needs no header row (back-compat).
SCHEDULE_URL_HEADERS = ("url",)
SCHEDULE_STREAMER_HEADERS = ("streamer", "name")  # "name" = the POV tab's column
SCHEDULE_STINT_HEADERS = ("stint",)
# Crew tab headers (matched case-insensitively). The Crew tab carries
# Name | Director | Producer boolean columns; commentator capability is NOT
# listed here -- it is implied by presence in the live Schedule roster
# (see resolve_roles). See issue #216 / the role-based-funnel-access spec.
CREW_NAME_HEADERS = ("name", "crew", "person")
CREW_DIRECTOR_HEADERS = ("director",)
CREW_PRODUCER_HEADERS = ("producer",)
CREW_COMMENTATOR_HEADERS = ("commentator",)
# Race Control (#244): a read-only monitoring desk role. Header-located only
# (mirrors Commentator) — no positional fallback. NB: the role string is
# "race_control"; the unrelated director-only HUD banner is "racecontrol".
CREW_RACE_CONTROL_HEADERS = ("race control", "race-control", "racecontrol", "rc")
CREW_DISCORD_HEADERS = ("discord", "discord handle", "discord username")
CREW_TRUTHY = frozenset({"x", "yes", "true", "1", "y", "✓"})


def _crew_truthy(v):
    """True iff a Crew cell marks the role set (case-insensitive, trimmed)."""
    return (v or "").strip().lower() in CREW_TRUTHY


# Optional per-team tile colours (issue #555): a flat background + text colour per
# car, published by the HUD as --team-bg/--team-fg and consumed by a profile's
# overlay CSS. Header-located like every other Configuration column, so positions
# stay free; absent column or blank cell -> "" and the profile's CSS fallback wins.
TEAM_BG_COLOR_HEADERS = ("bg color", "bg colour", "background color",
                         "background colour")
TEAM_TEXT_COLOR_HEADERS = ("text color", "text colour", "fg color", "fg colour")

# A plausible CSS colour token: #rgb/#rgba/#rrggbb/#rrggbbaa, an
# rgb()/rgba()/hsl()/hsla() function, or a bare keyword. Anything else -> "". This
# is the gate that keeps a sheet cell from smuggling a url() (a resource fetch)
# into the custom property the HUD sets on its slots — the sheet is admin-managed,
# but the overlay renders on air and a typo must never turn into a network request.
# Letters are allowed INSIDE the function form so unit/keyword arguments pass
# (`hsl(120deg 50% 50%)`, `rgb(0 0 0 / 50%)`); that keeps the security property
# intact because the character class admits no parenthesis, semicolon, colon or
# quote — so an accepted value can never nest a functional notation like url() nor
# escape the custom-property value into a second declaration.
CSS_COLOR_RE = re.compile(
    r"^(?:#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})"
    r"|(?:rgb|hsl)a?\([0-9a-z.,%\s/]+\)"
    r"|[a-z]{3,20})$", re.I)


def sanitize_css_color(v):
    """A sheet colour cell -> a safe CSS colour string, or '' when implausible."""
    s = (v or "").strip()
    return s if CSS_COLOR_RE.match(s) else ""


# Quali Times tab (issue #555): Team | Best Lap, one row per car, maintained ONCE
# between qualifying and the race. Deliberately NOT the 'Qualifying' tab — that
# name is the qualifying SCHEDULE (DEFAULT_QUALIFYING_TAB).
# The lap header is deliberately NARROW: a bare "quali"/"lap" alias would happily
# match an unrelated column (a fallback CSV, a renamed tab) and put plausible-
# looking garbage on air as a lap time.
QUALI_TEAM_HEADERS = ("team", "teams", "team name")
QUALI_LAP_HEADERS = ("best lap", "best-lap", "bestlap", "quali time")

# A best lap a Sheets duration format produced ('0:01:38.973'): the hours group is
# dropped when zero, so the HUD shows the broadcast form '1:38.973'.
QUALI_LAP_DURATION_RE = re.compile(r"^(\d{1,2}):(\d{1,2}):(\d{2}(?:\.\d+)?)$")


def normalize_quali_lap(v):
    """A 'Best Lap' cell -> the HUD display string. Verbatim except two
    deterministic fixes: a comma decimal becomes a dot, and a ZERO hours group
    from a Sheets duration-formatted cell is dropped ('0:01:38,973' ->
    '1:38.973'). A non-zero hour is left alone — implausible for a lap, but
    showing the cell beats silently destroying it. No conversion to seconds and
    no reformatting: the sheet stays WYSIWYG, like every other HUD text field."""
    s = (v or "").strip().replace(",", ".")
    mt = QUALI_LAP_DURATION_RE.match(s)
    if mt and not mt.group(1).strip("0"):
        return f"{int(mt.group(2))}:{mt.group(3)}"
    return s


def parse_quali_times(text):
    """Quali Times tab CSV -> {asset_key: display_lap}. Each row registers TWO
    keys, mirroring the two-step lookup the roster already uses (see
    parse_config_roster / team_entry): the asset_key of the VERBATIM cell (e.g.
    'scuderia-adriatica-motorsport-14' — the per-CAR identity) and the asset_key of
    the STRIPPED team name ('scuderia-adriatica-motorsport' — the whole team). So a
    sheet that spells out '#14' and '#54' gives each car its own lap, while a sheet
    that writes only the bare team name still matches every car of that team.
    Both keys are setdefault'ed, so a generic row can never overwrite a specific
    one; on an identical key the FIRST row wins. Columns are located by header
    name; a missing tab, missing header, or either column absent -> {}, so a league
    without this tab is simply unaffected."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return {}
    header = [(h or "").strip().lower() for h in rows[0]]
    ti = next((header.index(h) for h in QUALI_TEAM_HEADERS if h in header), None)
    li = next((header.index(h) for h in QUALI_LAP_HEADERS if h in header), None)
    if ti is None or li is None:
        return {}
    out = {}
    for row in rows[1:]:
        if len(row) <= ti or len(row) <= li:
            continue
        raw = (row[ti] or "").strip()
        name, _embedded = split_team_label(raw)
        lap = normalize_quali_lap(row[li])
        if not lap:
            continue
        for key in (asset_key(raw), asset_key(name)):
            if key:
                out.setdefault(key, lap)
    return out


def parse_config_roster(text):
    """Configuration tab CSV -> roster {team_label: {"number": str, "brandKey": str,
    "brandName": str, "bgColor": str, "textColor": str}}. The dict KEY is the
    VERBATIM team label (e.g. 'Scuderia #14'), so two cars sharing a name but
    differing in number stay distinct entries — the stripped name is NOT a unique
    identity. The embedded '#NNN' is peeled off only to derive the fallback car
    number (split_team_label); the displayed name is stripped later, at HUD render
    time. brandName = the "Brand Name Override" cell or, when blank, the verbatim
    brand text; brandKey (the logo) is always asset_key(brand) regardless. The
    Number column wins over the embedded token, which is only the fallback.
    bgColor/textColor are the optional per-team tile colours (sanitize_css_color'd,
    "" when unset or implausible). Columns are located by header name so positions
    stay free. A missing team-name header -> {}. A missing Brand/Number/colour
    column just yields '' for that field."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return {}
    header = [(h or "").strip().lower() for h in rows[0]]
    ti = next((header.index(h) for h in TEAM_NAME_HEADERS if h in header), None)
    if ti is None:
        return {}
    bi = next((header.index(h) for h in BRAND_TEXT_HEADERS if h in header), None)
    oi = next((header.index(h) for h in BRAND_NAME_OVERRIDE_HEADERS if h in header), None)
    ni = next((header.index(h) for h in NUMBER_HEADERS if h in header), None)
    ci = next((header.index(h) for h in TEAM_BG_COLOR_HEADERS if h in header), None)
    fi = next((header.index(h) for h in TEAM_TEXT_COLOR_HEADERS if h in header), None)
    out = {}
    for row in rows[1:]:
        if len(row) <= ti:
            continue
        label = (row[ti] or "").strip()
        _name, embedded = split_team_label(label)
        if not _name:
            continue
        col_num = (row[ni].strip() if ni is not None and len(row) > ni else "")
        brand_raw = (row[bi].strip() if bi is not None and len(row) > bi else "")
        override = (row[oi].strip() if oi is not None and len(row) > oi else "")
        out[label] = {"number": col_num or embedded,
                      "brandKey": asset_key(brand_raw),
                      "brandName": override or brand_raw,
                      "bgColor": sanitize_css_color(
                          row[ci] if ci is not None and len(row) > ci else ""),
                      "textColor": sanitize_css_color(
                          row[fi] if fi is not None and len(row) > fi else "")}
    return out


def parse_team_full_labels(text):
    """Configuration tab CSV -> {stripped_team_name: verbatim_label}. The verbatim
    label (e.g. 'OVO eSports #111') is exactly what the Setup tab's team dropdowns
    list; the panel offers the stripped name (name/number split for the HUD), so
    the relay maps it back to this verbatim value when writing the Setup cell —
    keeping teams in lockstep with the dropdown, like the other Setup fields. Same
    column-location rules as parse_config_roster; first occurrence wins."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return {}
    header = [(h or "").strip().lower() for h in rows[0]]
    ti = next((header.index(h) for h in TEAM_NAME_HEADERS if h in header), None)
    if ti is None:
        return {}
    out = {}
    for row in rows[1:]:
        if len(row) <= ti:
            continue
        label = (row[ti] or "").strip()
        name, _embedded = split_team_label(label)
        if name:
            out.setdefault(name, label)
    return out


# Configuration-tab vocabulary columns feeding the panel's Setup dropdowns
# (strict: the panel offers ONLY these values — spec: panel-sheet-control).
# Dict KEYS are the API field names used by panel endpoints; VALUES are sheet headers (matched case-insensitively).
VOCAB_COLUMNS = {"stint": "stints", "streamer": "streamers",
                 "session": "session", "racecontrol": "race control",
                 "flag": "flag"}


def parse_config_vocab(text):
    """Configuration tab CSV -> {field_key: [options]} for the panel
    dropdowns. Columns located by header name (parse_config_roster precedent);
    blanks skipped, duplicates dropped, sheet order kept."""
    rows = list(csv.reader(io.StringIO(text)))
    out = {k: [] for k in VOCAB_COLUMNS}
    if not rows:
        return out
    header = [(h or "").strip().lower() for h in rows[0]]
    for key, name in VOCAB_COLUMNS.items():
        if name not in header:
            continue
        i = header.index(name)
        seen = set()
        for row in rows[1:]:
            v = (row[i] or "").strip() if len(row) > i else ""
            if v and v not in seen:
                seen.add(v)
                out[key].append(v)
    return out


# Configuration-tab column of director cue presets (admin-managed, read-only in
# the panel). Located by header like the vocab columns; blanks/dupes dropped.
CUE_PRESET_HEADERS = ("cue preset", "cue presets", "cue")


def parse_cue_presets(text):
    """Configuration tab CSV -> [preset strings] for the panel's cue buttons."""
    return _parse_preset_column(text, CUE_PRESET_HEADERS)


# Configuration-tab column of Race Control quick-note presets (#376), located by
# header like the cue presets; same admin-managed, read-only vocabulary model.
RC_NOTE_PRESET_HEADERS = ("rc note", "rc notes", "race control note",
                          "race control notes")


def parse_rc_note_presets(text):
    """Configuration tab CSV -> [preset strings] for the Race Control desk's
    quick-note buttons (#376). Same shape as parse_cue_presets."""
    return _parse_preset_column(text, RC_NOTE_PRESET_HEADERS)


def _parse_preset_column(text, headers):
    """Shared: first matching header column -> de-duped, non-blank values in order."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    header = [(h or "").strip().lower() for h in rows[0]]
    i = next((header.index(h) for h in headers if h in header), None)
    if i is None:
        return []
    out, seen = [], set()
    for row in rows[1:]:
        v = (row[i] or "").strip() if len(row) > i else ""
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def team_entry(raw, roster, quali=None):
    """One /hud/data team object from an Overlay slot value + the roster (+ the
    optional Quali Times map). The roster is keyed by the VERBATIM label, so the
    lookup uses the raw slot value first (the per-car identity); a stripped-name
    fallback covers a bare slot value against a roster whose number lives in a
    separate Number column. Displayed 'name' is the stripped form;
    'number'/logo come from the roster (Number column precedence already baked
    in), with the slot's own embedded #NNN as the fallback. 'label' carries the
    verbatim value (with #NNN) so the panel dropdown can offer/select the exact
    car — the HUD ignores it. bgColor/textColor are the optional tile colours.
    qualiLap uses the SAME two-step lookup as the roster: the verbatim label first
    (so two cars of one team keep their own lap) and the stripped name as the
    fallback (so a bare Quali Times row matches every car of that team, and
    number/spelling variants between the two tabs still line up) — issue #555."""
    raw = (raw or "").strip()
    name, embedded = split_team_label(raw)
    info = roster.get(raw) or roster.get(name) or {}
    quali = quali or {}
    return {"name": name,
            "number": info.get("number") or embedded,
            "brandKey": info.get("brandKey", ""),
            "brandName": info.get("brandName", ""),
            "label": raw,
            "bgColor": info.get("bgColor", ""),
            "textColor": info.get("textColor", ""),
            "qualiLap": quali.get(asset_key(raw)) or quali.get(asset_key(name)) or ""}


def build_hud_data(overlay, roster, quali=None):
    """Combine an Overlay map + roster {team: {number, brandKey, brandName,
    bgColor, textColor}} + the optional Quali Times map into /hud/data."""
    return {
        "stint": overlay.get("stint", ""),
        "streamer": overlay.get("streamer", ""),
        "session": overlay.get("session", ""),
        "round": {
            "top": overlay.get("round_top", ""),
            "country": overlay.get("country", ""),
            "flagKey": asset_key(overlay.get("country", "")),
        },
        "teams": [team_entry(n, roster, quali)
                  for n in overlay.get("teams", ["", "", ""])],
        "raceControl": overlay.get("race_control", ""),
        "flag": overlay.get("flag", ""),
    }

# ---------- Race timer (relay-hosted countdown) ----------
# State model (spec: docs/superpowers/specs/2026-06-06-race-timer-design.md):
# one absolute end anchor + duration + visibility + an "updated" stamp that
# drives the newest-wins merge between the local file and the Sheet tab.
# Stopwatch semantics: START starts or resumes, STOP pauses (freezes the
# remainder in "remaining"), RESET clears. "end" and "remaining" are mutually
# exclusive — an anchor means running/finished, a remainder means paused.
TIMER_DEFAULT_DURATION = 6 * 3600          # seconds, until the Director sets one
DURATION_RE = re.compile(r"^(\d{1,2}):([0-5]\d):([0-5]\d)$")  # hours 1-2 digits (24h races OK)


def default_timer_state():
    return {"end": None, "duration": TIMER_DEFAULT_DURATION,
            "remaining": None, "visible": True, "updated": 0.0}


def parse_duration(s):
    """'H:MM:SS' (or HH:MM:SS) -> seconds, else None."""
    mt = DURATION_RE.match((s or "").strip())
    if not mt:
        return None
    h, mi, sec = (int(g) for g in mt.groups())
    return h * 3600 + mi * 60 + sec


def format_duration(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def parse_utc_ts(s):
    """Lenient UTC timestamp -> epoch seconds, else None. Accepts the canonical
    '...T..Z', Apps Script's toISOString ('.000Z'), and the 'YYYY-MM-DD HH:MM:SS'
    form gviz CSV produces when Sheets re-types the cell as a date."""
    s = (s or "").strip().replace("T", " ").rstrip("Zz").strip()
    s = s.split(".", 1)[0]                     # drop fractional seconds
    try:
        d = datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None
    return d.replace(tzinfo=datetime.timezone.utc).timestamp()


def iso_utc(epoch):
    """Epoch seconds -> canonical ISO UTC string (whole-second precision —
    a Sheet 'updated' stamp can therefore never out-rank the local stamp of
    the very write that produced it; the newest-wins merge stays correct)."""
    return datetime.datetime.fromtimestamp(
        epoch, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_timer_tab(text):
    """Timer tab CSV (label col A, value col B) -> state dict. Unknown labels
    and unparseable values fall back to the defaults — never throws."""
    raw = {}
    for row in csv.reader(io.StringIO(text or "")):
        if len(row) >= 2 and (row[0] or "").strip():
            raw[row[0].strip().lower()] = (row[1] or "").strip()
    st = default_timer_state()
    st["end"] = parse_utc_ts(raw.get("race end (utc)", ""))
    dur = parse_duration(raw.get("duration", ""))
    if dur is not None:
        st["duration"] = dur
    st["remaining"] = parse_duration(raw.get("remaining", ""))
    if st["end"] is not None:
        st["remaining"] = None   # mutually exclusive — a set anchor wins
    st["visible"] = raw.get("visible", "").upper() != "FALSE"
    st["updated"] = parse_utc_ts(raw.get("updated (utc)", "")) or 0.0
    return st


def timer_mode(state, now):
    """hidden | prestart | running | paused | finished (pure; the page renders
    blank for hidden and finished — spec: no 0:00:00 left standing)."""
    if not state.get("visible", True):
        return "hidden"
    end = state.get("end")
    if end is not None:
        return "running" if now < end else "finished"
    if state.get("remaining") is not None:
        return "paused"
    return "prestart"


def merge_timer_states(local, sheet):
    """Newest write wins; tie (or no sheet state) -> local. Makes a producer
    takeover adopt the Sheet anchor without letting a stale Sheet revert a
    fresh local action whose webhook push failed."""
    if sheet is None:
        return local
    return sheet if sheet.get("updated", 0.0) > local.get("updated", 0.0) else local


def post_webhook(url, payload, timeout=10):
    """POST JSON to the Apps-Script sheet-write webhook -> raw response bytes."""
    req = Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                  headers={"User-Agent": "racecast-feeds/1.0",
                           "Content-Type": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


WEBHOOK_OUTDATED_ERROR = ("webhook script outdated (no action echo) — redeploy "
                          "the v2 script (wiki: Sheet-Webhook)")


def check_webhook_response(body, expected_action=None):
    """(ok, error) from an Apps-Script response body. Apps Script answers
    HTTP 200 even for errors, so only its own {"ok": true} counts. The v2
    script echoes the action; an ok WITHOUT the echo on an action write is a
    still-deployed v1 (timer-only) script -> report it, never a false success."""
    try:
        d = json.loads((body or b"{}").decode("utf-8", "replace"))
    except (ValueError, AttributeError):
        d = None
    if not isinstance(d, dict) or d.get("ok") is not True:
        snippet = (body or b"")[:120].decode("utf-8", "replace")
        return False, f"webhook did not confirm: {snippet!r}"
    if expected_action and d.get("action") != expected_action:
        return False, WEBHOOK_OUTDATED_ERROR
    return True, None


WEBHOOK_RETRY_ATTEMPTS = 3
WEBHOOK_RETRY_BASE_S = 0.5
WEBHOOK_RETRY_BUDGET_S = 10.0
WEBHOOK_RETRY_TIMEOUT_S = 5   # per-attempt; lower than the direct 10 s so a hung try fails fast


def webhook_error_permanent(err):
    """True iff *err* is the permanent 'script outdated' config error (never retry).
    Any other non-ok error (a transient 'did not confirm') is retryable. Pure."""
    return err == WEBHOOK_OUTDATED_ERROR


def push_webhook_retrying(url, payload, expected_action=None, *,
                          post=None, sleep=None, rand=None, now=None,
                          attempts=WEBHOOK_RETRY_ATTEMPTS,
                          base_delay=WEBHOOK_RETRY_BASE_S,
                          budget_s=WEBHOOK_RETRY_BUDGET_S,
                          timeout=WEBHOOK_RETRY_TIMEOUT_S):
    """POST with bounded backoff+jitter until the Apps Script confirms {"ok":true},
    a PERMANENT error is hit, or attempts/budget are exhausted. Returns
    (ok, err, body); *err* is UNPREFIXED (caller adds its own 'push:' prefix as
    today). Retries a network exception and a transient 'did not confirm' body; a
    permanent 'script outdated' error is returned immediately. `post`/`sleep`/`rand`/
    `now` resolve at CALL time (default the module globals) so the existing
    m.post_webhook monkeypatch seam keeps working; injectable for deterministic
    tests."""
    post = post or post_webhook
    sleep = sleep or time.sleep
    rand = rand or random.random
    now = now or time.monotonic
    start = now()
    err = "not attempted"
    body = None
    for i in range(max(1, attempts)):
        if i > 0 and (now() - start) >= budget_s:
            break
        try:
            body = post(url, payload, timeout=timeout)
        except Exception as e:            # noqa: BLE001 — network/timeout is retryable
            err = f"{type(e).__name__}: {e}"
            body = None
        else:
            ok, cerr = check_webhook_response(body, expected_action)
            if ok:
                return True, None, body
            err = cerr
            if webhook_error_permanent(cerr):
                return False, err, body      # permanent config error -> no retry
        if i + 1 >= attempts:
            break
        delay = base_delay * (2 ** i) + rand() * base_delay
        if (now() - start) + delay >= budget_s:
            break
        sleep(delay)
    return False, err, body


def apply_stream_service_for_ref(ref, channel_csv_url, push_url, set_service,
                                 fetch=None, post=None):
    """Resolve the event platform (Channel tab) + the real stream key
    (get_stream_key webhook) for a stream-key `ref`, and apply it to OBS via
    set_service(platform, key). Returns (ok, note); `note` NEVER contains the
    key. Relay-side twin of racecast.py::_apply_stream_target. Seams: `fetch`
    (CSV text) and `post` (webhook) for tests."""
    fetch = fetch or TimerStore._fetch
    post = post or post_webhook
    if not push_url:
        return False, "no SHEET_PUSH_URL — the stream-key webhook is required"
    try:
        chan_rows = broadcast_chat.parse_channel_tab(fetch(channel_csv_url))
    except Exception as exc:                            # noqa: BLE001 — tolerant fetch
        return False, "channel fetch failed: {}".format(type(exc).__name__)
    platform = stream_target.event_platform(chan_rows)
    if not platform:
        return False, "no channel/platform configured (Channel tab)"
    try:
        body = post(push_url, {"action": "get_stream_key", "ref": ref})
    except Exception as exc:                            # noqa: BLE001 — tolerant webhook
        return False, "stream-key webhook failed: {}".format(type(exc).__name__)
    key, err = stream_target.parse_stream_key_response(body)
    if err:
        return False, err
    ok, note = set_service(platform, key)
    del key   # drop our last named reference to the key before returning
    if not ok:
        return False, note
    return True, "stream target set on {}".format(platform)


EVENT_TITLE_MAX = 120


def sanitize_event_title(raw):
    """Normalize a free-text event title (#207): coerce non-strings to "", drop
    control chars (a title is one line — newlines/tabs/etc. are removed), trim, and
    cap at EVENT_TITLE_MAX chars. Pure → unit-tested. This is normalization only:
    every surface renders the title via textContent (Cockpit) / a text node
    (Panel), so it is never interpreted as HTML."""
    if not isinstance(raw, str):
        return ""
    cleaned = "".join(ch for ch in raw if ord(ch) >= 32)
    return cleaned.strip()[:EVENT_TITLE_MAX].strip()


def default_part_state():
    return {"index": 1, "live": False}


class PartStore:
    """Persisted broadcast-Part pointer at runtime/<profile>/part.json.

    State {"index": N (1-based into the Producer order), "live": bool}. Reset to
    Part 1 by `racecast event start`; End advances `index`; Start marks it live.
    `live` is a written record only — the panel derives the authoritative live
    state from OBS. Same best-effort, lock-guarded, type-checked-load contract as
    TimerStore/EventTitleStore (a hand-edited file must never crash later)."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.state = default_part_state()
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        except OSError:
            pass  # fresh layout; _save_file degrades per-write if the dir is missing
        self._load_file()

    def _load_file(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                saved = json.load(fh)
        except (OSError, ValueError):
            return  # no/corrupt file -> defaults
        st = default_part_state()
        if isinstance(saved, dict):
            idx = saved.get("index")
            if isinstance(idx, int) and not isinstance(idx, bool) and idx >= 1:
                st["index"] = idx
            if isinstance(saved.get("live"), bool):
                st["live"] = saved["live"]
        self.state = st

    def _save_file(self):
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self.state, fh)
        except OSError:
            pass  # best-effort, same contract as the timer/event caches

    def get(self):
        with self.lock:
            return dict(self.state)

    def mark_live(self, index):
        with self.lock:
            self.state = {"index": int(index), "live": True}
            self._save_file()
            return dict(self.state)

    def end(self):
        with self.lock:
            self.state = {"index": int(self.state["index"]) + 1, "live": False}
            self._save_file()
            return dict(self.state)

    def reset(self):
        """Reset the pointer to the first part, not-live — used by `event start`
        (via the file) and on a live `/mode` switch so the mode-gated Parts
        control never carries a stale index into the other mode's subset."""
        with self.lock:
            self.state = default_part_state()
            self._save_file()
            return dict(self.state)


class EventTitleStore:
    """Free-text event title (#207), persisted to runtime/<profile>/event.json so it
    survives a relay restart and rides along at a producer takeover (pulled like
    chat). Mirrors TimerStore's local-file layer but with NO sheet sync: the title
    is producer-side event state, deliberately never written to the Sheet. Startup
    precedence: event.json (if present, even when blank) > the EVENT_TITLE default
    (from profile.env) > empty."""

    def __init__(self, path, default=""):
        self.path = path
        self.lock = threading.Lock()
        self.title = sanitize_event_title(default)
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        except OSError:
            pass  # fresh layout; _save_file degrades per-write if the dir is missing
        self._load_file()

    def _load_file(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                saved = json.load(fh)
        except (OSError, ValueError):
            return  # no/corrupt file -> keep the default
        if isinstance(saved, dict) and isinstance(saved.get("title"), str):
            self.title = sanitize_event_title(saved["title"])

    def _save_file(self):
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump({"title": self.title}, fh)
        except OSError:
            pass  # best-effort, same contract as the timer/schedule caches

    def get(self):
        with self.lock:
            return self.title

    def set(self, raw):
        """Validate + store + persist a new title; returns the stored value."""
        with self.lock:
            self.title = sanitize_event_title(raw)
            self._save_file()
            return self.title

    def data(self):
        return {"title": self.get()}


class TimerStore:
    """Race-timer state with three layers (spec §3): in-memory + local JSON
    file (restart-safe) + Sheet tab via CSV poll / Apps-Script webhook push
    (producer-handover-safe). Director actions apply locally first and push in
    the background — a webhook failure degrades, never breaks."""

    def __init__(self, csv_url, push_url, path):
        self.csv_url = csv_url            # None -> local-only (no sheet poll)
        self.push_url = push_url          # None -> push disabled
        self.path = path
        self.lock = threading.Lock()
        self.state = default_timer_state()
        self.last_ok = None               # last successful sheet read
        self.last_error = None
        self.push_status = "disabled" if not push_url else "never"
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        except OSError:
            # The runtime dir normally exists; a fresh layout must not break
            # startup — _save_file() degrades per-write if the dir is missing.
            pass
        self._load_file()

    # -- persistence ------------------------------------------------------
    def _load_file(self):
        """Adopt only known keys with sane types — a hand-edited timer.json
        must never crash data()/timer_mode() later."""
        try:
            with open(self.path, encoding="utf-8") as fh:
                saved = json.load(fh)
        except (OSError, ValueError):
            return  # no/corrupt file -> defaults; the sheet poll may catch us up
        st = default_timer_state()
        def num(v):
            return isinstance(v, (int, float)) and not isinstance(v, bool)
        if num(saved.get("end")):
            st["end"] = float(saved["end"])
        if num(saved.get("duration")):
            st["duration"] = int(saved["duration"])
        if num(saved.get("remaining")) and st["end"] is None:
            st["remaining"] = int(saved["remaining"])
        if isinstance(saved.get("visible"), bool):
            st["visible"] = saved["visible"]
        if num(saved.get("updated")):
            st["updated"] = float(saved["updated"])
        self.state = st

    def _save_file(self):
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self.state, fh)
        except OSError:
            pass  # best-effort, same contract as the schedule/HUD caches

    # -- sheet read (poller + adopt-if-newer merge) -------------------------
    @staticmethod
    def _fetch(url, timeout=10):
        req = Request(url, headers={"User-Agent": "racecast-feeds/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")

    def refresh(self, timeout=10):
        if not self.csv_url:
            return False
        try:
            sheet = parse_timer_tab(self._fetch(self.csv_url, timeout))
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            return False
        with self.lock:
            merged = merge_timer_states(self.state, sheet)
            if merged is not self.state:
                self.state = merged
                self._save_file()
        self.last_ok = time.time()       # diagnostics outside the lock (HudSource precedent)
        self.last_error = None
        return True

    # -- webhook push (Apps Script writes the Timer tab) --------------------
    def _push(self, payload):
        """Success = the Apps Script's own {"ok": true} (see check_webhook_response).
        Retries a transient failure with bounded backoff (#490); timer pushes carry
        no expected_action (a v1 script keeps working)."""
        ok, err, _body = push_webhook_retrying(self.push_url, payload)
        if ok:
            self.push_status = "ok"  # diagnostics: single ref assignments, no lock needed
        else:
            self.push_status = "failed"
            self.last_error = f"push: {err}"

    def _spawn_push(self, payload):
        threading.Thread(target=self._push, args=(payload,), daemon=True).start()

    @staticmethod
    def _payload(st):
        """Webhook body: the full state, strings only (Apps Script writes it 1:1)."""
        return {"end": iso_utc(st["end"]) if st["end"] is not None else "",
                "duration": format_duration(st["duration"]),
                "visible": "TRUE" if st["visible"] else "FALSE",
                "remaining": (format_duration(st["remaining"])
                              if st["remaining"] is not None else "")}

    # -- director actions (stopwatch semantics: start/resume, pause, reset) --
    def _apply(self, now=None, **changes):
        """Local-first write: update + stamp + save, then push full state."""
        now = time.time() if now is None else now
        with self.lock:
            self.state.update(changes)
            self.state["updated"] = now
            self._save_file()
            st = dict(self.state)
        if self.push_url:
            self._spawn_push(self._payload(st))
        return self.data(now)

    def start(self, now=None):
        """Start from prestart (full duration) or resume a paused remainder.
        No-op while an anchor is set (running/finished) — RESET clears first."""
        now = time.time() if now is None else now
        with self.lock:
            if self.state["end"] is not None:
                anchored = True
            else:
                base = self.state["remaining"]
                self.state["end"] = now + (base if base is not None
                                           else self.state["duration"])
                self.state["remaining"] = None
                self.state["updated"] = now
                self._save_file()
                anchored = False
            st = dict(self.state)
        if anchored:
            return {"note": "already started — /timer/reset to clear", **self.data(now)}
        if self.push_url:
            self._spawn_push(self._payload(st))
        return self.data(now)

    def stop(self, now=None):
        """Pause: freeze the remainder (the STOP button). Full reset = reset()."""
        now = time.time() if now is None else now
        with self.lock:
            end = self.state["end"]
            if end is not None:
                self.state["remaining"] = max(0, int(end - now))
                self.state["end"] = None
                self.state["updated"] = now
                self._save_file()
            st = dict(self.state)
        if end is None:
            return {"note": "not running — nothing to pause", **self.data(now)}
        if self.push_url:
            self._spawn_push(self._payload(st))
        return self.data(now)

    def reset(self, now=None):
        """Back to 'not started': clears the anchor and any paused remainder."""
        return self._apply(now=now, end=None, remaining=None)

    def show(self, now=None):
        return self._apply(now=now, visible=True)

    def hide(self, now=None):
        return self._apply(now=now, visible=False)

    def set_duration(self, seconds, now=None):
        # Never re-anchors a running timer (spec §5) — corrections use adjust.
        return self._apply(now=now, duration=int(seconds))

    def adjust(self, delta_s, now=None):
        """Shift whichever clock is relevant by ± seconds: a RUNNING anchor,
        a paused remainder, or — before start — the configured duration.
        Check-and-write under ONE lock so a concurrent stop/reset can never
        be overwritten."""
        now = time.time() if now is None else now
        delta = int(delta_s)
        with self.lock:
            if self.state["end"] is not None:
                self.state["end"] += delta
            elif self.state["remaining"] is not None:
                self.state["remaining"] = max(0, self.state["remaining"] + delta)
            else:
                self.state["duration"] = max(0, self.state["duration"] + delta)
            self.state["updated"] = now
            self._save_file()
            st = dict(self.state)
        if self.push_url:
            self._spawn_push(self._payload(st))
        return self.data(now)

    # -- read side ----------------------------------------------------------
    def data(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            st = dict(self.state)
        return {"visible": st["visible"], "end": st["end"],
                "duration_s": st["duration"], "remaining_s": st["remaining"],
                "server_now": now, "mode": timer_mode(st, now),
                "sync": {"push": self.push_status,
                         "sheet_last_ok_age_s": (round(now - self.last_ok, 1)
                                                 if self.last_ok else None),
                         "last_error": self.last_error}}

    def summary(self):
        """Compact line for /status and `racecast status`."""
        d = self.data()
        return {"mode": d["mode"], "visible": d["visible"],
                "remaining_s": (max(0, int(d["end"] - d["server_now"]))
                                if d["end"] is not None else d["remaining_s"]),
                "push": d["sync"]["push"]}


class ChatStore:
    """Crew-chat history: in-memory ring buffer + best-effort JSON file
    (runtime/<profile>/chat.json), loaded on construction. The relay only ever
    APPENDS via add() (the one remote write path) or re-reads the file via
    reload(); clear/import/pull overwrite the file out-of-band (CLI) and call
    reload(). ts is always the server clock — handover-safe across machines."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        except OSError:
            pass  # best-effort: load_messages() below tolerates a missing/unwritable dir
        self.messages = chat_admin.load_messages(self.path)

    def add(self, user, text, now=None):
        now = time.time() if now is None else now
        msg = chat_admin.sanitize_message({"ts": now, "user": user, "text": text})
        if msg is None:
            return {"error": "message must have non-empty text"}
        with self.lock:
            self.messages.append(msg)
            del self.messages[: -chat_admin.MAX_MESSAGES]
            try:
                chat_admin.write_messages(self.path, self.messages)
            except OSError:
                pass    # best-effort: in-memory append still stands
        return {"ok": True, "message": msg}

    def data(self):
        with self.lock:
            return {"messages": list(self.messages)}

    def reload(self):
        """Re-read the local file into memory. A corrupt file keeps the current
        buffer (a bad reload must never wipe live chat). The CLI/UI surface the
        returned error, so it carries the exception message."""
        # Read + validate outside the lock: a concurrent add() is safe — it has
        # already landed in memory and on disk; reload adopts the file as of now.
        try:
            with open(self.path, encoding="utf-8") as fh:
                payload = json.load(fh)
            msgs = chat_admin.validate_payload(payload)
        except (OSError, ValueError) as e:
            return {"error": f"reload failed: {type(e).__name__}: {e}"}
        with self.lock:
            self.messages = msgs
        return {"ok": True, "count": len(msgs)}


# ---------------------------------------------------------------------------
# Read-only broadcast-chat reader (#294)
#
# Mirrors the event's PUBLIC YouTube broadcast chat into the /console pages so
# the crew can follow it without a separate browser tab. The channel(s) come
# from the Sheet `Channel` tab; the relay resolves the currently-live videoId(s)
# via yt-dlp (exactly like the feed path) and polls each one's Innertube live
# chat. It is READ-ONLY, EPHEMERAL (no file) and best-effort: any failure logs
# and leaves the rest of the relay untouched. Pure parsers live in
# src/scripts/broadcast_chat.py; the network + threads live here (this is the
# self-contained relay, exempt from the http_util User-Agent guard).
#
# A producer handover briefly has TWO live streams on the channel; the
# supervisor runs one reader per live videoId and merges their messages (tagged
# by source), so the crew sees one continuous chat across the A->B overlap.
# ---------------------------------------------------------------------------

# Innertube/live_chat 403 the default urllib UA (like Discord/Fonts), so the
# broadcast-chat HTTP must present a browser UA. Public live chat needs no auth,
# so these requests carry no cookies (yt-dlp still uses the relay cookies for
# the resolve hop, where the bot-check applies).
_YT_CHAT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def _yt_chat_get(url, timeout=15):
    """GET text from a YouTube URL with a browser UA. None on any error."""
    try:
        req = Request(url, headers={"User-Agent": _YT_CHAT_UA,
                                    "Accept-Language": "en-US,en;q=0.9"})
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except Exception:
        return None


def _yt_chat_post_json(url, body, timeout=15):
    """POST a JSON body to an Innertube endpoint, parse the JSON reply. None on
    any error (network, non-JSON, decode)."""
    try:
        data = json.dumps(body).encode("utf-8")
        req = Request(url, data=data, method="POST",
                      headers={"User-Agent": _YT_CHAT_UA,
                               "Content-Type": "application/json"})
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


def _yt_run_json(cmd, timeout=30):
    """Run a yt-dlp command expected to print one JSON object, parse it. None on
    error/non-zero/non-JSON."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                           timeout=timeout, env=external_tool_env(), **_no_window_kwargs())
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


def yt_live_video_ids(channel, cookies, timeout=25, logger=None):
    """Currently-live videoId(s) for one YouTube channel.

    Prefers the channel `/streams` tab so CONCURRENT live streams are all found
    (the producer-handover overlap); falls back to the `/live` shorthand (one
    stream) when the tab enumeration yields nothing. Best-effort: any failure
    returns []."""
    ids = []
    streams_cmd = ["yt-dlp", "--flat-playlist", "--no-warnings",
                   "--playlist-items", "1-15", "-J"]
    if cookies:
        streams_cmd += ["--cookies", cookies]
    streams_cmd += ["--", broadcast_chat.channel_streams_url(channel)]
    data = _yt_run_json(streams_cmd, timeout=timeout)
    for entry in (data or {}).get("entries", []) or []:
        if not isinstance(entry, dict):
            continue
        live = entry.get("live_status") == "is_live" or entry.get("is_live") is True
        if live and entry.get("id"):
            ids.append(entry["id"])
    if ids:
        return ids
    # Fallback: the single primary /live stream (videoId only, no manifest).
    one_cmd = ["yt-dlp", "--print", "id", "--no-warnings", "--no-playlist"]
    if cookies:
        one_cmd += ["--cookies", cookies]
    one_cmd += ["--", broadcast_chat.channel_live_url(channel)]
    try:
        r = subprocess.run(one_cmd, capture_output=True, text=True, errors="replace",
                           timeout=timeout, env=external_tool_env(), **_no_window_kwargs())
        if r.returncode == 0 and r.stdout.strip():
            return [r.stdout.strip().splitlines()[0]]
    except (OSError, subprocess.TimeoutExpired):
        pass  # best-effort resolve; treat as "no live stream" and fall through
    if logger:
        logger.debug("no live stream resolved for channel %s", channel)
    return []


class BroadcastChatStore:
    """Read-only, in-memory mirror of the public broadcast chat (#294).

    Unlike ChatStore this is EPHEMERAL (no file) and has no remote write path —
    the relay's reader threads call add_many(); HTTP only ever reads data().
    Messages carry a `source` videoId so a producer-handover overlap (two live
    streams) renders as one merged, ts-ordered stream. Dedup is by the YouTube
    chat-item id (continuations re-deliver recent messages)."""

    _SEEN_CAP = 5000

    def __init__(self, cap=None):
        self.cap = cap or broadcast_chat.MAX_MESSAGES
        self.lock = threading.Lock()
        self.messages = []          # [{ts, user, text, source}], ts-ordered
        self._seen = set()          # chat-item ids already stored
        self._seen_order = []       # FIFO of ids for bounded eviction
        self._target = None         # {platform, url} compose popup target, or None

    def add_many(self, video_id, raw_msgs):
        """Sanitize + append new messages from one stream; dedup by id, re-sort
        by ts (merge across streams) and cap. Returns the count added."""
        added = 0
        with self.lock:
            for raw in raw_msgs or []:
                mid = raw.get("id") if isinstance(raw, dict) else None
                if mid:
                    if mid in self._seen:
                        continue
                    self._seen.add(mid)
                    self._seen_order.append(mid)
                msg = broadcast_chat.sanitize_message(raw, source=video_id)
                if msg is None:
                    continue
                self.messages.append(msg)
                added += 1
            self.messages.sort(key=lambda m: m["ts"])
            del self.messages[: -self.cap]
            # bound the dedup set so a long event can't grow it without limit
            overflow = len(self._seen_order) - self._SEEN_CAP
            if overflow > 0:
                for old in self._seen_order[:overflow]:
                    self._seen.discard(old)
                del self._seen_order[:overflow]
        return added

    def set_target(self, target):
        with self.lock:
            self._target = target

    def data(self):
        with self.lock:
            return {"messages": list(self.messages), "target": self._target}

    def reset(self):
        with self.lock:
            self.messages = []
            self._seen.clear()
            self._seen_order.clear()
            self._target = None


class ChannelSource:
    """Reads the Sheet `Channel` tab (CSV) -> [(platform, channel)] (#294).

    Thin fetch+parse wrapper (parsing is the pure broadcast_chat.parse_channel_tab).
    A missing/empty/unreachable tab is non-fatal -- it simply yields no channels,
    so the broadcast-chat reader has nothing to follow."""

    def __init__(self, csv_url, cache_path=None):
        self.csv_url = csv_url
        self.cache_path = cache_path
        self.lock = threading.Lock()
        self.rows = []
        self.last_error = None

    def _fetch_text(self, timeout=15):
        if not self.csv_url:
            return None
        try:
            req = Request(self.csv_url, headers={"User-Agent": "racecast-feeds/1.0"})
            with urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            return None

    def refresh(self, timeout=15):
        text = self._fetch_text(timeout)
        if text is None:
            return False
        rows = broadcast_chat.parse_channel_tab(text)
        with self.lock:
            self.rows = rows
            self.last_error = None
        return True

    def get(self):
        with self.lock:
            return list(self.rows)


class ProducerSource:
    """Reads the Sheet `Producer` tab (CSV) -> [{"part","producer","magicdns",
    "stream_key"}] via producer_mod.parse_producer_rows (pure). Same tolerant
    fetch+lock+cache shape as ChannelSource; a missing/empty/unreachable tab
    yields no parts, so the panel falls back to the plain GO-LIVE button."""

    def __init__(self, csv_url, cache_path=None):
        self.csv_url = csv_url
        self.cache_path = cache_path
        self.lock = threading.Lock()
        self.rows = []
        self.last_error = None

    def _fetch_text(self, timeout=15):
        if not self.csv_url:
            return None
        try:
            req = Request(self.csv_url, headers={"User-Agent": "racecast-feeds/1.0"})
            with urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as e:                          # noqa: BLE001 — tolerant
            self.last_error = "{}: {}".format(type(e).__name__, e)
            return None

    def refresh(self, timeout=15):
        text = self._fetch_text(timeout)
        if text is None:
            return False
        rows = producer_mod.parse_producer_rows(text)
        with self.lock:
            self.rows = rows
            self.last_error = None
        return True

    def get(self):
        with self.lock:
            return list(self.rows)


class EventNotesSource:
    """Reads the Sheet `Event Notes` tab (CSV) -> [{heading, note, priority}].

    Thin fetch+parse wrapper (parsing is the pure event_notes.parse_event_notes).
    A missing/empty/unreachable tab is non-fatal -- it simply yields no notes, so
    the console modal button self-hides."""

    def __init__(self, csv_url, cache_path=None):
        self.csv_url = csv_url
        self.cache_path = cache_path
        self.lock = threading.Lock()
        self.rows = []
        self.last_error = None

    def _fetch_text(self, timeout=15):
        if not self.csv_url:
            return None
        try:
            req = Request(self.csv_url, headers={"User-Agent": "racecast-feeds/1.0"})
            with urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            return None

    def refresh(self, timeout=15):
        text = self._fetch_text(timeout)
        if text is None:
            return False
        rows = event_notes.parse_event_notes(text)
        with self.lock:
            self.rows = rows
            self.last_error = None
        return True

    def get(self):
        with self.lock:
            return list(self.rows)


class _BroadcastReader:
    """Polls one live videoId's Innertube chat into the store until the stream
    ends or it is stopped. Bootstraps from the live_chat page, then follows the
    get_live_chat continuation. `ended` is set True only on a genuine end (the
    response carried no next continuation) so the supervisor does not restart
    it; a transient bootstrap/network miss leaves ended=False (retry allowed)."""

    def __init__(self, video_id, store, logger=None):
        self.video_id = video_id
        self.store = store
        self.logger = logger
        self.stop_evt = threading.Event()
        self.ended = False
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.stop_evt.set()

    def alive(self):
        return self.thread.is_alive()

    def _run(self):
        try:
            self._poll()
        except Exception as e:  # never let a reader crash bubble into the relay
            if self.logger:
                self.logger.warning("broadcast-chat reader %s failed: %s: %s",
                                    self.video_id, type(e).__name__, e)

    def _poll(self):
        page = _yt_chat_get(broadcast_chat.live_chat_page_url(self.video_id))
        bs = broadcast_chat.parse_bootstrap(page or "")
        api_key, cont = bs["api_key"], bs["continuation"]
        if not api_key or not cont:
            return                       # not live yet / blocked: transient, no tombstone
        api_url = broadcast_chat.get_live_chat_api_url(api_key)
        client_version = bs["client_version"]
        misses = 0
        while not self.stop_evt.is_set():
            body = broadcast_chat.build_get_live_chat_body(cont, client_version)
            raw = _yt_chat_post_json(api_url, body)
            status, parsed = broadcast_chat.classify_live_chat_poll(raw)
            if status == broadcast_chat.POLL_TRANSIENT:
                # A transient HTTP/network miss is NOT a stream end (#294): retry
                # a few times with a short backoff, then give up WITHOUT setting
                # ended, so the supervisor restarts (re-bootstraps) this reader
                # next cycle instead of tombstoning it for the whole broadcast.
                misses += 1
                if misses >= broadcast_chat.MAX_POLL_MISSES:
                    return
                self.stop_evt.wait(min(2.0 * misses, 10.0))
                continue
            misses = 0
            if parsed["messages"]:
                self.store.add_many(self.video_id, parsed["messages"])
            if status == broadcast_chat.POLL_ENDED:
                self.ended = True        # genuine end (well-formed, no continuation)
                return
            cont = parsed["continuation"]
            wait_s = (parsed["timeout_ms"] or 5000) / 1000.0
            self.stop_evt.wait(min(max(wait_s, 1.0), 10.0))


# Twitch IRC (Phase 2, #294): anonymous read-only chat — no API key / OAuth, just
# a `justinfan` nick over TLS, JOIN #<login>, receive PRIVMSG. Pure stdlib.
TWITCH_IRC_HOST = "irc.chat.twitch.tv"
TWITCH_IRC_PORT = 6697


class _TwitchReader:
    """Holds one persistent anonymous Twitch-IRC connection for a channel login,
    feeding PRIVMSGs into the store. Reconnects on drop with backoff until
    stopped; `ended` stays False (a Twitch channel is always worth retrying), so
    the supervisor keeps exactly one reader per configured Twitch channel."""

    def __init__(self, login, store, logger=None):
        self.login = login
        self.store = store
        self.logger = logger
        self.stop_evt = threading.Event()
        self.ended = False
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.stop_evt.set()

    def alive(self):
        return self.thread.is_alive()

    def _run(self):
        backoff = 2
        while not self.stop_evt.is_set():
            try:
                self._session()
            except (OSError, ssl.SSLError) as e:
                if self.logger:
                    self.logger.warning("twitch chat %s: %s: %s",
                                        self.login, type(e).__name__, e)
            except Exception as e:        # never let a reader crash bubble up
                if self.logger:
                    self.logger.warning("twitch chat %s failed: %s: %s",
                                        self.login, type(e).__name__, e)
            if self.stop_evt.is_set():
                break
            self.stop_evt.wait(backoff)   # reconnect backoff
            backoff = min(backoff * 2, 30)

    @staticmethod
    def _send(sock, line):
        sock.sendall((line + "\r\n").encode("utf-8"))

    def _session(self):
        raw = socket.create_connection((TWITCH_IRC_HOST, TWITCH_IRC_PORT), timeout=10)
        try:
            ctx = ssl.create_default_context()
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2   # no legacy TLS 1.0/1.1
            sock = ctx.wrap_socket(raw, server_hostname=TWITCH_IRC_HOST)
        except OSError:
            raw.close()
            raise
        try:
            sock.settimeout(1.0)          # poll stop_evt / stay responsive to PING
            nick = "justinfan%d" % random.randint(10000, 999999)
            self._send(sock, "CAP REQ :twitch.tv/tags twitch.tv/commands")
            self._send(sock, "PASS SCHMOOPIIE")    # ignored for anon login
            self._send(sock, "NICK " + nick)
            self._send(sock, "JOIN #" + self.login)
            buf = b""
            while not self.stop_evt.is_set():
                try:
                    data = sock.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    return                # disconnected -> _run reconnects
                buf += data
                *lines, buf = buf.split(b"\n")
                for raw_line in lines:
                    line = raw_line.decode("utf-8", "replace").rstrip("\r")
                    if not line:
                        continue
                    if line.startswith("PING"):
                        self._send(sock, "PONG :tmi.twitch.tv")
                        continue
                    msg = broadcast_chat.parse_twitch_privmsg(line)
                    if msg:
                        if not msg.get("ts"):
                            msg["ts"] = time.time()   # untagged line: stamp receipt
                        self.store.add_many("twitch:" + self.login, [msg])
        finally:
            try:
                sock.close()
            except OSError:
                pass  # best-effort close; the connection is being torn down anyway


class BroadcastChatSupervisor:
    """Owns the per-channel resolution and the per-source readers (YouTube + Twitch).

    Each cycle reads the Channel tab and builds the DESIRED set of readers keyed by
    a stable id: for **YouTube**, one per currently-live videoId (resolved via
    yt-dlp — the /streams tab catches the producer-handover overlap); for
    **Twitch**, one per channel login (a persistent anonymous IRC connection,
    `twitch:<login>`). It then reconciles: readers no longer desired are stopped;
    new ones are started; a reader that died is retried — except a YouTube reader
    that ended genuinely (stream over) is tombstoned until its videoId leaves the
    live set, so it is not restarted in a loop. Best-effort throughout."""

    def __init__(self, store, channel_source, cookies, interval=30, logger=None):
        self.store = store
        self.channel_source = channel_source
        self.cookies = cookies
        self.interval = interval
        self.logger = logger
        self.stop_evt = threading.Event()
        self._wake = threading.Event()  # set by stop()/rearm() to end the cadence wait early
        self._lock = threading.Lock()   # guards _readers (the run thread + rearm callers)
        self._readers = {}              # stable key -> reader (BroadcastReader/_TwitchReader)

    def stop(self):
        self.stop_evt.set()
        self._wake.set()

    def rearm(self):
        """Manual recovery (the console 'Refresh' button, #294): clear every
        reader's tombstone so the next reconcile restarts dead/ended ones, and
        wake the loop to run that reconcile NOW instead of waiting out the ~30 s
        cadence. A healthy live reader is left in place by the reconcile.
        Best-effort and idempotent — safe to call when nothing is frozen."""
        with self._lock:
            for reader in self._readers.values():
                reader.ended = False
        self._wake.set()

    def run(self):
        while not self.stop_evt.is_set():
            try:
                self._cycle()
            except Exception as e:      # a bad cycle must not kill the supervisor
                if self.logger:
                    self.logger.warning("broadcast-chat supervisor cycle failed: %s: %s",
                                        type(e).__name__, e)
            self._wake.wait(self.interval)   # rearm()/stop() cut this short
            self._wake.clear()
        with self._lock:
            for reader in self._readers.values():
                reader.stop()

    def _desired(self):
        """{stable_key: zero-arg factory} of the readers that SHOULD be running."""
        desired = {}
        for platform, channel in self.channel_source.get():
            if platform == "youtube":
                for vid in yt_live_video_ids(channel, self.cookies, logger=self.logger):
                    desired[vid] = (lambda v=vid: _BroadcastReader(v, self.store, self.logger))
            elif platform == "twitch":
                login = broadcast_chat.twitch_login(channel)
                if login:
                    key = "twitch:" + login
                    desired[key] = (lambda lg=login: _TwitchReader(lg, self.store, self.logger))
        return desired

    def _cycle(self):
        self.channel_source.refresh()
        desired = self._desired()        # network (yt-dlp resolve); kept off the lock
        # Publish the primary compose-popup target (KISS: first live source).
        self.store.set_target(broadcast_chat.primary_chat_target(list(desired)))
        with self._lock:                 # rearm() mutates _readers from a request thread
            # Stop readers that are no longer desired (stream gone / channel removed).
            for key in list(self._readers):
                if key not in desired:
                    self._readers.pop(key).stop()
            # Start brand-new readers; retry ones that died but are NOT tombstoned.
            for key, make in desired.items():
                reader = self._readers.get(key)
                if reader is None:
                    self._readers[key] = make().start()
                    if self.logger:
                        self.logger.info("broadcast-chat: following %s", key)
                elif not reader.alive() and not reader.ended:
                    self._readers[key] = make().start()


class HealthStore:
    """Thread-safe wrapper around the SQLite health-history store. One connection
    guarded by a lock (the heartbeat thread writes; request threads read). Marks a
    tick as an 'event' when the categorical state changed since the last row."""

    def __init__(self, path, retention_days=health_store.DEFAULT_RETENTION_DAYS):
        self.path = path
        self.retention_days = retention_days
        self.lock = threading.Lock()
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        except OSError:
            pass  # best-effort: open_db below will raise if the path is truly unwritable
        self.conn = health_store.open_db(self.path)
        health_store.migrate(self.conn)
        self._last_key = None

    @staticmethod
    def _state_key(snapshot):
        return tuple(snapshot.get(f) for f in health_store.STATE_KEY_FIELDS)

    def record_tick(self, snapshot, now=None):
        key = self._state_key(snapshot)
        with self.lock:
            kind = "event" if key != self._last_key else "periodic"
            self._last_key = key
            return health_store.record(self.conn, snapshot, kind, now=now)

    def record_event(self, ts, event_type, label="", producer="", metadata=None):
        """Persist a discrete annotation (takeover, OBS stream start/stop) to the
        events table. Best-effort caller contract — guarded by the same lock."""
        with self.lock:
            return health_store.record_event(self.conn, ts, event_type, label=label,
                                              producer=producer, metadata=metadata)

    def events(self, frm, to):
        with self.lock:
            return health_store.query_events(self.conn, frm, to)

    def annotate_latest_event(self, event_type, patch):
        with self.lock:
            return health_store.annotate_latest_event(self.conn, event_type, patch)

    def query(self, frm, to):
        with self.lock:
            return health_store.query_range(self.conn, frm, to)

    def bands(self, frm, to):
        return health_store.derive_bands(self.query(frm, to))

    def incidents(self, frm, to):
        return health_store.derive_incidents(self.query(frm, to))

    def series(self, frm, to, max_points):
        return health_store.numeric_series(self.query(frm, to), max_points)

    def prune(self):
        with self.lock:
            return health_store.prune(self.conn, self.retention_days)

    def export_lines(self, frm=0):
        with self.lock:
            return health_store.export_jsonl(self.conn, frm)

    def import_lines(self, lines):
        with self.lock:
            return health_store.import_jsonl(self.conn, lines)

    def close(self):
        with self.lock:
            self.conn.close()


class CueStore:
    """Director text-cue ring buffer + best-effort JSON file
    (runtime/<profile>/cues.json), loaded + pruned on construction. Mirrors
    ChatStore. add() is the director write; ack() is the commentator write (scoped to
    the cue's target); reload() re-reads the file (takeover). ts is server clock."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        except OSError:
            pass  # best-effort: load_cues() below tolerates a missing/unwritable dir
        self.cues = cue_admin.prune(cue_admin.load_cues(self.path), time.time())

    def add(self, target, level, text, from_name=cue_admin.DEFAULT_FROM, now=None,
            origin=cue_admin.ORIGIN_DIRECTOR):
        now = time.time() if now is None else now
        with self.lock:
            nid = max([0] + [c["id"] for c in self.cues]) + 1
            entry = cue_admin.sanitize_cue({"id": nid, "ts": now, "target": target,
                                            "level": level, "text": text,
                                            "from": from_name, "ack": None,
                                            "origin": origin})
            if entry is None:
                return {"error": "cue needs target, level (info|critical) and text"}
            self.cues.append(entry)
            del self.cues[: -cue_admin.MAX_CUES]
            try:
                cue_admin.write_cues(self.path, self.cues)
            except OSError:
                pass  # best-effort: the in-memory cue still stands
            return {"ok": True, "cue": entry}

    def list(self):
        with self.lock:
            return list(self.cues)

    def data(self):
        with self.lock:
            return {"cues": list(self.cues)}

    def ack(self, cue_id, streamer_key, now=None):
        now = time.time() if now is None else now
        with self.lock:
            for c in self.cues:
                if c["id"] == cue_id:
                    if c["target"] not in (streamer_key, "all"):
                        return {"error": "not your cue"}
                    c["ack"] = {"ts": now}
                    try:
                        cue_admin.write_cues(self.path, self.cues)
                    except OSError:
                        pass  # best-effort: the in-memory ack still stands
                    return {"ok": True, "id": cue_id}
            return {"error": "no such cue"}

    def reload(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                payload = json.load(fh)
            cues = cue_admin.prune(cue_admin.validate_payload(payload), time.time())
        except (OSError, ValueError) as e:
            return {"error": f"reload failed: {type(e).__name__}: {e}"}
        with self.lock:
            self.cues = cues
        return {"ok": True, "count": len(cues)}


# HLS tags that signal server-side ad insertion (SCTE-35 splice cues or an
# ad-classed date-range). Their PRESENCE in a YouTube manifest means the source
# is stitching ads we cannot reliably strip — we warn, never skip.
_SSAI_RE = re.compile(r"#EXT-X-(?:CUE-OUT|SCTE35|DATERANGE:[^\n]*(?:CLASS=\"[^\"]*ad|SCTE35-OUT))",
                      re.IGNORECASE)


def manifest_has_ssai_markers(text):
    """True iff an HLS playlist body carries server-side-ad-insertion markers.
    Pure + best-effort: empty/None -> False."""
    return bool(text) and bool(_SSAI_RE.search(text))


def ssai_warning(hls_url, logger):
    """Fetch the resolved manifest once and, if it carries SSAI markers, return a
    short warning string for /status (else None). Best-effort: any network/parse
    failure returns None so the feed is never blocked by the probe."""
    try:
        import urllib.request
        with urllib.request.urlopen(hls_url, timeout=10) as r:   # noqa: S310 (https HLS only)
            body = r.read(65536).decode("utf-8", errors="replace")
    except Exception:
        return None   # probe is a bonus signal; never fail the resolve on it
    if manifest_has_ssai_markers(body):
        logger.warning("source manifest carries server-side ads (cannot strip)")
        return "source has server-side ads (not a clean broadcast feed)"
    return None


# --- Per-platform cookie / ad-detection helpers (used by the Feed pull loop) ---
def cookies_for(platform, cookie_dir):
    """Resolve the cookie file for a platform inside the shared cookie dir.
    YouTube prefers yt-cookies.txt and falls back to the legacy cookies.txt;
    Twitch uses twitch-cookies.txt. Returns an existing path or None (public).
    Pure (no migration side effects — see migrate_legacy_cookie)."""
    if not cookie_dir:
        return None
    if platform == "twitch":
        p = os.path.join(cookie_dir, "twitch-cookies.txt")
        return p if os.path.isfile(p) else None
    p = os.path.join(cookie_dir, "yt-cookies.txt")
    if os.path.isfile(p):
        return p
    legacy = os.path.join(cookie_dir, "cookies.txt")
    return legacy if os.path.isfile(legacy) else None

def migrate_legacy_cookie(cookie_dir):
    """Rename a legacy cookies.txt to yt-cookies.txt once, if the new name does
    not yet exist. Returns the canonical yt-cookies.txt path. Best-effort."""
    new = os.path.join(cookie_dir, "yt-cookies.txt")
    legacy = os.path.join(cookie_dir, "cookies.txt")
    if not os.path.isfile(new) and os.path.isfile(legacy):
        try:
            os.replace(legacy, new)
        except OSError:
            return legacy   # migration failed -> keep using legacy this run
    return new

def twitch_oauth_from_cookies(path):
    """Extract the Twitch `auth-token` value from a Netscape cookies file, for
    Streamlink's --twitch-api-header. Returns the token or None (public/no auth).
    Pure-ish (reads a file); any error -> None."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("#") or "\t" not in line:
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 7 and parts[5] == "auth-token" and parts[6]:
                    return parts[6]
    except OSError:
        return None
    return None


def channel_url(entry: str) -> str:
    entry = entry.strip()
    if entry.startswith("http://") or entry.startswith("https://"):
        return entry
    return f"https://www.youtube.com/channel/{entry}/live"


def ytdlp_resolve_cmd(url, cookies, fmt=YTDLP_FORMAT):
    """Argv for resolving a live URL to an HLS manifest. `--` precedes the URL so
    a value can never be parsed as a yt-dlp flag (defense in depth on top of the
    is_channel host allow-list — yt-dlp's --exec etc. would be code execution)."""
    # -g yields the HLS URL; the extra --print emits a "rcq <height> <fps>" line so the
    # relay can show the ACTUALLY-served resolution (YouTube's streamlink only reports "live").
    cmd = ["yt-dlp", "-g", "-f", fmt, "--no-warnings", "--no-playlist",
           "--extractor-args", f"youtube:player_client={YTDLP_PLAYER_CLIENTS}",
           "--print", "rcq %(height)s %(fps)s"]
    if cookies:
        cmd += ["--cookies", cookies]
    cmd += ["--", url]
    return cmd


def ytdlp_live_status_cmd(url, cookies):
    """Argv that prints only the video's live status as "rcs <live_status>" (#621). Run
    ONLY after a resolve failed with 'Requested format is not available': the flag
    --ignore-no-formats-error turns every playability reason (bot check, rate limit,
    'This live event has ended') into a warning that --no-warnings hides, so it must
    never be on the resolve itself. `--` precedes the URL, as in ytdlp_resolve_cmd."""
    cmd = ["yt-dlp", "--skip-download", "--no-warnings", "--no-playlist",
           "--ignore-no-formats-error", "--print", "rcs %(live_status)s"]
    if cookies:
        cmd += ["--cookies", cookies]
    cmd += ["--", url]
    return cmd


def streamlink_serve_cmd(target, port, platform="youtube", twitch_token=None,
                         cookies=None, user_agent=STREAMLINK_YT_UA, tier="full"):
    """Argv for serving a stream to one OBS client. YouTube gets a resolved HLS
    URL (generic plugin); Twitch gets the twitch.tv URL itself so the Twitch
    plugin handles resolution, automatic ad-filtering and low-latency. `--`
    separates the positional URL/stream so neither can be parsed as a flag.

    For YouTube the resolved manifest is re-fetched by streamlink out-of-process,
    so it must carry the same session context yt-dlp used on the resolve — a browser
    User-Agent (always) and the cookies file (when present) — or YouTube 403s a
    protected live manifest (#345). Twitch resolves in-process and gets neither."""
    base = ["streamlink", "--player-external-http", "--player-external-http-port", str(port)]
    base += serve_flags(platform, tier)
    if platform == "twitch":
        if twitch_token:
            base += ["--twitch-api-header", f"Authorization=OAuth {twitch_token}"]
        selector = quality_twitch_selector(tier)
    else:
        base += queue_deadline_args(_streamlink_help())   # version-safe: renamed in streamlink 8.1.0
        if user_agent:
            base += ["--http-header", f"User-Agent={user_agent}"]
        if cookies:
            base += ["--http-cookies-file", cookies]
        selector = "best"     # yt-dlp already resolved the capped rendition
    return base + ["--", target, selector]


def streamlink_fanout_cmd(target, platform="youtube", twitch_token=None,
                          cookies=None, user_agent=STREAMLINK_YT_UA, tier="full"):
    """Argv for the fan-out live reader: same resolution rules as
    streamlink_serve_cmd, but the sink is --stdout (the relay reads it and
    re-serves to many consumers) instead of --player-external-http. `--` guards
    the positional URL/stream."""
    base = ["streamlink", "--stdout"]
    base += serve_flags(platform, tier)
    if platform == "twitch":
        if twitch_token:
            base += ["--twitch-api-header", f"Authorization=OAuth {twitch_token}"]
        selector = quality_twitch_selector(tier)
    else:
        base += queue_deadline_args(_streamlink_help())   # version-safe: renamed in streamlink 8.1.0
        if user_agent:
            base += ["--http-header", f"User-Agent={user_agent}"]
        if cookies:
            base += ["--http-cookies-file", cookies]
        selector = "best"     # yt-dlp already resolved the capped rendition
    return base + ["--", target, selector]


# --- Local capture source (#592): ffmpeg at the same fan-out seam ---
# A `local:` stint is read from the producer machine's capture card by ffmpeg and
# written as MPEG-TS to stdout, exactly the byte contract streamlink --stdout has, so
# the ring, watchdog, prebuffer, health and preview need no local-specific path.
# MPEG-TS on purpose: a mid-stream join resynchronises, fMP4 does not (#577).
#
# The bitrate is a constant, not a knob. FANOUT_RING_BYTES is 16 MB per feed, so the
# ring's time window is set by the bitrate (~12.8 s at 10 Mbps, 5.1 s at 25, 2.6 s at
# 50), and the #533 trailing mark sits 3 s behind live. A generous local bitrate
# (tempting, the source costs nothing) would push the mark past the oldest retained
# byte and make the consumer snap continuously. 8 Mbps video measured 9.3 Mbps on the
# wire (audio + TS overhead), a window of about 14 s, still above the ~6 Mbps a remote
# feed delivers.
LOCAL_VIDEO_KBPS = 8000
LOCAL_AUDIO_KBPS = 160
LOCAL_KEYFRAME_S = 1            # a joining consumer has a picture within a second
LOCAL_DSHOW_RTBUF = "512M"      # dshow's 3 MB default drops frames at 1080p60
LOCAL_ENCODER_ARGS = {
    "nvenc": ["-c:v", "h264_nvenc", "-preset", "p4", "-tune", "ll", "-rc", "cbr"],
    "x264": ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency"],
}
LOCAL_DEVICE_BUSY = ("capture device busy or missing — close any other program "
                     "using it (OBS capture source, Elgato utility); see feed log")
LOCAL_NEEDS_FANOUT = ("local capture needs the feed fan-out — remove "
                      "RACECAST_FEED_FANOUT=0 from .env and restart the relay")


def dshow_device_name(value):
    """The DirectShow friendly name ffmpeg's `-f dshow` wants, from a RACECAST_CAPTURE
    value. OBS stores a dshow device as "<name>:<path>" with '#' -> '#22' and ':' ->
    '#3A' escaped in each half (obs-studio plugins/win-dshow/encode-dstr.hpp); a value
    without ':' is taken as a bare name. Pure."""
    v = (value or "").strip()
    if ":" in v:
        v = v.split(":", 1)[0].replace("#3A", ":").replace("#22", "#")
    return v


def local_capture_input_args(platform, video, audio=""):
    """ffmpeg input argv for the capture device on `platform` (sys.platform), or None
    when no video device is set or the platform is unknown. `video`/`audio` are the
    machine .env values (RACECAST_CAPTURE / RACECAST_CAPTURE_AUDIO). Windows accepts
    the OBS device id (see dshow_device_name); Linux takes /dev/videoN plus a
    PulseAudio source; macOS AVFoundation takes a device name or index, NOT the UID
    OBS stores, so there RACECAST_CAPTURE must hold the name. Pure."""
    video, audio = (video or "").strip(), (audio or "").strip()
    if not video:
        return None
    tq = ["-thread_queue_size", "1024"]
    if platform.startswith("win"):
        spec = "video=" + dshow_device_name(video)
        if audio:
            spec += ":audio=" + dshow_device_name(audio)
        return tq + ["-f", "dshow", "-rtbufsize", LOCAL_DSHOW_RTBUF, "-i", spec]
    if platform.startswith("linux"):
        args = tq + ["-f", "v4l2", "-i", video]
        if audio:
            args += tq + ["-f", "pulse", "-i", audio]
        return args
    if platform == "darwin":
        return tq + ["-f", "avfoundation", "-i", f"{video}:{audio or 'none'}"]
    return None


def local_capture_cmd(input_args, encoder="x264", has_audio=True):
    """Argv for the local capture reader: the device in, H.264 at the capped bitrate
    with one keyframe per LOCAL_KEYFRAME_S, AAC, MPEG-TS on stdout. Pure."""
    kbps = LOCAL_VIDEO_KBPS
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "warning"]
    cmd += list(input_args)
    cmd += LOCAL_ENCODER_ARGS[encoder]
    cmd += ["-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{2 * kbps}k",
            "-pix_fmt", "yuv420p",
            "-force_key_frames", f"expr:gte(t,n_forced*{LOCAL_KEYFRAME_S})"]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", f"{LOCAL_AUDIO_KBPS}k", "-ar", "48000"]
    else:
        cmd += ["-an"]
    return cmd + ["-f", "mpegts", "-"]


# ---- Game audio: by default the capture card's OWN audio device ----
# The card's HDMI audio is a separate device for ffmpeg, and a wrong `audio=` does not
# degrade gracefully: ffmpeg aborts the whole capture (measured on the Windows streaming
# PC: `audio=Elgato HD60 X` -> "Could not find output pin from audio only capture
# device", I/O error). So the default is read from ffmpeg's own device list, never
# guessed: the card's audio pin when it has one (a combined device, what OBS uses by
# default), else the one audio device whose name contains the video device's name —
# on that PC, video "Elgato HD60 X" + audio "Elgato HD60 X (Elgato HD60 X)". No unique
# match -> picture only, said loudly (feed log + panel) with the devices to choose
# from. RACECAST_CAPTURE_AUDIO overrides; `none` means deliberately picture-only.
LOCAL_AUDIO_NONE = "none"
_DSHOW_DEVICE_RE = re.compile(r'^\[[^\]]*\] "(.+)" \(([^()]*)\)\s*$')
_AVF_DEVICE_RE = re.compile(r'^\[[^\]]*\] \[\d+\] (.+?)\s*$')
_SOURCES_RE = re.compile(r'^[* ] (\S+) \[(.*)\] \([^()]*\)\s*$')


def parse_dshow_device_list(text):
    """[(name, pin types)] from `ffmpeg -list_devices true -f dshow -i dummy`, whose
    device lines are `"<name>" (<type>[, <type>])` (libavdevice/dshow.c). Pure."""
    out = []
    for line in (text or "").splitlines():
        mt = _DSHOW_DEVICE_RE.match(line)
        if mt:
            out.append((mt.group(1), tuple(t.strip() for t in mt.group(2).split(","))))
    return out


def parse_avfoundation_audio_devices(text):
    """Audio device names from `ffmpeg -f avfoundation -list_devices true -i ""`. Pure."""
    out, in_audio = [], False
    for line in (text or "").splitlines():
        if "AVFoundation audio devices:" in line:
            in_audio = True
        elif "AVFoundation video devices:" in line:
            in_audio = False
        elif in_audio:
            mt = _AVF_DEVICE_RE.match(line)
            if mt:
                out.append(mt.group(1))
    return out


def parse_ffmpeg_sources(text):
    """[(name, description)] from `ffmpeg -sources <fmt>`, whose lines are
    `<*| > <name> [<description>] (<types>)` (fftools/opt_common.c). Pure."""
    out = []
    for line in (text or "").splitlines():
        mt = _SOURCES_RE.match(line)
        if mt:
            out.append((mt.group(1), mt.group(2)))
    return out


def pick_capture_audio(video_name, video_has_audio, audio_devices):
    """The capture card's own audio input, or None when there is no unique match.
    `audio_devices` is [(value for ffmpeg, label to match)]. Pure."""
    video_name = (video_name or "").strip()
    if not video_name:
        return None
    if video_has_audio:
        return video_name
    want = video_name.lower()
    hits = [value for value, label in audio_devices if want in (label or "").lower()]
    return hits[0] if len(hits) == 1 else None


def _no_audio_note(video_name, labels):
    choices = ", ".join(labels) if labels else "none found"
    return (f"local capture: no game-audio device found for '{video_name}' — picture "
            f"only; set RACECAST_CAPTURE_AUDIO in .env (audio devices: {choices})")


def dshow_audio_from_listing(video, listing):
    """(audio device, None) or (None, note) for the dshow `video` (an OBS device id or
    a bare name), from a `-list_devices` listing. Pure."""
    name = dshow_device_name(video)
    devices = parse_dshow_device_list(listing)
    has_pin = any(n == name and "audio" in t for n, t in devices)
    audio = [(n, n) for n, t in devices if "audio" in t and "video" not in t]
    pick = pick_capture_audio(name, has_pin, audio)
    return (pick, None) if pick else (None, _no_audio_note(name, [n for n, _ in audio]))


def _ffmpeg_listing(args):
    """ffmpeg's device listing (stdout + stderr), "" on any failure."""
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner"] + args, capture_output=True,
                           timeout=20, env=external_tool_env(), **_no_window_kwargs())
    except Exception:                         # noqa: BLE001 — best-effort scan
        return ""
    return (r.stdout + r.stderr).decode("utf-8", "replace")


def scan_capture_audio(platform, video):
    """(audio device, None) or (None, note): the capture card's own audio input on this
    machine, found by name in ffmpeg's device list (see LOCAL_AUDIO_NONE's comment)."""
    if platform.startswith("win"):
        return dshow_audio_from_listing(
            video, _ffmpeg_listing(["-list_devices", "true", "-f", "dshow", "-i", "dummy"]))
    if platform == "darwin":
        names = parse_avfoundation_audio_devices(
            _ffmpeg_listing(["-f", "avfoundation", "-list_devices", "true", "-i", ""]))
        pick = pick_capture_audio(video, False, [(n, n) for n in names])
        return (pick, None) if pick else (None, _no_audio_note(video, names))
    if platform.startswith("linux"):
        # v4l2 names the card "<card>: <node>"; the PulseAudio description carries the card.
        try:
            with open(f"/sys/class/video4linux/{os.path.basename(video)}/name",
                      encoding="utf-8") as fh:
                card = fh.read().split(":", 1)[0].strip()
        except OSError:
            card = ""
        sources = parse_ffmpeg_sources(_ffmpeg_listing(["-sources", "pulse"]))
        pick = pick_capture_audio(card, False, sources)
        return (pick, None) if pick else (None, _no_audio_note(card or video,
                                                               [d for _n, d in sources]))
    return None, None


def local_capture_setup(environ, platform, encoder, audio_scan=None):
    """(argv, None, note) for the configured capture device, or (None, reason, None)
    when it cannot be built. `note` is a non-fatal warning (no game audio found). The
    device comes from the machine .env, never from the sheet."""
    video = (environ.get("RACECAST_CAPTURE") or "").strip()
    audio = (environ.get("RACECAST_CAPTURE_AUDIO") or "").strip()
    if not video:
        return None, "local capture: RACECAST_CAPTURE is not set in .env", None
    if local_capture_input_args(platform, video) is None:
        return None, f"local capture is not supported on {platform}", None
    note = None
    if audio.lower() == LOCAL_AUDIO_NONE:
        audio = ""
    elif not audio:
        audio, note = (audio_scan or scan_capture_audio)(platform, video)
        audio = audio or ""
    args = local_capture_input_args(platform, video, audio)
    return local_capture_cmd(args, encoder, has_audio=bool(audio)), None, note


_LOCAL_ENCODER = None


def local_encoder():
    """'nvenc' when ffmpeg can actually encode with NVENC here, else 'x264'. Listed is
    not usable (Windows ffmpeg builds list h264_nvenc without an NVIDIA GPU), so this
    runs one tiny encode with the exact encoder args, once, and caches the answer."""
    global _LOCAL_ENCODER
    if _LOCAL_ENCODER is None:
        try:
            rc = subprocess.run(
                ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
                 "-f", "lavfi", "-i", "color=c=black:s=256x144:d=0.2"]
                + LOCAL_ENCODER_ARGS["nvenc"] + ["-f", "null", "-"],
                capture_output=True, timeout=20, env=external_tool_env(),
                **_no_window_kwargs()).returncode
        except Exception:                     # noqa: BLE001 — best-effort probe
            rc = 1
        _LOCAL_ENCODER = "nvenc" if rc == 0 else "x264"
    return _LOCAL_ENCODER


# --- Director Panel off-air preview pull (decoupled from OBS / the loopback port) ---
PREVIEW_FMT_YT = "b[height<=360]/w"     # yt-dlp: pick YouTube's 360p rendition (worst fallback)
PREVIEW_QUALITY_YT = "best"             # the resolved YT URL is already the 360p rendition
PREVIEW_QUALITY_TW = "360p,480p,worst"  # Twitch named qualities (low first)
PREVIEW_STILL_WIDTH = 480               # JPEG width of a preview still


def preview_pull_streamlink_cmd(target, platform, quality,
                                cookies=None, user_agent=STREAMLINK_YT_UA):
    """Argv: stream a LOW-res copy of a feed to stdout for the preview ffmpeg.
    Decoupled from the feed's loopback port (single-consumer, held by OBS). For
    YouTube `target` is a pre-resolved 360p HLS URL and needs the same browser
    UA + cookies context as the real feed (#345); Twitch gets the twitch.tv URL
    and its plugin picks the named quality. `--` guards the positional URL."""
    cmd = ["streamlink", "--stdout"]
    if platform != "twitch":
        if user_agent:
            cmd += ["--http-header", "User-Agent=" + user_agent]
        if cookies:
            cmd += ["--http-cookies-file", cookies]
    return cmd + ["--", target, quality]


def preview_ffmpeg_cmd(width=PREVIEW_STILL_WIDTH):
    """Argv: read the streamlink pipe on stdin, emit a 1 fps scaled MJPEG on
    stdout (latest-frame source) AND run ebur128 on the audio (its per-second
    measurements print to stderr at loglevel info -> parsed for the level bar).
    Audio is optional (`0:a:0?`) so a video-only feed still yields stills."""
    return ["ffmpeg", "-nostdin", "-loglevel", "info", "-i", "pipe:0",
            "-map", "0:v:0", "-vf", "fps=1,scale=%d:-2" % width,
            "-f", "mjpeg", "pipe:1",
            "-map", "0:a:0?", "-af", "ebur128", "-f", "null", "-"]


_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"
_EBUR128_M = re.compile(r"\bM:\s*(-?\d+(?:\.\d+)?)")
PREVIEW_LUFS_FLOOR = -60.0
PREVIEW_LUFS_CEIL = -10.0


# --- On-air program-audio monitor: encode the on-air feed's audio to MP3 -----
# Codec/params live in constants so switching to AAC-ADTS (audio/aac, "-c:a aac
# -f adts") is a one-line edit. MP3 is the default for universal <audio>
# decodability (Firefox on Linux may lack system AAC codecs). Fixed sample-rate
# + channel count guarantee frame compatibility across a handover ffmpeg restart
# (both feeds encode to identical params -> the client MP3 stream splices).
PROGRAM_AUDIO_CODEC = "libmp3lame"
PROGRAM_AUDIO_BITRATE = "96k"
PROGRAM_AUDIO_FORMAT = "mp3"
PROGRAM_AUDIO_CONTENT_TYPE = "audio/mpeg"
PROGRAM_AUDIO_SAMPLE_RATE = "44100"
PROGRAM_AUDIO_CHANNELS = "1"
PROGRAM_AUDIO_RING_BYTES = 512 * 1024   # encoded MP3 is low-bitrate; a small ring is ample


def program_audio_ffmpeg_cmd():
    """Argv: read the on-air feed's MPEG-TS on stdin, drop video, encode the
    (optional) audio stream to a fixed-param MP3 on stdout for endless HTTP
    streaming. `0:a:0?` makes audio optional so a video-only feed just yields
    silence rather than an ffmpeg error."""
    return ["ffmpeg", "-nostdin", "-loglevel", "warning", "-i", "pipe:0",
            "-vn", "-map", "0:a:0?",
            "-ar", PROGRAM_AUDIO_SAMPLE_RATE, "-ac", PROGRAM_AUDIO_CHANNELS,
            "-c:a", PROGRAM_AUDIO_CODEC, "-b:a", PROGRAM_AUDIO_BITRATE,
            "-f", PROGRAM_AUDIO_FORMAT, "pipe:1"]


# #576: a Twitch feed can be fMP4/CMAF rather than MPEG-TS. TS carries its own
# resync marker (the 0x47 sync byte), so a consumer joining mid-stream recovers on
# its own — that is what makes the opaque fan-out tee work. fMP4 does not: the
# codec parameters live once, in the `moov` of the initialization segment at the
# very start of the stream, and a join lands inside an `mdat` where nothing is
# parseable. Measured against a live Twitch capture, only the init segment PLUS a
# `moof`-aligned join produced MP3 frames; either half alone produced none.
FEED_HEAD_BYTES = 64 * 1024              # ample for ftyp+moov on every CMAF stream seen
FMP4_ALIGN_SCAN_BYTES = 4 * 1024 * 1024  # search budget, then pass through raw
FMP4_MAX_BOX_BYTES = 1024 * 1024         # a moof is ~1-2 KB; this is a sanity bound
# Top-level box types a validated `moof` candidate may be followed by.
FMP4_BOX_TYPES = frozenset((b"moof", b"mdat", b"styp", b"sidx", b"prft", b"emsg",
                            b"ftyp", b"moov", b"free", b"skip", b"mfra", b"ssix"))


def _iter_boxes(d):
    """(offset, size, type) for the top-level ISO-BMFF boxes in `d`, stopping at
    the first entry that is not a well-formed box header. Only valid from a real
    box boundary — see `fmp4_fragment_start` for the mid-stream case."""
    i = 0
    while i + 8 <= len(d):
        size = int.from_bytes(d[i:i + 4], "big")
        typ = bytes(d[i + 4:i + 8])
        if size == 1:                    # 64-bit largesize
            if i + 16 > len(d):
                return
            size = int.from_bytes(d[i + 8:i + 16], "big")
        if size < 8:
            return
        yield i, size, typ
        i += size


def fmp4_init_segment(head):
    """The fMP4 initialization segment (`ftyp`…`moov`) from the retained head of a
    feed, or b"" when the head is not fMP4 — so an MPEG-TS feed keeps today's raw
    path byte for byte.

    Completes at the end of `moov` rather than waiting for the first `moof`: the
    head can be captured before a fragment has arrived, and a truncated `moov`
    is worse than none (it hands ffmpeg a broken header), so that returns b"".
    """
    head = bytes(head or b"")
    first = next(_iter_boxes(head), None)
    if not first or first[2] != b"ftyp":
        return b""
    for off, size, typ in _iter_boxes(head):
        if typ == b"moof":
            return head[:off]            # complete: everything before the fragment
        if typ == b"moov":
            end = off + size
            return head[:end] if end <= len(head) else b""
    return b""


def fmp4_fragment_start(buf, start=0):
    """Offset of the first top-level `moof` box at or after `start`, or None.

    A VALIDATED PATTERN SEARCH, not a box walk: a mid-stream join lands inside an
    `mdat`, where box sizes are meaningless, so there is no boundary to walk from.
    A candidate qualifies when the four bytes before the `moof` tag are a
    plausible box size AND the box that size points at carries a known type —
    media payload happening to contain the bytes "moof" fails that. A candidate
    that cannot be validated yet reads as "not found", so the caller keeps
    buffering instead of committing to a guess."""
    data = bytes(buf)
    i = data.find(b"moof", max(start + 4, 4))
    while i != -1:
        off = i - 4
        size = int.from_bytes(data[off:i], "big")
        if 8 < size <= FMP4_MAX_BOX_BYTES:
            nxt = off + size
            if nxt + 8 > len(data):
                return None              # not enough held to validate — read more
            if bytes(data[nxt + 4:nxt + 8]) in FMP4_BOX_TYPES:
                return off
        i = data.find(b"moof", i + 1)
    return None


def fmp4_aligned_take(pending):
    """(write_now, still_pending) while aligning a joined fMP4 stream to its first
    fragment boundary. b"" and the buffer while still searching; the aligned
    payload and None once found — or, once the search budget is spent, the buffer
    unchanged, degrading to today's raw pass-through rather than stalling.

    That give-up path leaves ffmpeg alive but silent (it accepts the unaligned
    fMP4 and emits nothing) where the unfixed code had it die and respawn every
    two seconds. Same end state — no audio — at less cost; worth knowing before
    reading "encoder up, output ring empty" as a different bug."""
    at = fmp4_fragment_start(pending)
    if at is not None:
        return bytes(pending[at:]), None
    if len(pending) >= FMP4_ALIGN_SCAN_BYTES:
        return bytes(pending), None
    return b"", pending


class Fmp4Join:
    """The #576 join repair for ONE ring consumer: the init segment to send
    first, then every read passed through `take` until the first fragment
    boundary. Built from the ring's head at join time; on an MPEG-TS head `init`
    is b"" and `take` returns its input unchanged, so a TS feed keeps today's raw
    path byte for byte. Every mid-stream consumer of a feed's ring goes
    through this: the OBS serve, the preview tap and the program-audio tap
    (#577). The program-audio MP3 output ring does not need it; MP3 resyncs."""

    def __init__(self, head):
        self.init = fmp4_init_segment(head)
        self._pending = bytearray() if self.init else None

    def take(self, data):
        if self._pending is None or not data:
            return data
        self._pending += data
        out, self._pending = fmp4_aligned_take(self._pending)
        return out


def ring_join(ring):
    """A Fmp4Join for `ring`, or a pass-through one for a ring without a head
    (test doubles)."""
    return Fmp4Join(ring.head() if hasattr(ring, "head") else b"")


def should_retarget(prev_live, cur_live, serving):
    """The program-audio encoder should re-point (restart ffmpeg on the new
    feed's ring) only when the on-air feed changed AND the new feed is actually
    serving bytes. Guards against tapping a not-yet-serving / absent feed at a
    handover (mirrors the cut=True guard in Relay.next_auto)."""
    return bool(serving) and cur_live is not None and cur_live != prev_live


def _program_audio_is_probe(path):
    """Pure: True when a program-audio GET carries ?probe=1 (an availability check
    that must return WITHOUT acquiring the listener / spinning up the encoder). Any
    other value (absent, probe=0, probe=) is a real stream request."""
    return parse_qs(urlparse(path).query).get("probe", ["0"])[0] == "1"


def _program_audio_stream_ring(handler, ring, content_type, service):
    """Write an endless byte stream from a FeedRing to an HTTP client. Shared core
    of the H._stream_ring method (module-level so it is unit-testable without a
    socket). No Content-Length: the client reads until it disconnects, which makes
    handler.wfile.write raise (caught) -> the caller's finally releases the
    listener."""
    cursor = ring.live_offset() if hasattr(ring, "live_offset") else 0
    try:
        handler.send_response(200)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Connection", "close")
        handler.end_headers()
        while not getattr(ring, "closed", False):
            data, cursor = ring.read(cursor, 1.0)
            if data:
                handler.wfile.write(data)
            service.touch()
    except (OSError, ValueError):
        pass                       # client disconnected mid-write
    return None


def _warn_if_mic_failed(note):
    """Log a failed switch of the commentary mic (#593). The next OBS probe
    overwrites obs_note within seconds and a Companion SPLIT shows no note at all,
    so the relay log is where a local stint that went out without the producer's
    commentary stays visible."""
    mic = _OBS_WS_MODULE.COMMENTARY_MIC_INPUT if _OBS_WS_MODULE else None
    if mic and note and mic in note:
        LOG.warning("OBS: could not switch %s (%s) — this OBS collection predates the "
                    "commentary mic; run `racecast setup` and re-import it", mic, note)


# --- Relay-driven Splitscreen (#534 audio, #591 visibility) -------------------
def _apply_split_intents(relay, obs_ws, audio_only):
    """Resolve the on-air feed and apply the Splitscreen intents via obs-websocket,
    one call per intent. Every intent is attempted even after one fails, so a
    collection without a Discord input still gets its feed audio right. Best-effort,
    mirrors /obs/audio: obs unreachable -> ({"error":...}, 503), no on-air feed
    (solo) -> 409; never raises."""
    if obs_ws is None:
        return {"error": "obs unavailable"}, 503
    live = relay.live_feed()
    if live not in ("A", "B"):                 # solo: no feed pair, nothing to split
        return {"ok": False, "error": "no on-air feed", "live": live}, 409
    plan = getattr(relay, "obs_audio_plan", None)
    audio, extra_mute = plan() if plan else (None, ())
    slots = ({f: (_OBS_WS_MODULE.FEED_SOURCES[f], audio[f]) for f in audio}
             if audio else None)
    intents = _OBS_WS_MODULE.split_state_intents(live, False, slots=slots,
                                                 extra_mute=extra_mute)
    if audio_only:
        intents = [(v, t) for v, t in intents if v in ("mute", "unmute")]
    payload, status = _apply_obs_intents(obs_ws, _OBS_WS_MODULE.SPLIT_SCENE, intents)
    payload["live"] = live
    if audio_only:
        payload.pop("show", None)
        payload.pop("hide", None)
    else:
        payload.pop("hide", None)                 # a Splitscreen hides nothing
    return payload, status


def _apply_obs_intents(obs_ws, scene, intents):
    """Apply show/hide/mute/unmute intents in `scene`, one obs-websocket call each.
    Every intent is attempted even after one fails, so a collection without a
    Discord input or a commentary mic still gets its feed audio right. Returns
    (payload, 200|503); a failed mic switch is also logged (_warn_if_mic_failed)."""
    ok_all, notes = True, []
    for verb, target in intents:
        if verb in ("show", "hide"):
            ok, note = obs_ws.set_scene_item_enabled(scene, target, verb == "show")
        else:
            ok, note = obs_ws.set_input_mute(target, verb == "mute")
        ok_all = ok_all and ok
        if note:
            notes.append(note)
    payload = {"ok": bool(ok_all),
               "show": [t for v, t in intents if v == "show"],
               "hide": [t for v, t in intents if v == "hide"],
               "unmute": [t for v, t in intents if v == "unmute"],
               "mute": [t for v, t in intents if v == "mute"]}
    if notes:
        payload["note"] = "; ".join(str(n) for n in notes)
        _warn_if_mic_failed(payload["note"])
    return payload, (200 if ok_all else 503)


# --- Relay-driven STINT A/B override (#593 follow-up) ---------------------------
def apply_stint_state(relay, obs_ws, feed):
    """GET /obs/stint/<A|B> (Companion) and POST /obs/stint {"feed"} (Director
    Panel): make the Stint scene show `feed` — the director's pick, not necessarily
    the relay's on-air feed — with its audio live and the rest muted, the Discord
    bus included. The audio comes from the same plan as a handover
    (relay.obs_audio_plan), so the producer's commentary mic opens only when the
    pick is the local stint and closes when the other feed is. The relay's on-air
    state is not touched, and the scene cut stays with the caller, so a STINT press
    still cuts when the relay is down. Best effort, never raises: bad feed -> 400,
    no feed pair (solo) -> 409, OBS unavailable -> 503."""
    if obs_ws is None:
        return {"error": "obs unavailable"}, 503
    feed = str(feed or "").strip().upper()
    if feed not in ("A", "B"):
        return {"ok": False, "error": "feed must be A or B"}, 400
    if getattr(relay, "solo", False):
        return {"ok": False, "error": "no feed pair"}, 409
    plan = getattr(relay, "obs_audio_plan", None)
    audio, extra_mute = plan() if plan else (None, [])
    intents = _OBS_WS_MODULE.feed_state_intents(
        feed, False, audio=audio,
        extra_mute=list(extra_mute) + [_OBS_WS_MODULE.SPLIT_DISCORD_INPUT])
    payload, status = _apply_obs_intents(obs_ws, _OBS_WS_MODULE.STINT_SCENE, intents)
    payload["feed"] = feed
    return payload, status


def apply_split_state(relay, obs_ws):
    """GET/POST /obs/split (#591): make the Splitscreen match the on-air slot —
    both slots visible, the on-air slot's audio live, the rest muted. The scene cut
    stays with the caller, so a SPLIT still cuts when the relay is down."""
    return _apply_split_intents(relay, obs_ws, audio_only=False)


def apply_split_audio(relay, obs_ws):
    """GET/POST /obs/split-audio (#534): the audio half of apply_split_state, for
    boards and panels that still set the Splitscreen visibility themselves. Its
    `unmute` stays one name, as older boards expect. For a slot with several audio
    inputs (a local slot plus its microphone) every input is still switched, but
    only the first is reported."""
    payload, status = _apply_split_intents(relay, obs_ws, audio_only=True)
    if "unmute" in payload:
        payload["unmute"] = payload["unmute"][0]   # the route has always returned one name
    return payload, status


def split_mjpeg_frames(buf):
    """Pure: pull every COMPLETE JPEG (SOI..EOI) out of an MJPEG byte buffer.
    Returns (frames, remainder); remainder is the trailing incomplete bytes to
    prepend to the next read. Leading junk before the first SOI is discarded."""
    frames = []
    while True:
        start = buf.find(_JPEG_SOI)
        if start < 0:
            return frames, b""
        end = buf.find(_JPEG_EOI, start + 2)
        if end < 0:
            return frames, buf[start:]
        frames.append(buf[start:end + 2])
        buf = buf[end + 2:]


def parse_ebur128_momentary(line):
    """Pure: the momentary loudness (LUFS) from one ffmpeg ebur128 log line, or
    None when the line carries no finite `M:` value (e.g. '-inf' on silence)."""
    mt = _EBUR128_M.search(line)
    if not mt:
        return None
    try:
        return float(mt.group(1))
    except ValueError:
        return None


def lufs_to_meter(lufs):
    """Pure: map momentary LUFS to a 0.0..1.0 bar over [floor, ceil]. None -> 0."""
    if lufs is None:
        return 0.0
    frac = (lufs - PREVIEW_LUFS_FLOOR) / (PREVIEW_LUFS_CEIL - PREVIEW_LUFS_FLOOR)
    return max(0.0, min(1.0, frac))


class _PreviewPullWorker:
    """One decoupled low-res pull of a single (off-air) feed. Produces the latest
    JPEG still + a 0..1 audio level. Best effort: a resolve/spawn failure leaves
    .ok False and the manager shows the tile 'unavailable'; nothing here can
    affect the live feed workers. `spawn` is injectable for tests."""

    def __init__(self, target, channel, cookies, log, spawn=None):
        self.target = target
        self.channel = channel
        self.cookies = cookies
        self.log = log
        self._spawn = spawn or self._spawn_real
        self._proc = None
        self._frame = None
        self._level = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.ok = True

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def latest_frame(self):
        with self._lock:
            return self._frame

    def latest_level(self):
        with self._lock:
            return self._level

    def _spawn_real(self, _worker):
        url = feed_url(self.channel)
        plat = feed_platform(url)
        if plat == "local":
            # Only reachable with fan-out off, which a local feed refuses anyway; a
            # second opener of the card would also steal it from the feed (#592).
            self.log.info("preview pull skipped for the local capture source")
            return None, None, iter(())
        if plat == "twitch":
            target, quality = url, PREVIEW_QUALITY_TW
        else:
            hls, err, _q = resolve_hls(url, self.cookies, self.log, PREVIEW_FMT_YT)
            if not hls:
                self.log.info("preview resolve failed for %s: %s", self.target, err)
                return None, None, iter(())
            target, quality = hls, PREVIEW_QUALITY_YT
        sl_cmd = preview_pull_streamlink_cmd(target, plat, quality, cookies=self.cookies)
        ff_cmd = preview_ffmpeg_cmd()
        sl = subprocess.Popen(sl_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              env=external_tool_env(), **_no_window_kwargs())
        ff = subprocess.Popen(ff_cmd, stdin=sl.stdout, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=False,
                              env=external_tool_env(), **_no_window_kwargs())
        if sl.stdout:
            sl.stdout.close()   # ffmpeg owns the pipe now
        self._sl = sl
        stderr_iter = iter(ff.stderr.readline, b"")
        return ff, ff.stdout, _decode_lines(stderr_iter)

    def _run(self):
        try:
            proc, video, stderr_iter = self._spawn(self)
        except Exception as e:                       # noqa: BLE001 best-effort
            self.ok = False
            self.log.info("preview pull %s spawn error: %s", self.target, e)
            return
        if proc is None or video is None:
            self.ok = False
            return
        self._proc = proc
        threading.Thread(target=self._pump_levels, args=(stderr_iter,), daemon=True).start()
        buf = b""
        while not self._stop.is_set():
            chunk = video.read(65536)
            if not chunk:
                break
            frames, buf = split_mjpeg_frames(buf + chunk)
            if frames:
                with self._lock:
                    self._frame = frames[-1]
        self._kill()

    def _pump_levels(self, stderr_iter):
        for line in stderr_iter:
            if self._stop.is_set():
                break
            lufs = parse_ebur128_momentary(line)
            if lufs is not None:
                with self._lock:
                    self._level = lufs_to_meter(lufs)

    def _kill(self):
        for p in (getattr(self, "_proc", None), getattr(self, "_sl", None)):
            try:
                if p and p.poll() is None:
                    p.kill()
            except Exception:                        # noqa: BLE001
                pass

    def stop(self):
        self._stop.set()
        self._kill()


def _decode_lines(byte_iter):
    """Yield ffmpeg stderr lines as str (best effort), from a bytes line iterator."""
    for raw in byte_iter:
        yield raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else raw


class _PreviewRingTap:
    """Preview worker that taps a FeedRing instead of spawning a second streamlink
    pull. One ffmpeg subprocess receives ring bytes on stdin; its stdout (MJPEG) and
    stderr (ebur128) are pumped by the same helpers as _PreviewPullWorker. The ring
    is read from its current live edge so only recently-buffered data is consumed —
    no rewinding to stream start. Best effort: any spawn/read failure leaves .ok
    False and the manager shows the tile 'unavailable'; nothing here can affect the
    live feed workers. `spawn` is injectable for tests."""

    def __init__(self, ring, target, log, spawn=None):
        self.ring = ring
        self.target = target
        self.log = log
        self._spawn = spawn or self._spawn_real
        self._proc = None
        self._frame = None
        self._level = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.ok = True

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def latest_frame(self):
        with self._lock:
            return self._frame

    def latest_level(self):
        with self._lock:
            return self._level

    def _spawn_real(self, _worker):
        ff_cmd = preview_ffmpeg_cmd()
        ff = subprocess.Popen(ff_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=False,
                              env=external_tool_env(), **_no_window_kwargs())
        self._proc = ff
        stderr_iter = _decode_lines(iter(ff.stderr.readline, b""))
        return ff, ff.stdin, ff.stdout, stderr_iter

    def _run(self):
        try:
            proc, stdin, video, stderr_iter = self._spawn(self)
        except Exception as e:                       # noqa: BLE001 best-effort
            self.ok = False
            self.log.info("preview ring tap %s spawn error: %s", self.target, e)
            return
        if proc is None or video is None:
            self.ok = False
            return
        self._proc = proc
        threading.Thread(target=self._pump_levels, args=(stderr_iter,), daemon=True).start()
        threading.Thread(target=self._feed_stdin, args=(stdin,), daemon=True).start()
        buf = b""
        while not self._stop.is_set():
            chunk = video.read(65536)
            if not chunk:
                break
            frames, buf = split_mjpeg_frames(buf + chunk)
            if frames:
                with self._lock:
                    self._frame = frames[-1]
        self._kill()

    def _feed_stdin(self, stdin):
        """Pump ring bytes into ffmpeg stdin; join at the current live edge
        (with the fMP4 init segment first, #577)."""
        cursor = self.ring.live_offset() if hasattr(self.ring, "live_offset") else 0
        join = ring_join(self.ring)
        try:
            if join.init:
                stdin.write(join.init)
                stdin.flush()
            while not self._stop.is_set() and not self.ring.closed:
                data, cursor = self.ring.read(cursor, 1.0)
                data = join.take(data)
                if data:
                    stdin.write(data)
                    stdin.flush()
        except OSError:
            pass  # ffmpeg stdin closed (process gone) — ring pump exits cleanly
        finally:
            try:
                stdin.close()
            except OSError:
                pass  # already closed

    def _pump_levels(self, stderr_iter):
        for line in stderr_iter:
            if self._stop.is_set():
                break
            lufs = parse_ebur128_momentary(line)
            if lufs is not None:
                with self._lock:
                    self._level = lufs_to_meter(lufs)

    def _kill(self):
        p = getattr(self, "_proc", None)
        try:
            if p and p.poll() is None:
                p.kill()
        except Exception:                        # noqa: BLE001
            pass

    def stop(self):
        self._stop.set()
        self._kill()


PREVIEW_FEEDS = ("A", "B", "POV")        # tiles the Director Panel can request


def preview_source(target, live, pov_active, feed_keys, fanout=False):
    """Pure: how to source a feed preview tile.

    target      'A' | 'B' | 'POV'
    live        the on-air feed ('A' | 'B') from Relay.live_feed()
    pov_active  Relay.pov_active()
    feed_keys   the configured feed keys, e.g. {'A','B'} (+'POV' when a POV feed exists)
    fanout      when True the off-air feed is read from the relay's FeedRing
                ('ring') instead of a decoupled second pull ('pull').

    Returns ('obs', source_name) | ('pull', feed_key) | ('ring', feed_key)
            | ('placeholder', reason).
    The on-air feed and the active POV are decoding in OBS, so screenshot the
    source directly. The off-air feed is NOT decoded by OBS and its loopback port
    is held single-consumer by OBS, so it needs a decoupled low-res pull (handled
    by PreviewManager). A paused POV / unconfigured feed has nothing to show."""
    if target == "POV":
        return ("obs", "Feed POV") if pov_active else ("placeholder", "pov off")
    if target in ("A", "B"):
        if target not in feed_keys:
            return ("placeholder", "feed off")
        if target == live:
            return ("obs", "Feed " + target)
        return ("ring", target) if fanout else ("pull", target)
    return ("placeholder", "unknown feed")


class FeedRing:
    """A bounded byte ring for one feed: a single live writer (the streamlink
    reader) and many readers (OBS, preview), each tracking its own absolute
    offset. The writer NEVER blocks — when a reader falls behind the retained
    window, it snaps to the oldest retained byte, so it receives the full window
    and loses only overflowed bytes. Pure stdlib; unit-testable with no real stream."""

    def __init__(self, capacity):
        self.capacity = capacity
        self._buf = bytearray()
        self._head = bytearray()       # #576: the stream's first bytes, never evicted
        self._base = 0                 # absolute offset of self._buf[0]
        self._marks = collections.deque()   # #533: (live_offset, monotonic_ts) samples, throttled+pruned
        self._cond = threading.Condition()
        self.closed = False

    def write(self, data, now=None):
        if not data:
            return
        with self._cond:
            if len(self._head) < FEED_HEAD_BYTES:
                self._head += data[:FEED_HEAD_BYTES - len(self._head)]
            self._buf += data
            overflow = len(self._buf) - self.capacity
            if overflow > 0:
                del self._buf[:overflow]
                self._base += overflow
            live = self._base + len(self._buf)
            self._record_mark(live, now)     # #533: time index for trailing joins
            self._cond.notify_all()

    def _record_mark(self, live, now):
        # Caller holds self._cond. Sample the live edge at most once per
        # MARK_MIN_INTERVAL_S, then drop marks that have scrolled out of the
        # retained window (offset <= base). Pure aside from the default clock.
        if now is None:
            now = time.monotonic()
        if not self._marks or (now - self._marks[-1][1]) >= MARK_MIN_INTERVAL_S:
            self._marks.append((live, now))
        while self._marks and self._marks[0][0] <= self._base:
            self._marks.popleft()

    def head(self):
        """The retained first bytes of the CURRENT upstream stream (#576) — the
        fMP4 initialization segment lives here and nowhere else."""
        with self._cond:
            return bytes(self._head)

    def reset_head(self):
        """Drop the retained head: the writer is starting a NEW upstream process
        (stint advance, reconnect), whose initialization segment differs. The ring
        is created once in `Relay.start()` and outlives every streamlink process,
        so without this a later consumer is handed the previous stream's codec
        parameters."""
        with self._cond:
            self._head = bytearray()

    def live_offset(self):
        with self._cond:
            return self._base + len(self._buf)

    def start_offset(self):
        with self._cond:
            return self._base

    def offset_at_age(self, age_s, now):
        """The absolute offset that was the live edge ~age_s ago (the newest mark
        with ts <= now-age_s), clamped to the retained window. When the ring holds
        less than age_s of history it returns start_offset (serve the oldest
        retained byte). Pure — now is injected."""
        with self._cond:
            live = self._base + len(self._buf)
            target = now - age_s
            chosen = self._base
            for off, ts in self._marks:
                if ts <= target:
                    chosen = off
                else:
                    break
            return min(max(self._base, chosen), live)

    def age_at_offset(self, offset, now):
        """Seconds since the byte at `offset` reached the ring: how far a consumer whose
        next unread byte is `offset` sits behind the live edge (#583). 0.0 at or past the
        live edge (a source stall is not a consumer backlog, and an empty ring has nothing
        to be behind). Every write records a mark or lands within MARK_MIN_INTERVAL_S of
        one, so a non-empty ring always has one; the None return is defensive. The byte
        arrived no later than the first mark past it; an offset in the unmarked tail uses
        the newest mark (<= MARK_MIN_INTERVAL_S early). A lapped consumer below the
        retained window reports the oldest retained byte, an undercount. Pure."""
        with self._cond:
            if offset >= self._base + len(self._buf):
                return 0.0
            if not self._marks:
                return None
            ts = self._marks[-1][1]
            for off, mts in self._marks:
                if off > offset:
                    ts = mts
                    break
            return max(0.0, now - ts)

    def trailing_offset(self, prebuffer_s, now):
        """The join offset prebuffer_s seconds behind the live edge (#533);
        the live edge itself when prebuffer_s <= 0 (today's behaviour)."""
        if prebuffer_s <= 0:
            return self.live_offset()
        return self.offset_at_age(prebuffer_s, now)

    def read(self, cursor, timeout, high=None):
        with self._cond:
            live = self._base + len(self._buf)
            limit = live if high is None else min(high, live)
            if cursor >= limit and not self.closed:
                self._cond.wait(timeout)
                live = self._base + len(self._buf)
                limit = live if high is None else min(high, live)
            if cursor < self._base:        # fell behind → snap to oldest retained byte (lose only overflowed)
                cursor = self._base
            # `limit` may be below `cursor` (e.g. a trailing `high` computed just
            # before `base` overflowed past it, #533): the empty return below guards
            # it — the slice is never reached with a negative bound. Self-corrects
            # next cycle on a fresh `high`.
            if cursor >= limit:
                return b"", cursor
            data = bytes(self._buf[cursor - self._base: limit - self._base])
            return data, limit

    def close(self):
        with self._cond:
            self.closed = True
            self._cond.notify_all()


def fanout_join_offset(ring, prebuffer_s, now):
    """Absolute cursor a BROADCAST consumer (OBS, program-audio) joins at: prebuffer_s
    seconds behind the ring's live edge (#533), or the live edge when prebuffer_s <= 0
    or the ring has no time index (direct-serve / test doubles). Pure — now injected."""
    if hasattr(ring, "trailing_offset"):
        return ring.trailing_offset(prebuffer_s, now)
    if hasattr(ring, "live_offset"):
        return ring.live_offset()
    return 0


def fanout_capped_read(ring, cursor, prebuffer_s, now=None,
                       timeout_capped=0.2, timeout_live=1.0):
    """One read for a trailing broadcast consumer (#533): when prebuffer_s > 0 and
    the ring has a time index, cap the read at the trailing high-water mark
    trailing_offset(prebuffer_s, now) -- it advances at wall-clock 1x, so a greedy
    consumer cannot outrun it and the held-back reserve is released during a source
    stall. Otherwise read to the live edge. Returns (data, new_cursor). now is
    injected for tests."""
    if prebuffer_s > 0 and hasattr(ring, "trailing_offset"):
        if now is None:
            now = time.monotonic()
        high = ring.trailing_offset(prebuffer_s, now)
        return ring.read(cursor, timeout_capped, high=high)
    return ring.read(cursor, timeout_live)


class FeedFanoutServer:
    """Serve one FeedRing to many HTTP consumers (OBS + preview) on a loopback
    port. One accept loop + one handler thread per consumer. A slow/stuck socket
    stalls only its own handler; the ring writer is never touched. Best effort:
    a handler error closes that socket and returns."""

    def __init__(self, host, port, ring, log, prebuffer_s=0.0):
        self.host = host
        self.port = port
        self.ring = ring
        self.log = log
        self.prebuffer_s = prebuffer_s        # #533: join OBS this far behind the live edge
        self._sock = None
        self._stop = False
        self._consumers = {}            # id -> {"cycle_ts", "snaps", "cursor", "floor"} (#583)
        self._consumers_lock = threading.Lock()
        self._current = None            # the one live consumer socket (see retire_previous)

    def _join_offset(self, now):
        return fanout_join_offset(self.ring, self.prebuffer_s, now)

    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self.port = self._sock.getsockname()[1]
        self._sock.listen(8)
        threading.Thread(target=self._accept_loop, daemon=True).start()
        return self

    def mark_superseded(self, now=None):
        """A new consumer arrived: note where every attached one stands.

        Windows OBS leaves the old connection open on an input rebuild, and TCP cannot see
        that. Being superseded alone condemns nobody — this port serves several consumers
        by design; `_stale` adds the part that does."""
        now = time.monotonic() if now is None else now
        with self._consumers_lock:
            for st in self._consumers.values():
                st["superseded"] = (st.get("cycle_ts"), now)

    def _stale(self, st, now, grace_s=FANOUT_STALE_GRACE_S):
        """A superseded consumer that has completed nothing since, past the grace.

        `cycle_ts` (a completed read or send), not `cursor`: the cursor only advances once
        per read cycle, and a slow consumer sits inside one for many seconds. The grace
        belongs in the judgement, not just the reaping — in the instant of the mark
        nothing has completed yet."""
        mark = st.get("superseded")
        return (mark is not None and st.get("cycle_ts") == mark[0]
                and (now - mark[1]) >= grace_s)

    def reap_superseded(self, now=None, grace_s=FANOUT_STALE_GRACE_S):
        """Wake the abandoned consumers' handlers and return how many. Heartbeat only,
        never a read path. shutdown() only: it is what unblocks a handler stuck in
        sendall, and the descriptor belongs to that handler, which closes it in its own
        finally. Closing it here could free a number the handler still writes to."""
        now = time.monotonic() if now is None else now
        doomed = []
        with self._consumers_lock:
            for st in self._consumers.values():
                if self._stale(st, now, grace_s):
                    doomed.append(st.get("conn"))
        for conn in doomed:
            if conn is None:
                continue
            # The peer stopped reading long ago, so this may fail; a raise here would take
            # out the heartbeat tick that also samples health.
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except Exception:       # noqa: BLE001 — best-effort teardown
                pass
        return len(doomed)

    def _accept_loop(self):
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return                          # socket closed by stop()
            self.mark_superseded()
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        cid = None
        try:
            conn.recv(65536)                    # consume the request line/headers
            cursor = self._join_offset(time.monotonic())   # #533: join prebuffer_s behind the live edge
            # #577: OBS rejoins mid-stream at every activation (close_when_inactive),
            # so an fMP4 feed needs its init segment and a fragment-aligned start.
            # A cursor snap later is NOT re-aligned: the demuxer is mid-mdat by its
            # own count, and the heal is the resync rebuild's fresh connection.
            join = ring_join(self.ring)
            ctype = b"video/mp4" if join.init else b"video/mp2t"
            conn.sendall(b"HTTP/1.0 200 OK\r\n"
                         b"Content-Type: " + ctype + b"\r\n"
                         b"Connection: close\r\n\r\n" + join.init)
            cid = id(threading.current_thread())
            with self._consumers_lock:
                self._consumers[cid] = {"cycle_ts": time.monotonic(), "snaps": 0,
                                        "conn": conn}
            while not self._stop and not self.ring.closed:
                # #583: every byte before `cursor` was accepted by the consumer (the
                # previous sendall returned), so this is where OBS actually is.
                self._note_cycle(cid, cursor, time.monotonic())
                prev = cursor
                data, cursor = fanout_capped_read(self.ring, cursor, self.prebuffer_s)
                skipped = snap_bytes(prev, cursor, len(data))
                data = join.take(data)
                with self._consumers_lock:
                    st = self._consumers.get(cid)
                    if st is not None:
                        st["cycle_ts"] = time.monotonic()   # read cycle completed
                        if skipped > 0:
                            st["snaps"] += 1
                if data:
                    conn.sendall(data)                       # may block if OBS is slow
                    with self._consumers_lock:
                        st = self._consumers.get(cid)
                        if st is not None:
                            st["cycle_ts"] = time.monotonic()  # send completed
        except OSError:
            pass                                # consumer went away / slow send aborted
        finally:
            if cid is not None:
                with self._consumers_lock:
                    self._consumers.pop(cid, None)
            try:
                conn.close()
            except OSError:
                pass  # already closed

    def _note_cycle(self, cid, cursor, now):
        """Record a consumer's accepted position at the start of a read cycle and fold its
        backlog into the interval floor (#583). The read that follows jumps the cursor to
        the trailing mark, so only this position shows a consumer that falls behind."""
        age = self.ring.age_at_offset(cursor, now) if hasattr(self.ring, "age_at_offset") else None
        with self._consumers_lock:
            st = self._consumers.get(cid)
            if st is not None:
                st["cursor"] = cursor
                st["floor"] = fold_backlog_floor(st.get("floor"), age)

    def consumer_backlog(self, now):
        """Seconds the worst consumer is behind the live edge right now, from its last
        accepted position (#583). Grows while a consumer is blocked in sendall. None when
        no consumer is attached or the ring has no time index. `now` is monotonic.

        A consumer that was superseded and has not moved since is left out: it is an
        abandoned socket TCP still calls ESTABLISHED (see mark_superseded), and max()
        over it reported a backlog that no longer existed for as long as the handler sat
        there. That is excluded here rather than only in reap_superseded so the number
        is right immediately, instead of after the reaper's grace."""
        with self._consumers_lock:
            cursors = [st["cursor"] for st in self._consumers.values()
                       if "cursor" in st and not self._stale(st, now)]
        if not cursors or not hasattr(self.ring, "age_at_offset"):
            return None
        ages = [a for a in (self.ring.age_at_offset(c, now) for c in cursors) if a is not None]
        return max(ages) if ages else None

    def take_backlog_floor(self, now):
        """The worst consumer's smallest backlog since the last call, then reset (#583).
        The floor is what a slow consumer pushes up; a bursty source's sawtooth above it
        is not a backlog. The live age of each accepted position counts too: a slow
        consumer's sendall can outlast a whole heartbeat, and then no read cycle samples
        the interval in which its backlog is largest. Called once per heartbeat; `now` is
        monotonic. None when no consumer is attached."""
        with self._consumers_lock:
            # Same exclusion as consumer_backlog: an abandoned socket's frozen position
            # must not become the interval floor, which is what the health reason, the
            # health-history row and the automatic shed all read.
            states = [(st.get("floor"), st.get("cursor"))
                      for st in self._consumers.values() if not self._stale(st, now)]
            for st in self._consumers.values():
                st["floor"] = None
        floors = []
        for floor, cursor in states:
            if cursor is not None and hasattr(self.ring, "age_at_offset"):
                floor = fold_backlog_floor(floor, self.ring.age_at_offset(cursor, now))
            if floor is not None:
                floors.append(floor)
        return max(floors) if floors else None

    def consumer_health(self, now):
        """Worst-case OBS-consumer health: the max send-block age (now - cycle_ts) and the
        total cursor-snaps across active consumers, which the relay records as overflow
        incidents (#582). Returns
        (max_stuck_s, total_snaps); (None, 0) when no consumer is attached. Thread-safe."""
        with self._consumers_lock:
            if not self._consumers:
                return None, 0
            max_stuck = max(now - st["cycle_ts"] for st in self._consumers.values())
            total_snaps = sum(st["snaps"] for st in self._consumers.values())
        return max_stuck, total_snaps


    def stop(self):
        self._stop = True
        self.ring.close()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass  # already closed


class PreviewManager:
    """Director Panel preview source-of-truth. Active tiles (on-air feed / active
    POV) are served from a short-TTL OBS-screenshot cache; the single off-air feed
    is served from one reference-counted _PreviewPullWorker (fan-out off) or a
    _PreviewRingTap (fan-out on). All directors poll this shared state, so cost is
    flat in viewer count. Best effort throughout."""

    def __init__(self, relay, obs_ws_get, log, obs_ttl=1.0, idle_timeout=8.0,
                 worker_factory=None, ring_factory=None):
        self.relay = relay
        self._obs_get = obs_ws_get
        self.log = log
        self.obs_ttl = obs_ttl
        self.idle_timeout = idle_timeout
        self._factory = worker_factory or (
            lambda target, channel, cookies, log: _PreviewPullWorker(
                target, channel, cookies, log).start())
        self._ring_factory = ring_factory or (
            lambda ring, target, log: _PreviewRingTap(ring, target, log).start())
        self._obs_cache = {}              # source_name -> (monotonic_ts, jpeg); not locked — CPython GIL keeps dict ops atomic, a concurrent cold-miss only causes a benign double OBS fetch
        self._pull = None                 # current _PreviewPullWorker / _PreviewRingTap or None
        self._last_touch = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def _feed_keys(self):
        keys = set(self.relay.feeds)
        if self.relay.pov:
            keys.add("POV")
        return keys

    def still(self, target):
        target = target.upper()
        fanout = getattr(self.relay, "fanout", False)
        kind, ref = preview_source(target, self.relay.live_feed(),
                                   self.relay.pov_active(), self._feed_keys(),
                                   fanout=fanout)
        if kind == "placeholder":
            return None, ref
        if kind == "obs":
            return self._obs_still(ref)
        if kind == "ring":
            return self._ring_still(target)
        return self._pull_still(target)    # kind == "pull"

    def _obs_still(self, source_name):
        now = time.monotonic()  # captured before the OBS call: TTL measured from fetch start, so a slow OBS response shortens the effective window — intentional, conservative
        hit = self._obs_cache.get(source_name)
        if hit and now - hit[0] < self.obs_ttl:
            return hit[1], ""
        obs = self._obs_get()
        if obs is None:
            return None, "obs unavailable"
        data, note = obs.get_source_screenshot(source_name, width=PREVIEW_STILL_WIDTH)
        if data is None:
            return None, note
        self._obs_cache[source_name] = (now, data)
        return data, ""

    def _pull_still(self, target):
        with self._lock:
            self._last_touch = time.monotonic()
            if self._pull is None or self._pull.target != target:
                if self._pull is not None:
                    self._pull.stop()
                ch, _ = self.relay.feeds[target].current_channel()
                if not ch:
                    self._pull = None
                    return None, "feed off"
                self._pull = self._factory(
                    target, ch, self.relay.feeds[target].cookies, self.log)
            worker = self._pull
        frame = worker.latest_frame()
        if frame is None:
            return None, ("unavailable" if not worker.ok else "starting")
        return frame, ""

    def _ring_still(self, target):
        with self._lock:
            self._last_touch = time.monotonic()
            if self._pull is None or self._pull.target != target:
                if self._pull is not None:
                    self._pull.stop()
                ring = getattr(self.relay.feeds.get(target), "ring", None)
                if ring is None:
                    self._pull = None
                    return None, "unavailable"
                self._pull = self._ring_factory(ring, target, self.log)
            worker = self._pull
        frame = worker.latest_frame()
        if frame is None:
            return None, ("unavailable" if not worker.ok else "starting")
        return frame, ""

    def levels(self):
        with self._lock:
            w = self._pull
        if w is None:
            return {}
        return {w.target: w.latest_level()}

    def run(self):
        """Idle reaper: stop the off-air pull when no one has polled it recently."""
        while not self._stop.wait(2.0):
            with self._lock:
                if (self._pull is not None
                        and time.monotonic() - self._last_touch > self.idle_timeout):
                    self._pull.stop()
                    self._pull = None

    def shutdown(self):
        self._stop.set()
        with self._lock:
            if self._pull is not None:
                self._pull.stop()
                self._pull = None


class ProgramAudioService:
    """On-demand MP3 encoder of the ON-AIR feed's audio, re-served to many HTTP
    listeners from one output FeedRing. Reference-counted: the encoder starts on
    the first listener (acquire) and a supervisor thread idle-reaps it when the
    last one leaves (release). It follows the on-air feed across handovers by
    restarting ffmpeg on the new feed's ring while keeping the SAME output ring
    (MP3 frames are self-contained -> the client stream splices, only a brief
    silence gap). Requires fan-out (the in-process feed bytes only exist then);
    acquire() returns None otherwise. Best effort throughout: a spawn/read failure
    leaves the output silent and never raises. `spawn`/`ring_factory` injectable
    for tests."""

    def __init__(self, relay, log, idle_timeout=8.0, spawn=None, ring_factory=None):
        self.relay = relay
        self.log = log
        self.idle_timeout = idle_timeout
        self._spawn = spawn or self._spawn_real
        self._ring_factory = ring_factory or (lambda: FeedRing(PROGRAM_AUDIO_RING_BYTES))
        self._out = None            # output FeedRing (encoded MP3), shared by all listeners
        self._proc = None           # current ffmpeg subprocess
        self._enc_target = None     # feed name the encoder is currently pointed at
        self._listeners = 0
        self._last_touch = 0.0
        self._running = False
        self._lock = threading.Lock()
        self._stop = threading.Event()

    # ---- listener lifecycle (called by the HTTP streaming handler) ----
    def acquire(self):
        """Register a listener and return the shared output ring to stream from,
        or None if the feature can't run (fan-out off)."""
        with self._lock:
            if not getattr(self.relay, "fanout", False):
                return None
            self._listeners += 1
            self._last_touch = time.monotonic()
            if not self._running:
                self._out = self._ring_factory()
                self._running = True
                self._stop.clear()
                threading.Thread(target=self._supervise, daemon=True).start()
            return self._out

    def release(self):
        with self._lock:
            if self._listeners > 0:
                self._listeners -= 1
            self._last_touch = time.monotonic()

    def touch(self):
        with self._lock:
            self._last_touch = time.monotonic()

    # ---- encoder supervisor ----
    def _supervise(self):
        prev = None
        while not self._stop.is_set():
            with self._lock:
                idle = (self._listeners == 0
                        and time.monotonic() - self._last_touch > self.idle_timeout)
            if idle:
                break
            prev = self._encoder_tick(prev)
            self._stop.wait(1.0)
        self._teardown()

    def _encoder_tick(self, prev_live):
        """One supervisor step: (re)spawn the encoder for the current on-air feed
        when needed. Returns the feed name now encoding (or prev_live unchanged).
        The test seam — pure of threads/sleeps."""
        live = self.relay.live_feed()
        feed = self.relay.feeds.get(live) if live else None
        ring = getattr(feed, "ring", None)
        serving = ring is not None
        dead = self._proc is not None and self._proc.poll() is not None
        if self._proc is None or dead or should_retarget(self._enc_target, live, serving):
            if serving:
                self._restart_encoder(live, ring)
                return live
        return prev_live if self._enc_target is None else self._enc_target

    def _restart_encoder(self, live, ring):
        self._kill_proc()
        try:
            proc, stdin, stdout = self._spawn()
        except Exception as e:                     # noqa: BLE001 best-effort
            self.log.info("program-audio spawn error: %s", e)
            self._proc = None
            self._enc_target = None
            return
        self._proc = proc
        self._enc_target = live
        # Pass THIS generation's proc into the pumps so a later handover
        # (which reassigns self._proc) can't blind the old pump to its own
        # process dying — each pump checks the proc it was born with.
        threading.Thread(target=self._feed_stdin, args=(stdin, ring, proc), daemon=True).start()
        threading.Thread(target=self._pump_stdout, args=(stdout, proc), daemon=True).start()

    def _spawn_real(self):
        ff = subprocess.Popen(program_audio_ffmpeg_cmd(), stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=False, env=external_tool_env(), **_no_window_kwargs())
        threading.Thread(target=self._pump_stderr, args=(ff.stderr,), daemon=True).start()
        return ff, ff.stdin, ff.stdout

    def _join_offset(self, ring, now):
        return fanout_join_offset(ring, getattr(self.relay, "feed_prebuffer_s", 0.0), now)

    def _feed_stdin(self, stdin, ring, proc):
        """Pump on-air ring bytes into THIS encoder generation's ffmpeg stdin;
        join feed_prebuffer_s behind the live edge to match the broadcast (#533).
        *proc* is the process this thread owns — checked instead of the shared
        self._proc, which a handover reassigns to the NEXT generation."""
        cursor = self._join_offset(ring, time.monotonic())   # #533: match OBS's trailing join
        # #576: on an fMP4/CMAF feed the codec parameters exist only in the init
        # segment at the head of the stream, and the join lands inside an mdat.
        join = ring_join(ring)
        try:
            if join.init:
                stdin.write(join.init); stdin.flush()
            while not self._stop.is_set() and not getattr(ring, "closed", False):
                data, cursor = fanout_capped_read(
                    ring, cursor, getattr(self.relay, "feed_prebuffer_s", 0.0))
                data = join.take(data)
                if data:
                    stdin.write(data); stdin.flush()
                if proc is None or proc.poll() is not None:
                    break                          # THIS encoder gone (killed on handover)
        except OSError:
            pass                                   # ffmpeg stdin closed
        finally:
            try:
                stdin.close()
            except OSError:
                pass                               # already closed by the OS side

    def _pump_stdout(self, stdout, proc):
        """Pump encoded MP3 bytes from THIS generation's ffmpeg stdout into the
        shared output ring. *proc* is this thread's own process (see
        _feed_stdin); a stdout EOF on kill ends the loop, and the proc check is
        the belt-and-suspenders exit for a ring that stalls without EOF."""
        out = self._out
        try:
            while not self._stop.is_set():
                chunk = stdout.read(65536)
                if not chunk:
                    break                          # encoder EOF (killed / died)
                if out is not None:
                    out.write(chunk)
                if proc is None or proc.poll() is not None:
                    break                          # THIS encoder gone (killed on handover)
        except OSError:
            pass                                   # ffmpeg stdout closed (process killed)

    def _pump_stderr(self, stderr):
        for line in _decode_lines(iter(stderr.readline, b"")):
            if self._stop.is_set():
                break
            line = line.strip()
            if line:
                self.log.info("[program-audio ffmpeg] %s", line)

    def _kill_proc(self):
        p = self._proc
        try:
            if p and p.poll() is None:
                p.kill()
        except Exception:                          # noqa: BLE001
            pass

    def _teardown(self):
        # Always stop THIS (reaped) generation's encoder first, outside the lock
        # (kill must never block while holding it). The re-armed supervisor will
        # spawn a fresh one on its next _encoder_tick.
        self._kill_proc()
        self._proc = None
        self._enc_target = None
        with self._lock:
            # TOCTOU guard: a listener can slip in via acquire() between the
            # supervisor's idle check and here (it saw _running True, so it did
            # NOT start a supervisor and got the still-live self._out). If so,
            # DON'T finalize — keep the output ring and re-arm a fresh
            # supervisor so that listener keeps a live encoder. Only a genuine
            # shutdown (self._stop set) or a truly idle service finalizes.
            if self._listeners > 0 and not self._stop.is_set():
                self._running = True
                threading.Thread(target=self._supervise, daemon=True).start()
                return
            self._running = False
            if self._out is not None:
                try:
                    self._out.close()
                except Exception:                  # noqa: BLE001
                    pass
                self._out = None

    def shutdown(self):
        self._stop.set()
        self._teardown()


def resolve_hls(url, cookies, logger, fmt=YTDLP_FORMAT):
    """Resolve a YouTube live URL to a direct HLS manifest URL via yt-dlp (handles cookies +
    the bot-check). Returns (url, None, quality) on success — `quality` is the served
    resolution like '1080p60' (from yt-dlp's --print, None when unknown) — or
    (None, error_line, None). The error line feeds /status so the panel can show WHY a feed is
    stuck connecting (previously it only landed in feed_X.log)."""
    cmd = ytdlp_resolve_cmd(url, cookies, fmt)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                           timeout=90, env=external_tool_env(), **_no_window_kwargs())
    except FileNotFoundError:
        # Startup checks for yt-dlp; reaching here means it vanished mid-run.
        logger.error("yt-dlp not found on PATH")
        return None, "yt-dlp not found on PATH", None
    except subprocess.TimeoutExpired:
        return None, "yt-dlp timed out (90 s)", None
    out = [l for l in (r.stdout or "").splitlines() if l.startswith("http")]
    if out:
        return out[0], None, parse_ytdlp_quality(r.stdout)
    err = (r.stderr or "").strip().splitlines()
    last = err[-1] if err else "not live?"
    if YTDLP_NO_FORMAT.lower() in last.lower():
        # An ended broadcast (Post-Live Manifestless) and a logged-out jar both end
        # here; the live status tells them apart, and classify_source_state reads it
        # from this text (#621).
        status = ytdlp_live_status(url, cookies)
        if status not in (None, "NA"):
            last = f"{last} (live_status {status})"
        # A live (or unknown) video with a jar that holds no login: yt-dlp fell back to
        # logged-out clients with no muxed rendition. Name the fix, not just the format
        # (#615). An ended or upcoming source is already explained by its status.
        if classify_source_state(last) is None and cookie_jar.jar_has_login(cookies) is False:
            last = f"{last} — {cookie_jar.LOGGED_OUT_HINT}"
    logger.warning("yt-dlp could not resolve %s (%s)", url, last)
    return None, last, None


def cookie_login_warning(cookies):
    """The startup WARNING for a YouTube jar that holds no login, else None (no jar, no
    file, or logged in). Same rule as `racecast preflight` (cookie_jar, #615)."""
    if cookie_jar.jar_has_login(cookies) is False:
        return f"Cookies: {cookie_jar.LOGGED_OUT_HINT} ({cookies})"
    return None


def ytdlp_live_status(url, cookies):
    """The video's yt-dlp live status ('is_live', 'post_live', 'was_live', 'is_upcoming',
    'NA'), or None when the call fails. Best-effort: a failure leaves the resolve error as
    it was (#621)."""
    try:
        r = subprocess.run(ytdlp_live_status_cmd(url, cookies), capture_output=True,
                           text=True, errors="replace", timeout=30,
                           env=external_tool_env(), **_no_window_kwargs())
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_ytdlp_live_status(r.stdout)


def stint_start_indices(stint, schedule_len):
    """0-based (A, B) start indices for a producer takeover: 1-based stint
    <stint> is on air NOW -> Feed A serves it, Feed B preloads the NEXT slot.
    A is clamped to a real stint; B is always A+1 and may point past the end
    (an empty/missing slot) — the off-air feed then idles (black) until that
    stint's link appears, instead of duplicating A's stream."""
    stint = max(1, int(stint))
    hi = max(0, schedule_len - 1)
    a = min(stint - 1, hi)
    return a, a + 1


SUBSTITUTION_REASON_MAX = 200


def is_substitution(served_url, served_idx, new_url, new_idx):
    """True when the on-air feed swaps to a DIFFERENT non-empty URL at the SAME
    stint index (an operator reload after editing the on-air URL) — an ad-hoc
    stream substitution. A same-URL reconnect or a stint change is not one. Pure."""
    return (bool(new_url) and bool(served_url)
            and new_idx == served_idx and new_url != served_url)


def sanitize_reason(text):
    """Clean a free-text substitution reason: non-str -> ''; strip control chars,
    collapse all whitespace to single spaces, trim, cap at SUBSTITUTION_REASON_MAX.
    Rendered via textContent client-side, but sanitized here too (defense in depth).
    Pure."""
    if not isinstance(text, str):
        return ""
    kept = "".join(ch if ch >= " " else " " for ch in text)   # \n\t and other controls -> space
    return " ".join(kept.split())[:SUBSTITUTION_REASON_MAX].strip()


def _sheet_tab_csv_url(sheet_id, tab):
    return (f"https://docs.google.com/spreadsheets/d/{sheet_id}"
            f"/gviz/tq?tqx=out:csv&sheet={quote(tab)}")


def pov_schedule_source(sheet_id, tab, runtime):
    """The POV tab's source. Never `local:` (#592): a POV pull must not take the
    capture card from an on-air A/B feed."""
    return ScheduleSource(_sheet_tab_csv_url(sheet_id, tab),
                          os.path.join(runtime, "pov.cache.txt"), None, allow_local=False)


def qualifying_schedule_source(sheet_id, tab, runtime):
    """The Qualifying tab's source: the Schedule's structure, `local:` allowed."""
    return ScheduleSource(_sheet_tab_csv_url(sheet_id, tab),
                          os.path.join(runtime, "qualifying.cache.txt"), None)


def pull_slots(rows):
    """Slot id per row: maximal runs of CONSECUTIVE rows with the same non-empty
    URL share one slot, so a single feed pull serves the whole run — a commentator
    keeping one stream across back-to-back stints. A blank/empty URL never merges
    (you cannot 'continue' a stream that has no link), so each blank row is its own
    slot and a blank breaks a run. *rows* are ScheduleSource 4-tuples
    (url, streamer, stint, line); returns a list parallel to rows. Pure."""
    slots = []
    prev_url = None
    sid = -1
    for r in rows:
        url = (r[0] or "").strip()
        if url and url == prev_url:
            slots.append(sid)                 # continuation of the current run
        else:
            sid += 1
            slots.append(sid)
        prev_url = url or None                 # a blank breaks the run
    return slots


def slot_first_row(slots, sid):
    """First row index belonging to slot *sid*, or None when absent. Pure."""
    for i, s in enumerate(slots):
        if s == sid:
            return i
    return None


def next_slot_first_row(slots, row):
    """First row of the slot AFTER the slot containing *row*, or len(slots) (the
    idle sentinel, one past the last row) when there is none. This is where the
    off-air feed preloads and a freed feed advances, so it always skips a same-URL
    continuation run instead of landing a second feed on it. Pure."""
    if not slots:
        return 0
    row = max(0, min(row, len(slots) - 1))
    cur = slots[row]
    for i in range(row + 1, len(slots)):
        if slots[i] != cur:
            return i
    return len(slots)                          # no later slot -> idle past the end


def is_continuation(slots, row):
    """True when the Next onto 0-based *row* stays within the same slot as row-1
    (a same-URL back-to-back) -> a label-only advance: no re-pull, no OBS cut.
    Pure."""
    return 1 <= row < len(slots) and slots[row] == slots[row - 1]


def dedupe_pull_index(target_idx, other_idx, rows):
    """Collision-free pull index for a feed that wants to sit at 0-based
    *target_idx* while the OTHER feed sits at *other_idx*. Enforces the
    single-pull invariant (#491): a feed must never pull the identical non-empty
    URL the other feed already holds.

    - Empty/idle target (idx past the end or a blank URL), or a target URL that
      differs from the other feed's URL -> (target_idx unchanged, False).
    - Same non-empty URL -> advance to next_slot_first_row, the first row of the
      next DISTINCT slot; loop-until-safe so a non-contiguous later slot repeating
      the other feed's URL is skipped too, ending at the idle sentinel
      (idx == len(rows)) when nothing collision-free remains. Returns
      (idx, True). Pure."""
    n = len(rows)
    ti = max(0, min(int(target_idx), n))   # n == the idle sentinel (one past end)

    def url(i):
        return (rows[i][0] or "").strip() if 0 <= i < n else ""

    other_url = url(other_idx)
    if ti >= n or not url(ti) or url(ti) != other_url:
        return ti, False
    slots = pull_slots(rows)
    idx = ti
    while idx < n and url(idx) and url(idx) == other_url:
        idx = next_slot_first_row(slots, idx)   # strictly increases -> terminates
    return idx, True


def slot_start_indices(stint, rows):
    """Slot-aware producer-takeover placement. 1-based *stint* is on air NOW: Feed
    A pulls the HEAD of that stint's slot (so a takeover onto the second row of a
    back-to-back pulls the single stream once, not a mid-slot offset), Feed B
    preloads the head of the NEXT slot. Returns (a_idx, b_idx). For a normal
    schedule (each row its own slot) this equals stint_start_indices. *rows* are
    ScheduleSource 4-tuples. Pure; falls back to stint_start_indices on an empty
    schedule."""
    n = len(rows)
    if n == 0:
        return stint_start_indices(stint, 0)
    stint = max(1, int(stint))
    row = min(stint - 1, n - 1)
    slots = pull_slots(rows)
    a = slot_first_row(slots, slots[row])
    b = next_slot_first_row(slots, row)
    return a, b


def live_schedule_row(rows, live_idx):
    """The {"streamer", "stint"} for the schedule row a feed at 0-based
    *live_idx* is serving, or None when the index has no row (the feed idles
    past the end). Pure so the handover auto-write is unit-testable without a
    running relay (like stint_start_indices). *rows* are ScheduleSource
    4-tuples (url, streamer, stint, line), parallel to the items list a feed
    indexes into."""
    if live_idx is None or not (0 <= live_idx < len(rows)):
        return None
    _url, streamer, stint, _line = rows[live_idx]
    return {"streamer": streamer, "stint": stint}


def cockpit_tally(rows, live_idx, me_key):
    """Tally for a commentator identified by *me_key* (a streamer_key).
    Pure — unit-testable without a running relay. *rows* are ScheduleSource
    4-tuples (url, streamer, stint, line); *live_idx* is the on-air feed's index.
      on_air   = the on-air row's streamer normalizes to me_key
      up_next  = the nearest FUTURE row that is me ->
                 {"stint": <stint label>, "in_n": <handovers away>}, else None
      scheduled= me appears anywhere in the schedule"""
    cur = live_schedule_row(rows, live_idx)
    on_air = bool(cur and asset_key(cur["streamer"]) == me_key)
    up_next = None
    if live_idx is not None:
        for j in range(live_idx + 1, len(rows)):
            _url, streamer, stint, _line = rows[j]
            if asset_key(streamer) == me_key:
                up_next = {"stint": stint, "in_n": j - live_idx}
                break
    scheduled = any(asset_key(r[1]) == me_key for r in rows)
    return {"on_air": on_air, "up_next": up_next, "scheduled": scheduled}


def race_control_schedule(rows, live_map):
    """Redacted schedule for the Race Control monitoring desk (#244): stint +
    streamer + live-feed marker per row, with NO stream URL. The redaction is the
    same boundary as /console/takeover/status — feed URLs never leave the tailnet,
    and this desk is reachable over the public Funnel. Pure for unit testing.
    *rows* are ScheduleSource 4-tuples (url, streamer, stint, line); *live_map*
    maps a 0-based row index -> the feed key (A/B) currently serving it."""
    return [{"stint": st, "streamer": n, "live": live_map.get(i)}
            for i, (_u, n, st, _l) in enumerate(rows)]


def cockpit_schedule(rows, live_idx, me_key):
    """Redacted stint plan for the cockpit's read-only stint-plan card (right
    column, below the timer). Per row: stint + streamer, plus an on_air flag
    (row == live_idx) and a mine flag (asset_key match for the token's streamer).
    NO stream URL — the cockpit is reachable over the public Funnel, so this stays
    inside the /console/takeover/status redaction boundary. rows are
    ScheduleSource 4-tuples (url, streamer, stint, line). Pure."""
    return [{"stint": st, "streamer": n,
             "on_air": i == live_idx,
             "mine": asset_key(n) == me_key}
            for i, (_u, n, st, _l) in enumerate(rows)]


def redact_console_status(full, roles):
    """Redact the full /status for the Funnel-exposed /console mount, by role (#493).
    Feed stream URLs (feeds[*].channel), the POV stream URL, and the Sheet id are
    director/producer-only — the same boundary as /schedule/data, which already exposes
    per-stint URLs to directors (the panel's Preview button + POV editor consume them
    over Funnel). They are KEPT for director/producer and stripped for every other role.
    The tailnet /status is unaffected (served verbatim). Pure → unit-tested."""
    out = dict(full)
    keep_urls = bool({"director", "producer"} & set(roles or ()))
    out["feeds"] = {
        k: {kk: vv for kk, vv in (fd or {}).items() if keep_urls or kk != "channel"}
        for k, fd in (full.get("feeds") or {}).items()}
    if not keep_urls:
        pov = full.get("pov")
        if isinstance(pov, dict):
            out["pov"] = {k: v for k, v in pov.items() if k != "url"}
        lg = full.get("league")
        if isinstance(lg, dict):
            out["league"] = {k: v for k, v in lg.items() if k != "sheet_id"}
    return out


def cockpit_program_behind(backlogged):
    """Whole seconds the program runs behind live for the cockpit banner (#583), or None
    when no consumed feed is past the threshold. The commentator hears a delayed program
    against their own voice; the banner says the delay is on the producer side. Pure."""
    if not backlogged:
        return None
    return int(round(max(backlogged.values())))


def cockpit_syncing(desync):
    """True when the relay's desync block is active — the cockpit should show
    'syncing…' instead of the (index-derived, possibly wrong) ON-AIR tally. Pure."""
    return bool(desync.get("active"))


def ping_pong_desynced(live_serving, off_serving):
    """True when the index-designated on-air feed is NOT delivering a stable
    picture while the OFF-air feed IS — the feed on screen and the feed derived as
    on-air disagree. Pure; the caller supplies each feed's 'serving a stable
    picture' boolean and applies the settle debounce. False whenever the on-air
    feed is fine, or the off-air feed is not itself delivering (a plain on-air
    drop with nothing better to show is a health condition, not a desync)."""
    return (not live_serving) and off_serving


def desync_settled(raw, since_ts, now, settle_s):
    """Debounce the raw desync condition: it becomes ACTIVE only after it has held
    for *settle_s* seconds (so a quick reconnect blip never raises it). Returns
    (active, since_ts): the running start-timestamp is preserved across ticks while
    raw holds and cleared to None as soon as it ends. Pure."""
    if not raw:
        return False, None
    if since_ts is None:
        since_ts = now
    return (now - since_ts) >= settle_s, since_ts


def cockpit_display_name(rows, me_key):
    """The display streamer name whose asset_key == me_key (first match), so chat
    messages are attributed to a human-readable name. Falls back to me_key."""
    for _url, streamer, _stint, _line in rows:
        if asset_key(streamer) == me_key:
            return streamer
    return me_key


def cockpit_own_stints(rows, me_key):
    """The schedule rows belonging to *me_key* (asset_key match) as a list of
    {"row": <1-based schedule index>, "stint": <label>, "has_link": bool, "url": str},
    in schedule order. Feeds the cockpit's stint picker so a commentator submits a
    link against one of THEIR OWN stints (never a free-form/foreign row), and lets
    the cockpit pre-fill the field with that stint's current link. ONLY the
    commentator's own rows are included, so /cockpit/data exposes a commentator's
    own URLs but never anyone else's (issue #193). Pure."""
    out = []
    for i, (url, streamer, stint, _line) in enumerate(rows):
        if asset_key(streamer) == me_key:
            u = (url or "").strip()
            out.append({"row": i + 1, "stint": stint, "has_link": bool(u), "url": u})
    return out


def own_submission_target(rows, me_key, stint=None, row=None):
    """Resolve the schedule row a commentator (*me_key*) may write via a cockpit
    submission. Returns (True, {target_line, target_stint, streamer_name,
    prev_url}) on success, else (False, error_message). Pure — unit-tested.

    Ownership is enforced server-side: the target row's streamer must normalize
    to *me_key* (set-or-replace ANY of their own rows, per the approved design),
    so a leaked token can only ever touch that person's own slots. Selection is
    by *stint* label (preferred) or 1-based schedule *row* index; with neither,
    a sole own row is used and ambiguity is rejected. *rows* are ScheduleSource
    4-tuples (url, streamer, stint, line)."""
    own = [(i, r) for i, r in enumerate(rows) if asset_key(r[1]) == me_key]
    if not own:
        return False, "no schedule rows are assigned to you"
    chosen = None
    if row is not None:
        try:
            idx = int(row) - 1
        except (TypeError, ValueError):
            return False, "row must be a whole number (1-based)"
        if not (0 <= idx < len(rows)):
            return False, "no such schedule row"
        if asset_key(rows[idx][1]) != me_key:
            return False, "that schedule row is not assigned to you"
        chosen = rows[idx]
    elif stint is not None:
        matches = [r for _i, r in own if r[2] == stint]
        if not matches:
            return False, f"no stint {stint!r} is assigned to you"
        chosen = matches[0]
    elif len(own) == 1:
        chosen = own[0][1]
    else:
        return False, "specify which stint to submit for"
    url, streamer, st, line = chosen
    return True, {"target_line": line, "target_stint": st,
                  "streamer_name": streamer, "prev_url": (url or "").strip()}


def _event_footer(event_title, suffix=""):
    """Discord embed footer text combining the optional event title (#207) and an
    optional suffix (e.g. a pending count). Returns the joined text, or "" when both
    are empty (caller then omits the footer)."""
    parts = [p for p in (event_title, suffix) if p]
    return " · ".join(parts)


def cockpit_submission_payload(entry, pending_count, event_title=""):
    """Discord webhook JSON announcing a new pending stream submission (issue
    #193), mirroring discord_health_payload: the @here mention sits in top-level
    `content` (Discord ignores mentions inside an embed). Pure → unit-tested; the
    caller no-ops when no webhook is configured. A non-empty event_title (#207) is
    prefixed onto the footer (which already carries the pending count)."""
    desc = (f"**{entry['streamer_name']}** proposed a stream link for stint "
            f"**{entry['target_stint']}**.\n{entry['proposed_url']}\n\n"
            f"Approve or reject it in the Director Panel · Pending stream submissions.")
    return {"username": "GT Racecast",
            "content": "@here",
            "allowed_mentions": {"parse": ["everyone"]},
            "embeds": [{"title": "📥 New stream-link submission",
                        "description": desc, "color": 0x1D4ED8,
                        "footer": {"text": _event_footer(event_title,
                                                         f"{pending_count} pending")}}]}


def cockpit_approval_payload(entry, event_title=""):
    """Discord webhook JSON announcing that the director APPROVED a stream-link
    submission (follow-up to #193). Deliberately carries NO @here ping — it is a
    heads-up that the link is now scheduled, not a call to action — so there is no
    top-level `content` and mentions are suppressed. Pure → unit-tested; the caller
    no-ops when no webhook is configured. A non-empty event_title (#207) is shown as
    the embed footer; empty -> no footer (unchanged)."""
    desc = (f"**{entry['streamer_name']}**'s stream link for stint "
            f"**{entry['target_stint']}** was approved by the director.\n"
            f"{entry['proposed_url']}")
    embed = {"title": "✅ Stream link approved",
             "description": desc, "color": 0x16A34A}
    if event_title:
        embed["footer"] = {"text": event_title}
    return {"username": "GT Racecast",
            "allowed_mentions": {"parse": []},
            "embeds": [embed]}


class SubmissionStore:
    """Thread-safe wrapper around cockpit_submissions (issue #193): serializes the
    read-modify-write of the pending JSON across request threads, holds the file
    paths, and emits the audit log. The pure logic + atomic writes live in
    src/scripts/cockpit_submissions.py (unit-tested directly); this is the thin
    relay-side adapter, mirroring how ChatStore wraps chat_admin."""
    def __init__(self, path, audit_path=None):
        self.path = path
        self.audit_path = audit_path
        self._lock = threading.Lock()

    def add(self, **kw):
        with self._lock:
            entry = cockpit_submissions.add_pending(self.path, **kw)
        self._audit("submit", entry)
        return entry

    def list(self):
        return cockpit_submissions.list_pending(self.path)

    def get(self, entry_id):
        for e in self.list():
            if e["id"] == entry_id:
                return e
        return None

    def pop(self, entry_id, event):
        with self._lock:
            entry = cockpit_submissions.pop_pending(self.path, entry_id)
        if entry is not None:
            self._audit(event, entry)
        return entry

    def _audit(self, event, entry):
        if self.audit_path:
            cockpit_submissions.append_audit(
                self.audit_path, {"event": event, **entry})


class ScheduleSource:
    """Reads the schedule from the Google Sheet (CSV) with last-good + fallback."""
    def __init__(self, csv_url, cache_path, local_fallback, allow_local=True):
        self.csv_url = csv_url
        self.cache_path = cache_path
        self.local_fallback = local_fallback
        # #592: Schedule/Qualifying may name the capture card (`local:`); the POV tab
        # may not — a POV pull must never take the card from an on-air A/B feed.
        self.allow_local = allow_local
        self.lock = threading.Lock()
        self.items = []
        self.rows = []
        self.last_ok = None
        self.last_error = None

    @staticmethod
    def _parse_rows(text, allow_local=True):
        """CSV -> [(url, name, stint, line)] rows where *line* is the 1-based CSV
        line index of each accepted row (== physical sheet row when the Schedule
        tab starts at sheet row 1 with no leading blank rows — gviz export maps
        1:1).  Any row whose URL cell fails is_channel() (including a header row)
        is silently skipped; their line numbers are NOT remapped.

        Two layouts are supported:
        - **Header mode** (opt-in): if row 1 carries a recognized `URL` header,
          the URL/Streamer/Stint columns are located by header text (so they may
          move and the per-stint *stint* label is read). A row counts as a
          (planned) stint when it has a channel URL OR a Stint label OR a
          Streamer — a pre-planned stint whose URL is still blank is kept with an
          empty URL so the panel shows all stints and the feed idles on it until
          the URL is filled (issue #137). A non-channel URL is treated as
          not-yet-filled (url -> "") so the feed never serves junk.
        - **Positional fallback** (no header row): the URL column is auto-detected
          (most cells matching `accept`) and the streamer is the cell right of
          it; no stint label exists in this layout (URL-bearing rows only).
        `local:` counts as a URL only with allow_local (#592)."""
        accept = is_feed_source if allow_local else is_channel
        rows = list(csv.reader(io.StringIO(text)))
        if not rows:
            return None
        header = [(h or "").strip().lower() for h in rows[0]]
        url_i = next((header.index(h) for h in SCHEDULE_URL_HEADERS if h in header), None)
        if url_i is not None:
            name_i = next((header.index(h) for h in SCHEDULE_STREAMER_HEADERS if h in header), None)
            stint_i = next((header.index(h) for h in SCHEDULE_STINT_HEADERS if h in header), None)
            out = []
            for line, r in enumerate(rows, 1):
                if line == 1:
                    continue                       # the header row itself
                url = feed_source_value(r[url_i]) if len(r) > url_i else ""
                name = r[name_i].strip() if name_i is not None and len(r) > name_i else ""
                stint = r[stint_i].strip() if stint_i is not None and len(r) > stint_i else ""
                if not accept(url):
                    if not (name or stint):
                        continue                   # blank/spacer row -> not a stint
                    url = ""                        # planned stint, URL not yet provided
                out.append((url, name, stint, line))
            return out or None
        # Positional fallback: detect the URL column, streamer is the cell to its
        # right, no stint label.
        ncols = max((len(r) for r in rows), default=0)
        best_col, best_cnt = None, 0
        for c in range(ncols):
            cnt = sum(1 for r in rows if len(r) > c and accept(r[c]))
            if cnt > best_cnt:
                best_cnt, best_col = cnt, c
        if best_col is None or best_cnt == 0:
            return None
        out = [(feed_source_value(r[best_col]),
                (r[best_col + 1].strip() if len(r) > best_col + 1 else ""),
                "",
                line)
               for line, r in enumerate(rows, 1)
               if len(r) > best_col and accept(r[best_col])]
        return out or None

    @staticmethod
    def _parse_csv(text):
        """URL-only wrapper around _parse_rows; kept for the URL-list callers/tests."""
        rows = ScheduleSource._parse_rows(text)
        return [u for u, _n, _s, _l in rows] if rows else None

    def fetch(self, timeout=15):
        if not self.csv_url:
            return None
        try:
            req = Request(self.csv_url, headers={"User-Agent": "racecast-feeds/1.0"})
            with urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", "replace")
            rows = self._parse_rows(text, self.allow_local)
            if not rows:
                self.last_error = ("Sheet reachable, but no channel IDs found "
                                   "(correct tab name? a column with UC… IDs / watch URLs? sharing?)")
                return None
            return rows
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            return None

    def refresh(self, timeout=15):
        rows = self.fetch(timeout)
        if rows:
            with self.lock:
                self.rows = rows
                self.items = [u for u, _n, _s, _l in rows]
                self.last_ok = time.time()
                self.last_error = None
            try:
                with open(self.cache_path, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(u for u, _n, _s, _l in rows) + "\n")
            except Exception:
                pass  # cache write is best-effort; the in-memory schedule is current
            return True
        return False

    def load_initial(self, template=None):
        if self.refresh():
            LOG.info("Schedule loaded from Google Sheet: %d stints.", len(self.items))
            return
        # Sheet unreachable -> cache, then a user-filled local fallback
        for path, label in ((self.cache_path, "cache"), (self.local_fallback, "local schedule.txt")):
            if path and os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    items = [l.split("#", 1)[0].strip() for l in fh]
                items = [i for i in items if i]
                if items:
                    with self.lock:
                        self.items = items
                        self.rows = [(u, "", "", i + 1) for i, u in enumerate(items)]
                    LOG.warning("sheet unreachable (%s). Using %s: %d stints.",
                                self.last_error, label, len(items))
                    return
        # Nothing available: drop a commented template (if missing) and explain.
        if template and self.local_fallback and not os.path.exists(self.local_fallback):
            try:
                os.makedirs(os.path.dirname(self.local_fallback), exist_ok=True)
                with open(self.local_fallback, "w", encoding="utf-8") as fh:
                    fh.write(template)
                LOG.info("Wrote a fallback template to %s", self.local_fallback)
            except OSError:
                pass  # template is a convenience; the sys.exit below explains anyway
        sys.exit(f"ERROR: no schedule available. Sheet error: {self.last_error}\n"
                 f"Check tab '{DEFAULT_SHEET_TAB}', sharing (Anyone with the link: Viewer), "
                 f"or fill {self.local_fallback}.")

    def get(self):
        with self.lock:
            return list(self.items)

    def get_rows(self):
        with self.lock:
            return list(self.rows)

    def inject_row(self, physical_row, url=None, name=None, stint=None):
        """Optimistically merge a panel schedule write into the in-memory
        schedule so an idling feed — and /schedule/data + /cockpit/data — reflect
        it before the next sheet poll. Keyed by physical sheet row (matches
        _parse_rows line numbers); the next poll reconciles against the sheet.
        Each of url/name/stint is applied when given and LEFT UNCHANGED when None,
        so a URL clear (url="") or a name/stint-only edit each touch only their
        own fields. A non-empty non-channel URL is rejected (never serve junk); a
        row left fully empty is dropped, matching _parse_rows which skips blank
        rows (so the optimistic state can't diverge from a re-poll)."""
        with self.lock:
            existing = next((r for r in self.rows if r[3] == physical_row), None)
            cur_u, cur_n, cur_s = existing[:3] if existing else ("", "", "")
            new_u = cur_u if url is None else feed_source_value(url)
            new_n = cur_n if name is None else (name or "").strip()
            new_s = cur_s if stint is None else (stint or "").strip()
            if new_u and not (is_feed_source(new_u) if self.allow_local else is_channel(new_u)):
                return False
            rows = [r for r in self.rows if r[3] != physical_row]
            if new_u or new_n or new_s:        # keep planned stints (url may be "")
                rows.append((new_u, new_n, new_s, physical_row))
            rows.sort(key=lambda r: r[3])
            self.rows = rows
            self.items = [u for u, _n, _s, _l in rows]
        return True

    def health(self):
        with self.lock:
            n = len(self.items)
        return {"count": n,
                "last_ok_age_s": (round(time.time() - self.last_ok, 1) if self.last_ok else None),
                "last_error": self.last_error}


class CrewSource:
    """Reads the Crew roster from the Google Sheet (CSV) with last-good + fallback.

    Mirrors ScheduleSource: a Name | Commentator | Director | Producer | Discord
    tab giving the role capabilities. Commentator capability is resolved via the
    A1 union: subject in live Schedule OR Crew Commentator flag. A missing or
    empty tab is non-fatal -- it simply yields no director/producer rows."""

    def __init__(self, csv_url, cache_path=None):
        self.csv_url = csv_url
        self.cache_path = cache_path
        self.lock = threading.Lock()
        self.rows = []             # canonical: [(name, is_dir, is_prod, is_commentator, is_race_control, discord)]
        self.last_ok = None
        self.last_error = None

    @staticmethod
    def _parse_rows_positional(text):
        """Positional fallback CSV -> [(name, is_director, is_producer)].
        col0=name, col1=director, col2=producer; drops a leading header-like row.
        Returns [] (not None) when no data rows found."""
        rows = list(csv.reader(io.StringIO(text)))
        if not rows:
            return []
        start = 0
        r0 = [(c or "").strip().lower() for c in rows[0]]
        if (len(r0) > 1 and r0[1] in CREW_DIRECTOR_HEADERS) or \
           (len(r0) > 2 and r0[2] in CREW_PRODUCER_HEADERS):
            start = 1                              # drop a header-like first row
        out = []
        for r in rows[start:]:
            name = r[0].strip() if r else ""
            if not name:
                continue
            is_dir = _crew_truthy(r[1]) if len(r) > 1 else False
            is_prod = _crew_truthy(r[2]) if len(r) > 2 else False
            out.append((name, is_dir, is_prod))
        return out

    @staticmethod
    def _parse_full(text):
        """CSV -> [(name, is_dir, is_prod, is_commentator, is_race_control, discord)].

        Header mode (opt-in): if a recognized Name header is present, all columns
        are located by header text (so they may move and extras are ignored).
        Positional fallback (no name header): col0=name, col1=director,
        col2=producer; is_commentator/is_race_control=False and discord="" for
        every row (those columns need a header to locate). Returns [] on empty input."""
        rows = list(csv.reader(io.StringIO(text)))
        if not rows:
            return []
        header = [(h or "").strip().lower() for h in rows[0]]
        name_i = next((header.index(h) for h in CREW_NAME_HEADERS if h in header), None)
        if name_i is not None:
            def _col(headers):
                return next((header.index(h) for h in headers if h in header), None)
            dir_i = _col(CREW_DIRECTOR_HEADERS)
            prod_i = _col(CREW_PRODUCER_HEADERS)
            com_i = _col(CREW_COMMENTATOR_HEADERS)
            rc_i = _col(CREW_RACE_CONTROL_HEADERS)
            dis_i = _col(CREW_DISCORD_HEADERS)

            def _cell(i, row):
                return row[i] if i is not None and len(row) > i else ""

            out = []
            for line, r in enumerate(rows, 1):
                if line == 1:
                    continue                       # the header row itself
                name = r[name_i].strip() if len(r) > name_i else ""
                if not name:
                    continue
                out.append((
                    name,
                    _crew_truthy(_cell(dir_i, r)),
                    _crew_truthy(_cell(prod_i, r)),
                    _crew_truthy(_cell(com_i, r)),
                    _crew_truthy(_cell(rc_i, r)),
                    (_cell(dis_i, r) or "").strip(),
                ))
            return out
        # Positional fallback: name/dir/prod only; no commentator/race-control/discord.
        triples = CrewSource._parse_rows_positional(text)
        return [(n, d, p, False, False, "") for (n, d, p) in triples]

    @staticmethod
    def _parse_rows(text):
        """CSV -> [(name, is_director, is_producer)] or None.

        Header mode (opt-in): if a recognized Name header is present, the
        Name/Director/Producer columns are located by header text (so they may
        move and extra columns are ignored). Positional fallback (no name
        header): col0=name, col1=director, col2=producer, dropping a leading
        header-like row. Rows with an empty name are skipped."""
        full = CrewSource._parse_full(text)
        result = [(n, d, p) for (n, d, p, _c, _rc, _x) in full]
        return result or None

    def get(self):
        """Back-compat 3-tuple roster (name, is_dir, is_prod) for resolve_roles."""
        with self.lock:
            return [(n, d, p) for (n, d, p, _c, _rc, _x) in self.rows]

    def get_full(self):
        """Full 6-tuple roster
        (name, is_dir, is_prod, is_commentator, is_race_control, discord)."""
        with self.lock:
            return list(self.rows)

    def discord_map(self):
        """{discord_username_lower: crew_name} from the Crew tab's Discord column.
        Empty handles are skipped. Last write wins on a duplicate handle."""
        with self.lock:
            full = list(self.rows)
        out = {}
        for name, _d, _p, _c, _rc, discord in full:
            h = (discord or "").strip().lower()
            if h:
                out[h] = name
        return out

    def commentator_keys(self):
        """asset_key set of crew names whose Commentator flag is truthy (A1 union)."""
        with self.lock:
            full = list(self.rows)
        return {asset_key(n) for (n, _d, _p, c, _rc, _x) in full if c and (n or "").strip()}

    def race_control_keys(self):
        """asset_key set of crew names whose Race Control flag is truthy (#244)."""
        with self.lock:
            full = list(self.rows)
        return {asset_key(n) for (n, _d, _p, _c, rc, _x) in full if rc and (n or "").strip()}

    def _fetch_text(self, timeout=15):
        """Fetch the raw CSV text from csv_url. Returns None on error."""
        if not self.csv_url:
            return None
        try:
            req = Request(self.csv_url, headers={"User-Agent": "racecast-feeds/1.0"})
            with urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            return None

    def fetch(self, timeout=15):
        text = self._fetch_text(timeout)
        if text is None:
            return None
        rows = self._parse_rows(text)
        if not rows:
            self.last_error = ("Crew tab reachable, but no rows found "
                               "(correct tab name? a Name column?)")
            return None
        return rows

    def refresh(self, timeout=15):
        text = self._fetch_text(timeout)
        if text is None:
            return False
        rows = self._parse_full(text)
        if not rows:
            self.last_error = ("Crew tab reachable, but no rows found "
                               "(correct tab name? a Name column?)")
            return False
        with self.lock:
            self.rows = rows
            self.last_ok = time.time()
            self.last_error = None
        return True

    def inject_row(self, row, name=None, director=None, producer=None,
                   commentator=None, race_control=None, discord=None):
        """Optimistically merge a Control-Center crew write into the in-memory
        roster so /crew/data reflects it before the next sheet poll. `row` is the
        1-based data-row index (header excluded); row == len+1 appends a row whose
        name is non-empty. Each of name/director/producer/commentator/race_control/
        discord is applied when given and LEFT UNCHANGED when None. The next CSV
        poll reconciles against the sheet."""
        with self.lock:
            rows = list(self.rows)
            i = int(row) - 1
            cur = rows[i] if 0 <= i < len(rows) else ("", False, False, False, False, "")
            cur_n, cur_d, cur_p, cur_c, cur_rc, cur_x = cur
            entry = (cur_n if name is None else (name or "").strip(),
                     cur_d if director is None else bool(director),
                     cur_p if producer is None else bool(producer),
                     cur_c if commentator is None else bool(commentator),
                     cur_rc if race_control is None else bool(race_control),
                     cur_x if discord is None else (discord or "").strip())
            if 0 <= i < len(rows):
                rows[i] = entry
            elif i == len(rows) and entry[0]:
                rows.append(entry)
            self.rows = rows

    def delete_row(self, row):
        """Drop the 1-based data row from the in-memory roster (optimistic echo of
        a Control-Center crew delete). Out-of-range is a no-op."""
        with self.lock:
            i = int(row) - 1
            if 0 <= i < len(self.rows):
                rows = list(self.rows)
                del rows[i]
                self.rows = rows


OVERRIDE_TTL = 30  # s: unconfirmed panel write -> HUD falls back to sheet truth


# The team-entry key set, spelled once. EMPTY and the override padding build
# directly from it (a placeholder has no live values to compute, so there is
# nothing to join). The one constructor that DOES join live values -- roster
# lookup + quali lookup -- is team_entry; HudSource.resolve_team delegates to
# it instead of duplicating that join, so a new field needs adding in exactly
# two places (this literal, and team_entry's return dict), never three or four
# (a forgotten key there would blank a tile for the 30 s of an optimistic echo
# -- the bug that made the suite red between 805b1627 and b50fc381).
EMPTY_TEAM_ENTRY = {"name": "", "number": "", "brandKey": "", "brandName": "",
                    "label": "", "bgColor": "", "textColor": "", "qualiLap": ""}


class HudSource:
    """Reads the Overlay + Configuration tabs and serves the /hud/data dict
    with last-good caching (mirrors ScheduleSource robustness)."""
    EMPTY = {"stint": "", "streamer": "", "session": "",
             "round": {"top": "", "country": "", "flagKey": ""},
             "teams": [dict(EMPTY_TEAM_ENTRY) for _ in range(3)],
             "raceControl": "", "flag": ""}

    def __init__(self, overlay_url, config_url, cache_path, quali_url=None):
        self.overlay_url = overlay_url
        self.config_url = config_url
        self.quali_url = quali_url      # None = no Quali Times tab configured
        self.cache_path = cache_path
        self.lock = threading.Lock()
        self._data = None
        self._vocab = {k: [] for k in VOCAB_COLUMNS}
        self._cue_presets = []
        self._rc_note_presets = []
        self._roster = {}
        self._roster_full = {}   # stripped team name -> verbatim Configuration label
        self._quali = {}
        self._quali_warned = False
        self.overrides = {}   # hud-data key -> (value, expires_ts)
        self.team_overrides = {}   # slot index 0..2 -> (entry_dict, expires_ts)
        self.last_ok = None
        self.last_error = None
        self._load_cache()

    @staticmethod
    def _fetch(url, timeout=10):
        req = Request(url, headers={"User-Agent": "racecast-feeds/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")

    def _load_cache(self):
        try:
            with open(self.cache_path, encoding="utf-8") as fh:
                self._data = json.load(fh)
        except (OSError, ValueError):
            self._data = None

    def refresh_quali(self, timeout=10):
        """Fetch + parse the OPTIONAL Quali Times tab (issue #555) and commit the
        map. Deliberately NOT part of refresh(): quali times are entered once
        between qualifying and the race, so they need none of the HUD's 5 s
        freshness — and keeping the fetch out of refresh() is what guarantees that
        no quali-tab state (missing, present, or unreachable) can ever add a round
        trip to an on-air HUD refresh or to the synchronous panel-push confirm that
        clears an optimistic override. Its own slow thread calls this (see
        quali_poller / QUALI_TIMES_POLL_S), plus once at boot.

        Returns True on a successful fetch+parse, False on failure or when no tab
        is configured. Never raises. A failure keeps the LAST-GOOD map (never
        rolled back to empty) and warns once per relay run; a success resets that
        gate, so a later, DIFFERENT failure (e.g. a parse error after a sheet-format
        change) still warns."""
        if not self.quali_url:
            return False
        try:
            quali = parse_quali_times(self._fetch(self.quali_url, timeout))
        except Exception as e:
            if not self._quali_warned:
                LOG.warning("quali times unavailable (%s: %s) — keeping the last "
                            "known lap times", type(e).__name__, e)
                self._quali_warned = True
            return False
        with self.lock:
            self._quali = quali
            self._quali_warned = False
        return True

    def refresh(self, timeout=10):
        # No quali-tab work happens here -- not a fetch, not a parse (issue #555):
        # this method runs every --hud-poll seconds AND synchronously after every
        # panel write, so nothing optional may sit on it. It only READS the
        # last-good map that refresh_quali() commits.
        #
        # Total by construction: the poll thread calls refresh() bare, so an
        # escaping exception would freeze the on-air overlay on last-good data for
        # the rest of the relay run. Every parse/build step therefore stays inside
        # the guarded try; a failure sets last_error and returns False.
        try:
            with self.lock:
                quali = self._quali          # last-good; committed by refresh_quali
            overlay = parse_overlay(self._fetch(self.overlay_url, timeout))
            config_text = self._fetch(self.config_url, timeout)
            roster = parse_config_roster(config_text)
            roster_full = parse_team_full_labels(config_text)
            vocab = parse_config_vocab(config_text)
            cue_presets = parse_cue_presets(config_text)
            rc_note_presets = parse_rc_note_presets(config_text)
            data = build_hud_data(overlay, roster, quali)
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            return False
        with self.lock:
            self._data = data
            # NB: self._quali is NOT written here -- refresh_quali() owns it, so a
            # commit from its thread is never rolled back by an in-flight refresh.
            self._vocab = vocab
            self._cue_presets = cue_presets
            self._rc_note_presets = rc_note_presets
            self._roster = roster
            self._roster_full = roster_full
            # a sheet poll that already shows the pushed value = confirmation
            self.overrides = {k: (v, exp) for k, (v, exp) in self.overrides.items()
                              if data.get(k) != v}
            self.team_overrides = {
                s: (e, exp) for s, (e, exp) in self.team_overrides.items()
                if (data["teams"][s] if s < len(data["teams"]) else None) != e}
            self.last_ok = time.time()
            self.last_error = None
        try:
            with open(self.cache_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
        except OSError:
            pass  # cache write is best-effort; the in-memory HUD data is current
        return True

    def set_override(self, key, value, now=None):
        """Optimistic echo for a panel write: /hud/data shows the value NOW;
        the sheet poll confirms (clears) it, or it expires after OVERRIDE_TTL."""
        now = time.time() if now is None else now
        with self.lock:
            self.overrides[key] = (value, now + OVERRIDE_TTL)

    def pending(self, now=None):
        """Keys with an unconfirmed (and unexpired) optimistic override."""
        now = time.time() if now is None else now
        with self.lock:
            return {k for k, (_v, exp) in self.overrides.items() if exp > now}

    def data(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            self.overrides = {k: (v, exp) for k, (v, exp) in self.overrides.items()
                              if exp > now}
            self.team_overrides = {s: (e, exp) for s, (e, exp) in self.team_overrides.items()
                                   if exp > now}
            # Always shallow-copy: callers (and /setup/data decoration) must never
            # be able to mutate the canonical dict.
            base = dict(self._data) if self._data is not None else dict(self.EMPTY)
            out = dict(base)
            if self.overrides:
                out.update({k: v for k, (v, _exp) in self.overrides.items()})
            if self.team_overrides:
                teams = [dict(t) for t in out.get("teams", [])]
                while len(teams) < 3:
                    teams.append(dict(EMPTY_TEAM_ENTRY))
                for s, (e, _exp) in self.team_overrides.items():
                    if 0 <= s < len(teams):
                        teams[s] = dict(e)
                out["teams"] = teams
            return out

    def vocab(self):
        with self.lock:
            return {k: list(v) for k, v in self._vocab.items()}

    def cue_presets(self):
        with self.lock:
            return list(self._cue_presets)

    def rc_note_presets(self):
        with self.lock:
            return list(self._rc_note_presets)

    def roster_names(self):
        """Team names from the Configuration roster, in sheet order (panel vocab)."""
        with self.lock:
            return list(self._roster.keys())

    def quali_times(self):
        """The Quali Times map {asset_key: lap}, last-good (empty when the tab is
        absent/unreachable)."""
        with self.lock:
            return dict(self._quali)

    def resolve_team(self, label):
        """A /hud/data team entry for a roster label (the verbatim '#NNN' value the
        panel dropdown sends) or an unknown label. Delegates to team_entry --
        the same join a live Overlay slot value gets (verbatim-label-first
        lookup, then a stripped-name fallback, the verbatim 'label' riding
        along for the panel's optimistic echo) -- so the two constructors can
        never drift apart. Only the roster/quali snapshot is taken under the
        lock; the join itself runs lock-free."""
        with self.lock:
            roster, quali = dict(self._roster), dict(self._quali)
        return team_entry(label, roster, quali)

    def full_team_name(self, name):
        """The verbatim Configuration team label (e.g. 'OVO eSports #111') to write
        into the Setup cell — what the Setup tab dropdown expects. The panel now
        sends the verbatim label directly (a roster key), so it passes through
        unchanged; a bare name from an older panel is mapped back via roster_full,
        and an unknown value falls through to itself (never a KeyError)."""
        name = (name or "").strip()
        with self.lock:
            if name in self._roster:
                return name                       # already the verbatim label
            bare, _embedded = split_team_label(name)
            return self._roster_full.get(bare) or name

    def set_team_override(self, slot, entry, now=None):
        """Optimistic echo for a panel team write into podium slot 0..2."""
        now = time.time() if now is None else now
        with self.lock:
            self.team_overrides[slot] = (entry, now + OVERRIDE_TTL)

    def set_teams_override(self, entries, now=None):
        """Optimistic echo for a BATCH panel team write: set multiple podium-slot
        overrides (0-based slot -> entry) under a SINGLE lock acquisition, so the
        /hud/data reader (same lock) never observes a partial top-3. The per-call
        set_team_override would let a poll interleave between slots — the exact
        duplication this batch path removes."""
        now = time.time() if now is None else now
        exp = now + OVERRIDE_TTL
        with self.lock:
            for slot, entry in entries.items():
                self.team_overrides[slot] = (entry, exp)

    def team_pending(self, now=None):
        """Slot indices with an unconfirmed (and unexpired) optimistic team override."""
        now = time.time() if now is None else now
        with self.lock:
            return {s for s, (_e, exp) in self.team_overrides.items() if exp > now}

    def health(self):
        with self.lock:
            return {"last_ok_age_s": (round(time.time() - self.last_ok, 1)
                                      if self.last_ok else None),
                    "last_error": self.last_error}


# Panel setup fields: URL segment -> (Setup-tab header, /hud/data key).
# NOTE: the Setup "Stint" is the HUD display LABEL — it has no relationship
# to the relay's feed stint index (/set/stint, NEXT).
SETUP_FIELDS = {
    "stint": ("Stint", "stint"),
    "streamer": ("Streamer", "streamer"),
    "session": ("Session", "session"),
    "racecontrol": ("Race Control", "raceControl"),
    "flag": ("Flag", "flag"),
}

# Panel team slots: URL segment -> 1-based podium slot (Setup tab "Team <n>" cell).
TEAM_SLOTS = {"p1": 1, "p2": 2, "p3": 3}


class SetupControl:
    """Panel -> sheet writes (spec: panel-sheet-control). Setup fields are
    async-optimistic (override now, push in the background, the sheet poll
    confirms); Schedule/POV URL writes are synchronous (no local echo target,
    and answering after the webhook confirm removes the save-vs-RELOAD race).
    The sheet stays authoritative throughout."""

    def __init__(self, push_url, hud_source, schedule_source=None, qual_source=None,
                 pov_source=None, crew_source=None):
        self.push_url = push_url
        self.hud = hud_source
        self.schedule_source = schedule_source
        self.qual_source = qual_source
        self.pov_source = pov_source
        self.crew_source = crew_source
        self.push_status = "disabled" if not push_url else "never"
        self.last_error = None

    # -- shared blocking push -> (ok, error); diagnostics like TimerStore ----
    def _push(self, payload, expected_action):
        ok, err, _body = push_webhook_retrying(self.push_url, payload, expected_action)
        # diagnostics: single ref assignments, no lock needed
        self.push_status = "ok" if ok else "failed"
        self.last_error = None if ok else err
        return ok, err

    # -- setup fields (async-optimistic) -------------------------------------
    def set_field(self, key, value, now=None):
        if key not in SETUP_FIELDS:
            return {"error": f"unknown field: {key!r} "
                             f"(one of {', '.join(sorted(SETUP_FIELDS))})"}
        if not self.push_url:
            return {"error": "webhook not configured — set RACECAST_SHEET_PUSH_URL "
                             "in the active profile or .env (wiki: Sheet-Webhook)"}
        header, hud_key = SETUP_FIELDS[key]
        if not value and key not in ("racecontrol", "flag"):
            return {"error": "empty value only allowed for racecontrol/flag"}
        if value and value not in self.hud.vocab().get(key, []):
            return {"error": f"not in the Configuration vocabulary: {value!r} "
                             "(add it to the Configuration tab first)"}
        self.hud.set_override(hud_key, value, now)
        threading.Thread(target=self._push_setup, args=(header, value),
                         daemon=True).start()
        return {"ok": True, "field": key, "value": value, "pending": True}

    def _push_setup(self, header, value):
        ok, _err = self._push({"action": "setup", "fields": {header: value}},
                              "setup")
        if ok:
            self.hud.refresh()   # confirm now, not at the next poll tick

    # -- team slots (async-optimistic, writes the Setup tab team cells A6/B6/C6;
    #    the Overlay tab only mirrors them read-only) --------------------------
    def set_team(self, slot_key, name, now=None):
        if slot_key not in TEAM_SLOTS:
            return {"error": f"unknown team slot: {slot_key!r} "
                             f"(one of {', '.join(sorted(TEAM_SLOTS))})"}
        if not self.push_url:
            return {"error": "webhook not configured — set RACECAST_SHEET_PUSH_URL "
                             "in the active profile or .env (wiki: Sheet-Webhook)"}
        name = (name or "").strip()
        if name not in self.hud.roster_names():
            return {"error": f"not in the team roster: {name!r} "
                             "(add it to the Configuration tab first)"}
        slot = TEAM_SLOTS[slot_key]
        self.hud.set_team_override(slot - 1, self.hud.resolve_team(name), now)
        threading.Thread(target=self._push_team, args=(slot, name),
                         daemon=True).start()
        return {"ok": True, "slot": slot_key, "value": name, "pending": True}

    def _push_team(self, slot, name):
        # Write the verbatim Configuration label (e.g. 'OVO eSports #111') the
        # Setup dropdown lists, not the panel's stripped vocab name — the Setup
        # cell then matches the dropdown exactly, like the other fields.
        full = self.hud.full_team_name(name)
        ok, _err = self._push({"action": "teams", "slot": slot, "name": full},
                              "teams")
        if ok:
            self.hud.refresh()

    # -- batch team apply (Director Panel "Apply Top 3"): all slots atomic ----
    def set_teams(self, teams, now=None):
        """Set all given podium slots ATOMICALLY (one HudSource lock -> the
        broadcast HUD never shows a partial/duplicated standing), then write each
        slot back to the Sheet via the existing single-slot `teams` webhook action
        (no Apps Script change). Validation is all-or-nothing: any bad slot key or
        non-roster value applies nothing and writes nothing."""
        if not isinstance(teams, dict):
            return {"error": "teams must be an object like {\"p1\":\"…\"}"}
        if not self.push_url:
            return {"error": "webhook not configured — set RACECAST_SHEET_PUSH_URL "
                             "in the active profile or .env (wiki: Sheet-Webhook)"}
        roster = self.hud.roster_names()
        resolved = {}                       # 0-based slot index -> (slot_key, name)
        for slot_key, name in teams.items():
            if slot_key not in TEAM_SLOTS:
                return {"error": f"unknown team slot: {slot_key!r} "
                                 f"(one of {', '.join(sorted(TEAM_SLOTS))})"}
            name = (name or "").strip()
            if name not in roster:
                return {"error": f"not in the team roster: {name!r} "
                                 "(add it to the Configuration tab first)"}
            resolved[TEAM_SLOTS[slot_key] - 1] = (slot_key, name)
        if not resolved:
            return {"error": "no team slots given"}
        entries = {idx: self.hud.resolve_team(name)
                   for idx, (_k, name) in resolved.items()}
        self.hud.set_teams_override(entries, now)
        writes = [(idx + 1, name) for idx, (_k, name) in sorted(resolved.items())]
        threading.Thread(target=self._push_teams, args=(writes,),
                         daemon=True).start()
        return {"ok": True,
                "slots": [k for _i, (k, _n) in sorted(resolved.items())],
                "pending": True}

    def _push_teams(self, writes):
        """Sheet write-back for a batch apply: one webhook call per slot (the
        single-slot `teams` action, reused), then a single hud.refresh() once all
        slots are written. `writes` is a list of (1-based slot, roster name)."""
        ok_all = True
        first_err = None
        for slot, name in writes:
            full = self.hud.full_team_name(name)
            ok, err = self._push({"action": "teams", "slot": slot, "name": full},
                                 "teams")
            if not ok:
                ok_all = False
                if first_err is None:
                    first_err = err
        if ok_all:
            self.hud.refresh()
        else:
            # A later slot's success must not mask an earlier slot's failure:
            # _push sets push_status per call, so without this the panel would
            # read "sheet sync OK" while a slot silently reverted after the TTL.
            self.push_status = "failed"
            self.last_error = first_err

    # -- URL writes (synchronous) --------------------------------------------
    def schedule_set(self, row, url=None, name=None, stint=None):
        """Write a race Schedule row (the default tab)."""
        return self._schedule_write(row, url, name, stint,
                                    tab=None, inject_source=self.schedule_source)

    def qualifying_set(self, row, url=None, name=None, stint=None):
        """Write the qualifying row — same payload, but targeting the Qualifying
        tab (issue #124) and echoing into the qualifying source. Kept separate so
        the race schedule is never touched by a qualifying edit and vice versa."""
        return self._schedule_write(row, url, name, stint,
                                    tab=DEFAULT_QUALIFYING_TAB,
                                    inject_source=self.qual_source)

    # -- crew roster writes (Crew tab: Name | Commentator | Director | Producer | Discord) --
    def crew_set(self, row, name=None, director=None, producer=None,
                 commentator=None, race_control=None, discord=None):
        """Write one Crew tab row via the webhook (per-row, mirrors schedule).
        `name` is free text (crew may be director/producer-only people, not in
        the streamer vocabulary); director/producer/commentator/race_control are
        coerced to booleans; discord is the trimmed Discord username (may be empty).
        The webhook degrades gracefully if the Sheet Apps Script lacks the
        race_control column (it ignores the extra field)."""
        if not self.push_url:
            return {"error": "webhook not configured — set RACECAST_SHEET_PUSH_URL "
                             "in the active profile or .env (wiki: Sheet-Webhook)"}
        rownum = self._crew_rownum(row)
        if isinstance(rownum, dict):
            return rownum
        name = (name or "").strip()
        if not name:
            return {"error": "name is required"}
        discord = (discord or "").strip()
        payload = {"action": "crew", "row": rownum, "name": name,
                   "director": bool(director), "producer": bool(producer),
                   "commentator": bool(commentator),
                   "race_control": bool(race_control), "discord": discord}
        ok, err = self._push(payload, "crew")
        if ok and self.crew_source is not None:
            self.crew_source.inject_row(rownum, name=name, director=bool(director),
                                        producer=bool(producer),
                                        commentator=bool(commentator),
                                        race_control=bool(race_control), discord=discord)
        return {"ok": True, "row": rownum} if ok else {"error": err}

    def crew_delete(self, row):
        """Delete one Crew tab row by 1-based index via the webhook."""
        if not self.push_url:
            return {"error": "webhook not configured — set RACECAST_SHEET_PUSH_URL "
                             "in the active profile or .env (wiki: Sheet-Webhook)"}
        rownum = self._crew_rownum(row)
        if isinstance(rownum, dict):
            return rownum
        ok, err = self._push({"action": "crew", "row": rownum, "delete": True}, "crew")
        if ok and self.crew_source is not None:
            self.crew_source.delete_row(rownum)
        return {"ok": True, "row": rownum} if ok else {"error": err}

    @staticmethod
    def _crew_rownum(row):
        """A 1-based crew row as int, or an error dict. Mirrors the schedule row
        validation (rejects bool, non-numeric, < 1)."""
        if isinstance(row, bool) or not isinstance(row, (int, str)):
            return {"error": "row must be a whole number (1-based)"}
        try:
            row = int(row)
        except (TypeError, ValueError):
            return {"error": "row must be a number (1-based)"}
        if row < 1:
            return {"error": "row must be >= 1"}
        return row

    def _schedule_write(self, row, url=None, name=None, stint=None,
                        tab=None, inject_source=None):
        if not self.push_url:
            return {"error": "webhook not configured — set RACECAST_SHEET_PUSH_URL "
                             "in the active profile or .env (wiki: Sheet-Webhook)"}
        if isinstance(row, bool) or not isinstance(row, (int, str)):
            return {"error": "row must be a whole number (1-based)"}
        try:
            row = int(row)
        except (TypeError, ValueError):
            return {"error": "row must be a number (1-based)"}
        if row < 1:
            return {"error": "row must be >= 1"}
        if url is None and name is None and stint is None:
            return {"error": "nothing to write (provide url, name and/or stint)"}
        if url is not None and not isinstance(url, str):
            return {"error": "url must be a string"}
        if name is not None and not isinstance(name, str):
            return {"error": "name must be a string"}
        if stint is not None and not isinstance(stint, str):
            return {"error": "stint must be a string"}
        payload = {"action": "schedule", "row": row}
        if tab:
            payload["tab"] = tab        # webhook writes this tab (default: Schedule)
        if url is not None:
            url = feed_source_value(url)
            if url and not is_feed_source(url):
                return {"error": "url must be a watch URL, UC… channel ID or local:"}
            payload["url"] = url
        # Streamer + Stint are vocabulary-constrained, like the Setup fields:
        # a value picked in the Schedule editor must exist in the Configuration
        # tab so the handover auto-write can never produce one set_field rejects.
        if name is not None:
            name = name.strip()
            err = self._reject_off_vocab("streamer", name)
            if err:
                return err
            payload["name"] = name
        if stint is not None:
            stint = stint.strip()
            err = self._reject_off_vocab("stint", stint)
            if err:
                return err
            payload["stint"] = stint
        ok, err = self._push(payload, "schedule")
        if ok and inject_source is not None:
            # Reflect the write locally now — INCLUDING a URL clear (url="") — so
            # /schedule/data + /cockpit/data don't show the stale link for a poll
            # interval. None for a field the write didn't touch leaves it as-is.
            inject_source.inject_row(row, payload.get("url"), payload.get("name"),
                                     payload.get("stint"))
        return {"ok": True, "row": row} if ok else {"error": err}

    def _reject_off_vocab(self, key, value):
        """An error dict when a non-empty value is outside the Configuration
        vocabulary for *key*, else None. Mirrors set_field's strictness; a no-op
        when no HUD source is wired (the vocab is then unknown)."""
        if value and self.hud is not None and value not in self.hud.vocab().get(key, []):
            return {"error": f"not in the Configuration {key} vocabulary: {value!r} "
                             "(add it to the Configuration tab first)"}
        return None

    def pov_set(self, url, name=None):
        if not self.push_url:
            return {"error": "webhook not configured — set RACECAST_SHEET_PUSH_URL "
                             "in the active profile or .env (wiki: Sheet-Webhook)"}
        if url is not None and not isinstance(url, str):
            return {"error": "url must be a string"}
        url = (url or "").strip()
        if url and not is_channel(url):
            return {"error": "url must be a watch URL or UC… channel ID"}
        payload = {"action": "pov", "url": url}
        if name is not None:        # omitted -> leave the Sheet cell; "" -> explicit clear
            payload["name"] = (name or "")[:20]
        ok, err = self._push(payload, "pov")
        if ok and self.pov_source is not None:
            self.pov_source.refresh()    # name (and stored url) live immediately
        return {"ok": True} if ok else {"error": err}

    # -- panel poll ------------------------------------------------------------
    def data(self):
        hud = self.hud.data()
        pending = self.hud.pending()
        team_pending = self.hud.team_pending()
        teams = hud.get("teams", [])
        names = self.hud.roster_names()
        fields = {k: hud.get(hk, "") for k, (_h, hk) in SETUP_FIELDS.items()}
        options = self.hud.vocab()
        out_pending = sorted(k for k, (_h, hk) in SETUP_FIELDS.items() if hk in pending)
        for key, slot in TEAM_SLOTS.items():
            i = slot - 1
            # The verbatim '#NNN' label (not the stripped name) so the panel's
            # <select> current value matches one of the verbatim roster options.
            fields[key] = (teams[i].get("label") or teams[i].get("name", "")
                           if i < len(teams) else "")
            options[key] = list(names)
            if i in team_pending:
                out_pending.append(key)
        return {"fields": fields, "options": options,
                "pending": sorted(out_pending),
                "push": self.push_status, "last_error": self.last_error}


_STREAM_QUALITY_RE = re.compile(r"Opening stream:\s+(\S+)")


def parse_stream_quality(line):
    """The quality token from a streamlink 'Opening stream: <quality> (...)' line,
    else None. Pure → unit-tested."""
    if not line:
        return None
    m = _STREAM_QUALITY_RE.search(line)
    return m.group(1) if m else None


_YTDLP_QUALITY_RE = re.compile(r"^rcq\s+(\d+)(?:\s+(\S+))?", re.M)
_YTDLP_LIVE_STATUS_RE = re.compile(r"^rcs\s+(\S+)", re.M)
YTDLP_NO_FORMAT = "Requested format is not available"


def parse_ytdlp_live_status(text):
    """The live status from a yt-dlp `--print "rcs %(live_status)s"` line ('is_live',
    'post_live', 'was_live', 'is_upcoming', 'NA' when unknown), else None when the line is
    absent (yt-dlp aborted before printing). Pure → unit-tested (#621)."""
    if not text:
        return None
    m = _YTDLP_LIVE_STATUS_RE.search(text)
    return m.group(1) if m else None



def parse_ytdlp_quality(text):
    """The '<height>p<fps>' token from a yt-dlp `--print "rcq %(height)s %(fps)s"` line
    (e.g. 'rcq 1080 60.0' -> '1080p60'), else None when the height is unknown/absent. fps is
    rounded and dropped when unavailable ('rcq 480 NA' -> '480p'). For YouTube the actually-
    served resolution is captured HERE (yt-dlp selected the format); streamlink only reports
    the useless single-variant HLS name 'live'. Pure → unit-tested."""
    if not text:
        return None
    m = _YTDLP_QUALITY_RE.search(text)
    if not m:
        return None
    height, fps_raw = m.group(1), m.group(2)
    try:
        fps = int(round(float(fps_raw)))
    except (TypeError, ValueError):
        fps = None
    return f"{height}p{fps}" if fps else f"{height}p"


def quality_token_is_useful(q):
    """Whether a streamlink-reported quality token is worth keeping. YouTube serves a single-
    variant HLS, so streamlink only ever reports 'live' (no resolution) — ignore it so the
    yt-dlp-resolved resolution stays; real labels (Twitch '1080p60'/'source'/…) are kept.
    Pure → unit-tested."""
    return bool(q) and q != "live"


class Feed:
    def __init__(self, name, port, idx, provider, logdir, cookies=None, fmt=YTDLP_FORMAT,
                 cookie_dir=None):
        self.name = name
        self.port = port
        self.idx = idx
        self.provider = provider          # callable -> current schedule list
        self.cookies = cookies            # path to yt-cookies.txt (bot-check protection) or None
        self.fmt = fmt                    # legacy yt-dlp format default; run() now resolves via
                                          # quality_ytdlp_fmt(self.quality_tier) instead (#493)
        self.cookie_dir = cookie_dir      # dir holding yt-/twitch-cookies.txt (for per-pull resolve)
        self.paused = False               # when True the feed idles (POV off / stopped)
        self.lock = threading.Lock()
        self.proc = None
        self.stop = False
        self.advance = threading.Event()
        self.logfile = os.path.join(logdir, f"feed_{name}.log")
        self.log = logsetup.configure_logging(
            f"racecast.feed.{name}", self.logfile, to_stdout=False)
        # Health for /status: phase ("idle" | "connecting" | "serving"),
        # since-when, and the last yt-dlp error line. Written by the run()
        # thread, read by Relay.status() — a reader may briefly pair a new
        # phase with a stale timestamp; tolerable for a 2 s-polled display
        # (same convention as self.proc).
        self.phase = "idle"
        self.phase_since = time.time()
        self.last_error = None
        # True after a live serve was lost unexpectedly (not a stop/handover) and
        # not yet recovered or acknowledged — surfaced as feeds.<X>.down in
        # /status so the panel/Companion can raise a distinct alarm. Cleared on
        # recovery (re-serving) and on director intervention (reload/reposition).
        self.dropped = False
        # Health debounce (issue #278): dropped_since stamps when `dropped` flips
        # False->True so the heartbeat can require a 30 s grace before escalating
        # to CRITICAL; served_ok turns sticky once a serve has lasted
        # HEALTH_SERVED_OK_S, so a feed that never delivered a stable picture
        # (demo/startup) is classified "connecting", not a lost live stream.
        self.dropped_since = None
        self.served_ok = False
        # Consecutive "was live, now dead" serves (resolved OK but died faster than
        # HEALTH_SERVED_OK_S — the 403/expired-manifest case). Drives escalating
        # backoff and the idle-after-N give-up in run(). Reset on a real serve, on
        # operator advance, and in set_index()/reload(). NOT reset in
        # _clear_drop_health() (that runs every serve-start, which would zero it).
        self.dead_serves = 0
        self.quality_tier = "full"        # #493: full|robust|emergency (POV: pinned robust)
        self.quality_pinned = False       # True = operator pinned; suppresses auto-step-down
        self.on_step_down = None          # relay-set callback(feed, stint, from_tier, to_tier)
        self.quality = None               # last streamlink-selected quality (e.g. "720p")
        self.ring = None              # set by the relay when fan-out is enabled (#358); None → direct-serve
        self.fanout_server = None     # its FeedFanoutServer (fan-out on); the freeze detector reads its snap count (#488)
        self.serve_generation = 0     # #614: bumped per fan-out serve, so a scheduled rejoin can tell it was superseded
        self.last_byte_ts = None      # monotonic ts of the last byte pumped into the ring (fan-out health)
        self._max_inbound_gap = 0.0   # #535: largest inter-arrival gap this heartbeat interval
        self.on_recovery = None       # relay-set callback(feed, stint, downtime_s, source_state) on a drop-recovery
        self.source_state = None      # #495: "not_live_yet"/"ended"/None (why the feed isn't serving)
        self.offline_since = None     # #495: epoch the source first became classified-offline (else None)

    def _set_source_state(self, st):
        """Set source_state and maintain offline_since (#495): stamp the epoch on the
        first None->classified transition of an outage (kept across not_live_yet->ended),
        and clear it when the source is no longer classified-offline."""
        if st:
            if self.offline_since is None:
                self.offline_since = time.time()
        else:
            self.offline_since = None
        self.source_state = st

    def take_max_inbound_gap(self):
        """Return the largest inter-arrival gap seen since the last call, and reset (#535).
        Called once per heartbeat so a transient stall between 30 s ticks is still captured."""
        g = self._max_inbound_gap
        self._max_inbound_gap = 0.0
        return g

    def current_channel(self):
        if self.paused:
            return None, self.idx
        sched = self.provider()
        with self.lock:
            if not sched or self.idx >= len(sched):
                return None, self.idx          # idle: empty schedule or own slot not filled yet
            return sched[self.idx], self.idx

    def is_serving(self):
        p = self.proc
        return bool(p and p.poll() is None)

    def _set_phase(self, phase):
        """Phase + timestamp, updated only on change — so state_age_s keeps
        accumulating across resolve retries within one 'connecting' stretch."""
        if phase != self.phase:
            self.phase = phase
            self.phase_since = time.time()

    def _kill_proc(self):
        p = self.proc
        if p and p.poll() is None:
            try:
                p.terminate()
                try: p.wait(timeout=5)
                except subprocess.TimeoutExpired: p.kill()
            except Exception:
                pass  # the process may already be gone — nothing left to kill

    def _clear_drop_health(self):
        """Reset the drop-debounce state: no active drop, and the new/reconnecting
        source has not yet delivered a stable picture (served_ok goes False so a
        fresh source must re-earn it before a future drop can page CRITICAL)."""
        self.dropped = False
        self.dropped_since = None
        self.served_ok = False
        self._set_source_state(None)     # a fresh serve/reposition: the drop's cause no longer applies

    def _observe_streamlink_line(self, line):
        q = parse_stream_quality(line)
        if quality_token_is_useful(q):
            self.quality = q     # Twitch reports a real label; YouTube's "live" is ignored (yt-dlp set it)
        st = classify_source_state(line)
        if st is not None and not self.served_ok:
            self._set_source_state(st)

    def set_index(self, new_idx):
        sched = self.provider()
        new_idx = max(0, min(new_idx, len(sched)))   # len == idle slot (one past the last stint)
        with self.lock:
            if new_idx == self.idx:
                return False
            self.idx = new_idx
        self._clear_drop_health()         # director repositioned -> alarm acknowledged, new source not yet served
        self.dead_serves = 0              # new source -> fresh dead-serve count
        self.quality_tier = "full"        # #493: a new source starts fresh at full quality
        self.quality_pinned = False
        self.advance.set(); self._kill_proc()
        return True

    def reload(self):
        """Reconnect to the (possibly changed) channel at the CURRENT index."""
        self._clear_drop_health()         # director intervened -> alarm acknowledged, reconnecting source
        self.dead_serves = 0              # operator reload -> fresh dead-serve count
        self.advance.set(); self._kill_proc()
        return True

    def set_quality(self, tier, pinned):
        """Set the quality tier and pin state, then trigger a re-resolve so the change
        takes effect immediately (brief reconnect — a deliberate director action)."""
        self.quality_tier = tier
        self.quality_pinned = pinned
        if is_local_source(self.current_channel()[0]):
            return      # #592: a capture card ignores tiers; a restart is a black gap on air
        self.advance.set(); self._kill_proc()

    def maybe_step_down(self):
        """If an auto FULL->ROBUST step-down is due (see quality_step_down_due), apply it
        and return (from_tier, to_tier); else None. Leaves pinned False (still managed)."""
        if not feed_robust_auto_enabled(os.environ):
            return None
        if is_local_source(self.current_channel()[0]):
            return None       # #592: a capture card has no quality tiers
        if quality_step_down_due(self.quality_tier, self.quality_pinned,
                                 self.dead_serves, self.source_state):
            frm = self.quality_tier
            self.quality_tier = "robust"
            return (frm, "robust")
        return None

    def _obs_reconnect_now(self):
        """Force OBS to reconnect its media source for THIS feed's port (fan-out only).
        In fan-out the RELAY is the persistent HTTP server, so a streamlink restart is
        invisible to OBS: its socket stays open and the fresh stream is spliced into a
        stale demuxer → the ~1 Hz freeze-frame stutter (2026-07-10). Rebuilding just this
        feed's OBS input (SetInputSettings, the proven relay-stop primitive, scoped by
        port) drops OBS's socket so it re-joins at the fresh stream with a clean demuxer —
        exactly what a direct-serve streamlink restart does for free. Callers: the
        re-serve hook (_obs_rejoin_hook), the freeze detector and the director's RESET.
        Best-effort + synchronous; returns the rebuilt input names (for the log + tests)."""
        if _obs_ws is None:
            return []
        try:
            # #537: deliberately NOT routed through relay._obs — the Feed holds no
            # facade reference (it is built before/independent of Relay), and a rejoin
            # is rare (a drop, a director click, a stint change), so it stays a
            # connect-per-call site.
            names, note = _obs_ws.release_feed_inputs(ports=[self.port])
            if names:
                self.log.info("fan-out rejoin on %s — rebuilt OBS input(s) %s so OBS "
                              "reconnects cleanly", self.name, ", ".join(names))
            elif note:
                self.log.debug("fan-out rejoin on %s — OBS reconnect skipped (%s)",
                               self.name, note)
            return names
        except Exception as exc:              # noqa: BLE001 — best-effort, never crash the serve
            self.log.debug("fan-out rejoin on %s — OBS reconnect error (%s)", self.name, exc)
            return []

    def _obs_reconnect(self):
        """Threaded wrapper so the reconnect (an obs-websocket round-trip) never blocks
        the ring reader."""
        threading.Thread(target=self._obs_reconnect_now, daemon=True).start()

    def consumer_attached(self):
        """True when a consumer is reading this feed's fan-out ring right now (#614).
        The HTTP fan-out port serves OBS only — the Director-Panel preview
        (_PreviewRingTap) and the program-audio encoder read the ring object
        in-process — so this answers "would a restart splice into OBS's open
        demuxer?". The OBS socket survives the streamlink kill (the ring is not
        closed, its read just blocks), so it is still counted between the two
        serves. Best-effort: no fan-out server, or a server that cannot answer,
        means no rejoin — today's behaviour."""
        srv = self.fanout_server
        if srv is None:
            return False
        try:
            stuck, _snaps = srv.consumer_health(time.monotonic())
        except Exception:      # noqa: BLE001 — a health read must never break the serve loop
            return False
        return stuck is not None

    def _obs_rejoin_after_prefetch(self, segments, prebuffer_s,
                                   sleep=time.sleep, clock=time.monotonic):
        """Rejoin OBS once this serve's HLS prefetch burst has landed (#614).

        The burst's arrival span is a download duration — it depends on the producer's
        downlink, the source bitrate and the CDN — so it is MEASURED here rather than
        predicted: the thread watches `last_byte_ts` and takes the first inbound idle of
        `BURST_IDLE_S` as the burst's end. `prefetch_land_s` is only the ceiling, for a
        source that never pauses that long (Twitch low-latency does not, above ~1.4 s).

        Detecting the end costs `BURST_IDLE_S`, and that latency IS the safety margin:
        the join has to be `prebuffer_s` past the burst, and it ends up
        `BURST_IDLE_S + prebuffer_s` past it.

        Threaded, because the wait must never touch the ring reader, whose "never
        blocks" invariant is load-bearing. `sleep`/`clock` are injected for tests. A
        serve that was superseded or died during the wait no-ops: its rebuild would drop
        OBS onto the newest serve's burst, or onto a feed with no bytes at all."""
        gen = self.serve_generation
        ceiling = max(0.0, prefetch_land_s(segments, 0.0))     # burst span only
        def _stale():
            return rejoin_is_stale(self.stop, self.advance.is_set(), gen,
                                   self.serve_generation, self.phase == "serving")

        def _run():
            if segments > 0:
                started = clock()
                while True:
                    if _stale():
                        return self._log_rejoin_skipped(gen)
                    now = clock()
                    if feed_stalled(self.last_byte_ts, now, stall_s=BURST_IDLE_S):
                        break                      # the burst stopped arriving
                    if now - started >= ceiling:
                        self.log.debug("fan-out rejoin on %s — the source never paused "
                                       "within %.1fs; rejoining on the ceiling",
                                       self.name, ceiling)
                        break
                    sleep(BURST_POLL_S)
                sleep(max(0.0, prebuffer_s))       # let the burst age past the join mark
            if _stale():
                return self._log_rejoin_skipped(gen)
            self._obs_reconnect_now()
            return None
        t = threading.Thread(target=_run, daemon=True)
        try:
            t.start()
        except RuntimeError as exc:    # out of threads: a cosmetic rejoin never kills a serve
            self.log.debug("fan-out rejoin on %s not scheduled (%s)", self.name, exc)
            return None
        return t                       # production ignores it; tests join on it

    def _log_rejoin_skipped(self, gen):
        self.log.debug("fan-out rejoin on %s skipped — serve %d was superseded or ended "
                       "during the prefetch wait", self.name, gen)
        return None

    def _obs_rejoin_hook(self, flags):
        """The `on_first_byte` hook Feed.run hands the fan-out serve: a prefetch-delayed
        rejoin when this re-serve would splice into an open OBS demuxer, else None.

        `flags` is the streamlink flag list this serve runs with — the wait is derived
        from its `--hls-live-edge` count and the server's own prebuffer, so a tier switch
        or a changed RACECAST_FEED_PREBUFFER_S moves it automatically. The local capture
        ffmpeg (#592) passes no flags: no burst, no wait."""
        if not should_obs_reconnect(self.ring is not None, self.dropped,
                                    self.consumer_attached()):
            return None
        # consumer_attached() already proved the server exists; the default only covers
        # a test double. It mirrors the relay's own default rather than 0, because 0 is
        # the single value that makes the derived wait WRONG instead of conservative.
        prebuffer = getattr(self.fanout_server, "prebuffer_s", None)
        if prebuffer is None:
            prebuffer = health_store.DEFAULT_FEED_PREBUFFER_S
        segments = live_edge_segments(flags)
        return lambda: self._obs_rejoin_after_prefetch(segments, prebuffer)

    def _serve_fanout(self, target, serve_platform, token, on_first_byte=None,
                      cmd=None, tool="streamlink"):
        """Fan-out serve: stream `streamlink --stdout` into self.ring, tracking
        last_byte_ts so the stall watchdog and EOF both surface. Returns
        (serve_elapsed, serve_rc) like the direct-serve proc.wait() so Feed.run's
        classification tail is shared. A separate watchdog kills streamlink on a
        byte-stall (the reader is parked in read1() and can't self-check); stop /
        advance / EOF end the loop directly. `on_first_byte` (if given) is called
        once, right after the first byte reaches the ring — used to force OBS to
        reconnect on a drop-recovery (see _obs_reconnect). A prebuilt `cmd` (the
        local capture ffmpeg, #592) replaces the streamlink argv; `tool` tags its
        stderr in feed_X.log, and only streamlink's lines feed the quality/source-
        state observer."""
        if cmd is None:
            cmd = streamlink_fanout_cmd(target, serve_platform, token, cookies=self.cookies,
                                        tier=self.quality_tier)
        # stdout is the raw video byte stream (read into the ring); stderr is
        # streamlink's ONLY diagnostic channel here, so it must be PIPEd and pumped
        # — unlike direct-serve (which merges stderr into a text stdout), discarding
        # it left every fan-out stall/EOF unexplained in feed_X.log. The pump thread
        # also keeps the stderr pipe drained so streamlink can never block on a full
        # pipe (parity with direct-serve; #294-class diagnostics).
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=external_tool_env(), **_no_window_kwargs())
        stderr_text = io.TextIOWrapper(self.proc.stderr, encoding="utf-8", errors="replace")
        threading.Thread(
            target=logsetup.pump_subprocess,
            args=(stderr_text, self.log, tool),
            kwargs={"on_line": (self._observe_streamlink_line
                                if tool == "streamlink" else None)},
            daemon=True).start()
        self._set_phase("serving")
        self._clear_drop_health()
        self.serve_generation += 1    # #614: this serve owns OBS from here on
        self.last_byte_ts = None
        if self.ring is not None:
            self.ring.reset_head()     # #576: a new upstream = a new init segment
        serve_started = time.monotonic()
        stdout = self.proc.stdout
        stall_s = feed_stall_s(os.environ)
        watchdog_stop = threading.Event()

        def _watchdog():
            # The reader blocks in read1() while bytes flow; a true stall
            # (streamlink alive, zero bytes) can't be caught inline, so this
            # thread kills the proc when last_byte_ts goes stale, unblocking the
            # read with EOF. A never-produced byte (last_byte_ts None) is left to
            # the existing dead_serves/EOF path, per the spec. (The consumer-side
            # OBS rebuild is NOT here — it lives in the relay's cursor-progress
            # freeze sampler; see Relay._freeze_tick.)
            while not watchdog_stop.wait(1.0):
                if self.stop or self.advance.is_set():
                    return
                if feed_stalled(self.last_byte_ts, time.monotonic(), stall_s=stall_s):
                    self.log.warning("fan-out stall on %s (>%.0fs) — killing reader",
                                     self.name, stall_s)
                    self._kill_proc()
                    return

        wd = threading.Thread(target=_watchdog, daemon=True)
        wd.start()
        try:
            while not self.stop and not self.advance.is_set():
                chunk = stdout.read1(65536)
                if not chunk:
                    break                    # EOF (ended / 403 / expired) or watchdog kill
                first = self.last_byte_ts is None
                _now = time.monotonic()
                self._max_inbound_gap = update_max_gap(self._max_inbound_gap, self.last_byte_ts, _now)
                self.ring.write(chunk)
                self.last_byte_ts = _now
                if first and on_first_byte is not None:
                    on_first_byte()          # e.g. force OBS to reconnect on a drop-recovery
        finally:
            watchdog_stop.set()
            self._kill_proc()
        return time.monotonic() - serve_started, (self.proc.returncode or 0)

    def run(self):
        while not self.stop:
            ch, i = self.current_channel()
            if not ch:
                self._set_phase("idle")
                time.sleep(3); continue
            self._set_phase("connecting")
            url = feed_url(ch)
            plat = feed_platform(url)
            self.log.info("stint %d (%s) -> %s", i + 1, plat, url)

            local_cmd = None
            if plat == "local":
                if self.ring is None:
                    local_err = LOCAL_NEEDS_FANOUT
                else:
                    local_cmd, local_err, local_note = local_capture_setup(
                        os.environ, sys.platform, local_encoder())
                if local_err:
                    # A configuration problem, not a flaky source: idle with the reason
                    # until the operator reloads/moves the feed, like dead-serve idle.
                    self.log.error("%s", local_err)
                    self.last_error = local_err
                    self._set_phase("idle")
                    while not self.stop and not self.advance.is_set():
                        self.advance.wait(1.0)
                    self.advance.clear()
                    continue
                if local_note:
                    self.log.warning("%s", local_note)
                self.last_error = local_note     # picture-only is shown, never silent
                self.quality = None          # no stale remote resolution on the panel
                token, target, serve_platform = None, None, "local"
            elif plat == "twitch":
                token = twitch_oauth_from_cookies(
                    cookies_for("twitch", self.cookie_dir))      # None for public Twitch (no auth file)
                target, serve_platform = url, "twitch"           # no yt-dlp hop
            else:
                hls, err, ytq = resolve_hls(url, self.cookies, self.log,
                                            quality_ytdlp_fmt(self.quality_tier))
                if self.stop: break
                if self.advance.is_set():
                    self.advance.clear(); continue
                if not hls:
                    self.last_error = err
                    self._set_source_state(classify_source_state(err))
                    time.sleep(RESOLVE_RETRY); continue
                self.last_error = ssai_warning(hls, self.log)  # warn, never block
                token, target, serve_platform = None, hls, "youtube"
                # The actually-served resolution comes from yt-dlp (streamlink only reports the
                # single-variant HLS name "live"); _observe_streamlink_line won't overwrite it.
                self.quality = ytq

            self.log.info("serving stint %d (%s)", i + 1, serve_platform)
            # This re-serve follows an unexpected DROP (not the first serve, not a handover):
            # record it as a discrete recovery event (report/health/log + churn Discord),
            # mode-agnostic (fan-out AND direct-serve). Best-effort — never break the loop.
            if self.dropped and self.on_recovery is not None:
                try:
                    down = (time.time() - self.dropped_since) if self.dropped_since else 0.0
                    self.on_recovery(self.name, i + 1, max(0.0, down), self.source_state)
                except Exception:      # noqa: BLE001 — best-effort telemetry
                    pass
            if self.ring is not None:
                # Force OBS to reconnect once the fresh stream flows, so it re-joins with a
                # clean demuxer instead of splicing onto the stale one (the 2026-07-10
                # fan-out freeze-frame stutter). Fires after a drop AND on any restart with
                # a consumer still attached — the director's /reload or tier change, and a
                # single-feed /next (#614). Not on the first serve, not on the ping-pong
                # handover: there OBS has already dropped the off-air feed.
                tool = "ffmpeg" if local_cmd else "streamlink"
                # The rejoin's wait comes from THIS serve's prefetch size, read from the
                # same serve_flags() the command builder uses — one place, so a tier or
                # platform change can never move the command without moving the wait. A
                # local capture serve has no streamlink flags and therefore no wait.
                _flags = [] if local_cmd else serve_flags(serve_platform, self.quality_tier)
                _recover = self._obs_rejoin_hook(_flags)
                try:
                    serve_elapsed, serve_rc = self._serve_fanout(
                        target, serve_platform, token, on_first_byte=_recover,
                        cmd=local_cmd, tool=tool)
                except FileNotFoundError:
                    self.log.warning("%s not found on PATH — retrying", tool)
                    self.proc = None
                    time.sleep(RETRY_SLEEP); continue
            else:
                cmd = streamlink_serve_cmd(target, self.port, serve_platform, token,
                                           cookies=self.cookies, tier=self.quality_tier)
                try:
                    self.proc = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace",
                        env=external_tool_env(), **_no_window_kwargs())
                    pump = threading.Thread(
                        target=logsetup.pump_subprocess,
                        args=(self.proc.stdout, self.log, "streamlink"),
                        kwargs={"on_line": self._observe_streamlink_line},
                        daemon=True)
                    pump.start()
                    if serve_platform != "youtube":   # YouTube keeps ssai_warning() result as last_error
                        self.last_error = None
                    self._set_phase("serving")
                    self._clear_drop_health()     # live picture -> any prior alarm clears; re-earn served_ok
                    serve_started = time.monotonic()
                    self.proc.wait()
                    serve_elapsed = time.monotonic() - serve_started
                    serve_rc = self.proc.returncode
                except FileNotFoundError:
                    self.log.warning("streamlink not found on PATH — retrying")
                    self.proc = None
                    time.sleep(RETRY_SLEEP); continue
            self._set_phase("connecting")
            # The just-ended serve "earned" served_ok if it lasted long enough —
            # only then can a future drop be classified a genuine live-picture loss
            # (issue #278). A near-instant exit (demo/startup) leaves served_ok False.
            if serve_elapsed >= HEALTH_SERVED_OK_S:
                self.served_ok = True
                self._set_source_state(None)   # confirmed live serve: the drop's cause no longer applies (#495)
                self.dead_serves = 0               # stable live picture -> reset
            # A serving process just exited: flag an unexpected loss (DROP) so the
            # panel/Companion alarm fires — but not on an intentional stop or a
            # handover/reload (both handled just below). Stamp dropped_since on the
            # False->True edge so the heartbeat can apply the 30 s grace.
            was_dropped = self.dropped
            self.dropped = serve_exit_is_drop(self.stop, self.advance.is_set())
            if self.dropped and not was_dropped:
                self.dropped_since = time.time()
            if self.stop: break
            if self.advance.is_set():
                self.advance.clear()
                self.dead_serves = 0               # operator moved/reloaded -> fresh source
                continue
            # #143: an unexpected, near-instant non-zero exit (not a stop/handover,
            # both handled above) means streamlink couldn't bind its port — surface
            # it so /status + the panel stop showing a silent 'connecting'.
            err = feed_fast_exit_error(serve_elapsed, serve_rc)
            if err and local_cmd:
                # The relay ffmpeg must own the card; a second opener (OBS, the Elgato
                # utility, another feed) makes it exit at once. Say so, never go quiet.
                err = LOCAL_DEVICE_BUSY
                self.log.error("local capture: ffmpeg exited after %.1f s (rc=%s) — %s",
                               serve_elapsed, serve_rc, LOCAL_DEVICE_BUSY)
            if err:
                self.last_error = err
            if serve_elapsed < HEALTH_SERVED_OK_S:
                # Resolved OK but the serve died fast (403/expired manifest / VOD).
                self.dead_serves += 1
                # #493: enough consecutive dead serves at the current tier -> auto step
                # down to a lighter tier; the NEXT resolve() picks up the new tier.
                stepped = self.maybe_step_down()
                if stepped and self.on_step_down is not None:
                    try:
                        self.on_step_down(self.name, i + 1, stepped[0], stepped[1])
                    except Exception:        # noqa: BLE001 — best-effort telemetry
                        pass
                if should_idle_dead_serves(self.dead_serves):
                    self._set_phase("idle")
                    self.last_error = ("stint source unavailable — paused after "
                                       f"{self.dead_serves} attempts; /next or /reload to retry")
                    if local_cmd:
                        self.last_error += f" ({LOCAL_DEVICE_BUSY})"
                    # Stop hammering: wait for operator /next or /reload (which set
                    # advance) or shutdown (self.stop). advance wakes us instantly;
                    # the 1 s timeout bounds the stop-check latency.
                    while not self.stop and not self.advance.is_set():
                        self.advance.wait(1.0)
                    continue                       # top of loop re-evaluates; advance/stop handled there
                time.sleep(dead_serve_backoff(self.dead_serves))
                continue
            time.sleep(RETRY_SLEEP)                 # served_ok serve that simply ended normally

    def shutdown(self):
        self.stop = True; self._kill_proc()


OBS_PROBE_INTERVAL_S = 5.0   # min seconds between background OBS reachability probes


def should_probe_obs(last_ts, running, now, interval):
    """True when status() should kick off a fresh OBS reachability probe: none
    already in flight, and the previous probe is older than `interval`. Pure so
    the throttle is unit-testable without a live relay."""
    return not running and (now - last_ts) >= interval


class AvSyncWatcher:
    """Tails OBS's own log and folds the A/V sync disturbances it reports into an
    `av_sync` state (#619).

    OBS is the only component that knows when a source's audio timing broke, and until
    now it told nobody but its log file. This reads that file; it never writes to OBS
    and never acts on what it finds — by the time the line exists OBS has already
    repaired it (see av_sync's module docstring).

    Only lines appended AFTER the watcher starts are read. That is what lets every event
    be stamped with our own clock: OBS writes `HH:MM:SS.mmm` with no date, so parsing its
    timestamp would need the file's date plus midnight-rollover handling for a value the
    tail lag already gives to within a second.

    Best-effort in the strong sense: no OBS, no log directory, a rotated or truncated
    file, a permission error — each of those means no detector, never a broken relay.
    """

    POLL_S = 2.0        # how often new lines are read; a repair is not time-critical
    RESCAN_S = 30.0     # how often to look for a newer log file (OBS opens one per run)

    def __init__(self, log_dir, serving_age, state, lock, log):
        self.log_dir = log_dir
        self.serving_age = serving_age   # feed name -> seconds in `serving`, or None
        self.state = state
        self.lock = lock
        self.log = log
        self.stop = False
        self._warned = False
        # A tail reads a file another process is still writing, so the last read can end
        # mid-line. readline() hands that fragment back as if it were a line, and the
        # remainder arrives as another: a repair split across two polls parsed to
        # nothing TWICE and was lost silently — the one event this exists to catch.
        self._partial = ""

    def _newest_log(self):
        # logsetup.list_logs filters to regular files (newest first) — a FIFO named
        # *.txt in the log directory would otherwise block open() and hang this thread
        # for good. It follows symlinks, so a link to a regular file still passes.
        for path in logsetup.list_logs(self.log_dir):
            if path.endswith(".txt"):
                return path
        return None

    def drain(self, fh):
        """Read every COMPLETE line available right now and fold it in.

        A tail reads a file another process is still writing, so the last read can end
        mid-line: readline() hands that fragment back as if it were a line and the
        remainder arrives as another, so a repair split across two polls parses to
        nothing twice and is lost. The remainder is therefore held until its newline
        arrives. Public because the test drives THIS loop — an earlier version of that
        test rebuilt it and proved only its own copy."""
        while True:
            chunk = fh.readline()
            if not chunk:
                return
            self._partial += chunk
            if not self._partial.endswith("\n"):
                return               # writer is mid-line; wait for the rest
            line, self._partial = self._partial, ""
            self._ingest(line, time.monotonic())

    def _ingest(self, line, now):
        ev = av_sync.parse_obs_log_line(line.rstrip("\n"))
        if ev is None:
            return
        feed = av_sync.feed_for_source(ev.get("source"))
        age = self.serving_age(feed) if feed else None
        with self.lock:
            av_sync.record(self.state, ev, now, age)

    def run(self):
        path, fh, opened_at = None, None, 0.0
        try:
            while not self.stop:
                now = time.monotonic()
                if fh is None or (now - opened_at) >= self.RESCAN_S:
                    newest = self._newest_log()
                    if newest and newest != path:
                        if fh is not None:
                            # A NEW file was created after we started watching, so it
                            # begins with this session: read it whole, not from the end.
                            try: fh.close()
                            except OSError: pass    # closing a rotated handle is best-effort
                            fh = None
                        try:
                            # noqa SIM115: a tail holds its handle across loop turns;
                            # a context manager would close it on every poll.
                            fh = open(newest, encoding="utf-8", errors="replace")  # noqa: SIM115
                            if path is None:
                                fh.seek(0, os.SEEK_END)   # first open: skip the backlog
                            path = newest
                            self.log.info("A/V sync watcher reading %s", newest)
                        except OSError as exc:
                            if not self._warned:
                                self.log.warning("A/V sync watcher cannot read %s (%s) "
                                                 "— no sync disturbance reporting", newest, exc)
                                self._warned = True
                            fh = None
                    opened_at = now
                if fh is not None:
                    try:
                        self.drain(fh)
                    except (OSError, ValueError):
                        # ONLY the file is allowed to end up here. A parse failure would
                        # be filed as a rotation, skip the lines up to the reopen, and
                        # hide itself — so parsing never raises (av_sync returns None).
                        try: fh.close()
                        except OSError: pass    # already gone; we reopen below
                        fh, path = None, None     # rotated or truncated: pick it up again
                        self._partial = ""        # its tail belongs to the old file
                time.sleep(self.POLL_S)
        except Exception:                          # noqa: BLE001 — a detector must never kill the relay
            self.log.exception("A/V sync watcher stopped")
        finally:
            if fh is not None:
                try: fh.close()
                except OSError: pass    # shutting down; nothing left to salvage


class Relay:
    def __init__(self, source, ports, logdir, cookies=None, pov_source=None,
                 pov_port=None, start_stint=1, cookie_dir=None,
                 qual_source=None, mode="race", discord_webhook_url=None,
                 sheet_id=None, event_title_store=None, league_name="",
                 producer_name="", solo=False):
        self.race_source = source
        self.qual_source = qual_source
        self.sheet_id = sheet_id          # league identity, surfaced in /status for takeover
        self.league_name = league_name    # display name injected from the active profile (#236)
        self.producer_name = producer_name  # who runs this machine — on events/status (#317)
        self.event_title_store = event_title_store   # free-text event title for Discord footers (#207)
        # Active schedule is race by default; qualifying only when a qual source
        # exists. self.source (property below) returns whichever is active, so
        # every existing call site (status/next/reload/set_stint/handler) becomes
        # mode-aware for free.
        self.mode = "qualifying" if (mode == "qualifying" and qual_source) else "race"
        self.cookies = cookies
        self.cookie_dir = cookie_dir
        # 0-based DISPLAY row currently on air (drives the HUD stint label + the
        # ON-AIR marker). Equals the on-air feed's pull index in normal operation;
        # diverges one row ahead only during a same-URL back-to-back continuation.
        self.solo = solo
        self.manual_feed_arm = manual_feed_arm_enabled(os.environ)
        if solo:
            # Solo: no Schedule/Qualifying and no A/B ping-pong feeds. POV (below)
            # is the only optional feed; every sheet-driven source is built by main()
            # exactly as endurance. The feed-touching methods guard on self.solo.
            self.on_air_row = 0
            self.A = self.B = None
            self.feeds = {}
        else:
            a_idx, b_idx = slot_start_indices(start_stint, self.active_source().get_rows())
            self.on_air_row = a_idx
            # Feeds read the ACTIVE source via active_items, so a /mode switch re-points
            # both feeds without rebuilding them (the OBS Feed A/B sources never change).
            self.A = Feed("A", ports[0], a_idx, self.active_items, logdir, cookies, cookie_dir=cookie_dir)
            self.B = Feed("B", ports[1], b_idx, self.active_items, logdir, cookies, cookie_dir=cookie_dir)
            # Record every A/B drop-recovery (report/health/log + churn Discord). POV is paused
            # by design → not tracked, no callback.
            self.A.on_recovery = self._record_feed_recovery
            self.B.on_recovery = self._record_feed_recovery
            # #493: auto quality step-down fires a health incident + Discord @here.
            # POV stays pinned-robust: no on_step_down.
            self.A.on_step_down = self._record_feed_step_down
            self.B.on_step_down = self._record_feed_step_down
            self.feeds = {"A": self.A, "B": self.B}
            # Two-stage feed scheduling (#492): when RACECAST_MANUAL_FEED_ARM is set,
            # both A/B feeds start DISARMED (paused) — a URL at the index does not pull
            # until the director arms the feed. Default off = auto-pull unchanged.
            if self.manual_feed_arm:
                self.A.paused = True
                self.B.paused = True
        self.obs_note = None          # last OBS note (None/"" = ok); read by status()
        self.obs_reachable = None     # last LIVE reachability probe; None until first /status
        self._obs_probe_ts = 0.0      # time.time() of the last reachability probe
        self._obs_probe_running = False
        self._obs_lock = threading.Lock()
        self.obs_stats = {}               # last redacted OBS GetStats/GetStreamStatus
        self._obs_last_bytes = None       # for stream_kbps derivation
        self._obs_last_bytes_ts = None
        self.conn_state = {"funnel_ok": None, "tailscale_up": None,
                           "companion_ok": None}
        self.funnel_expected = False      # latched True once the Funnel is seen up
        self.stream_expected = False      # latched True once OBS is seen streaming
                                          # (off-air alarm only fires after this)
        self._stream_active_prev = None   # last bool stream_active, for start/stop events (#317)
        self._stream_started_ts = None    # when OBS last went live, for the stop-line uptime
        # POV is a THIRD, independent feed — not part of the A/B index. Starts
        # paused (off) until the Director calls /pov/reload.
        self.pov_source = pov_source
        self.pov = None
        self.pov_shown = False          # relay-driven PiP visibility (drives the HUD box)
        if pov_source is not None and pov_port is not None:
            self.pov = Feed("POV", pov_port, 0, pov_source.get, logdir, cookies,
                            cookie_dir=cookie_dir)
            self.pov.quality_tier = "robust"     # #493: PiP is always 720p + robust, never auto/exposed
            self.pov.quality_pinned = True
            self.pov.paused = True
        self.fanout = fanout_enabled(os.environ)
        # #537: persistent obs-websocket connections (or a passthrough shim when
        # disabled), routed to by the Relay methods/handlers below instead of
        # connect-per-call. Conn classes come from _OBS_WS_MODULE (the real module,
        # stable even if a test has already swapped the plain `_obs_ws` name to a
        # fake by this point); _RelayObsFacade late-binds every call to whatever
        # `_obs_ws` currently is, routing through the persistent conns only for the
        # real module.
        if _obs_ws is None:
            self._obs = None
        elif obs_ws_persist_enabled(os.environ):
            self._obs = _RelayObsFacade(
                _OBS_WS_MODULE._ObsConn(), _OBS_WS_MODULE._ObsConn())
        else:
            self._obs = _RelayObsFacade(
                _OBS_WS_MODULE._PassthroughConn(), _OBS_WS_MODULE._PassthroughConn())
        self.feed_prebuffer_s = feed_prebuffer_s(os.environ)   # #533: OBS/program-audio trailing depth
        self._stall_signal = feed_stall_signal_enabled(os.environ)   # #535
        self._stall_floor = feed_stall_floor_s(os.environ)           # #535
        self._interval_max_gaps = {}      # #535: last heartbeat's per-feed max inbound gap
        self._served_max_gaps = {}        # #619: same, but None where nothing measured it
        self._jittery_feeds = []          # #535: feeds whose last-interval gap tripped the signal
        self._backlog_warn_s = feed_backlog_warn_s(os.environ)      # #583
        # Automatic backlog shed (2026-09-21): a second reason to pull the SAME OBS-rebuild
        # control the freeze detector owns. Shares _rebuild_guard and _last_freeze_ts.
        self._backlog_shed = feed_backlog_shed_enabled(os.environ)
        self._backlog_shed_ticks = feed_backlog_shed_ticks(os.environ)
        self._backlog_streak = {}      # feed -> consecutive backlogged heartbeats
        self._obs_splice_ts = {}       # feed -> when the relay last rebuilt its OBS input
        self._interval_backlogs = {}      # #583: last heartbeat's per-feed consumer backlog floor
        self._backlogged_feeds = {}       # #583: feed -> floor (s) for feeds past the threshold
        self._av = av_sync.new_state()    # #619: A/V sync disturbances OBS reported
        self._av_lock = threading.Lock()
        self._av_watcher = None
        self.program_audio = program_audio_enabled(os.environ)
        self._fanout_servers = []
        # Auto-failover to the Intermission scene on confirmed on-air feed loss
        # (#378): opt-in via RACECAST_AUTO_FAILOVER; fires once per outage and
        # re-arms on a manual return to the on-air feed scene. Return is manual.
        self.auto_failover = auto_failover_enabled(os.environ)
        self._failed_over = False
        # #495 auto-cover: raise the #378 Standby Cover over an offline on-air source
        # (keeps the HUD). Default-on (RACECAST_OBS_AUTO_COVER); fires once per outage,
        # re-arms on recovery; never fights the manual RED FLAG (owns only what it raised).
        self.auto_cover = auto_cover_enabled(os.environ)
        self._cover_fired = False       # already raised the cover for the current outage
        self._cover_auto_owned = False  # auto raised the cover that is currently shown
        # OBS render-skip counts from the previous heartbeat poll: a recorded diagnostic
        # for the health chart, never a trigger (#582).
        self._prev_render_counts = None   # (skipped, total)
        # #488 cursor-progress freeze detector (its own faster sampler, since a stutter's
        # stall ticks recur every few seconds — finer than the 30 s heartbeat). The only
        # automatic consumer-side rebuild; #582 guards it against repeating uselessly.
        self._freeze_detect = feed_freeze_detect_enabled(os.environ)
        self._freeze_stall_ratio = feed_freeze_stall_ratio(os.environ)
        self._freeze_frac = feed_freeze_frac_threshold(os.environ)
        self._freeze_window = feed_freeze_window(os.environ)
        self._freeze_interval_s = feed_freeze_interval_s(os.environ)
        self._freeze_cooldown = feed_freeze_cooldown_s(os.environ)
        self._last_freeze_ts = None
        self._fz_key = None               # (mode, on-air feed, on-air row) the window belongs to
        self._fz_prev_cursor = None
        self._fz_ratios = []
        self._rebuild_guard = RebuildGuard()   # #582 effectiveness guard for that rebuild
        self._rebuild_stood_down_feed = None   # the feed the guard stood down on
        self._rebuild_lock = threading.Lock()  # sampler thread vs. the director's re-arm
        self._prev_snaps = {}             # feed_key -> last consumer cursor-snap count
        # Live health heartbeat: displayed level (refreshed on every /status and
        # every tick) + the notification baseline (advanced ONLY by the heartbeat
        # tick so a 2 s /status refresh never "consumes" a transition before the
        # tick can push it). Discord push fires on level changes only.
        self.discord_webhook_url = discord_webhook_url
        self.health_level = None
        self.health_reasons = []
        self.health_since = time.time()
        self._notified_level = None
        self._desync_since = None   # monotonic-ish start ts of the raw desync condition
        self._desync = {"active": False}   # the /status desync block (recomputed each tick)
        self._desync_active = False        # last active state, for the log-on-transition
        self._health_lock = threading.Lock()
        self._hb_stop = threading.Event()
        self._recovery_churn_notified = {}   # feed -> ts of the last churn @here (cooldown)
        self.health_store = None  # assigned by bootstrap (Task 13); always exists
        self.timer_store = None  # assigned by bootstrap; sampled best-effort for timer_push
        self._last_prune = 0  # epoch of last health-history prune (daily, in heartbeat)
        self._resource_sampler = resources.ResourceSampler()

    def active_source(self):
        """The schedule the A/B feeds currently serve: qualifying when in
        qualifying mode (and a qual source exists), else the race schedule."""
        return self.qual_source if (self.mode == "qualifying" and self.qual_source) else self.race_source

    def active_items(self):
        """Feed provider: the active schedule's URL list (mode-aware)."""
        return self.active_source().get()

    @property
    def source(self):
        """The active ScheduleSource. A property so status/next/reload/set_stint
        and the /schedule + handover paths all follow the current mode."""
        return self.active_source()

    def start(self):
        if self.fanout:
            live = list(self.feeds.items()) + ([("POV", self.pov)] if self.pov else [])
            for _name, f in live:
                f.ring = FeedRing(FANOUT_RING_BYTES)
                srv = FeedFanoutServer("127.0.0.1", f.port, f.ring,
                                       logging.getLogger("racecast.fanout." + f.name),
                                       prebuffer_s=self.feed_prebuffer_s)
                srv.start()
                f.fanout_server = srv          # #488: the freeze detector reads its consumer snap count
                self._fanout_servers.append(srv)
        for f in self.feeds.values():
            threading.Thread(target=f.run, daemon=True).start()
        if self.pov:
            threading.Thread(target=self.pov.run, daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        threading.Thread(target=self._auto_cover_loop, daemon=True).start()
        self._start_av_watcher()
        if self.fanout:                   # #488 freeze detector only applies to the fan-out demuxer
            threading.Thread(target=self._freeze_sampler_loop, daemon=True).start()

    def _health_facts(self, now):
        """Gather the raw facts aggregate_health() rolls up. Mirrors what
        status() exposes so the pill and the webhook agree. Best-effort: a flaky
        tailscale probe must never raise (default present -> no false alarm)."""
        feeds_down, connecting_long = [], []
        feed_source_states = {}
        live = list(self.feeds.items()) + ([("POV", self.pov)] if self.pov else [])
        for name, f in live:
            if f.paused:
                continue
            # Debounce the raw drop flag (#278): only a served-then-lost feed past
            # the grace window is "down" (red); a within-grace blip or a
            # never-served (demo/startup) feed is a quiet "connecting" (yellow).
            state = feed_health_state(f.dropped, f.dropped_since, f.served_ok, now)
            if state == "down":
                feeds_down.append(name)
            elif state == "connecting":
                # A just-dropped served feed is a silent blip until it has stayed
                # down past the settle window — so a reconnect that self-heals
                # within a heartbeat (fan-out EOF-churn / byte-stall re-serve on a
                # VOD) never pages @here. A never-served connect is unchanged.
                if drop_connecting_notifiable(f.dropped, f.dropped_since, now):
                    connecting_long.append(name)
            elif f.phase == "connecting" and (now - f.phase_since) > HEALTH_CONNECTING_S:
                connecting_long.append(name)
            if f.source_state is not None:
                feed_source_states[name] = f.source_state
        try:
            ts_present = detect_tailscale_ip() is not None
        except Exception:                                # noqa: BLE001 — best effort
            ts_present = True
        st = self.obs_stats or {}
        cs = self.conn_state or {}
        funnel_down = bool(self.funnel_expected) and (cs.get("funnel_ok") is False)
        tpush = None
        tstore = getattr(self, "timer_store", None)
        if tstore is not None:
            try:
                tpush = tstore.summary().get("push")
            except Exception:                            # noqa: BLE001 — best-effort
                tpush = None
        return {"feeds_down": feeds_down, "feeds_connecting_long": connecting_long,
                "cookies_stale": cookie_health(self.cookies, now=now)["stale"],
                "obs_reachable": self.obs_reachable, "tailscale_present": ts_present,
                "stream_active": st.get("stream_active"),
                "stream_expected": bool(self.stream_expected),
                "stream_reconnecting": st.get("stream_reconnecting"),
                "funnel_down": funnel_down,
                "sheet_push_failing": (tpush == "failed"),
                "feed_source_states": feed_source_states,
                "feeds_jittery": list(self._jittery_feeds),
                "rebuilds_stood_down": self._rebuilds_stood_down_fact(st.get("obs_fps")),
                "feeds_backlogged": dict(self._backlogged_feeds),
                "feeds_av_disturbed": self._av_health_fact()}

    def _note_obs_splice(self, feed):
        """Record that the relay just spliced this feed into OBS by rebuilding the input.
        Takes no clock on purpose: its only reader compares against time.time(), and a
        caller passing a monotonic `now` would silently never match."""
        self._obs_splice_ts[feed] = time.time()

    def _serving_age(self, feed):
        """How long ago the relay last spliced a stream into OBS for that feed, or None
        when it is not serving — what tells an EXPECTED sync disturbance from an
        unexplained one (#619). An input rebuild counts: it splices without restarting the
        feed, so the serving age alone would leave our own remedy looking unexplained."""
        f = self.pov if feed == "POV" else self.feeds.get(feed)
        if f is None or f.paused or f.phase != "serving":
            return None
        since = f.phase_since
        spliced = self._obs_splice_ts.get(feed)
        if spliced is not None and spliced > since:
            since = spliced
        return time.time() - since

    def _start_av_watcher(self):
        """Start the A/V sync watcher on OBS's own log directory. Entirely optional:
        without OBS installed there is no directory and the relay simply runs without
        the detector (#619)."""
        try:
            d = logsetup.obs_log_dir(sys.platform)
            if not d or not os.path.isdir(d):
                return
            self._av_watcher = AvSyncWatcher(d, self._serving_age, self._av,
                                             self._av_lock, LOG)
            threading.Thread(target=self._av_watcher.run, daemon=True).start()
        except Exception as exc:                 # noqa: BLE001 — a detector is never fatal
            LOG.warning("A/V sync watcher not started (%s)", exc)

    # The watcher stamps every event with time.monotonic(), so both readers below take
    # their own monotonic reading rather than the wall-clock `now` the /status and
    # heartbeat paths pass around. Mixing the two clocks would silently produce ages of
    # about 1.8 billion seconds.
    def _av_status(self):
        """The /status `av` block: per-feed disturbance counts plus the unattributed
        context lines. Empty when nothing has happened, so a clean event adds no noise."""
        mono = time.monotonic()
        with self._av_lock:
            feeds = av_sync.status_block(self._av, mono)
            ctx = dict(self._av["context"])
        return {"feeds": feeds, "context": ctx} if (feeds or ctx) else {}

    def _av_totals(self):
        """Running A/V disturbance totals for the health snapshot (#619)."""
        with self._av_lock:
            feeds = self._av["feeds"].values()
            return {"av_repairs_total": sum(f["repairs"] for f in feeds),
                    "av_unexplained_total": sum(f["unexplained"] for f in feeds)}

    def _av_health_fact(self):
        """feed -> magnitude in ms for feeds with a RECENT UNEXPLAINED repair. A repair
        right after a restart is the expected cost of that restart, so it never reaches
        the health block (#619)."""
        mono = time.monotonic()
        with self._av_lock:
            return av_sync.health_fact(self._av, mono)

    def _refresh_health(self, now):
        """Recompute + store the DISPLAYED health (level/reasons/since). Does NOT
        touch the notification baseline, so /status can refresh it every 2 s
        without stealing a transition from the heartbeat tick. Also returns a
        `notify_level` with feeds_jittery and feeds_backlogged excluded (#535, #583:
        quiet, display-only yellows that must never page Discord)."""
        facts = self._health_facts(now)
        h = aggregate_health(facts)
        notify_level = aggregate_health({**facts, "feeds_jittery": [],
                                         "feeds_backlogged": {},
                                         "feeds_av_disturbed": {}})["level"]
        with self._health_lock:
            if h["level"] != self.health_level:
                self.health_level = h["level"]
                self.health_since = now
            self.health_reasons = h["reasons"]
        self._compute_desync(now)
        h["notify_level"] = notify_level
        return h

    def _health_snapshot(self, now):
        """A redacted, flat health sample (matches health_store.COLUMNS minus
        ts/kind). No stream URLs / sheet_id — safe to persist and pull."""
        def feed_fields(f):
            return ("stopped" if f.paused else f.phase,
                    1 if (f.dropped and not f.paused) else 0, f.idx + 1)
        if self.solo:
            a_state = a_down = a_stint = None
            b_state = b_down = b_stint = None
            live = None
        else:
            a_state, a_down, a_stint = feed_fields(self.feeds["A"])
            b_state, b_down, b_stint = feed_fields(self.feeds["B"])
            live = self.live_feed()
        ch = cookie_health(self.cookies, now=now)
        tmode, tpush = None, None
        ts = getattr(self, "timer_store", None)
        if ts is not None:
            try:
                summ = ts.summary()
                tmode, tpush = summ.get("mode"), summ.get("push")
            except Exception:  # noqa: BLE001 — sampling is best-effort
                pass
        st = self.obs_stats or {}
        cs = self.conn_state or {}

        def _b(v):
            return None if v is None else (1 if v else 0)

        def _q(f):
            return getattr(f, "quality", None)

        try:
            sys_res = resources.to_health_fields(self._resource_sampler.sample(now))
        except Exception:  # noqa: BLE001 - best-effort; never break the heartbeat
            sys_res = resources.to_health_fields({})   # all-None, keys still present

        return {"ts": now,
                "health_level": self.health_level, "health_reasons": self.health_reasons,
                "feed_a_state": a_state, "feed_a_down": a_down, "feed_a_stint": a_stint,
                "feed_b_state": b_state, "feed_b_down": b_down, "feed_b_stint": b_stint,
                "feed_a_max_gap_s": self._interval_max_gaps.get("A"),
                "feed_b_max_gap_s": self._interval_max_gaps.get("B"),
                "feed_a_backlog_s": self._interval_backlogs.get("A"),
                "feed_b_backlog_s": self._interval_backlogs.get("B"),
                "pov_backlog_s": self._interval_backlogs.get("POV"),
                "pov_state": (None if not self.pov else
                              ("stopped" if self.pov.paused else self.pov.phase)),
                "obs_reachable": (None if self.obs_reachable is None
                                  else (1 if self.obs_reachable else 0)),
                "source_last_ok_age_s": (None if self.solo else self.source.health().get("last_ok_age_s")),
                "source_count": (None if self.solo else self.source.health().get("count")),
                "cookies_present": 1 if self.cookies else 0,
                "cookies_age_h": ch.get("age_h"),
                "cookies_stale": 1 if ch.get("stale") else 0,
                "timer_mode": tmode, "timer_push": tpush,
                "mode": self.mode,
                # live_stint = the DISPLAY stint (who is on screen), continuation-aware
                # via on_air_row_idx() — NOT the physical pull index, so a same-URL
                # back-to-back counts as two distinct stints in the report (#500). The
                # pull index stays reconstructable from feed_a/b_stint + live_feed.
                "live_feed": live, "live_stint": (None if self.solo else self.on_air_row_idx() + 1),
                "desync_active": 1 if self._desync.get("active") else 0,
                # v11 (#619): running totals, so the post-event report can say how
                # often the chain was disturbed and how often nothing explained it.
                **self._av_totals(),
                # v3 OBS stats (already redacted: obs_stats never carries output_bytes)
                "stream_active": _b(st.get("stream_active")),
                "stream_reconnecting": _b(st.get("stream_reconnecting")),
                "stream_kbps": st.get("stream_kbps"),
                "stream_dropped_pct": st.get("stream_dropped_pct"),
                "stream_congestion": st.get("stream_congestion"),
                "obs_cpu_pct": st.get("obs_cpu_pct"),
                "obs_mem_mb": st.get("obs_mem_mb"),
                "obs_fps": st.get("obs_fps"),
                "obs_fps_target": st.get("obs_fps_target"),   # v10: the reference for obs_fps (#586)
                "obs_render_skipped_pct": st.get("obs_render_skipped_pct"),
                "obs_render_skip_rate_pct": (None if (_rsr := self._current_render_skip_rate())
                                             is None else round(_rsr * 100, 3)),
                "obs_disk_free_mb": st.get("obs_disk_free_mb"),
                # v3 connectivity (sheet_push_ok derived from the timer push status)
                "funnel_ok": _b(cs.get("funnel_ok")),
                "tailscale_up": _b(cs.get("tailscale_up")),
                "companion_ok": _b(cs.get("companion_ok")),
                "sheet_push_ok": (None if tpush in (None, "never", "disabled")
                                  else (1 if tpush == "ok" else 0)),
                # v3 feed quality
                "feed_a_quality": (None if self.solo else _q(self.feeds["A"])),
                "feed_b_quality": (None if self.solo else _q(self.feeds["B"])),
                "pov_quality": _q(self.pov) if self.pov else None,
                **sys_res}

    def _discord_post(self, payload, what):
        """Best-effort fire-and-forget POST of a Discord webhook payload; a no-op
        when no webhook is configured. *what* names the event for the failure log.
        Discord is behind Cloudflare, which 403s the default urllib
        "Python-urllib/x.y" User-Agent — without an explicit UA the post silently
        never arrives, so we match the UA the rest of the relay sends."""
        url = self.discord_webhook_url
        if not url:
            return
        try:
            data = json.dumps(payload).encode()
            req = Request(url, data=data, method="POST",
                          headers={"Content-Type": "application/json",
                                   "User-Agent": "racecast-feeds/1.0"})
            urlopen(req, timeout=5).read()   # noqa: S310 — operator-configured webhook
        except Exception as e:                # noqa: BLE001 — best effort
            LOG.warning("Discord %s webhook failed: %s: %s", what, type(e).__name__, e)

    def _event_title(self):
        """The current free-text event title (#207) for Discord footers, or ""."""
        return self.event_title_store.get() if self.event_title_store else ""

    def _send_health_webhook(self, level, reasons, prev):
        """POST a Discord health alert. Fully best-effort — never breaks the
        heartbeat. The footer names this producer (#317)."""
        self._discord_post(
            discord_health_payload(level, reasons, prev, self._event_title(),
                                   self.producer_name),
            "health")

    def _heartbeat_loop(self):
        """Background tick: refresh health and push to Discord on a level change
        only (degradation and recovery). Daemon thread; stops with the process."""
        while not self._hb_stop.is_set():
            now = time.time()
            self._maybe_probe_obs(now)
            self._sample_connectivity()
            self._sample_inbound_gaps()
            self._sample_consumer_backlogs()
            try:
                self._backlog_shed_tick(now)    # reads the classification just sampled
            except Exception as exc:            # noqa: BLE001 — a remedy never breaks the heartbeat
                LOG.debug("backlog shed error (%s)", exc)
            h = self._refresh_health(now)
            if self.health_store is not None:
                try:
                    self.health_store.record_tick(self._health_snapshot(now), now)
                except Exception:  # noqa: BLE001 — sampling is best-effort
                    pass  # never let a store write break the heartbeat
            if self.health_store is not None and (now - self._last_prune) > 86400:
                try:
                    self.health_store.prune(); self._last_prune = now
                except Exception:  # noqa: BLE001 — best-effort
                    pass
            if health_should_notify(self._notified_level, h["notify_level"]):
                self._send_health_webhook(h["notify_level"], self.health_reasons, self._notified_level)
                self._notified_level = h["notify_level"]
            self._maybe_auto_failover(now)
            self._record_render_counts()
            self._record_consumer_overflows(now)
            self._hb_stop.wait(HEARTBEAT_INTERVAL_S)

    def _sample_inbound_gaps(self):
        """#535: read+reset each feed's interval max gap ONCE per heartbeat (the 2 s
        /status poll must never steal the reset), and classify jitter.

        Two maps come out of it, because two readers want different things:
        `_interval_max_gaps` is the raw accumulator value and goes to health-history.db
        unchanged, while `_served_max_gaps` (#619) is what /status may publish and is
        None for an interval in which nothing could have measured anything. The
        accumulator only advances inside the fan-out read loop, so an idle feed, or any
        feed under RACECAST_FEED_FANOUT=0, reads 0.0. Publishing that would say "the
        source never stuttered" about a feed nobody measured, and it would survive a
        whole heartbeat after the feed goes on air.

        A feed is judged by its state at the tick, which is the same approximation
        _sample_consumer_backlogs makes: a feed that stopped just before the tick loses
        its last reading, and one that started mid-interval publishes a partial one."""
        gaps, served, jittery = {}, {}, []
        for name, f in self.feeds.items():
            g = f.take_max_inbound_gap()   # always take: a stopped feed resets too
            gaps[name] = g
            measured = (not f.paused and f.phase == "serving"
                        and getattr(f, "fanout_server", None) is not None)
            served[name] = g if measured else None
            if (self._stall_signal and not f.paused and f.phase == "serving"
                    and feed_inbound_degraded(g, self.feed_prebuffer_s, self._stall_floor)):
                jittery.append(name)
        self._interval_max_gaps = gaps
        self._served_max_gaps = served
        self._jittery_feeds = jittery

    def _sample_consumer_backlogs(self):
        """#583: read+reset each fan-out server's interval backlog floor ONCE per heartbeat
        (the 2 s /status poll reads the live value and never resets), and classify the
        feeds whose consumer fell behind the live edge.

        `_backlogged_feeds` is no longer observability only: `_backlog_shed_tick` runs
        immediately after this in the same heartbeat and rebuilds the on-air feed's OBS
        input off it (2026-09-21). Keep the two adjacent and in this order — the shed reads
        the classification this call just produced."""
        floors, lagging = {}, {}
        live = list(self.feeds.items()) + ([("POV", self.pov)] if self.pov else [])
        for name, f in live:
            srv = getattr(f, "fanout_server", None)
            if srv is None:
                continue
            try:
                reaped = srv.reap_superseded(time.monotonic())   # before the floor is taken
                if reaped:
                    LOG.info("feed %s: closed %d abandoned consumer connection(s) "
                             "OBS left open after an input rebuild", name, reaped)
            except Exception as exc:    # noqa: BLE001 — a reaper never breaks the tick
                LOG.debug("consumer reap on %s failed (%s)", name, exc)
            fl = srv.take_backlog_floor(time.monotonic())   # always take: a stopped feed resets too
            if f.paused or f.phase != "serving":
                floors[name] = None                  # no live edge to be behind
                continue
            floors[name] = None if fl is None else round(fl, 1)
            if feed_backlog_degraded(fl, self.feed_prebuffer_s, self._backlog_warn_s):
                lagging[name] = floors[name]
        self._interval_backlogs = floors
        self._backlogged_feeds = lagging

    def _backlog_status(self, name, f):
        """/status fields for one feed (#583): the live consumer backlog and whether it is
        backlogged: the last heartbeat classified it AND the live value is still past the
        threshold, so the panel pill stops being amber as soon as OBS has caught up. Also
        what a RESET would discard (#587), from the same live value. Only a serving feed
        has a live edge to be behind; a stopped or connecting one reports None."""
        srv = getattr(f, "fanout_server", None)
        serving = srv is not None and not f.paused and f.phase == "serving"
        live = None
        if serving:
            try:
                live = srv.consumer_backlog(time.monotonic())
            except Exception:                   # noqa: BLE001 — best-effort
                live = None
        snaps = None
        if srv is not None and hasattr(srv, "consumer_health"):
            try:
                snaps = srv.consumer_health(time.monotonic())[1]
            except Exception:                   # noqa: BLE001 — best-effort
                snaps = None
        return {"backlog_s": None if live is None else round(live, 1),
                "backlogged": (name in self._backlogged_feeds and feed_backlog_degraded(
                    live, self.feed_prebuffer_s, self._backlog_warn_s)),
                "reset_discards_s": reset_discards_s(live, self.feed_prebuffer_s),
                # #614: cumulative cursor snaps of the attached consumers (the ring lapped
                # them); `obs benchmark` marks a window with new snaps as contaminated
                "consumer_snaps": snaps,
                # #619: the inbound side of the same interval (#535). This is the
                # largest gap between bytes arriving from the source. With backlog_s and
                # consumer_snaps, `obs benchmark` can tell a bursty source from a
                # consumer that fell behind, through one scripted restart.
                #
                # This reads the heartbeat's last value, never take_max_inbound_gap().
                # That call resets the accumulator, so a 2 s /status poll would swallow
                # the interval the heartbeat is about to classify.
                #
                # Gated on `serving`, NOT on `live is not None` like backlog_s. The two
                # differ by one thing: consumer_backlog() answers None while no consumer
                # is attached, which is every OBS source rebuild, including the #614
                # rejoin after a restart. The gap is measured on the SOURCE side by the
                # fan-out read loop, so it stays valid while OBS is away, and blanking it
                # there would hand `obs benchmark` a run of Nones at the start of a
                # window. Its guard skips a LEADING run, so that run would let the
                # pre-restart reading through as if measured in-window.
                # `_served_max_gaps` (not `_interval_max_gaps`) already answers None for
                # an interval nothing measured. POV is in neither map, because the
                # heartbeat walks self.feeds (A and B only), so it reports None here too.
                "inbound_max_gap_s": (self._served_max_gaps.get(name)
                                      if serving else None)}

    def _current_render_skip_rate(self):
        """Per-interval OBS render-skip rate (0..1) from obs_stats vs the previous heartbeat's
        raw counts, or None (no prev / no counts). Does NOT advance the prev counts — the
        heartbeat's _record_render_counts advances them once per tick. The cumulative
        obs_render_skipped_pct barely moves during a spike, so this delta rate is the signal
        the drift shows up in (#488) — recorded into the health history + charted."""
        st = self.obs_stats or {}
        skipped = st.get("obs_render_skipped_frames")
        total = st.get("obs_render_total_frames")
        prev = self._prev_render_counts
        if skipped is None or total is None or prev is None:
            return None
        d_total = total - prev[1]
        if d_total <= 0:                       # OBS idle/paused this interval — not drifting
            return 0.0
        return (skipped - prev[0]) / d_total

    def _record_render_counts(self):
        """Advance the previous OBS render counts once per heartbeat, so the health
        snapshot can derive the per-interval render-skip rate. A diagnostic only: the
        rate never triggers an action (#582 removed the #488 render-skip auto-resync,
        which had not run since the freeze detector became the default)."""
        st = self.obs_stats or {}
        skipped, total = st.get("obs_render_skipped_frames"), st.get("obs_render_total_frames")
        if skipped is not None and total is not None:
            self._prev_render_counts = (skipped, total)

    def _record_event(self, now, event_type, label, metadata):
        """Record a discrete health event (health monitor + post-event report).
        Best-effort; never raises into a sampler or the heartbeat."""
        if self.health_store is None:
            return
        try:
            self.health_store.record_event(now, event_type, label=label,
                                           producer=self.producer_name, metadata=metadata)
        except Exception:                       # noqa: BLE001 — best-effort
            pass

    def _record_consumer_overflows(self, now):
        """Record each fan-out ring lap under OBS as a `fanout_overflow` incident (#582).
        Once part of the freeze sampler's trigger (26 rebuilds on 2026-08-28); now a record
        only, read once per heartbeat so it needs no OBS round-trip."""
        for key, f in self.feeds.items():
            snaps = self._feed_snaps(f)
            prev = self._prev_snaps.get(key)
            self._prev_snaps[key] = snaps
            if not consumer_overflowed(prev, snaps):
                continue
            n = snaps - prev
            LOG.warning("Feed %s consumer overflow — the ring lapped OBS %d time(s); "
                        "OBS is reading slower than real time", key, n)
            self._record_event(now, "fanout_overflow",
                               f"Feed {key} consumer overflow — OBS fell a full ring behind "
                               f"the live edge ({n}x)",
                               {"feed": key, "stint": f.idx + 1, "snaps": n})

    def rebuild_guard_status(self):
        """The #582 stand-down state for /status and the Director Panel."""
        with self._rebuild_lock:
            stood = self._rebuild_guard.stood_down
            return {"stood_down": stood,
                    "feed": self._rebuild_stood_down_feed if stood else None}

    def _rebuilds_stood_down_fact(self, fps):
        """aggregate_health's `rebuilds_stood_down` fact: {feed: OBS fps} or {}."""
        st = self.rebuild_guard_status()
        return {st["feed"]: fps} if st["stood_down"] else {}

    def rearm_rebuild_guard(self, reason, now=None):
        """Lift a #582 stand-down (the next stint change, or the director once a cause is
        found). Returns True when a stand-down was lifted. Also clears a half-judged
        rebuild, so the new stint starts with a clean streak."""
        with self._rebuild_lock:
            was = self._rebuild_guard.stood_down
            feed = self._rebuild_stood_down_feed
            self._rebuild_guard.rearm()
            self._rebuild_stood_down_feed = None
        if was:
            now = time.time() if now is None else now
            live = self.live_feed()
            f = self.feeds.get(live)
            LOG.info("auto-rebuild re-armed (%s; was stood down on Feed %s)", reason, feed)
            self._record_event(now, "obs_rebuild_rearmed",
                               f"Auto-rebuild re-armed ({reason})",
                               {"feed": live, "stint": None if f is None else f.idx + 1,
                                "reason": reason})
        return was

    def _feed_snaps(self, f):
        """Total consumer cursor-snaps on a feed's fan-out server (the drift-class
        discontinuity counter, #488), or None when unavailable. Best-effort."""
        srv = getattr(f, "fanout_server", None)
        if srv is None:
            return None
        try:
            _stuck, snaps = srv.consumer_health(time.time())
            return snaps
        except Exception:                       # noqa: BLE001 — best-effort
            return None

    def _freeze_sampler_loop(self):
        """#488 cursor-progress freeze detector: a faster-than-heartbeat sampler (see
        _freeze_tick). Best-effort daemon; any OBS/network error is swallowed so the
        sampler never crashes the relay."""
        while not self._hb_stop.is_set():
            self._hb_stop.wait(self._freeze_interval_s)
            if self._hb_stop.is_set():
                break
            try:
                self._freeze_tick(time.time())
            except Exception as exc:            # noqa: BLE001 — best-effort, never crash the sampler
                LOG.debug("freeze sampler error (%s)", exc)

    def _freeze_tick(self, now):
        """One freeze-sampler step: watch OBS's mediaCursor for the ON-AIR fan-out feed and
        rebuild its OBS input when the cursor stalls — a stale-demuxer freeze/stutter the
        render-skip signal is blind to (measured live 2026-07-15).

        #582 effectiveness guard: the first full window after a rebuild says whether it
        helped. After REBUILD_GUARD_MAX_ATTEMPTS ineffective rebuilds in a row the sampler
        stands down (yellow health, plain text) instead of rebuilding forever; the next
        stint change or the director's re-arm lifts it."""
        if not self._freeze_detect or _obs_ws is None:
            return
        live = self.live_feed()
        f = self.feeds.get(live)
        # The displayed on-air row, not the pull index: a same-URL continuation moves the
        # row while the pull stays parked, and a mode switch re-points Feed A.
        key = (self.mode, live, None if f is None else self.on_air_row_idx())
        if key != self._fz_key:                 # stint change -> fresh window, lift a stand-down
            self._fz_prev_cursor = None; self._fz_ratios = []; self._fz_key = key
            self.rearm_rebuild_guard("stint change", now)
        # Only judge a SERVING feed (bytes flowing). A stopped/connecting feed with a
        # frozen cursor is expected, not a fault — reset the window and skip.
        if f is None or f.paused or f.phase != "serving":
            self._fz_prev_cursor = None; self._fz_ratios = []
            return
        cursors, _note = self._obs.feed_media_cursors(ports=[f.port])
        cur = cursors.get(f.port)
        ratio = cursor_progress_ratio(self._fz_prev_cursor, cur, self._freeze_interval_s)
        self._fz_prev_cursor = cur
        if ratio is not None:
            self._fz_ratios = (self._fz_ratios + [ratio])[-self._freeze_window:]
        if len(self._fz_ratios) < self._freeze_window:
            return                              # debounce: judge full windows only
        frac = stall_fraction(self._fz_ratios, stall_ratio=self._freeze_stall_ratio)
        with self._rebuild_lock:
            stood_down = self._rebuild_guard.on_window(frac, frac_threshold=self._freeze_frac)
            if stood_down:
                self._rebuild_stood_down_feed = live
            attempts = self._rebuild_guard.ineffective("freeze")
            since = None if self._last_freeze_ts is None else now - self._last_freeze_ts
            fire = (self._rebuild_guard.allows("freeze")
                    and freeze_decision(frac, since, frac_threshold=self._freeze_frac,
                                        cooldown_s=self._freeze_cooldown))
            if fire:
                self._last_freeze_ts = now      # claim the cooldown before releasing the lock
                self._rebuild_guard.on_fire("freeze")
        if stood_down:
            LOG.warning("Feed %s auto-rebuild stood down — %d OBS rebuilds did not clear the "
                        "stall (stall_fraction=%.2f); re-arms at the next stint change or "
                        "from the Director Panel (#582)", live, attempts, frac)
            self._record_event(now, "obs_rebuild_stood_down",
                               f"Feed {live} auto-rebuild stood down after {attempts} "
                               f"ineffective rebuilds",
                               {"feed": live, "stint": f.idx + 1, "attempts": attempts})
        if not fire:
            return
        LOG.warning("freeze auto-reconnect %s — stall_fraction=%.2f — rebuilding OBS input "
                    "(#488)", live, frac)
        f._obs_reconnect()                      # the RESET primitive, threaded + best-effort
        self._note_obs_splice(live)             # so the A/V detector does not flag our own work
        self._record_event(now, "obs_rebuild",
                           f"Feed {live} OBS input rebuilt (stall fraction {frac:.2f})",
                           {"feed": live, "stint": f.idx + 1, "stall_fraction": round(frac, 2)})
        self._fz_prev_cursor = None; self._fz_ratios = []

    def _backlog_shed_tick(self, now):
        """Rebuild the ON-AIR feed's OBS input when its consumer stays behind the live
        edge, so the picture returns without a director pressing RESET. Heartbeat only,
        right after the classification it reads; errors are swallowed there.

        A second REASON on the freeze detector's control, not a second automation: same
        rebuild, same RebuildGuard, same cooldown. On-air feed only (an off-air one has no
        consumer under close_when_inactive); POV is out. A backlog that keeps GROWING is
        not fixed here — three attempts, then the guard stands down. See
        docs/superpowers/specs/2026-09-21-automatic-backlog-shed-design.md."""
        if not self._backlog_shed or _obs_ws is None or not self.fanout:
            return
        live = self.live_feed()
        f = self.feeds.get(live)
        degraded = live in self._backlogged_feeds
        # An unmeasurable round consumes no verdict; it waits.
        measured = self._interval_backlogs.get(live) is not None or degraded
        with self._rebuild_lock:
            stood_down = self._rebuild_guard.judge(degraded if measured else None,
                                                   reason="backlog")
            attempts = self._rebuild_guard.ineffective("backlog")
        if stood_down:
            self._rebuild_stood_down_feed = live
            LOG.warning("Feed %s backlog shed stood down — %d OBS rebuilds did not bring the "
                        "output back to the live edge, so the relay has stopped trying. The "
                        "picture stays behind live until the next stint change or a re-arm "
                        "from the Director Panel", live, attempts)
            self._record_event(now, "backlog_shed_stood_down",
                               f"Feed {live} backlog shed stood down after {attempts} "
                               f"ineffective rebuilds", {"feed": live, "attempts": attempts})
        # A feed that is not serving has no live edge to be behind: reset its streak so a
        # handover does not carry an old one into the next stint.
        if f is None or f.paused or f.phase != "serving" or not degraded:
            self._backlog_streak[live] = 0
            return
        streak = self._backlog_streak[live] = self._backlog_streak.get(live, 0) + 1
        with self._rebuild_lock:
            since = None if self._last_freeze_ts is None else now - self._last_freeze_ts
            fire = (self._rebuild_guard.allows("backlog")
                    and backlog_shed_decision(streak, since,
                                              min_streak=self._backlog_shed_ticks,
                                              cooldown_s=self._freeze_cooldown))
            if fire:
                self._last_freeze_ts = now      # claim the cooldown before releasing the lock
                self._rebuild_guard.on_fire("backlog")
        if not fire:
            return
        behind = self._backlogged_feeds[live]   # degraded, so the classification holds one
        LOG.warning("backlog shed %s — output %.1f s behind live — rebuilding OBS input",
                    live, behind)
        f._obs_reconnect()                      # the RESET primitive, threaded + best-effort
        self._note_obs_splice(live)             # so the A/V detector does not flag our own work
        self._backlog_streak[live] = 0
        self._record_event(now, "backlog_shed",
                           f"Feed {live} OBS input rebuilt to shed a {behind:.1f} s backlog",
                           {"feed": live, "backlog_s": behind})

    def _maybe_auto_failover(self, now):
        """Auto-switch OBS to the Intermission scene when the ON-AIR feed is
        confirmed down (#378). Best-effort: never raises, never blocks. Opt-in
        (RACECAST_AUTO_FAILOVER); only fires while OBS is still on the on-air feed
        scene; fires once + notifies loudly; a manual return to the feed scene
        re-arms it. Return to the feed is always the producer's call."""
        if self.solo or not self.auto_failover or _obs_ws is None:
            return
        live = self.live_feed()
        f = self.feeds[live]
        on_air_down = (not f.paused and
                       feed_health_state(f.dropped, f.dropped_since, f.served_ok, now) == "down")
        # Nothing to do unless we are either down (maybe flip) or latched (maybe re-arm).
        if not on_air_down and not self._failed_over:
            return
        on_air_scene = getattr(_obs_ws, "STINT_SCENE", "Stint")
        intermission = getattr(_obs_ws, "INTERMISSION_SCENE", "Intermission")
        try:
            scene, note = self._obs.get_current_program_scene()
        except Exception as e:                            # noqa: BLE001 — best effort
            self.obs_note = f"{type(e).__name__}: {e}"
            return
        if scene is None:                                 # OBS unreachable — one note, no crash
            self.obs_note = note or self.obs_note
            return
        # Re-arm once the producer has manually returned to the on-air feed scene.
        if self._failed_over and scene == on_air_scene:
            self._failed_over = False
        if not should_failover(self.auto_failover, on_air_down, scene,
                               on_air_scene=on_air_scene,
                               already_failed_over=self._failed_over):
            return
        ok, note = self._obs.set_current_program_scene(intermission)
        if not ok:                                        # leave un-latched so the next tick retries
            self.obs_note = note or self.obs_note
            return
        self._failed_over = True
        LOG.warning("Auto-failover: on-air feed %s confirmed down -> switched OBS to %s",
                    live, intermission)
        self._discord_post(
            discord_failover_payload(live, intermission, self._event_title(),
                                     self.producer_name),
            "auto-failover")

    def _auto_cover_loop(self):
        """Fast tick (AUTO_COVER_POLL_S) that auto-raises/lowers the Standby Cover on an
        offline on-air source (#495). Separate from the 30 s health heartbeat because
        covering black is time-sensitive. Daemon; stops with the process."""
        while not self._hb_stop.is_set():
            try:
                self._maybe_auto_cover(time.time())
            except Exception as exc:  # noqa: BLE001 — best-effort; never break the tick loop
                LOG.debug("auto-cover tick error (ignored): %s", exc, exc_info=True)
            self._hb_stop.wait(AUTO_COVER_POLL_S)

    def _maybe_auto_cover(self, now):
        """Auto-raise the #378 Standby Cover over the ON-AIR picture when that feed's
        classified source_state (#502) is offline/ended, and lower it on confirmed-live
        recovery — keeping the HUD. Default-on; fires once per outage, re-arms on
        recovery, never fights the manual RED FLAG. Best-effort: never raises."""
        if _obs_ws is None:
            return
        f = self.feeds[self.live_feed()]
        ss, off_since = f.source_state, f.offline_since
        if ss is None:
            self._cover_fired = False        # re-arm for the next outage (memory-only)
        # Cheap gate: touch OBS only when a raise or a lower could actually be pending.
        maybe_raise = (self.auto_cover and ss in ("not_live_yet", "ended")
                       and off_since is not None and (now - off_since) >= AUTO_COVER_SETTLE_S
                       and not self._cover_fired)
        maybe_lower = (self._cover_auto_owned and ss is None)
        if not maybe_raise and not maybe_lower:
            return
        on_air_scene = getattr(_obs_ws, "STINT_SCENE", "Stint")
        state, note = self._obs.read_obs_state([(on_air_scene, STANDBY_COVER_SOURCE)], [])
        if state is None:                    # OBS unreachable — one note, retry next tick
            self.obs_note = note or self.obs_note
            return
        scene = state.get("scene")
        src0 = (state.get("sources") or [{}])[0] or {}
        cover_shown = bool(src0.get("enabled"))
        action = auto_cover_action(self.auto_cover, ss, off_since, now, AUTO_COVER_SETTLE_S,
                                   cover_shown, self._cover_auto_owned, self._cover_fired,
                                   scene, on_air_scene=on_air_scene)
        if action == "raise":
            ok, note = self._obs.set_scene_item_enabled(on_air_scene, STANDBY_COVER_SOURCE, True)
            if not ok:                       # not latched -> retry next tick
                self.obs_note = note or self.obs_note
                return
            self._cover_fired = True
            self._cover_auto_owned = True
            LOG.warning("Auto-cover: on-air feed %s source %s -> raised Standby Cover (#495)",
                        f.name, ss)
        elif action == "lower":
            ok, note = self._obs.set_scene_item_enabled(on_air_scene, STANDBY_COVER_SOURCE, False)
            if not ok:
                self.obs_note = note or self.obs_note
                return
            self._cover_auto_owned = False
            LOG.info("Auto-cover: on-air source recovered -> lowered Standby Cover (#495)")
        elif maybe_raise and cover_shown:
            # A cover is already up (the director raised it) during this outage. Adopt the
            # outage as handled so we neither double-raise nor re-poll OBS every tick — but
            # do NOT take ownership (auto never lowers a manually-raised cover).
            self._cover_fired = True
        # Recovery reconciliation: once the source is live again we own no cover, even if
        # it was already hidden manually (prevents a stale-owned OBS re-poll loop).
        if ss is None:
            self._cover_auto_owned = False

    def status(self):
        now = time.time()
        self._maybe_probe_obs(now)
        if self.solo:
            return self._solo_status(now)
        sched = self.source.get()
        out = {"schedule_len": len(sched), "cookies": bool(self.cookies),
               "cookies_health": cookie_health(self.cookies, now=now),
               "mode": self.mode, "auto_cover_active": bool(self._cover_auto_owned),
               # #614: the trailing join mark a consumer rejoins at. `racecast obs
               # benchmark` derives its prefetch wait from it, so it must read the value
               # this relay actually runs with, not assume the default.
               "feed_prebuffer_s": self.feed_prebuffer_s,
               "source": self.source.health(), "feeds": {}}
        if self.qual_source:
            out["qualifying"] = {"active": self.mode == "qualifying",
                                 "source": self.qual_source.health()}
        for k, f in self.feeds.items():
            ch, i = f.current_channel()
            out["feeds"][k] = {"port": f.port, "index": i, "stint": i + 1,
                               "channel": ch,
                               "platform": feed_platform(feed_url(ch)) if ch else None,
                               "state": "stopped" if f.paused else f.phase,
                               "armed": not f.paused,
                               "state_age_s": round(now - f.phase_since, 1),
                               "down": f.dropped and not f.paused,
                               "last_error": f.last_error,
                               "source_state": f.source_state,
                               "profile": f.quality_tier,
                               "pinned": f.quality_pinned,
                               "quality": getattr(f, "quality", None),
                               **self._backlog_status(k, f)}
        if self.pov:
            raw = (self.pov_source.get()[:1] or [None])[0] if self.pov_source else None
            out["pov"] = {"port": self.pov.port, "url": raw,
                          "name": self.pov_name(),
                          "shown": self.pov_shown,
                          "state": "stopped" if self.pov.paused else self.pov.phase,
                          "state_age_s": round(now - self.pov.phase_since, 1),
                          "down": self.pov.dropped and not self.pov.paused,
                          "source": self.pov_source.health() if self.pov_source else None,
                          **self._backlog_status("POV", self.pov)}
        out["obs"] = {"reachable": self.obs_reachable, "note": self.obs_note}
        # On-air feed/stint + league identity for producer takeover (#takeover):
        # the on-air feed is the lower-index one (live_feed); the stint is the
        # DISPLAY row (on_air_row_idx), not the feed's physical pull index — during
        # a same-URL back-to-back continuation the display advances one row ahead
        # of the still-parked pull, and a takeover/health-monitor consumer must
        # resume/show that displayed stint, not the stale pull row.
        live = self.live_feed()
        out["live"] = {"feed": live, "stint": self.on_air_row_idx() + 1, "mode": self.mode}
        out["manual_feed_arm"] = self.manual_feed_arm
        out["league"] = {"sheet_id": self.sheet_id, "name": self.league_name}
        out["producer"] = self.producer_name   # who runs this machine (#317 — for takeover)
        self._refresh_health(now)            # keep the displayed level fresh (2 s poll)
        out["health"] = {"level": self.health_level, "reasons": self.health_reasons,
                         "since_s": round(now - self.health_since, 1)}
        out["desync"] = self._desync
        # NOT part of `desync` above: that one is the ping-pong stint mismatch (#494).
        # This is A/V sync (#619). Two different states, and sharing a name would make
        # the panel show one under the other's label.
        av = self._av_status()
        if av:
            out["av"] = av
        out["rebuild_guard"] = self.rebuild_guard_status()
        return out

    def _solo_status(self, now):
        """Status payload for solo mode: no A/B schedule/feeds. POV + OBS + health +
        league identity (the console/panel read these); mirrors the tail of status()."""
        out = {"mode": "solo", "solo": True, "feeds": {},
               "cookies": bool(self.cookies),
               "cookies_health": cookie_health(self.cookies, now=now)}
        if self.pov:
            raw = (self.pov_source.get()[:1] or [None])[0] if self.pov_source else None
            out["pov"] = {"port": self.pov.port, "url": raw,
                          "name": self.pov_name(), "shown": self.pov_shown,
                          "state": "stopped" if self.pov.paused else self.pov.phase,
                          "state_age_s": round(now - self.pov.phase_since, 1),
                          "down": self.pov.dropped and not self.pov.paused,
                          "source": self.pov_source.health() if self.pov_source else None,
                          **self._backlog_status("POV", self.pov)}
        out["obs"] = {"reachable": self.obs_reachable, "note": self.obs_note}
        out["live"] = {"feed": None, "stint": None, "mode": "solo"}
        out["league"] = {"sheet_id": self.sheet_id, "name": self.league_name}
        out["producer"] = self.producer_name
        self._refresh_health(now)
        out["health"] = {"level": self.health_level, "reasons": self.health_reasons,
                         "since_s": round(now - self.health_since, 1)}
        return out

    def live_feed(self):
        """The on-air feed = the one on the lower (earlier) stint index."""
        if self.solo:
            return None
        return "A" if self.A.idx <= self.B.idx else "B"

    def on_air_row_idx(self):
        """0-based schedule row currently ON SCREEN — drives the HUD stint label
        and the ON-AIR marker on the cockpit / stint-plan / Race Control views.
        Equals the on-air feed's pull index in normal operation; during a same-URL
        back-to-back continuation it sits one row ahead of the still-parked pull.
        Clamped to the active schedule."""
        if self.solo:
            return 0
        n = len(self.source.get())
        if not n:
            return 0
        return max(0, min(self.on_air_row, n))

    def live_row_map(self):
        """{row_index: feed_key} for the schedule-highlight consumers: the on-air
        feed is keyed by the DISPLAY row (on_air_row_idx), the off-air feed by its
        physical pull index. Equals {f.idx: key} in normal operation; during a
        continuation the on-air marker follows the displayed stint. The on-air
        entry is written last so it wins any (degenerate) index collision."""
        if self.solo:
            return {}
        live = self.live_feed()
        off = "B" if live == "A" else "A"
        row_map = {self.feeds[off].idx: off}
        row_map[self.on_air_row_idx()] = live
        return row_map

    def live_after_next(self):
        """Which feed will be on air after the next /next: the one NOT advanced."""
        return "B" if self.live_feed() == "A" else "A"

    def _feed_serving(self, f):
        """A feed is 'delivering a stable picture' when its process is up and it is
        neither dropped nor paused. Used only for desync detection."""
        return f.phase == "serving" and not f.dropped and not f.paused

    def _compute_desync(self, now):
        """Recompute the panel-local desync flag (never mutates feeds/OBS). The
        index-designated on-air feed (live_feed) not delivering while the other is
        = a ping-pong desync; debounced by HEALTH_CONNECTING_SETTLE_S. Stores the
        /status block in self._desync and logs on the active transition."""
        if self.manual_feed_arm:
            # Manual mode intentionally disarms feeds; the index-derived desync
            # predicate would false-positive during arm-before-cut. Off here (#492).
            with self._health_lock:
                self._desync_active = False
                self._desync_since = None
                self._desync = {"active": False}
            return self._desync
        live = self.live_feed()
        off = "B" if live == "A" else "A"
        raw = ping_pong_desynced(self._feed_serving(self.feeds[live]),
                                 self._feed_serving(self.feeds[off]))
        with self._health_lock:
            active, self._desync_since = desync_settled(
                raw, self._desync_since, now, HEALTH_CONNECTING_SETTLE_S)
            block = {"active": active}
            if active:
                block["since_s"] = round(now - self._desync_since, 1)
                block["serving_feed"] = off
                block["suggested_stint"] = self.feeds[off].idx + 1
            if active and not self._desync_active:
                LOG.warning("ping-pong desync: on-air feed %s not delivering while %s "
                            "serves stint %d — resync suggested", live, off,
                            self.feeds[off].idx + 1)
            elif not active and self._desync_active:
                LOG.info("ping-pong desync cleared")
            self._desync_active = active
            self._desync = block
        return block

    def splitscreen_state(self):
        """State for the /splitscreen overlay. Feed A is always the Splitscreen
        scene's left half, Feed B the right; the overlay labels the on-air feed
        CURRENT and the other NEXT. In qualifying mode only Feed A is used, so
        NEXT is hidden (next_active False)."""
        return {"current": self.live_feed(),
                "next_active": self.mode != "qualifying",
                "mode": self.mode}

    def pov_active(self):
        """True when the POV PiP is shown on screen (relay-driven, reflected to
        OBS by /pov/toggle|show|hide). Drives the HUD: the whole POV box — frame
        and name — shows only while the PiP is on air."""
        return self.pov_shown

    def pov_name(self):
        """The POV name from the POV tab's one data row (the 'name' column),
        or '' when there is no POV source / row."""
        if not self.pov_source:
            return ""
        rows = self.pov_source.get_rows()
        return rows[0][1] if rows else ""

    def set_pov_shown(self, shown):
        """Show/hide the POV PiP on screen: record the relay state (drives the
        HUD box) and reflect it into OBS best-effort. Returns the new state."""
        self.pov_shown = bool(shown)
        self._reflect_pov(self.pov_shown)
        return {"shown": self.pov_shown}

    def pov_toggle(self):
        return self.set_pov_shown(not self.pov_shown)

    def _reflect_pov(self, shown):
        """Enable/disable the OBS 'Feed POV' scene item off-thread; never blocks
        the HTTP response, never raises. Records the note for /status (mirrors
        _reflect)."""
        if _obs_ws is None:
            return

        def run():
            _ok, note = self._obs.set_scene_item_enabled(
                _obs_ws.STINT_SCENE, _obs_ws.POV_SOURCE, shown)
            self.obs_note = note or None
        threading.Thread(target=run, daemon=True).start()

    def live_schedule_row(self):
        """{"streamer", "stint"} for the stint currently ON SCREEN, or None when it
        idles past the schedule end. Drives the handover HUD auto-write (issue
        #112) and follows a same-URL continuation label."""
        if self.solo:
            return None
        return live_schedule_row(self.source.get_rows(), self.on_air_row_idx())

    def obs_audio_plan(self):
        """(audio, extra_mute) for the OBS intent planners (#593): which feed carries
        the local capture right now, and whether this machine manages the commentary
        mic at all — only one with a capture card (RACECAST_CAPTURE) does."""
        # Solo sets RACECAST_CAPTURE as well, but there the mic ships hot as the main
        # audio and has no feed pair to follow: never manage it.
        mic = (_OBS_WS_MODULE.COMMENTARY_MIC_INPUT
               if (os.environ.get("RACECAST_CAPTURE") or "").strip()
               and not getattr(self, "solo", False) else None)
        try:
            local = {f for f, feed in self.feeds.items()
                     if is_local_source(feed.current_channel()[0])}
        except Exception:                    # noqa: BLE001 — runs in the handover's caller
            local = set()                    # unknown -> the mic stays closed
        return _OBS_WS_MODULE.feed_audio_plan(local, mic=mic)

    def _reflect(self, live, cut):
        """Push the on-air feed (A/B) into OBS off-thread; never blocks the HTTP
        response, never raises. Records the note for /status."""
        if _obs_ws is None:
            return
        # Snapshot now: a handover re-indexes the freed feed right after this call.
        audio, extra_mute = self.obs_audio_plan()

        def run():
            _applied, note = self._obs.reflect_feed_state(
                live, cut, audio=audio, extra_mute=extra_mute)
            self.obs_note = note or None
            _warn_if_mic_failed(note)
        threading.Thread(target=run, daemon=True).start()

    def _maybe_probe_obs(self, now):
        """Kick off a throttled, side-effect-free OBS reachability probe off-thread
        so /status reflects OBS's CURRENT state, not a value cached from the last
        handover. Makes the common 'relay started before OBS' case self-heal: the
        banner clears within one probe interval once OBS is up — no restart, no
        handover needed. Returns the spawned Thread (or None when throttled /
        disabled) so callers/tests can join it; status() ignores the return."""
        if _obs_ws is None:
            return None
        with self._obs_lock:
            if not should_probe_obs(self._obs_probe_ts, self._obs_probe_running,
                                    now, OBS_PROBE_INTERVAL_S):
                return None
            self._obs_probe_running = True
            self._obs_probe_ts = now          # stamp at launch (one clock: the caller's)
        t = threading.Thread(target=self._run_obs_probe, daemon=True)
        t.start()
        return t

    def _run_obs_probe(self):
        """Body of the OBS probe (own method so it is testable synchronously).
        Fetches reachability + GetStats/GetStreamStatus in one session, derives the
        upstream kbps, and stores a REDACTED stats dict (never output_bytes)."""
        if _obs_ws is None:
            with self._obs_lock:
                self.obs_reachable = False
                self.obs_note = "obs_ws unavailable"
                self._obs_probe_running = False
                self.obs_stats = {}
            return
        now = time.time()
        try:
            reachable, stats, note = self._obs.get_health_stats()
            kbps = _obs_ws.stream_kbps(self._obs_last_bytes, self._obs_last_bytes_ts,
                                       stats.get("output_bytes"), now,
                                       stats.get("stream_active"))
            transition = None
            with self._obs_lock:
                self.obs_reachable = reachable
                self.obs_note = note or None
                self._obs_probe_running = False
                self._obs_last_bytes = stats.get("output_bytes")
                self._obs_last_bytes_ts = now
                redacted = {k: v for k, v in stats.items() if k != "output_bytes"}
                redacted["stream_kbps"] = kbps
                self.obs_stats = redacted
                active = stats.get("stream_active")    # True / False / None
                prev = self._stream_active_prev
                if active is not None:                 # ignore unreachable blips
                    self._stream_active_prev = active
                if active:                             # latch once OBS goes live
                    self.stream_expected = True
                transition = stream_transition(prev, active)
            # Off-thread I/O OUTSIDE the lock: a relay-log line + Discord push +
            # Health-Monitor marker.
            if transition:
                self._on_stream_transition(transition, now, kbps=kbps)
        except Exception:                                # noqa: BLE001 — best-effort
            with self._obs_lock:
                self._obs_probe_running = False

    def _on_stream_transition(self, transition, now, kbps=None):
        """Emit an OBS stream start/stop event (#317): a relay-log line, a
        best-effort Discord push, and a persisted Health-Monitor marker. The log
        line makes the transition greppable in `racecast relay logs` alongside the
        feed events; it carries the upstream kbps on start and the stream uptime on
        stop. Discord + health markers name this producer."""
        started = transition == "started"
        if started:
            self._stream_started_ts = now
            uptime = None
        else:
            uptime = (now - self._stream_started_ts
                      if self._stream_started_ts is not None else None)
            self._stream_started_ts = None
        LOG.info(stream_event_log_line(started, kbps=kbps, uptime_s=uptime))
        self._discord_post(
            notify.obs_stream_discord_payload(started, self.producer_name,
                                              self._event_title()),
            "obs-stream")
        if self.health_store is not None:
            try:
                self.health_store.record_event(
                    now, "obs_stream_start" if started else "obs_stream_stop",
                    label="OBS stream started" if started else "OBS stream stopped",
                    producer=self.producer_name)
            except Exception:    # noqa: BLE001 — best-effort
                pass

    def _spawn_event_stop(self):
        """Detached `racecast event stop` — it generates+sends the report (we are
        still up, so names resolve) then tears racecast down (kills us by PID).
        Detach into a new session so our own teardown cannot cascade-kill the child.
        Best-effort — a spawn failure is logged, never raised."""
        try:
            frozen = bool(getattr(sys, "frozen", False))
            argv = event_stop_argv(frozen, sys.executable, _REL_HERE)
            env = os.environ.copy()
            if frozen:
                env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                      "stderr": subprocess.DEVNULL, "env": env}
            if os.name == "nt":
                kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                           | subprocess.CREATE_NEW_PROCESS_GROUP)
            else:
                kwargs["start_new_session"] = True
            subprocess.Popen(argv, **kwargs)
            LOG.info("Last part ended — spawned `event stop` (report + teardown).")
        except Exception:      # noqa: BLE001 — best-effort
            LOG.exception("failed to spawn event stop")

    def _sample_connectivity(self):
        """Sample Funnel/Tailscale/Companion reachability into self.conn_state and
        latch funnel_expected. Best-effort: each probe defaults to None on failure."""
        try:
            funnel = tailscale.funnel_on("/console")
        except Exception:                                # noqa: BLE001 — best-effort
            funnel = None
        try:
            ts_state = tailscale.tailscale_backend()[1]
            ts_up = (ts_state == "Running") if ts_state is not None else None
        except Exception:                                # noqa: BLE001
            ts_up = None
        try:
            comp_port = int(os.environ.get("RACECAST_COMPANION_PORT")
                            or companion_common.COMPANION_DEFAULT_ADMIN_PORT)
            comp = companion_common.companion_reachable("127.0.0.1", comp_port)
        except Exception:                                # noqa: BLE001
            comp = None
        if funnel:
            self.funnel_expected = True
        # Tri-state: True = up; False = down AND was previously expected (real
        # regression); None = down/absent but never seen up (neutral — renders
        # grey in the health band, not red).
        if funnel:
            funnel_ok = True
        elif self.funnel_expected:
            funnel_ok = False
        else:
            funnel_ok = None
        self.conn_state = {"funnel_ok": funnel_ok, "tailscale_up": ts_up,
                           "companion_ok": comp}

    def next_auto(self):
        if self.solo:
            return {"error": "not available in solo mode", "solo": True}
        self.source.refresh(timeout=6)               # fresh sheet data at handover (bounded wait)
        rows = self.source.get_rows()
        slots = pull_slots(rows)
        cur = self.on_air_row_idx()
        nxt = cur + 1

        # Same-URL back-to-back: keep the on-air pull, advance the LABEL only.
        if is_continuation(slots, nxt):
            self.on_air_row = nxt
            LOG.info("continuation -> stint %d stays on feed %s (no cut)",
                     nxt + 1, self.live_feed())
            # **self.status() spread FIRST, explicit keys last: a future status()
            # field named "continuation"/"obs_cut" can never silently override
            # these (uniform across all three next_auto return branches).
            return {**self.status(), "changed": False, "feed": self.live_feed(),
                    "continuation": True, "obs_cut": False}

        # Past the last stint: idle over-press (unchanged behavior).
        if nxt >= len(rows):
            new_live = self.live_after_next()
            target = "A" if new_live == "B" else "B"
            result = self.advance(target, +2)
            cut = self.feeds[new_live].phase == "serving"
            if cut:
                self._reflect(new_live, cut=True)
            self.on_air_row = self.feeds[new_live].idx
            # `result` (from advance()) already carries status() plus "changed"/"feed";
            # spread it first and place continuation/obs_cut last, matching the
            # spread-first/explicit-last ordering of the other two branches above.
            return {**result, "continuation": False, "obs_cut": cut}

        # Real handover to the pre-warmed off-air feed, walking SLOTS (not +2) so a
        # freed feed skips a continuation run instead of duplicating the on-air pull.
        new_live = self.live_after_next()
        freed = "A" if new_live == "B" else "B"
        # Reconcile the incoming feed's preload against fresh slots (a Sheet edit may
        # have moved the boundary): it must sit on the next slot after the current row.
        self.feeds[new_live].set_index(next_slot_first_row(slots, cur))
        cut = self.feeds[new_live].phase == "serving"
        if cut:
            self._reflect(new_live, cut=True)         # only flip onto a feed that is actually live
        # #489/#505: on a real handover cut, stop the outgoing pull so only ONE feed
        # ever pulls googlevideo between handovers (the durable single-puller fix). Gated
        # on cut (never stop a feed we did not cut away from -> never blacks the program)
        # and on manual arm (the legacy opt-out keeps its whole-stint pre-warm).
        stop_freed = cut and self.manual_feed_arm
        # Pause BEFORE re-indexing: set_index's advance.set() wakes the freed feed's run
        # loop, so paused must already be True — otherwise the loop could spawn one
        # throwaway resolve on the new slot in the window before we stop it.
        if stop_freed:
            self.feeds[freed].paused = True
        # Advance the freed feed to the slot AFTER the new on-air row.
        self.feeds[freed].set_index(next_slot_first_row(slots, nxt))
        if stop_freed:
            self.feeds[freed].reload()          # kill proc -> port closes (also covers a no-op set_index)
            LOG.info("handover -> freed feed %s auto-stopped after cut", freed)
        self.on_air_row = nxt
        nf = self.feeds[new_live]
        LOG.info("handover -> feed %s now on air (stint %d)", nf.name, nxt + 1)
        # **self.status() spread FIRST, explicit keys last — see the continuation
        # branch above for why (uniform across all three next_auto branches).
        return {**self.status(), "changed": True, "feed": new_live,
                "continuation": False, "obs_cut": cut}

    def advance(self, which, delta):
        f = self.feeds.get(which.upper())
        if not f:
            return None
        other_key = "B" if which.upper() == "A" else "A"
        other = self.feeds.get(other_key)
        target = f.idx + delta
        redirected = False
        if other is not None:
            rows = self.source.get_rows()
            target, redirected = dedupe_pull_index(target, other.idx, rows)
            if redirected:
                LOG.info("advance %s would duplicate feed %s's stream; "
                         "auto-advanced to row %d (next distinct slot)",
                         which.upper(), other_key, target + 1)
        changed = f.set_index(target)
        # Spread **self.status() FIRST, explicit keys last, so a future status()
        # field can never silently shadow these (matches next_auto's ordering).
        return {**self.status(), "changed": changed, "feed": which.upper(),
                "redirected": redirected}

    def set_index(self, which, idx):
        f = self.feeds.get(which.upper())
        if not f:
            return None
        other_key = "B" if which.upper() == "A" else "A"
        other = self.feeds.get(other_key)
        redirected = False
        if other is not None:
            rows = self.source.get_rows()
            new_idx, redirected = dedupe_pull_index(idx, other.idx, rows)
            if redirected:
                # The other feed already pulls this URL -> the requested row is a
                # same-URL continuation: advance the DISPLAY to it and send this
                # feed to the next distinct slot, never a duplicate pull (#491).
                self.on_air_row = max(0, min(idx, max(0, len(rows) - 1)))
                LOG.info("set %s -> row %d would duplicate feed %s's stream; "
                         "auto-advanced to row %d (next distinct slot)",
                         which.upper(), idx + 1, other_key, new_idx + 1)
                idx = new_idx
        f.set_index(idx)
        return {**self.status(), "redirected": redirected}

    def set_stint(self, stint):
        """Producer-takeover correction: 1-based stint <stint> is on air NOW ->
        Feed A serves the HEAD of that stint's slot (a takeover onto a back-to-back's
        second row still pulls the single stream once), Feed B preloads the next
        slot. The DISPLAY row is set to <stint>. Tears a running feed off its stream
        (like /set) — use BEFORE going live, not mid-program."""
        if self.solo:
            return {"error": "not available in solo mode", "solo": True}
        self.source.refresh(timeout=6)      # clamp against fresh sheet data
        rows = self.source.get_rows()
        a_idx, b_idx = slot_start_indices(stint, rows)
        self.A.set_index(a_idx)
        self.B.set_index(b_idx)
        self.on_air_row = min(max(1, int(stint)) - 1, max(0, len(rows) - 1))
        LOG.info("set_stint -> Feed A slot-head %d, Feed B %d, display stint %d",
                 a_idx + 1, b_idx + 1, self.on_air_row + 1)
        self._reflect(self.live_feed(), cut=False)   # set visibility/audio; director picks the scene
        return self.status()

    def resync_to_stint(self, stint):
        """Feed-agnostic desync recovery: reconcile 'stint <N> is on air NOW' onto
        whichever feed is ACTUALLY serving it, preserving the live picture. Finds the
        feed whose current URL == stint N's row URL AND is delivering a stable
        picture (_feed_serving), keeps it on air (A OR B), sets on_air_row, and moves
        the OTHER feed to the next distinct slot after both the target row and the
        anchor's own index (#491-safe; keeps the anchor the lower index so live_feed
        names it). Non-destructive: the anchor feed is never re-indexed. When NO feed
        serves N this is a takeover, not a resync -> returns an error and does nothing
        (use /set/stint, which is producer+step-up gated)."""
        self.source.refresh(timeout=6)
        rows = self.source.get_rows()
        n = len(rows)
        target = min(max(1, int(stint)) - 1, max(0, n - 1)) if n else 0
        target_url = (rows[target][0] or "").strip() if n else ""
        anchor = None
        if target_url:
            for k in ("A", "B"):
                ch, _ = self.feeds[k].current_channel()
                if (ch or "").strip() == target_url and self._feed_serving(self.feeds[k]):
                    anchor = k
                    break
        if anchor is None:
            LOG.info("resync_to_stint -> stint %d not served by any feed; no-op "
                     "(use /set/stint for a takeover)", target + 1)
            return {"error": f"no feed serves stint {target + 1} — "
                             f"use /set/stint for a takeover"}
        other = "B" if anchor == "A" else "A"
        slots = pull_slots(rows)
        # Place the other feed after BOTH the target row and the anchor's own pull
        # index, so the anchor keeps the lower index and live_feed() names it even
        # for a non-contiguous same-URL recurrence.
        pivot = max(target, self.feeds[anchor].idx)
        off_idx, _redir = dedupe_pull_index(
            next_slot_first_row(slots, pivot), self.feeds[anchor].idx, rows)
        self.feeds[other].set_index(off_idx)   # no kill if already there
        self.on_air_row = target
        LOG.info("resync_to_stint -> stint %d anchored on serving feed %s (no cut); "
                 "feed %s -> slot %d", target + 1, anchor, other, off_idx + 1)
        self._reflect(anchor, cut=False)
        return self.status()

    def set_mode(self, mode):
        """Switch the active schedule between 'race' and 'qualifying'. Re-points
        both feeds to the new schedule's stint 1 — for a single-stream qualifying
        Feed A serves the one row and Feed B idles — and sets Feed-A visibility/
        audio (cut=False; the director picks the scene). Tears a running pull off
        its stream like set_stint: an explicit between-session action, not for
        mid-program use. No-op-with-error when qualifying is unavailable."""
        if self.solo:
            return {"error": "not available in solo mode", "solo": True}
        if mode not in ("race", "qualifying"):
            return {"error": f"unknown mode: {mode!r} (one of race, qualifying)"}
        if mode == "qualifying" and not self.qual_source:
            return {"error": "qualifying disabled (no Qualifying tab, or --no-qualifying)"}
        self.mode = mode
        LOG.info("mode -> %s", self.mode)
        self.active_source().refresh(timeout=6)        # fresh rows for the schedule we switch to
        rows = self.active_source().get_rows()
        a_idx, b_idx = slot_start_indices(1, rows)
        self.A.set_index(a_idx)
        self.B.set_index(b_idx)
        self.on_air_row = a_idx
        self._reflect(self.live_feed(), cut=False)
        return self.status()

    def reload(self, which=None):
        if self.solo:
            return {"error": "not available in solo mode", "solo": True}
        # Detect an ad-hoc on-air stream substitution: the operator edited the
        # on-air feed's URL then pressed Reload, so the on-air feed's URL changes
        # at the SAME stint across the schedule refresh. Captured before feeds
        # actually reconnect; best-effort, never blocks the reload.
        # NOTE: detection is index-scoped (URL changed at the on-air feed's index).
        # The documented flow edits the on-air URL cell in place; inserting/deleting
        # rows ABOVE the on-air row in the same reload shifts that index onto a
        # different stint's URL and would read as a substitution — an accepted edge
        # of the "URL change at unchanged stint index" definition, not the real flow.
        live = self.live_feed()
        old_url, old_idx = self.feeds[live].current_channel()
        self.source.refresh(timeout=6)
        new_url, new_idx = self.feeds[live].current_channel()
        if (which is None or which.upper() == live) and \
                is_substitution(old_url, old_idx, new_url, new_idx):
            self._record_substitution(live, new_idx)
        # Single-pull invariant (#491): a schedule edit may have made the off-air
        # feed's parked row share the on-air feed's URL. Re-point the OFF-AIR feed
        # to the next distinct slot before it reconnects, so two feeds never pull
        # the identical stream. Moving a parked feed never cuts the live picture;
        # done regardless of `which` (a reload("A") can still leave B on a dup).
        off = "B" if live == "A" else "A"
        ded_rows = self.source.get_rows()
        ded_idx, ded_redir = dedupe_pull_index(
            self.feeds[off].idx, self.feeds[live].idx, ded_rows)
        if ded_redir:
            LOG.info("reload: feed %s parked on feed %s's stream; auto-advanced "
                     "to row %d (next distinct slot)", off, live, ded_idx + 1)
            self.feeds[off].set_index(ded_idx)
        targets = [which.upper()] if which else list(self.feeds)
        for t in targets:
            if t in self.feeds: self.feeds[t].reload()
        LOG.info("reload: schedule re-read (%d stints), feeds %s",
                 len(self.source.get()), ",".join(targets))
        return {"reloaded": targets, **self.status()}

    def _record_substitution(self, feed, idx):
        """Record + announce an ad-hoc on-air stream substitution (best-effort):
        a discrete Health event (feed + 1-based stint only, NO url) plus a Discord
        post. A missing health_store or webhook is a silent no-op."""
        stint = idx + 1
        if self.health_store is not None:
            try:
                self.health_store.record_event(
                    time.time(), "feed_substitution", producer=self.producer_name,
                    metadata={"feed": feed, "stint": stint})
            except Exception:                # noqa: BLE001 — best-effort
                pass
        self._discord_post(
            notify.substitution_discord_payload(feed, stint, self.producer_name,
                                                 self._event_title()),
            "feed-substitution")
        LOG.info("stream substitution recorded: Feed %s stint %d", feed, stint)

    def _record_feed_recovery(self, feed, stint, downtime_s, source_state=None):
        """Feed.on_recovery callback: a feed dropped and is recovering. Record it as a
        discrete `feed_recovery` health event ALWAYS (so a self-healed on-air blip shows in
        the post-event report + health monitor + log — the 2026-07-10 'all green' gap), and
        fire a Discord @here only on CHURN. Best-effort; never raises into the feed loop."""
        now = time.time()
        if self.health_store is not None:
            try:
                self.health_store.record_event(
                    now, "feed_recovery", producer=self.producer_name,
                    metadata={"feed": feed, "stint": stint,
                              "downtime_s": round(max(0.0, downtime_s), 1)})
            except Exception:                # noqa: BLE001 — best-effort
                pass
        LOG.info("feed recovery recorded: Feed %s stint %d (~%.0fs degraded)",
                 feed, stint, max(0.0, downtime_s))
        self._maybe_notify_recovery_churn(feed, now, source_state)

    def _record_feed_step_down(self, feed, stint, from_tier, to_tier):
        """Feed.on_step_down callback: an auto quality step-down happened. Record a
        `feed_step_down` health incident (post-event report + health monitor) AND fire a
        Discord @here — it is actionable. Best-effort; never raises into the feed loop."""
        now = time.time()
        if self.health_store is not None:
            try:
                self.health_store.record_event(
                    now, "feed_step_down", producer=self.producer_name,
                    metadata={"feed": feed, "stint": stint,
                              "from": from_tier, "to": to_tier})
            except Exception:                # noqa: BLE001 — best-effort
                pass
        # State the step-down fact only — do NOT claim the @here here: _discord_post is
        # best-effort and no-ops without a webhook, so an unconditional "posted" would
        # mislead incident triage (mirrors _record_feed_recovery, which makes no such claim).
        LOG.warning("feed step-down: Feed %s stint %d %s->%s — source can't sustain %s",
                    feed, stint, from_tier, to_tier, from_tier)
        try:
            self._discord_post(
                discord_step_down_payload(feed, stint, from_tier, to_tier,
                                          self._event_title(), self.producer_name),
                "feed-step-down")
        except Exception:                    # noqa: BLE001 — best-effort
            pass

    def _maybe_notify_recovery_churn(self, feed, now, source_state=None):
        """Discord @here when Feed `feed` is CHURNING (≥ FEED_CHURN_THRESHOLD recoveries in
        FEED_CHURN_WINDOW_S), throttled to one ping per feed per FEED_CHURN_COOLDOWN_S so a
        persistently-flapping feed does not spam. Best-effort."""
        if churn_at_here_suppressed(source_state):
            return                    # #495: expected churn from a not-(yet)-live source
        if self.health_store is None:
            return
        last = self._recovery_churn_notified.get(feed)
        if last is not None and (now - last) < FEED_CHURN_COOLDOWN_S:
            return
        try:
            events = self.health_store.events(now - FEED_CHURN_WINDOW_S, now)
        except Exception:                    # noqa: BLE001 — best-effort read
            return
        ts = [e.get("ts") for e in events
              if e.get("type") == "feed_recovery"
              and (e.get("metadata") or {}).get("feed") == feed]
        if not feed_recovery_churn(ts, now):
            return
        self._recovery_churn_notified[feed] = now
        self._discord_post(
            notify.feed_recovery_churn_payload(feed, len(ts), FEED_CHURN_WINDOW_S // 60,
                                               self.producer_name, self._event_title()),
            "feed-recovery-churn")
        LOG.warning("feed churn: Feed %s recovered %d× in %d min — @here posted",
                    feed, len(ts), FEED_CHURN_WINDOW_S // 60)

    def latest_substitution(self):
        """The most recent feed_substitution event as
        {"ts","feed","stint","reason"}, or None. Read side for the Director Panel's
        substitution section."""
        if self.health_store is None:
            return None
        try:
            events = self.health_store.events(0, time.time())
        except Exception:                    # noqa: BLE001 — best-effort read
            return None
        subs = [e for e in events if e.get("type") == "feed_substitution"]
        if not subs:
            return None
        e = subs[-1]                         # events() is ascending -> last = newest
        md = e.get("metadata") or {}
        return {"ts": e.get("ts"), "feed": md.get("feed") or "",
                "stint": md.get("stint"), "reason": md.get("reason") or ""}

    def annotate_substitution_reason(self, reason):
        """Attach a sanitized free-text reason to the latest feed_substitution
        event (director action from the panel). Returns the updated
        latest_substitution() shape, or {"error": ...} when disabled/absent."""
        if self.health_store is None:
            return {"error": "health history disabled"}
        updated = self.health_store.annotate_latest_event(
            "feed_substitution", {"reason": sanitize_reason(reason)})
        if updated is None:
            return {"error": "no substitution to annotate"}
        return self.latest_substitution()

    def pov_reload(self):
        if not self.pov:
            return {"error": "pov disabled"}
        if self.pov_source:
            self.pov_source.refresh()   # re-read the POV cell on demand
        self.pov.paused = False
        self.pov.reload()
        return self.status()

    def pov_stop(self):
        if not self.pov:
            return {"error": "pov disabled"}
        self.pov.paused = True
        self.pov.reload()               # advance.set() + kill the serving proc -> port closes
        return self.status()

    def feed_activate(self, which):
        """Arm Feed A/B: start pulling at its current index (two-stage scheduling,
        #492). Mirrors pov_reload. Manual-mode only — an error otherwise so the auto
        pre-warm/handover logic and manual arm never fight."""
        if not self.manual_feed_arm:
            return {"error": "manual feed arm disabled (set RACECAST_MANUAL_FEED_ARM=1)"}
        f = self.feeds.get(which.upper())
        if not f:
            return {"error": f"unknown feed {which!r}"}
        # #491 single-pull invariant on the arm path: refuse to arm a feed onto a
        # URL the OTHER feed is already armed on (would be a same-URL double-pull).
        other_key = "B" if which.upper() == "A" else "A"
        other = self.feeds.get(other_key)
        sched = self.source.get()
        this_url = (sched[f.idx] or "").strip() if 0 <= f.idx < len(sched) else ""
        if this_url and other is not None and not other.paused:
            other_url = (other.current_channel()[0] or "").strip()
            if this_url == other_url:
                return {"error": f"would duplicate feed {other_key}'s stream — "
                                 f"disarm it or point this feed to a different stint"}
        f.paused = False
        f.reload()
        LOG.info("feed %s armed (manual)", which.upper())
        return self.status()

    def feed_deactivate(self, which):
        """Disarm Feed A/B: stop its pull, kill the process, close the port (frees
        bandwidth) — mirrors pov_stop. Manual-mode only (#492)."""
        if not self.manual_feed_arm:
            return {"error": "manual feed arm disabled (set RACECAST_MANUAL_FEED_ARM=1)"}
        f = self.feeds.get(which.upper())
        if not f:
            return {"error": f"unknown feed {which!r}"}
        f.paused = True
        f.reload()
        LOG.info("feed %s disarmed (manual)", which.upper())
        return self.status()

    def set_feed_quality(self, which, tier):
        """Director control: set a feed's quality profile. `tier` is full|robust|emergency
        (a manual pin) or `auto` (release to managed FULL). Returns {feed, profile, pinned}
        or {"error": ...}. Validates `tier` itself (via parse_quality_tier) so the method is
        safe to call directly, even though the HTTP route pre-validates too (#493)."""
        feed = self.feeds.get(which)
        if feed is None:
            return {"error": f"unknown feed {which}"}
        norm = parse_quality_tier(tier)
        if norm is None:
            return {"error": f"unknown quality tier {tier!r}"}
        if norm == "auto":
            feed.set_quality("full", False)
        else:
            feed.set_quality(norm, True)
        return {"feed": which, "profile": feed.quality_tier, "pinned": feed.quality_pinned}

    def shutdown(self):
        if self._av_watcher is not None:
            self._av_watcher.stop = True    # #619: ends the tail on the next poll
        for f in self.feeds.values(): f.shutdown()
        if self.pov: self.pov.shutdown()
        for srv in self._fanout_servers: srv.stop()
        if getattr(self, "_obs", None) is not None:
            try:
                self._obs.close()          # #537: clean 1000 on the persistent sessions
            except Exception:  # noqa: BLE001 — best-effort
                pass


def _push_live_schedule(relay, setup_ctl):
    """On a handover, auto-write the on-air stint's Streamer + Stint label from
    the Schedule into the HUD (issue #112), reusing the existing async-optimistic
    set_field path so the Sheet stays the source of truth. Best-effort: a value
    not in the Configuration vocabulary is rejected by set_field and skipped,
    like every other panel write; an empty cell leaves the field untouched."""
    row = relay.live_schedule_row()
    if not row:
        return
    if row.get("streamer"):
        setup_ctl.set_field("streamer", row["streamer"])
    if row.get("stint"):
        setup_ctl.set_field("stint", row["stint"])


def should_push_live_schedule(result):
    """True when a next_auto() result should trigger _push_live_schedule: a real
    cut (obs_cut) OR a same-URL back-to-back continuation — the DISPLAY stint
    advances on a continuation even though OBS doesn't cut, so the HUD label must
    follow it too. False only on a plain idle over-press (neither). Pure so the
    /next gate is unit-testable without a live relay."""
    return bool(result.get("obs_cut") or result.get("continuation"))


def _benign_client_disconnect(exc):
    """True when `exc` is a client hanging up mid-response — an OBS browser
    source closing, a panel tab refreshing, a director's tablet going to sleep.
    ConnectionError covers BrokenPipeError, ConnectionResetError and
    ConnectionAbortedError (WinError 10053, issue #25) on every platform. These
    are never a relay fault, so the server must neither try to answer on the dead
    socket nor log a traceback. A plain OSError (e.g. disk full) is NOT a
    ConnectionError and still surfaces."""
    return isinstance(exc, ConnectionError)


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that stays silent when a client disconnects mid-
    response. The stdlib's handle_error() dumps a full traceback for the
    ConnectionAbortedError that wfile.flush() raises after the client is gone
    (issue #25: the HUD/timer browser sources poll and close constantly), which
    floods the relay console with noise that looks like a crash."""
    def handle_error(self, request, client_address):
        if _benign_client_disconnect(sys.exc_info()[1]):
            return                       # client went away — nothing to report
        super().handle_error(request, client_address)


# Human-facing /console PAGE routes (segment tuples, sans the leading "console").
# A GET to one of these with a missing/invalid token, when OAuth is configured,
# serves the launcher (Login with Discord) instead of a naked 401 JSON page.
_CONSOLE_PAGE_GETS = frozenset({(), ("cockpit",), ("panel",),
                                ("health-monitor",), ("race-control",), ("buttons",)})


def make_handler(relay, panel_path=None, hud_source=None, hud_path=None, assets_dir=None,
                 timer_store=None, setup_ctl=None, overlay_dir=None,
                 chat_store=None, cue_store=None, preview_path=None, graphics_dir=None,
                 splitscreen_path=None, intermission_path=None,
                 cockpit_page_path=None, console_secret=None,
                 discord_client_id=None, discord_client_secret=None,
                 console_versions_path=None,
                 submission_store=None, event_store=None, crew_source=None,
                 event_notes_source=None,
                 console_page_path=None, companion_url=None, logo_path=None,
                 buttons_page_path=None, race_control_page_path=None,
                 health_store=None, health_monitor_page_path=None, uplot_dir=None,
                 broadcast_chat_store=None, broadcast_chat_supervisor=None,
                 preview_manager=None, program_audio_service=None, brands_dir=None,
                 flag_graphic_store=None, app_version="dev",
                 channel_source=None, producer_source=None, part_store=None,
                 channel_csv_url=None, push_url=None, telemetry_store=None):
    # Shared across all H instances (one limiter per relay). The CHAT limiter is
    # keyed on the authenticated streamer (per-commentator). The AUTH-FAILURE
    # limiter is keyed on the source IP, which behind Tailscale Funnel collapses to
    # the single proxy address — so it is a COARSE GLOBAL cap on failed cockpit
    # auths, not a per-client control. That is acceptable: valid tokens never reach
    # this limiter (only the failure branch does, so legit commentators are never locked
    # out), and the 128-bit HMAC signature makes brute force infeasible regardless
    # — this is pure defense-in-depth. X-Forwarded-For is deliberately NOT trusted
    # for the key (spoofable over the public ingress, which would weaken it).
    # Wire the health store onto the relay so the payload builders below read it
    # via relay.health_store (the canonical attribute; Task 13's bootstrap also
    # assigns it). The explicit make_handler param is the carrier from the test
    # harness and the bootstrap call.
    if health_store is not None:
        relay.health_store = health_store
    _console_authfail_rl = console_auth.RateLimiter(limit=20, window_s=60)
    _cockpit_chat_rl = console_auth.RateLimiter(limit=10, window_s=60)
    # Submit is a PUBLIC write path (funnelled). Keyed on the authed identity
    # (not the shared proxy IP, like chat) so one commentator can't exhaust the
    # crew's quota; a low cap — a human submits a link a handful of times, not
    # dozens per minute.
    _cockpit_submit_rl = console_auth.RateLimiter(limit=5, window_s=60)
    # Commentator ack is a funnelled write; key on the authed identity (like chat), not
    # the shared proxy IP. The director SEND has no limiter (director-gated /
    # tailnet-trusted, like /next).
    _cockpit_cue_ack_rl = console_auth.RateLimiter(limit=30, window_s=60)
    # Cue-back (#377) is a funnelled commentator write; key on the authed identity
    # (like chat) so one commentator can't exhaust the crew's quota.
    _cockpit_cueback_rl = console_auth.RateLimiter(limit=12, window_s=60)

    class H(BaseHTTPRequestHandler):
        def _send(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
        def _send_file(self, path, ctype):
            try:
                with open(path, "rb") as fh: body = fh.read()
            except OSError:
                return self._send({"error": "file not found", "looked_for": path}, 404)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            # The panel/HUD/timer pages change between releases — never let a
            # browser serve a stale copy (e.g. a panel without the latest JS).
            self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(body)
            return None
        def _send_css(self, body):
            self.send_response(200)
            self.send_header("Content-Type", "text/css; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(body)
            return None
        def _send_page(self, path, api_base="", cookie_token=None, cookie_path=None):
            """Serve an HTML page, substituting the __RC_API_BASE__ placeholder with
            api_base ("" at the tailnet/loopback root, "/console" behind Funnel) and
            optionally setting the rc_console auth cookie scoped to cookie_path."""
            try:
                with open(path, "rb") as fh:
                    body = fh.read()
            except OSError:
                return self._send({"error": "page not found"}, 404)
            body = body.replace(b"__RC_API_BASE__", (api_base or "").encode())
            oauth_flag = b"1" if (discord_client_id and discord_client_secret) else b""
            body = body.replace(b"__RC_OAUTH__", oauth_flag)
            body = body.replace(b"__RC_VERSION__", (app_version or "dev").encode())
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            # Restrict where inline images may load from (#351 broadcast-chat
            # emotes). Only `img-src` is set -- with no default-src the page's
            # inline scripts/styles are untouched; `'self'` covers same-origin
            # program stills + HUD flag/brand assets, the two CDNs cover YouTube
            # and Twitch emotes. Defense in depth atop the builder host allowlist.
            self.send_header(
                "Content-Security-Policy",
                "img-src 'self' data: https://static-cdn.jtvnw.net "
                "https://yt3.ggpht.com https://yt4.ggpht.com")
            if cookie_token is not None and cookie_path:
                # `Secure` only behind the HTTPS Funnel (X-Forwarded-Proto); browsers
                # drop a Secure cookie over plain http, which would break the tailnet
                # fallback link (its sub-requests would never re-auth). The tailnet
                # hop is already WireGuard-encrypted.
                secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
                # Allowlist-sanitize: the value must never be raw request input
                # (CWE-113 response splitting / CWE-20 cookie injection).
                safe = console_auth.safe_cookie_token(cookie_token)
                self.send_header("Set-Cookie",
                                 f"{console_auth.COOKIE_NAME}={safe}; Path={cookie_path}; "
                                 f"HttpOnly{secure}; SameSite=Lax")
            self.end_headers()
            self.wfile.write(body)
            return None

        def _send_html_with_cookie(self, path, token):
            """Back-compat: the tailnet /cockpit page (cookie scoped to /cockpit,
            base empty)."""
            return self._send_page(path, "", cookie_token=token, cookie_path="/cockpit")
        def _send_jpeg(self, body):
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(body)
            return None
        def _stream_ring(self, ring, content_type, service):
            return _program_audio_stream_ring(self, ring, content_type, service)
        def _send_text(self, text, code=200):
            body = (text or "").encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
            return None
        def _send_asset(self, assets_dir, sub, key):
            hit = resolve_brand_override(brands_dir, key) if sub == "brands" else None
            if not hit:
                hit = resolve_asset(assets_dir, sub, key)
            if not hit:
                return self._send({"error": "asset not found", "key": key}, 404)
            path, ctype = hit
            # Header value comes from the ASSET_CTYPES constant, never from the
            # request-derived tuple (defense vs. header injection).
            ctype = ASSET_CTYPES.get(ctype)
            if not ctype:
                return self._send({"error": "asset not found", "key": key}, 404)
            return self._send_file(path, ctype)
        def _send_font(self, overlay_dir, name):
            hit = resolve_overlay_font(overlay_dir, name)
            if not hit:
                return self._send({"error": "font not found", "key": name}, 404)
            path, ctype = hit
            # Header value comes from the FONT_CTYPES_OUT constant, never from the
            # request-derived tuple (defense vs. header injection) — mirrors _send_asset.
            ctype = FONT_CTYPES_OUT.get(ctype)
            if not ctype:
                return self._send({"error": "font not found", "key": name}, 404)
            return self._send_file(path, ctype)
        def _companion_base(self):
            """Resolved Companion admin base URL. The make_handler `companion_url` override
            wins (tests / non-standard installs); otherwise resolve from Companion's own
            config.json bind_ip, then the Tailscale IP, then loopback (#236)."""
            if companion_url:
                return companion_url
            bind_ip = None
            try:
                import sys as _sys, json as _json
                with open(companion_common.companion_config_path(_sys.platform)) as fh:
                    bind_ip = (_json.load(fh).get("bind_ip") or "").strip() or None
            except Exception:
                bind_ip = None
            return console_proxy.resolve_companion_base(bind_ip, tailscale.detect_tailscale_ip())

        def _proxy_companion(self, method):
            """Reverse-proxy a /console/buttons/* request to the local Companion (Option C,
            #236), director-gated by the caller. HTTP and WebSocket (tRPC /trpc) paths both
            strip the relay auth token first. Best-effort: never raises; unreachable Companion
            -> 502."""
            import urllib.request, urllib.error
            from urllib.parse import urlsplit
            clean_path = console_proxy.strip_relay_token(self.path)   # relay token never goes upstream
            base = self._companion_base()
            u = urlsplit(base); host, cport = u.hostname or "127.0.0.1", u.port or 8000
            if console_proxy.is_websocket_upgrade(self.headers):
                import socket, select
                try:
                    up = socket.create_connection((host, cport), timeout=5)
                except OSError as e:
                    LOG.warning("Companion WS connect failed: %s", e)
                    return self._send({"error": "Companion not reachable"}, 502)
                # Replay the upgrade: rewritten+token-stripped path, upgrade headers forwarded
                # RAW (Upgrade/Connection/Sec-WebSocket-* must survive), prefix injected.
                # The relay's rc_console auth cookie must never reach Companion.
                hdrs = {}
                for k, v in self.headers.items():
                    lk = k.lower()
                    if lk in ("host", "accept-encoding"):
                        continue
                    if lk == "cookie":
                        cleaned = console_proxy.scrub_relay_cookie(v)
                        if cleaned:
                            hdrs[k] = cleaned
                        # else: drop entirely
                        continue
                    hdrs[k] = v
                hdrs["Host"] = "%s:%d" % (host, cport)
                hdrs[console_proxy.COMPANION_PREFIX_HEADER] = console_proxy.PREFIX_HEADER_VALUE
                raw = ("GET %s HTTP/1.1\r\n" % console_proxy.upstream_path(clean_path)
                       + "".join("%s: %s\r\n" % kv for kv in hdrs.items()) + "\r\n")
                up.sendall(raw.encode("latin-1"))
                client = self.connection
                try:
                    while True:
                        r, _, _ = select.select([client, up], [], [], 120)
                        if not r:
                            break
                        for s in r:
                            chunk = s.recv(65536)
                            if not chunk:
                                raise ConnectionError
                            (up if s is client else client).sendall(chunk)
                except Exception:
                    pass  # normal on client/upstream disconnect
                finally:
                    up.close()
                self.close_connection = True
                return None
            # --- HTTP path (token-stripped clean_path forwarded upstream) ---
            url = base.rstrip("/") + console_proxy.upstream_path(clean_path)
            length = int(self.headers.get("Content-Length") or 0)
            data = self.rfile.read(length) if length else None
            hdrs = console_proxy.forward_request_headers(
                self.headers, host="%s:%d" % (host, cport))
            req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    body, status = resp.read(), resp.status
                    out_headers = console_proxy.filter_response_headers(resp.headers.items())
                    ctype = resp.headers.get("Content-Type", "application/octet-stream")
            except urllib.error.HTTPError as e:
                body, status = e.read(), e.code
                out_headers = console_proxy.filter_response_headers(e.headers.items())
                ctype = e.headers.get("Content-Type", "application/octet-stream")
            except Exception as e:
                LOG.warning("Companion proxy failed: %s: %s", type(e).__name__, e)
                return self._send({"error": "Companion not reachable"}, 502)
            self.send_response(status)
            for k, v in out_headers:
                self.send_header(k, v)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(body)
            return None

        def _buttons_health(self):
            """Director-gated availability probe for the launcher: is Companion up and new
            enough (>= 4.1.0) to serve under the /console/buttons sub-path? (#236)"""
            import urllib.request
            base = self._companion_base()
            ver = install_apps.companion_http_version(base)
            reachable = ver is not None
            if not reachable:
                try:
                    urllib.request.urlopen(base.rstrip("/") + "/", timeout=2); reachable = True
                except Exception:
                    reachable = False
            ok = reachable and console_proxy.version_ge(ver, (4, 1, 0))
            return self._send({"reachable": reachable, "version": ver, "ok": ok})

        def log_message(self, *a): pass

        def _send_redirect(self, location):
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None

        def _send_html(self, content, code=200):
            body = content.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(body)
            return None

        def _oauth_redirect_uri(self):
            """Build this host's callback URI from the request Host header.
            Returns None if the host is not a valid MagicDNS name (forged-Host guard)."""
            host = (self.headers.get("Host") or "").split(":")[0].strip()
            if not discord_oauth.valid_redirect_host(host):
                return None
            return f"https://{host}/console/oauth/callback"

        def _oauth_exchange(self, code, redirect_uri):
            """Exchange auth code for Discord username. Returns "" on any failure.
            A module-level _TEST_EXCHANGE hook short-circuits this in unit tests."""
            hook = globals().get("_TEST_EXCHANGE")
            if hook is not None:
                return hook(code, redirect_uri)
            try:
                data = urlencode({
                    "client_id": discord_client_id,
                    "client_secret": discord_client_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                }).encode("utf-8")
                req = Request(discord_oauth.TOKEN_ENDPOINT, data=data, method="POST",
                              headers={"Content-Type": "application/x-www-form-urlencoded",
                                       "User-Agent": "racecast-feeds/1.0"})
                with urlopen(req, timeout=10) as resp:
                    tok = json.loads(resp.read().decode("utf-8")).get("access_token")
                if not tok:
                    return ""
                ureq = Request(discord_oauth.USERINFO_ENDPOINT,
                               headers={"Authorization": f"Bearer {tok}",
                                        "User-Agent": "racecast-feeds/1.0"})
                with urlopen(ureq, timeout=10) as uresp:
                    return discord_oauth.parse_identity(
                        json.loads(uresp.read().decode("utf-8")))
            except Exception as e:
                LOG.warning("Discord OAuth exchange failed: %s: %s", type(e).__name__, e)
                return ""

        _STATE_COOKIE = "rc_oauth_state"

        def _state_clear_cookie(self):
            """Per-request state cookie clear — mirrors the Secure flag when on HTTPS."""
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            return (f"{self._STATE_COOKIE}=; Path=/console; "
                    f"Max-Age=0; HttpOnly{secure}; SameSite=Lax")

        def _oauth_login(self):
            """GET /console/login -> 302 to Discord authorize (OAuth must be configured)."""
            if not (discord_client_id and discord_client_secret):
                self._send({"error": "not found"}, 404)
                return None
            redirect_uri = self._oauth_redirect_uri()
            if not redirect_uri:
                return self._send_html(
                    "<h1>Login unavailable</h1><p>This host can't run Discord login "
                    "(needs the public Funnel address).</p>", 400)
            nonce = secrets.token_urlsafe(16)
            state = discord_oauth.sign_state(console_secret, nonce, int(time.time()))
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            state_cookie = (f"{self._STATE_COOKIE}={nonce}; Path=/console; "
                            f"HttpOnly; SameSite=Lax; Max-Age=600{secure}")
            self.send_response(302)
            self.send_header("Location",
                             discord_oauth.authorize_url(discord_client_id, redirect_uri, state))
            self.send_header("Set-Cookie", state_cookie)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None

        def _oauth_callback(self):
            """GET /console/oauth/callback?code=&state= -> mint cookie or deny page."""
            if not (discord_client_id and discord_client_secret):
                self._send({"error": "not found"}, 404)
                return None
            qs = parse_qs(urlparse(self.path).query)
            if qs.get("error"):
                return self._send_html_with_set_cookie(
                    "<h1>Login cancelled</h1>"
                    "<p><a href='/console/login'>Try again</a></p>",
                    self._state_clear_cookie())
            state = (qs.get("state") or [""])[0]
            if not discord_oauth.verify_state(console_secret, state, int(time.time())):
                return self._send_html_with_set_cookie(
                    "<h1>Login expired or invalid</h1>"
                    "<p><a href='/console/login'>Try again</a></p>",
                    self._state_clear_cookie(), 400)
            # CSRF: verify the state nonce matches the session cookie.
            cookie_nonce = console_auth.parse_cookie_token(
                self.headers.get("Cookie"), cookie_name=self._STATE_COOKIE) or ""
            state_nonce = discord_oauth.state_nonce(state)
            if not cookie_nonce or not hmac.compare_digest(cookie_nonce, state_nonce):
                return self._send_html_with_set_cookie(
                    "<h1>Login expired or invalid</h1>"
                    "<p><a href='/console/login'>Try again</a></p>",
                    self._state_clear_cookie(), 400)
            redirect_uri = self._oauth_redirect_uri()
            if not redirect_uri:
                return self._send_html_with_set_cookie(
                    "<h1>Login unavailable</h1>", self._state_clear_cookie(), 400)
            username = self._oauth_exchange((qs.get("code") or [""])[0], redirect_uri)
            if not username:
                return self._send_html_with_set_cookie(
                    "<h1>Login failed</h1>"
                    "<p><a href='/console/login'>Try again</a></p>",
                    self._state_clear_cookie(), 502)
            dm = crew_source.discord_map() if (crew_source and hasattr(crew_source, "discord_map")) else {}
            name = discord_oauth.match_subject(username, dm)
            if not name:
                return self._send_html_with_set_cookie(
                    f"<h1>Not on the crew list</h1>"
                    f"<p>Your Discord <b>@{html.escape(username)}</b> "
                    "isn't in this league's Crew list. Ask your league admin to add it.</p>",
                    self._state_clear_cookie(), 403)
            key = console_auth.streamer_key(name)
            versions = (console_admin.load_versions(console_versions_path)
                        if console_versions_path else {})
            token = console_auth.mint_token(console_secret, key,
                                            console_admin.current_version(versions, key))
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            safe = console_auth.safe_cookie_token(token)
            self.send_response(302)
            self.send_header("Location", "/console")
            self.send_header("Set-Cookie",
                             f"{console_auth.COOKIE_NAME}={safe}; Path=/console; "
                             f"HttpOnly{secure}; SameSite=Lax")
            self.send_header("Set-Cookie", self._state_clear_cookie())
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None

        def _send_html_with_set_cookie(self, content, set_cookie, code=200):
            """Like _send_html but also emits a Set-Cookie header before the body."""
            body = content.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Set-Cookie", set_cookie)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(body)
            return None

        def _cockpit_active(self):
            """True iff a per-league cockpit secret is configured. The secret is
            auto-provisioned by the CLI (zero-config), so the cockpit is served
            whenever one exists; when it is absent every /cockpit/* path 404s (like
            chat/timer when disabled). PUBLIC exposure is the separate Funnel switch."""
            return bool(console_secret)
        def _cockpit_token(self):
            """The presented token: query ?t= first (link load), else the cookie."""
            qs = parse_qs(urlparse(self.path).query)
            if qs.get("t"):
                return qs["t"][0]
            return console_auth.parse_cookie_token(self.headers.get("Cookie"))
        def _console_subject(self):
            """Verify the presented token -> streamer_key, or None. Sends nothing
            (the caller decides whether to deny or serve a login page)."""
            versions = (console_admin.load_versions(console_versions_path)
                        if console_versions_path else {})
            return console_auth.verify_token(console_secret, self._cockpit_token(),
                                             versions)

        def _console_deny(self):
            """Send the rate-limited 401/429 for an unauthenticated /console request."""
            client = self.client_address[0] if self.client_address else "?"
            if not _console_authfail_rl.allow(client):
                self._send({"error": "rate limited"}, 429)
            else:
                self._send({"error": "unauthorized"}, 401)

        def _console_auth(self):
            """Return the authed streamer_key, or None after sending 401/429.
            Applies a per-client failure rate limit. Caller must return on None."""
            me = self._console_subject()
            if me is None:
                self._console_deny()
            return me
        def _console_roles(self, subject):
            """Resolve a verified subject to its capability set from the live
            schedule (commentator) + Crew roster (director/producer)."""
            src = getattr(relay, "source", None)
            if src is not None and hasattr(src, "get_rows"):
                rows = src.get_rows()
            else:
                rows = getattr(src, "rows", []) or []
            crew = crew_source.get() if crew_source else []
            ckeys = crew_source.commentator_keys() if crew_source else frozenset()
            rckeys = crew_source.race_control_keys() if crew_source else frozenset()
            return resolve_roles(crew, schedule_keys(rows), subject, ckeys, rckeys)

        def _console_gate(self, p, method):
            """Authorize a /console/* request and return the segment list to fall
            through to (ALLOW), or None after sending a response. p includes the
            leading 'console'. Identity-bound routes are rewritten to their
            identity-forced /cockpit equivalents so the server sets the speaker.
            Boundary: when no league cockpit secret is configured, /console 404s
            exactly like /cockpit."""
            if not console_secret:
                self._send({"error": "not found"}, 404)
                return None
            sub = p[1:]
            # OAuth front door — bootstrap identity before the auth check.
            if sub == ["login"]:
                return self._oauth_login()
            if sub == ["oauth", "callback"]:
                return self._oauth_callback()
            # Producer-to-producer takeover pull (#216 Phase 7): authorized by the
            # shared per-league step-up secret ALONE. Producer B holds the league
            # CONSOLE_SECRET (it signs every token) — NOT a per-person commentator
            # token — so requiring an rc_console subject here would 401 a legitimate
            # takeover even when the secret matches (the original bug). The secret is
            # producer-level and strictly stronger than any single token, so it is
            # sufficient. A missing/invalid secret is 403 (step-up required), NEVER
            # 401, which misled operators into blaming a matching secret. Runs BEFORE
            # the subject check below precisely so no token is needed.
            if sub and sub[0] == "takeover":
                presented = self.headers.get("X-Console-Secret")
                if not (presented and console_auth.secret_matches(presented, console_secret)):
                    self._send({"error": "step-up required"}, 403)
                    return None
                # Identity-bound routes -> their full-data handlers (secret already
                # verified). status is served REDACTED in do_GET (feed URLs never
                # leave the tailnet); unknown takeover paths 404.
                rewrite = {
                    ("takeover", "status"): ["takeover", "status"],
                    ("takeover", "chat"): ["chat", "data"],
                    ("takeover", "versions"): ["cockpit", "versions"],
                    ("takeover", "cues"): ["cues", "data"],
                    ("takeover", "health"): ["health", "raw"],
                }.get(tuple(sub))
                if rewrite is None:
                    self._send({"error": "not found"}, 404)
                    return None
                return rewrite
            # Authenticate (verify only — no response sent yet).
            subject = self._console_subject()
            if subject is None:
                # Human navigation to a console PAGE (launcher / cockpit / panel /
                # health-monitor / race-control / buttons) with a MISSING OR INVALID
                # token, and OAuth configured: serve the launcher so the visitor sees
                # "Login with Discord" — NEVER a naked 401 JSON page. A stale cookie
                # from another profile, or a revoked/expired token, no longer
                # dead-ends; the page itself degrades to the login button on whoami.
                if (bool(discord_client_id and discord_client_secret)
                        and method == "GET" and tuple(sub) in _CONSOLE_PAGE_GETS):
                    self._send_page(console_page_path, "/console")
                    return None
                # API calls, non-GET, or OAuth-off installs: the rate-limited 401.
                self._console_deny()
                return None
            roles = self._console_roles(subject)
            # Step-up secret header.
            presented = self.headers.get("X-Console-Secret")
            has_step_up = bool(presented) and console_auth.secret_matches(presented, console_secret)
            # /console-only: identity introspection for the launcher (any auth).
            if sub == ["whoami"]:
                self._send({"subject": subject, "roles": sorted(roles)})
                return None
            # Read-only broadcast-chat mirror (#294): any authenticated /console
            # subject (commentator, director, race_control) may read it. Funnelled
            # under the existing /console mount — no new public surface. The data
            # is already public on YouTube and is never persisted.
            if sub == ["broadcast-chat", "data"] and method == "GET":
                if not broadcast_chat_store:
                    self._send({"error": "broadcast chat disabled"}, 404)
                    return None
                self._send(broadcast_chat_store.data())
                return None
            # Manual recovery (#294): re-arm a frozen reader. ANY authenticated
            # /console subject may kick it (idempotent, read-only side effect) —
            # no new public surface beyond the existing /console mount.
            if sub == ["broadcast-chat", "reload"] and method == "GET":
                if not broadcast_chat_store:
                    self._send({"error": "broadcast chat disabled"}, 404)
                    return None
                if broadcast_chat_supervisor is not None:
                    broadcast_chat_supervisor.rearm()
                self._send(broadcast_chat_store.data())
                return None
            # /console/buttons/* -> reverse-proxy to the local Companion (#236), director only.
            # 'buttons/health' is served by the relay (launcher availability probe) and shadows
            # that one upstream path (Companion's UI does not use /health).
            if sub and sub[0] == "buttons":
                if console_policy.decide(roles, sub, method, has_step_up) != console_policy.ALLOW:
                    self._send({"error": "forbidden"}, 403)
                    return None
                if sub == ["buttons", "health"]:
                    self._buttons_health()
                    return None
                # /console/buttons (exact) -> our thin wrapper page: a "← Console" back
                # bar + an iframe embedding the Companion tablet UI (same tab as the
                # other launcher links). /console/buttons/<rest> proxies to Companion.
                if sub == ["buttons"] and method == "GET" and buttons_page_path:
                    self._send_page(buttons_page_path, "/console",
                                    cookie_token=self._cockpit_token(), cookie_path="/console")
                    return None
                self._proxy_companion(method)
                return None
            # /console/logo — serve the league logo to any authenticated subject (#236).
            # Funnelled so it must live under /console; served locally from logo_path.
            if sub == ["logo"]:
                if console_policy.decide(roles, sub, method, has_step_up) != console_policy.ALLOW:
                    self._send({"error": "forbidden"}, 403)
                    return None
                path = servable_logo_path(logo_path)
                if not path:
                    self._send({"error": "no logo"}, 404)
                    return None
                ext = os.path.splitext(path)[1].lower()
                ctype = _LOGO_CTYPES.get(ext, "image/png")
                self._send_file(path, ctype)
                return None
            # Race Control monitoring desk (#244): a read-only page + ONE new data
            # endpoint, both gated on the race_control capability. The redacted
            # schedule strips stream URLs (the Funnel boundary). The desk's
            # program/timer/chat reuse the ANY cockpit endpoints (no new surface).
            if sub and sub[0] == "race-control":
                if console_policy.decide(roles, sub, method, has_step_up) != console_policy.ALLOW:
                    self._send({"error": "forbidden"}, 403)
                    return None
                if sub == ["race-control"] and method == "GET":
                    if not race_control_page_path:
                        return self._send({"error": "page not found"}, 404)
                    self._send_page(race_control_page_path, "/console",
                                    cookie_token=self._cockpit_token(), cookie_path="/console")
                    return None
                if sub == ["race-control", "data"] and method == "GET":
                    rows = relay.source.get_rows()
                    live = relay.live_row_map()
                    live_idx = relay.on_air_row_idx()
                    return self._send({
                        "schedule": race_control_schedule(rows, live),
                        "event_title": event_store.get() if event_store else "",
                        "mode": relay.mode,
                        "on_air": live_schedule_row(rows, live_idx)})
                # RC -> commentator quick-note presets (#376), admin-managed.
                if sub == ["race-control", "presets"] and method == "GET":
                    return self._send({"presets": hud_source.rc_note_presets()
                                       if hud_source else []})
                # RC -> commentator note send (#376). Fall through to do_POST,
                # which reads the request body before dispatch (authorized here).
                if sub == ["race-control", "cues"] and method == "POST":
                    return ["race-control", "cues"]
                return self._send({"error": "not found"}, 404)
            # Health-monitor dashboard (#health): a read-only page + ONE data
            # endpoint, any authenticated subject. Redacted by construction
            # (_health_monitor_payload never carries stream URLs). Mirrors the
            # race-control branch above.
            if sub and sub[0] == "health-monitor":
                if console_policy.decide(roles, sub, method, has_step_up) != console_policy.ALLOW:
                    self._send({"error": "forbidden"}, 403)
                    return None
                if sub == ["health-monitor"] and method == "GET":
                    if not health_monitor_page_path:
                        return self._send({"error": "page not found"}, 404)
                    self._send_page(health_monitor_page_path, "/console",
                                    cookie_token=self._cockpit_token(), cookie_path="/console")
                    return None
                if sub == ["health-monitor", "data"] and method == "GET":
                    self._send(self._health_monitor_payload())
                    return None
                if len(sub) == 3 and sub[:2] == ["health-monitor", "assets"]:
                    return sub        # served by the root asset route
                return self._send({"error": "not found"}, 404)
            # Role-adaptive pages: authorize via the same matrix, then serve HTML
            # with the /console base + a Path=/console cookie. Served BEFORE the API
            # fall-through so they don't reach the root page handlers (wrong cookie
            # path / no base).
            page = {(): console_page_path, ("cockpit",): cockpit_page_path,
                    ("panel",): panel_path}.get(tuple(sub))
            if page is not None or tuple(sub) in {(), ("cockpit",), ("panel",)}:
                if console_policy.decide(roles, sub, method, has_step_up) != console_policy.ALLOW:
                    self._send({"error": "forbidden"}, 403)
                    return None
                if not page:
                    return self._send({"error": "page not found"}, 404)
                token = self._cockpit_token()
                self._send_page(page, "/console", cookie_token=token, cookie_path="/console")
                return None
            # console_policy.decide derives capability from the path; the `method`
            # arg is plumbing for a future tightening — it is not a live check today.
            outcome = console_policy.decide(roles, sub, method, has_step_up)
            if outcome == console_policy.ALLOW:
                # /console/status is Funnel-exposed: serve a role-redacted payload
                # instead of full status — feed stream URLs (feeds[*].channel), pov.url
                # and sheet_id are kept only for director/producer and stripped for every
                # other role (see redact_console_status). GET-only; a POST falls through
                # to the root dispatch's 404.
                if sub == ["status"] and method == "GET":
                    self._send(self._console_status_payload(roles))
                    return None
                # Identity-bound routes -> their identity-forced /cockpit handlers,
                # which re-run _console_auth to set the speaker from the token (a
                # harmless second verify, NOT a missing optimization). This is what
                # makes a client-supplied chat "user" impossible over /console.
                if sub == ["chat", "send"]:
                    return ["cockpit", "chat", "send"]
                if sub == ["chat", "data"]:
                    return ["cockpit", "chat", "data"]
                if sub == ["submit"]:
                    return ["cockpit", "submit"]
                # NB: /console/takeover/* is handled earlier (step-up-secret only,
                # no token) and never reaches this role-gated branch.
                return sub
            if outcome == console_policy.STEP_UP_REQUIRED:
                self._send({"error": "step-up required"}, 403)
                return None
            if outcome == console_policy.FORBIDDEN:
                self._send({"error": "forbidden"}, 403)
                return None
            self._send({"error": "not found"}, 404)   # NOT_FOUND
            return None
        def _post_discord(self, payload, what):
            """Best-effort fire-and-forget POST of a Discord webhook payload; a
            natural no-op when no webhook is configured. Mirrors the health
            webhook's posting. *what* names the event for the failure log."""
            url = getattr(relay, "discord_webhook_url", None)
            if not url:
                return
            try:
                data = json.dumps(payload).encode()
                req = Request(url, data=data, method="POST",
                              headers={"Content-Type": "application/json",
                                       "User-Agent": "racecast-feeds/1.0"})
                urlopen(req, timeout=5).read()
            except Exception as e:
                LOG.warning("Discord %s webhook failed: %s: %s",
                            what, type(e).__name__, e)
        def _event_title(self):
            """The current event title (#207) for Discord footers, or ""."""
            return event_store.get() if event_store else ""
        def _notify_submission(self, entry, pending_count):
            """Best-effort Discord @here ping on a new pending submission (#193)."""
            self._post_discord(
                cockpit_submission_payload(entry, pending_count, self._event_title()),
                "submission")
        def _notify_approval(self, entry):
            """Best-effort Discord heads-up (no @here ping) once the director
            approves a submission and the link is scheduled (#193 follow-up)."""
            self._post_discord(
                cockpit_approval_payload(entry, self._event_title()), "approval")
        def _health_query_window(self):
            qs = parse_qs(urlparse(self.path).query)
            def _f(name):
                try:
                    return float(qs[name][0])
                except (KeyError, ValueError, IndexError):
                    return None
            frm, to = _f("from"), _f("to")
            try:
                maxn = int(qs["max"][0])
            except (KeyError, ValueError, IndexError):
                maxn = _HEALTH_CONST.DEFAULT_MAX_POINTS
            now = time.time()
            if frm is None or to is None:
                to, frm = now, now - _HEALTH_CONST.LIVE_WINDOW_S
            return frm, to, maxn, now

        def _health_current(self):
            """Allowlist of the live health fields — NEVER the feed/pov stream URLs
            that relay.status() carries (redaction boundary)."""
            full = relay.status()
            feeds = {}
            for k, v in (full.get("feeds") or {}).items():
                q = getattr(relay.feeds.get(k), "quality", None) if relay.feeds.get(k) else None
                feeds[k] = {"state": v.get("state"), "down": v.get("down"),
                            "stint": v.get("stint"), "state_age_s": v.get("state_age_s"),
                            "quality": q, "profile": v.get("profile"), "pinned": v.get("pinned")}
            pov = full.get("pov")
            pov_red = None if not pov else {"state": pov.get("state"),
                                            "down": pov.get("down"), "shown": pov.get("shown"),
                                            "quality": getattr(relay.pov, "quality", None)}
            return {"health": full.get("health"), "feeds": feeds, "pov": pov_red,
                    "obs": full.get("obs"), "source": full.get("source"),
                    "cookies_health": full.get("cookies_health"),
                    "mode": full.get("mode"), "live": full.get("live"),
                    "timer": timer_store.summary() if timer_store else None,
                    "event_title": event_store.get() if event_store else ""}

        def _health_monitor_payload(self):
            if relay.health_store is None:
                return {"error": "health monitor disabled"}
            frm, to, maxn, now = self._health_query_window()
            store = relay.health_store
            return {"now": now, "from": frm, "to": to,
                    "current": self._health_current(),
                    "bands": store.bands(frm, to),
                    "incidents": store.incidents(frm, to),
                    "events": store.events(frm, to),   # discrete markers (#317)
                    "series": store.series(frm, to, maxn)}

        def _health_raw_payload(self):
            """JSON-Lines body of raw samples (for takeover/export). Returns a str."""
            if relay.health_store is None:
                return ""
            qs = parse_qs(urlparse(self.path).query)
            try:
                frm = float(qs["from"][0])
            except (KeyError, ValueError, IndexError):
                frm = 0
            return "\n".join(relay.health_store.export_lines(frm))

        def _status_payload(self):
            """The full /status JSON (feeds, pov, league, health) + timer +
            event_title. Served verbatim on the tailnet; redacted for /console
            via _console_status_payload."""
            base = relay.status()
            if timer_store:
                base["timer"] = timer_store.summary()
            base["event_title"] = event_store.get() if event_store else ""
            return base
        def _console_status_payload(self, roles):
            """Status for the Funnel-exposed /console mount. Feed stream URLs are
            director/producer-only over the Funnel — the same boundary as
            /schedule/data, which already exposes per-stint URLs to directors (the
            panel's Preview button + POV editor consume them over Funnel). So
            feeds[*].channel, the POV stream URL, and the Sheet id are KEPT for
            director/producer and stripped for every other role. The plain tailnet
            /status is unaffected."""
            return redact_console_status(self._status_payload(), roles)
        def do_GET(self):
            p = [x for x in urlparse(self.path).path.split("/") if x]
            try:
                if p and p[0] == "console":
                    p = self._console_gate(p, "GET")
                    if p is None:
                        return None     # gate already sent its response (401/403/404)
                if not p or p == ["status"]:
                    return self._send(self._status_payload())
                if p == ["health-monitor"]:
                    if not health_monitor_page_path:
                        return self._send({"error": "page not found"}, 404)
                    return self._send_page(health_monitor_page_path, "")
                if p == ["health-monitor", "data"]:
                    return self._send(self._health_monitor_payload())
                if len(p) == 3 and p[:2] == ["health-monitor", "assets"]:
                    resolved = resolve_uplot_asset(uplot_dir, p[2])
                    if resolved is None:
                        return self._send({"error": "not found"}, 404)
                    full, ctype = resolved
                    # Header value comes from the UPLOT_CTYPES_OUT constant, never
                    # from the request-derived tuple (defense vs. header injection)
                    # — mirrors _send_asset / _send_font.
                    ctype = UPLOT_CTYPES_OUT.get(ctype)
                    if not ctype:
                        return self._send({"error": "not found"}, 404)
                    try:
                        with open(full, "rb") as fh:
                            data = fh.read()
                    except OSError:
                        return self._send({"error": "not found"}, 404)
                    self.send_response(200)
                    self.send_header("Content-Type", ctype + "; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "max-age=86400")
                    self.end_headers(); self.wfile.write(data)
                    return None
                if p == ["health", "raw"]:
                    return self._send_text(self._health_raw_payload())
                if p == ["takeover", "status"]:
                    # Funnel-exposed (producer + step-up via _console_gate). Redacted:
                    # ONLY the fields a takeover needs — NEVER the feeds/pov stream URLs
                    # that the tailnet /status carries (this leaves the tailnet).
                    full = relay.status()
                    return self._send({
                        "live": full.get("live"),
                        "league": full.get("league"),
                        "mode": full.get("mode"),
                        "producer": full.get("producer"),   # #317 — name A for the takeover
                        "event_title": event_store.get() if event_store else "",
                        "timer": timer_store.summary() if timer_store else None,
                    })
                if p == ["panel"]:
                    if not panel_path: return self._send({"error":"panel disabled"}, 404)
                    return self._send_page(panel_path, "")
                if p == ["hud"]:
                    if not (hud_source and hud_path):
                        return self._send({"error": "hud disabled"}, 404)
                    return self._send_file(hud_path, "text/html; charset=utf-8")
                if p == ["hud", "data"]:
                    if not hud_source:
                        return self._send({"error": "hud disabled"}, 404)
                    data = hud_source.data()           # already a shallow copy
                    data["povActive"] = relay.pov_active()
                    data["povName"] = relay.pov_name()
                    # Race vs qualifying, so a profile's overlay CSS can gate on
                    # body[data-mode] (issue #555). Relay state, like povActive —
                    # deliberately not part of HudSource.
                    data["mode"] = relay.mode
                    return self._send(data)
                if p == ["hud", "preview"]:
                    if not preview_path:
                        return self._send({"error": "preview disabled"}, 404)
                    return self._send_file(preview_path, "text/html; charset=utf-8")
                if p == ["hud", "preview", "bg"]:
                    hit = resolve_preview_bg(overlay_dir, assets_dir)
                    if not hit:
                        return self._send({"error": "no preview backdrop"}, 404)
                    return self._send_file(hit[0], ASSET_CTYPES[hit[1]])
                if p == ["hud", "preview", "frame"]:
                    hit = resolve_preview_frame(graphics_dir)
                    if not hit:
                        return self._send({"error": "no overlay frame"}, 404)
                    return self._send_file(hit[0], ASSET_CTYPES[hit[1]])
                if len(p) == 4 and p[:2] == ["hud", "assets"]:
                    return self._send_asset(assets_dir, p[2], p[3])
                if p == ["hud", "override.css"]:
                    return self._send_css(read_overlay_css(overlay_dir, "hud"))
                if p == ["hud", "logo"]:
                    # League logo for the HUD overlay slot (#league-logo). Loopback —
                    # OBS reads it on 127.0.0.1; served from the active profile's LOGO
                    # (logo_path). 404 when unset/non-image so the slot self-hides.
                    path = servable_logo_path(logo_path)
                    if not path:
                        return self._send({"error": "no logo"}, 404)
                    ext = os.path.splitext(path)[1].lower()
                    return self._send_file(path, _LOGO_CTYPES.get(ext, "image/png"))
                if p == ["preview", "program"]:
                    if _obs_ws is None:
                        return self._send({"error": "obs unavailable"}, 503)
                    data, note = _program_shot_cache.fetch(
                        lambda: relay._obs.get_program_screenshot(width=640))
                    if data is None:
                        return self._send({"error": "preview unavailable",
                                           "note": note}, 503)
                    return self._send_jpeg(data)
                if p == ["preview", "program-audio"]:
                    if program_audio_service is None:
                        return self._send({"error": "program audio disabled"}, 404)
                    # ?probe=1 -> availability only (front-end self-hide). Must NOT
                    # acquire() -> never spins up the encoder / touches the listener count.
                    if _program_audio_is_probe(self.path):
                        if not getattr(relay, "fanout", False):
                            return self._send({"error": "program audio unavailable"}, 404)
                        return self._send({"available": True})
                    ring = program_audio_service.acquire()
                    if ring is None:               # fan-out off -> no in-process feed bytes
                        return self._send({"error": "program audio unavailable"}, 404)
                    try:
                        return self._stream_ring(ring, PROGRAM_AUDIO_CONTENT_TYPE,
                                                 program_audio_service)
                    finally:
                        program_audio_service.release()
                if len(p) == 3 and p[:2] == ["preview", "feed"]:
                    target = p[2].upper()
                    if target not in PREVIEW_FEEDS:
                        return self._send({"error": "unknown feed", "feed": p[2]}, 404)
                    if preview_manager is None:
                        return self._send({"error": "preview disabled"}, 404)
                    data, note = preview_manager.still(target)
                    if data is None:
                        return self._send({"error": "preview unavailable",
                                           "note": note}, 503)
                    return self._send_jpeg(data)
                if p == ["preview", "levels"]:
                    if preview_manager is None:
                        return self._send({"error": "preview disabled"}, 404)
                    return self._send(preview_manager.levels())
                if p == ["splitscreen"]:
                    if not splitscreen_path:
                        return self._send({"error": "splitscreen page not found"}, 404)
                    return self._send_file(splitscreen_path, "text/html; charset=utf-8")
                if p == ["splitscreen", "data"]:
                    return self._send(relay.splitscreen_state())
                if p == ["splitscreen", "override.css"]:
                    return self._send_css(read_overlay_css(overlay_dir, "splitscreen"))
                if p == ["intermission"]:
                    if not intermission_path:
                        return self._send({"error": "intermission page not found"}, 404)
                    return self._send_file(intermission_path, "text/html; charset=utf-8")
                if p == ["intermission", "override.css"]:
                    return self._send_css(read_overlay_css(overlay_dir, "intermission"))
                if p == ["telemetry", "data"]:
                    if telemetry_store is None:
                        return self._send({"error": "telemetry disabled"}, 404)
                    return self._send(telemetry_store.data())
                if p == ["telemetry", "trace"]:
                    if telemetry_store is None:
                        return self._send({"error": "telemetry disabled"}, 404)
                    return self._send({"samples": telemetry_store.trace(150)})
                if len(p) == 3 and p[:2] == ["overlay", "fonts"]:
                    return self._send_font(overlay_dir, p[2])
                if p[:1] == ["timer"]:
                    if not timer_store:
                        return self._send({"error": "timer disabled"}, 404)
                    if p == ["timer", "data"]:   return self._send(timer_store.data())
                    if p == ["timer", "start"]:  return self._send(timer_store.start())
                    if p == ["timer", "stop"]:   return self._send(timer_store.stop())
                    if p == ["timer", "reset"]:  return self._send(timer_store.reset())
                    if p == ["timer", "show"]:   return self._send(timer_store.show())
                    if p == ["timer", "hide"]:   return self._send(timer_store.hide())
                    if len(p) == 3 and p[1] == "set":
                        dur = parse_duration(p[2])
                        if dur is None:
                            return self._send({"error": "duration must be H:MM:SS"}, 400)
                        return self._send(timer_store.set_duration(dur))
                    if len(p) == 3 and p[1] == "adjust":
                        try: delta = int(p[2])
                        except ValueError:
                            return self._send({"error": "adjust takes +/- seconds"}, 400)
                        return self._send(timer_store.adjust(delta))
                    return self._send({"error": "unknown", "path": self.path}, 404)
                if p[:1] == ["setup"]:
                    if not setup_ctl:
                        return self._send({"error": "setup control disabled"}, 404)
                    if p == ["setup", "data"]:
                        return self._send(setup_ctl.data())
                    if len(p) == 4 and p[1] == "set":
                        return self._send(setup_ctl.set_field(p[2].lower(),
                                                              unquote(p[3])))
                    if len(p) == 4 and p[1] == "team":
                        return self._send(setup_ctl.set_team(p[2].lower(),
                                                             unquote(p[3])))
                    if len(p) == 3 and p[1] == "clear":
                        return self._send(setup_ctl.set_field(p[2].lower(), ""))
                    return self._send({"error": "unknown", "path": self.path}, 404)
                if p[:2] == ["obs", "flag"]:
                    # Flag-status GRAPHIC toggle (parallel to the flag-text chip).
                    # GET so Companion's Generic-HTTP module hits it directly; the
                    # tailnet is the trust boundary. Funnel reaches it via the
                    # /console mount, director-gated by console_policy ('obs').
                    if not flag_graphic_store:
                        return self._send({"error": "flag graphic disabled"}, 404)
                    if p == ["obs", "flag", "data"]:
                        return self._send(flag_graphic_store.data())
                    if len(p) == 4 and p[2] == "set":
                        return self._send(flag_graphic_store.set(unquote(p[3])))
                    if p == ["obs", "flag", "clear"]:
                        return self._send(flag_graphic_store.clear())
                    return self._send({"error": "unknown", "path": self.path}, 404)
                if p == ["obs", "split-audio"]:
                    payload, status = apply_split_audio(relay, relay._obs)
                    return self._send(payload, status)
                if p == ["obs", "split"]:
                    payload, status = apply_split_state(relay, relay._obs)
                    return self._send(payload, status)
                if len(p) == 3 and p[:2] == ["obs", "stint"]:
                    payload, status = apply_stint_state(relay, relay._obs, p[2])
                    return self._send(payload, status)
                if p[:1] == ["chat"]:
                    if not chat_store:
                        return self._send({"error": "chat disabled"}, 404)
                    if p == ["chat", "data"]:
                        return self._send(chat_store.data())
                    if p == ["chat", "reload"]:
                        return self._send(chat_store.reload())
                    return self._send({"error": "unknown", "path": self.path}, 404)
                if p[:1] == ["parts"]:
                    if part_store is None or producer_source is None:
                        return self._send({"enabled": False})   # feature off -> panel fallback
                    if p == ["parts", "data"]:
                        rows = producer_mod.active_producer_rows(
                            producer_source.get(), relay.mode)
                        # The panel forwards the OBS stream state it already polls
                        # (via /obs/state) so we skip a redundant obs-websocket read;
                        # a bare call (no param) still self-reconciles against OBS.
                        qs = parse_qs(urlparse(self.path).query)
                        active = parts_mod.stream_active_param(
                            qs.get("stream_active", [None])[0])
                        if active is None and _obs_ws is not None:
                            st, _n = relay._obs.read_obs_state([], [])
                            if isinstance(st, dict) and isinstance(st.get("stream"), dict):
                                active = bool(st["stream"].get("active"))
                        vm = parts_mod.parts_view_model(rows, part_store.get(), active)
                        if channel_source is not None:
                            vm["platform"] = stream_target.event_platform(
                                channel_source.get()) or None
                        return self._send(vm)
                    return self._send({"error": "unknown", "path": self.path}, 404)
                if p[:1] == ["broadcast-chat"]:
                    # Read-only mirror of the public YouTube broadcast chat (#294).
                    # Tailnet/loopback read; the Funnel read is the ANY-auth
                    # /console/broadcast-chat/data branch in _console_gate.
                    if not broadcast_chat_store:
                        return self._send({"error": "broadcast chat disabled"}, 404)
                    if p == ["broadcast-chat", "data"]:
                        return self._send(broadcast_chat_store.data())
                    if p == ["broadcast-chat", "reload"]:
                        # Manual recovery (#294): re-arm any frozen YouTube reader,
                        # then echo the current mirror so the card re-renders now.
                        if broadcast_chat_supervisor is not None:
                            broadcast_chat_supervisor.rearm()
                        return self._send(broadcast_chat_store.data())
                    return self._send({"error": "unknown", "path": self.path}, 404)
                if p[:1] == ["cues"]:
                    if not cue_store:
                        return self._send({"error": "cues disabled"}, 404)
                    if p == ["cues", "data"]:
                        return self._send(cue_store.data())
                    if p == ["cues", "presets"]:
                        return self._send({"presets": hud_source.cue_presets()
                                           if hud_source else []})
                    if p == ["cues", "reload"]:
                        return self._send(cue_store.reload())
                    if p == ["cues", "back"]:
                        # Commentator -> director cue-backs (#377), director-gated
                        # like the rest of /cues; shown on the Director Panel.
                        return self._send({"cueBacks": cue_admin.cue_backs(cue_store.list())})
                    return self._send({"error": "unknown", "path": self.path}, 404)
                if p[:1] == ["cockpit"]:
                    if not self._cockpit_active():
                        return self._send({"error": "cockpit not configured"}, 404)
                    if p == ["cockpit"]:
                        me = self._console_auth()
                        if me is None:
                            return None
                        if not cockpit_page_path:
                            return self._send({"error": "cockpit page not found"}, 404)
                        return self._send_html_with_cookie(cockpit_page_path,
                                                           self._cockpit_token())
                    if p == ["cockpit", "data"]:
                        me = self._console_auth()
                        if me is None:
                            return None
                        rows = relay.source.get_rows()
                        live_idx = relay.on_air_row_idx()
                        tally = cockpit_tally(rows, live_idx, me)
                        # The commentator's OWN pending submissions (stint + id
                        # only, never a URL) so the cockpit shows live status that
                        # clears once the director approves/rejects (#193).
                        my_pending = ([{"id": e["id"], "stint": e["target_stint"]}
                                       for e in submission_store.list()
                                       if e["streamer_key"] == me]
                                      if submission_store else [])
                        tally.update({"me": me, "mode": relay.mode,
                                      "program_available": _obs_ws is not None,
                                      # read-only event title for the commentator header (#207)
                                      "event_title": event_store.get() if event_store else "",
                                      # own stints for the link-submission picker
                                      # (#193); empty -> the cockpit hides the form.
                                      "submit_enabled": submission_store is not None,
                                      "my_stints": cockpit_own_stints(rows, me),
                                      # read-only redacted stint plan for the
                                      # right-column card (no stream URLs).
                                      "schedule": cockpit_schedule(rows, live_idx, me),
                                      "syncing": cockpit_syncing(relay._desync),
                                      "program_behind_s": cockpit_program_behind(
                                          getattr(relay, "_backlogged_feeds", None)),
                                      "my_pending": my_pending})
                        return self._send(tally)
                    if p == ["cockpit", "program"]:
                        if self._console_auth() is None:
                            return None
                        if _obs_ws is None:
                            return self._send({"error": "obs unavailable"}, 503)
                        data, note = _program_shot_cache.fetch(
                            lambda: relay._obs.get_program_screenshot(width=640))
                        if data is None:
                            return self._send({"error": "preview unavailable",
                                               "note": note}, 503)
                        return self._send_jpeg(data)
                    if p == ["cockpit", "program-audio"]:
                        if self._console_auth() is None:
                            return None
                        if program_audio_service is None:
                            return self._send({"error": "program audio disabled"}, 404)
                        # ?probe=1 -> availability only (still auth-gated above). Must NOT
                        # acquire() -> never spins up the encoder / touches the listener count.
                        if _program_audio_is_probe(self.path):
                            if not getattr(relay, "fanout", False):
                                return self._send({"error": "program audio unavailable"}, 404)
                            return self._send({"available": True})
                        ring = program_audio_service.acquire()
                        if ring is None:
                            return self._send({"error": "program audio unavailable"}, 404)
                        try:
                            return self._stream_ring(ring, PROGRAM_AUDIO_CONTENT_TYPE,
                                                     program_audio_service)
                        finally:
                            program_audio_service.release()
                    if p == ["cockpit", "timer"]:
                        if self._console_auth() is None:
                            return None
                        if not timer_store:
                            return self._send({"error": "timer disabled"}, 404)
                        return self._send(timer_store.data())
                    if p == ["cockpit", "chat", "data"]:
                        if self._console_auth() is None:
                            return None
                        if not chat_store:
                            return self._send({"error": "chat disabled"}, 404)
                        return self._send(chat_store.data())
                    if p == ["cockpit", "cues"]:
                        me = self._console_auth()
                        if me is None:
                            return None
                        if not cue_store:
                            return self._send({"error": "cues disabled"}, 404)
                        return self._send({"cues": cue_admin.active_cues_for(
                            cue_store.list(), me, time.time())})
                    if p == ["cockpit", "rc-notes"]:
                        # RC -> commentator notes (#376): identity-scoped rolling
                        # window for the cockpit's "Race Control" card.
                        me = self._console_auth()
                        if me is None:
                            return None
                        if not cue_store:
                            return self._send({"error": "cues disabled"}, 404)
                        return self._send({"notes": cue_admin.race_control_notes_for(
                            cue_store.list(), me)})
                    if p == ["cockpit", "versions"]:
                        # Producer-to-producer takeover pull. This path SITS UNDER
                        # /cockpit, so it IS reachable via Funnel — it must therefore
                        # authenticate, not rely on obscurity. Commentator tokens are
                        # per-commentator; this is gated on the shared league secret
                        # (every producer of the league holds it), constant-time.
                        presented = self.headers.get("X-Console-Secret")
                        if not console_auth.secret_matches(presented, console_secret):
                            return self._send({"error": "unauthorized"}, 401)
                        if not console_versions_path:
                            return self._send({"versions": {}})
                        return self._send({"versions":
                            console_admin.load_versions(console_versions_path)})
                    if p == ["cockpit", "graphics"]:
                        if self._console_auth() is None:
                            return None
                        return self._send({"graphics": list_graphics(graphics_dir)})
                    if len(p) == 3 and p[:2] == ["cockpit", "graphics"]:
                        if self._console_auth() is None:
                            return None
                        hit = resolve_graphic(graphics_dir, unquote(p[2]))
                        if not hit:
                            return self._send({"error": "graphic not found"}, 404)
                        return self._send_file(hit[0], "image/png")
                    return self._send({"error": "unknown", "path": self.path}, 404)
                if p == ["graphics"]:
                    # Root graphics browser: same read-only list as /cockpit/graphics
                    # but WITHOUT console auth, so the Director Panel widget loads on
                    # the token-less tailnet /panel too. Reached via /console/graphics
                    # as well (the gate authorizes it ANY, then falls through here).
                    # The tailnet is the trust boundary (like /status, /schedule/data);
                    # only /console is funnelled, so this never leaves the tailnet.
                    return self._send({"graphics": list_graphics(graphics_dir)})
                if len(p) == 2 and p[0] == "graphics":
                    hit = resolve_graphic(graphics_dir, unquote(p[1]))
                    if not hit:
                        return self._send({"error": "graphic not found"}, 404)
                    return self._send_file(hit[0], "image/png")
                if p == ["submissions"]:
                    # Director pending-submissions list (issue #193). Tailnet-only:
                    # it is NOT under the funnelled /cockpit prefix, so the public
                    # ingress never reaches it. Available even when the cockpit is
                    # disabled (returns []) so the panel section is always safe.
                    pend = submission_store.list() if submission_store else []
                    return self._send({"pending": pend})
                if p == ["schedule", "data"]:
                    rows = relay.source.get_rows()
                    # Deliberately the PHYSICAL pull row, NOT live_row_map()/
                    # on_air_row_idx(): the Director Panel schedule EDITOR's "live"
                    # marker must warn which row a feed is actually pulling (so a
                    # director never edits a live pull's URL), which stays the
                    # slot-head row during a same-URL back-to-back continuation.
                    # Do not "unify" this with the display-row map used elsewhere
                    # (RC/stint-plan/HUD) — that would regress the URL-edit safety.
                    live = {f.idx: k for k, f in relay.feeds.items()}
                    return self._send({"rows": [{"row": i + 1, "sheetRow": line,
                                                 "url": u, "name": n, "stint": st,
                                                 "live": live.get(i)}
                                                for i, (u, n, st, line) in enumerate(rows)],
                                       "source": relay.source.health()})
                if p == ["substitution", "latest"]:
                    # Director-panel read side for the ad-hoc stream-substitution
                    # section. Root path; mirrored at /console/substitution/latest
                    # (director-gated) via console_policy + the gate's ALLOW
                    # fall-through, so a director reaches it over the Funnel.
                    return self._send({"substitution": relay.latest_substitution()})
                if p == ["crew", "data"]:
                    # Tailnet-only crew roster view (#216 phase 5). A ROOT path —
                    # NOT under the funnelled /console prefix, so the public
                    # ingress never reaches it (same trust model as
                    # /schedule/data). Lets the `racecast links` CLI enumerate
                    # Crew ∪ Schedule over loopback. Empty when crew is disabled.
                    rows = crew_source.get_full() if crew_source else []
                    return self._send({"rows": [
                        {"name": n, "director": bool(d), "producer": bool(pr),
                         "commentator": bool(c), "race_control": bool(rc),
                         "discord": x or ""}
                        for (n, d, pr, c, rc, x) in rows]})
                if p == ["event-notes", "data"]:
                    # League-owner notes shown as a modal in the three console
                    # pages. Read-only; one shared list for all roles. Mirrored
                    # at /console/event-notes/data (ANY-auth) via the gate's
                    # generic ALLOW fall-through. Disabled -> available:false ->
                    # the front-end hides the button.
                    notes = event_notes_source.get() if event_notes_source else []
                    return self._send({"available": bool(notes), "notes": notes})
                if p == ["qualifying", "data"]:
                    qs = relay.qual_source
                    if not qs:
                        return self._send({"available": False, "mode": relay.mode,
                                           "rows": []})
                    qrows = qs.get_rows()
                    live = {f.idx: k for k, f in relay.feeds.items()} if relay.mode == "qualifying" else {}
                    return self._send({"available": True, "mode": relay.mode,
                                       "rows": [{"row": i + 1, "sheetRow": line,
                                                 "url": u, "name": n, "stint": st,
                                                 "live": live.get(i)}
                                                for i, (u, n, st, line) in enumerate(qrows)],
                                       "source": qs.health()})
                if len(p) == 2 and p[0] == "mode":
                    res = relay.set_mode(p[1].lower())
                    if "error" not in res:
                        # On a successful switch the new schedule's stint is on
                        # air -> auto-fill the HUD Streamer/Stint from it
                        # (issue #112 path).
                        if setup_ctl:
                            _push_live_schedule(relay, setup_ctl)
                        # Re-point the broadcast-part pointer to the new mode's
                        # first part, unless a part is currently live (never
                        # disturb an on-air broadcast). Keeps the mode-gated
                        # Parts control coherent across a live
                        # race<->qualifying switch.
                        if part_store is not None and not part_store.get().get("live"):
                            part_store.reset()
                    return self._send(res)
                if p == ["next"]:
                    result = relay.next_auto()
                    # One-button handover: next_auto cuts OBS back to the Stint
                    # scene itself, so no STINT macro press follows to clear Race
                    # Control. Mirror that macro's rc:"" here when a real cut
                    # happened. Best-effort: set_field no-ops without the webhook.
                    if result.get("obs_cut") and setup_ctl:
                        setup_ctl.set_field("racecontrol", "")
                    # Auto-follow the on-air stint's Streamer + Stint label from the
                    # Schedule (issue #112) on a real cut OR a same-URL continuation
                    # — the DISPLAY stint advances on a continuation too, even
                    # though OBS doesn't cut, so the HUD label must follow it.
                    if should_push_live_schedule(result) and setup_ctl:
                        _push_live_schedule(relay, setup_ctl)
                    return self._send(result)
                if p == ["reload"]:                     return self._send(relay.reload())
                if p == ["pov", "reload"]:              return self._send(relay.pov_reload())
                if p == ["pov", "stop"]:                return self._send(relay.pov_stop())
                if p == ["pov", "toggle"]:              return self._send(relay.pov_toggle())
                if p == ["pov", "show"]:                return self._send(relay.set_pov_shown(True))
                if p == ["pov", "hide"]:                return self._send(relay.set_pov_shown(False))
                if len(p)==3 and p[0]=="feed" and p[2]=="activate":
                    return self._send(relay.feed_activate(p[1]))
                if len(p)==3 and p[0]=="feed" and p[2]=="deactivate":
                    return self._send(relay.feed_deactivate(p[1]))
                if len(p)==4 and p[0]=="feed" and p[2]=="quality":
                    # #493 GET form of the quality switch (path tier) — for Companion's
                    # generic-http GET buttons, mirroring /reload/A & /set/A/n. Loopback/
                    # tailnet only (NOT under the /console mount), so it never leaves the
                    # tailnet; directors over the Funnel use the POST /console form instead.
                    which = (p[1] or "").upper(); tier = parse_quality_tier(p[3])
                    if which not in ("A", "B") or tier is None:
                        return self._send({"error": "usage: GET /feed/<A|B>/quality/"
                                           "<full|robust|emergency|auto>"}, 400)
                    return self._send(relay.set_feed_quality(which, tier))
                if len(p)==2 and p[0]=="next":          return self._ok(relay.advance(p[1], +2))
                if len(p)==2 and p[0]=="prev":          return self._ok(relay.advance(p[1], -2))
                if len(p)==2 and p[0]=="reload":        return self._ok(relay.reload(p[1]))
                if len(p)==3 and p[:2]==["resync","stint"]:
                    res = relay.resync_to_stint(int(p[2]))
                    # Puts a stint on air -> same HUD auto-write as /set/stint.
                    if setup_ctl:
                        _push_live_schedule(relay, setup_ctl)
                    return self._send(res)
                if len(p)==3 and p[:2]==["set","stint"]:
                    res = relay.set_stint(int(p[2]))
                    # Producer takeover puts a fresh stint on air -> same HUD
                    # auto-write as /next (issue #112). No obs_cut gate here: the
                    # director picks the scene, the HUD reflects the takeover stint.
                    if setup_ctl:
                        _push_live_schedule(relay, setup_ctl)
                    return self._send(res)
                if len(p)==3 and p[0]=="set":           return self._ok(relay.set_index(p[1], int(p[2])))
                return self._send({"error":"unknown","path":self.path}, 404)
            except ConnectionError:
                return None              # client hung up mid-response — benign (issue #25)
            except Exception as e:
                try:
                    return self._send({"error": str(e)}, 500)
                except ConnectionError:
                    return None          # client also gone before the error could be sent
        def _ok(self, r):
            self._send(r) if r else self._send({"error":"feed? (A/B)"}, 404)
        def do_POST(self):
            p = [x for x in urlparse(self.path).path.split("/") if x]
            try:
                if p and p[0] == "console":
                    p = self._console_gate(p, "POST")
                    if p is None:
                        return None     # gate already sent its response (401/403/404)
                length = int(self.headers.get("Content-Length") or 0)
                if length > 65536:
                    return self._send({"error": "body too large"}, 413)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    return self._send({"error": "body must be JSON"}, 400)
                if not isinstance(body, dict):
                    return self._send({"error": "body must be a JSON object"}, 400)
                if p[:1] == ["cockpit"]:
                    if not self._cockpit_active():
                        return self._send({"error": "cockpit not configured"}, 404)
                    if p == ["cockpit", "chat", "send"]:
                        me = self._console_auth()
                        if me is None:
                            return None
                        if not chat_store:
                            return self._send({"error": "chat disabled"}, 404)
                        # Key the limit on the authed identity, not the socket IP:
                        # behind Funnel every commentator shares one proxy IP, so an
                        # IP key would throttle the whole crew together (#191 review).
                        if not _cockpit_chat_rl.allow(me):
                            return self._send({"error": "rate limited"}, 429)
                        # Identity is the token's streamer, never client-declared.
                        name = cockpit_display_name(relay.source.get_rows(), me)
                        return self._send(chat_store.add(user=name,
                                                         text=body.get("text")))
                    if p == ["cockpit", "submit"]:
                        # Public write path (issue #193): a commentator proposes a
                        # stream link for one of THEIR OWN stints. Token-auth +
                        # per-identity rate limit + is_channel() SSRF guard +
                        # server-side ownership check. NEVER goes live here — it
                        # lands as pending for director approval in /panel.
                        me = self._console_auth()
                        if me is None:
                            return None
                        if not submission_store:
                            return self._send({"error": "submissions disabled"}, 404)
                        if not _cockpit_submit_rl.allow(me):
                            return self._send({"error": "rate limited"}, 429)
                        url = (body.get("url") or "").strip()
                        if not is_channel(url):
                            return self._send(
                                {"error": "url must be a watch URL or UC… channel ID"}, 400)
                        rows = relay.source.get_rows()
                        ok, res = own_submission_target(
                            rows, me, stint=body.get("stint"), row=body.get("row"))
                        if not ok:
                            return self._send({"error": res}, 400)
                        entry = submission_store.add(
                            streamer_key=me,
                            streamer_name=res["streamer_name"] or me,
                            target_line=res["target_line"],
                            target_stint=res["target_stint"],
                            proposed_url=url, prev_url=res["prev_url"],
                            now=time.time(), mode=relay.mode)
                        # Director notification: panel badge polls /submissions;
                        # also fire a Discord @here ping (no-op without a webhook).
                        self._notify_submission(entry,
                                                len(submission_store.list()))
                        return self._send({"ok": True, "id": entry["id"],
                                           "stint": entry["target_stint"]})
                    if p == ["cockpit", "cues", "ack"]:
                        me = self._console_auth()
                        if me is None:
                            return None
                        if not cue_store:
                            return self._send({"error": "cues disabled"}, 404)
                        if not _cockpit_cue_ack_rl.allow(me):
                            return self._send({"error": "rate limited"}, 429)
                        try:
                            cid = int(body.get("id"))
                        except (TypeError, ValueError):
                            return self._send({"error": "id must be an integer"}, 400)
                        return self._send(cue_store.ack(cid, me))
                    if p == ["cockpit", "cue-back"]:
                        # Commentator -> director cue-back (#377). Identity-scoped
                        # (the sender name is the token's streamer, never client-
                        # declared, like chat); stored as origin="commentator" so it
                        # surfaces only on the Director Panel, never in any cockpit.
                        me = self._console_auth()
                        if me is None:
                            return None
                        if not cue_store:
                            return self._send({"error": "cues disabled"}, 404)
                        if not _cockpit_cueback_rl.allow(me):
                            return self._send({"error": "rate limited"}, 429)
                        name = cockpit_display_name(relay.source.get_rows(), me)
                        return self._send(cue_store.add(
                            target=cue_admin.CUE_BACK_TARGET, level="info",
                            text=body.get("text"), from_name=name,
                            origin=cue_admin.ORIGIN_COMMENTATOR))
                    return self._send({"error": "unknown", "path": self.path}, 404)
                if p == ["chat", "send"]:
                    if not chat_store:
                        return self._send({"error": "chat disabled"}, 404)
                    return self._send(chat_store.add(
                        user=body.get("user"), text=body.get("text")))
                if p == ["substitution", "note"]:
                    return self._send(relay.annotate_substitution_reason(body.get("reason")))
                if p == ["cues", "send"]:
                    if not cue_store:
                        return self._send({"error": "cues disabled"}, 404)
                    rows = relay.source.get_rows()
                    live_idx = relay.on_air_row_idx()
                    cur = live_schedule_row(rows, live_idx)
                    on_air_key = asset_key(cur["streamer"]) if cur else None
                    target = cue_admin.resolve_target(body.get("target"),
                                                      on_air_key, asset_key)
                    if not target:
                        return self._send({"error": "unknown target (nobody on air?)"}, 400)
                    return self._send(cue_store.add(
                        target=target, level=(body.get("level") or "").strip(),
                        text=body.get("text")))
                if p == ["race-control", "cues"]:
                    # RC -> commentator note (#376). Authorized in _console_gate
                    # (race_control capability); reachable only under /console.
                    # Stored as an info-level cue with origin="race_control" so it
                    # renders in the cockpit's rolling RC card, never as a director
                    # toast. Target resolves like a director cue (on-air/all/name).
                    if not cue_store:
                        return self._send({"error": "cues disabled"}, 404)
                    rows = relay.source.get_rows()
                    live_idx = relay.on_air_row_idx()
                    cur = live_schedule_row(rows, live_idx)
                    on_air_key = asset_key(cur["streamer"]) if cur else None
                    target = cue_admin.resolve_target(body.get("target"),
                                                      on_air_key, asset_key)
                    if not target:
                        return self._send({"error": "unknown target (nobody on air?)"}, 400)
                    return self._send(cue_store.add(
                        target=target, level="info", text=body.get("text"),
                        from_name=cue_admin.RACE_CONTROL_FROM,
                        origin=cue_admin.ORIGIN_RACE_CONTROL))
                if p[:1] == ["submissions"]:
                    # Director approve/reject (issue #193). Tailnet-only (not
                    # funnelled). Reject needs no webhook; approve writes the
                    # schedule via the existing setup_ctl.schedule_set path.
                    if not submission_store:
                        return self._send({"error": "submissions disabled"}, 404)
                    if p == ["submissions", "reject"]:
                        entry = submission_store.pop(body.get("id"), "reject")
                        if entry is None:
                            return self._send({"error": "no such submission"}, 404)
                        return self._send({"ok": True, "id": entry["id"]})
                    if p == ["submissions", "approve"]:
                        if not setup_ctl:
                            return self._send({"error": "setup control disabled"}, 404)
                        entry = submission_store.get(body.get("id"))
                        if entry is None:
                            return self._send({"error": "no such submission"}, 404)
                        # Same mechanism the panel's Schedule editor uses: write
                        # the Sheet; the URL applies on the next /reload (the relay
                        # never tears a feed mid-stint). Pass the row's own
                        # streamer + stint so the optimistic local inject keeps
                        # them (both are Configuration vocab, from the sheet). Only
                        # clear the pending entry once the write actually succeeds.
                        # Branch on the ENTRY's recorded mode (not the relay's
                        # current mode) so a director can approve a qualifying
                        # submission into the Qualifying tab regardless of what
                        # mode the relay happens to be in right now.
                        writer = (setup_ctl.qualifying_set
                                  if entry.get("mode") == "qualifying"
                                  else setup_ctl.schedule_set)
                        res = writer(
                            entry["target_line"], url=entry["proposed_url"],
                            name=entry["streamer_name"], stint=entry["target_stint"])
                        if res.get("error"):
                            return self._send(res)
                        submission_store.pop(entry["id"], "approve")
                        # Heads-up to the crew that the link is now scheduled —
                        # deliberately no @here ping (no-op without a webhook).
                        self._notify_approval(entry)
                        return self._send({"ok": True, "id": entry["id"], **res})
                    return self._send({"error": "unknown", "path": self.path}, 404)
                if p == ["event", "title"]:
                    # Director live-edit of the free-text event title (#207). Tailnet
                    # trust boundary like the rest of the panel; persisted to
                    # event.json so it survives a restart and a takeover pull.
                    if not event_store:
                        return self._send({"error": "event title disabled"}, 404)
                    title = event_store.set(body.get("title"))
                    return self._send({"ok": True, "title": title})
                if p == ["obs", "scene"]:
                    if _obs_ws is None:
                        return self._send({"error": "obs unavailable"}, 503)
                    transition = body.get("transition")
                    duration = body.get("duration")
                    if duration is not None:
                        try:
                            duration = max(0, min(10000, int(duration)))
                        except (TypeError, ValueError):
                            duration = None
                    ok, note = relay._obs.set_current_program_scene(
                        body.get("scene"), transition=transition, duration_ms=duration)
                    if not ok:
                        return self._send({"ok": False, "error": note}, 503)
                    return self._send({"ok": True, "note": note} if note
                                      else {"ok": True})
                if p == ["obs", "source"]:
                    if _obs_ws is None:
                        return self._send({"error": "obs unavailable"}, 503)
                    ok, note = relay._obs.set_scene_item_enabled(
                        body.get("scene"), body.get("source"), bool(body.get("on")))
                    return self._send({"ok": True} if ok
                                      else {"ok": False, "error": note}, 200 if ok else 503)
                if p == ["obs", "audio"]:
                    if _obs_ws is None:
                        return self._send({"error": "obs unavailable"}, 503)
                    inp = body.get("input")
                    if "mute" in body:
                        ok, note = relay._obs.set_input_mute(inp, bool(body.get("mute")))
                    elif "db" in body:
                        ok, note = relay._obs.set_input_volume(inp, body.get("db"))
                    else:
                        return self._send({"ok": False, "error": "audio needs db or mute"}, 400)
                    return self._send({"ok": True} if ok
                                      else {"ok": False, "error": note}, 200 if ok else 503)
                if p == ["obs", "split-audio"]:
                    payload, status = apply_split_audio(relay, relay._obs)
                    return self._send(payload, status)
                if p == ["obs", "split"]:
                    payload, status = apply_split_state(relay, relay._obs)
                    return self._send(payload, status)
                if p == ["obs", "stint"]:
                    payload, status = apply_stint_state(relay, relay._obs, body.get("feed"))
                    return self._send(payload, status)
                if p == ["obs", "stream"]:
                    if _obs_ws is None:
                        return self._send({"error": "obs unavailable"}, 503)
                    if "on" not in body:
                        return self._send({"ok": False,
                                           "error": "stream needs on"}, 400)
                    ok, note = relay._obs.set_stream(bool(body.get("on")))
                    return self._send({"ok": True} if ok
                                      else {"ok": False, "error": note},
                                      200 if ok else 503)
                if p == ["obs", "state"]:
                    if _obs_ws is None:
                        return self._send({"error": "obs unavailable"}, 503)
                    sources = [(s.get("scene"), s.get("source"))
                               for s in (body.get("sources") or [])]
                    state, note = relay._obs.read_obs_state(sources, body.get("inputs") or [])
                    if state is None:
                        return self._send({"ok": False, "error": note}, 503)
                    return self._send({"ok": True, **state})
                if p == ["obs", "refresh"]:
                    # Reload the relay-served OBS browser sources (HUD / overlay /
                    # timer) — the programmatic right-click -> Refresh. Unconditional
                    # force (no hash gate; the CLI owns obs-pages.hash): the director
                    # presses this precisely to clear stale caches. Best-effort like
                    # the other /obs/* branches. Auto director-gated by console_policy
                    # (p[0] == "obs"); reachable over Funnel only under /console.
                    if _obs_ws is None:
                        return self._send({"error": "obs unavailable"}, 503)
                    port = self.server.server_address[1]
                    names, note = relay._obs.refresh_browser_inputs(
                        needle=f"127.0.0.1:{port}")
                    return self._send({"ok": True, "count": len(names),
                                       "note": note or
                                       f"Refreshed {len(names)} browser source(s)"})
                if p == ["obs", "rebuild-rearm"]:
                    # #582: lift the auto-rebuild stand-down once the director has found
                    # the cause. Needs no OBS call. Director-gated (p[0]=="obs").
                    return self._send({"ok": True,
                                       "rearmed": relay.rearm_rebuild_guard("director")})
                if p == ["obs", "feed-reset"]:
                    # Manual: force OBS to reconnect ONE feed's media source. The new
                    # consumer re-joins with a fresh demuxer at prebuffer_s behind the
                    # live edge, so this is both the targeted fix for a frozen/stuttering
                    # picture and the director's deliberate backlog resolution (#587):
                    # everything OBS lagged beyond the reserve is dropped, at the cost of
                    # a short black dropout if the feed is on air. The panel shows that
                    # cost (reset_discards_s on /status) before the click. Same primitive
                    # the automatic drop-recovery uses (_obs_reconnect_now); only a human
                    # triggers it to shed a backlog. Director-gated (p[0]=="obs").
                    if _obs_ws is None:
                        return self._send({"error": "obs unavailable"}, 503)
                    key = feed_reset_target(body.get("feed"), relay.feeds)
                    if key is None:
                        return self._send({"ok": False,
                            "error": f"unknown feed {body.get('feed')!r}"}, 400)
                    names, note = relay._obs.release_feed_inputs(ports=[relay.feeds[key].port])
                    return self._send({"ok": True, "feed": key, "count": len(names),
                                       "rebuilt": names,
                                       "note": note or f"Reconnected OBS to Feed {key}"})
                if len(p) == 3 and p[0] == "feed" and p[2] == "quality":
                    # Manual quality-profile control (#493): director pins a feed
                    # to a lower-bandwidth streamlink profile, or releases it back
                    # to auto-managed FULL. No URL in the request -> no SSRF surface.
                    # Director-gated by console_policy (p[0]=="feed", like activate/
                    # deactivate); reachable over Funnel only under /console.
                    which = (p[1] or "").upper()
                    tier = parse_quality_tier(body.get("tier"))
                    if which not in ("A", "B") or tier is None:
                        return self._send({"ok": False,
                            "error": "usage: POST /feed/<A|B>/quality {\"tier\": full|robust|emergency|auto}"}, 400)
                    return self._send({"ok": True, **relay.set_feed_quality(which, tier)})
                if p == ["parts", "start"]:
                    if part_store is None or producer_source is None or _obs_ws is None:
                        return self._send({"ok": False, "error": "parts unavailable"}, 503)
                    rows = producer_mod.active_producer_rows(
                        producer_source.get(), relay.mode)
                    ok, res = parts_mod.validate_start(body, rows, part_store.get())
                    if not ok:
                        return self._send({"ok": False, "error": res[0]}, res[1])
                    idx = res
                    st, _n = relay._obs.read_obs_state([], [])
                    if (isinstance(st, dict) and isinstance(st.get("stream"), dict)
                            and st["stream"].get("active")):
                        return self._send({"ok": False,
                            "error": "already streaming — end the current Part first"}, 409)
                    ref = (rows[idx - 1].get("stream_key") or "").strip()
                    if not ref:
                        return self._send({"ok": False,
                            "error": "Part {} has no stream-key reference "
                                     "(Producer tab)".format(idx)}, 400)
                    ok2, note = apply_stream_service_for_ref(
                        ref, channel_csv_url, push_url, relay._obs.set_stream_service)
                    if not ok2:
                        return self._send({"ok": False, "error": note}, 502)
                    ok3, note3 = relay._obs.set_stream(True)
                    if not ok3:
                        return self._send({"ok": False, "error": note3}, 503)
                    part_store.mark_live(idx)
                    plabel = (rows[idx - 1].get("part")
                              if 1 <= idx <= len(rows) else "") or f"Part {idx}"
                    if health_store is not None:
                        try:
                            health_store.record_event(
                                time.time(), "part_start",
                                label=f"{plabel} started",
                                producer=relay.producer_name,
                                metadata={"index": idx})
                        except Exception:   # noqa: BLE001 — best-effort
                            pass
                    # Echo the applied part label + active mode so the panel/operator can
                    # confirm WHICH part (and mode) went live — a race-vs-qualifying
                    # mismatch (wrong stream key) is then obvious at the moment of Start.
                    return self._send({"ok": True, "index": idx,
                                       "part": plabel, "mode": relay.mode})
                if p == ["parts", "end"]:
                    if part_store is None or _obs_ws is None:
                        return self._send({"ok": False, "error": "parts unavailable"}, 503)
                    rows = (producer_mod.active_producer_rows(producer_source.get(), relay.mode)
                            if producer_source is not None else [])
                    ok, res = parts_mod.validate_end(body, rows, part_store.get())
                    if not ok:
                        return self._send({"ok": False, "error": res[0]}, res[1])
                    ok2, note = relay._obs.set_stream(False)
                    if not ok2:
                        return self._send({"ok": False, "error": note}, 503)
                    pre_vm = parts_mod.parts_view_model(rows, part_store.get(),
                                                        stream_active=True)
                    is_last = bool(pre_vm.get("live") and pre_vm.get("count")
                                   and pre_vm.get("index") == pre_vm.get("count"))
                    part_store.end()
                    if health_store is not None:
                        try:
                            plabel = (rows[res - 1].get("part")
                                      if 1 <= res <= len(rows) else "") or f"Part {res}"
                            health_store.record_event(
                                time.time(), "part_end",
                                label=f"{plabel} ended",
                                producer=relay.producer_name,
                                metadata={"index": res})
                        except Exception:   # noqa: BLE001 — best-effort
                            pass
                    if is_last:
                        self._send({"ok": True, "index": res, "final": True})
                        relay._spawn_event_stop()
                        return
                    return self._send({"ok": True, "index": res})
                if not setup_ctl:
                    return self._send({"error": "setup control disabled"}, 404)
                if p == ["schedule", "set"]:
                    return self._send(setup_ctl.schedule_set(
                        body.get("row"), body.get("url"), body.get("name"),
                        body.get("stint")))
                if p == ["qualifying", "set"]:
                    return self._send(setup_ctl.qualifying_set(
                        body.get("row"), body.get("url"), body.get("name"),
                        body.get("stint")))
                if p == ["crew", "set"]:
                    return self._send(setup_ctl.crew_set(
                        body.get("row"), body.get("name"),
                        body.get("director"), body.get("producer"),
                        body.get("commentator"), body.get("race_control"),
                        body.get("discord")))
                if p == ["crew", "delete"]:
                    return self._send(setup_ctl.crew_delete(body.get("row")))
                if p == ["pov", "set"]:
                    return self._send(setup_ctl.pov_set(body.get("url"),
                                                        body.get("name")))
                if p == ["setup", "teams"]:
                    return self._send(setup_ctl.set_teams(body.get("teams")))
                return self._send({"error": "unknown", "path": self.path}, 404)
            except ConnectionError:
                return None              # client hung up mid-response — benign (issue #25)
            except Exception as e:
                try:
                    return self._send({"error": str(e)}, 500)
                except ConnectionError:
                    return None          # client also gone before the error could be sent
    return H


def poller(source, interval, stop_evt):
    # Guarded: refresh() is written to be total, but if one ever escaped, an
    # unguarded loop would die and leave the source frozen on last-good data for
    # the rest of the relay run -- silently, which is the expensive part (a frozen
    # on-air overlay with nothing in the log). Log it and keep polling instead.
    while not stop_evt.wait(interval):
        try:
            source.refresh()
        except Exception as e:
            LOG.warning("sheet poll raised (%s: %s) — continuing",
                        type(e).__name__, e)


def quali_poller(hud_source, interval, stop_evt):
    """Re-read the optional Quali Times tab on its own SLOW cadence, off the HUD
    refresh path entirely (issue #555): no quali-tab state may delay an on-air HUD
    refresh or a panel-push confirm. refresh_quali() never raises; the guard is
    there so a future change cannot kill this thread unnoticed."""
    while not stop_evt.wait(interval):
        try:
            hud_source.refresh_quali()
        except Exception as e:
            LOG.warning("quali times poll raised (%s: %s) — continuing",
                        type(e).__name__, e)


# ---------- GT7 UDP telemetry listener (solo/POV only, #324) ----------------
GT7_RECV_PORT = 33740
GT7_SEND_PORT = 33739
GT7_HEARTBEAT_S = 10.0


def _telemetry_loop(store, ps_ip, stop_evt):
    """Bind 33740, send a heartbeat every ~10 s, decrypt+parse+feed each packet.
    Best-effort: any error logs and the loop keeps running; no console -> idle."""
    tlog = logging.getLogger("racecast.relay.telemetry")
    sock = None
    last_hb = 0.0
    dest = ps_ip
    while not stop_evt.is_set():
        try:
            if sock is None:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.bind(("0.0.0.0", GT7_RECV_PORT))
                sock.settimeout(1.0)
            now = time.monotonic()
            if now - last_hb >= GT7_HEARTBEAT_S:
                target = dest or "255.255.255.255"
                try:
                    sock.sendto(b"A", (target, GT7_SEND_PORT))
                except OSError as e:
                    tlog.warning("heartbeat send failed: %s", e)
                last_hb = now
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            if dest is None:                 # discovery: latch the responder
                dest = addr[0]
                store.set_source(dest)
                tlog.info("GT7 console discovered at %s", dest)
            elif addr[0] != dest:
                continue                      # ignore packets from any other host once the
                                             # console is latched/pinned — a rogue LAN host can
                                             # forge valid telemetry (the key is fixed+public)
            plain = gt7_crypto.decrypt_packet(data)
            if plain is None:
                continue
            store.update(gt7_telemetry.parse_packet(plain), time.time())
        except OSError as e:
            tlog.warning("telemetry socket error: %s — reopening", e)
            try:
                if sock:
                    sock.close()
            except OSError:
                pass  # already closed/gone -- reopening on the next loop iteration
            sock = None
            stop_evt.wait(1.0)
        except Exception as e:                 # never let the thread die silently
            tlog.error("telemetry loop error: %s", e)
            stop_evt.wait(1.0)


def cookie_health(path, now=None, max_age_hours=COOKIE_MAX_AGE_H):
    """Cookie staleness for /status, computed on demand — during a 24 h event the
    cookies age while the relay runs, so this must be live, not a startup snapshot.

    The age is the EXPORT age (cookie_jar's stamp), not the jar's mtime: yt-dlp
    rewrites the jar on every resolve, so mid-event its mtime is always minutes old
    and the banner could never fire. A jar with no stamp reports age_h=None and is
    not stale — unknown is never reported as old, and never as fresh either.
    Running cookie-less (path None / file gone) is a legitimate configuration
    (public streams): present=False, stale=False — the panel raises its cookie
    banner only on stale=True."""
    try:
        present = bool(path) and os.path.isfile(path)
    except OSError:
        present = False
    if not present:
        return {"present": False, "age_h": None, "stale": False}
    age_h = cookie_jar.export_age_h(path, now=now)
    if age_h is None:
        return {"present": True, "age_h": None, "stale": False}
    return {"present": True, "age_h": age_h, "stale": age_h > max_age_hours}


def _cookie_hint(stderr_text, browser):
    """failure_hint() from the sibling get-cookies.py — one source of truth
    for the actionable export-failure hints (locked DB, no profile, DPAPI)."""
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(
        "get_cookies", os.path.join(here, "get-cookies.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.failure_hint(stderr_text, browser)


def export_cookies(browser, out):
    """Export YouTube cookies from a logged-in browser to a Netscape yt-cookies.txt
    using yt-dlp. Best-effort: returns True on success. yt-dlp writes the whole
    browser profile into a private directory; only the youtube.com cookies reach
    *out* (#616), and a failed filter leaves the previous jar unchanged."""
    url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    try:
        with cookie_jar.private_export_path(out, warn=LOG.warning) as raw:
            try:
                proc = subprocess.run(["yt-dlp", "--cookies-from-browser", browser,
                                       "--cookies", raw, "--skip-download", "--no-warnings", url],
                                      timeout=90, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.PIPE, env=external_tool_env(),
                                      **_no_window_kwargs())
            except FileNotFoundError:
                LOG.warning("yt-dlp not found — cannot auto-export cookies."); return False
            except subprocess.TimeoutExpired:
                LOG.warning("cookie export timed out (Keychain prompt not approved?)."); return False
            if not os.path.exists(raw):
                err = (proc.stderr or b"").decode("utf-8", errors="replace")
                for line in [l for l in err.splitlines() if l.strip()][-1:]:
                    LOG.warning("%s", line)   # the real yt-dlp reason, not a guess
                LOG.warning("Cookie export from '%s': FAILED — %s", browser,
                            _cookie_hint(err, browser))
                return False
            dropped = cookie_jar.filter_jar(raw, "youtube", dest=out)
    except OSError as exc:
        LOG.warning("Cookie export from '%s': FAILED — cannot prepare it next to %s: %s",
                    browser, out, exc)
        return False
    if dropped is None:
        LOG.warning("Cookie export from '%s': FAILED — could not write the filtered "
                    "cookies to %s; the previous jar is unchanged.", browser, out)
        return False
    try: os.chmod(out, 0o600)   # live YouTube session — owner-only
    except OSError: pass        # best-effort hardening; never block the export
    cookie_jar.record_export(out)
    LOG.info("Cookie export from '%s': OK -> %s (kept only youtube.com cookies, dropped %d "
             "other lines)", browser, out, dropped)
    return True

def main():
    logsetup.harden_stdio()    # before argparse builds help text that may be non-ASCII
    load_dotenv(os.path.dirname(os.path.abspath(__file__)))  # before defaults are read
    ap = argparse.ArgumentParser(description="GT Racing 2-feed relay with Google-Sheet schedule")
    ap.add_argument("--sheet-id", default=os.environ.get("RACECAST_SHEET_ID"),
                    help="Google Sheet ID for the schedule/POV tabs. Default: env "
                         "RACECAST_SHEET_ID (injected by the CLI from the active profile).")
    ap.add_argument("--league-name", default=os.environ.get("RACECAST_PROFILE_NAME", ""),
                    help="League display name shown on the /console launcher. Default: env "
                         "RACECAST_PROFILE_NAME (injected by the CLI from the active profile).")
    ap.add_argument("--logo", default=os.environ.get("RACECAST_LOGO", ""),
                    help="Absolute path to the league logo image. Default: env "
                         "RACECAST_LOGO (injected by the CLI from the active profile).")
    ap.add_argument("--sheet-tab", default=DEFAULT_SHEET_TAB)
    ap.add_argument("--sheet-csv-url", default=None,
                    help="Full CSV URL (overrides sheet-id/tab; e.g. publish-to-web)")
    ap.add_argument("--pov-tab", default=DEFAULT_POV_TAB,
                    help="Google-Sheet tab holding the ad-hoc driver-POV URL (cell A2).")
    ap.add_argument("--pov-port", type=int, default=53003,
                    help="Local port for the driver-POV PiP feed (OBS 'Feed POV').")
    ap.add_argument("--no-pov", action="store_true",
                    help="Disable the driver-POV PiP feed entirely.")
    ap.add_argument("--qualifying-tab", default=DEFAULT_QUALIFYING_TAB,
                    help="Google-Sheet tab for the qualifying schedule (same "
                         "URL/Streamer/Stint structure as the race Schedule tab; "
                         "default 'Qualifying'). One stream, served on Feed A.")
    ap.add_argument("--crew-tab", default="Crew",
                    help="Sheet tab naming Director/Producer crew for /console roles "
                         "(#216); disabled with a custom --sheet-csv-url.")
    ap.add_argument("--channel-tab", default="Channel",
                    help="Sheet tab naming the event broadcast channel(s) for the "
                         "read-only broadcast-chat reader (#294); disabled with a "
                         "custom --sheet-csv-url or --no-broadcast-chat.")
    ap.add_argument("--no-broadcast-chat", action="store_true",
                    help="Disable the read-only YouTube broadcast-chat reader (#294).")
    ap.add_argument("--producer-tab", default="Producer",
                    help="Sheet tab mapping broadcast Part -> stream-key ref (#395)")
    ap.add_argument("--no-parts", action="store_true",
                    help="disable the Director-Panel broadcast Part control")
    ap.add_argument("--event-notes-tab", default="Event Notes",
                    help="Sheet tab name for the Event Notes modal "
                         "(default 'Event Notes'). Disabled by a custom "
                         "--sheet-csv-url or --no-event-notes.")
    ap.add_argument("--no-event-notes", action="store_true",
                    help="Disable the Event Notes reader/modal.")
    ap.add_argument("--qualifying", action="store_true",
                    help="Start in QUALIFYING mode: serve the qualifying tab on "
                         "Feed A instead of the race schedule (different-day "
                         "qualifying). Switch live via /mode/race | /mode/qualifying.")
    ap.add_argument("--no-qualifying", action="store_true",
                    help="Do not build the qualifying schedule source at all.")
    ap.add_argument("--poll", type=int, default=30, help="Sheet poll interval (seconds)")
    ap.add_argument("--schedule", default="schedule.txt", help="Local offline fallback")
    ap.add_argument("--http-port", type=int, default=8088)
    ap.add_argument("--runtime-dir", default=None,
                    help="Directory for runtime data (yt-cookies.txt, logs/, *.cache.txt). "
                         "Default: next to this script (keeps the distributed package "
                         "self-locating). The repo passes its runtime/ folder.")
    ap.add_argument("--bind", default="auto",
                    help="Address(es) the control/panel/HUD HTTP server binds to. "
                         "Default 'auto': binds 127.0.0.1 (for OBS, always) AND this "
                         "machine's Tailscale IP when present, so remote directors/"
                         "tablets reach /panel + /hud over the tailnet without "
                         "exposing the server on the local LAN. Pass '127.0.0.1' to "
                         "force local-only, or an explicit address (e.g. 0.0.0.0) to "
                         "override.")
    ap.add_argument("--no-panel", action="store_true",
                    help="Do not serve the director panel at /panel.")
    ap.add_argument("--overlay-tab", default="Overlay",
                    help="Google-Sheet tab with the live HUD values (default 'Overlay').")
    ap.add_argument("--config-tab", default="Configuration",
                    help="Google-Sheet tab with the team-to-brand map (default 'Configuration').")
    ap.add_argument("--quali-times-tab", default=DEFAULT_QUALI_TIMES_TAB,
                    help="Google-Sheet tab with per-car qualifying best laps "
                         "(default 'Quali Times'). Absent tab = blank lap slots.")
    ap.add_argument("--hud-poll", type=int, default=5,
                    help="HUD sheet refresh interval in seconds (default 5).")
    ap.add_argument("--no-hud", action="store_true",
                    help="Do not serve the HUD overlay at /hud.")
    ap.add_argument("--overlay-dir", default=None,
                    help="profiles/<name>/overlay dir with per-profile hud.css/"
                         "fonts (relay-served at /hud/override.css etc).")
    ap.add_argument("--timer-tab", default="Timer",
                    help="Google-Sheet tab holding the race-timer anchor (default 'Timer').")
    ap.add_argument("--no-timer", action="store_true",
                    help="Do not run the race timer (the HUD clock stays blank; "
                         "/timer/data and the /timer controls are disabled).")
    ap.add_argument("--solo", action="store_true",
                    default=(os.environ.get("RACECAST_KIND", "").strip().lower() == "solo"),
                    help="Solo mode: no Schedule/Qualifying tab and no A/B feeds "
                         "(the main program is a local capture card + webcam). The "
                         "Sheet, HUD, POV, timer, chat and console stay on. Defaults "
                         "on when RACECAST_KIND=solo (injected by the CLI for a "
                         "kind=solo profile).")
    ap.add_argument("--gt7-ps-ip", default=os.environ.get("RACECAST_GT7_PS_IP"),
                    help="PS4/PS5 IP for GT7 UDP telemetry (solo/POV). Empty -> "
                         "subnet-broadcast discovery.")
    ap.add_argument("--ports", default="53001,53002")
    ap.add_argument("--stint", type=int, default=1,
                    help="1-based stint that is ON AIR right now (producer takeover): "
                         "Feed A serves it, Feed B preloads the next one. Default 1.")
    ap.add_argument("--event-title", default=None,
                    help="Free-text event title shown in the Director Panel, the "
                         "Commentator Cockpit and Discord messages (#207). When given, "
                         "it is written to runtime event.json (overriding any saved/"
                         "EVENT_TITLE default); omit to keep the persisted title or fall "
                         "back to RACECAST_EVENT_TITLE.")
    ap.add_argument("--logdir", default="logs")
    ap.add_argument("--cookies", default=None,
                    help="Path to yt-cookies.txt (Netscape format) for YouTube login - "
                         "bypasses the 'Sign in to confirm you're not a bot' check. "
                         "Default: yt-cookies.txt next to this script, if present. "
                         "Twitch feeds use twitch-cookies.txt (picked up automatically "
                         "from the same directory — no separate flag needed).")
    ap.add_argument("--cookies-from-browser", default=None,
                    help="Auto-export YouTube cookies from this browser on startup via "
                         "yt-dlp (e.g. firefox, chrome, safari, edge, brave) and use them. "
                         "Writes yt-cookies.txt next to this script.")
    args = ap.parse_args()
    # line_buffering: show logs immediately. encoding="utf-8": the relay runs as a
    # daemon with stdout piped to a log file, so Python would otherwise use the
    # locale/ANSI codepage (cp1252 on Windows) and crash/mojibake on the non-ASCII
    # glyphs in our banner (-> arrows, em dashes) — same class as issue #24.
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception: pass   # not all stdout objects support reconfigure (e.g. pipes)

    if not args.sheet_csv_url and not args.sheet_id:
        sys.exit("ERROR: no Google Sheet configured. Set SHEET_ID in the active profile "
                 "(profiles/<name>/profile.env), or pass --sheet-id / --sheet-csv-url.")

    if args.stint < 1:
        sys.exit("ERROR: --stint must be >= 1 (1-based stint number, as in the sheet).")

    if not args.solo:
        # Solo has no A/B feeds; the tools are needed only by the optional POV feed,
        # which logs + idles if they are absent (best-effort). So solo starts without
        # yt-dlp/streamlink on PATH.
        for _tool in ("yt-dlp", "streamlink"):
            if not shutil.which(_tool):
                sys.exit(f"ERROR: '{_tool}' not found on PATH "
                         f"(brew install {_tool} / pip install -U {_tool}).")

    here = os.path.dirname(os.path.abspath(__file__))
    runtime = os.path.abspath(args.runtime_dir) if args.runtime_dir else default_runtime_dir(here)
    os.makedirs(runtime, exist_ok=True)
    try: os.chmod(runtime, 0o700)   # holds the cookie jar / caches — keep it private
    except OSError: pass            # best-effort hardening; never block startup
    logdir = args.logdir if os.path.isabs(args.logdir) else os.path.join(runtime, args.logdir)
    os.makedirs(logdir, exist_ok=True)

    logsetup.configure_logging("racecast.relay", os.path.join(logdir, "relay.console.log"))
    _keep = int(os.environ.get("RACECAST_LOG_RETENTION_DAYS") or logsetup.DEFAULT_RETENTION_DAYS)
    logsetup.prune_old_logs(logdir, keep_days=_keep)   # cleanup on every start

    local = args.schedule if os.path.isabs(args.schedule) else os.path.join(runtime, args.schedule)
    cache = os.path.join(runtime, "schedule.cache.txt")
    ports = [int(x) for x in args.ports.split(",")]

    csv_url = args.sheet_csv_url or (
        f"https://docs.google.com/spreadsheets/d/{args.sheet_id}"
        f"/gviz/tq?tqx=out:csv&sheet={quote(args.sheet_tab)}")

    LOG.info("relay starting — profile=%s bind=%s ports=%s mode=%s schedule=%s",
             (args.league_name or "?"), args.bind, args.ports,
             ("qualifying" if args.qualifying else "race"), csv_url)

    # Fail fast: the loopback control port is mandatory (OBS always reaches the relay
    # on 127.0.0.1). If another relay already holds it, abort BEFORE the network
    # refreshes below — otherwise the log reads like a successful start
    # ("Schedule loaded …") right before the bind fails. The real bind later is the
    # authoritative guard; this just turns a slow, misleading failure into a fast,
    # clear one.
    if not control_port_available("127.0.0.1", args.http_port):
        LOG.error("control port 127.0.0.1:%s already in use — another relay is "
                  "probably running; aborting before any startup work.", args.http_port)
        sys.exit(f"Could not bind the control server on 127.0.0.1 port {args.http_port} "
                 f"— another relay is probably already running. Stop it first "
                 f"('racecast relay stop'), then check 'racecast status' / 'racecast preflight' "
                 f"to see what holds the port.")

    # POV source: own sheet tab (cell A2). Derivable only from sheet-id/tab,
    # so a custom --sheet-csv-url disables POV (no tab to point at).
    pov_source = None
    if not args.no_pov and not args.sheet_csv_url:
        pov_source = pov_schedule_source(args.sheet_id, args.pov_tab, runtime)
        pov_source.refresh()   # non-fatal: empty cell / unreachable = POV simply off

    # Qualifying source: own sheet tab, same parser/structure as the race
    # schedule. Like POV it is derivable only from sheet-id/tab (a custom
    # --sheet-csv-url disables it). Single stream -> served on Feed A.
    qual_source = None
    if not args.no_qualifying and not args.sheet_csv_url and not args.solo:
        qual_source = qualifying_schedule_source(args.sheet_id, args.qualifying_tab, runtime)
        qual_source.refresh()   # non-fatal: empty/unreachable = qualifying mode just idles

    # Crew roster (#216): Name | Director | Producer tab giving the director/
    # producer capabilities for /console. Like POV/qualifying it is derivable
    # only from sheet-id/tab, so a custom --sheet-csv-url disables it. Missing or
    # empty tab is non-fatal -- roles just fall back to schedule-only commentator.
    crew_source = None
    if not args.sheet_csv_url:
        crew_csv_url = (f"https://docs.google.com/spreadsheets/d/{args.sheet_id}"
                        f"/gviz/tq?tqx=out:csv&sheet={quote(args.crew_tab)}")
        crew_cache = os.path.join(runtime, "crew.cache.txt")
        crew_source = CrewSource(crew_csv_url, crew_cache)
        crew_source.refresh()   # non-fatal: empty/unreachable = no director/producer rows

    # Broadcast-chat reader (#294): the Channel tab + an ephemeral in-memory
    # store. Derived from sheet-id/tab like the crew roster, so a custom
    # --sheet-csv-url (or --no-broadcast-chat) disables it. The supervisor thread
    # is started later, once cookies are resolved. channel_csv_url is hoisted so
    # both this reader and the Part control (#395) below can share it.
    channel_csv_url = None
    if not args.sheet_csv_url:
        channel_csv_url = (f"https://docs.google.com/spreadsheets/d/{args.sheet_id}"
                           f"/gviz/tq?tqx=out:csv&sheet={quote(args.channel_tab)}")
    channel_source = None
    broadcast_chat_store = None
    if channel_csv_url and not args.no_broadcast_chat:
        channel_cache = os.path.join(runtime, "channel.cache.txt")
        channel_source = ChannelSource(channel_csv_url, channel_cache)
        broadcast_chat_store = BroadcastChatStore()

    # Broadcast Part control (#395): the Producer-tab Part list + the persisted
    # part.json pointer. Disabled under a custom --sheet-csv-url or --no-parts;
    # part_store is always present (a missing file -> {index:1, live:False}).
    producer_source = None
    if channel_csv_url and not args.no_parts:
        # headers=1: pin sheet row 1 as the header so gviz never merges it into
        # row 1 (which happens when the Part column mixes text + numbers), which
        # would empty the parse and break the Parts control's stream-key lookup.
        producer_csv_url = (f"https://docs.google.com/spreadsheets/d/{args.sheet_id}"
                            f"/gviz/tq?tqx=out:csv&headers=1&sheet={quote(args.producer_tab)}")
        producer_source = ProducerSource(producer_csv_url,
                                         os.path.join(runtime, "producer.cache.txt"))
        producer_source.refresh()   # non-fatal: prime the Part list on startup
    part_store = PartStore(os.path.join(runtime, "part.json"))

    # Event Notes (#owner-notes): a read-only league-owner notes tab shown as a
    # modal in the three console pages. Derived from sheet-id/tab like the crew
    # roster, so a custom --sheet-csv-url (or --no-event-notes) disables it.
    event_notes_source = None
    if not args.sheet_csv_url and not args.no_event_notes:
        event_notes_csv_url = (f"https://docs.google.com/spreadsheets/d/{args.sheet_id}"
                               f"/gviz/tq?tqx=out:csv&sheet={quote(args.event_notes_tab)}")
        event_notes_cache = os.path.join(runtime, "event-notes.cache.txt")
        event_notes_source = EventNotesSource(event_notes_csv_url, event_notes_cache)
        event_notes_source.refresh()   # non-fatal: empty/unreachable = no notes

    # Optionally auto-export cookies from a logged-in browser first (yt-dlp)
    if args.cookies_from_browser:
        export_cookies(args.cookies_from_browser, os.path.join(runtime, "yt-cookies.txt"))

    # Cookies: explicit via --cookies, otherwise auto-detect yt-cookies.txt (+ one-time migration)
    cookies = args.cookies
    if cookies is None:
        auto = migrate_legacy_cookie(runtime)   # yt-cookies.txt (+ one-time rename)
        cookies = auto if os.path.exists(auto) else None
    elif not os.path.isabs(cookies):
        cookies = os.path.join(runtime, cookies)
    if cookies and not os.path.exists(cookies):
        sys.exit(f"ERROR: cookies file not found: {cookies}")
    if cookies:
        try: os.chmod(cookies, 0o600)   # contains a live YouTube session — owner-only
        except OSError: pass            # best-effort hardening; never block startup
    if cookies:
        _ch = cookie_health(cookies)
        if _ch["stale"]:
            LOG.warning("yt-cookies.txt is %.0f h old — cookies rotate; "
                        "run 'racecast cookies firefox' before the event.", _ch['age_h'])

    # Locate the director panel (shipped in the package root, one level up from relay/)
    panel_path = None
    if not args.no_panel:
        for cand in (os.path.join(here, "director-panel.html"),
                     os.path.join(here, "..", "director-panel.html"),
                     os.path.join(here, "..", "director", "director-panel.html")):
            if os.path.exists(cand):
                panel_path = os.path.abspath(cand); break

    # HUD overlay: derived from sheet-id/tab (disabled with a custom CSV URL).
    hud_source = None
    hud_path = None
    preview_path = None
    graphics_dir = os.path.join(runtime, "graphics")   # Overlay.png frame for /hud/preview
    brands_dir = os.path.join(runtime, "brands")   # per-league brand-logo overrides
    assets_dir = os.path.abspath(os.path.join(here, "..", "assets"))
    splitscreen_path = None
    for cand in (os.path.join(here, "splitscreen.html"),
                 os.path.join(here, "..", "splitscreen.html"),
                 os.path.join(here, "..", "obs", "splitscreen.html")):
        if os.path.exists(cand):
            splitscreen_path = os.path.abspath(cand); break
    if not splitscreen_path:
        LOG.warning("splitscreen.html not found — /splitscreen will 404.")
    intermission_path = None
    for cand in (os.path.join(here, "intermission.html"),
                 os.path.join(here, "..", "intermission.html"),
                 os.path.join(here, "..", "obs", "intermission.html")):
        if os.path.exists(cand):
            intermission_path = os.path.abspath(cand); break
    if not intermission_path:
        LOG.warning("intermission.html not found — /intermission will 404.")
    cockpit_page_path = None
    for cand in (os.path.join(here, "cockpit.html"),
                 os.path.join(here, "..", "cockpit.html"),
                 os.path.join(here, "..", "cockpit", "cockpit.html")):
        if os.path.exists(cand):
            cockpit_page_path = os.path.abspath(cand); break
    console_page_path = None
    for cand in (os.path.join(here, "console.html"),
                 os.path.join(here, "..", "console.html"),
                 os.path.join(here, "..", "console", "console.html")):
        if os.path.exists(cand):
            console_page_path = os.path.abspath(cand); break
    race_control_page_path = None
    for cand in (os.path.join(here, "race-control.html"),
                 os.path.join(here, "..", "race-control.html"),
                 os.path.join(here, "..", "racecontrol", "race-control.html")):
        if os.path.exists(cand):
            race_control_page_path = os.path.abspath(cand); break
    buttons_page_path = None
    for cand in (os.path.join(here, "buttons.html"),
                 os.path.join(here, "..", "buttons.html"),
                 os.path.join(here, "..", "console", "buttons.html")):
        if os.path.exists(cand):
            buttons_page_path = os.path.abspath(cand); break
    health_monitor_page_path = None
    for cand in (os.path.join(here, "health-monitor.html"),
                 os.path.join(here, "..", "health-monitor.html"),
                 os.path.join(here, "..", "console", "health-monitor.html")):
        if os.path.exists(cand):
            health_monitor_page_path = os.path.abspath(cand); break
    uplot_dir = os.path.abspath(os.path.join(here, "..", "assets", "vendor", "uplot"))
    if not args.no_hud and not args.sheet_csv_url:
        base = f"https://docs.google.com/spreadsheets/d/{args.sheet_id}/gviz/tq?tqx=out:csv&sheet="
        overlay_url = base + quote(args.overlay_tab)
        config_url = base + quote(args.config_tab)
        quali_url = base + quote(args.quali_times_tab)
        hud_cache = os.path.join(runtime, "hud.cache.json")
        hud_source = HudSource(overlay_url, config_url, hud_cache,
                               quali_url=quali_url)
        # The optional Quali Times tab (issue #555) is read ONCE here and then only
        # by its own slow thread (quali_poller, below) -- never on the HUD refresh
        # path. Non-fatal, like the POV/crew boot reads: an absent or unreachable
        # tab just leaves the lap slots blank. It runs BEFORE the first refresh()
        # so the very FIRST built HUD frame already carries the laps -- refresh()
        # reads the committed map, and doing it in this order needs no second
        # lap-join site to achieve that.
        hud_source.refresh_quali()
        hud_source.refresh()   # non-fatal: keeps last-good / empty if unreachable
        for cand in (os.path.join(here, "hud.html"),
                     os.path.join(here, "..", "hud.html"),
                     os.path.join(here, "..", "obs", "hud.html")):
            if os.path.exists(cand):
                hud_path = os.path.abspath(cand); break
        if not hud_path:
            LOG.warning("hud.html not found — /hud will 404 (assets dir: %s).", assets_dir)
        for cand in (os.path.join(here, "hud-preview.html"),
                     os.path.join(here, "..", "hud-preview.html"),
                     os.path.join(here, "..", "obs", "hud-preview.html")):
            if os.path.exists(cand):
                preview_path = os.path.abspath(cand); break

    # One sheet-write webhook powers the race timer AND the panel's
    # Setup/Schedule/POV controls (wiki: Sheet-Webhook).
    push_url = os.environ.get("RACECAST_SHEET_PUSH_URL")

    # Race timer: local file always; sheet sync derived from sheet-id/tab
    # (custom --sheet-csv-url -> local-only); push via RACECAST_SHEET_PUSH_URL.
    timer_store = None
    if not args.no_timer:
        timer_csv = None
        if not args.sheet_csv_url:
            timer_csv = (f"https://docs.google.com/spreadsheets/d/{args.sheet_id}"
                         f"/gviz/tq?tqx=out:csv&sheet={quote(args.timer_tab)}")
        timer_store = TimerStore(timer_csv, push_url,
                                 os.path.join(runtime, "timer.json"))
        timer_store.refresh()   # non-fatal: adopt a newer sheet anchor on startup

    chat_store = ChatStore(os.path.join(runtime, "chat.json"))
    cue_store = CueStore(os.path.join(runtime, "cues.json"))
    def _flag_graphic_apply(scene, source, enabled):
        # Best-effort OBS apply; _obs_ws is None when the obs_ws import failed or
        # OBS is unreachable. Same contract as the POV/feed reflect calls.
        # #537: deliberately NOT routed through relay._obs — reassert() below calls
        # this synchronously (to re-push a persisted flag on restart) before `relay`
        # is constructed; closing over `relay` here would NameError on that path
        # whenever a flag was left active across a restart. Stays connect-per-call.
        if _obs_ws is None:
            return False, "obs unavailable"
        return _obs_ws.set_scene_item_enabled(scene, source, enabled)
    flag_graphic_store = flag_graphic.FlagGraphicStore(
        os.path.join(runtime, "flag-graphic.json"), apply_fn=_flag_graphic_apply)
    flag_graphic_store.reassert()   # re-push the saved flag to OBS (best-effort)
    _health_store_obj = HealthStore(
        os.path.join(runtime, "health-history.db"),
        retention_days=int(os.environ.get("RACECAST_HEALTH_RETENTION_DAYS",
                                          health_store.DEFAULT_RETENTION_DAYS)))
    try:
        _health_store_obj.prune()        # drop stale rows on start
    except Exception:                     # noqa: BLE001 — best-effort
        pass
    # Free-text event title (#207): persisted runtime state (event.json), seeded
    # from the EVENT_TITLE default (profile.env). An explicit --event-title wins and
    # is persisted, so it survives a restart; a takeover instead pulls A's title
    # into event.json before this runs.
    event_store = EventTitleStore(os.path.join(runtime, "event.json"),
                                  default=os.environ.get("RACECAST_EVENT_TITLE", ""))
    if args.event_title is not None:
        event_store.set(args.event_title)
    # Solo: no Schedule/Qualifying source and no A/B ping-pong feeds. Every other
    # sheet-driven source (HUD/Channel/Crew/Producer/Timer/EventNotes/POV) is built
    # above exactly as endurance.
    source = None
    if not args.solo:
        source = ScheduleSource(csv_url, cache, local)
        source.load_initial(SCHEDULE_TEMPLATE)
    # SetupControl already defaults schedule_source=None and only dereferences it on
    # the /schedule editor write path (unused in solo), so source=None is safe; the
    # Setup HUD-field writes solo uses do not touch it.
    setup_ctl = (SetupControl(push_url, hud_source, schedule_source=source,
                              qual_source=qual_source, pov_source=pov_source,
                              crew_source=crew_source)
                 if hud_source else None)
    if source is not None and len(source.get()) < 2:
        LOG.info("schedule has fewer than 2 stints — Feed B idles on the empty next "
                 "slot (black) until that stint's link is added; Feed A keeps serving stint 1.")

    relay = Relay(source, ports, logdir, cookies,
                  pov_source=pov_source, pov_port=args.pov_port,
                  start_stint=args.stint,
                  cookie_dir=(os.path.dirname(cookies) if cookies else runtime),
                  qual_source=qual_source,
                  mode=("qualifying" if args.qualifying else "race"),
                  discord_webhook_url=os.environ.get("RACECAST_DISCORD_WEBHOOK_URL"),
                  sheet_id=args.sheet_id,
                  event_title_store=event_store,
                  league_name=args.league_name,
                  producer_name=os.environ.get("RACECAST_PRODUCER_NAME", ""),
                  solo=args.solo)
    relay.health_store = _health_store_obj
    relay.timer_store = timer_store
    relay.start()
    if not args.solo:
        relay._reflect(relay.live_feed(), cut=False)     # pre-set Stint visibility/audio for the live feed
    # Launching straight into qualifying mode: seed the HUD Streamer/Stint from
    # the qualifying row now (the /mode switch does this live; the launch path
    # should match so the HUD isn't stale until the first action). Best-effort.
    if relay.mode == "qualifying" and setup_ctl:
        _push_live_schedule(relay, setup_ctl)

    stop_evt = threading.Event()
    if source is not None:
        threading.Thread(target=poller, args=(source, args.poll, stop_evt), daemon=True).start()
    if qual_source:
        threading.Thread(target=poller, args=(qual_source, args.poll, stop_evt),
                         daemon=True).start()
    if crew_source:
        threading.Thread(target=poller, args=(crew_source, args.poll, stop_evt),
                         daemon=True).start()
    if event_notes_source:
        threading.Thread(target=poller, args=(event_notes_source, args.poll, stop_evt),
                         daemon=True).start()
    if producer_source is not None:
        # Parts change rarely; the same cadence as the other sheet-tab readers is fine.
        threading.Thread(target=poller, args=(producer_source, args.poll, stop_evt),
                         daemon=True).start()
    if hud_source:
        threading.Thread(target=poller, args=(hud_source, args.hud_poll, stop_evt),
                         daemon=True).start()
    if hud_source and hud_source.quali_url:
        # Quali Times on its own slow thread (issue #555): entered once between
        # qualifying and the race, so QUALI_TIMES_POLL_S — never args.hud_poll, and
        # never inline in the HUD refresh, which must stay free of it.
        threading.Thread(target=quali_poller,
                         args=(hud_source, QUALI_TIMES_POLL_S, stop_evt),
                         daemon=True).start()
    if timer_store and timer_store.csv_url:
        threading.Thread(target=poller, args=(timer_store, args.hud_poll, stop_evt),
                         daemon=True).start()

    # GT7 UDP telemetry (solo POV only, #324): a best-effort listener thread that
    # idles harmlessly when no console answers. telemetry_store stays None outside
    # a solo POV broadcast (endurance, solo COMMENTARY, or explicitly disabled), so
    # make_handler's /telemetry/* (Task 8) 404s cleanly and the HUD block self-hides.
    telemetry_store = None
    if telemetry_active(args.solo, os.environ):
        _tunits = os.environ.get("RACECAST_TELEMETRY_UNITS", "metric")
        _tthr = (float(os.environ.get("RACECAST_TELEMETRY_TYRE_COLD", 70)),
                 float(os.environ.get("RACECAST_TELEMETRY_TYRE_OPTIMAL_HI", 85)),
                 float(os.environ.get("RACECAST_TELEMETRY_TYRE_HOT_HI", 95)))
        telemetry_store = gt7_telemetry.TelemetryStore(
            os.path.join(runtime, "telemetry.json"), units=_tunits, thresholds=_tthr,
            reset=True)          # fresh reference each relay start (spec §D) — no stale cross-track lap
        threading.Thread(target=_telemetry_loop,
                         args=(telemetry_store, args.gt7_ps_ip, stop_evt), daemon=True).start()
        LOG.info("GT7 telemetry listener started (bind 0.0.0.0:33740, ps_ip=%s)",
                args.gt7_ps_ip or "<discovery>")

    # Broadcast-chat reader (#294): resolve the channel's live videoId set and
    # poll each stream's chat. Its own ~30 s resolve cadence (not args.poll —
    # yt-dlp resolution is heavier than a CSV fetch). Best-effort daemon.
    _bc_supervisor = None
    if broadcast_chat_store is not None and channel_source is not None:
        _bc_supervisor = BroadcastChatSupervisor(
            broadcast_chat_store, channel_source, cookies, interval=30, logger=LOG)
        threading.Thread(target=_bc_supervisor.run, daemon=True).start()

    # Commentator cockpit (#191): per-league secret (injected from profile.env,
    # auto-provisioned by the CLI — zero-config). Present => /cockpit/* is served
    # (token-gated); absent => every /cockpit/* path 404s. PUBLIC exposure is the
    # separate Tailscale Funnel switch, never implied by the secret alone.
    console_secret = (os.environ.get("RACECAST_CONSOLE_SECRET") or "").strip() or None
    discord_client_id = (os.environ.get("RACECAST_DISCORD_CLIENT_ID") or "").strip()
    discord_client_secret = (os.environ.get("RACECAST_DISCORD_CLIENT_SECRET") or "").strip()
    console_versions_path = os.path.join(runtime, "console-versions.json")
    # Commentator stream-link submissions (#193): pending store + audit log,
    # profile-scoped like chat.json / console-versions.json. Always created so the
    # director's /submissions list works even before the cockpit is enabled.
    submission_store = SubmissionStore(
        os.path.join(runtime, "cockpit-pending.json"),
        os.path.join(runtime, "cockpit-submissions.log"))
    preview_manager = PreviewManager(relay, lambda: _obs_ws, LOG)
    threading.Thread(target=preview_manager.run, daemon=True).start()
    program_audio_service = (ProgramAudioService(relay, LOG)
                             if relay.program_audio else None)
    handler = make_handler(relay, panel_path, hud_source, hud_path, assets_dir,
                           timer_store, setup_ctl,
                           overlay_dir=args.overlay_dir, chat_store=chat_store,
                           cue_store=cue_store,
                           preview_path=preview_path, graphics_dir=graphics_dir,
                           splitscreen_path=splitscreen_path,
                           intermission_path=intermission_path,
                           cockpit_page_path=cockpit_page_path,
                           console_secret=console_secret,
                           discord_client_id=discord_client_id,
                           discord_client_secret=discord_client_secret,
                           console_versions_path=console_versions_path,
                           submission_store=submission_store,
                           event_store=event_store,
                           console_page_path=console_page_path,
                           race_control_page_path=race_control_page_path,
                           buttons_page_path=buttons_page_path,
                           health_store=relay.health_store,
                           health_monitor_page_path=health_monitor_page_path,
                           uplot_dir=uplot_dir,
                           brands_dir=brands_dir,
                           crew_source=crew_source,
                           event_notes_source=event_notes_source,
                           broadcast_chat_store=broadcast_chat_store,
                           broadcast_chat_supervisor=_bc_supervisor,
                           logo_path=args.logo,
                           preview_manager=preview_manager,
                           program_audio_service=program_audio_service,
                           app_version=VERSION_LABEL,
                           flag_graphic_store=flag_graphic_store,
                           channel_source=channel_source,
                           producer_source=producer_source,
                           part_store=part_store,
                           channel_csv_url=channel_csv_url,
                           push_url=push_url,
                           telemetry_store=telemetry_store)
    bind_addrs = resolve_bind_addresses(
        args.bind, detect_tailscale_ip() if args.bind == "auto" else None)
    servers, bound_addrs = [], []
    for addr in bind_addrs:
        try:
            servers.append(QuietThreadingHTTPServer((addr, args.http_port), handler))
            bound_addrs.append(addr)
        except OSError as e:
            LOG.warning("could not bind %s:%s — %s", addr, args.http_port, e)
    # The loopback bind is mandatory when requested: OBS always reaches the relay
    # on 127.0.0.1. If it failed but (e.g.) the Tailscale IP bound, running on
    # would be a silent split-brain — 127.0.0.1 stays served by the STALE relay
    # that holds the port (issue #84). Abort loudly instead of half-starting.
    if not servers or loopback_bind_failed(bind_addrs, bound_addrs):
        for httpd in servers:
            httpd.server_close()
        sys.exit(f"Could not bind the control server on 127.0.0.1 port {args.http_port} "
                 f"— another relay is probably already running. Stop it first "
                 f"('racecast relay stop'), then check 'racecast status' / 'racecast preflight' "
                 f"to see what holds the port.")

    def shutdown(*_):
        # IMPORTANT: do NOT call shutdown() from the thread running serve_forever()
        # (deadlock). Stop the feeds and exit hard — the OS frees the sockets; the
        # streamlink subprocesses are cleanly terminated.
        LOG.info("Stopping feeds…")
        stop_evt.set(); relay.shutdown(); os._exit(0)
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # The non-loopback bind (if any) is the address remote directors/tablets use.
    remote_host = next((a for a in bind_addrs if a not in ("127.0.0.1", "localhost")), None)

    LOG.info("racecast relay running.  Schedule source: %s",
             'CSV URL' if args.sheet_csv_url else f'sheet tab “{args.sheet_tab}”')
    if relay.qual_source:
        _qmode = "QUALIFYING (live)" if relay.mode == "qualifying" else "race"
        LOG.info("  Qualifying tab '%s' available — mode: %s  "
                 "(switch: /mode/qualifying | /mode/race)", args.qualifying_tab, _qmode)
    else:
        _dg = qualifying_downgrade_note(args.qualifying, bool(relay.qual_source),
                                        args.qualifying_tab)
        if _dg:
            LOG.error("  %s", _dg)
    LOG.info("  Feed A -> http://127.0.0.1:%s   Feed B -> http://127.0.0.1:%s", ports[0], ports[1])
    if args.stint != 1 and not args.solo:
        if relay.A.idx != args.stint - 1:
            LOG.warning("  --stint %s clamped to stint %s (schedule has %s stints).",
                        args.stint, relay.A.idx + 1, len(source.get()))
        LOG.info("  Takeover start: stint %s on Feed A; Feed B preloads stint %s.",
                 relay.A.idx + 1, relay.B.idx + 1)
    LOG.info("  Controls: http://127.0.0.1:%s/status | /next | /reload", args.http_port)
    if relay.pov:
        LOG.info("  Driver-POV PiP -> http://127.0.0.1:%s  "
                 "(sheet tab '%s' url+name; control /pov/reload | /pov/stop | /pov/toggle)",
                 args.pov_port, args.pov_tab)
    if panel_path:
        LOG.info("  Director panel (local): http://127.0.0.1:%s/panel", args.http_port)
        if remote_host:
            LOG.info("    remote (tailnet):      http://%s:%s/panel", remote_host, args.http_port)
        elif args.bind == "auto":
            LOG.info("    (no Tailscale IP found — local only; start Tailscale for remote access)")
    if event_store.get():
        LOG.info("  Event title: “%s”  (panel/cockpit/Discord; edit live in the Director Panel)",
                 event_store.get())
    if console_secret:
        if cockpit_page_path:
            LOG.info("  Commentator cockpit: /cockpit (auth) — links via 'racecast links'")
        else:
            LOG.warning("  cockpit secret set but cockpit.html not found — /cockpit will 404.")
        if race_control_page_path:
            LOG.info("  Race Control desk: /console/race-control (auth, role-gated)")
        else:
            LOG.warning("  console secret set but race-control.html not found — "
                        "/console/race-control will 404.")
    if hud_source and hud_path:
        LOG.info("  HUD overlay (OBS source): http://127.0.0.1:%s/hud  "
                 "(tabs '%s'/'%s', refresh %ss)",
                 args.http_port, args.overlay_tab, args.config_tab, args.hud_poll)
    if setup_ctl:
        mode = "writes ON" if push_url else "read-only (set RACECAST_SHEET_PUSH_URL)"
        LOG.info("  Panel sheet controls (/setup /schedule /pov/set): %s", mode)
    if timer_store:
        push = "sheet+push" if timer_store.push_url else (
            "sheet read-only (set RACECAST_SHEET_PUSH_URL for handover sync)"
            if timer_store.csv_url else "local only")
        LOG.info("  HUD overlay incl. race timer (OBS source): http://127.0.0.1:%s/hud",
                 args.http_port)
        LOG.info("  Timer controls: /timer/start | /timer/stop | /timer/reset (tab '%s', %s)",
                 args.timer_tab, push)
    LOG.info("  Cookies (bot-check protection): %s",
             'ON — ' + cookies if cookies else 'off (no yt-cookies.txt)')
    login_warning = cookie_login_warning(cookies)
    if login_warning:
        LOG.warning("  %s", login_warning)
    LOG.info("  Sheet poll every %ss.  Ctrl+C to stop.", args.poll)
    # Serve every bound address; keep the last on the main thread for signals.
    for httpd in servers[:-1]:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
    servers[-1].serve_forever()

if __name__ == "__main__":
    main()

"""Pure logic for the post-update smoke test (`racecast smoketest`).

No network, no argv parsing, no subprocess: discovery URL/payload builders,
candidate acceptance, the director rundown table with its expected OBS state,
verdict aggregation and the history-line shape. The network calls and the
orchestration live in racecast.py; this module is what the unit tests exercise.
Mirrors the pure-store pattern of cue_admin.py / cockpit_submissions.py.

Why the smoke test drives a REAL event rather than invoking the tools itself:
tools/e2e.py already proves the relay's HTTP surface with stubbed tools, and a
re-implementation of the relay's yt-dlp/streamlink command forms would only ever
test the re-implementation. Design:
docs/superpowers/specs/2026-08-27-post-update-smoketest-design.md
"""
import json
import re
from urllib.parse import urlparse
import urllib.parse

# Game titles, not English words: a sim-racing stream is titled in the
# streamer's own language but names the game the same way everywhere. Matched on
# word boundaries so "lmu" never fires inside "alumni".
SIM_RACING_KEYWORDS = (
    "gt7", "gran turismo", "iracing", "acc", "assetto corsa", "lmu",
    "le mans ultimate", "rfactor", "automobilista", "ams2", "raceroom",
    "f1 23", "f1 24", "f1 25", "sim racing", "simracing", "endurance",
)

# Twitch category names are exact; the category IS the topical filter there.
TWITCH_CATEGORIES = (
    "Gran Turismo 7", "iRacing", "Assetto Corsa Competizione",
    "Le Mans Ultimate", "Assetto Corsa", "Automobilista 2",
)

YOUTUBE_QUERIES = ("sim racing live", "iracing", "gt7 daily race",
                   "le mans ultimate", "endurance sim racing")

MIN_HEIGHT = 720                 # below this a feed is not a broadcast source
MAX_ATTEMPTS_PER_PLATFORM = 3    # an uncapped walk is how an IP gets throttled
DEFAULT_MINUTES = 5              # observation window
SHEET_ROWS_TO_CLEAR = 4          # the sheet has 4 stint rows; we write 3

# YouTube's own "Live" search filter (sp=EgJAAQ%3D%3D). Without it the result
# set is dominated by VODs, and a VOD cannot prove the live path at all: its
# best MUXED format caps at 360p, so the relay's `rcq` readout is meaningless.
YOUTUBE_LIVE_FILTER = "sp=EgJAAQ%3D%3D"

# The public web-player Client-ID, the same one streamlink embeds. Using it
# keeps Twitch discovery dependency-free: no Helix app, no OAuth, stdlib HTTP.
TWITCH_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
TWITCH_GQL_URL = "https://gql.twitch.tv/gql"


def youtube_live_search_url(query):
    """The live-filtered YouTube search URL yt-dlp reads as a flat playlist."""
    return ("https://www.youtube.com/results?search_query="
            + urllib.parse.quote_plus(query) + "&" + YOUTUBE_LIVE_FILTER)


def twitch_category_query(category, first=6):
    """GraphQL body listing a category's live streams. The category name is
    JSON-escaped into the query string so a quote in it can never close the
    GraphQL string literal and inject a second field."""
    name = json.dumps(str(category))          # includes the surrounding quotes
    return {"query": "{game(name:%s){streams(first:%d){edges{node{id title "
                     "viewersCount broadcaster{login}}}}}}" % (name, int(first))}


def rank_by_viewers(candidates):
    """Order attempts by audience, never exclude by it: a 3-viewer channel
    serves the same 1080p60 transport stream as a 500-viewer one. Popularity is
    a stability hint, not a technical criterion."""
    return sorted(candidates, key=lambda c: -int(c.get("viewers") or 0))


# Mirrors the relay's _YTDLP_QUALITY_RE. Real output is `rcq 1080 60.0`: fps is a
# FLOAT, is "NA" when unavailable, and can be absent entirely. Requiring an integer
# fps here would make every YouTube candidate look "not live".
_RCQ_RE = re.compile(r"^rcq\s+(\d+)(?:\s+(\S+))?", re.MULTILINE)
_JS_RUNTIME_RE = re.compile(r"JS runtimes:\s*(\S+)")
_LADDER_RE = re.compile(r"^(\d+)p\d*$")


def parse_rcq(text):
    """(height, fps) from the relay's extra `--print "rcq %(height)s %(fps)s"`
    line, or None when yt-dlp printed no height for this format.

    fps comes back as a float when yt-dlp gives one and None otherwise ("NA", or
    the field missing). Only the height decides acceptance; fps is recorded.
    """
    m = _RCQ_RE.search(text or "")
    if not m:
        return None
    try:
        fps = float(m.group(2))
    except (TypeError, ValueError):
        fps = None
    return int(m.group(1)), fps


def parse_js_runtimes(text):
    """yt-dlp's `[debug] JS runtimes: deno-x.y.z`. On the broadcast box deno is
    the ONLY available JS challenge provider, so this line is worth recording:
    if it changes or disappears, the YouTube bot-check has no fallback."""
    m = _JS_RUNTIME_RE.search(text or "")
    return m.group(1) if m else None


def ladder_max_height(qualities):
    """Highest numeric height in a streamlink quality ladder; 0 when it holds
    only aliases (audio_only/worst/best)."""
    best = 0
    for q in qualities or ():
        m = _LADDER_RE.match(str(q).strip())
        if m:
            best = max(best, int(m.group(1)))
    return best


def topical_match(text, keywords=SIM_RACING_KEYWORDS):
    """True when *text* names a sim-racing title. Word-boundary matching keeps
    short keys ("acc", "lmu") from firing inside unrelated words."""
    low = (text or "").lower()
    return any(re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", low)
               for k in keywords)


def accept_youtube(height, title, channel, keywords=SIM_RACING_KEYWORDS):
    """(ok, reason) for a YouTube candidate already resolved in the relay's own
    command form. Passing this IS the proof that the live path works, because it
    is the exact operation the relay performs on air."""
    if not height:
        return False, "no resolution reported (resolve failed or not live)"
    if height < MIN_HEIGHT:
        return False, f"{height}p is below the {MIN_HEIGHT}p minimum"
    if not topical_match(f"{title} {channel}", keywords):
        return False, "off topic (no sim-racing title in name or channel)"
    return True, ""


def accept_twitch(plugin, qualities, category, categories=TWITCH_CATEGORIES):
    """(ok, reason) from a `streamlink --json` probe, which pulls no bytes."""
    if plugin != "twitch":
        return False, f"unexpected streamlink plugin: {plugin!r}"
    h = ladder_max_height(qualities)
    if h < MIN_HEIGHT:
        return False, f"{h}p ladder is below the {MIN_HEIGHT}p minimum"
    if (category or "") not in categories:
        return False, f"category {category!r} is not a sim-racing category"
    return True, ""


# YouTube, Twitch, YouTube, NOT YouTube, YouTube, Twitch. Both orders exercise
# both transports in both roles; they differ only during SPLIT, where two feeds
# pull at once. Two concurrent googlevideo connections are the known throttle
# state (#505) and the relay's feed classifier cannot tell a 429 from a generic
# failure, so that order would redden the run for a reason that is not a
# regression. With this order SPLIT is always one YouTube next to one Twitch.
SOURCE_PLAN = ("youtube", "twitch", "youtube")


# Mirrors the relay's SCHEDULE_URL_HEADERS / _parse_rows layout detection. Row
# numbers are PHYSICAL and 1-based, header included, the same key the relay uses for
# /schedule/data ("keyed by physical sheet row"). Writing to a hardcoded row 1 would
# overwrite a real sheet's `URL` header, knocking the tab out of header mode so the
# read-back never sees the run's own writes.
SCHEDULE_URL_HEADERS = ("url",)


SCHEDULE_STREAMER_HEADERS = ("streamer", "name")
SCHEDULE_STINT_HEADERS = ("stint",)


def _header_index(header, names):
    return next((header.index(h) for h in names if h in header), None)


def schedule_layout(rows):
    """(url_col, first_data_row, header_mode) for a Schedule tab.

    Mirrors the relay's `_parse_rows`: header mode when row 1 carries a
    recognized `URL` header (data from physical row 2), otherwise the URL column
    is the one holding the most channel values and the data starts at row 1.
    """
    if not rows:
        return None, 0, False
    header = [(c or "").strip().lower() for c in rows[0]]
    url_i = _header_index(header, SCHEDULE_URL_HEADERS)
    if url_i is not None:
        return url_i, 2, True
    best_col, best_cnt = None, 0
    for col in range(max((len(r) for r in rows), default=0)):
        cnt = sum(1 for r in rows if len(r) > col and is_channel_value(r[col]))
        if cnt > best_cnt:
            best_col, best_cnt = col, cnt
    return (best_col, 1, False) if best_col is not None else (None, 0, False)


def writable_layout_note(rows):
    """"" when this tab can be written safely, else the reason it cannot.

    The Apps Script writes `colOf('url') || 1`: with a `URL` header it writes
    that header's column, WITHOUT one it always writes column A. Our positional
    detection can land on a different column, and reading one column while
    writing another would blank real data in A. Refuse before anything is
    written rather than discover it from a read-back that never matches.
    """
    col, _first, header = schedule_layout(rows)
    if col is None:
        return "no URL column found in the Schedule tab"
    if not header and col != 0:
        return ("the Schedule tab has no `URL` header and its stream column is "
                f"{chr(ord('A') + col)}, but the webhook can only write column A. "
                "Add a `URL` header row (wiki: Sheet-Template)")
    return ""


def schedule_data_rows(rows):
    """Physical rows the RELAY counts as stints, the only ones safe to write.

    Mirrors `_parse_rows`. In header mode a row counts when it has a channel URL
    OR a Streamer OR a Stint label (a planned stint whose URL is not filled in
    yet); a fully blank spacer does not, since writing into one would invent a stint
    the league never planned. In positional mode only URL-bearing rows count, so
    a differently-named header row can never be targeted and overwritten.
    """
    col, first, header = schedule_layout(rows)
    if col is None:
        return []
    if not header:
        return [i for i in range(first, len(rows) + 1)
                if len(rows[i - 1]) > col and is_channel_value(rows[i - 1][col])]
    head = [(c or "").strip().lower() for c in rows[0]]
    name_i = _header_index(head, SCHEDULE_STREAMER_HEADERS)
    stint_i = _header_index(head, SCHEDULE_STINT_HEADERS)

    def _cell(r, i):
        return r[i].strip() if i is not None and len(r) > i else ""

    out = []
    for line in range(first, len(rows) + 1):
        r = rows[line - 1]
        if is_channel_value(_cell(r, col)):
            out.append(line)
        elif _cell(r, name_i) or _cell(r, stint_i):
            out.append(line)          # planned stint, URL not yet provided
    return out


def schedule_urls(rows):
    """{physical_row: url} for the stint rows, in the located URL column."""
    col, _first, _header = schedule_layout(rows)
    if col is None:
        return {}
    out = {}
    for line in schedule_data_rows(rows):
        r = rows[line - 1]
        out[line] = r[col].strip() if len(r) > col else ""
    return out


def rows_match(served, expected):
    """True when the sheet serves EXACTLY `expected` ({physical_row: url}) in the
    rows it names. `served` is `schedule_urls()` output.

    Per row, not set inclusion: `want <= got` over all rows answered "are these
    URLs somewhere in the tab", which a repeat run against a DEAD webhook passes
    without anything having been written, because the previous run's identical URLs are
    still sitting there. Checking the row a value was written to, and checking
    the cleared rows are really empty, is what makes the transition observable.
    """
    return all((served.get(row) or "") == (url or "") for row, url in expected.items())


def plan_rows(youtube_urls, twitch_urls, data_rows):
    """[(physical_row, url), …] for SOURCE_PLAN, or None when a slot or a sheet
    row is missing: a short sheet must abort, never silently write fewer."""
    pools = {"youtube": list(youtube_urls or ()), "twitch": list(twitch_urls or ())}
    targets = list(data_rows or ())
    if len(targets) < len(SOURCE_PLAN):
        return None
    rows = []
    for i, platform in enumerate(SOURCE_PLAN):
        if not pools[platform]:
            return None
        rows.append((targets[i], pools[platform].pop(0)))
    return rows


def clear_rows(data_rows, total=SHEET_ROWS_TO_CLEAR):
    """Rows whose URL cell is emptied before the write. Wider than what gets
    written so a stale URL cannot linger below the run's own rows, but never
    beyond the rows the sheet actually has."""
    return list(data_rows or ())[:total]


def confirm_phrase(profile):
    """The phrase names the profile on purpose. The accident this guards against
    is the right command in the wrong league, and a bare YES would not catch it."""
    return f"CLEAR SCHEDULE {profile}"


def phrase_ok(profile, typed):
    """Exact match after trimming, case-sensitive so it cannot be muscle memory."""
    return (typed or "").strip() == confirm_phrase(profile)


class Step:
    """One director action plus the OBS state it must produce.

    Deliberately NOT a copy of the panel's CONFIG: this is a broadcast rundown
    (a story with an order), not the button matrix. tests/test_smoketest.py
    asserts every scene/source named here exists in the shipped collection, so a
    rename fails in CI instead of on air.
    """
    __slots__ = ("label", "kind", "scene", "show", "hide", "unmute", "mute",
                 "relay_split", "relay_stint", "relay", "wait_for_bytes", "check")

    def __init__(self, label, kind, scene=None, show=(), hide=(), unmute=(),
                 mute=(), relay_split=False, relay_stint=None, relay=None,
                 wait_for_bytes=False, check=None):
        self.label = label; self.kind = kind; self.scene = scene
        self.show = tuple(show); self.hide = tuple(hide)
        self.unmute = tuple(unmute); self.mute = tuple(mute)
        self.relay_split = relay_split; self.relay = relay
        # A relay-resolved STINT (/obs/stint/<X>): the relay applies what show/hide/
        # unmute/mute describe, so those stay as the expected read-back only.
        self.relay_stint = relay_stint
        self.wait_for_bytes = wait_for_bytes; self.check = check

    def __repr__(self):                                   # pragma: no cover
        return f"<Step {self.label} {self.kind}>"


_FEEDS = ("Feed A", "Feed B")
DISCORD_AUDIO = "Discord Audio Capture"

RUNDOWN = (
    Step("STANDBY", "macro", scene="Standby", mute=(*_FEEDS, DISCORD_AUDIO)),
    Step("INTRO", "macro", scene="Intro", mute=(*_FEEDS, DISCORD_AUDIO)),
    # Manual feed arm is default-on (#489/#505), so BOTH feeds start paused and
    # `event start` arms neither; the director arms the one they are about to cut to.
    # Without this the whole first stint pulls nothing and STINT A shows black.
    Step("ARM A", "arm", relay="feed/A/activate", wait_for_bytes=True),
    Step("STINT A", "macro", scene="Stint", relay_stint="A",
         show=(("Stint", "Feed A"),), hide=(("Stint", "Feed B"),),
         unmute=("Feed A",), mute=("Feed B", DISCORD_AUDIO)),
    Step("HUD ON", "graphic", scene="Stint", show=(("Stint", "Stint HUD"),)),
    Step("STANDINGS ON", "graphic", scene="Stint", show=(("Stint", "Standings"),)),
    Step("STANDINGS OFF", "graphic", scene="Stint", hide=(("Stint", "Standings"),)),
    Step("FLAG YELLOW", "graphic", scene="Stint", show=(("Stint", "Flag Yellow"),)),
    Step("CLEAR FLAG", "graphic", scene="Stint", hide=(("Stint", "Flag Yellow"),)),
    Step("TIMER START", "relay", relay="timer/start"),
    Step("TIMER PAUSE", "relay", relay="timer/stop"),   # panel: PAUSE -> timer/stop
    Step("TIMER RESET", "relay", relay="timer/reset"),
    # The off-air feed is still paused: SPLIT against an unarmed feed would inspect
    # a black half, hence the arm plus the byte wait.
    Step("ARM B", "arm", relay="feed/B/activate", wait_for_bytes=True),
    Step("SPLIT", "macro", scene="Splitscreen", relay_split=True),
    Step("NEXT", "relay", relay="next"),
    Step("STINT B", "macro", scene="Stint", relay_stint="B",
         show=(("Stint", "Feed B"),), hide=(("Stint", "Feed A"),),
         unmute=("Feed B",), mute=("Feed A", DISCORD_AUDIO)),
    Step("ARM A", "arm", relay="feed/A/activate", wait_for_bytes=True),
    Step("SPLIT", "macro", scene="Splitscreen", relay_split=True),
    Step("INTERVIEW", "macro", scene="Interview",
         unmute=(DISCORD_AUDIO,), mute=_FEEDS),
    Step("OUTRO", "macro", scene="Outro", mute=(*_FEEDS, DISCORD_AUDIO)),
    Step("STANDBY", "macro", scene="Standby", mute=(*_FEEDS, DISCORD_AUDIO)),
)


def arm_violations(rundown=RUNDOWN):
    """Labels of steps that put an UNARMED feed on air. Empty == the rundown is sound.

    Models the relay's manual-arm state machine (`RACECAST_MANUAL_FEED_ARM`, #492,
    default-on): both feeds start paused, `feed/X/activate` arms one,
    `feed/X/deactivate` disarms it, and a `next` handover disarms the OUTGOING feed
    (`stop_freed = cut and self.manual_feed_arm` in the relay, the #489/#505
    single-puller rule). A feed is "on air" for a step when the step unmutes it or
    makes its source visible; showing a paused feed is a black frame, not a failure
    the smoke test could attribute to the toolchain.
    """
    armed, on_air, bad = set(), None, []
    for step in rundown:
        if step.relay == "next":
            if on_air:                       # the relay stops the feed we cut away from
                armed.discard(on_air)
            on_air = "Feed B" if on_air == "Feed A" else "Feed A"
            continue
        if step.relay and step.relay.startswith("feed/"):
            _, which, action = step.relay.split("/")
            feed = f"Feed {which.upper()}"
            if action == "activate":
                armed.add(feed)
            else:
                armed.discard(feed)
            continue
        needed = {f for f in _FEEDS
                  if f in step.unmute or any(src == f for _, src in step.show)}
        if needed - armed:
            bad.append(step.label)
        if len(needed) == 1:                 # a split airs both; it does not re-cue
            on_air = next(iter(needed))
    return bad


def expected_after(step, on_air="Feed A"):
    """The OBS state a step must have produced, for the `POST /obs/state`
    read-back. `relay_split` resolves like the relay does (#534, #591): both
    feeds visible, the ON-AIR feed live, the off-air feed and Discord muted.
    Reading this back after SPLIT on BOTH sides of the handover is the regression
    test for SPLIT muting the on-air commentator on an even->odd handover."""
    visible = {t: True for t in step.show}
    visible.update({t: False for t in step.hide})
    muted = {n: False for n in step.unmute}
    muted.update({n: True for n in step.mute})
    if step.relay_split:
        visible.update({(step.scene, f): True for f in _FEEDS})
        off = [f for f in _FEEDS if f != on_air]
        muted[on_air] = False
        for f in off:
            muted[f] = True
        muted[DISCORD_AUDIO] = True
    return {"scene": step.scene, "visible": visible, "muted": muted}


def rundown_scenes():
    return {s.scene for s in RUNDOWN if s.scene}


def rundown_obs_targets():
    out = set()
    for s in RUNDOWN:
        out.update(s.show); out.update(s.hide)
        if s.relay_split:
            out.update((s.scene, f) for f in _FEEDS)
    return out


def rundown_audio_inputs():
    out = set()
    for s in RUNDOWN:
        out.update(s.unmute); out.update(s.mute)
        if s.relay_split:
            out.update(_FEEDS); out.add(DISCORD_AUDIO)
    return out


def state_mismatches(expected, observed):
    """Differences between `expected_after()` and a `POST /obs/state` payload,
    as human-readable strings (empty = the step landed).

    This is what turns a red run into an actionable one: "step 13 SPLIT:
    Feed B expected live, measured muted" names the defect, where a single check
    at the end would only say that something went wrong somewhere.

    A None reading means OBS could not answer for that item; it is reported
    rather than silently treated as a match.
    """
    out = []
    scene = (observed or {}).get("scene")
    if expected.get("scene") and scene != expected["scene"]:
        out.append(f"scene expected {expected['scene']!r}, measured {scene!r}")
    seen_vis = {(s.get("scene"), s.get("source")): s.get("enabled")
                for s in (observed or {}).get("sources") or ()}
    for (sc, src), want in (expected.get("visible") or {}).items():
        got = seen_vis.get((sc, src), "missing")
        if got != want:
            out.append(f"{sc}/{src} expected {'visible' if want else 'hidden'}, "
                       f"measured {got!r}")
    seen_aud = {(a.get("input") or a.get("name")): a.get("muted")
                for a in (observed or {}).get("audio") or ()}
    for name, want_muted in (expected.get("muted") or {}).items():
        got = seen_aud.get(name, "missing")
        if got != want_muted:
            out.append(f"{name} expected {'muted' if want_muted else 'live'}, "
                       f"measured {got!r}")
    return out


def state_probe(step, on_air="Feed A"):
    """The `POST /obs/state` request body needed to verify *step*."""
    exp = expected_after(step, on_air)
    return {"sources": [{"scene": sc, "source": src}
                        for (sc, src) in (exp.get("visible") or {})],
            "inputs": sorted(exp.get("muted") or {})}


# The relay's /status shape (Relay.status): feeds is a DICT keyed "A"/"B"/"POV"
# with state/armed/down per feed, and live={"feed": "A"|"B", "stint": N}. Reading
# it through these helpers keeps the shape assumption in one tested place.

def feed_serving(status, which):
    """True when feed A/B is actually delivering. With the fan-out, health means
    "bytes are flowing", not "the process is alive"; `state == "serving"` is the
    relay's own word for that, and `down` overrides it."""
    feed = ((status or {}).get("feeds") or {}).get(str(which).upper()) or {}
    return feed.get("state") == "serving" and not feed.get("down")


def on_air_feed(status):
    """"Feed A"/"Feed B" from live.feed, or None when the relay did not say."""
    live = ((status or {}).get("live") or {}).get("feed")
    return f"Feed {live}" if live in ("A", "B") else None


def health_level(status):
    return str((((status or {}).get("health") or {}).get("level")) or "").lower()


# The relay's own vocabulary (_HEALTH_LABEL): green=OK, yellow=DEGRADED,
# red=CRITICAL. Pinned by a test against that dict, so a value the relay never
# emits cannot read as coverage.
HEALTH_RED = "red"


def is_drop_sample(status):
    """A CRITICAL health sample. A feed that just dropped is a SILENT blip until
    it stays down past the settle window, so a single reconnect that self-heals
    must not count; only the level the relay itself has settled on counts. DEGRADED
    (yellow) is deliberately not a drop: it is the warning before the loss."""
    return health_level(status) == HEALTH_RED


PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"

# Hard failure is reserved for what is attributable to the TOOLCHAIN, the one
# question this command exists to answer. Environment conditions degrade to WARN
# because they can stem from a headless session and have nothing to do with
# ffmpeg or yt-dlp, and a test that reddens for environmental reasons is a test
# the operator learns to ignore.
SOFT_CHECKS = frozenset({
    "obs_standby", "obs_collection", "companion", "tailscale", "discord",
    "media", "overlay_fonts",
    # A missing YouTube cookie jar is a setup fact, not a toolchain regression:
    # Twitch still resolves, and discovery reports the shortfall on its own.
    "cookies",
})


class Result:
    __slots__ = ("name", "severity", "note")

    def __init__(self, name, severity, note=""):
        self.name = name; self.severity = severity; self.note = note

    def __repr__(self):                                   # pragma: no cover
        return f"<Result {self.name} {self.severity}>"


_TWITCH_LOGIN_RE = re.compile(r"^[a-z0-9_]{1,25}$")
# Mirrors the relay's CHANNEL_RE.
_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_\-]{20,}$")


def stream_host(url):
    """The supported streaming host of `url`, or "": the SSRF gate for discovery.

    The allow-list is deliberately duplicated from the relay's `_is_stream_url`
    rather than imported: this module is pure stdlib and is loaded standalone (by
    the CLI and by the tests), while the relay is a dash-named script that cannot
    be imported at all.

    Mirrors that allow-list EXACTLY, including that `youtu.be` is matched only
    as a whole host (no subdomains) while youtube.com and twitch.tv also match
    their subdomains. tests/test_smoketest.py pins the
    two copies against the relay's own source.

    Discovery hands its results to a local yt-dlp WITH the cookie jar attached
    and writes them into the league sheet through the webhook, which bypasses
    `schedule_set`'s own `is_channel` check, so the allow-list has to be applied
    here too. Parsing the hostname (rather than testing a substring) is what
    makes `https://evil.example/twitch.tv` fail.
    """
    try:
        p = urlparse(url or "")
    except ValueError:
        return ""
    if p.scheme not in ("http", "https"):
        return ""
    host = (p.hostname or "").lower()
    if host == "youtu.be":
        return "youtu.be"
    for known in ("youtube.com", "twitch.tv"):
        if host == known or host.endswith("." + known):
            return known
    return ""


def is_channel_value(value):
    """What the relay accepts in a Schedule URL cell: a stream URL or a bare
    `UC…` id. Mirrors its `is_channel`; using only `stream_host` here would make a
    documented UC-id layout look like an empty schedule."""
    v = (value or "").strip()
    return bool(_CHANNEL_ID_RE.match(v)) or bool(stream_host(v))


def platform_of(url):
    """"twitch" | "youtube" | "", by parsed hostname, never by substring."""
    host = stream_host(url)
    if host == "twitch.tv":
        return "twitch"
    return "youtube" if host else ""


def twitch_login_ok(login):
    """Twitch's own `[a-z0-9_]{1,25}` charset, mirroring `broadcast_chat.twitch_login`.

    The login arrives verbatim from the public GQL reply and is interpolated into
    a URL that goes to streamlink and into the league sheet, so it is validated
    before either.
    """
    return bool(_TWITCH_LOGIN_RE.match((login or "").strip().lower()))


OBS_UNREACHABLE_MARKERS = ("obs unavailable", "obs unreachable", "obs is not")


def step_error_verdict(error):
    """(status, note) when a rundown step's own call reports an error.

    An unreachable OBS is an environment fact, not a toolchain regression, and
    it already only WARNs on every read-back, so failing hard when the SPLIT
    audio call hits the same dead OBS would be the classification contradicting
    itself. Anything else is a real failure.
    """
    text = str(error)
    low = text.lower()
    if any(m in low for m in OBS_UNREACHABLE_MARKERS):
        return WARN, text[:120]
    return FAIL, text[:120]


def arm_verdict(manual_arm, bytes_ok):
    """(status, note) for an ARM step. Bytes are the signal, not the arm call.

    With `RACECAST_MANUAL_FEED_ARM=0` the feeds pre-warm themselves and the relay
    refuses `feed/X/activate` outright ("manual feed arm disabled"), so issuing it
    would redden the run over a documented machine setting. The caller skips the
    call in that mode; the byte wait still decides, because a feed that delivers
    nothing is a toolchain failure either way.
    """
    if bytes_ok:
        return PASS, "" if manual_arm else "feed self-armed (manual arm off)"
    return FAIL, "no bytes after ARM"


PROGRAM_AUDIO_MIN_BYTES = 1024


def program_audio_verdict(nbytes, note=""):
    """(status, note) for the program-audio tap. A 404 is NOT a toolchain defect.

    `/preview/program-audio` only exists when the feed fan-out is on and
    `RACECAST_PROGRAM_AUDIO` is not 0, both documented machine settings. Failing
    the run there would blame ffmpeg for a config choice, so an absent endpoint
    skips. Anything else (a short read, a connection error) does mean the encoder
    did not produce frames, which is exactly what an ffmpeg major bump threatens.
    """
    if nbytes > PROGRAM_AUDIO_MIN_BYTES:
        return PASS, ""
    if note.startswith("HTTP 404"):
        return SKIP, "endpoint absent: feed fan-out or program audio is off"
    return FAIL, note or f"{nbytes} bytes"


def severity_for(name, ok):
    if ok:
        return PASS
    return WARN if name in SOFT_CHECKS else FAIL


_ORDER = (PASS, SKIP, WARN, FAIL)      # increasing severity for the roll-up


def summarize(results):
    """Worst severity wins. An empty run is SKIP, not PASS: nothing was proven."""
    counts = {PASS: 0, WARN: 0, FAIL: 0, SKIP: 0}
    for r in results:
        counts[r.severity] = counts.get(r.severity, 0) + 1
    verdict = SKIP
    for r in results:
        if _ORDER.index(r.severity) > _ORDER.index(verdict):
            verdict = r.severity
    if results and verdict == SKIP and counts[SKIP] == 0:
        verdict = PASS
    return {"verdict": verdict, "counts": counts}


def exit_code(verdict):
    """Only FAIL is a non-zero exit. "Nobody is streaming at 04:00" is a SKIP:
    reddening for that would train the operator to click red away."""
    return 1 if verdict == FAIL else 0


def history_entry(ts, verdict, tools, sources, minutes, results, cleared=None):
    """One JSONL line per run, mirroring runtime/speedtest-history.jsonl, so
    comparing a red run against the last green one is a `tail -2`.

    `cleared` carries the URL cells as they were BEFORE the run emptied them.
    The run does not restore them (the rows are meant to stay), so this file is
    the only record of what was there, including the row that gets cleared but
    never rewritten.
    """
    return {
        "ts": ts,
        "verdict": verdict,
        "minutes": minutes,
        "tools": dict(tools or {}),
        "sources": [dict(s) for s in (sources or ())],
        "cleared": {str(k): v for k, v in (cleared or {}).items()},
        "checks": [{"name": r.name, "severity": r.severity, "note": r.note}
                   for r in (results or ())],
    }

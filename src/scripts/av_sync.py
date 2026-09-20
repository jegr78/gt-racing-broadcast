"""A/V sync disturbance detector — pure parsing, classification and aggregation.

OBS is the only component in the chain that knows when a source's audio timing broke,
and it writes that to its own log file, which no part of racecast reads. This module
turns those lines into something the relay can publish.

What OBS is telling us (read from libobs/obs-audio.c, not assumed):

    /* if a source has gone backward in time and we can no
     * longer buffer, drop some or all of its audio */
    if (audio_buffering_maxed(audio) && source->audio_ts != 0 && source->audio_ts < ts.start)
            bool rerender = ignore_audio(source, channels, sample_rate, ts.start);

`ignore_audio()` discards audio samples until the source is back on the mix clock and,
if that is not enough, logs the line this module parses and resets the source's audio
timing. Three consequences shape everything here:

1. The AUDIO is pulled forward; the video is never touched. An intuition that "the
   picture has to catch up" is inverted.
2. By the time the line exists, OBS has already applied its remedy. So this is a
   detector, not a trigger: reacting with a relay-side rebuild would be a second
   disturbance on top of a finished repair.
3. The trigger is a timestamp that went BACKWARD, which is exactly what splicing a
   restarted stream into an open socket does. That is why a restart window explains
   most of them.

There is no millisecond threshold to tune. The condition is `audio_buffering_maxed()`,
and the logged number is only how far past the mix clock the source was. Measured on one
host on 2026-09-20: 103, 170-190, 999-1009, 4717 and 5415 ms, all the same event.

Not detectable here, and the caller must never imply otherwise: a lip-sync error whose
timestamps are internally consistent. OBS aligns by timestamp, so if the timestamps
themselves disagree with the content, nothing in OBS or the relay can know. Proving lip
sync needs content analysis or a person.

Pure: no I/O, no threads, no clock of its own. The relay owns the tail thread and passes
`now`. Tests: tests/test_av_sync.py.
"""
import re

# How long after a feed entered `serving` a repair still counts as explained by that
# restart. Measured 2026-09-20: repairs landed 6-10 s after the feed started serving
# (three of four restarts produced one). 30 s matches the heartbeat and leaves room for
# a slower host without swallowing a genuinely unexplained one an hour later.
RESTART_WINDOW_S = 30.0

# How long an unexplained repair keeps the health reason up. Long enough that a director
# who looks away for a moment still sees it, short enough that one blip does not paint
# the panel yellow for the rest of the event.
HEALTH_HOLD_S = 300.0

# `Source <name> audio is lagging (over by <n> ms) at max audio buffering.`
# The name may contain spaces ("Feed POV"), so it is taken up to " audio is lagging".
_REPAIR_RE = re.compile(
    r"^(?P<at>\d{2}:\d{2}:\d{2}\.\d{3}):\s+Source\s+(?P<source>.+?)\s+audio is lagging\s+"
    r"\(over by\s+(?P<ms>[\d.]+)\s+ms\)")
# Two shapes, same meaning: a decode timestamp that did not move forward.
_DTS_RE = re.compile(r"^(?P<at>\d{2}:\d{2}:\d{2}\.\d{3}):\s+warning:\s+DTS\s+"
                     r"(?:\d+\s+<\s+\d+\s+out of order|discontinuity\s+in\s+stream)")
_CORRUPT_RE = re.compile(r"^(?P<at>\d{2}:\d{2}:\d{2}\.\d{3}):\s+warning:\s+Packet corrupt\b")

# The OBS inputs that are feeds. `Commentary Mic Device` (#593) is an input too and must
# never be read as one.
_FEED_SOURCES = {"Feed A": "A", "Feed B": "B", "Feed POV": "POV"}


def parse_obs_log_line(line):
    """One OBS log line -> an event dict, or None when it is not one of ours.

    `{"at": "<HH:MM:SS.mmm as OBS wrote it>", "kind": ..., "source": str|None,
      "ms": float|None}`. Only `audio_repair` names a source and carries a magnitude;
    the DTS and packet lines carry neither, so they stay unattributed rather than being
    guessed onto a feed. Pure."""
    if not line:
        return None
    m = _REPAIR_RE.match(line)
    if m:
        return {"at": m.group("at"), "kind": "audio_repair",
                "source": m.group("source"), "ms": float(m.group("ms"))}
    for rx, kind in ((_DTS_RE, "dts_backward"), (_CORRUPT_RE, "packet_corrupt")):
        m = rx.match(line)
        if m:
            return {"at": m.group("at"), "kind": kind, "source": None, "ms": None}
    return None


def feed_for_source(source):
    """The feed name an OBS source belongs to, or None. Pure."""
    return _FEED_SOURCES.get(source)


def new_state():
    """An empty detector state. The relay holds one of these under its own lock."""
    return {"feeds": {}, "context": {}}


def record(state, event, now, serving_age_s, window_s=RESTART_WINDOW_S):
    """Fold one parsed event into `state` and return it (mutated in place).

    `serving_age_s` is how long the event's feed has been in the `serving` phase, or
    None when it is not serving. A repair within `window_s` of that feed starting to
    serve is EXPECTED — the relay caused it by splicing a new stream. Anything else is
    UNEXPLAINED, and that is the only kind worth a health reason: something disturbed
    the stream that the relay did not do. Pure."""
    if not event:
        return state
    feed = feed_for_source(event.get("source"))
    if feed is None:
        kind = event.get("kind") or "unknown"
        state["context"][kind] = state["context"].get(kind, 0) + 1
        return state
    expected = serving_age_s is not None and serving_age_s <= window_s
    f = state["feeds"].setdefault(
        feed, {"repairs": 0, "unexplained": 0, "last_ms": None,
               "last_ts": None, "last_at": None, "last_unexplained_ts": None,
               "last_unexplained_ms": None})
    f["repairs"] += 1
    f["last_ms"] = event.get("ms")
    f["last_ts"] = now
    f["last_at"] = event.get("at")
    if not expected:
        f["unexplained"] += 1
        f["last_unexplained_ts"] = now
        # Kept apart from last_ms on purpose. Live on the producer host the reason
        # read "broke by 997 ms" for an unexplained repair that had been 987 ms; the
        # 997 belonged to a LATER, expected one. Naming another event's magnitude is
        # the mis-attribution this detector exists to avoid.
        f["last_unexplained_ms"] = event.get("ms")
    return state


def status_block(state, now):
    """The per-feed `av` block for /status. Empty dict when nothing has happened, so a
    clean event adds no noise. Pure."""
    out = {}
    for feed, f in state["feeds"].items():
        out[feed] = {"repairs": f["repairs"], "unexplained": f["unexplained"],
                     "last_ms": f["last_ms"], "last_at": f["last_at"],
                     "last_age_s": (round(now - f["last_ts"], 1)
                                    if f["last_ts"] is not None else None)}
    return out


def health_fact(state, now, hold_s=HEALTH_HOLD_S):
    """feed -> magnitude in ms, for feeds with a RECENT unexplained repair.

    Only unexplained ones: a repair right after a restart is the expected cost of the
    restart and paging on it would cry wolf on every handover. Pure."""
    out = {}
    for feed, f in state["feeds"].items():
        ts = f.get("last_unexplained_ts")
        if ts is not None and (now - ts) <= hold_s:
            out[feed] = f.get("last_unexplained_ms")
    return out

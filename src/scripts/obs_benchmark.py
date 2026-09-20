#!/usr/bin/env python3
"""`racecast obs benchmark` (#584): measure the whole chain before automating a remedy.

Answers one question for epic #581 stage 5: on this host, does the on-air feed at
ROBUST (720p) bring the OBS consumer back to real time where FULL (1080p) does not?
It drives the program scene, starts a recording (render lag only appears with an
active output; a measurement without one is worthless), pins the on-air feed to
FULL and then to ROBUST, and samples over the same window for each:

- OBS `averageFrameRenderTime`, `activeFps` and the per-window render skip rate;
- the encoder: skipped output frames and recorded seconds per wall-clock second
  (`renderSkippedFrames` is blind to encoder-side lag);
- the playback rate of the feed's OBS media input (mediaCursor over wall clock) and
  the share of ticks where it stood still: fps and the encoder are blind to a frozen
  demuxer, the cursor is not;
- the fan-out consumer backlog behind the live edge (#583), as the floor at the start
  and at the end of the window. Reported, not judged: on 5-s HLS segments the raw
  value is a sawtooth, and after a restart it is dominated by the prefetch (#614).

After each tier switch the new serve's HLS prefetch lands in one burst. OBS, still
attached, would play that burst and sit its length behind the live edge (#614), so the
benchmark rejoins OBS once the prefetch has arrived (`POST /obs/feed-reset`), then
settles and samples. A window in which the ring lapped OBS, OBS reconnected, or the
feed stopped serving is marked disturbed and gives no verdict.

The upstream latency the ROBUST streamlink profile adds is a separate quantity. It
is recorded as the extra HLS segments ROBUST holds back from the live edge, read
from the two flag sets, not clocked: measuring it would need a manifest fetch the
relay deliberately does not make.

The result is appended to a machine-level JSONL history (top-level `runtime/`, like
`speedtest-history.jsonl`: a host property, not a league one) and reported by
`racecast preflight` with its age. Never runs automatically, never during a
broadcast. Pure Python 3 standard library; the OBS session and relay calls are
injected so the core stays testable.
"""
import json
import os
import tempfile
import time
import urllib.parse
from collections import namedtuple

# Same shape and level strings as preflight.Result, defined locally: preflight
# imports this module lazily, so importing preflight back would be a cycle.
PASS, WARN, INFO = "PASS", "WARN", "INFO"
Result = namedtuple("Result", ("level", "name", "detail"))

HISTORY_NAME = "obs-benchmark-history.jsonl"
HISTORY_LIMIT = 10
# A host changes slowly (a driver update, a new OBS, another scene layer), but a
# month-old number no longer describes the machine that goes on air tonight.
DEFAULT_MAX_AGE_DAYS = 30

DEFAULT_WINDOW_S = 60          # sampling window per tier
DEFAULT_SETTLE_S = 10          # after a tier switch: let the rejoin transient pass
SAMPLE_EVERY_S = 2.0
SERVING_TIMEOUT_S = 90         # a re-resolve plus reconnect; a live source takes seconds
# OBS answers StopRecord before the file is finalized (seen on 32.2.2). Deleting it
# then works on macOS but fails on Windows, where the file is still open.
RECORD_STOP_TIMEOUT_S = 30
TIERS = ("full", "robust")

# After the new serve delivers its first bytes, how long until the HLS prefetch burst
# has landed in the ring. streamlink fetches it in one go; a few seconds covers it.
# Since #614 the relay waits the same span before rejoining OBS by itself
# (`FEED_PREFETCH_LAND_S` in the relay) — keep the two in sync. The benchmark keeps
# issuing its own reset: it needs to know exactly when the rejoin happened to time the
# settle window, and the relay's is best-effort and fires only with OBS attached.
PREFETCH_LAND_S = 5
# The rejoin rebuilds the OBS input; OBS reconnects within a second or two. Sampling
# before that would see the rebuild's cursor jump and mark every window disturbed.
MIN_SETTLE_S = 3

# Real time. Rendering, encoding and playback each have to keep up. Playback is judged
# from the feed's mediaCursor: STALL_RATIO is the relay's freeze threshold
# (RACECAST_FEED_FREEZE_STALL_RATIO default), a tick below it counts as stood still.
REAL_TIME_FPS_RATIO = 0.95
ENCODER_SPEED_MIN = 0.98
PLAYBACK_RATE_MIN = 0.95
STALL_RATIO = 0.25
STALL_FRACTION_MAX = 0.10
# A tick that stood still while OBS had read everything the ring held (backlog at the
# live edge) is the source not delivering, not the host falling behind.
STARVED_BACKLOG_S = 0.5

_QUALITY_SOURCES = ("youtube", "twitch")


class BenchmarkRefused(RuntimeError):
    """The chain is in a state the benchmark must not touch (live, recording, …)."""


class BenchmarkFailed(RuntimeError):
    """The benchmark started but could not complete a measurement."""


# --------------------------------------------------------------------------
# pure core
# --------------------------------------------------------------------------
def live_edge_segments(flags):
    """The `--hls-live-edge` value of a streamlink flag list, or None."""
    try:
        return int(flags[flags.index("--hls-live-edge") + 1])
    except (ValueError, IndexError, TypeError):
        return None


def robust_extra_segments(full_flags, robust_flags):
    """How many more HLS segments the ROBUST profile holds back from the live edge
    than FULL: the upstream latency it adds, in segments. None when unknown."""
    full, robust = live_edge_segments(full_flags), live_edge_segments(robust_flags)
    if full is None or robust is None:
        return None
    return robust - full


def _values(samples, key):
    return [s[key] for s in samples if s.get(key) is not None]


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _round(x, nd):
    return None if x is None else round(x, nd)


def _delta_pct(samples, part_key, total_key):
    """Skipped/total over the window from the first and last cumulative counts."""
    pts = [s for s in samples if s.get(part_key) is not None
           and s.get(total_key) is not None]
    if len(pts) < 2:
        return None
    total = pts[-1][total_key] - pts[0][total_key]
    if total <= 0:
        return None
    return round((pts[-1][part_key] - pts[0][part_key]) / total * 100.0, 2)


def _playback(samples):
    """(rate, stall_fraction, rejoined) from the mediaCursor over the window. A cursor
    that goes backwards is a rejoin: that pair is not measured and the window is
    marked. Pure."""
    pairs, rejoined = [], False
    pts = [(s["t"], s.get("cursor_ms")) for s in samples if s.get("cursor_ms") is not None]
    for (t0, c0), (t1, c1) in zip(pts, pts[1:], strict=False):
        if t1 <= t0:
            continue
        if c1 < c0:
            rejoined = True
            continue
        pairs.append(((c1 - c0) / 1000.0, t1 - t0))
    if not pairs:
        return None, None, rejoined
    rate = sum(d for d, _ in pairs) / sum(dt for _, dt in pairs)
    stalled = sum(1 for d, dt in pairs if d / dt < STALL_RATIO)
    return round(rate, 3), round(stalled / len(pairs), 2), rejoined


def _starved_s(samples):
    """Seconds in which OBS's cursor stood still with nothing left to read: the source
    stopped delivering. Needs the backlog of the later sample of each pair. Pure."""
    pts = [s for s in samples if s.get("cursor_ms") is not None]
    total = 0.0
    for a, b in zip(pts, pts[1:], strict=False):
        dt = b["t"] - a["t"]
        if dt <= 0 or b["cursor_ms"] < a["cursor_ms"]:
            continue
        stalled = (b["cursor_ms"] - a["cursor_ms"]) / 1000.0 / dt < STALL_RATIO
        backlog = b.get("backlog_s")
        if stalled and backlog is not None and backlog <= STARVED_BACKLOG_S:
            total += dt
    return total


def _floor(values):
    return min(values) if values else None


def _disturbances(samples, rejoined):
    """Why a window cannot be judged: the ring lapped OBS, OBS reconnected, or the feed
    left serving. Pure."""
    out = []
    snaps = [s["snaps"] for s in samples if s.get("snaps") is not None]
    if snaps and snaps[-1] < snaps[0]:
        rejoined = True                  # a new consumer starts counting again
    elif snaps and snaps[-1] > snaps[0]:
        out.append(f"the ring lapped OBS {snaps[-1] - snaps[0]} time(s)")
    if rejoined:
        out.append("OBS reconnected the feed")
    left = [s["state"] for s in samples if s.get("state") not in (None, "serving")]
    if left:
        out.append(f"the feed left serving ({left[0]})")
    starved = _starved_s(samples)
    if starved > 0:
        out.append(f"the source stopped delivering ({starved:.0f} s)")
    return out


def summarize(samples):
    """One tier's sampling window -> the numbers the verdict and the report use.
    A missing value stays None; it is never filled in."""
    ts = [s["t"] for s in samples]
    rec = [s for s in samples if s.get("rec_ms") is not None]
    speed = None
    if len(rec) >= 2 and rec[-1]["t"] > rec[0]["t"]:
        speed = round((rec[-1]["rec_ms"] - rec[0]["rec_ms"]) / 1000.0
                      / (rec[-1]["t"] - rec[0]["t"]), 3)
    fps = _values(samples, "fps")
    rate, stall, rejoined = _playback(samples)
    backlog = _values(samples, "backlog_s")
    third = max(1, len(samples) // 3)
    snaps = _values(samples, "snaps")
    return {
        "samples": len(samples),
        "duration_s": round(ts[-1] - ts[0], 1) if ts else None,
        "render_ms_avg": _round(_mean(_values(samples, "render_ms")), 2),
        "fps_avg": _round(_mean(fps), 2),
        "fps_min": _round(min(fps), 2) if fps else None,
        "render_skip_pct": _delta_pct(samples, "render_skipped", "render_total"),
        "encoder_skip_pct": _delta_pct(samples, "output_skipped", "output_total"),
        "encoder_speed": speed,
        "playback_rate": rate,
        "stall_fraction": stall,
        "backlog_floor_start_s": _floor(_values(samples[:third], "backlog_s")),
        "backlog_floor_end_s": _floor(_values(samples[-third:], "backlog_s")),
        "backlog_max_s": max(backlog) if backlog else None,
        "snaps": (snaps[-1] - snaps[0]) if len(snaps) >= 2 else None,
        "contaminated": _disturbances(samples, rejoined),
    }


def keeps_real_time(summary, fps_target):
    """True when OBS renders at (nearly) its configured rate, the encoder keeps up with
    the wall clock and the feed's media cursor plays at real time without standing
    still. None when a needed signal is missing or the window was disturbed. The
    backlog is not a criterion (see the module docstring)."""
    if summary.get("contaminated"):
        return None
    fps = summary.get("fps_avg")
    if fps is None or not fps_target:
        return None
    if fps < fps_target * REAL_TIME_FPS_RATIO:
        return False
    speed = summary.get("encoder_speed")
    if speed is not None and speed < ENCODER_SPEED_MIN:
        return False
    rate = summary.get("playback_rate")
    if rate is None:
        return None
    stall = summary.get("stall_fraction") or 0.0
    return rate >= PLAYBACK_RATE_MIN and stall <= STALL_FRACTION_MAX


def verdict(full, robust, fps_target):
    """The calibration answer: does ROBUST recover a host where FULL falls behind?
    `robust_recovers` is None when FULL already keeps up or either side is unknown.
    `contaminated` names the disturbed tiers and why."""
    f, r = keeps_real_time(full, fps_target), keeps_real_time(robust, fps_target)
    recovers = r if (f is False and r is not None) else None
    bad = {t: s["contaminated"] for t, s in (("full", full), ("robust", robust))
           if s.get("contaminated")}
    return {"full_real_time": f, "robust_real_time": r, "robust_recovers": recovers,
            "contaminated": bad}


def refusal(status, stream_active, record_active):
    """Why the benchmark must not run, or None. Checked before anything touches OBS
    or a feed. `status` is the relay's /status; the two flags come from OBS."""
    if stream_active is None or record_active is None:
        return "OBS output state unknown — is obs-websocket reachable?"
    if stream_active:
        return ("OBS is streaming — the benchmark switches scenes and reconnects the "
                "on-air feed; run it before the broadcast")
    if record_active:
        return "OBS is already recording — stop that recording first"
    feed = ((status or {}).get("live") or {}).get("feed")
    if not feed:
        return "no on-air feed — start the relay with a live stint first"
    f = ((status or {}).get("feeds") or {}).get(feed) or {}
    if "backlog_s" not in f:
        return ("the relay runs without feed fan-out (RACECAST_FEED_FANOUT=0) — "
                "there is no consumer backlog to measure")
    if f.get("platform") not in _QUALITY_SOURCES:
        return (f"Feed {feed} carries a {f.get('platform') or 'unknown'} source, which "
                "has no quality tiers to compare")
    if f.get("state") != "serving":
        return f"Feed {feed} is not serving (state: {f.get('state')})"
    return None


def serving_since_switch(feed_status, elapsed):
    """True once the feed serves again AFTER a tier switch `elapsed` seconds ago: a
    serving phase older than the switch is the old serve before its kill landed."""
    return (feed_status or {}).get("state") == "serving" and \
        (feed_status.get("state_age_s") or 0.0) <= elapsed


def restore_tier(orig):
    """The tier to POST back: a pin is restored as it was; an unpinned feed is
    released to auto (managed FULL)."""
    return (orig.get("profile") or "full") if orig.get("pinned") else "auto"


# --------------------------------------------------------------------------
# history (machine-level JSONL, like speedtest-history.jsonl)
# --------------------------------------------------------------------------
def history_path(runtime_dir):
    return os.path.join(runtime_dir, HISTORY_NAME)


def _read_all(runtime_dir):
    out = []
    try:
        with open(history_path(runtime_dir), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue   # one bad write must not lose the history
    except FileNotFoundError:
        return []
    return out


def load_latest(runtime_dir):
    """The most recent record, or None when nothing has been measured yet."""
    recs = _read_all(runtime_dir)
    return recs[-1] if recs else None


def append_record(record, runtime_dir):
    """Append one record, keeping the last HISTORY_LIMIT (atomic rewrite)."""
    os.makedirs(runtime_dir, exist_ok=True)
    kept = (_read_all(runtime_dir) + [record])[-HISTORY_LIMIT:]
    fd, tmp = tempfile.mkstemp(dir=runtime_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for rec in kept:
                fh.write(json.dumps(rec) + "\n")
        os.replace(tmp, history_path(runtime_dir))
    except BaseException:
        os.unlink(tmp)
        raise
    return record


# --------------------------------------------------------------------------
# preflight line + CLI rendering
# --------------------------------------------------------------------------
def _fmt_age(age_days):
    if age_days < 1 / 24:
        return "just now"
    if age_days < 1:
        return f"{int(age_days * 24)} h ago"
    return f"{int(age_days)} d ago"


def _outcome(v):
    if v.get("full_real_time"):
        return "FULL keeps real time"
    if v.get("full_real_time") is None:
        return "real time not determined"
    if v.get("robust_recovers"):
        return "FULL falls behind, ROBUST recovers"
    if v.get("robust_recovers") is False:
        return "FULL falls behind, ROBUST does not recover — this host is unsuitable"
    return "FULL falls behind, ROBUST not determined"


def classify(record, now, max_age_days=DEFAULT_MAX_AGE_DAYS):
    """Latest record -> a preflight Result. Stale or behind real time WARN; never FAIL."""
    if not record:
        return Result(INFO, "OBS benchmark",
                      "not measured yet — run `racecast obs benchmark` before the event")
    age = max(0.0, (now - record.get("ts", now)) / 86_400.0)
    v = record.get("verdict") or {}
    where = f"{_outcome(v)} · measured {_fmt_age(age)}"
    if age > max_age_days:
        return Result(WARN, "OBS benchmark",
                      f"{where} — stale (older than {int(max_age_days)} d); re-run it")
    bad = v.get("contaminated") or {}
    if bad:
        why = "; ".join(f"{t.upper()}: {', '.join(r)}" for t, r in bad.items())
        return Result(WARN, "OBS benchmark",
                      f"measurement disturbed ({why}) · measured {_fmt_age(age)} — re-run it")
    if v.get("full_real_time") is False:
        return Result(WARN, "OBS benchmark", where)
    if v.get("full_real_time") is None:
        return Result(WARN, "OBS benchmark", where)
    return Result(PASS, "OBS benchmark", where)


def _fmt(x, unit="", nd=1):
    return "n/a" if x is None else f"{x:.{nd}f}{unit}"


def render(record, now):
    """Human-readable summary for the CLI."""
    lines = [f"OBS benchmark — Feed {record.get('feed')} ({record.get('platform')}) "
             f"on '{record.get('scene')}', {record.get('window_s')} s per tier, "
             f"target {_fmt(record.get('fps_target'), ' fps', 2)}",
             "              render ms   fps avg/min   render skip   encoder skip/speed"
             "   playback rate/stalled   backlog floor start→end"]
    for tier in TIERS:
        s = record.get(tier) or {}
        lines.append(
            f"  {tier.upper():<8}    {_fmt(s.get('render_ms_avg'), '', 2):>8}   "
            f"{_fmt(s.get('fps_avg'))}/{_fmt(s.get('fps_min'))}   "
            f"{_fmt(s.get('render_skip_pct'), ' %', 2):>10}   "
            f"{_fmt(s.get('encoder_skip_pct'), ' %', 2)}/{_fmt(s.get('encoder_speed'), 'x', 3)}"
            f"   {_fmt(s.get('playback_rate'), 'x', 3)}/"
            f"{_fmt(None if s.get('stall_fraction') is None else s['stall_fraction'] * 100, ' %', 0)}"
            f"   {_fmt(s.get('backlog_floor_start_s'), ' s')}→"
            f"{_fmt(s.get('backlog_floor_end_s'), ' s')}")
        if s.get("contaminated"):
            lines.append(f"              disturbed: {'; '.join(s['contaminated'])}")
    extra = record.get("robust_extra_segments")
    lines.append("  Upstream: ROBUST holds " + (
        "n/a" if extra is None else f"+{extra} HLS segments")
        + " more behind the source's live edge than FULL (derived from the "
          "streamlink flags, not clocked)")
    c = classify(record, now)
    lines.append(f"  => {c.level}: {c.detail}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------
def feed_input_name(session, port):
    """The OBS media input that plays the relay feed on `port`, or None."""
    want = {f"127.0.0.1:{port}", f"localhost:{port}"}
    inputs = (session.request("GetInputList", {"inputKind": "ffmpeg_source"}) or {}).get(
        "inputs", [])
    for inp in inputs:
        name = inp.get("inputName")
        if not name:
            continue
        settings = (session.request("GetInputSettings", {"inputName": name}) or {}).get(
            "inputSettings", {})
        url = settings.get("input")
        if isinstance(url, str) and urllib.parse.urlsplit(url.strip()).netloc in want:
            return name
    return None


def _obs_sample(session, t, relay_status, feed, input_name):
    stats = session.request("GetStats", {}) or {}
    rec = session.request("GetRecordStatus", {}) or {}
    media = session.request("GetMediaInputStatus", {"inputName": input_name}) or {}
    f = ((relay_status or {}).get("feeds") or {}).get(feed) or {}
    return {"t": t, "fps": stats.get("activeFps"),
            "cursor_ms": media.get("mediaCursor"),
            "state": f.get("state"),
            "snaps": f.get("consumer_snaps"),
            "render_ms": stats.get("averageFrameRenderTime"),
            "render_skipped": stats.get("renderSkippedFrames"),
            "render_total": stats.get("renderTotalFrames"),
            "output_skipped": stats.get("outputSkippedFrames"),
            "output_total": stats.get("outputTotalFrames"),
            "rec_ms": rec.get("outputDuration"),
            "backlog_s": f.get("backlog_s")}


def _fps_target(session):
    p = session.request("GetVideoSettings", {}) or {}
    num, den = p.get("fpsNumerator"), p.get("fpsDenominator")
    if not isinstance(num, (int, float)) or not isinstance(den, (int, float)) \
            or num <= 0 or den <= 0:
        return None
    return round(num / den, 3)


def _wait_serving(relay, feed, t_switch, clock, sleep, timeout_s):
    while True:
        elapsed = clock() - t_switch
        f = ((relay.status() or {}).get("feeds") or {}).get(feed) or {}
        if serving_since_switch(f, elapsed):
            return round(elapsed, 1)
        if elapsed > timeout_s:
            raise BenchmarkFailed(f"Feed {feed} did not serve again within {timeout_s} s "
                                  f"of the tier switch (state: {f.get('state')})")
        sleep(1.0)


def _record_finished(session, sleep, timeout_s):
    """Wait until OBS reports the recording output inactive. False on timeout."""
    waited = 0.0
    while (session.request("GetRecordStatus", {}) or {}).get("outputActive"):
        if waited >= timeout_s:
            return False
        sleep(1.0)
        waited += 1.0
    return True


def _rejoin_after_prefetch(relay, feed, sleep):
    """Let the new serve's HLS prefetch land, then reconnect OBS to the feed so it joins
    at the live edge instead of playing the burst (#614)."""
    sleep(PREFETCH_LAND_S)
    relay.feed_reset(feed)


def _measure_tier(relay, session, feed, tier, input_name, *, clock, sleep, window_s,
                  settle_s, sample_every_s, serving_timeout_s, progress):
    progress(f"Feed {feed} → {tier.upper()}: reconnecting …")
    # Clock the switch BEFORE the request: the relay may restart the serve before its
    # reply arrives, and that serve must still count as the one after the switch.
    t_switch = clock()
    relay.set_quality(feed, tier)
    reconnect_s = _wait_serving(relay, feed, t_switch, clock, sleep, serving_timeout_s)
    progress(f"  serving again after {reconnect_s} s; rejoining OBS after the prefetch, "
             f"settling {settle_s} s, then sampling {window_s} s")
    _rejoin_after_prefetch(relay, feed, sleep)
    sleep(settle_s)
    samples, t0 = [], clock()
    while True:
        t = clock() - t0
        samples.append(_obs_sample(session, round(t, 2), relay.status(), feed, input_name))
        if t >= window_s:
            break
        sleep(sample_every_s)
    return {**summarize(samples), "reconnect_s": reconnect_s}


def run(relay, session, runtime_dir, *, flags, scene="Stint", window_s=DEFAULT_WINDOW_S,
        settle_s=DEFAULT_SETTLE_S, sample_every_s=SAMPLE_EVERY_S,
        serving_timeout_s=SERVING_TIMEOUT_S, keep_recording=False,
        clock=time.monotonic, sleep=time.sleep, now=time.time, remove=os.remove,
        isfile=os.path.isfile, getmtime=os.path.getmtime, progress=lambda _msg: None):
    """Measure FULL then ROBUST on the on-air feed, persist the record, return it.

    `relay` has status() -> the /status dict and set_quality(feed, tier); `session`
    is an obs-websocket session (request(type, data) -> dict). `flags` maps a
    platform to its (full, robust) streamlink flag lists. Whatever happens after the
    gate, the recording is stopped and the scene and the feed's tier are restored."""
    status = relay.status()
    stream = session.request("GetStreamStatus", {}) or {}
    record_st = session.request("GetRecordStatus", {}) or {}
    why = refusal(status, stream.get("outputActive"), record_st.get("outputActive"))
    if why:
        raise BenchmarkRefused(why)
    feed = status["live"]["feed"]
    orig = status["feeds"][feed]
    platform = orig.get("platform")
    input_name = feed_input_name(session, orig.get("port"))
    if input_name is None:
        raise BenchmarkRefused(f"OBS has no media input on the Feed {feed} port "
                               f"({orig.get('port')}) — is the racecast collection loaded?")
    fps_target = _fps_target(session)
    cur = session.request("GetCurrentProgramScene", {}) or {}
    orig_scene = cur.get("currentProgramSceneName") or cur.get("sceneName")
    results, recording, out_path, started = {}, False, None, None
    settle_s = max(settle_s, MIN_SETTLE_S)
    interrupted = False
    try:
        if scene and scene != orig_scene:
            session.request("SetCurrentProgramScene", {"sceneName": scene})
        started = now()
        session.request("StartRecord", {})
        recording = True
        for tier in TIERS:
            results[tier] = _measure_tier(
                relay, session, feed, tier, input_name, clock=clock, sleep=sleep,
                window_s=window_s,
                settle_s=settle_s, sample_every_s=sample_every_s,
                serving_timeout_s=serving_timeout_s, progress=progress)
    except KeyboardInterrupt:
        interrupted = True
        raise
    finally:
        notes = []
        if recording:
            try:
                out_path = (session.request("StopRecord", {}) or {}).get("outputPath")
            except Exception as exc:              # noqa: BLE001 — keep restoring
                notes.append(f"stopping the recording failed ({exc}) — stop it in OBS")
        restored_at = None
        try:
            t_restore = clock()
            relay.set_quality(feed, restore_tier(orig))
            restored_at = t_restore
            if not orig.get("pinned") and (orig.get("profile") or "full") != "full":
                # the API can only pin a tier or release it; releasing ends the step-down
                notes.append(f"Feed {feed} was on {orig.get('profile').upper()} from an "
                             "automatic step-down; it is back on managed FULL now")
        except Exception as exc:                  # noqa: BLE001 — keep restoring
            notes.append(f"restoring Feed {feed}'s quality failed ({exc})")
        if orig_scene and scene and scene != orig_scene:
            try:
                session.request("SetCurrentProgramScene", {"sceneName": orig_scene})
            except Exception as exc:              # noqa: BLE001 — keep restoring
                notes.append(f"switching back to '{orig_scene}' failed ({exc})")
        finished = False
        if out_path:
            try:
                finished = _record_finished(session, sleep, RECORD_STOP_TIMEOUT_S)
            except Exception as exc:              # noqa: BLE001 — keep restoring
                notes.append(f"reading the recording state failed ({exc})")
            if not finished:
                keep_recording = True
                notes.append(f"OBS is still writing {out_path} — left in place")
        if out_path and not keep_recording and isfile(out_path):
            try:
                # OBS names the path. Only a file written since StartRecord is ours:
                # a remote OBS (RACECAST_OBS_WS_HOST) names a path on ITS disk.
                if started is None or getmtime(out_path) < started - 1:
                    keep_recording = True
                    notes.append(f"{out_path} was not created by this benchmark — "
                                 "not deleted")
                else:
                    remove(out_path)
            except OSError as exc:
                keep_recording = True
                notes.append(f"could not delete the benchmark recording {out_path} ({exc})")
        if restored_at is not None and interrupted:
            notes.append(f"interrupted — OBS was not rejoined to Feed {feed}; press RESET "
                         f"for Feed {feed} in the Director Panel")
        elif restored_at is not None:
            # The restore is a restart too: without a rejoin OBS keeps playing its
            # prefetch and the operator is left that far behind live (#614). Last, so
            # an interrupt here cannot keep the scene or the recording from coming back.
            try:
                _wait_serving(relay, feed, restored_at, clock, sleep, serving_timeout_s)
                _rejoin_after_prefetch(relay, feed, sleep)
            except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 — cleanup: report, go on
                notes.append(f"Feed {feed} is back on its tier but OBS was not rejoined "
                             f"({exc.__class__.__name__}: {exc}) — press RESET for "
                             f"Feed {feed} in the Director Panel")
        for n in notes:
            progress("WARNING: " + n)
    full_flags, robust_flags = flags.get(platform, ([], []))
    record = {"ts": int(now()), "feed": feed, "platform": platform, "scene": scene,
              "fps_target": fps_target, "window_s": window_s,
              "full": results["full"], "robust": results["robust"],
              "robust_extra_segments": robust_extra_segments(full_flags, robust_flags),
              "verdict": verdict(results["full"], results["robust"], fps_target)}
    if keep_recording and out_path:
        record["recording"] = out_path
    append_record(record, runtime_dir)
    return record

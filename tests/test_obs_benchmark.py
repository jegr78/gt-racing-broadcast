#!/usr/bin/env python3
"""Stdlib checks for `racecast obs benchmark` (#584). Run: python3 tests/test_obs_benchmark.py

Every OBS and relay call goes through a fake: a real OBS listens on 4455 on the
maintainer's machine, and the benchmark starts a recording."""
import os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import obs_benchmark as m   # noqa: E402

NOW = 1_800_000_000
FULL_FLAGS = ["--ringbuffer-size", "64M", "--hls-live-edge", "4"]
ROBUST_FLAGS = ["--ringbuffer-size", "128M", "--hls-live-edge", "6"]


def _sample(t, fps=60.0, render_ms=4.0, rs=0, rt=0, os_=0, ot=0, rec_ms=0, backlog=3.0,
            cursor=None, state="serving", snaps=0):
    return {"t": t, "fps": fps, "render_ms": render_ms, "render_skipped": rs,
            "render_total": rt, "output_skipped": os_, "output_total": ot,
            "rec_ms": rec_ms, "backlog_s": backlog,
            "cursor_ms": t * 1000 if cursor is None else cursor,
            "state": state, "snaps": snaps}


# --------------------------------------------------------------------------
# upstream latency of the ROBUST profile: a segment count read from the flags
# --------------------------------------------------------------------------
def t_live_edge_segments_reads_the_flag():
    assert m.live_edge_segments(FULL_FLAGS) == 4
    assert m.live_edge_segments(ROBUST_FLAGS) == 6


def t_live_edge_segments_without_the_flag_is_none():
    assert m.live_edge_segments(["--ringbuffer-size", "64M"]) is None
    assert m.live_edge_segments(["--hls-live-edge"]) is None
    assert m.live_edge_segments(["--hls-live-edge", "x"]) is None


def t_robust_extra_segments_is_the_difference():
    assert m.robust_extra_segments(FULL_FLAGS, ROBUST_FLAGS) == 2
    # Twitch keeps the edge and only grows the ring: no extra upstream latency.
    tw = ["--ringbuffer-size", "64M", "--hls-live-edge", "2", "--twitch-low-latency"]
    tw_r = ["--ringbuffer-size", "128M", "--hls-live-edge", "2", "--twitch-low-latency"]
    assert m.robust_extra_segments(tw, tw_r) == 0
    assert m.robust_extra_segments(["--x"], ROBUST_FLAGS) is None


# --------------------------------------------------------------------------
# one measurement window -> a summary
# --------------------------------------------------------------------------
def t_playback_treats_a_forward_cursor_jump_as_a_rejoin_too():
    # An OBS media source that is rebuilt does not always restart its cursor at zero: it
    # can resume on the new stream's own timestamps and jump FORWARD by hours. The guard
    # only knew the backward jump, so such a pair was counted as playback and one of them
    # dominated the whole window. Measured on the shipped helper before the fix: a jump to
    # 11_000_000 ms reported 1374x and rejoined=False, and keeps_real_time said True on it.
    # A broken measurement read as the healthiest possible answer.
    def w(*pairs):
        return [{"t": t, "cursor_ms": c} for t, c in pairs]
    back = w((0, 10_000), (2, 12_000), (4, 1_000), (6, 3_000), (8, 5_000))
    assert m._playback(back) == (1.0, 0.0, True), "the backward jump was always caught"
    fwd = w((0, 10_000), (2, 12_000), (4, 11_000_000), (6, 11_002_000), (8, 11_004_000))
    rate, stall, rejoined = m._playback(fwd)
    assert rejoined is True, "a forward jump is a rejoin as much as a backward one"
    assert rate == 1.0, rate            # the three sane pairs, the jump discarded
    assert stall == 0.0
    # The ceiling is the window's own wall time: OBS can outrun the wall clock only by
    # what its own buffer holds (8 MB, about 9 s at 7 Mbps), never by a whole window.
    # A pair inside that allowance stays playback, so draining a buffer is not a rejoin.
    # The window is the benchmark's default 60 s; the ceiling tightens with shorter ones.
    drain = w((0, 0), (2, 9_000)) + w(*[(t, 9_000 + (t - 2) * 1000) for t in range(4, 62, 2)])
    assert m._playback(drain)[2] is False, "a buffer drain is not a discontinuity"


def t_summary_says_when_the_source_outran_the_wall_clock():
    # A fresh serve walks the CDN's DVR window at whatever rate it can fetch, so media
    # arrives faster than real time. backlog_s ages by ARRIVAL, so it climbs while OBS
    # plays at 1.0x and nothing downstream is slow. Measured live on the production host:
    # the window reported 15.5 s -> 57.2 s, and three minutes later the steady state was
    # 4.6 s. Whoever reads that column concludes the machine is broken.
    # The consumer can only add (1 - playback_rate) * duration to a backlog. The rest came
    # from the inbound side, and that is arithmetic, not a guess.
    slow_consumer = [_sample(t, backlog=3.0 + t * 0.5, cursor=t * 500) for t in (0.0, 20.0, 40.0, 60.0)]
    s = m.summarize(slow_consumer)
    assert s["playback_rate"] == 0.5
    assert s["source_ahead_s"] == 0.0, s["source_ahead_s"]   # all of it is the consumer
    catching_up = [_sample(t, backlog=3.0 + t * 0.7, cursor=t * 1000) for t in (0.0, 20.0, 40.0, 60.0)]
    s = m.summarize(catching_up)
    assert s["playback_rate"] == 1.0
    assert s["source_ahead_s"] == 42.0, s["source_ahead_s"]  # none of it is the consumer
    # A steady feed: nothing to explain, so the field stays 0.0 rather than inventing one.
    steady = [_sample(t, backlog=4.0, cursor=t * 1000) for t in (0.0, 20.0, 40.0, 60.0)]
    assert m.summarize(steady)["source_ahead_s"] == 0.0
    # Missing signals must not become a number.
    assert m.summarize([_sample(0.0, backlog=None), _sample(2.0, backlog=None)])[
        "source_ahead_s"] is None


def t_summary_states_the_backlog_growth_rate_the_host_caused():
    # #585's trigger is "the backlog grows by at least X seconds per minute", and #584
    # promised this field as what calibrates it. The rate must exclude the source's early
    # arrival, or a bursty CDN would step a healthy host down.
    slow = [_sample(t, backlog=3.0 + t * 0.5, cursor=t * 500) for t in (0.0, 20.0, 40.0, 60.0)]
    s = m.summarize(slow)
    # 3.0 -> 33.0 s over 60 s, all of it the consumer's: 30 s of rise = 30 s/min.
    assert s["backlog_growth_s_per_min"] == 30.0, s["backlog_growth_s_per_min"]
    catching_up = [_sample(t, backlog=3.0 + t * 0.7, cursor=t * 1000) for t in (0.0, 20.0, 40.0, 60.0)]
    s = m.summarize(catching_up)
    # Same 42 s climb, but OBS played at 1.0x throughout, so the host caused none of it.
    assert s["backlog_growth_s_per_min"] == 0.0, s["backlog_growth_s_per_min"]
    # A shrinking backlog reports a negative rate; clamping it would hide a recovery.
    recovering = [_sample(t, backlog=30.0 - t * 0.25, cursor=t * 1000) for t in (0.0, 20.0, 40.0, 60.0)]
    g = m.summarize(recovering)["backlog_growth_s_per_min"]
    assert g == -15.0, f"a shrinking backlog must report a negative rate, got {g}"
    # Without a playback rate the split is unknowable, so the field stays None.
    no_cursor = [_sample(t, backlog=3.0 + t * 0.5, cursor=None) for t in (0.0, 60.0)]
    for smp in no_cursor:
        smp["cursor_ms"] = None
    g = m.summarize(no_cursor)["backlog_growth_s_per_min"]
    assert g is None, f"no playback rate means no attribution, got {g}"


def t_render_names_the_growth_rate_and_tolerates_a_record_without_it():
    # Every cell carries a value, so the only "n/a" the row can show is the one under test.
    def tier(growth):
        return {"render_ms_avg": 1.4, "fps_avg": 60.0, "fps_min": 60.0,
                "render_skip_pct": 0.0, "encoder_skip_pct": 0.0, "encoder_speed": 1.0,
                "playback_rate": 1.0, "stall_fraction": 0.0, "backlog_floor_start_s": 2.9,
                "backlog_floor_end_s": 38.8, "source_ahead_s": 0.0,
                "inbound_gap_worst_s": 1.2, "backlog_growth_s_per_min": growth}
    rec = {"feed": "A", "platform": "youtube", "scene": "Stint", "window_s": 60,
           "fps_target": 60.0, "ts": NOW, "full": tier(47.9), "robust": tier(0.2)}
    full_row = m.render(rec, NOW).splitlines()[2]
    assert "host growth" in m.render(rec, NOW), m.render(rec, NOW)
    assert "47.9 s/min" in full_row, full_row
    assert full_row.count("n/a") == 0, full_row
    # The history is append-only, so a record written before the field must still render —
    # with that one cell empty and every other value untouched.
    del rec["full"]["backlog_growth_s_per_min"]
    full_row = m.render(rec, NOW).splitlines()[2]
    assert full_row.count("n/a") == 1, full_row
    assert "47.9 s/min" not in full_row and "2.9 s→38.8 s" in full_row, full_row


def t_render_explains_a_backlog_the_source_caused():
    # The number is not hidden, it is named. Hiding it would also hide a real backlog.
    rec = {"feed": "A", "platform": "youtube", "scene": "Stint", "window_s": 60,
           "fps_target": 60.0, "ts": NOW,
           "full": {"backlog_floor_start_s": 15.5, "backlog_floor_end_s": 57.2,
                    "source_ahead_s": 41.7, "playback_rate": 0.99},
           "robust": {"backlog_floor_start_s": 4.0, "backlog_floor_end_s": 4.2,
                      "source_ahead_s": 0.0, "playback_rate": 1.0}}
    text = m.render(rec, NOW)
    assert "source was still catching up" in text, text
    assert "41.7 s" in text, text
    # The tier that did not catch up says nothing, or the note becomes noise.
    assert text.count("source was still catching up") == 1, text


def t_summarize_derives_rates_over_the_window():
    samples = [
        _sample(0.0, fps=60.0, render_ms=4.0, rs=100, rt=1000, os_=10, ot=1000,
                rec_ms=0, backlog=3.0, cursor=10_000),
        _sample(30.0, fps=50.0, render_ms=8.0, rs=250, rt=2800, os_=10, ot=2800,
                rec_ms=30_000, backlog=4.0, cursor=40_000),
        _sample(60.0, fps=40.0, render_ms=12.0, rs=400, rt=4600, os_=46, ot=4600,
                rec_ms=57_000, backlog=5.0, cursor=67_000),
    ]
    s = m.summarize(samples)
    assert s["samples"] == 3
    assert s["duration_s"] == 60.0
    assert s["render_ms_avg"] == 8.0
    assert s["fps_avg"] == 50.0 and s["fps_min"] == 40.0
    # (400-100) / (4600-1000) = 300/3600
    assert s["render_skip_pct"] == 8.33
    # (46-10) / (4600-1000)
    assert s["encoder_skip_pct"] == 1.0
    # 57 s of recording in 60 s of wall clock
    assert s["encoder_speed"] == 0.95
    # OBS played 57 s of media in 60 s of wall clock; the second half was slow
    assert s["playback_rate"] == 0.95 and s["stall_fraction"] == 0.0
    assert s["backlog_floor_start_s"] == 3.0 and s["backlog_floor_end_s"] == 5.0
    assert s["backlog_max_s"] == 5.0
    assert s["snaps"] == 0 and s["contaminated"] == []


def t_summarize_counts_frozen_ticks_from_the_cursor():
    # 60 fps and a PLAYING state say nothing about a frozen demuxer; the cursor does.
    samples = [_sample(t, cursor=5000) for t in (0.0, 2.0, 4.0, 6.0)]
    samples += [_sample(8.0, cursor=7000)]
    s = m.summarize(samples)
    assert s["stall_fraction"] == 0.75
    assert s["playback_rate"] == 0.25


def t_summarize_marks_a_disturbed_window():
    lapped = [_sample(0.0, snaps=2), _sample(10.0, snaps=5)]
    assert m.summarize(lapped)["contaminated"] == ["the ring lapped OBS 3 time(s)"]
    rejoined = [_sample(0.0, cursor=9000), _sample(10.0, cursor=1000)]
    assert m.summarize(rejoined)["contaminated"] == ["OBS reconnected the feed"]
    dropped = [_sample(0.0), _sample(10.0, state="connecting")]
    assert m.summarize(dropped)["contaminated"] == ["the feed left serving (connecting)"]
    # a consumer that left and came back starts its count again: that is a reconnect too
    restarted = [_sample(0.0, snaps=4), _sample(10.0, snaps=0)]
    assert m.summarize(restarted)["contaminated"] == ["OBS reconnected the feed"]


def t_summarize_blames_a_starved_consumer_on_the_source():
    # The source stopped delivering: OBS has read everything (backlog at the live edge,
    # 0.0) and its cursor stands still. That is not the host falling behind.
    samples = [_sample(0.0, cursor=5000, backlog=2.8), _sample(2.0, cursor=7000, backlog=0.9),
               _sample(4.0, cursor=7000, backlog=0.0), _sample(6.0, cursor=7000, backlog=0.0),
               _sample(8.0, cursor=9000, backlog=2.5)]
    s = m.summarize(samples)
    assert s["contaminated"] == ["the source stopped delivering (4 s)"]


def t_summarize_keeps_a_slow_consumer_with_data_waiting_on_the_host():
    # Data is waiting (the backlog grows) while the cursor stands: OBS is not reading.
    samples = [_sample(0.0, cursor=5000, backlog=3.0), _sample(2.0, cursor=5000, backlog=5.0),
               _sample(4.0, cursor=5000, backlog=7.0)]
    s = m.summarize(samples)
    assert s["contaminated"] == [] and s["stall_fraction"] == 1.0


def t_summarize_ignores_missing_values_instead_of_inventing_them():
    blank = dict(fps=None, render_ms=None, backlog=None, rec_ms=None, rs=None, rt=None,
                 os_=None, ot=None, snaps=None)
    samples = [_sample(0.0, **blank), _sample(10.0, **blank)]
    for smp in samples:
        smp["cursor_ms"] = None
    s = m.summarize(samples)
    assert s["samples"] == 2
    for key in ("render_ms_avg", "fps_avg", "fps_min", "render_skip_pct",
                "encoder_skip_pct", "encoder_speed", "playback_rate", "stall_fraction",
                "backlog_floor_start_s", "backlog_floor_end_s", "backlog_max_s",
                "backlog_growth_s_per_min", "snaps"):
        assert s[key] is None, key
    assert s["contaminated"] == []


def t_summarize_of_nothing_is_empty_not_a_crash():
    s = m.summarize([])
    assert s["samples"] == 0 and s["duration_s"] is None and s["fps_avg"] is None
    assert s["playback_rate"] is None and s["contaminated"] == []


# --------------------------------------------------------------------------
# real time: frame rate, encoder speed and the media cursor
# --------------------------------------------------------------------------
def _summary(fps=60.0, speed=1.0, rate=1.0, stall=0.0, contaminated=()):
    return {"fps_avg": fps, "encoder_speed": speed, "playback_rate": rate,
            "stall_fraction": stall, "contaminated": list(contaminated)}


def t_keeps_real_time_on_a_healthy_window():
    assert m.keeps_real_time(_summary(), 60.0) is True


def t_keeps_real_time_fails_on_each_failing_part():
    assert m.keeps_real_time(_summary(fps=50.0), 60.0) is False
    assert m.keeps_real_time(_summary(speed=0.9), 60.0) is False
    assert m.keeps_real_time(_summary(rate=0.8), 60.0) is False
    assert m.keeps_real_time(_summary(stall=m.STALL_FRACTION_MAX + 0.05), 60.0) is False


def t_keeps_real_time_at_the_thresholds_holds():
    assert m.keeps_real_time(_summary(fps=60.0 * m.REAL_TIME_FPS_RATIO,
                                      speed=m.ENCODER_SPEED_MIN,
                                      rate=m.PLAYBACK_RATE_MIN,
                                      stall=m.STALL_FRACTION_MAX), 60.0) is True


def t_keeps_real_time_is_unknown_without_the_signals_it_needs():
    assert m.keeps_real_time(_summary(fps=None), 60.0) is None
    assert m.keeps_real_time(_summary(), None) is None
    # without the cursor a frozen demuxer is indistinguishable from a healthy one
    assert m.keeps_real_time(_summary(rate=None), 60.0) is None


def t_keeps_real_time_is_unknown_for_a_disturbed_window():
    assert m.keeps_real_time(_summary(contaminated=["the ring lapped OBS 1 time(s)"]),
                             60.0) is None


def t_verdict_answers_the_calibration_question():
    v = m.verdict(_summary(fps=45.0), _summary(), 60.0)
    assert v["full_real_time"] is False and v["robust_real_time"] is True
    assert v["robust_recovers"] is True and v["contaminated"] == {}
    v = m.verdict(_summary(fps=45.0), _summary(fps=45.0), 60.0)
    assert v["robust_recovers"] is False
    # FULL already holds real time: there is nothing for ROBUST to recover.
    v = m.verdict(_summary(), _summary(), 60.0)
    assert v["robust_recovers"] is None
    v = m.verdict(_summary(fps=45.0), _summary(fps=None), 60.0)
    assert v["robust_recovers"] is None


def t_verdict_names_the_disturbed_tiers():
    v = m.verdict(_summary(contaminated=["OBS reconnected the feed"]), _summary(), 60.0)
    assert v["full_real_time"] is None
    assert v["contaminated"] == {"full": ["OBS reconnected the feed"]}


# --------------------------------------------------------------------------
# the gate before anything touches OBS or a feed
# --------------------------------------------------------------------------
def _status(feed="A", state="serving", platform="youtube", backlog=3.0, prebuffer=3.0):
    feeds = {"A": {"state": "serving", "platform": "youtube", "profile": "full",
                   "pinned": False, "backlog_s": 3.0, "state_age_s": 100.0},
             "B": {"state": "stopped", "platform": "youtube", "profile": "full",
                   "pinned": False, "backlog_s": None, "state_age_s": 100.0}}
    if feed:
        feeds[feed].update(state=state, platform=platform, backlog_s=backlog)
    out = {"live": {"feed": feed, "stint": 1, "mode": "race"}, "feeds": feeds}
    if prebuffer is not None:
        out["feed_prebuffer_s"] = prebuffer
    return out


def t_refusal_passes_a_serving_on_air_feed():
    assert m.refusal(_status(), stream_active=False, record_active=False) is None


def t_refusal_never_touches_a_live_broadcast():
    why = m.refusal(_status(), stream_active=True, record_active=False)
    assert why and "streaming" in why


def t_refusal_leaves_someone_elses_recording_alone():
    why = m.refusal(_status(), stream_active=False, record_active=True)
    assert why and "recording" in why


def t_refusal_without_an_on_air_feed():
    why = m.refusal(_status(feed=None), stream_active=False, record_active=False)
    assert why and "on-air feed" in why


def t_refusal_when_the_feed_is_not_serving():
    why = m.refusal(_status(state="connecting"), stream_active=False, record_active=False)
    assert why and "not serving" in why


def t_refusal_for_a_source_without_quality_tiers():
    why = m.refusal(_status(platform="local"), stream_active=False, record_active=False)
    assert why and "quality tiers" in why


def t_refusal_without_fan_out_measures_nothing():
    st = _status()
    del st["feeds"]["A"]["backlog_s"]            # direct-serve relay: no backlog field
    why = m.refusal(st, stream_active=False, record_active=False)
    assert why and "fan-out" in why


def t_refusal_when_obs_state_is_unknown():
    why = m.refusal(_status(), stream_active=None, record_active=False)
    assert why and "OBS" in why


def t_serving_since_switch_ignores_the_serve_from_before_the_switch():
    f = {"state": "serving", "state_age_s": 30.0}
    assert m.serving_since_switch(f, elapsed=5.0) is False     # the old serve, not yet killed
    f = {"state": "serving", "state_age_s": 2.0}
    assert m.serving_since_switch(f, elapsed=5.0) is True
    f = {"state": "connecting", "state_age_s": 1.0}
    assert m.serving_since_switch(f, elapsed=5.0) is False


def t_restore_tier_puts_a_pin_back_and_releases_an_unpinned_feed():
    assert m.restore_tier({"profile": "robust", "pinned": True}) == "robust"
    assert m.restore_tier({"profile": "full", "pinned": False}) == "auto"
    assert m.restore_tier({"profile": "robust", "pinned": False}) == "auto"


# --------------------------------------------------------------------------
# persisted result + preflight line
# --------------------------------------------------------------------------
def _record(ts=NOW, full_rt=True, robust_rt=True, contaminated=()):
    full = {**_summary(fps=60.0 if full_rt else 45.0, contaminated=contaminated),
            "render_ms_avg": 5.0, "backlog_floor_end_s": 3.1}
    robust = {**_summary(fps=60.0 if robust_rt else 45.0), "render_ms_avg": 4.0,
              "backlog_floor_end_s": 3.0}
    return {"ts": ts, "feed": "A", "platform": "youtube", "scene": "Stint",
            "fps_target": 60.0, "window_s": 60, "full": full, "robust": robust,
            "robust_extra_segments": 2,
            "verdict": m.verdict(full, robust, 60.0)}


def t_history_round_trip_keeps_the_newest_last():
    with tempfile.TemporaryDirectory() as d:
        assert m.load_latest(d) is None
        for i in range(m.HISTORY_LIMIT + 3):
            m.append_record(_record(ts=NOW + i), d)
        assert m.load_latest(d)["ts"] == NOW + m.HISTORY_LIMIT + 2
        with open(m.history_path(d), encoding="utf-8") as fh:
            assert len(fh.read().splitlines()) == m.HISTORY_LIMIT


def t_history_skips_a_corrupt_line():
    with tempfile.TemporaryDirectory() as d:
        m.append_record(_record(ts=NOW), d)
        with open(m.history_path(d), "a", encoding="utf-8") as fh:
            fh.write("{not json\n")
        assert m.load_latest(d)["ts"] == NOW


def t_classify_not_measured_is_info():
    r = m.classify(None, NOW)
    assert r.level == m.INFO and "racecast obs benchmark" in r.detail


def t_classify_fresh_real_time_passes_and_names_the_age():
    r = m.classify(_record(ts=NOW - 3 * 86_400), NOW)
    assert r.level == m.PASS
    assert "3 d ago" in r.detail and "FULL keeps real time" in r.detail


def t_classify_stale_warns():
    r = m.classify(_record(ts=NOW - (m.DEFAULT_MAX_AGE_DAYS + 1) * 86_400), NOW)
    assert r.level == m.WARN and "stale" in r.detail


def t_classify_full_behind_real_time_warns_and_says_whether_robust_helps():
    r = m.classify(_record(full_rt=False, robust_rt=True), NOW)
    assert r.level == m.WARN and "ROBUST recovers" in r.detail
    r = m.classify(_record(full_rt=False, robust_rt=False), NOW)
    assert r.level == m.WARN and "ROBUST does not recover" in r.detail


def t_classify_respects_a_custom_max_age():
    r = m.classify(_record(ts=NOW - 3 * 86_400), NOW, max_age_days=2)
    assert r.level == m.WARN and "stale" in r.detail


def t_classify_a_disturbed_run_warns_and_asks_for_a_rerun():
    r = m.classify(_record(contaminated=["the ring lapped OBS 2 time(s)"]), NOW)
    assert r.level == m.WARN
    assert "disturbed" in r.detail and "ring lapped OBS" in r.detail


def t_classify_reads_a_record_from_the_first_version():
    # #613 records have no "contaminated" key in their verdict
    old = _record()
    del old["verdict"]["contaminated"]
    assert m.classify(old, NOW).level == m.PASS


def t_render_names_both_tiers_the_cursor_and_the_upstream_cost():
    text = m.render(_record(full_rt=False), NOW)
    assert "FULL" in text and "ROBUST" in text
    assert "playback" in text
    assert "+2 HLS segments" in text


# --------------------------------------------------------------------------
# driver: fakes for the clock, the relay and the OBS session
# --------------------------------------------------------------------------
class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class _Relay:
    """/status + POST /feed/<X>/quality + POST /obs/feed-reset. A tier switch starts a
    fresh serve that is 'connecting' for `reconnect_s`, then 'serving' from that moment
    on. `snaps(tier, age)` is the cumulative consumer-snap count the relay reports."""
    def __init__(self, clock, status=None, reconnect_s=4.0, backlog=None, fail_on=None,
                 snaps=None):
        self.clock, self.reconnect_s, self.fail_on = clock, reconnect_s, fail_on
        self.st = status or _status()
        self.backlog = backlog or (lambda tier, t: 3.0)
        self.snaps = snaps or (lambda tier, t: 0)
        self.calls, self.resets, self.switched_at, self.tier = [], [], None, "full"
        self.switches = []
        self.session = None                      # the OBS fake, rejoined by a reset

    def status(self):
        f = self.st["feeds"][self.st["live"]["feed"] or "A"]
        f.setdefault("port", 53001)
        f.setdefault("consumer_snaps", 0)
        if self.switched_at is not None:
            age = self.clock() - self.switched_at
            if age < self.reconnect_s:
                f.update(state="connecting", state_age_s=age, backlog_s=None)
            else:
                f.update(state="serving", state_age_s=age - self.reconnect_s,
                         backlog_s=self.backlog(self.tier, age),
                         consumer_snaps=self.snaps(self.tier, age))
        return self.st

    def set_quality(self, feed, tier):
        self.calls.append((feed, tier))
        if self.fail_on == tier:
            raise RuntimeError("relay gone")
        self.tier, self.switched_at = tier, self.clock()
        self.switches.append(self.switched_at)
        return {"feed": feed, "profile": tier, "pinned": tier != "auto"}

    def feed_reset(self, feed):
        self.resets.append((self.clock(), self.tier))
        if self.session is not None:
            self.session.rejoin()
        return {"ok": True, "feed": feed}


class _Session:
    """obs-websocket stand-in. The "Feed A" media input on port 53001 plays at
    `rate(tier)` of real time; a rejoin (feed reset) starts its cursor again at 0."""
    def __init__(self, clock, stream=False, recording=False, scene="Standby",
                 fps=lambda tier: 60.0, relay=None, fail=None, rate=lambda tier: 1.0,
                 inputs=None):
        self.clock, self.stream, self.recording, self.scene = clock, stream, recording, scene
        self.fps, self.relay, self.fail, self.sent = fps, relay, fail, []
        self.rate, self.rec_started, self.finalize_polls = rate, None, 0
        self.inputs = {"Feed A": "http://127.0.0.1:53001"} if inputs is None else inputs
        self.cursor_ms, self.cursor_ts = 0.0, clock()
        if relay is not None:
            relay.session = self

    def _tier(self):
        return self.relay.tier if self.relay else "full"

    def _advance(self):
        now = self.clock()
        self.cursor_ms += (now - self.cursor_ts) * 1000 * self.rate(self._tier())
        self.cursor_ts = now

    def rejoin(self):
        self._advance()
        self.cursor_ms = 0.0

    def request(self, kind, data=None):
        self.sent.append((kind, data or {}))
        if kind == self.fail:
            raise RuntimeError("obs gone")
        if kind == "GetStreamStatus":
            return {"outputActive": self.stream}
        if kind == "GetRecordStatus":
            dur = 0 if self.rec_started is None else int((self.clock() - self.rec_started) * 1000)
            if self.recording == "finalizing":
                if self.finalize_polls > 0:
                    self.finalize_polls -= 1
                    return {"outputActive": True, "outputDuration": 0}
                self.recording = False
            return {"outputActive": self.recording, "outputDuration": dur}
        if kind == "GetVideoSettings":
            return {"fpsNumerator": 60, "fpsDenominator": 1}
        if kind == "GetCurrentProgramScene":
            return {"currentProgramSceneName": self.scene}
        if kind == "SetCurrentProgramScene":
            self.scene = data["sceneName"]
        if kind == "StartRecord":
            self.recording, self.rec_started = True, self.clock()
        if kind == "StopRecord":
            # OBS answers StopRecord before the file is finalized (seen on 32.2.2)
            self.recording = "finalizing" if self.finalize_polls else False
            return {"outputPath": "/rec/benchmark.mkv"}
        if kind == "GetInputList":
            return {"inputs": [{"inputName": n} for n in self.inputs]}
        if kind == "GetInputSettings":
            return {"inputSettings": {"input": self.inputs[data["inputName"]]}}
        if kind == "GetMediaInputStatus":
            self._advance()
            return {"mediaCursor": int(self.cursor_ms), "mediaState": "OBS_MEDIA_STATE_PLAYING"}
        if kind == "GetStats":
            return {"activeFps": self.fps(self._tier()), "averageFrameRenderTime": 5.0,
                    "renderSkippedFrames": 0, "renderTotalFrames": int(self.clock() * 60),
                    "outputSkippedFrames": 0, "outputTotalFrames": int(self.clock() * 60)}
        return {}


FLAGS = {"youtube": (FULL_FLAGS, ROBUST_FLAGS)}


def _run(d, clock, relay, sess, **kw):
    removed = []
    kw.setdefault("getmtime", lambda p: NOW)
    rec = m.run(relay, sess, d, flags=FLAGS, window_s=20, settle_s=5,
                clock=clock, sleep=clock.sleep, now=lambda: NOW,
                remove=removed.append, isfile=lambda p: True, **kw)
    return rec, removed


def t_run_measures_both_tiers_and_restores_everything():
    clock = _Clock()
    relay = _Relay(clock)
    sess = _Session(clock, relay=relay, fps=lambda tier: 45.0 if tier == "full" else 60.0)
    with tempfile.TemporaryDirectory() as d:
        rec, removed = _run(d, clock, relay, sess)
        assert m.load_latest(d)["ts"] == NOW
    assert relay.calls == [("A", "full"), ("A", "robust"), ("A", "auto")]
    kinds = [k for k, _ in sess.sent]
    assert kinds.index("StartRecord") < kinds.index("StopRecord")
    assert sess.recording is False
    assert sess.scene == "Standby"                         # the original scene is back
    assert ("SetCurrentProgramScene", {"sceneName": "Stint"}) in sess.sent
    assert removed == ["/rec/benchmark.mkv"]               # our own recording, not kept
    assert rec["full"]["fps_avg"] == 45.0 and rec["robust"]["fps_avg"] == 60.0
    assert rec["full"]["playback_rate"] == 1.0 and rec["full"]["contaminated"] == []
    assert rec["full"]["reconnect_s"] == 4.0
    assert rec["verdict"] == {"full_real_time": False, "robust_real_time": True,
                              "robust_recovers": True, "contaminated": {}}
    assert rec["robust_extra_segments"] == 2
    assert rec["fps_target"] == 60.0
    assert "recording" not in rec


def t_run_rejoins_obs_after_each_switch_once_the_prefetch_has_landed():
    # #614: a streamlink restart splices its HLS prefetch into OBS's open socket; OBS
    # would play it and sit that far behind the live edge. The benchmark rejoins OBS
    # after the prefetch has arrived, for each tier and again after restoring.
    clock = _Clock()
    relay = _Relay(clock)
    sess = _Session(clock, relay=relay)
    with tempfile.TemporaryDirectory() as d:
        _run(d, clock, relay, sess)
    assert [tier for _, tier in relay.resets] == ["full", "robust", "auto"]
    waits = [m.prefetch_land_s(4), m.prefetch_land_s(6), m.prefetch_land_s(4)]
    for (at, _tier), switch, wait in zip(relay.resets, relay.switches, waits, strict=True):
        # reconnect (4 s) + this tier's prefetch landing: never before it has delivered
        assert at >= switch + 4.0 + wait, (at, switch, wait)
    # ROBUST prefetches two more segments, so its rejoin must wait strictly longer than
    # FULL's. A single constant for both was measured ~3 s short for ROBUST (#614).
    assert waits[1] > waits[0]


def t_sample_and_summary_carry_the_inbound_gap():
    # #619 step 2: through one scripted restart the benchmark has to record the relay
    # backlog, OBS mediaCursor, ring snaps AND the inbound gaps. The first three were
    # already there. Without the fourth a slow window has no stated cause, because a
    # bursty source and a consumer that fell behind look the same in backlog alone.
    st = _status()
    st["feeds"]["A"]["inbound_max_gap_s"] = 2.5
    sess = _Session(_Clock())
    smp = m._obs_sample(sess, 1.0, st, "A", "Feed A")
    assert smp["inbound_max_gap_s"] == 2.5, smp     # named exactly as /status names it
    # A relay that does not publish the field (an older one) reports None, never 0.0.
    # "No reading" and "no gap" are different answers, and 0.0 would read as healthy.
    st2 = _status()
    st2["feeds"]["A"].pop("inbound_max_gap_s", None)
    assert m._obs_sample(sess, 1.0, st2, "A", "Feed A")["inbound_max_gap_s"] is None
    # It has to reach the operator's eyes, not just the JSONL.
    rec = {"feed": "A", "platform": "youtube", "scene": "Stint", "window_s": 60,
           "fps_target": 60.0, "ts": NOW,
           "full": {"inbound_gap_worst_s": 2.5}, "robust": {"inbound_gap_worst_s": None}}
    text = m.render(rec, NOW)
    assert "src gap" in text, text
    assert "2.5 s" in text, text
    assert "n/a" in text.splitlines()[3], "a tier without a reading prints n/a, not 0.0"


def t_inbound_gap_worst_ignores_the_reading_the_window_inherited():
    # /status carries the heartbeat's last reading and the heartbeat runs every 30 s,
    # while the benchmark samples every 2 s. The value present when a window opens
    # describes an interval that began BEFORE the restart, so counting it would blame the
    # old serve's jitter on the new one.
    def w(*vals):
        return [{"t": float(i), "inbound_max_gap_s": v} for i, v in enumerate(vals)]
    # 9.0 is inherited from before the restart; only 0.4 and 1.2 were produced in-window.
    assert m._inbound_gap_worst(w(9.0, 9.0, 9.0, 0.4, 0.4, 1.2, 1.2)) == 1.2
    # A later repeat of a value is real data, because two intervals may share a max.
    assert m._inbound_gap_worst(w(9.0, 0.4, 1.2, 1.2)) == 1.2
    assert m._inbound_gap_worst(w(0.4, 9.0, 0.4)) == 9.0   # a spike after the first change
    # A window opens ON the restart, and the #614 rejoin rebuilds the OBS source, so the
    # relay has no reading to give for the first samples. Anchoring the inherited value
    # on samples[0] made those Nones the anchor, and the first real number after them
    # started the count: the pre-restart reading, exactly what this guard excludes.
    # Measured against the shipped helper before the fix: 9.0 instead of 1.2.
    assert m._inbound_gap_worst(
        w(None, None, None, 9.0, 9.0, 9.0, 0.4, 0.4, 1.2)) == 1.2, \
        "a leading None run must not make 9.0 look like an in-window reading"
    # Nothing but the inherited reading after the Nones: still nothing to say.
    assert m._inbound_gap_worst(w(None, None, 9.0, 9.0)) is None
    # The reading never changed: the window was shorter than a heartbeat, so nothing can
    # be said, and reporting the inherited 9.0 would measure the old serve instead.
    assert m._inbound_gap_worst(w(9.0, 9.0, 9.0)) is None
    assert m._inbound_gap_worst(w()) is None
    assert m._inbound_gap_worst(w(None, None)) is None
    # An older relay publishes nothing at all: None throughout, never 0.0.
    assert m._inbound_gap_worst(w(None, None, None)) is None
    # And it is wired into the summary, not just defined.
    assert m.summarize(w(9.0, 9.0, 0.4, 1.2))["inbound_gap_worst_s"] == 1.2


def t_prefetch_wait_rule_has_not_drifted_from_the_relay():
    # prefetch_land_s and its budget exist twice (relay + benchmark) so the benchmark
    # stays importable on its own. A "keep in sync" comment is not a guard — the repo
    # pins duplicated logic with a source comparison (tests/test_streams.py), and the
    # two tools waiting different spans is exactly the divergence #614 closed.
    import importlib.util, inspect
    spec = importlib.util.spec_from_file_location(
        "feeds_x", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
    feeds_x = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(feeds_x)
    assert m.SEGMENT_FETCH_BUDGET_S == feeds_x.SEGMENT_FETCH_BUDGET_S
    assert m.DEFAULT_PREBUFFER_S == feeds_x.health_store.DEFAULT_FEED_PREBUFFER_S
    # Same rule, checked by behaviour rather than by text: the benchmark's copy carries
    # a default the relay's does not, so the sources legitimately differ.
    for segments in (0, 1, 2, 4, 6):
        for prebuffer in (0.0, 3.0, 8.0):
            assert m.prefetch_land_s(segments, prebuffer) == \
                feeds_x.prefetch_land_s(segments, prebuffer), (segments, prebuffer)
    assert m.prefetch_land_s(None, 3.0) == feeds_x.prefetch_land_s(0, 3.0)
    # And the burst size is read the same way out of a flag list.
    for flags in (FULL_FLAGS, ROBUST_FLAGS, [], ["--hls-live-edge"], ["--hls-live-edge", "x"]):
        assert (m.live_edge_segments(flags) or 0) == feeds_x.live_edge_segments(flags), flags
    assert inspect.signature(feeds_x.prefetch_land_s).parameters["prebuffer_s"].default \
        is inspect.Parameter.empty, "the relay's copy must keep prebuffer_s required"


def t_run_honours_a_relay_that_reports_a_zero_prebuffer():
    # RACECAST_FEED_PREBUFFER_S=0 is documented ("=0 restores the live-edge serve") and
    # 0.0 is falsy, so a `status.get(...) or DEFAULT` waits 3 s too long against exactly
    # the relay that turned the reserve off. An absent field still falls back.
    for prebuffer, expected in ((0.0, 0.0), (3.0, 3.0), (None, m.DEFAULT_PREBUFFER_S)):
        clock = _Clock()
        relay = _Relay(clock, status=_status(prebuffer=prebuffer))
        sess = _Session(clock, relay=relay)
        with tempfile.TemporaryDirectory() as d:
            _run(d, clock, relay, sess)
        waits = [m.prefetch_land_s(4, expected), m.prefetch_land_s(6, expected),
                 m.prefetch_land_s(4, expected)]
        for (at, _tier), switch, wait in zip(relay.resets, relay.switches, waits, strict=True):
            assert at == switch + 4.0 + wait, (prebuffer, at, switch, wait)


def t_flags_for_tier_mirrors_the_relays_profile_rule():
    # The restore path hands back the feed's ORIGINAL tier, which is usually "auto".
    # Resolving that to no flags would silently skip the wait on the last rejoin.
    tf = {"full": FULL_FLAGS, "robust": ROBUST_FLAGS}
    assert m.flags_for_tier(tf, "full") == FULL_FLAGS
    assert m.flags_for_tier(tf, "auto") == FULL_FLAGS
    assert m.flags_for_tier(tf, None) == FULL_FLAGS
    assert m.flags_for_tier(tf, "robust") == ROBUST_FLAGS
    assert m.flags_for_tier(tf, "emergency") == ROBUST_FLAGS
    assert m.flags_for_tier({}, "full") == ()


def t_prefetch_land_s_scales_with_the_burst_and_the_prebuffer():
    # Mirrors the relay's rule so the two tools cannot drift into different waits.
    assert m.prefetch_land_s(4, 3.0) == 4 * m.SEGMENT_FETCH_BUDGET_S + 3.0
    assert m.prefetch_land_s(6, 3.0) == 6 * m.SEGMENT_FETCH_BUDGET_S + 3.0
    assert m.prefetch_land_s(6, 3.0) > m.prefetch_land_s(4, 3.0)
    assert m.prefetch_land_s(4, 8.0) - m.prefetch_land_s(4, 3.0) == 5.0
    assert m.prefetch_land_s(4) == m.prefetch_land_s(4, m.DEFAULT_PREBUFFER_S)
    assert m.prefetch_land_s(0, 3.0) == 0.0
    assert m.prefetch_land_s(None, 3.0) == 0.0       # live_edge_segments found no flag
    # Every measured worst case must fit the budget it was rounded up from (2026-09-20):
    # YouTube FULL 1.82/4, YouTube ROBUST 4.96/6, Twitch FULL 0.69/2, Twitch ROBUST 1.80/2.
    for burst, segments in ((1.82, 4), (4.96, 6), (0.69, 2), (1.80, 2)):
        assert burst / segments <= m.SEGMENT_FETCH_BUDGET_S, (burst, segments)


def t_run_samples_only_after_the_reconnect_the_rejoin_and_the_settle():
    clock = _Clock()
    # the backlog reads as the serve's age, so the first sample shows when sampling began
    relay = _Relay(clock, backlog=lambda tier, age: round(age, 1))
    sess = _Session(clock, relay=relay)
    with tempfile.TemporaryDirectory() as d:
        rec, _ = _run(d, clock, relay, sess)
    for tier in m.TIERS:
        # reconnect (4 s) + this tier's prefetch landing + settle (5 s)
        wait = m.prefetch_land_s(4 if tier == "full" else 6)
        assert rec[tier]["backlog_floor_start_s"] == 4.0 + wait + 5.0, rec[tier]
        assert rec[tier]["samples"] >= 10 and rec[tier]["duration_s"] >= 20.0


def t_run_accepts_a_serve_that_came_up_before_the_switch_reply():
    # The relay can restart the serve before its reply to the tier switch arrives;
    # that serve is already older than "now" when the client reads its clock.
    class _SlowReplyRelay(_Relay):
        def set_quality(self, feed, tier):
            reply = super().set_quality(feed, tier)
            self.clock.t += 0.5                     # the reply takes 0.5 s
            return reply

    clock = _Clock()
    relay = _SlowReplyRelay(clock, reconnect_s=0.0)
    sess = _Session(clock, relay=relay)
    with tempfile.TemporaryDirectory() as d:
        rec, _ = _run(d, clock, relay, sess, serving_timeout_s=30)
    assert rec["full"]["reconnect_s"] == 0.5 and rec["robust"]["reconnect_s"] == 0.5


def t_run_catches_a_frozen_demuxer_that_fps_and_encoder_miss():
    clock = _Clock()
    relay = _Relay(clock)
    # FULL: OBS renders 60 fps and records in real time, but the feed's cursor stands still
    sess = _Session(clock, relay=relay, rate=lambda tier: 0.0 if tier == "full" else 1.0)
    with tempfile.TemporaryDirectory() as d:
        rec, _ = _run(d, clock, relay, sess)
    assert rec["full"]["fps_avg"] == 60.0 and rec["full"]["encoder_speed"] == 1.0
    assert rec["full"]["playback_rate"] == 0.0 and rec["full"]["stall_fraction"] == 1.0
    assert rec["verdict"]["full_real_time"] is False
    assert rec["verdict"]["robust_real_time"] is True


def t_run_marks_a_window_the_ring_lapped_as_disturbed():
    clock = _Clock()
    relay = _Relay(clock, snaps=lambda tier, age: int(age // 10) if tier == "full" else 0)
    sess = _Session(clock, relay=relay)
    with tempfile.TemporaryDirectory() as d:
        rec, _ = _run(d, clock, relay, sess)
    assert rec["full"]["contaminated"] and "ring lapped OBS" in rec["full"]["contaminated"][0]
    assert rec["verdict"]["full_real_time"] is None
    assert rec["robust"]["contaminated"] == []


def t_run_refuses_when_obs_has_no_input_on_the_feed_port():
    clock = _Clock()
    relay = _Relay(clock)
    sess = _Session(clock, relay=relay, inputs={"Something": "http://127.0.0.1:9999"})
    with tempfile.TemporaryDirectory() as d:
        try:
            _run(d, clock, relay, sess)
        except m.BenchmarkRefused as exc:
            assert "53001" in str(exc)
        else:
            raise AssertionError("expected BenchmarkRefused")
    assert relay.calls == []
    assert [k for k, _ in sess.sent if k.startswith(("Set", "Start", "Stop"))] == []


def t_run_deletes_the_recording_only_after_obs_has_finished_it():
    clock = _Clock()
    relay = _Relay(clock)
    sess = _Session(clock, relay=relay)
    sess.finalize_polls = 3
    removed = []

    def remove(p):
        removed.append((p, sess.recording))

    with tempfile.TemporaryDirectory() as d:
        m.run(relay, sess, d, flags=FLAGS, window_s=20, settle_s=5, clock=clock,
              sleep=clock.sleep, now=lambda: NOW, remove=remove, isfile=lambda p: True,
              getmtime=lambda p: NOW)
    assert removed == [("/rec/benchmark.mkv", False)]


def t_run_leaves_a_recording_obs_does_not_finish():
    clock = _Clock()
    relay = _Relay(clock)
    sess = _Session(clock, relay=relay)
    sess.finalize_polls = 10_000
    removed, said = [], []
    with tempfile.TemporaryDirectory() as d:
        rec = m.run(relay, sess, d, flags=FLAGS, window_s=20, settle_s=5, clock=clock,
                    sleep=clock.sleep, now=lambda: NOW, remove=removed.append,
                    isfile=lambda p: True, progress=said.append)
    assert removed == []
    assert any("/rec/benchmark.mkv" in s and "still" in s for s in said), said
    assert rec["recording"] == "/rec/benchmark.mkv"     # the report names the file


def t_run_never_deletes_a_file_older_than_its_own_recording():
    # OBS names the path; with a remote OBS it may name an unrelated local file.
    clock = _Clock()
    relay = _Relay(clock)
    sess = _Session(clock, relay=relay)
    said = []
    with tempfile.TemporaryDirectory() as d:
        rec, removed = _run(d, clock, relay, sess, getmtime=lambda p: NOW - 3600,
                            progress=said.append)
    assert removed == []
    assert rec["recording"] == "/rec/benchmark.mkv"
    assert any("not created by this benchmark" in s for s in said), said


def t_run_keeps_a_pin_and_the_recording_on_request():
    clock = _Clock()
    st = _status()
    st["feeds"]["A"].update(profile="robust", pinned=True)
    relay = _Relay(clock, status=st)
    sess = _Session(clock, relay=relay, scene="Stint")
    with tempfile.TemporaryDirectory() as d:
        rec, removed = _run(d, clock, relay, sess, keep_recording=True)
    assert relay.calls[-1] == ("A", "robust")
    assert removed == [] and rec["recording"] == "/rec/benchmark.mkv"
    # already on the target scene: no switch there and none back
    assert not [k for k, _ in sess.sent if k == "SetCurrentProgramScene"]


def t_run_refuses_before_touching_anything():
    clock = _Clock()
    relay = _Relay(clock)
    sess = _Session(clock, stream=True, relay=relay)
    with tempfile.TemporaryDirectory() as d:
        try:
            _run(d, clock, relay, sess)
        except m.BenchmarkRefused as exc:
            assert "streaming" in str(exc)
        else:
            raise AssertionError("expected BenchmarkRefused")
        assert m.load_latest(d) is None
    assert relay.calls == []
    assert [k for k, _ in sess.sent if k.startswith(("Set", "Start", "Stop"))] == []


def t_run_restores_after_a_failure_mid_measurement():
    clock = _Clock()
    relay = _Relay(clock, reconnect_s=10_000)              # never serves again
    sess = _Session(clock, relay=relay)
    with tempfile.TemporaryDirectory() as d:
        try:
            _run(d, clock, relay, sess, serving_timeout_s=30)
        except m.BenchmarkFailed as exc:
            assert "did not serve again" in str(exc)
        else:
            raise AssertionError("expected BenchmarkFailed")
        assert m.load_latest(d) is None                    # no partial record
    assert sess.recording is False and sess.scene == "Standby"
    assert relay.calls[-1] == ("A", "auto")


def t_run_restores_on_an_interrupt():
    clock = _Clock()
    relay = _Relay(clock)
    sess = _Session(clock, relay=relay)

    def boom(_s):
        raise KeyboardInterrupt

    with tempfile.TemporaryDirectory() as d:
        try:
            m.run(relay, sess, d, flags=FLAGS, clock=clock, sleep=boom,
                  now=lambda: NOW, remove=lambda p: None, isfile=lambda p: True)
        except KeyboardInterrupt:
            pass  # the interrupt must reach the caller after the restore
        else:
            raise AssertionError("expected KeyboardInterrupt")
    assert sess.recording is False and sess.scene == "Standby"
    assert relay.calls[-1] == ("A", "auto")


def t_run_does_not_hold_an_interrupted_run_for_the_rejoin():
    # Ctrl-C once: the cleanup restores everything but does not wait up to 95 s for the
    # restored serve to rejoin OBS; it tells the operator to press RESET instead.
    clock = _Clock()
    relay = _Relay(clock)
    sess = _Session(clock, relay=relay)
    fired, said = [], []

    def sleep(s):
        if not fired:
            fired.append(True)
            raise KeyboardInterrupt
        clock.sleep(s)

    with tempfile.TemporaryDirectory() as d:
        try:
            m.run(relay, sess, d, flags=FLAGS, clock=clock, sleep=sleep, now=lambda: NOW,
                  remove=lambda p: None, isfile=lambda p: True, progress=said.append)
        except KeyboardInterrupt:
            pass  # the interrupt must reach the caller after the restore
    assert relay.calls[-1] == ("A", "auto")
    assert relay.resets == []                              # no rejoin wait after Ctrl-C
    assert any("RESET" in s for s in said), said


def t_run_says_when_it_releases_an_automatic_step_down():
    clock = _Clock()
    st = _status()
    st["feeds"]["A"].update(profile="robust", pinned=False)   # stepped down by the relay
    relay = _Relay(clock, status=st)
    sess = _Session(clock, relay=relay)
    said = []
    with tempfile.TemporaryDirectory() as d:
        _run(d, clock, relay, sess, progress=said.append)
    assert relay.calls[-1] == ("A", "auto")
    assert any("automatic step-down" in s for s in said), said


def t_run_lets_ctrl_c_skip_the_final_rejoin_but_not_other_exits():
    # Ctrl-C during the final rejoin wait skips the rejoin and finishes the run; a
    # SystemExit there is not swallowed (CodeQL py/catch-base-exception).
    class _Relay2(_Relay):
        def __init__(self, clock, exc):
            super().__init__(clock)
            self.exc = exc

        def feed_reset(self, feed):
            if self.tier == "auto":
                raise self.exc
            return super().feed_reset(feed)

    clock = _Clock()
    relay = _Relay2(clock, KeyboardInterrupt())
    sess = _Session(clock, relay=relay)
    said = []
    with tempfile.TemporaryDirectory() as d:
        rec, _ = _run(d, clock, relay, sess, progress=said.append)
        assert m.load_latest(d)["ts"] == NOW
    assert any("RESET" in s for s in said), said
    assert rec["verdict"]["full_real_time"] is True

    clock = _Clock()
    relay = _Relay2(clock, SystemExit(3))
    sess = _Session(clock, relay=relay)
    with tempfile.TemporaryDirectory() as d:
        try:
            _run(d, clock, relay, sess)
        except SystemExit as exc:
            assert exc.code == 3
        else:
            raise AssertionError("SystemExit was swallowed")


def t_run_reports_a_failed_restore_step_and_still_does_the_rest():
    clock = _Clock()
    relay = _Relay(clock, fail_on="auto")
    sess = _Session(clock, relay=relay)
    said = []
    with tempfile.TemporaryDirectory() as d:
        _run(d, clock, relay, sess, progress=said.append)
    assert any("restoring Feed A" in s for s in said)
    assert sess.scene == "Standby" and sess.recording is False


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

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


def _sample(t, fps=60.0, render_ms=4.0, rs=0, rt=0, os_=0, ot=0, rec_ms=0, backlog=3.0):
    return {"t": t, "fps": fps, "render_ms": render_ms, "render_skipped": rs,
            "render_total": rt, "output_skipped": os_, "output_total": ot,
            "rec_ms": rec_ms, "backlog_s": backlog}


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
def t_summarize_derives_rates_over_the_window():
    samples = [
        _sample(0.0, fps=60.0, render_ms=4.0, rs=100, rt=1000, os_=10, ot=1000,
                rec_ms=0, backlog=3.0),
        _sample(30.0, fps=50.0, render_ms=8.0, rs=250, rt=2800, os_=10, ot=2800,
                rec_ms=30_000, backlog=4.0),
        _sample(60.0, fps=40.0, render_ms=12.0, rs=400, rt=4600, os_=46, ot=4600,
                rec_ms=57_000, backlog=5.0),
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
    assert s["backlog_start_s"] == 3.0 and s["backlog_end_s"] == 5.0
    assert s["backlog_max_s"] == 5.0
    # +2 s over 60 s, least squares -> 2 s per minute
    assert s["backlog_growth_s_per_min"] == 2.0


def t_summarize_ignores_missing_values_instead_of_inventing_them():
    samples = [_sample(0.0, fps=None, render_ms=None, backlog=None, rec_ms=None,
                       rs=None, rt=None, os_=None, ot=None),
               _sample(10.0, fps=None, render_ms=None, backlog=None, rec_ms=None,
                       rs=None, rt=None, os_=None, ot=None)]
    s = m.summarize(samples)
    assert s["samples"] == 2
    for key in ("render_ms_avg", "fps_avg", "fps_min", "render_skip_pct",
                "encoder_skip_pct", "encoder_speed", "backlog_start_s",
                "backlog_end_s", "backlog_max_s", "backlog_growth_s_per_min"):
        assert s[key] is None, key


def t_summarize_needs_two_backlog_points_for_a_growth_rate():
    s = m.summarize([_sample(0.0, backlog=3.0), _sample(10.0, backlog=None)])
    assert s["backlog_start_s"] == 3.0
    assert s["backlog_growth_s_per_min"] is None


def t_summarize_of_nothing_is_empty_not_a_crash():
    s = m.summarize([])
    assert s["samples"] == 0 and s["duration_s"] is None and s["fps_avg"] is None


# --------------------------------------------------------------------------
# real time: frame rate, encoder speed, and a backlog that does not grow
# --------------------------------------------------------------------------
def _summary(fps=60.0, speed=1.0, growth=0.0):
    return {"fps_avg": fps, "encoder_speed": speed, "backlog_growth_s_per_min": growth}


def t_keeps_real_time_on_a_healthy_window():
    assert m.keeps_real_time(_summary(), 60.0) is True


def t_keeps_real_time_fails_on_each_failing_part():
    assert m.keeps_real_time(_summary(fps=50.0), 60.0) is False
    assert m.keeps_real_time(_summary(speed=0.9), 60.0) is False
    assert m.keeps_real_time(_summary(growth=m.BACKLOG_GROWTH_WARN_S_PER_MIN + 0.5),
                             60.0) is False


def t_keeps_real_time_at_the_thresholds_holds():
    assert m.keeps_real_time(_summary(fps=60.0 * m.REAL_TIME_FPS_RATIO,
                                      speed=m.ENCODER_SPEED_MIN,
                                      growth=m.BACKLOG_GROWTH_WARN_S_PER_MIN), 60.0) is True


def t_keeps_real_time_without_a_frame_rate_is_unknown():
    assert m.keeps_real_time(_summary(fps=None), 60.0) is None
    assert m.keeps_real_time(_summary(), None) is None


def t_keeps_real_time_without_a_backlog_judges_the_rest():
    # No consumer attached (e.g. the feed was not on the program scene): the render
    # and encoder numbers still answer the question for OBS itself.
    assert m.keeps_real_time(_summary(growth=None), 60.0) is True


def t_verdict_answers_the_calibration_question():
    v = m.verdict(_summary(fps=45.0), _summary(), 60.0)
    assert v == {"full_real_time": False, "robust_real_time": True, "robust_recovers": True}
    v = m.verdict(_summary(fps=45.0), _summary(fps=45.0), 60.0)
    assert v["robust_recovers"] is False
    # FULL already holds real time: there is nothing for ROBUST to recover.
    v = m.verdict(_summary(), _summary(), 60.0)
    assert v["robust_recovers"] is None
    v = m.verdict(_summary(fps=45.0), _summary(fps=None), 60.0)
    assert v["robust_recovers"] is None


# --------------------------------------------------------------------------
# the gate before anything touches OBS or a feed
# --------------------------------------------------------------------------
def _status(feed="A", state="serving", platform="youtube", backlog=3.0):
    feeds = {"A": {"state": "serving", "platform": "youtube", "profile": "full",
                   "pinned": False, "backlog_s": 3.0, "state_age_s": 100.0},
             "B": {"state": "stopped", "platform": "youtube", "profile": "full",
                   "pinned": False, "backlog_s": None, "state_age_s": 100.0}}
    if feed:
        feeds[feed].update(state=state, platform=platform, backlog_s=backlog)
    return {"live": {"feed": feed, "stint": 1, "mode": "race"}, "feeds": feeds}


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
def _record(ts=NOW, full_rt=True, robust_rt=True):
    full = {**_summary(fps=60.0 if full_rt else 45.0), "render_ms_avg": 5.0,
            "backlog_end_s": 3.1}
    robust = {**_summary(fps=60.0 if robust_rt else 45.0), "render_ms_avg": 4.0,
              "backlog_end_s": 3.0}
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


def t_render_names_both_tiers_and_the_upstream_cost():
    text = m.render(_record(full_rt=False), NOW)
    assert "FULL" in text and "ROBUST" in text
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
    """/status + POST /feed/<X>/quality. A tier switch starts a fresh serve that is
    'connecting' for `reconnect_s`, then 'serving' from that moment on."""
    def __init__(self, clock, status=None, reconnect_s=4.0, backlog=None, fail_on=None):
        self.clock, self.reconnect_s, self.fail_on = clock, reconnect_s, fail_on
        self.st = status or _status()
        self.backlog = backlog or (lambda tier, t: 3.0)
        self.calls, self.switched_at, self.tier = [], None, "full"

    def status(self):
        f = self.st["feeds"][self.st["live"]["feed"] or "A"]
        if self.switched_at is not None:
            age = self.clock() - self.switched_at
            if age < self.reconnect_s:
                f.update(state="connecting", state_age_s=age, backlog_s=None)
            else:
                f.update(state="serving", state_age_s=age - self.reconnect_s,
                         backlog_s=self.backlog(self.tier, age))
        return self.st

    def set_quality(self, feed, tier):
        self.calls.append((feed, tier))
        if self.fail_on == tier:
            raise RuntimeError("relay gone")
        self.tier, self.switched_at = tier, self.clock()
        return {"feed": feed, "profile": tier, "pinned": tier != "auto"}


class _Session:
    def __init__(self, clock, stream=False, recording=False, scene="Standby",
                 fps=lambda tier: 60.0, relay=None, fail=None):
        self.clock, self.stream, self.recording, self.scene = clock, stream, recording, scene
        self.fps, self.relay, self.fail, self.sent = fps, relay, fail, []
        self.rec_started, self.finalize_polls, self.active_at_remove = None, 0, None

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
        if kind == "GetStats":
            tier = self.relay.tier if self.relay else "full"
            return {"activeFps": self.fps(tier), "averageFrameRenderTime": 5.0,
                    "renderSkippedFrames": 0, "renderTotalFrames": int(self.clock() * 60),
                    "outputSkippedFrames": 0, "outputTotalFrames": int(self.clock() * 60)}
        return {}


FLAGS = {"youtube": (FULL_FLAGS, ROBUST_FLAGS)}


def _run(d, clock, relay, sess, **kw):
    removed = []
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
    assert rec["full"]["reconnect_s"] == 4.0
    assert rec["verdict"] == {"full_real_time": False, "robust_real_time": True,
                              "robust_recovers": True}
    assert rec["robust_extra_segments"] == 2
    assert rec["fps_target"] == 60.0
    assert "recording" not in rec


def t_run_samples_only_after_the_reconnect_and_the_settle():
    clock = _Clock()
    # the backlog reads as the serve's age, so the first sample shows when sampling began
    relay = _Relay(clock, backlog=lambda tier, age: round(age, 1))
    sess = _Session(clock, relay=relay)
    with tempfile.TemporaryDirectory() as d:
        rec, _ = _run(d, clock, relay, sess)
    for tier in m.TIERS:
        # reconnect (4 s) + settle (5 s): nothing from the rejoin transient
        assert rec[tier]["backlog_start_s"] == 9.0, rec[tier]
        assert rec[tier]["samples"] >= 10 and rec[tier]["duration_s"] >= 20.0


def t_run_measures_the_backlog_growth_per_tier():
    clock = _Clock()
    relay = _Relay(clock, backlog=lambda tier, age: 3.0 + (age / 60.0 * 6 if tier == "full" else 0))
    sess = _Session(clock, relay=relay)
    with tempfile.TemporaryDirectory() as d:
        rec, _ = _run(d, clock, relay, sess)
    assert rec["full"]["backlog_growth_s_per_min"] == 6.0
    assert rec["robust"]["backlog_growth_s_per_min"] == 0.0
    assert rec["verdict"]["full_real_time"] is False


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
              sleep=clock.sleep, now=lambda: NOW, remove=remove, isfile=lambda p: True)
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

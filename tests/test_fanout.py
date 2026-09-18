#!/usr/bin/env python3
"""Stdlib unit checks for relay feed fan-out. Run: python3 tests/test_fanout.py"""
import importlib.util, os, socket, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "irofeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def t_fanout_enabled_truthy_tokens():
    for v in ("1", "true", "TRUE", "Yes", "on"):
        assert m.fanout_enabled({"RACECAST_FEED_FANOUT": v}) is True, v


def t_fanout_enabled_default_on():
    # Default ON (#358 live-verified 2026-06-29): absent or empty -> fan-out.
    assert m.fanout_enabled({}) is True
    assert m.fanout_enabled({"RACECAST_FEED_FANOUT": ""}) is True


def t_fanout_enabled_explicit_falsey_disables():
    # Only an explicit falsey token falls back to the direct-serve path.
    for v in ("0", "false", "FALSE", "no", "off"):
        assert m.fanout_enabled({"RACECAST_FEED_FANOUT": v}) is False, v


def t_feed_stalled_window():
    assert m.feed_stalled(100.0, 100.0 + m.FANOUT_STALL_S + 0.1) is True
    assert m.feed_stalled(100.0, 100.0 + m.FANOUT_STALL_S - 0.1) is False


def t_feed_stalled_none_is_not_stall():
    assert m.feed_stalled(None, 1_000_000.0) is False


def t_feed_stalled_honours_configured_grace():
    # The graduated grace: at 20 s default, an 8 s gap is NOT a stall (streamlink's
    # retry gets time); a 21 s gap is.
    g = m.feed_stall_s({})                       # 20.0
    assert m.feed_stalled(100.0, 100.0 + 8.0, stall_s=g) is False
    assert m.feed_stalled(100.0, 100.0 + g + 0.1, stall_s=g) is True


def t_ring_basic_read_after_write():
    r = m.FeedRing(1024)
    r.write(b"abc")
    data, cur = r.read(0, timeout=0.1)
    assert data == b"abc" and cur == 3


def t_ring_incremental_cursor():
    r = m.FeedRing(1024)
    r.write(b"abc")
    _, cur = r.read(0, timeout=0.1)
    r.write(b"de")
    data, cur2 = r.read(cur, timeout=0.1)
    assert data == b"de" and cur2 == 5


def t_ring_overflow_drops_oldest_and_snaps_slow_reader():
    r = m.FeedRing(4)                     # tiny window
    r.write(b"0123")                      # window = "0123", base=0, live=4
    r.write(b"4567")                      # window = "4567", base=4, live=8
    assert r.live_offset() == 8
    assert r.start_offset() == 4
    # a reader still at cursor 0 fell behind: it is snapped to start_offset (4)
    data, cur = r.read(0, timeout=0.1)
    assert cur == 8 and data == b"4567"   # got the retained window, not the lost "0123"


def t_ring_read_times_out_without_new_data():
    r = m.FeedRing(1024)
    r.write(b"abc")
    _, cur = r.read(0, timeout=0.1)
    t0 = time.monotonic()
    data, cur2 = r.read(cur, timeout=0.15)
    assert data == b"" and cur2 == cur
    assert time.monotonic() - t0 >= 0.1


def t_ring_writer_never_blocks_on_absent_reader():
    # Writing far more than capacity with NO reader must return immediately.
    r = m.FeedRing(1024)
    t0 = time.monotonic()
    for _ in range(1000):
        r.write(b"x" * 1024)
    assert time.monotonic() - t0 < 1.0    # never blocked
    assert r.live_offset() == 1000 * 1024


def _http_get_body(port, nbytes, deadline=2.0):
    s = socket.create_connection(("127.0.0.1", port), timeout=deadline)
    s.sendall(b"GET / HTTP/1.0\r\n\r\n")
    buf = b""
    s.settimeout(deadline)
    while True:
        # count only body bytes (after \r\n\r\n) so we don't exit early on the header
        sep = buf.find(b"\r\n\r\n")
        body_len = len(buf) - (sep + 4) if sep >= 0 else 0
        if body_len >= nbytes:
            break
        try:
            chunk = s.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
    s.close()
    # strip headers
    sep = buf.find(b"\r\n\r\n")
    return buf[sep + 4:] if sep >= 0 else buf


def t_fanout_server_streams_ring_to_two_consumers():
    ring = m.FeedRing(1 << 20)
    srv = m.FeedFanoutServer("127.0.0.1", 0, ring, m.logging.getLogger("t"))
    srv.start()
    try:
        bodies = {}

        def grab(idx):
            bodies[idx] = _http_get_body(srv.port, 10)
        t1 = threading.Thread(target=grab, args=(1,)); t1.start()
        t2 = threading.Thread(target=grab, args=(2,)); t2.start()
        time.sleep(0.2)                       # let both connect
        for i in range(10):
            ring.write(bytes([65 + i]))       # b"A".."J"
            time.sleep(0.01)
        t1.join(3); t2.join(3)
        assert bodies[1] == b"ABCDEFGHIJ"
        assert bodies[2] == b"ABCDEFGHIJ"
    finally:
        srv.stop()


def t_fanout_server_writer_unblocked_by_dead_consumer():
    # A consumer that connects then never reads must not stop the ring writer.
    ring = m.FeedRing(4096)
    srv = m.FeedFanoutServer("127.0.0.1", 0, ring, m.logging.getLogger("t"))
    srv.start()
    try:
        s = socket.create_connection(("127.0.0.1", srv.port), timeout=2.0)
        s.sendall(b"GET / HTTP/1.0\r\n\r\n")   # connect, then never recv
        t0 = time.monotonic()
        for _ in range(2000):
            ring.write(b"y" * 4096)            # 8 MB through a 4 KB ring
        assert time.monotonic() - t0 < 2.0     # writer never blocked
        s.close()
    finally:
        srv.stop()


def t_streamlink_fanout_cmd_youtube_has_stdout_ua_cookies():
    cmd = m.streamlink_fanout_cmd("https://hls.example/x.m3u8", "youtube",
                                  cookies="/tmp/yt.txt", user_agent="UA/9")
    assert cmd[0] == "streamlink" and "--stdout" in cmd
    assert "--player-external-http" not in cmd
    assert "--http-header" in cmd and "User-Agent=UA/9" in cmd
    assert "--http-cookies-file" in cmd and "/tmp/yt.txt" in cmd
    assert cmd[-2:] == ["https://hls.example/x.m3u8", "best"]


def t_streamlink_fanout_cmd_twitch_uses_plugin_no_ua():
    cmd = m.streamlink_fanout_cmd("https://twitch.tv/foo", "twitch",
                                  twitch_token="tok")
    assert "--stdout" in cmd and "--http-header" not in cmd
    assert "--twitch-api-header" in cmd
    assert cmd[-2:] == ["https://twitch.tv/foo", "best"]


def t_fanout_eof_is_drop_when_not_stopped_or_advancing():
    # streamlink EOF mid-serve in fan-out mode, not a stop/handover → a real DROP.
    # The fan-out path reads the same exit-classification predicate as direct-serve.
    assert m.serve_exit_is_drop(False, False) is True
    assert m.serve_exit_is_drop(True, False) is False   # stop → not a drop
    assert m.serve_exit_is_drop(False, True) is False   # advance/handover → not a drop


def t_fanout_fast_eof_counts_as_dead_serve():
    # A fan-out reader that returns near-instantly (403 / expired manifest) is a
    # fast exit: feed_fast_exit_error produces an error string, and
    # should_idle_dead_serves trips once DEAD_SERVE_IDLE_AFTER consecutive fast
    # exits accumulate — same dead-serve path as direct-serve.
    err = m.feed_fast_exit_error(0.2, 1)
    assert err                                           # non-empty error string
    assert m.should_idle_dead_serves(m.DEAD_SERVE_IDLE_AFTER) is True


def t_fanout_watchdog_kill_condition_is_feed_stalled():
    # The byte-stall watchdog's kill decision is exactly feed_stalled(last_byte_ts, now).
    # A stale timestamp (no bytes for > FANOUT_STALL_S) trips the kill condition;
    # a fresh timestamp (bytes arrived recently) does not.
    now = 1000.0
    stale_ts = now - m.FANOUT_STALL_S - 0.1   # bytes arrived too long ago
    fresh_ts = now - m.FANOUT_STALL_S + 0.1   # bytes arrived recently
    assert m.feed_stalled(stale_ts, now) is True    # watchdog WOULD kill
    assert m.feed_stalled(fresh_ts, now) is False   # watchdog would NOT kill
    # NOTE: The closure's wiring (predicate → _kill_proc) lives inside
    # Feed._serve_fanout and is not separately callable without a live streamlink
    # subprocess.  Integration coverage is provided by the live-UAT
    # (racecast-local-uat skill), not a unit test — that is the honest boundary.


def t_feed_autoresync_default_on_and_falsey_disables():
    assert m.feed_autoresync_enabled({}) is True
    assert m.feed_autoresync_enabled({"RACECAST_FEED_AUTORESYNC": ""}) is True
    for v in ("0", "false", "OFF", "no"):
        assert m.feed_autoresync_enabled({"RACECAST_FEED_AUTORESYNC": v}) is False, v
    for v in ("1", "true", "on"):
        assert m.feed_autoresync_enabled({"RACECAST_FEED_AUTORESYNC": v}) is True, v


def t_env_float_defaults_and_guards():
    assert m._env_float({}, "K", 5.0) == 5.0
    assert m._env_float({"K": ""}, "K", 5.0) == 5.0
    assert m._env_float({"K": "abc"}, "K", 5.0) == 5.0
    assert m._env_float({"K": "0"}, "K", 5.0) == 5.0        # <=0 -> default
    assert m._env_float({"K": "-3"}, "K", 5.0) == 5.0
    assert m._env_float({"K": "12.5"}, "K", 5.0) == 12.5


def t_feed_tuning_getter_defaults():
    assert m.feed_autoresync_skip_rate({}) == 0.02
    assert m.feed_autoresync_cooldown_s({}) == 60.0
    assert m.feed_stall_s({}) == 20.0
    assert m.feed_autoresync_skip_rate({"RACECAST_FEED_AUTORESYNC_SKIP_RATE": "0.05"}) == 0.05
    assert m.feed_stall_s({"RACECAST_FEED_STALL_S": "30"}) == 30.0


def t_ring_headroom_is_16mb():
    assert m.FANOUT_RING_BYTES == 16 * 1024 * 1024


def t_relay_fanout_flag_from_env(monkeypatch=None):
    # fanout_enabled drives Relay.fanout; verified via the pure helper to avoid
    # constructing a full Relay (which needs sources). This guards the wiring contract.
    assert m.fanout_enabled({"RACECAST_FEED_FANOUT": "1"}) is True
    assert m.FANOUT_RING_BYTES >= 1 << 20      # bounded, at least 1 MB


def t_snap_bytes_zero_when_contiguous():
    # data spans [new_cursor-len, new_cursor); start == prev_cursor -> no skip.
    assert m.snap_bytes(100, 150, 50) == 0
    assert m.snap_bytes(100, 100, 0) == 0


def t_snap_bytes_counts_skipped_on_overflow():
    # consumer at 100, but read snapped it forward: served [180,200) -> skipped 80.
    assert m.snap_bytes(100, 200, 20) == 80


def t_render_drift_decision_rate_and_cooldown():
    kw = dict(rate_threshold=0.02, cooldown_s=60.0)
    # below threshold -> False
    assert m.render_drift_decision(0.01, None, **kw) is False
    # at threshold -> False (strict >)
    assert m.render_drift_decision(0.02, None, **kw) is False
    # above threshold -> True
    assert m.render_drift_decision(0.05, None, **kw) is True
    # within cooldown -> False even if over threshold
    assert m.render_drift_decision(0.5, 10.0, **kw) is False
    # cooldown elapsed -> True
    assert m.render_drift_decision(0.05, 61.0, **kw) is True
    # None rate never trips
    assert m.render_drift_decision(None, None, **kw) is False


def t_consumer_health_aggregates_registry():
    # consumer_health aggregates the per-connection registry deterministically
    # (max send-block age + total snaps). White-box: the socket-timing path is the
    # soak's job, not a flaky unit test — the honest boundary.
    ring = m.FeedRing(1 << 20)
    srv = m.FeedFanoutServer("127.0.0.1", 0, ring, m.logging.getLogger("t"))
    assert srv.consumer_health(1000.0) == (None, 0)          # no consumer attached
    with srv._consumers_lock:
        srv._consumers[1] = {"cycle_ts": 990.0, "snaps": 2}
        srv._consumers[2] = {"cycle_ts": 998.0, "snaps": 1}
    stuck, snaps = srv.consumer_health(1000.0)
    assert stuck == 10.0 and snaps == 3                       # max(10,2)=10 ; 2+1=3


def t_soak_stall_active_schedule():
    import importlib.util as _il
    p = os.path.join(ROOT, "tools", "fanout-soak.py")
    s = _il.spec_from_file_location("fanout_soak", p)
    soak = _il.module_from_spec(s); s.loader.exec_module(soak)
    # last 3 s of every 30 s period are a stall
    assert soak.soak_stall_active(0.0, period_s=30, duration_s=3) is False
    assert soak.soak_stall_active(26.9, period_s=30, duration_s=3) is False
    assert soak.soak_stall_active(27.1, period_s=30, duration_s=3) is True
    assert soak.soak_stall_active(29.9, period_s=30, duration_s=3) is True
    assert soak.soak_stall_active(57.1, period_s=30, duration_s=3) is True   # wraps
    assert soak.soak_stall_active(5.0, period_s=0, duration_s=3) is False    # disabled


# --- #488 cursor-progress freeze/stutter detector (pure decision core) ---
# The OBS render-skip signal is blind to a source-demuxer freeze/stutter (measured live
# 2026-07-15: renderSkip 0% / fps 60 while the picture is frozen). The reliable signal is
# OBS's mediaCursor progress: healthy ~1.0x, frozen 0x, stutter = choppy with frequent
# zero-progress ticks (its window AVERAGE is still ~1x, so we count STALL TICKS, not the mean).

def t_cursor_progress_ratio_basic():
    assert m.cursor_progress_ratio(1000, 2000, 1.0) == 1.0        # +1000ms/1s = 1.0x (healthy)
    assert m.cursor_progress_ratio(2000, 2000, 1.0) == 0.0        # no advance = frozen tick
    assert m.cursor_progress_ratio(0, 2000, 1.0) == 2.0           # 2x catch-up burst


def t_cursor_progress_ratio_none_cases():
    assert m.cursor_progress_ratio(None, 2000, 1.0) is None       # no previous sample
    assert m.cursor_progress_ratio(1000, None, 1.0) is None       # cursor unavailable
    assert m.cursor_progress_ratio(1000, 2000, 0.0) is None       # dt <= 0
    assert m.cursor_progress_ratio(5000, 1000, 1.0) is None       # backwards (RESET jump), not a stall


def t_stall_fraction_counts_low_progress_ticks():
    assert m.stall_fraction([1.0, 1.0, 0.95, 1.05], stall_ratio=0.25) == 0.0   # healthy: no stalls
    assert m.stall_fraction([0.0, 0.0, 0.0], stall_ratio=0.25) == 1.0          # frozen: all stalls
    assert m.stall_fraction([0.0, 2.0, 0.0, 1.8], stall_ratio=0.25) == 0.5     # stutter: 2/4 stalled


def t_stall_fraction_ignores_none_and_empty():
    assert m.stall_fraction([None, 1.0, 0.0, None], stall_ratio=0.25) == 0.5   # skip unmeasurable ticks
    assert m.stall_fraction([], stall_ratio=0.25) is None                      # nothing to decide on
    assert m.stall_fraction([None, None], stall_ratio=0.25) is None


def t_freeze_decision_threshold_and_cooldown():
    kw = dict(frac_threshold=0.3, cooldown_s=60.0)
    assert m.freeze_decision(0.0, None, **kw) is False       # healthy
    assert m.freeze_decision(0.2, None, **kw) is False       # below threshold
    assert m.freeze_decision(0.3, None, **kw) is True        # at threshold -> fire
    assert m.freeze_decision(1.0, None, **kw) is True        # frozen
    assert m.freeze_decision(1.0, 10.0, **kw) is False       # within cooldown
    assert m.freeze_decision(1.0, 61.0, **kw) is True        # cooldown elapsed
    assert m.freeze_decision(None, None, **kw) is False      # no data


def t_snap_early_trigger_on_increase():
    assert m.snap_early_trigger(3, 5) is True     # new cursor-snaps since last check = a discontinuity
    assert m.snap_early_trigger(5, 5) is False    # no new snaps
    assert m.snap_early_trigger(None, 5) is False # no baseline yet
    assert m.snap_early_trigger(5, None) is False


def t_feed_freeze_detect_default_on_and_falsey_disables():
    assert m.feed_freeze_detect_enabled({}) is True
    assert m.feed_freeze_detect_enabled({"RACECAST_FEED_FREEZE_DETECT": ""}) is True
    for v in ("1", "true", "on"):
        assert m.feed_freeze_detect_enabled({"RACECAST_FEED_FREEZE_DETECT": v}) is True, v
    for v in ("0", "false", "off", "no"):
        assert m.feed_freeze_detect_enabled({"RACECAST_FEED_FREEZE_DETECT": v}) is False, v


def t_feed_freeze_tuning_getter_defaults():
    assert m.feed_freeze_stall_ratio({}) == 0.25
    assert m.feed_freeze_frac_threshold({}) == 0.30
    assert m.feed_freeze_window({}) == 10
    assert m.feed_freeze_interval_s({}) == 3.0
    assert m.feed_freeze_cooldown_s({}) == 60.0
    # overrides
    assert m.feed_freeze_stall_ratio({"RACECAST_FEED_FREEZE_STALL_RATIO": "0.4"}) == 0.4
    assert m.feed_freeze_window({"RACECAST_FEED_FREEZE_WINDOW": "20"}) == 20
    # invalid / <=0 falls back to the default
    assert m.feed_freeze_window({"RACECAST_FEED_FREEZE_WINDOW": "0"}) == 10
    assert m.feed_freeze_cooldown_s({"RACECAST_FEED_FREEZE_COOLDOWN_S": "x"}) == 60.0


def t_ring_trailing_offset_holds_reserve():
    r = m.FeedRing(1_000_000)
    for i in range(10):                     # write 100 bytes/s; live edge = (i+1)*100
        r.write(b"x" * 100, now=float(i))
    # 3 s behind now=9 -> newest mark with ts<=6 is (700, 6)
    assert r.trailing_offset(3.0, now=9.0) == 700


def t_ring_offset_at_age_clamps_to_start_when_young():
    r = m.FeedRing(1_000_000)
    r.write(b"x" * 100, now=0.0)
    r.write(b"x" * 100, now=0.5)            # only 0.5 s of history
    assert r.offset_at_age(3.0, now=0.5) == r.start_offset() == 0


def t_ring_trailing_offset_zero_is_live_edge():
    r = m.FeedRing(1_000_000)
    r.write(b"x" * 500, now=1.0)
    assert r.trailing_offset(0.0, now=5.0) == r.live_offset() == 500


def t_ring_marks_throttled():
    r = m.FeedRing(10_000)
    for i in range(20):                     # 20 writes 10 ms apart = 0.19 s span
        r.write(b"x" * 10, now=i * 0.01)
    assert len(r._marks) <= 3               # MARK_MIN_INTERVAL_S=0.1 -> ~2-3 marks


def t_ring_marks_pruned_on_overflow():
    r = m.FeedRing(250)
    for i in range(10):                     # 1 s apart -> each its own mark
        r.write(b"x" * 100, now=float(i))
    assert r.start_offset() == 750          # 1000 written, cap 250
    assert all(off > 750 for off, _ in r._marks)   # scrolled-out marks pruned


def t_ring_write_still_works_without_now():
    r = m.FeedRing(1024)                     # production writer passes no `now`
    r.write(b"abc")
    data, cur = r.read(0, timeout=0.1)
    assert data == b"abc" and cur == 3


def t_feed_prebuffer_s_default_and_overrides():
    assert m.feed_prebuffer_s({}) == 3.0                                   # absent -> default
    assert m.feed_prebuffer_s({"RACECAST_FEED_PREBUFFER_S": ""}) == 3.0    # empty -> default
    assert m.feed_prebuffer_s({"RACECAST_FEED_PREBUFFER_S": "3.5"}) == 3.5
    assert m.feed_prebuffer_s({"RACECAST_FEED_PREBUFFER_S": "0"}) == 0.0   # explicit disable
    assert m.feed_prebuffer_s({"RACECAST_FEED_PREBUFFER_S": "-1"}) == 0.0  # negative clamps to 0
    assert m.feed_prebuffer_s({"RACECAST_FEED_PREBUFFER_S": "abc"}) == 3.0 # invalid -> default


def t_fanout_join_offset_trailing_when_indexed():
    r = m.FeedRing(1_000_000)
    for i in range(10):
        r.write(b"x" * 100, now=float(i))
    assert m.fanout_join_offset(r, 3.0, now=9.0) == 700


def t_fanout_join_offset_zero_prebuffer_is_live():
    r = m.FeedRing(1_000_000)
    r.write(b"x" * 500, now=1.0)
    assert m.fanout_join_offset(r, 0.0, now=5.0) == 500


def t_fanout_join_offset_falls_back_without_index():
    class BareLive:
        def live_offset(self):
            return 42
    assert m.fanout_join_offset(BareLive(), 3.0, now=9.0) == 42

    class Bare:
        pass
    assert m.fanout_join_offset(Bare(), 3.0, now=9.0) == 0


def t_fanout_server_join_uses_prebuffer():
    r = m.FeedRing(1_000_000)
    for i in range(10):
        r.write(b"x" * 100, now=float(i))
    srv = m.FeedFanoutServer("127.0.0.1", 0, r, m.logging.getLogger("t533"),
                             prebuffer_s=3.0)
    assert srv.prebuffer_s == 3.0
    assert srv._join_offset(now=9.0) == 700


def t_ring_read_high_caps_at_offset():
    r = m.FeedRing(1_000_000)
    r.write(b"x" * 1000, now=0.0)
    data, cur = r.read(0, 0.1, high=400)
    assert cur == 400 and len(data) == 400


def t_ring_read_high_none_reads_to_live():
    r = m.FeedRing(1_000_000)
    r.write(b"x" * 1000, now=0.0)
    data, cur = r.read(0, 0.1)                 # high omitted -> live edge (unchanged)
    assert cur == 1000 and len(data) == 1000


def t_ring_read_at_cap_returns_empty():
    r = m.FeedRing(1_000_000)
    r.write(b"x" * 1000, now=0.0)
    data, cur = r.read(400, 0.05, high=400)    # cursor already at the cap
    assert data == b"" and cur == 400


def t_fanout_capped_read_holds_below_trailing():
    r = m.FeedRing(1_000_000)
    for i in range(10):
        r.write(b"x" * 100, now=float(i))      # live=1000; trailing(3,9)=700
    data, cur = m.fanout_capped_read(r, 0, 3.0, now=9.0)
    assert cur == 700 and len(data) == 700     # capped at the trailing high-water


def t_fanout_capped_read_zero_prebuffer_reads_to_live():
    r = m.FeedRing(1_000_000)
    r.write(b"x" * 500, now=1.0)
    data, cur = m.fanout_capped_read(r, 0, 0.0, now=5.0)
    assert cur == 500 and len(data) == 500


def t_feed_prebuffer_s_rejects_non_finite():
    for v in ("inf", "-inf", "nan", "Infinity", "NaN"):
        assert m.feed_prebuffer_s({"RACECAST_FEED_PREBUFFER_S": v}) == 3.0, v


def t_fanout_capped_read_falls_back_without_time_index():
    # prebuffer_s > 0 but the ring has no trailing_offset (direct-serve / a test
    # double): fanout_capped_read must take the live-edge branch (no cap applied).
    calls = {}

    class _NoIndexRing:
        def read(self, cursor, timeout, high=None):
            calls["high"] = high
            return b"tail", 500

    data, cur = m.fanout_capped_read(_NoIndexRing(), 0, 3.0, now=9.0)
    assert (data, cur) == (b"tail", 500)
    assert calls["high"] is None            # live-edge branch: no cap passed to read


def t_feed_inbound_degraded_thresholds():
    # gap must exceed BOTH the reserve and the floor
    assert m.feed_inbound_degraded(0.5, 3.0, 1.0) is False   # below both
    assert m.feed_inbound_degraded(2.0, 3.0, 1.0) is False   # below the 3s reserve
    assert m.feed_inbound_degraded(3.5, 3.0, 1.0) is True    # above the reserve
    assert m.feed_inbound_degraded(1.5, 0.0, 1.0) is True    # prebuffer off -> floor governs
    assert m.feed_inbound_degraded(0.9, 0.0, 1.0) is False   # below the floor


def t_update_max_gap_tracks_and_ignores_none():
    assert m.update_max_gap(0.0, None, 100.0) == 0.0         # no prior byte -> unchanged
    assert m.update_max_gap(0.0, 98.0, 100.0) == 2.0         # 2s gap
    assert m.update_max_gap(2.0, 99.5, 100.0) == 2.0         # smaller gap keeps the max


def t_feed_stall_config():
    assert m.feed_stall_floor_s({}) == 1.0
    assert m.feed_stall_floor_s({"RACECAST_FEED_STALL_FLOOR_S": "0.5"}) == 0.5
    assert m.feed_stall_signal_enabled({}) is True
    assert m.feed_stall_signal_enabled({"RACECAST_FEED_STALL_SIGNAL": "0"}) is False


# --- #592: a local capture device as a feed (ffmpeg at the fan-out seam) ---

def t_dshow_device_name_decodes_obs_id():
    # OBS win-dshow stores "<name>:<path>" with '#'->'#22' and ':'->'#3A' escaped in
    # both halves (plugins/win-dshow/encode-dstr.hpp). ffmpeg dshow wants the name.
    obs_id = "Game Capture HD60 X:\\\\?\\usb#22vid_0fd9&pid_0082#22{65e8773d}"
    assert m.dshow_device_name(obs_id) == "Game Capture HD60 X"
    assert m.dshow_device_name("Cam#3A One#22A:\\\\?\\x") == "Cam: One#A"
    assert m.dshow_device_name("  Plain Name  ") == "Plain Name"    # no ':' -> a bare name
    assert m.dshow_device_name("") == ""


def t_local_capture_input_args_per_platform():
    win = m.local_capture_input_args("win32", "HD60 X:\\\\?\\usb#22x", "HD60 X Audio:\\\\?\\a")
    assert win[win.index("-f") + 1] == "dshow"
    assert win[-1] == "video=HD60 X:audio=HD60 X Audio"
    assert "-rtbufsize" in win                       # dshow drops frames on the 3 MB default
    assert m.local_capture_input_args("win32", "HD60 X:p", "")[-1] == "video=HD60 X"
    lin = m.local_capture_input_args("linux", "/dev/video2", "alsa_input.usb-Elgato")
    assert lin[lin.index("-f") + 1] == "v4l2" and "/dev/video2" in lin
    assert lin[-4:] == ["-f", "pulse", "-i", "alsa_input.usb-Elgato"]
    assert m.local_capture_input_args("linux", "/dev/video2", "")[-1] == "/dev/video2"
    mac = m.local_capture_input_args("darwin", "Game Capture HD60 X", "")
    assert mac[mac.index("-f") + 1] == "avfoundation" and mac[-1] == "Game Capture HD60 X:none"
    assert m.local_capture_input_args("win32", "", "x") is None      # no device configured
    assert m.local_capture_input_args("sunos5", "/dev/x", "") is None  # unknown platform


def t_local_capture_cmd_is_mpegts_on_stdout_with_capped_bitrate():
    inp = ["-f", "v4l2", "-i", "/dev/video0"]
    cmd = m.local_capture_cmd(inp, "x264", has_audio=True)
    assert cmd[0] == "ffmpeg" and cmd[-3:] == ["-f", "mpegts", "-"]
    assert "-nostdin" in cmd and "-nostats" in cmd      # no progress flood into feed_X.log
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-tune") + 1] == "zerolatency"
    assert cmd[cmd.index("-b:v") + 1] == f"{m.LOCAL_VIDEO_KBPS}k"
    assert cmd[cmd.index("-maxrate") + 1] == f"{m.LOCAL_VIDEO_KBPS}k"
    assert cmd[cmd.index("-force_key_frames") + 1] == "expr:gte(t,n_forced*1)"
    assert cmd[cmd.index("-c:a") + 1] == "aac" and "-an" not in cmd
    assert cmd[cmd.index("-i") + 1] == "/dev/video0"
    nv = m.local_capture_cmd(inp, "nvenc", has_audio=False)
    assert nv[nv.index("-c:v") + 1] == "h264_nvenc" and "-an" in nv and "-c:a" not in nv


def t_local_bitrate_keeps_the_ring_window_well_above_the_trailing_mark():
    # The 16 MB ring's time window is set by the bitrate; the #533 trailing mark sits
    # 3 s behind live. The cap must leave the window several times that mark.
    bps = (m.LOCAL_VIDEO_KBPS + m.LOCAL_AUDIO_KBPS) * 1000 * 1.1      # ~10% TS overhead
    window_s = m.FANOUT_RING_BYTES * 8 / bps
    assert window_s >= 4 * m.DEFAULT_FEED_PREBUFFER_S, window_s


def t_local_capture_setup_reads_the_machine_env():
    cmd, err = m.local_capture_setup({"RACECAST_CAPTURE": "/dev/video2"}, "linux", "x264")
    assert err is None and "-an" in cmd and "/dev/video2" in cmd
    cmd, err = m.local_capture_setup({"RACECAST_CAPTURE": "/dev/video2",
                                      "RACECAST_CAPTURE_AUDIO": "hw"}, "linux", "x264")
    assert err is None and "-an" not in cmd
    cmd, err = m.local_capture_setup({}, "linux", "x264")
    assert cmd is None and "RACECAST_CAPTURE" in err
    cmd, err = m.local_capture_setup({"RACECAST_CAPTURE": "x"}, "sunos5", "x264")
    assert cmd is None and "sunos5" in err


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("all fanout tests passed")

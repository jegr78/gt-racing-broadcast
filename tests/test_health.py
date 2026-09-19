#!/usr/bin/env python3
"""Stdlib unit checks for live-failure-visibility: cookie_health, resolve_hls
error propagation, Feed phases, Relay.status() contract.
Run: python3 tests/test_health.py"""
import importlib.util, logging, os, tempfile, time

# resolve_hls/ssai_warning now take a logger (per-feed logger in production),
# not a path. A plain logging.Logger with no handlers is the test stand-in:
# its .info/.warning/.error calls are no-ops without a handler.
_LOG = logging.getLogger("test_health.resolve")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "irofeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

# Manual feed arm defaults ON (#492 follow-up): a bare Relay would start feeds
# disarmed (paused). These checks exercise the legacy auto-pull path; pin the
# opt-out so they stay focused (same guard as tests/test_pov.py).
os.environ.setdefault("RACECAST_MANUAL_FEED_ARM", "0")


def t_cookie_health_no_path():
    # Running cookie-less (public streams) is legitimate: never stale.
    assert m.cookie_health(None) == {"present": False, "age_h": None, "stale": False}


def t_cookie_health_missing_file():
    h = m.cookie_health(os.path.join(HERE, "no-such-cookies.txt"))
    assert h == {"present": False, "age_h": None, "stale": False}


def t_cookie_health_fresh_and_stale():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "cookies.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# Netscape HTTP Cookie File\n")
        mtime = os.path.getmtime(path)
        fresh = m.cookie_health(path, now=mtime + 3600)
        assert fresh == {"present": True, "age_h": 1.0, "stale": False}, fresh
        stale = m.cookie_health(path, now=mtime + 14 * 3600)
        assert stale["present"] is True and stale["stale"] is True
        assert round(stale["age_h"]) == 14


def t_cookie_max_age_matches_preflight():
    # One source of truth: 12 h, same as preflight.cookies_status default.
    assert m.COOKIE_MAX_AGE_H == 12


class _FakeRun:
    def __init__(self, stdout="", stderr=""):
        self.stdout, self.stderr = stdout, stderr


def t_resolve_hls_success_returns_url_and_no_error():
    orig = m.subprocess.run
    m.subprocess.run = lambda *a, **k: _FakeRun(stdout="rcq 854 30.0\nhttps://hls.example/x.m3u8\n")
    try:
        url, err, q = m.resolve_hls("https://yt.example/x", None, _LOG)
    finally:
        m.subprocess.run = orig
    assert url == "https://hls.example/x.m3u8" and err is None
    assert q == "854p30"      # actually-served resolution parsed from yt-dlp's --print


def t_resolve_hls_failure_returns_last_stderr_line():
    orig = m.subprocess.run
    m.subprocess.run = lambda *a, **k: _FakeRun(
        stderr="WARNING: noise\nERROR: This live event will begin in 2 hours\n")
    try:
        url, err, _q = m.resolve_hls("https://yt.example/x", None, _LOG)
    finally:
        m.subprocess.run = orig
    assert url is None
    assert "live event will begin" in err


def t_resolve_hls_failure_without_stderr_says_not_live():
    orig = m.subprocess.run
    m.subprocess.run = lambda *a, **k: _FakeRun()
    try:
        url, err, _q = m.resolve_hls("https://yt.example/x", None, _LOG)
    finally:
        m.subprocess.run = orig
    assert url is None and err == "not live?"


def t_feed_initial_phase_is_idle():
    # Feed now opens a per-feed log at init -> use a tempdir, not the repo tree.
    with tempfile.TemporaryDirectory() as td:
        f = m.Feed("A", 53001, 0, lambda: [], td)
    assert f.phase == "idle"
    assert f.last_error is None
    assert isinstance(f.phase_since, float)


def t_set_phase_updates_since_only_on_change():
    with tempfile.TemporaryDirectory() as td:
        f = m.Feed("A", 53001, 0, lambda: [], td)
    f._set_phase("connecting")
    assert f.phase == "connecting"
    since = f.phase_since
    f._set_phase("connecting")          # same phase -> timestamp untouched
    assert f.phase_since == since       # duration accumulates across retries
    f._set_phase("serving")
    assert f.phase == "serving" and f.phase_since >= since


def _mk_relay(td, items, cookies=None, pov_items=None):
    src = m.ScheduleSource(None, os.path.join(td, "cache.txt"), None)
    src.items = list(items)
    src.rows = [(u, "", "", i + 1) for i, u in enumerate(items)]
    pov_src = None
    if pov_items is not None:
        pov_src = m.ScheduleSource(None, os.path.join(td, "pov-cache.txt"), None)
        pov_src.items = list(pov_items)
        pov_src.rows = [(u, "", "", i + 1) for i, u in enumerate(pov_items)]
    return m.Relay(src, [53001, 53002], td, cookies,
                   pov_source=pov_src, pov_port=53003 if pov_src else None)


def t_status_reports_feed_state_age_and_error():
    with tempfile.TemporaryDirectory() as td:
        r = _mk_relay(td, ["https://youtu.be/a", "https://youtu.be/b"])
        r.A._set_phase("serving")
        r.B._set_phase("connecting")
        r.B.last_error = "ERROR: This live event will begin in 2 hours"
        st = r.status()
        assert st["feeds"]["A"]["state"] == "serving"
        assert st["feeds"]["B"]["state"] == "connecting"
        assert st["feeds"]["B"]["last_error"].startswith("ERROR:")
        assert st["feeds"]["A"]["last_error"] is None
        assert st["feeds"]["A"]["state_age_s"] >= 0
        # existing keys unchanged
        assert st["feeds"]["A"]["stint"] == 1 and st["feeds"]["A"]["port"] == 53001
        assert st["cookies"] is False


def t_status_live_and_league_block():
    # Takeover reads the on-air feed/stint + league key from /status so producer B
    # never has to guess.
    with tempfile.TemporaryDirectory() as td:
        r = _mk_relay(td, ["a", "b", "c", "d"])   # A idx0/stint1, B idx1/stint2
        r.sheet_id = "SHEET123"
        st = r.status()
        assert st["live"] == {"feed": "A", "stint": 1, "mode": "race"}
        assert st["league"] == {"sheet_id": "SHEET123", "name": ""}
        r.A.idx = 2                               # after a handover B (idx1/stint2) is on air
        r.on_air_row = 1                          # a real handover advances the DISPLAY row too
        assert r.status()["live"] == {"feed": "B", "stint": 2, "mode": "race"}


def t_status_league_sheet_id_none_when_unset():
    with tempfile.TemporaryDirectory() as td:
        r = _mk_relay(td, ["a", "b"])
        assert r.status()["league"] == {"sheet_id": None, "name": ""}


def t_status_includes_producer_name():
    # #317: the takeover names the outgoing producer A from /status.
    with tempfile.TemporaryDirectory() as td:
        r = _mk_relay(td, ["a", "b"])
        assert r.status()["producer"] == ""        # default: unset -> hostname elsewhere
        r.producer_name = "Bob"
        assert r.status()["producer"] == "Bob"


def t_stream_transition_only_genuine_bool_changes():
    assert m.stream_transition(False, True) == "started"
    assert m.stream_transition(True, False) == "stopped"
    # None on either side (baseline / OBS-unreachable blip) = no event
    for prev, cur in ((None, True), (True, None), (None, False), (None, None),
                      (True, True), (False, False)):
        assert m.stream_transition(prev, cur) is None, (prev, cur)


def t_stream_event_log_line_formats():
    # Start: kbps appended when known (the first sample is a partial-window
    # estimate → an approximate 'bytes are flowing' signal, hence the '~').
    assert m.stream_event_log_line(True) == "OBS stream output started"
    assert (m.stream_event_log_line(True, kbps=4520.4)
            == "OBS stream output started — upstream ~4520 kbps")
    # Stop: uptime appended when known; H/M/S rollover + sub-minute forms.
    assert m.stream_event_log_line(False) == "OBS stream output stopped"
    assert (m.stream_event_log_line(False, uptime_s=1000.0)
            == "OBS stream output stopped after 16m 40s")
    assert (m.stream_event_log_line(False, uptime_s=3672.0)
            == "OBS stream output stopped after 1h 01m 12s")
    assert (m.stream_event_log_line(False, uptime_s=42.0)
            == "OBS stream output stopped after 42s")
    # Defensive: negative/None uptime is dropped, not rendered.
    assert m.stream_event_log_line(False, uptime_s=-3.0) == "OBS stream output stopped"


def t_on_stream_transition_logs_relay_line_with_uptime():
    # The transition is greppable in the relay log: a start line (upstream kbps)
    # and a stop line (uptime start->stop), alongside the feed events.
    with tempfile.TemporaryDirectory() as td:
        r = _mk_relay(td, ["a", "b"])
        recs = []
        handler = logging.Handler()
        handler.emit = lambda rec: recs.append(rec.getMessage())
        prev_level = m.LOG.level
        m.LOG.setLevel(logging.INFO)
        m.LOG.addHandler(handler)
        try:
            r._on_stream_transition("started", now=1000.0, kbps=4520.0)
            r._on_stream_transition("stopped", now=2000.0)
        finally:
            m.LOG.removeHandler(handler)
            m.LOG.setLevel(prev_level)
        assert recs == ["OBS stream output started — upstream ~4520 kbps",
                        "OBS stream output stopped after 16m 40s"], recs


def t_discord_health_payload_producer_in_footer():
    p = m.discord_health_payload("red", ["Feed A down"], prev_level="green",
                                 event_title="GTEC R4", producer="Bob")
    assert p["embeds"][0]["footer"]["text"] == "GTEC R4 · Bob"
    only_prod = m.discord_health_payload("red", ["x"], producer="Bob")
    assert only_prod["embeds"][0]["footer"]["text"] == "Bob"
    neither = m.discord_health_payload("red", ["x"])
    assert "footer" not in neither["embeds"][0]


def t_on_stream_transition_records_health_event_with_producer():
    with tempfile.TemporaryDirectory() as td:
        r = _mk_relay(td, ["a", "b"])
        r.producer_name = "Bob"                    # no webhook URL -> _discord_post no-ops
        r.health_store = m.HealthStore(os.path.join(td, "h.db"))
        try:
            r._on_stream_transition("started", now=1000.0)
            r._on_stream_transition("stopped", now=2000.0)
            evs = r.health_store.events(0, 1e12)
            assert [e["type"] for e in evs] == ["obs_stream_start", "obs_stream_stop"]
            assert all(e["producer"] == "Bob" for e in evs)
        finally:
            r.health_store.close()


def t_status_cookies_health_no_cookies():
    with tempfile.TemporaryDirectory() as td:
        r = _mk_relay(td, ["https://youtu.be/a"])
        st = r.status()
        assert st["cookies_health"] == {"present": False, "age_h": None, "stale": False}


def t_status_cookies_health_stale_file():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "cookies.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("x\n")
        old = os.path.getmtime(path) - 14 * 3600
        os.utime(path, (old, old))
        r = _mk_relay(td, ["https://youtu.be/a"], cookies=path)
        st = r.status()
        assert st["cookies_health"]["present"] is True
        assert st["cookies_health"]["stale"] is True


def t_status_pov_stopped_when_paused_with_age():
    with tempfile.TemporaryDirectory() as td:
        r = _mk_relay(td, ["https://youtu.be/a"], pov_items=["https://youtu.be/p"])
        st = r.status()                      # POV starts paused
        assert st["pov"]["state"] == "stopped"
        assert st["pov"]["state_age_s"] >= 0
        assert st["pov"]["url"] == "https://youtu.be/p"   # existing key kept


def t_cookie_health_vanished_file_treated_as_absent():
    # The cookies file can be swapped/deleted mid-poll (racecast cookies refresh
    # while the relay runs) — must degrade to absent, never raise.
    with tempfile.TemporaryDirectory() as td:
        gone = os.path.join(td, "soon-gone.txt")
        with open(gone, "w", encoding="utf-8") as fh:
            fh.write("x\n")
        os.remove(gone)
        assert m.cookie_health(gone) == {"present": False, "age_h": None, "stale": False}


# --------------------------------------------------------------------------
# Live OBS-reachability probe behind /status's obs.reachable
# --------------------------------------------------------------------------
def t_should_probe_obs_throttles_and_respects_inflight():
    # first call (last_ts=0, idle) -> probe
    assert m.should_probe_obs(0.0, False, 1000.0, 5.0) is True
    # within the interval -> skip
    assert m.should_probe_obs(998.0, False, 1000.0, 5.0) is False
    # interval elapsed -> probe again (>= boundary counts)
    assert m.should_probe_obs(995.0, False, 1000.0, 5.0) is True
    # a probe already in flight -> never launch a second
    assert m.should_probe_obs(0.0, True, 1000.0, 5.0) is False


class _FakeObs:
    """Stand-in for the obs_ws module: get_health_stats() returns
    (reachable, stats, note). stats defaults to {} (no OBS metrics)."""
    def __init__(self, reachable, note, stats=None):
        self._result = (reachable, dict(stats or {}), note)
        self.calls = 0

    def get_health_stats(self):
        self.calls += 1
        return self._result

    def stream_kbps(self, *args, **kwargs):
        return None


def t_status_obs_field_reports_probed_reachability():
    # status() surfaces self.obs_reachable verbatim (the probe owns it), and the
    # default before any probe is None ("unknown" -> panel shows no banner).
    orig = m._obs_ws
    m._obs_ws = None                       # disable the live probe for determinism
    try:
        with tempfile.TemporaryDirectory() as td:
            r = _mk_relay(td, ["https://youtu.be/a", "https://youtu.be/b"])
            assert r.status()["obs"] == {"reachable": None, "note": None}
            r.obs_reachable = False
            r.obs_note = "OBS WebSocket not reachable on 127.0.0.1:4455 (OBS not running?)"
            st = r.status()
            assert st["obs"]["reachable"] is False
            assert "not reachable" in st["obs"]["note"]
            r.obs_reachable = True
            r.obs_note = None
            assert r.status()["obs"] == {"reachable": True, "note": None}
    finally:
        m._obs_ws = orig


def t_run_obs_probe_records_live_result_and_clears_inflight():
    orig = m._obs_ws
    try:
        with tempfile.TemporaryDirectory() as td:
            r = _mk_relay(td, ["https://youtu.be/a", "https://youtu.be/b"])
            r._obs_probe_running = True               # as set by _maybe_probe_obs
            m._obs_ws = _FakeObs(True, "")
            r._run_obs_probe()
            assert r.obs_reachable is True
            assert r.obs_note is None                 # "" normalizes to None
            assert r._obs_probe_running is False
            # an unreachable result records the reason and stays False
            r._obs_probe_running = True
            m._obs_ws = _FakeObs(False, "OBS not running?")
            r._run_obs_probe()
            assert r.obs_reachable is False
            assert r.obs_note == "OBS not running?"
            assert r._obs_probe_running is False
    finally:
        m._obs_ws = orig


def t_run_obs_probe_latches_stream_expected():
    orig = m._obs_ws
    try:
        with tempfile.TemporaryDirectory() as td:
            r = _mk_relay(td, ["https://youtu.be/a", "https://youtu.be/b"])
            assert r.stream_expected is False
            # OBS reachable but NOT streaming -> latch stays False (pre-show).
            r._obs_probe_running = True
            m._obs_ws = _FakeObs(True, "", {"stream_active": False})
            r._run_obs_probe()
            assert r.stream_expected is False
            # OBS goes live -> latch sets True and stays True after it stops again.
            r._obs_probe_running = True
            m._obs_ws = _FakeObs(True, "", {"stream_active": True})
            r._run_obs_probe()
            assert r.stream_expected is True
            r._obs_probe_running = True
            m._obs_ws = _FakeObs(True, "", {"stream_active": False})
            r._run_obs_probe()
            assert r.stream_expected is True          # latched — survives going off air
    finally:
        m._obs_ws = orig


def t_maybe_probe_obs_is_noop_without_client():
    orig = m._obs_ws
    m._obs_ws = None
    try:
        with tempfile.TemporaryDirectory() as td:
            r = _mk_relay(td, ["https://youtu.be/a", "https://youtu.be/b"])
            r._maybe_probe_obs(1000.0)
            assert r.obs_reachable is None
            assert r._obs_probe_running is False
    finally:
        m._obs_ws = orig


def t_maybe_probe_obs_throttles_repeat_calls():
    orig = m._obs_ws
    try:
        with tempfile.TemporaryDirectory() as td:
            r = _mk_relay(td, ["https://youtu.be/a", "https://youtu.be/b"])
            fake = _FakeObs(True, "")
            m._obs_ws = fake
            t = r._maybe_probe_obs(1000.0)
            assert t is not None; t.join(timeout=2)
            assert fake.calls == 1
            # immediate second call within the interval must not re-probe
            assert r._maybe_probe_obs(1001.0) is None
            assert fake.calls == 1
            # past the interval -> a fresh probe runs
            t = r._maybe_probe_obs(1000.0 + m.OBS_PROBE_INTERVAL_S + 0.1)
            assert t is not None; t.join(timeout=2)
            assert fake.calls == 2
    finally:
        m._obs_ws = orig


def t_maybe_probe_obs_disabled_returns_none_without_client():
    orig = m._obs_ws
    m._obs_ws = None
    try:
        with tempfile.TemporaryDirectory() as td:
            r = _mk_relay(td, ["https://youtu.be/a", "https://youtu.be/b"])
            assert r._maybe_probe_obs(1000.0) is None
    finally:
        m._obs_ws = orig


# --------------------------------------------------------------------------
# Aggregate health (live heartbeat) — pure evaluation, transition, payload
# --------------------------------------------------------------------------
def _facts(**kw):
    base = {"feeds_down": [], "feeds_connecting_long": [], "cookies_stale": False,
            "obs_reachable": True, "tailscale_present": True}
    base.update(kw)
    return base


def t_aggregate_health_green_when_all_ok():
    h = m.aggregate_health(_facts())
    assert h["level"] == "green" and h["reasons"] == []


def t_aggregate_health_red_on_feed_down():
    h = m.aggregate_health(_facts(feeds_down=["A"]))
    assert h["level"] == "red"
    assert any("Feed A" in r and "down" in r.lower() for r in h["reasons"])


def t_aggregate_health_yellow_causes():
    # each non-feed-down problem is yellow on its own
    assert m.aggregate_health(_facts(obs_reachable=False))["level"] == "yellow"
    assert m.aggregate_health(_facts(cookies_stale=True))["level"] == "yellow"
    assert m.aggregate_health(_facts(tailscale_present=False))["level"] == "yellow"
    assert m.aggregate_health(_facts(feeds_connecting_long=["B"]))["level"] == "yellow"


def t_aggregate_health_yellow_when_rebuilds_stood_down():
    # #582: a stood-down auto-rebuild is an honest yellow with plain text, naming the
    # feed and, when OBS reports it, the frame rate the producer host renders.
    h = m.aggregate_health(_facts(rebuilds_stood_down={"A": 44.2}))
    assert h["level"] == "yellow"
    assert h["reasons"] == ["Feed A rebuild ineffective — 3 OBS rebuilds did not clear "
                            "the stall (producer host renders 44 fps); auto-rebuild paused"]
    h = m.aggregate_health(_facts(rebuilds_stood_down={"B": None}))
    assert h["reasons"] == ["Feed B rebuild ineffective — 3 OBS rebuilds did not clear "
                            "the stall; auto-rebuild paused"]
    assert m.aggregate_health(_facts(rebuilds_stood_down={}))["level"] == "green"


def t_aggregate_health_obs_unknown_is_not_yellow():
    # obs_reachable None = not probed yet -> must not raise a false alarm
    assert m.aggregate_health(_facts(obs_reachable=None))["level"] == "green"


def t_aggregate_health_red_lists_underlying_yellows():
    h = m.aggregate_health(_facts(feeds_down=["A"], cookies_stale=True))
    assert h["level"] == "red"
    assert any("Feed A" in r for r in h["reasons"])
    assert any("cookie" in r.lower() for r in h["reasons"])


def t_feed_health_state_ok_when_not_dropped():
    # A feed that is not dropped is never "down" — regardless of served/since.
    now = 1000.0
    assert m.feed_health_state(False, None, False, now) == "ok"
    assert m.feed_health_state(False, now - 999, True, now) == "ok"


def t_feed_health_state_never_served_is_connecting():
    # Never delivered a stable picture -> cannot have "lost" one. Even past the
    # grace window it stays connecting (yellow), never down (red). Kills the
    # startup/demo false CRITICAL.
    now = 1000.0
    assert m.feed_health_state(True, now - 999, False, now) == "connecting"


def t_feed_health_state_within_grace_is_connecting():
    # Served, then dropped, but still inside the grace window -> connecting, not
    # down. A self-healing reconnect blip never reaches CRITICAL.
    now = 1000.0
    assert m.feed_health_state(True, now - 5, True, now,
                               grace_s=m.HEALTH_DROP_GRACE_S) == "connecting"
    # missing timestamp is treated as just-dropped (within grace)
    assert m.feed_health_state(True, None, True, now) == "connecting"


def t_feed_health_state_down_after_grace_when_served():
    # Served a stable picture, then dropped continuously past the grace window
    # -> genuine loss -> down (red). The crew gets paged.
    now = 1000.0
    assert m.feed_health_state(True, now - (m.HEALTH_DROP_GRACE_S + 1), True, now) == "down"


def t_health_grace_is_one_heartbeat_interval():
    # Grace = 30 s = one heartbeat interval (scope-confirmed in the issue).
    assert m.HEALTH_DROP_GRACE_S == 30
    assert m.HEALTH_DROP_GRACE_S == m.HEARTBEAT_INTERVAL_S
    assert m.HEALTH_SERVED_OK_S == 10


def t_connecting_settle_is_below_grace():
    # The yellow "stuck connecting" settle window must sit BELOW the red grace so
    # a drop still surfaces yellow before it escalates to red (settle < grace).
    assert 0 < m.HEALTH_CONNECTING_SETTLE_S < m.HEALTH_DROP_GRACE_S


def t_drop_connecting_notifiable_blip_suppressed():
    # A served feed that JUST dropped is a silent blip — not yet a notifiable
    # "stuck connecting" — until it has stayed down past the settle window. This
    # is what stops a reconnect that self-heals within a heartbeat from pinging.
    now = 1000.0
    s = m.HEALTH_CONNECTING_SETTLE_S
    # within the settle window -> silent (NOT notifiable)
    assert m.drop_connecting_notifiable(True, now - (s - 1), now) is False
    # past the settle window -> a genuinely stuck reconnect -> notifiable
    assert m.drop_connecting_notifiable(True, now - (s + 1), now) is True
    # a never-served / not-yet-dropped connecting feed is unchanged (notifiable)
    assert m.drop_connecting_notifiable(False, None, now) is True
    # missing timestamp is treated as just-dropped -> still a blip
    assert m.drop_connecting_notifiable(True, None, now) is False


def t_health_facts_quick_reconnect_blip_not_yellow():
    # A served feed that dropped a few seconds ago (a reconnect in progress) must
    # NOT surface as "stuck connecting" — so the heartbeat does not @here a blip
    # that self-heals within a heartbeat (the VOD EOF-churn / fan-out case).
    orig = m.detect_tailscale_ip
    m.detect_tailscale_ip = lambda: "100.64.0.9"
    try:
        with tempfile.TemporaryDirectory() as td:
            r = _mk_relay(td, ["https://youtu.be/a", "https://youtu.be/b"])
            r._maybe_probe_obs = lambda now: None
            r.obs_reachable = True
            now = time.time()
            r.A.dropped = True
            r.A.served_ok = True                       # HAD a stable picture
            r.A.dropped_since = now - 3                 # dropped 3 s ago (reconnecting)
            facts = r._health_facts(now)
            assert "A" not in facts["feeds_connecting_long"]
            assert m.aggregate_health(facts)["level"] == "green"
    finally:
        m.detect_tailscale_ip = orig


def t_health_facts_stuck_reconnect_past_settle_is_yellow():
    # A served feed still not reconnected past the settle window (but inside the
    # red grace) IS a genuine "stuck connecting" -> yellow (early warning before
    # the loss escalates to red).
    orig = m.detect_tailscale_ip
    m.detect_tailscale_ip = lambda: "100.64.0.9"
    try:
        with tempfile.TemporaryDirectory() as td:
            r = _mk_relay(td, ["https://youtu.be/a", "https://youtu.be/b"])
            r._maybe_probe_obs = lambda now: None
            r.obs_reachable = True
            now = time.time()
            r.A.dropped = True
            r.A.served_ok = True
            r.A.dropped_since = now - (m.HEALTH_CONNECTING_SETTLE_S + 2)
            facts = r._health_facts(now)
            assert "A" in facts["feeds_connecting_long"]
            assert "A" not in facts["feeds_down"]       # not yet red
            assert m.aggregate_health(facts)["level"] == "yellow"
    finally:
        m.detect_tailscale_ip = orig


def t_health_facts_demo_feed_never_red():
    # A feed whose serve never lasted long enough (served_ok False) and has been
    # "dropped" for a long time must NOT land in feeds_down -> no CRITICAL.
    orig = m.detect_tailscale_ip
    m.detect_tailscale_ip = lambda: "100.64.0.9"
    try:
        with tempfile.TemporaryDirectory() as td:
            r = _mk_relay(td, ["https://youtu.be/a", "https://youtu.be/b"])
            r._maybe_probe_obs = lambda now: None
            r.obs_reachable = True
            now = time.time()
            r.A.dropped = True
            r.A.served_ok = False
            r.A.dropped_since = now - 600          # long gone, but never served
            facts = r._health_facts(now)
            assert "A" not in facts["feeds_down"]
            assert m.aggregate_health(facts)["level"] != "red"
    finally:
        m.detect_tailscale_ip = orig


def t_health_facts_sustained_loss_is_red():
    # A feed that DID serve a stable picture and then dropped past the grace
    # window is a genuine loss -> feeds_down -> red.
    orig = m.detect_tailscale_ip
    m.detect_tailscale_ip = lambda: "100.64.0.9"
    try:
        with tempfile.TemporaryDirectory() as td:
            r = _mk_relay(td, ["https://youtu.be/a", "https://youtu.be/b"])
            r._maybe_probe_obs = lambda now: None
            r.obs_reachable = True
            now = time.time()
            r.A.dropped = True
            r.A.served_ok = True
            r.A.dropped_since = now - (m.HEALTH_DROP_GRACE_S + 5)
            facts = r._health_facts(now)
            assert "A" in facts["feeds_down"]
            assert m.aggregate_health(facts)["level"] == "red"
    finally:
        m.detect_tailscale_ip = orig


def t_health_should_notify_transitions_only():
    # first tick: announce only a non-green baseline
    assert m.health_should_notify(None, "green") is False
    assert m.health_should_notify(None, "red") is True
    # no change -> silent (anti-spam over a multi-hour race)
    assert m.health_should_notify("red", "red") is False
    assert m.health_should_notify("green", "green") is False
    # any change -> notify (degrade AND recover)
    assert m.health_should_notify("green", "yellow") is True
    assert m.health_should_notify("red", "green") is True
    assert m.health_should_notify("yellow", "red") is True


def t_discord_health_payload_shape_and_color():
    red = m.discord_health_payload("red", ["Feed A down"], prev_level="green")
    emb = red["embeds"][0]
    assert emb["color"] == m.HEALTH_COLORS["red"]
    assert "Feed A down" in emb["description"]
    # recovery wording when returning to green
    ok = m.discord_health_payload("green", [], prev_level="red")
    assert ok["embeds"][0]["color"] == m.HEALTH_COLORS["green"]
    assert "recover" in (ok["embeds"][0]["title"] + ok["embeds"][0]["description"]).lower()


def t_discord_health_payload_event_title_footer():
    # A non-empty event title (#207) rides along as the embed footer; empty -> no
    # footer at all (the embed is byte-for-byte the pre-#207 shape).
    with_title = m.discord_health_payload("red", ["Feed A down"], prev_level="green",
                                          event_title="GTEC - Round 4")
    assert with_title["embeds"][0]["footer"] == {"text": "GTEC - Round 4"}
    without = m.discord_health_payload("red", ["Feed A down"], prev_level="green")
    assert "footer" not in without["embeds"][0]


def t_discord_health_payload_pings_here_on_every_level():
    # @here must live in top-level `content` (Discord ignores mentions inside
    # embeds) and allowed_mentions must permit it — so a health change pings the
    # crew even if the panel pill is missed. Fires on degraded AND recovery.
    for level, prev in (("red", "green"), ("yellow", "green"), ("green", "red")):
        p = m.discord_health_payload(level, ["x"], prev_level=prev)
        assert p["content"] == "@here"
        assert "everyone" in p["allowed_mentions"]["parse"]


def t_status_includes_health():
    orig = m.detect_tailscale_ip
    m.detect_tailscale_ip = lambda: "100.64.0.9"     # present -> no false yellow in CI
    try:
        with tempfile.TemporaryDirectory() as td:
            r = _mk_relay(td, ["https://youtu.be/a", "https://youtu.be/b"])
            # status() kicks off a throttled async OBS probe; on a loaded CI runner
            # it can finish and overwrite obs_reachable=False before status() reads
            # the health facts, flipping green->yellow. Disable it so the assertion
            # reflects the value we set, not a probe race (flaky macos-3.12, #189 CI).
            r._maybe_probe_obs = lambda now: None
            r.obs_reachable = True
            h = r.status()["health"]
            assert h["level"] == "green" and h["reasons"] == [] and "since_s" in h
            # A served feed that dropped past the grace window -> red.
            r.A.dropped = True                        # lost a live feed
            r.A.served_ok = True                      # it HAD a stable picture
            r.A.dropped_since = time.time() - (m.HEALTH_DROP_GRACE_S + 5)
            assert r.status()["health"]["level"] == "red"
    finally:
        m.detect_tailscale_ip = orig


def t_status_refresh_does_not_consume_notification_baseline():
    # /status refreshes the DISPLAYED level every 2 s but must never advance the
    # webhook baseline — else the 30 s heartbeat would miss the transition.
    orig = m.detect_tailscale_ip
    m.detect_tailscale_ip = lambda: "100.64.0.9"
    try:
        with tempfile.TemporaryDirectory() as td:
            r = _mk_relay(td, ["https://youtu.be/a", "https://youtu.be/b"])
            r.obs_reachable = True
            r.A.dropped = True
            r.A.served_ok = True
            r.A.dropped_since = time.time() - (m.HEALTH_DROP_GRACE_S + 5)
            r.status(); r.status()
            assert r.health_level == "red"            # display followed the drop
            assert r._notified_level is None          # baseline untouched by /status
            assert m.health_should_notify(r._notified_level, r.health_level) is True
    finally:
        m.detect_tailscale_ip = orig


def t_send_health_webhook_noop_without_url():
    # No URL configured -> push disabled, never raises (health display still works).
    with tempfile.TemporaryDirectory() as td:
        r = _mk_relay(td, ["https://youtu.be/a"])
        assert r.discord_webhook_url is None
        r._send_health_webhook("red", ["Feed A down"], None)   # must not raise


def t_send_health_webhook_sets_user_agent():
    # Discord sits behind Cloudflare, which 403s the default "Python-urllib/x.y"
    # User-Agent -> the POST silently never arrives. The request MUST carry an
    # explicit User-Agent (the rest of the relay already does). Regression guard.
    with tempfile.TemporaryDirectory() as td:
        r = _mk_relay(td, ["https://youtu.be/a"])
        r.discord_webhook_url = "https://discord.test/api/webhooks/1/abc"
        captured = {}

        class _Resp:
            def read(self): return b""

        def fake_urlopen(req, timeout=None):
            captured["req"] = req
            return _Resp()

        orig = m.urlopen
        m.urlopen = fake_urlopen
        try:
            r._send_health_webhook("red", ["Feed A down"], None)
        finally:
            m.urlopen = orig
        ua = captured["req"].get_header("User-agent")
        assert ua and "racecast" in ua.lower(), ua


# --------------------------------------------------------------------------
# Auto-failover to the Intermission scene on confirmed on-air feed loss (#378)
# --------------------------------------------------------------------------
def t_auto_failover_disabled_by_default():
    # Opt-in: absent / empty / falsey -> OFF; only an explicit truthy token arms it.
    assert m.auto_failover_enabled({}) is False
    assert m.auto_failover_enabled({"RACECAST_AUTO_FAILOVER": ""}) is False
    for off in ("0", "false", "no", "off", "  Off "):
        assert m.auto_failover_enabled({"RACECAST_AUTO_FAILOVER": off}) is False, off
    for on in ("1", "true", "YES", "on", " On "):
        assert m.auto_failover_enabled({"RACECAST_AUTO_FAILOVER": on}) is True, on


def t_should_failover_fires_on_confirmed_on_air_loss():
    # armed + on-air feed confirmed down + OBS still on the on-air scene + not yet
    # failed over -> flip.
    assert m.should_failover(True, True, "Stint",
                             on_air_scene="Stint", already_failed_over=False) is True


def t_should_failover_quiet_when_disabled():
    assert m.should_failover(False, True, "Stint",
                             on_air_scene="Stint", already_failed_over=False) is False


def t_should_failover_quiet_when_on_air_not_down():
    assert m.should_failover(True, False, "Stint",
                             on_air_scene="Stint", already_failed_over=False) is False


def t_should_failover_quiet_when_obs_not_on_air_scene():
    # Don't yank the program if the producer already moved OBS off the feed scene
    # (Intermission/Intro/replay/…). This is also why a second tick won't re-fire.
    assert m.should_failover(True, True, "Intermission",
                             on_air_scene="Stint", already_failed_over=False) is False
    assert m.should_failover(True, True, None,
                             on_air_scene="Stint", already_failed_over=False) is False


def t_should_failover_quiet_when_already_failed_over():
    # Fire ONCE: the latch blocks a re-fire until a manual return re-arms it.
    assert m.should_failover(True, True, "Stint",
                             on_air_scene="Stint", already_failed_over=True) is False


def t_discord_failover_payload_has_here_ping_and_names_scene():
    p = m.discord_failover_payload("A", "Intermission",
                                   event_title="6h Spa", producer="Bob")
    assert p["content"] == "@here"
    assert p["allowed_mentions"] == {"parse": ["everyone"]}
    embed = p["embeds"][0]
    assert "Intermission" in embed["description"]
    assert "Feed A" in embed["description"]
    assert embed["footer"]["text"] == "6h Spa · Bob"


class _FailoverObs:
    """obs_ws stand-in for the auto-failover path: records the program scene and
    the scene switches. `scene` is the current program scene; set_current_program_scene
    mutates it (so the next read reflects the flip)."""
    STINT_SCENE = "Stint"
    INTERMISSION_SCENE = "Intermission"

    def __init__(self, scene="Stint", reachable=True):
        self.scene = scene
        self.reachable = reachable
        self.switches = []

    def get_current_program_scene(self, *a, **k):
        if not self.reachable:
            return None, "OBS not running?"
        return self.scene, ""

    def set_current_program_scene(self, scene, *a, **k):
        if not self.reachable:
            return False, "OBS not running?"
        self.switches.append(scene)
        self.scene = scene
        return True, ""


def _arm_on_air_down(r):
    """Put feed A (on air) into the confirmed-down ('down') health state."""
    r.A._set_phase("serving")
    r.A.served_ok = True
    r.A.dropped = True
    r.A.dropped_since = time.time() - (m.HEALTH_DROP_GRACE_S + 5)
    r.A.paused = False


def t_auto_failover_flips_to_intermission_once_and_notifies():
    orig = m._obs_ws
    posts = []
    try:
        with tempfile.TemporaryDirectory() as td:
            r = _mk_relay(td, ["a", "b"])      # A on air (idx0)
            r.auto_failover = True
            r._discord_post = lambda payload, what: posts.append((what, payload))
            fake = _FailoverObs(scene="Stint")
            m._obs_ws = fake
            _arm_on_air_down(r)
            r._maybe_auto_failover(time.time())
            assert fake.switches == ["Intermission"]
            assert r._failed_over is True
            assert posts and posts[0][1]["content"] == "@here"
            # A second tick must NOT re-fire (latched; and OBS is now on Intermission)
            r._maybe_auto_failover(time.time())
            assert fake.switches == ["Intermission"]
            assert len(posts) == 1
    finally:
        m._obs_ws = orig


def t_auto_failover_does_not_fire_when_disabled():
    orig = m._obs_ws
    try:
        with tempfile.TemporaryDirectory() as td:
            r = _mk_relay(td, ["a", "b"])
            r.auto_failover = False
            fake = _FailoverObs(scene="Stint")
            m._obs_ws = fake
            _arm_on_air_down(r)
            r._maybe_auto_failover(time.time())
            assert fake.switches == []
            assert r._failed_over is False
    finally:
        m._obs_ws = orig


def t_auto_failover_quiet_when_obs_already_off_feed_scene():
    orig = m._obs_ws
    try:
        with tempfile.TemporaryDirectory() as td:
            r = _mk_relay(td, ["a", "b"])
            r.auto_failover = True
            fake = _FailoverObs(scene="Intermission")   # producer already moved off
            m._obs_ws = fake
            _arm_on_air_down(r)
            r._maybe_auto_failover(time.time())
            assert fake.switches == []
            assert r._failed_over is False
    finally:
        m._obs_ws = orig


def t_auto_failover_re_arms_after_manual_return():
    orig = m._obs_ws
    try:
        with tempfile.TemporaryDirectory() as td:
            r = _mk_relay(td, ["a", "b"])
            r.auto_failover = True
            r._discord_post = lambda *a, **k: None
            fake = _FailoverObs(scene="Stint")
            m._obs_ws = fake
            _arm_on_air_down(r)
            r._maybe_auto_failover(time.time())
            assert fake.switches == ["Intermission"] and r._failed_over is True
            # Producer manually returns to the feed scene; feed has recovered.
            fake.scene = "Stint"
            r.A.dropped = False
            r._maybe_auto_failover(time.time())
            assert r._failed_over is False            # re-armed
            # Feed dies again -> a fresh failover is allowed.
            _arm_on_air_down(r)
            r._maybe_auto_failover(time.time())
            assert fake.switches == ["Intermission", "Intermission"]
    finally:
        m._obs_ws = orig


def t_auto_failover_obs_unreachable_is_quiet_no_crash():
    orig = m._obs_ws
    try:
        with tempfile.TemporaryDirectory() as td:
            r = _mk_relay(td, ["a", "b"])
            r.auto_failover = True
            fake = _FailoverObs(scene="Stint", reachable=False)
            m._obs_ws = fake
            _arm_on_air_down(r)
            r._maybe_auto_failover(time.time())        # must not raise
            assert fake.switches == []
            assert r._failed_over is False
    finally:
        m._obs_ws = orig


def t_auto_failover_noop_without_obs_module():
    orig = m._obs_ws
    m._obs_ws = None
    try:
        with tempfile.TemporaryDirectory() as td:
            r = _mk_relay(td, ["a", "b"])
            r.auto_failover = True
            _arm_on_air_down(r)
            r._maybe_auto_failover(time.time())        # must not raise
            assert r._failed_over is False
    finally:
        m._obs_ws = orig


def t_event_stop_argv():
    # frozen: re-invoke the binary itself
    assert m.event_stop_argv(True, "/opt/racecast", "/ignored") == \
        ["/opt/racecast", "event", "stop"]
    # source: python runs ../racecast.py relative to the relay script dir
    argv = m.event_stop_argv(False, "/usr/bin/python3", "/repo/src/relay")
    assert argv[0] == "/usr/bin/python3" and argv[1].endswith("racecast.py")
    assert argv[-2:] == ["event", "stop"]
    assert "src/relay/../racecast.py".split("/")[-1] in argv[1]


def t_classify_source_state():
    # Twitch: channel offline / not live yet.
    assert m.classify_source_state(
        "error: No playable streams found on this URL: twitch.tv/kekko") == "not_live_yet"
    # yt-dlp YouTube: channel not currently live.
    assert m.classify_source_state("ERROR: [youtube] abc: The channel is not currently live") == "not_live_yet"
    assert m.classify_source_state("not live?") is None       # ambiguous fallback -> generic
    # YouTube: broadcast over.
    assert m.classify_source_state("ERROR: [youtube] sgoDA5E4aJ0: This live event has ended.") == "ended"
    # Generic / transient -> None (unchanged behaviour).
    assert m.classify_source_state("HTTP Error 429: Too Many Requests") is None
    assert m.classify_source_state("HTTP Error 403: Forbidden") is None
    assert m.classify_source_state("") is None
    assert m.classify_source_state(None) is None


def t_aggregate_health_not_live_yet_distinct_reason():
    h = m.aggregate_health(_facts(feeds_connecting_long=["A"],
                                  feed_source_states={"A": "not_live_yet"}))
    assert h["level"] == "yellow"                              # severity unchanged
    assert any("source not live yet" in r for r in h["reasons"])
    assert not any("stuck connecting" in r for r in h["reasons"])


def t_aggregate_health_ended_distinct_reason():
    h = m.aggregate_health(_facts(feeds_down=["A"], feed_source_states={"A": "ended"}))
    assert h["level"] == "red"                                # ended is still a genuine loss
    assert any("live stream ENDED" in r for r in h["reasons"])
    assert not any("lost the live stream" in r for r in h["reasons"])


def t_aggregate_health_reasons_unchanged_without_source_states():
    # No feed_source_states -> the existing generic reasons (backward compat).
    assert any("lost the live stream" in r
               for r in m.aggregate_health(_facts(feeds_down=["A"]))["reasons"])
    assert any("stuck connecting" in r
               for r in m.aggregate_health(_facts(feeds_connecting_long=["B"]))["reasons"])


def t_churn_at_here_suppressed_predicate():
    assert m.churn_at_here_suppressed("not_live_yet") is True
    assert m.churn_at_here_suppressed("ended") is True
    assert m.churn_at_here_suppressed(None) is False       # genuine churn still notifies
    assert m.churn_at_here_suppressed("serving") is False


def t_churn_at_here_suppressed_for_not_live_source():
    with tempfile.TemporaryDirectory() as td:
        r = _mk_relay(td, ["a", "b"])
        r.health_store = m.HealthStore(os.path.join(td, "h.db"))
        posts = []
        r._discord_post = lambda payload, what: posts.append(what)
        now = 1000.0
        try:
            # Make Feed A churn: >= threshold feed_recovery events inside the window.
            for k in range(m.FEED_CHURN_THRESHOLD):
                r.health_store.record_event(now - 10 + k, "feed_recovery",
                                            metadata={"feed": "A"})
            # A not-live-yet source -> the @here is suppressed.
            r._maybe_notify_recovery_churn("A", now, "not_live_yet")
            assert posts == []
            # Genuine churn (source_state None) -> the @here still fires.
            r._maybe_notify_recovery_churn("A", now, None)
            assert "feed-recovery-churn" in posts
        finally:
            r.health_store.close()


def t_aggregate_health_jitter_is_yellow_reason():
    facts = {"feeds_jittery": ["A"]}
    h = m.aggregate_health(facts)
    assert h["level"] == "yellow", h
    assert any("inbound stall" in r.lower() for r in h["reasons"]), h


def t_aggregate_health_no_jitter_is_green():
    assert m.aggregate_health({"feeds_jittery": []})["level"] == "green"


def t_jitter_yellow_does_not_page_but_real_yellow_does():
    # display includes jitter (yellow); notify excludes it (green) -> no page
    facts = {"feeds_jittery": ["A"]}
    display = m.aggregate_health(facts)["level"]
    notify = m.aggregate_health({**facts, "feeds_jittery": []})["level"]
    assert display == "yellow" and notify == "green"
    assert m.health_should_notify(None, notify) is False       # green baseline -> no page
    # a real yellow (e.g. cookies) still pages even with jitter present
    real = m.aggregate_health({**facts, "feeds_jittery": [], "cookies_stale": True})["level"]
    assert real == "yellow" and m.health_should_notify("green", real) is True



def t_aggregate_health_backlog_is_a_yellow_reason_with_the_next_step():
    # #583: named after the measurement (any slow consumer), not a guessed cause, and
    # the line carries the operator's next step.
    h = m.aggregate_health(_facts(feeds_backlogged={"A": 11.6}))
    assert h["level"] == "yellow", h
    assert h["reasons"] == ["Feed A output 12 s behind live — OBS reads slower than real "
                            "time; step the feed quality down to ROBUST"], h
    assert m.aggregate_health(_facts(feeds_backlogged={}))["level"] == "green"

if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

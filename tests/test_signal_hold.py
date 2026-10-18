#!/usr/bin/env python3
"""Feed covers, signal hold and ultra-low-latency hint
(spec docs/superpowers/specs/2026-10-17-feed-signal-hold-design.md).
Run: python3 tests/test_signal_hold.py"""
import importlib.util, logging, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "irofeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
LOG = logging.getLogger("t")

MEDIA_ULL = ("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:1\n"
             "#EXT-X-MEDIA-SEQUENCE:5856\n#EXTINF:1.0,\nseg.ts\n")
MEDIA_NORMAL = "#EXTM3U\n#EXT-X-TARGETDURATION:5\n#EXTINF:5.0,\nseg.ts\n"
MASTER = ("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=1920x1080\n"
          "variant/index.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=3000000\nlow.m3u8\n")


def t_parse_target_duration_media_playlist():
    assert m.parse_target_duration(MEDIA_ULL, "https://h/x.m3u8") == (1.0, None)
    assert m.parse_target_duration(MEDIA_NORMAL, "https://h/x.m3u8") == (5.0, None)


def t_parse_target_duration_master_points_at_first_variant():
    assert m.parse_target_duration(MASTER, "https://h/a/master.m3u8") == (
        None, "https://h/a/variant/index.m3u8")


def t_parse_target_duration_garbage_is_none():
    for text in ("", None, "#EXTM3U\n", "#EXT-X-TARGETDURATION:abc\n"):
        assert m.parse_target_duration(text, "https://h/x") == (None, None), text


def t_probe_manifest_follows_one_master_hop():
    pages = {"https://h/a/master.m3u8": MASTER, "https://h/a/variant/index.m3u8": MEDIA_ULL}
    assert m.probe_manifest("https://h/a/master.m3u8", LOG, get=pages.__getitem__) == (None, 1.0)


def t_probe_manifest_failure_is_quiet():
    def boom(_url):
        raise OSError("down")
    assert m.probe_manifest("https://h/x", LOG, get=boom) == (None, None)


def t_probe_manifest_keeps_the_ssai_warning():
    ssai = MEDIA_NORMAL + "#EXT-X-CUE-OUT:30\n"
    warn, target = m.probe_manifest("https://h/x", LOG, get=lambda _u: ssai)
    assert target == 5.0 and "server-side ads" in warn


def t_is_ultra_low_latency_threshold():
    assert m.is_ultra_low_latency(1.0) is True
    assert m.is_ultra_low_latency(2.0) is False
    assert m.is_ultra_low_latency(None) is False


def t_latency_status_fields():
    assert m.latency_status_fields(1.0) == {"target_duration_s": 1.0, "ultra_low_latency": True}
    assert m.latency_status_fields(5.0) == {"target_duration_s": 5.0, "ultra_low_latency": False}
    assert m.latency_status_fields(None) == {"target_duration_s": None, "ultra_low_latency": False}


def t_get_manifest_sends_the_cookie_jar_and_browser_agent():
    """YouTube 403s a manifest fetch without the session cookies and a browser UA
    (measured 2026-10-18), so the probe must send what streamlink sends."""
    import http.server, tempfile, threading
    seen = {}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen["ua"], seen["cookie"] = self.headers.get("User-Agent"), self.headers.get("Cookie")
            ok = seen["ua"] == m.STREAMLINK_YT_UA and "SID=abc" in (seen["cookie"] or "")
            self.send_response(200 if ok else 403)
            self.end_headers()
            if ok:
                self.wfile.write(MEDIA_ULL.encode())

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    jar = os.path.join(tempfile.mkdtemp(), "yt-cookies.txt")
    with open(jar, "w", encoding="utf-8") as fh:
        fh.write("# Netscape HTTP Cookie File\n"
                 "127.0.0.1\tFALSE\t/\tFALSE\t4102444800\tSID\tabc\n")
    url = f"http://127.0.0.1:{srv.server_port}/manifest.m3u8"
    try:
        assert m.probe_manifest(url, LOG, cookies=jar) == (None, 1.0), seen
        assert m.probe_manifest(url, LOG) == (None, None)        # no jar: 403, quiet
    finally:
        srv.shutdown()


# --- Part C: feed covers -------------------------------------------------------------

def t_cover_ffmpeg_cmd_decodes_keyframes_only():
    cmd = m.cover_ffmpeg_cmd(1920)
    assert cmd[cmd.index("-skip_frame") + 1] == "nokey"
    assert cmd.index("-skip_frame") < cmd.index("-i")          # decoder option, before input
    assert "scale=1920:-2" in cmd and cmd[-1] == "pipe:1" and "mjpeg" in cmd


class _Pipe:
    def __init__(self, chunks):
        self._c = list(chunks)

    def read(self, _n):
        return self._c.pop(0) if self._c else b""


class _IdleRing:
    closed = False

    def live_offset(self):
        return 0

    def head(self):
        return b""

    def read(self, cursor, timeout, high=None):
        import time
        time.sleep(0.01)
        return b"", cursor


def _fake_ffmpeg(stdout_chunks):
    class P:
        def poll(self):
            return None

        def kill(self):
            pass

    class In:
        def write(self, _b):
            pass

        def flush(self):
            pass

        def close(self):
            pass
    return lambda _tap: (P(), In(), _Pipe(stdout_chunks))


def t_cover_tap_writes_the_latest_frame_atomically():
    import tempfile, time
    jpeg1, jpeg2 = b"\xff\xd8one\xff\xd9", b"\xff\xd8two\xff\xd9"
    path = os.path.join(tempfile.mkdtemp(), "covers", "feed-a.jpg")
    tap = m.FeedCoverTap(_IdleRing(), path, LOG,
                         spawn=_fake_ffmpeg([jpeg1 + jpeg2[:4], jpeg2[4:]])).start()
    def written():
        with open(path, "rb") as fh:
            return fh.read()
    for _ in range(200):
        if tap.last_write_ts is not None and written() == jpeg2:
            break
        time.sleep(0.02)
    tap.stop()
    with open(path, "rb") as fh:
        assert fh.read() == jpeg2
    assert not [f for f in os.listdir(os.path.dirname(path)) if f.endswith(".tmp")]


def t_cover_tap_spawn_failure_is_quiet():
    import tempfile, time

    def boom(_tap):
        raise OSError("no ffmpeg")
    path = os.path.join(tempfile.mkdtemp(), "covers", "feed-b.jpg")
    tap = m.FeedCoverTap(_IdleRing(), path, LOG, spawn=boom).start()
    time.sleep(0.1)
    tap.stop()
    assert tap.last_write_ts is None and not os.path.exists(path)


def t_cover_visible_table():
    base = dict(enabled=True, serving=True, obs_playing_s=m.COVER_PLAY_S,
                hold_active=False, feed_item_shown=True)
    assert m.cover_visible(**base) is False                                 # playing: down
    assert m.cover_visible(**{**base, "obs_playing_s": 0.5}) is True        # not yet
    assert m.cover_visible(**{**base, "obs_playing_s": None}) is True       # OBS not playing
    assert m.cover_visible(**{**base, "serving": False}) is True
    assert m.cover_visible(**{**base, "hold_active": True}) is True


def t_cover_hidden_when_feed_item_hidden():
    # A cover above a hidden feed item would cover the on-air feed in the Stint scene.
    assert m.cover_visible(enabled=True, serving=False, obs_playing_s=None,
                           hold_active=True, feed_item_shown=False) is False


def t_cover_disabled_is_never_shown():
    assert m.cover_visible(enabled=False, serving=False, obs_playing_s=None,
                           hold_active=True, feed_item_shown=True) is False


def t_media_play_update_needs_an_advancing_cursor():
    # OBS's mediaCursor advances only while it decodes frames (live test 2026-10-18:
    # bytes arriving is not enough, the picture waits for a keyframe).
    U = m.media_play_update
    st = U(None, ("OBS_MEDIA_STATE_PLAYING", 1000), 10.0)
    assert m.media_playing_s(st, 10.0) is None                 # first sample: no proof yet
    st = U(st, ("OBS_MEDIA_STATE_PLAYING", 1400), 11.0)
    assert m.media_playing_s(st, 11.0) == 0.0                  # advanced: playing since now
    st = U(st, ("OBS_MEDIA_STATE_PLAYING", 2400), 12.0)
    assert m.media_playing_s(st, 12.0) == 1.0
    st = U(st, ("OBS_MEDIA_STATE_PLAYING", 2400), 13.0)        # frozen
    assert m.media_playing_s(st, 13.0) is None
    st = U(st, ("OBS_MEDIA_STATE_PLAYING", 300), 14.0)         # reopened: cursor restarted
    assert m.media_playing_s(st, 14.0) is None
    st = U(st, ("OBS_MEDIA_STATE_STOPPED", 900), 15.0)
    assert m.media_playing_s(st, 15.0) is None
    st = U(st, (None, None), 16.0)                             # OBS unreachable
    assert m.media_playing_s(st, 16.0) is None


class FakeObs:
    """The obs_ws calls the cover and hold ticks make, recorded; never a socket."""

    def __init__(self, missing=False, feed_shown=True, program="Stint"):
        self.calls, self.missing, self.feed_shown = [], missing, feed_shown
        self.director_muted = False
        self.program = program
        self.cursor = {"Feed A": None, "Feed B": None}

    def ensure_cover_items(self, feed, cover, path, scenes=None):
        self.calls.append(("ensure", cover))
        state = "no-feed" if self.missing else "ok"
        return {s: state for s in (scenes or ("Stint", "Splitscreen"))}, ""

    def get_current_program_scene(self):
        return self.program, ""

    def get_media_status(self, name):
        c = self.cursor.get(name)
        return (("OBS_MEDIA_STATE_PLAYING", c) if c is not None else (None, None)), ""

    def get_scene_item_enabled(self, scene, source):
        shown = self.feed_shown(scene, source) if callable(self.feed_shown) else self.feed_shown
        return shown, ""

    def set_scene_item_enabled(self, scene, source, enabled):
        self.calls.append(("enable", scene, source, enabled))
        return True, ""

    def set_input_file(self, name, path):
        self.calls.append(("file", name))
        return True, ""

    def get_input_mute(self, name):
        return self.director_muted, ""

    def set_input_mute(self, name, muted):
        self.calls.append(("mute", name, muted))
        return True, ""


def _relay_for_covers(obs, serving=True, stills=True):
    import tempfile
    r = m.Relay.__new__(m.Relay)            # no network, no threads
    r._obs, r.obs_note, r.fanout, r.solo = obs, "", True, False
    r._covers = True
    r._cover = {}
    r._play = {}
    r._hold = {}
    r._cover_dir = tempfile.mkdtemp()
    r._cover_ensured_ts = None

    class F:
        def __init__(self, name):
            self.name, self.paused = name, False
            self.phase = "serving" if serving else "idle"
            self.fanout_server, self.cover_tap = None, None
    r.feeds = {"A": F("A"), "B": F("B")}
    if stills:                              # the taps have written a still already
        for k in r.feeds:
            with open(r._cover_latest_path(k), "wb") as fh:
                fh.write(b"\xff\xd8" + k.encode() + b"\xff\xd9")
    return r


def _play(obs, r, feed_input, start_s, t0):
    """Advance OBS's cursor for `feed_input` over ticks t0, t0+1, ..., as if playing."""
    for i in range(int(start_s) + 2):
        obs.cursor[feed_input] = 1000 * (i + 1)
        r._cover_tick(t0 + i)


def _enables(obs, **match):
    return [c for c in obs.calls if c[0] == "enable"
            and all(c[{"scene": 1, "source": 2, "on": 3}[k]] == v for k, v in match.items())]


def t_cover_tick_shows_until_obs_plays_then_hides():
    obs = FakeObs()
    r = _relay_for_covers(obs)
    r._cover_tick(100.0)
    assert _enables(obs, scene="Stint", source="Feed A Cover", on=True)
    obs.calls.clear()
    _play(obs, r, "Feed A", m.COVER_PLAY_S, 101.0)
    assert _enables(obs, scene="Stint", source="Feed A Cover", on=False)


def t_cover_still_is_frozen_while_its_scene_is_on_program():
    # Live test 2026-10-18: reloading a visible still every few seconds looks like stutter.
    obs = FakeObs(program="Splitscreen")
    r = _relay_for_covers(obs)
    for t in range(0, 30):
        r._cover_tick(100.0 + t)
    assert obs.calls.count(("file", "Feed B Cover")) <= 1      # at most the raise itself


def t_cover_still_refreshes_while_off_program():
    obs = FakeObs(program="Standby")
    r = _relay_for_covers(obs)
    for t in range(0, 12):
        r._cover_tick(100.0 + t)
    assert obs.calls.count(("file", "Feed B Cover")) >= 2


def t_cover_raise_on_program_loads_one_fresh_still():
    obs = FakeObs(program="Stint")
    r = _relay_for_covers(obs)
    _play(obs, r, "Feed A", m.COVER_PLAY_S, 100.0)              # A playing, cover down
    obs.calls.clear()
    obs.cursor["Feed A"] = obs.cursor["Feed A"]                 # freeze: OBS reopens / stalls
    for t in range(5):
        r._cover_tick(110.0 + t)
    assert _enables(obs, scene="Stint", source="Feed A Cover", on=True)
    assert obs.calls.count(("file", "Feed A Cover")) == 1


def t_cover_tick_applies_only_on_change_and_reasserts():
    obs = FakeObs(program="Standby")
    r = _relay_for_covers(obs)
    r._cover_tick(100.0)
    obs.calls.clear()
    r._cover_tick(101.0)
    assert not _enables(obs)
    r._cover_tick(100.0 + m.COVER_REASSERT_S + 1)
    assert _enables(obs)


def t_cover_tick_missing_items_never_raise():
    obs = FakeObs(missing=True)
    r = _relay_for_covers(obs)
    r._cover_tick(100.0)
    assert r._cover["A"]["status"] == "missing" and not _enables(obs)
    r._cover_tick(101.0)                                      # retried only after 30 s
    assert obs.calls.count(("ensure", "Feed A Cover")) == 1


def t_cover_tick_hides_a_cover_whose_feed_item_is_hidden():
    obs = FakeObs(feed_shown=lambda scene, src: not (scene == "Stint" and src == "Feed B"))
    r = _relay_for_covers(obs)
    r._cover_tick(100.0)
    assert _enables(obs, scene="Stint", source="Feed B Cover", on=False)
    assert _enables(obs, scene="Splitscreen", source="Feed B Cover", on=True)


def t_pre_show_cover_only_when_the_feed_is_not_already_playing():
    obs = FakeObs(program="Standby")
    r = _relay_for_covers(obs)
    r._cover_tick(100.0)
    obs.calls.clear()
    r._pre_show_cover("A", "Stint")
    assert _enables(obs, scene="Stint", source="Feed A Cover", on=True)
    _play(obs, r, "Feed A", m.COVER_PLAY_S, 101.0)            # already live in Splitscreen
    obs.calls.clear()
    r._pre_show_cover("A", "Stint")
    assert not obs.calls


def t_obs_file_changes_only_on_refresh():
    # OBS reloads an image source by itself whenever its file changes (live test
    # 2026-10-18), so the tap must never write the file OBS shows: the relay copies the
    # tap's latest still over only when it refreshes.
    obs = FakeObs(program="Splitscreen")
    r = _relay_for_covers(obs)
    shown, latest = r._cover_path("B"), r._cover_latest_path("B")
    assert shown != latest
    with open(latest, "wb") as fh:
        fh.write(b"\xff\xd8one\xff\xd9")
    r._cover_tick(100.0)                                       # raise on program: one copy
    with open(shown, "rb") as fh:
        assert fh.read() == b"\xff\xd8one\xff\xd9"
    with open(latest, "wb") as fh:
        fh.write(b"\xff\xd8two\xff\xd9")                        # the tap moves on
    for t in range(1, 20):
        r._cover_tick(100.0 + t)
    with open(shown, "rb") as fh:
        assert fh.read() == b"\xff\xd8one\xff\xd9"               # frozen while on program


def t_refresh_without_a_still_yet_is_quiet():
    obs = FakeObs(program="Standby")
    r = _relay_for_covers(obs, stills=False)
    r._cover_tick(100.0)                                       # no tap output yet
    assert not os.path.exists(r._cover_path("A"))


def t_forced_pre_show_raises_even_a_playing_feed_but_only_where_it_is_visible():
    # A rebuild (RESET, reconnect, backlog shed) reopens a feed OBS was playing.
    obs = FakeObs(program="Stint",
                  feed_shown=lambda scene, src: not (scene == "Splitscreen"))
    r = _relay_for_covers(obs)
    _play(obs, r, "Feed A", m.COVER_PLAY_S, 100.0)
    obs.calls.clear()
    r._pre_show_cover("A", force=True)
    assert _enables(obs, scene="Stint", source="Feed A Cover", on=True)
    assert not _enables(obs, scene="Splitscreen")             # feed item hidden there
    assert r.cover_status("A") == "shown"                     # /status at once, not next tick


def t_feed_rebuild_raises_the_cover_first():
    order = []

    class FakeWs:
        @staticmethod
        def release_feed_inputs(ports=None):
            order.append("rebuild")
            return ["Feed A"], ""
    f = m.Feed.__new__(m.Feed)
    f.name, f.port, f.log = "A", 53001, LOG
    f.before_obs_reconnect = lambda: order.append("cover")
    real = m._obs_ws
    m._obs_ws = FakeWs
    try:
        assert f._obs_reconnect_now() == ["Feed A"]
    finally:
        m._obs_ws = real
    assert order == ["cover", "rebuild"]


def t_covers_enabled_env():
    assert m.covers_enabled({}) is True
    assert m.covers_enabled({"RACECAST_FEED_COVERS": "0"}) is False


def t_cover_status_field():
    obs = FakeObs(program="Standby")
    r = _relay_for_covers(obs)
    assert r.cover_status("A") == "missing"
    r._cover_tick(100.0)
    assert r.cover_status("A") == "shown"
    r._covers = False
    assert r.cover_status("A") == "off"


class _CutRelay:
    """The relay surface the split/stint appliers use, plus a recorded pre-show."""
    solo = False

    def __init__(self):
        self.pre = []

    def live_feed(self):
        return "A"

    def _pre_show_cover(self, feed, scene):
        self.pre.append((feed, scene))


class _OkObs:
    def set_scene_item_enabled(self, scene, target, enabled):
        return True, ""

    def set_input_mute(self, target, muted):
        return True, ""


def t_split_cut_pre_shows_both_covers_before_the_intents():
    r = _CutRelay()
    m.apply_split_state(r, _OkObs())
    assert sorted(r.pre) == [("A", "Splitscreen"), ("B", "Splitscreen")]
    r.pre.clear()
    m.apply_split_audio(r, _OkObs())                         # audio only: no cut, no cover
    assert r.pre == []


def t_stint_cut_pre_shows_the_picked_feed():
    r = _CutRelay()
    m.apply_stint_state(r, _OkObs(), "B")
    assert r.pre == [("B", "Stint")]


# --- Part B: signal hold -------------------------------------------------------------

S = m.signal_hold_step


def t_hold_short_gap_absorbed_by_reserve_never_engages():
    assert S("live", reserve_s=1.2, last_byte_age_s=1.6, steady_s=0.0, cancel=False) == ("live", None)


def t_hold_engages_when_reserve_empty_and_idle():
    assert S("live", reserve_s=0.1, last_byte_age_s=1.1, steady_s=0.0, cancel=False) == ("hold", "engage")
    assert S("live", reserve_s=0.1, last_byte_age_s=0.5, steady_s=0.0, cancel=False) == ("live", None)
    assert S("live", reserve_s=None, last_byte_age_s=9.0, steady_s=0.0, cancel=False) == ("live", None)


def t_hold_single_burst_does_not_release():
    assert S("hold", reserve_s=2.0, last_byte_age_s=0.1, steady_s=1.0, cancel=False) == ("hold", None)


def t_hold_releases_after_steady_window():
    assert S("hold", reserve_s=3.0, last_byte_age_s=0.1, steady_s=5.0, cancel=False) == ("live", "release")


def t_hold_cancel_wins():
    assert S("hold", reserve_s=0.0, last_byte_age_s=9.0, steady_s=0.0, cancel=True) == ("live", "cancel")
    assert S("live", reserve_s=0.0, last_byte_age_s=9.0, steady_s=0.0, cancel=True) == ("live", None)


def t_hold_suzuka_2026_10_17_sequence():
    """21:07: gaps 4.03 -> 5.72 s, backlog floor 0.8 s: engages, no release mid-stall."""
    st, seen = "live", []
    for reserve, age, steady in [(3.9, 0.2, 30), (0.8, 0.4, 30), (0.1, 1.2, 0),
                                 (0.0, 4.0, 0), (1.9, 0.1, 0.5), (0.2, 1.5, 0),
                                 (2.5, 0.1, 2.0), (3.0, 0.1, 5.2)]:
        st, a = S(st, reserve_s=reserve, last_byte_age_s=age, steady_s=steady, cancel=False)
        seen.append(a)
    assert seen == [None, None, "engage", None, None, None, None, "release"], seen


def t_steady_since_update():
    assert m.steady_since_update(None, None, 10.0) == 10.0
    assert m.steady_since_update(5.0, 9.5, 10.0) == 5.0       # 0.5 s gap: still steady
    assert m.steady_since_update(5.0, 8.0, 10.0) == 10.0      # 2.0 s gap: restart


def t_signal_hold_enabled_env():
    assert m.signal_hold_enabled({}) is True
    assert m.signal_hold_enabled({"RACECAST_FEED_SIGNAL_HOLD": "0"}) is False


def _hold_relay(director_muted=False, url="https://www.youtube.com/watch?v=x"):
    obs = FakeObs(program="Stint")
    obs.director_muted = director_muted
    r = _relay_for_covers(obs)
    r._signal_hold, r._hold, r._rebuilds = True, {}, []
    r._rebuild_lock, r._last_freeze_ts = m.threading.Lock(), None
    r.live_feed = lambda: "A"
    r._note_obs_splice = lambda feed: None
    r._record_event = lambda now, typ, label, md: obs.calls.append(("event", typ))

    class Srv:
        def consumer_backlog(self, now):
            return 0.0

        def consumer_attached(self):
            return True
    f = r.feeds["A"]
    f.fanout_server = Srv()
    f.last_byte_ts, f.steady_since = 100.0, 100.0
    f.current_channel = lambda: (url, 2)
    f._obs_reconnect = lambda: r._rebuilds.append("A")
    return r, obs


def t_hold_engage_mutes_and_records():
    r, obs = _hold_relay()
    r._hold_tick(101.5)                          # 1.5 s without bytes, reserve 0
    assert r._hold_active("A") and ("mute", "Feed A", True) in obs.calls
    assert ("event", "signal_hold") in obs.calls


def t_hold_release_rebuilds_and_unmutes_once_obs_plays():
    r, obs = _hold_relay()
    r._hold_tick(101.5)
    f = r.feeds["A"]
    f.last_byte_ts, f.steady_since = 107.0, 101.9
    r._hold_tick(107.1)
    assert r._rebuilds == ["A"] and not r._hold_active("A")
    assert ("mute", "Feed A", False) not in obs.calls     # still under the cover
    assert r._last_freeze_ts is not None                    # claimed the rebuild cooldown
    r._play["A"] = {"cursor": 5000, "since": 107.5}         # OBS plays again
    r._hold_tick(109.0)
    assert ("mute", "Feed A", False) in obs.calls
    assert ("event", "signal_hold_end") in obs.calls


def t_hold_release_keeps_director_mute():
    r, obs = _hold_relay(director_muted=True)
    r._hold_tick(101.5)
    assert ("mute", "Feed A", True) not in obs.calls      # already muted: not ours
    f = r.feeds["A"]
    f.last_byte_ts, f.steady_since = 107.0, 101.9
    r._play["A"] = {"cursor": 5000, "since": 100.0}
    r._hold_tick(107.1)
    r._hold_tick(109.0)
    assert ("mute", "Feed A", False) not in obs.calls


def t_hold_cancel_restores_only_our_mute():
    r, obs = _hold_relay()
    r._hold_tick(101.5)
    r.cancel_signal_hold("A", "reset")
    assert not r._hold_active("A") and ("mute", "Feed A", False) in obs.calls
    assert r._rebuilds == []


def t_hold_cancels_when_the_stint_changes():
    r, obs = _hold_relay()
    r._hold_tick(101.5)
    r.feeds["A"].current_channel = lambda: ("https://www.youtube.com/watch?v=y", 4)
    r._hold_tick(102.0)
    assert not r._hold_active("A") and ("mute", "Feed A", False) in obs.calls


def t_hold_never_for_a_local_stint():
    r, obs = _hold_relay(url="local:")
    r._hold_tick(101.5)
    assert not r._hold_active("A")


def t_hold_keeps_the_cover_up():
    r, obs = _hold_relay()
    r._play["A"] = {"cursor": 9000, "since": 90.0}          # OBS still decodes old frames
    r._hold_tick(101.5)
    assert r._hold_active("A")
    obs.calls.clear()
    r._cover_tick(101.6)
    assert _enables(obs, scene="Stint", source="Feed A Cover", on=True)


def t_forced_pre_show_resets_the_play_proof():
    # The rebuild runs on a thread: the next tick must not lower the cover on the
    # pre-rebuild play position before OBS has reopened the feed.
    obs = FakeObs(program="Stint")
    r = _relay_for_covers(obs)
    _play(obs, r, "Feed A", m.COVER_PLAY_S, 100.0)
    r._pre_show_cover("A", force=True)
    assert r._obs_playing_s("A", 104.0) is None


def _held_relay_without_the_rest():
    r, _obs = _hold_relay()
    r._hold_tick(101.5)
    assert r._hold_active("A")
    return r


def t_backlog_shed_stands_down_during_a_hold():
    r = _held_relay_without_the_rest()
    r._backlog_shed = True                     # anything past the hold check would need
    r._backlog_shed_tick(200.0)                # attributes this bare relay does not have


def t_freeze_detector_stands_down_during_a_hold():
    r = _held_relay_without_the_rest()
    r._freeze_detect = True
    r._freeze_tick(200.0)


def t_director_reset_cancels_the_hold_and_raises_the_cover_first():
    r, obs = _hold_relay()
    r._hold_tick(101.5)
    order = []
    r.feeds["A"].port = 53001
    r.feeds["A"].before_obs_reconnect = lambda: order.append("cover")
    obs.release_feed_inputs = lambda ports=None: (order.append(("rebuild", ports)) or (["Feed A"], ""))
    assert r.obs_feed_reset("A") == (["Feed A"], "")
    assert order == ["cover", ("rebuild", [53001])]
    assert not r._hold_active("A") and ("mute", "Feed A", False) in obs.calls


CONFIG = ("Team,Number,Race Control,Signal Hold\n"
          "A,1,Driver Swaps,Signal is being restored\nB,2,,\n")


def t_parse_signal_hold_text():
    assert m.parse_signal_hold_text(CONFIG) == "Signal is being restored"
    assert m.parse_signal_hold_text("Team,Signal Hold\nA,\n") is None
    assert m.parse_signal_hold_text("Team\nA\n") is None
    assert m.parse_signal_hold_text("") is None


class _Texts:
    def __init__(self, text):
        self.text = text

    def signal_hold_text(self):
        return self.text


def t_hud_notice_only_while_the_on_air_feed_holds():
    r, _obs = _hold_relay()
    texts = _Texts("Signal is being restored")
    assert r.signal_hold_notice(texts) is None
    r._hold_tick(101.5)
    assert r.signal_hold_notice(texts) == "Signal is being restored"
    assert r.signal_hold_notice(None) is None


def t_hud_notice_absent_text_keeps_sheet_value():
    r, _obs = _hold_relay()
    r._hold_tick(101.5)
    assert r.signal_hold_notice(_Texts(None)) is None


def t_hud_data_override_applies_the_notice():
    data = {"raceControl": "Driver Swaps", "stint": "S3"}
    assert m.apply_signal_hold_notice(dict(data), "Signal is being restored")["raceControl"] \
        == "Signal is being restored"
    assert m.apply_signal_hold_notice(dict(data), None) == data


def t_hud_source_carries_the_signal_hold_text():
    h = m.HudSource.__new__(m.HudSource)
    h.lock = m.threading.Lock()
    h._signal_hold_text = "Signal is being restored"
    assert h.signal_hold_text() == "Signal is being restored"


def t_signal_hold_status():
    r, obs = _hold_relay()
    assert r.signal_hold_status("A") == {"active": False, "since": None}
    r._hold_tick(101.5)
    st = r.signal_hold_status("A")
    assert st["active"] is True and st["since"] is not None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

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
    st = U(st, ("OBS_MEDIA_STATE_PLAYING", 2000), 11.0)
    assert m.media_playing_s(st, 11.0) == 0.0                  # advanced: playing since now
    st = U(st, ("OBS_MEDIA_STATE_PLAYING", 3000), 12.0)
    assert m.media_playing_s(st, 12.0) == 1.0
    st = U(st, ("OBS_MEDIA_STATE_PLAYING", 3000), 13.0)        # frozen
    assert m.media_playing_s(st, 13.0) is None
    assert m.media_stalled_s(st, 13.5) == 0.5                  # stalled since 13.0
    st = U(st, ("OBS_MEDIA_STATE_PLAYING", 3000), 14.0)
    assert m.media_stalled_s(st, 14.0) == 1.0                  # still since 13.0
    st = U(st, ("OBS_MEDIA_STATE_PLAYING", 3500), 14.5)
    assert m.media_stalled_s(st, 14.5) is None                 # advancing again
    st = U(st, ("OBS_MEDIA_STATE_PLAYING", 300), 14.6)         # reopened: cursor restarted
    assert m.media_playing_s(st, 14.6) is None
    assert m.media_stalled_s(st, 14.6) is None                 # a reopen is not a stall
    st = U(st, ("OBS_MEDIA_STATE_STOPPED", 900), 15.0)
    assert m.media_playing_s(st, 15.0) is None
    assert m.media_stalled_s(st, 15.5) is None                 # nothing played since it
    st = U(st, (None, None), 16.0)                             # OBS unreachable: unknown
    assert m.media_playing_s(st, 16.0) is None and m.media_stalled_s(st, 16.0) is None


def t_a_crawling_cursor_is_not_playing():
    # Live test 2026-10-18: after a rejoin OBS stuttered for 2-3 s; a cursor that moves
    # but far slower than real time is a stutter, not playback.
    U = m.media_play_update
    st = U(None, ("OBS_MEDIA_STATE_PLAYING", 1000), 10.0)
    st = U(st, ("OBS_MEDIA_STATE_PLAYING", 2000), 11.0)          # real time
    assert m.media_playing_s(st, 11.0) == 0.0
    st = U(st, ("OBS_MEDIA_STATE_PLAYING", 2300), 12.0)          # 0.3 s in 1 s
    assert m.media_playing_s(st, 12.0) is None
    st = U(st, ("OBS_MEDIA_STATE_PLAYING", 3250), 13.0)
    st = U(st, ("OBS_MEDIA_STATE_PLAYING", 4200), 14.0)
    assert m.media_playing_s(st, 14.0) == 1.0                    # playing again since 13.0


def t_a_fast_forwarding_cursor_is_not_stable_play():
    # Live test 2026-10-18: after a long stall OBS caught up audio at high speed.
    U = m.media_play_update
    st = U(None, ("OBS_MEDIA_STATE_PLAYING", 1000), 10.0)
    st = U(st, ("OBS_MEDIA_STATE_PLAYING", 3000), 11.0)          # 2 s in 1 s
    assert m.media_playing_s(st, 11.0) is None
    st = U(st, ("OBS_MEDIA_STATE_PLAYING", 4000), 12.0)
    assert m.media_playing_s(st, 12.0) == 0.0


def t_feed_restart_rejoin_is_skipped_while_a_hold_owns_obs():
    # Live test 2026-10-18: a watchdog restart rejoined OBS 4 s before the hold's own
    # release rejoin; between the two OBS fast-forwarded, then resynced again.
    import threading as th
    f = m.Feed.__new__(m.Feed)
    f.name, f.log, f.stop, f.phase = "A", LOG, False, "serving"
    f.advance, f.serve_generation = th.Event(), 1
    f.defer_rejoin = lambda: True
    calls = []
    f._obs_reconnect_now = lambda: calls.append("rebuild")
    t = f._obs_rejoin_after_prefetch(0, 0.0, sleep=lambda _s: None)
    t.join(2)
    assert calls == []
    f.defer_rejoin = lambda: False
    f._obs_rejoin_after_prefetch(0, 0.0, sleep=lambda _s: None).join(2)
    assert calls == ["rebuild"]


def t_motion_needs_changing_frames():
    # Live test 2026-10-18: after a rejoin OBS's cursor ran in real time on the audio
    # while the picture still stood; only changing frames prove a running video.
    M = m.motion_update
    st = M(None, b"a", 10.0)
    assert m.motion_s(st, 10.0) is None                         # one frame proves nothing
    st = M(st, b"b", 11.0)
    assert m.motion_s(st, 11.0) == 0.0
    st = M(st, b"c", 12.0)
    assert m.motion_s(st, 12.0) == 1.0
    st = M(st, b"c", 13.0)                                     # frozen picture
    assert m.motion_s(st, 13.0) is None
    st = M(st, None, 14.0)                                     # no screenshot
    assert m.motion_s(st, 14.0) is None


def t_cover_stays_while_the_picture_stands_despite_a_running_cursor():
    obs = FakeObs(program="Stint")
    obs.frame = lambda name: b"frozen"
    r = _relay_for_covers(obs)
    r._cover_tick(100.0)
    _play(obs, r, "Feed A", m.COVER_PLAY_S + 2, 101.0)
    assert not _enables(obs, scene="Stint", source="Feed A Cover", on=False)


def t_opening_obs_is_never_a_stall():
    # Live test 2026-10-18: at relay start and after a RESET OBS sits still while it
    # opens the source; that held the feed at once and for good.
    U = m.media_play_update
    st = U(None, ("OBS_MEDIA_STATE_PLAYING", 0), 10.0)
    for t in (11.0, 12.0, 13.0):
        st = U(st, ("OBS_MEDIA_STATE_PLAYING", 0), t)
    assert m.media_stalled_s(st, 13.0) is None
    st = U(st, ("OBS_MEDIA_STATE_PLAYING", 1000), 14.0)
    st = U(st, ("OBS_MEDIA_STATE_PLAYING", 1000), 15.0)         # played, then stopped
    assert m.media_stalled_s(st, 16.0) == 1.0


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

    def get_source_screenshot(self, name, width=64):
        self._shots = getattr(self, "_shots", 0) + 1
        frame = getattr(self, "frame", None)
        return (frame(name) if frame else f"{name}-{self._shots}".encode()), ""

    def get_media_status(self, name):
        c = self.cursor.get(name)
        return (("OBS_MEDIA_STATE_PLAYING", c) if c is not None else (None, None)), ""

    def get_scene_item_enabled(self, scene, source):
        getattr(self, "reads", []).append("item_enabled")
        shown = self.feed_shown(scene, source) if callable(self.feed_shown) else self.feed_shown
        return shown, ""

    def get_scene_items(self, scene):
        getattr(self, "reads", []).append("scene_items")
        out = {}
        for src in ("Feed A", "Feed B", "Feed A Cover", "Feed B Cover"):
            shown = self.feed_shown(scene, src) if callable(self.feed_shown) else self.feed_shown
            out[src] = bool(shown)
        return out, ""

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
    r._motion = {}
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
    for i in range(int(start_s) + 3):           # cursor proof, then thumbnail proof
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


def t_forced_pre_show_keeps_the_still_viewers_already_see():
    # Live test 2026-10-18: the release rejoin replaced the hold's still with a new one.
    obs = FakeObs(program="Stint")
    r = _relay_for_covers(obs, stills=True)
    r._cover_tick(100.0)                                       # raised: one snapshot
    assert r.cover_status("A") == "shown"
    obs.calls.clear()
    r._pre_show_cover("A", force=True)
    assert ("file", "Feed A Cover") not in obs.calls


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


def t_a_cut_pre_show_is_not_undone_by_the_next_tick():
    # Review 2026-10-18: the cut raises the cover before the feed item is enabled; a tick
    # in between saw the item hidden and lowered the cover again.
    obs = FakeObs(program="Stint", feed_shown=lambda scene, src: src != "Feed B")
    r = _relay_for_covers(obs)
    r._cover_tick(100.0)
    obs.calls.clear()
    r._pre_show_cover("B", "Stint")
    r._cover_tick(time_now := m.time.monotonic())
    assert not _enables(obs, scene="Stint", source="Feed B Cover", on=False)
    r._cover_tick(time_now + m.COVER_PIN_S + 0.1)          # pin over: the rule applies
    assert _enables(obs, scene="Stint", source="Feed B Cover", on=False)


def t_a_poll_racing_a_rebuild_does_not_restore_the_old_play_proof():
    # Review 2026-10-18: a poll that started before a forced pre-show wrote the
    # pre-rebuild play state back after it.
    obs = FakeObs(program="Stint")
    r = _relay_for_covers(obs)
    _play(obs, r, "Feed A", m.COVER_PLAY_S, 100.0)
    real = obs.get_media_status

    def status_then_rebuild(name):
        out = real(name)
        if name == "Feed A":
            r._pre_show_cover("A", force=True)
        return out
    obs.get_media_status = status_then_rebuild
    obs.cursor["Feed A"] += 1000
    r._poll_play(110.0)
    assert r._obs_playing_s("A", 110.0) is None


def t_cover_tap_respawns_after_ffmpeg_exits():
    import tempfile, time
    path = os.path.join(tempfile.mkdtemp(), "covers", "feed-a.latest.jpg")
    spawns = []

    def spawn(tap):
        spawns.append(1)
        chunks = [] if len(spawns) == 1 else [b"\xff\xd8two\xff\xd9"]
        return _fake_ffmpeg(chunks)(tap)
    tap = m.FeedCoverTap(_IdleRing(), path, LOG, spawn=spawn, respawn_s=0.05).start()
    for _ in range(200):
        if tap.last_write_ts is not None:
            break
        time.sleep(0.02)
    tap.stop()
    assert len(spawns) >= 2 and tap.last_write_ts is not None


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
# Live test 2026-10-18: a 7 s source stall never showed, OBS played it out of its own
# buffer. Viewers see a stall only when OBS's play position stops, so that (with the
# reserve empty: the source, not OBS, is the cause) is what engages a hold.


def _s(state, reserve, stalled, age, steady, cancel=False, seg=1.0, held=0.0):
    # `reserve`: seconds of data the relay still holds for OBS; 0 means starved.
    starved = None if reserve is None else reserve <= 0.25
    return S(state, starved=starved, obs_stalled_s=stalled, last_byte_age_s=age,
             steady_s=steady, held_s=held, segment_s=seg, cancel=cancel)


def t_cadence_thresholds_follow_the_segment_duration():
    # YouTube normal latency arrives in 5 s segments: 2-5 s between bytes is normal.
    assert m.source_silent_s(1.0) == 2.0 and m.source_silent_s(2.0) == 3.0
    assert m.source_silent_s(5.0) == 7.5 and m.source_silent_s(None) == 7.5
    assert m.release_steady_s(1.0) == 5.0 and m.release_steady_s(5.0) == 10.0


def t_hold_a_normal_latency_burst_gap_is_not_silence():
    # 5 s segments: a 4 s byte gap is the normal cadence, even while OBS hiccups.
    assert _s("live", 1.0, 1.2, 4.0, 0.0, seg=5.0) == ("live", None)
    assert _s("live", 1.0, 1.2, 8.0, 0.0, seg=5.0) == ("hold", "engage")


def t_hold_releases_on_the_streams_own_cadence():
    assert _s("hold", 3.0, 6.0, 4.0, 10.0, seg=5.0) == ("live", "release")
    assert _s("hold", 3.0, 6.0, 4.0, 9.0, seg=5.0) == ("hold", None)


def t_hold_never_outlives_its_ceiling():
    assert _s("hold", 0.0, 9.0, 9.0, 0.0, held=m.HOLD_MAX_S) == ("live", "timeout")
    assert _s("hold", 0.0, 9.0, 9.0, 0.0, held=m.HOLD_MAX_S - 1) == ("hold", None)


def t_hold_source_gap_obs_bridges_never_engages():
    assert _s("live", 0.0, None, 7.0, 0.0) == ("live", None)      # OBS still plays
    assert _s("live", 1.2, 1.5, 0.2, 9.0) == ("live", None)       # OBS froze, data there


def t_hold_engages_when_obs_stops_while_the_source_is_silent():
    # Live test 2026-10-18 (15 s stall): OBS stopped playing AND stopped reading, so it
    # never reached the newest byte; the source had sent nothing for 14 s.
    assert _s("live", 13.8, 1.2, 14.0, 0.0) == ("hold", "engage")
    assert _s("live", 13.8, 1.2, m.source_silent_s(1.0) - 0.5, 0.0) == ("live", None)


def t_hold_engages_when_obs_stops_on_an_empty_reserve():
    assert _s("live", 0.1, 1.1, 3.0, 0.0) == ("hold", "engage")
    assert _s("live", 0.1, 0.5, 3.0, 0.0) == ("live", None)
    assert _s("live", None, 9.0, 0.5, 0.0) == ("live", None)   # nothing proves a source cause


def t_hold_single_burst_does_not_release():
    assert _s("hold", 2.0, 3.0, 0.1, 1.0) == ("hold", None)


def t_hold_releases_after_steady_window():
    assert _s("hold", 3.0, 6.0, 0.1, 5.0) == ("live", "release")


def t_hold_cancel_wins():
    assert _s("hold", 0.0, 9.0, 9.0, 0.0, cancel=True) == ("live", "cancel")
    assert _s("live", 0.0, 9.0, 9.0, 0.0, cancel=True) == ("live", None)


def t_hold_suzuka_2026_10_17_sequence():
    """21:07: gaps up to 5.7 s on a ~1 s reserve: OBS stops, holds, no release mid-stall."""
    st, seen = "live", []
    for reserve, stalled, age, steady in [(1.2, None, 0.2, 30), (0.3, None, 2.0, 0),
                                          (0.0, 1.2, 3.2, 0), (0.0, 4.0, 5.7, 0),
                                          (1.9, 5.0, 0.1, 0.5), (0.2, 6.0, 1.6, 0),
                                          (2.5, 7.0, 0.1, 2.0), (3.0, 8.0, 0.1, 5.2)]:
        st, a = _s(st, reserve, stalled, age, steady)
        seen.append(a)
    assert seen == [None, None, "engage", None, None, None, None, "release"], seen


def t_steady_since_update():
    assert m.steady_since_update(None, None, 10.0) == 10.0
    assert m.steady_since_update(5.0, 9.5, 10.0, gap_s=1.5) == 5.0    # 0.5 s gap: steady
    assert m.steady_since_update(5.0, 8.0, 10.0, gap_s=1.5) == 10.0   # 2.0 s gap: restart
    assert m.steady_since_update(5.0, 6.0, 10.0, gap_s=7.5) == 5.0    # 4 s at 5 s segments


def t_feed_steady_gap_follows_its_measured_segments():
    f = m.Feed.__new__(m.Feed)
    f.target_duration_s = 5.0
    assert f.steady_gap_s() == 7.5
    f.target_duration_s = None
    assert f.steady_gap_s() == 7.5                               # unknown: assume normal


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

    class Srv:                              # the FeedFanoutServer methods the hold uses
        def consumer_starved(self):
            return True
    f = r.feeds["A"]
    f.fanout_server = Srv()
    f.consumer_attached = lambda: True      # a Feed method, not the server's
    f.last_byte_ts, f.steady_since = 100.0, 100.0
    # OBS stopped playing Feed A at 100.0 (its buffer ran dry).
    r._play["A"] = {"cursor": 9000, "since": None, "stalled_since": 100.0, "played": True}
    f.target_duration_s = 1.0                # an ultra-low-latency source, like Suzuka
    f.current_channel = lambda: (url, 2)
    f._obs_reconnect = lambda: r._rebuilds.append("A")
    return r, obs


def t_hold_engage_mutes_and_records():
    r, obs = _hold_relay()
    r._hold_tick(101.5)                          # OBS stopped 1.5 s ago, reserve 0
    assert r._hold_active("A") and ("mute", "Feed A", True) in obs.calls
    assert ("event", "signal_hold") in obs.calls


def t_hold_release_rebuilds_and_unmutes_once_obs_plays():
    r, obs = _hold_relay()
    r._hold_tick(101.5)
    f = r.feeds["A"]
    f.last_byte_ts, f.steady_since = 107.0, 101.9
    r._hold_tick(107.1)
    assert r._rebuilds == ["A"] and r._hold_active("A")    # settling under the cover
    assert ("mute", "Feed A", False) not in obs.calls
    assert r._last_freeze_ts is not None                    # claimed the rebuild cooldown
    r._play["A"] = {"cursor": 5000, "since": 107.5}         # OBS plays again
    r._motion["A"] = {"digest": "x", "since": 107.5}
    r._hold_tick(107.5 + m.HOLD_SETTLE_S)
    assert ("mute", "Feed A", False) in obs.calls and not r._hold_active("A")
    assert ("event", "signal_hold_end") in obs.calls


def t_hold_release_keeps_director_mute():
    r, obs = _hold_relay(director_muted=True)
    r._hold_tick(101.5)
    assert ("mute", "Feed A", True) not in obs.calls      # already muted: not ours
    f = r.feeds["A"]
    f.last_byte_ts, f.steady_since = 107.0, 101.9
    r._play["A"] = {"cursor": 5000, "since": 100.0}
    r._motion["A"] = {"digest": "x", "since": 100.0}
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


class _Ring:
    def __init__(self, live):
        self.live = live

    def live_offset(self):
        return self.live


def t_consumer_starved_means_obs_has_read_everything():
    # Live test 2026-10-18: during a stall the backlog (the AGE of OBS's position) grows
    # to 20 s, so it cannot say "nothing left"; the read position against the newest
    # byte can.
    srv = m.FeedFanoutServer("127.0.0.1", 0, ring=_Ring(5000), log=LOG)
    assert srv.consumer_starved() is None                      # no consumer
    srv._consumers[1] = {"cursor": 4000, "cycle_ts": 1.0}
    assert srv.consumer_starved() is False                     # 1000 bytes still to read
    srv._consumers[1]["cursor"] = 5000
    assert srv.consumer_starved() is True


def t_hold_fakes_match_the_real_classes():
    # Live test 2026-10-18: the hold called consumer_attached() on the fan-out server,
    # where it does not exist; a fake that had it hid the AttributeError every tick.
    for name in ("consumer_starved", "consumer_backlog"):
        assert hasattr(m.FeedFanoutServer, name), name
    for name in ("consumer_attached", "current_channel", "_obs_reconnect"):
        assert hasattr(m.Feed, name), name
    assert not hasattr(m.FeedFanoutServer, "consumer_attached")


def t_hold_release_keeps_cover_mute_and_notice_until_obs_settles():
    # Live test 2026-10-18: lowering the cover after 1 s of play showed a 2-3 s stutter
    # with choppy sound right after the rejoin.
    r, obs = _hold_relay()
    texts = _Texts("Technical Difficulties")
    r._hold_tick(101.5)
    f = r.feeds["A"]
    f.last_byte_ts, f.steady_since = 107.0, 101.9
    r._hold_tick(107.1)                                    # release: rebuild
    assert r._rebuilds == ["A"]
    r._play["A"] = {"cursor": 5000, "since": 107.5, "played": True}
    r._motion["A"] = {"digest": "x", "since": 107.5}
    r._hold_tick(109.0)                                    # 1.5 s of play: still settling
    assert r._hold_active("A") and ("mute", "Feed A", False) not in obs.calls
    assert r.signal_hold_notice(texts) == "Technical Difficulties"
    r._hold_tick(107.5 + m.HOLD_SETTLE_S)                  # settled
    assert not r._hold_active("A") and ("mute", "Feed A", False) in obs.calls
    assert r.signal_hold_notice(texts) is None


def t_hold_settle_has_a_ceiling():
    r, obs = _hold_relay()
    r._hold_tick(101.5)
    f = r.feeds["A"]
    f.last_byte_ts, f.steady_since = 107.0, 101.9
    r._hold_tick(107.1)
    r._play["A"] = None                                    # the rebuild reopened OBS
    r._hold_tick(107.1 + m.HOLD_SETTLE_MAX_S + 1)          # OBS never settled
    assert not r._hold_active("A") and ("mute", "Feed A", False) in obs.calls


def t_hold_survives_obs_dropping_its_connection():
    # Live test 2026-10-18: during an 18 s stall OBS closed its socket after ~5 s; the
    # hold cancelled and the notice vanished mid-outage. OBS reconnects by itself.
    r, obs = _hold_relay()
    r._hold_tick(101.5)
    f = r.feeds["A"]
    f.consumer_attached = lambda: False
    f.fanout_server.consumer_starved = lambda: None
    f.phase = "connecting"                      # the stall watchdog restarted the serve
    r._hold_tick(107.0)
    assert r._hold_active("A")
    f.paused = True                             # the director stopped the feed: cancel
    r._hold_tick(108.0)
    assert not r._hold_active("A")


def _make_like_a(r, key, url="https://www.youtube.com/watch?v=b", idx=3):
    a, f = r.feeds["A"], r.feeds[key]
    f.fanout_server, f.consumer_attached = a.fanout_server, a.consumer_attached
    f.last_byte_ts, f.steady_since, f.target_duration_s = 200.0, 200.0, 1.0
    f.current_channel = lambda: (url, idx)
    f._obs_reconnect = lambda: r._rebuilds.append(key)


def t_handover_ends_a_hold_on_the_outgoing_feed():
    # Review 2026-10-18: after NEXT the held feed is off air and nothing ended its hold,
    # so its frozen still stayed up in the next Splitscreen.
    r, obs = _hold_relay()
    r._hold_tick(101.5)
    assert r._hold_active("A")
    _make_like_a(r, "B")
    r.live_feed = lambda: "B"
    r._hold_tick(102.0)
    assert not r._hold_active("A") and ("mute", "Feed A", False) in obs.calls


def t_handover_ends_a_settle_on_the_outgoing_feed():
    r, obs = _hold_relay()
    r._hold_tick(101.5)
    f = r.feeds["A"]
    f.last_byte_ts, f.steady_since = 107.0, 101.9
    r._hold_tick(107.1)                                    # released, settling
    _make_like_a(r, "B")
    r.live_feed = lambda: "B"
    r._hold_tick(108.0)
    assert not r._hold_active("A") and ("mute", "Feed A", False) in obs.calls


def t_a_reset_during_engage_is_not_undone():
    # Review 2026-10-18: the tick wrote the hold state after the engage's OBS calls, so a
    # RESET landing in between was overwritten and the hold came back without a clock.
    r, obs = _hold_relay()
    real = obs.get_input_mute

    def mute_then_reset(name):
        r.cancel_signal_hold("A", "reset")
        return real(name)
    obs.get_input_mute = mute_then_reset
    r._hold_tick(101.5)
    assert r._hold["A"]["state"] == "live" or r._hold["A"].get("held_mono") is not None


def t_relay_start_registers_every_fanout_server():
    # Review 2026-10-18: an edit moved the append under `if self._signal_hold:`.
    import inspect
    src = inspect.getsource(m.Relay.start)
    i = src.index("f.fanout_server = srv")
    j = src.index("self._fanout_servers.append(srv)")
    assert 0 < j - i < 200 and src.index("if self._covers:") > j, src[i:j + 40]


# --- #923: follow-ups from the final review -------------------------------------------

def _released(r, t=107.1):
    r._hold_tick(101.5)
    f = r.feeds["A"]
    f.last_byte_ts, f.steady_since = t - 0.1, 101.9
    r._hold_tick(t)
    r._play["A"] = None                                    # the rejoin reopened OBS
    return f


def t_settle_polls_obs_itself_when_covers_are_off():
    r, obs = _hold_relay()
    r._covers = False
    r._play["A"] = None                                    # only OBS's own samples count
    for t, c in ((98.0, 1000), (99.0, 2000), (100.0, 3000), (100.5, 3000), (101.6, 3000)):
        obs.cursor["Feed A"] = c                           # played, then froze at 100.5
        r._hold_tick(t)
    assert r._hold_active("A")
    f = r.feeds["A"]
    f.last_byte_ts, f.steady_since = 107.0, 101.9
    r._hold_tick(107.1)                                    # release
    r._play["A"] = None
    for i in range(1, 8):
        obs.cursor["Feed A"] = 1000 * i
        r._hold_tick(107.1 + i)
    assert ("mute", "Feed A", False) in obs.calls and not r._hold_active("A")


def t_cover_tick_reads_each_scene_once():
    obs = FakeObs(program="Stint")
    r = _relay_for_covers(obs)
    r._cover_tick(100.0)
    obs.calls.clear()
    obs.reads = []
    r._cover_tick(101.0)
    assert obs.reads.count("scene_items") == 2 and "item_enabled" not in obs.reads


def t_director_mute_during_a_hold_is_left_alone():
    r, obs = _hold_relay()
    r._hold_tick(101.5)
    r.note_director_mute("Feed A", True)                   # the director mutes on purpose
    _released(r)
    r._play["A"] = {"cursor": 5000, "since": 107.5, "played": True}
    r._motion["A"] = {"digest": "x", "since": 107.5}
    r._hold_tick(107.5 + m.HOLD_SETTLE_S)
    assert ("mute", "Feed A", False) not in obs.calls


def t_director_unmute_during_a_hold_is_not_redone():
    r, obs = _hold_relay()
    r._hold_tick(101.5)
    r.note_director_mute("Feed A", False)                  # the director wants the sound
    obs.calls.clear()
    _released(r)
    r._play["A"] = {"cursor": 5000, "since": 107.5, "played": True}
    r._motion["A"] = {"digest": "x", "since": 107.5}
    r._hold_tick(107.5 + m.HOLD_SETTLE_S)
    assert not [c for c in obs.calls if c[0] == "mute"]


def t_a_static_picture_counts_as_playing_after_the_fallback():
    obs = FakeObs(program="Stint")
    r = _relay_for_covers(obs)
    r._play["A"] = {"cursor": 9000, "since": 100.0, "played": True}
    r._motion["A"] = {"digest": "x", "since": None}        # the picture never changes
    assert r._obs_playing_s("A", 100.0 + m.MOTION_FALLBACK_S - 1) is None
    assert r._obs_playing_s("A", 100.0 + m.MOTION_FALLBACK_S) is not None


def t_status_reports_the_settle_phase():
    r, obs = _hold_relay()
    assert r.signal_hold_status("A")["phase"] is None
    r._hold_tick(101.5)
    assert r.signal_hold_status("A")["phase"] == "hold"
    _released(r)
    st = r.signal_hold_status("A")
    assert st["active"] is True and st["phase"] == "settling"


def t_hold_times_out_and_warns():
    r, obs = _hold_relay()
    r._hold_tick(101.5)
    assert r._hold_active("A")
    r._hold_tick(101.5 + m.HOLD_MAX_S + 1)
    assert r._hold["A"]["state"] == "live" and r._rebuilds == ["A"]   # released, settling


def t_hold_waits_while_obs_still_plays_its_buffer():
    r, obs = _hold_relay()
    r._play["A"] = {"cursor": 9000, "since": 90.0, "stalled_since": None}
    r._hold_tick(105.0)                          # 5 s without bytes, OBS still plays
    assert not r._hold_active("A")


def t_hold_never_for_a_local_stint():
    r, obs = _hold_relay(url="local:")
    r._hold_tick(101.5)
    assert not r._hold_active("A")


def t_hold_keeps_the_cover_up():
    r, obs = _hold_relay()
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
    assert r.signal_hold_status("A") == {"active": False, "since": None, "phase": None}
    r._hold_tick(101.5)
    st = r.signal_hold_status("A")
    assert st["active"] is True and st["since"] is not None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

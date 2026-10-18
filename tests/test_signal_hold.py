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
    base = dict(enabled=True, serving=True, consumer_flowing_s=2.0,
                hold_active=False, feed_item_shown=True)
    assert m.cover_visible(**base) is False                                   # playing: down
    assert m.cover_visible(**{**base, "consumer_flowing_s": 1.0}) is True     # not ready yet
    assert m.cover_visible(**{**base, "consumer_flowing_s": None}) is True    # no consumer
    assert m.cover_visible(**{**base, "serving": False}) is True
    assert m.cover_visible(**{**base, "hold_active": True}) is True


def t_cover_hidden_when_feed_item_hidden():
    # A cover above a hidden feed item would cover the on-air feed in the Stint scene.
    assert m.cover_visible(enabled=True, serving=False, consumer_flowing_s=None,
                           hold_active=True, feed_item_shown=False) is False


def t_cover_disabled_is_never_shown():
    assert m.cover_visible(enabled=False, serving=False, consumer_flowing_s=None,
                           hold_active=True, feed_item_shown=True) is False


def t_consumer_flowing_s_tracks_first_and_last_send():
    srv = m.FeedFanoutServer("127.0.0.1", 0, ring=None, log=LOG)
    assert srv.consumer_flowing_s(100.0) is None
    srv._consumers[1] = {"first_send_ts": 98.0, "last_send_ts": 99.8}
    assert srv.consumer_flowing_s(100.0) == 2.0
    srv._consumers[1]["last_send_ts"] = 98.5            # silent for 1.5 s: not flowing
    assert srv.consumer_flowing_s(100.0) is None
    srv._consumers[1]["last_send_ts"] = 100.4           # resumes: the count restarts
    assert srv.consumer_flowing_s(100.5) is None
    srv._note_send(1, 100.5)
    assert srv.consumer_flowing_s(101.0) == 0.5


def t_note_send_starts_and_extends_the_flow():
    srv = m.FeedFanoutServer("127.0.0.1", 0, ring=None, log=LOG)
    srv._consumers[7] = {}
    srv._note_send(7, 10.0)
    srv._note_send(7, 10.5)
    assert srv._consumers[7]["first_send_ts"] == 10.0 and srv._consumers[7]["last_send_ts"] == 10.5
    srv._note_send(99, 11.0)                             # a consumer already gone: no-op
    assert 99 not in srv._consumers


class FakeObs:
    """The obs_ws calls the cover and hold ticks make, recorded; never a socket."""

    def __init__(self, missing=False, feed_shown=True):
        self.calls, self.missing, self.feed_shown = [], missing, feed_shown
        self.director_muted = False

    def ensure_cover_items(self, feed, cover, path, scenes=None):
        self.calls.append(("ensure", cover))
        state = "no-feed" if self.missing else "ok"
        return {s: state for s in (scenes or ("Stint", "Splitscreen"))}, ""

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


def _relay_for_covers(obs, flowing=None, serving=True):
    import tempfile
    r = m.Relay.__new__(m.Relay)            # no network, no threads
    r._obs, r.obs_note, r.fanout, r.solo = obs, "", True, False
    r._covers = True
    r._cover = {}
    r._cover_dir = tempfile.mkdtemp()
    r._cover_ensured_ts = None

    class Srv:
        def __init__(self):
            self.flowing = flowing

        def consumer_flowing_s(self, now):
            return self.flowing

    class F:
        def __init__(self, name):
            self.name, self.paused = name, False
            self.phase = "serving" if serving else "idle"
            self.fanout_server, self.cover_tap = Srv(), None
    r.feeds = {"A": F("A"), "B": F("B")}
    return r


def _enables(obs, **match):
    return [c for c in obs.calls if c[0] == "enable"
            and all(c[{"scene": 1, "source": 2, "on": 3}[k]] == v for k, v in match.items())]


def t_cover_tick_shows_until_flowing_then_hides():
    obs = FakeObs()
    r = _relay_for_covers(obs, flowing=0.5)
    r._cover_tick(100.0)
    assert _enables(obs, scene="Stint", source="Feed A Cover", on=True)
    assert ("file", "Feed A Cover") in obs.calls             # fresh still before showing
    r.feeds["A"].fanout_server.flowing = 2.0
    obs.calls.clear()
    r._cover_tick(101.0)
    assert _enables(obs, scene="Stint", source="Feed A Cover", on=False)
    assert ("file", "Feed A Cover") not in obs.calls         # no reload just to hide


def t_cover_tick_applies_only_on_change_and_reasserts():
    obs = FakeObs()
    r = _relay_for_covers(obs, flowing=None)
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
    assert [c for c in obs.calls if c[0] == "ensure"].count(("ensure", "Feed A Cover")) == 1


def t_cover_tick_hides_a_cover_whose_feed_item_is_hidden():
    obs = FakeObs(feed_shown=lambda scene, src: not (scene == "Stint" and src == "Feed B"))
    r = _relay_for_covers(obs, flowing=None)
    r._cover_tick(100.0)
    assert _enables(obs, scene="Stint", source="Feed B Cover", on=False)
    assert _enables(obs, scene="Splitscreen", source="Feed B Cover", on=True)


def t_pre_show_cover_only_when_the_feed_is_not_already_playing():
    obs = FakeObs()
    r = _relay_for_covers(obs, flowing=None)
    r._cover_tick(100.0)
    obs.calls.clear()
    r._pre_show_cover("A", "Stint")
    assert _enables(obs, scene="Stint", source="Feed A Cover", on=True)
    obs.calls.clear()
    r.feeds["A"].fanout_server.flowing = 3.0               # already live in Splitscreen
    r._pre_show_cover("A", "Stint")
    assert not obs.calls


def t_covers_enabled_env():
    assert m.covers_enabled({}) is True
    assert m.covers_enabled({"RACECAST_FEED_COVERS": "0"}) is False


def t_cover_status_field():
    obs = FakeObs()
    r = _relay_for_covers(obs, flowing=None)
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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

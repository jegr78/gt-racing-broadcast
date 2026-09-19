#!/usr/bin/env python3
"""Stdlib unit checks for the Director Panel live preview. Run: python3 tests/test_feed_preview.py"""
import importlib.util, json, os, threading, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "irofeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def t_preview_pull_streamlink_cmd_twitch():
    cmd = m.preview_pull_streamlink_cmd("https://twitch.tv/foo", "twitch",
                                        m.PREVIEW_QUALITY_TW)
    assert cmd == ["streamlink", "--stdout", "--",
                   "https://twitch.tv/foo", m.PREVIEW_QUALITY_TW]


def t_preview_pull_streamlink_cmd_youtube_has_ua_and_cookies():
    cmd = m.preview_pull_streamlink_cmd("https://hls.example/360.m3u8", "youtube",
                                        m.PREVIEW_QUALITY_YT,
                                        cookies="/tmp/yt.txt", user_agent="UA/1")
    assert "--http-header" in cmd and "User-Agent=UA/1" in cmd
    assert "--http-cookies-file" in cmd and "/tmp/yt.txt" in cmd
    assert cmd[-2:] == ["https://hls.example/360.m3u8", m.PREVIEW_QUALITY_YT]
    assert cmd[0] == "streamlink" and "--stdout" in cmd


def t_preview_ffmpeg_cmd_pinned():
    assert m.preview_ffmpeg_cmd(480) == [
        "ffmpeg", "-nostdin", "-loglevel", "info", "-i", "pipe:0",
        "-map", "0:v:0", "-vf", "fps=1,scale=480:-2", "-f", "mjpeg", "pipe:1",
        "-map", "0:a:0?", "-af", "ebur128", "-f", "null", "-"]


def t_split_mjpeg_frames_extracts_complete_jpegs():
    soi, eoi = b"\xff\xd8", b"\xff\xd9"
    a = soi + b"AAAA" + eoi
    b = soi + b"BBBB" + eoi
    frames, rem = m.split_mjpeg_frames(b"\x00\x00" + a + b + soi + b"CC")
    assert frames == [a, b]
    assert rem == soi + b"CC"          # incomplete trailing frame is kept


def t_split_mjpeg_frames_no_complete_frame():
    frames, rem = m.split_mjpeg_frames(b"\xff\xd8partial")
    assert frames == []
    assert rem == b"\xff\xd8partial"


def t_parse_ebur128_momentary():
    line = "[Parsed_ebur128_1 @ 0x55] t: 3   TARGET:-23 LUFS    M: -20.1 S: -22.0 ..."
    assert abs(m.parse_ebur128_momentary(line) - (-20.1)) < 1e-6
    assert m.parse_ebur128_momentary("frame= 10 fps=1.0") is None
    assert m.parse_ebur128_momentary("[Parsed_ebur128_1] M: -inf S: -inf") is None


def t_lufs_to_meter_maps_range():
    assert m.lufs_to_meter(None) == 0.0
    assert m.lufs_to_meter(-60.0) == 0.0          # at/below floor
    assert m.lufs_to_meter(-10.0) == 1.0          # at/above ceiling
    mid = m.lufs_to_meter(-35.0)                   # halfway (-60..-10)
    assert 0.49 < mid < 0.51




class _FakeProc:
    def __init__(self): self._alive = True
    def poll(self): return None if self._alive else 0
    def kill(self): self._alive = False
    def wait(self, timeout=None): self._alive = False


def _quiet_log():
    import logging
    lg = logging.getLogger("test.preview"); lg.addHandler(logging.NullHandler()); return lg


def _wait(pred, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred(): return
        time.sleep(0.02)


def t_preview_pull_worker_collects_frame_and_level():
    soi, eoi = b"\xff\xd8", b"\xff\xd9"
    frame = soi + b"IMG" + eoi
    proc = _FakeProc()

    def fake_spawn(worker):
        # video: one complete JPEG then EOF; stderr: one ebur128 line then EOF
        video = [frame, b""]
        def vread(n=65536):
            return video.pop(0) if video else b""
        class _V:  # minimal read() interface
            read = staticmethod(vread)
        stderr = iter(["[Parsed_ebur128_1] M: -20.0 S: -22.0\n"])
        return proc, _V(), stderr

    w = m._PreviewPullWorker("B", "https://twitch.tv/x", None,
                             _quiet_log(), spawn=fake_spawn)
    w.start()
    _wait(lambda: w.latest_frame() == frame, 2.0)
    assert w.latest_frame() == frame
    _wait(lambda: w.latest_level() > 0.0, 2.0)
    assert 0.0 < w.latest_level() <= 1.0
    w.stop()


class _FakeFeed:
    def __init__(self, ch): self._ch = ch; self.cookies = None
    def current_channel(self): return (self._ch, 0)


class _FakeRelay:
    def __init__(self, live="A", pov=None):
        self.feeds = {"A": _FakeFeed("https://twitch.tv/a"),
                      "B": _FakeFeed("https://twitch.tv/b")}
        self._live = live; self.pov = pov
    def live_feed(self): return self._live
    def pov_active(self): return bool(self.pov)


class _FakeObs:
    def get_source_screenshot(self, name, width=480):
        return (b"\xff\xd8OBS" + name.encode() + b"\xff\xd9", "")


def t_manager_onair_returns_obs_screenshot():
    mgr = m.PreviewManager(_FakeRelay(live="A"), _FakeObs, _quiet_log())
    data, note = mgr.still("A")
    assert data == b"\xff\xd8OBSFeed A\xff\xd9" and note == ""


def t_manager_obs_cache_reuses_within_ttl():
    calls = {"n": 0}
    class _CountObs:
        def get_source_screenshot(self, name, width=480):
            calls["n"] += 1; return (b"\xff\xd8x\xff\xd9", "")
    mgr = m.PreviewManager(_FakeRelay(live="A"), _CountObs, _quiet_log(), obs_ttl=60.0)
    mgr.still("A"); mgr.still("A")
    assert calls["n"] == 1          # second call served from cache


def t_manager_offair_starts_pull_and_levels():
    started = {}
    def fake_factory(target, channel, cookies, log):
        class _W:
            def __init__(self): self.target = target; self.ok = True
            def start(self): started["t"] = target; return self
            def stop(self): started["stopped"] = True
            def latest_frame(self): return b"\xff\xd8P\xff\xd9"
            def latest_level(self): return 0.7
        return _W().start()
    mgr = m.PreviewManager(_FakeRelay(live="A"), _FakeObs, _quiet_log(),
                           worker_factory=fake_factory)
    data, note = mgr.still("B")     # B is off-air
    assert data == b"\xff\xd8P\xff\xd9"
    assert started["t"] == "B"
    assert mgr.levels() == {"B": 0.7}


def t_manager_placeholder_when_pov_off():
    mgr = m.PreviewManager(_FakeRelay(live="A", pov=None), _FakeObs, _quiet_log())
    data, note = mgr.still("POV")
    assert data is None and note == "pov off"


def _make_min_relay_for_preview():
    """Minimal Relay for endpoint tests — two stints, temp log dir."""
    return _FakeRelay(live="A")


def _serve_with_manager(mgr):
    relay = mgr.relay
    srv = m.ThreadingHTTPServer(("127.0.0.1", 0),
                                m.make_handler(relay, preview_manager=mgr))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def t_endpoint_preview_levels_json():
    # off-air pull via fake factory returns level 0.5
    def fake_factory(target, channel, cookies, log):
        class _W:
            ok = True
            def __init__(self): self.target = target
            def stop(self): pass
            def latest_frame(self): return b"\xff\xd8P\xff\xd9"
            def latest_level(self): return 0.5
        return _W()
    relay = _make_min_relay_for_preview()
    mgr = m.PreviewManager(relay, lambda: None, _quiet_log(), worker_factory=fake_factory)
    mgr.still("B")                          # start the off-air pull
    srv = _serve_with_manager(mgr)
    port = srv.server_address[1]
    try:
        body = urllib.request.urlopen(
            "http://127.0.0.1:%d/preview/levels" % port, timeout=3).read()
        assert json.loads(body) == {"B": 0.5}
    finally:
        srv.shutdown()


def t_endpoint_feed_b_returns_jpeg():
    # off-air pull via fake factory returns a known JPEG frame
    _FRAME = b"\xff\xd8" + b"DATA" + b"\xff\xd9"

    def fake_factory(target, channel, cookies, log):
        class _W:
            ok = True
            def __init__(self): self.target = target
            def stop(self): pass
            def latest_frame(self): return _FRAME
            def latest_level(self): return 0.5
        return _W()
    relay = _make_min_relay_for_preview()
    mgr = m.PreviewManager(relay, lambda: None, _quiet_log(), worker_factory=fake_factory)
    srv = _serve_with_manager(mgr)
    port = srv.server_address[1]
    try:
        resp = urllib.request.urlopen(
            "http://127.0.0.1:%d/preview/feed/B" % port, timeout=3)
        assert resp.status == 200
        assert resp.headers["Content-Type"] == "image/jpeg"
        assert resp.read() == _FRAME
    finally:
        srv.shutdown()


# ── Task 8: fan-out ring-tap routing and worker ─────────────────────────────

def t_preview_source_ring_when_fanout_and_offair():
    """Off-air feed with fan-out on → ('ring', key), not ('pull', key)."""
    kind, ref = m.preview_source("B", "A", False, {"A", "B"}, fanout=True)
    assert (kind, ref) == ("ring", "B")


def t_preview_source_pull_when_fanout_off():
    """Fan-out off → classic ('pull', key) for the off-air feed."""
    kind, ref = m.preview_source("B", "A", False, {"A", "B"}, fanout=False)
    assert (kind, ref) == ("pull", "B")


def t_preview_source_onair_still_obs_with_fanout():
    """On-air feed is always OBS regardless of the fan-out flag."""
    assert m.preview_source("A", "A", False, {"A", "B"}, fanout=True) == ("obs", "Feed A")


class _FakeRing:
    """Minimal FeedRing stand-in for unit tests: returns pre-baked bytes once."""

    def __init__(self, data=b""):
        self._data = data
        self.closed = False

    def start_offset(self):
        return 0

    def read(self, cursor, timeout):
        if cursor >= len(self._data):
            return b"", cursor
        data = self._data[cursor:]
        return data, len(self._data)


def t_preview_ring_tap_collects_frame_and_level():
    """_PreviewRingTap exposes latest_frame / latest_level via the spawn seam."""
    soi, eoi = b"\xff\xd8", b"\xff\xd9"
    frame = soi + b"RINGTAP" + eoi
    proc = _FakeProc()

    def fake_spawn(worker):
        video = [frame, b""]
        def vread(n=65536):
            return video.pop(0) if video else b""
        class _V:
            read = staticmethod(vread)
        class _Stdin:
            def write(self, data): pass
            def flush(self): pass
            def close(self): pass
        stderr = iter(["[Parsed_ebur128_1] M: -18.5 S: -22.0\n"])
        return proc, _Stdin(), _V(), stderr

    ring = _FakeRing(b"stream bytes")
    w = m._PreviewRingTap(ring, "B", _quiet_log(), spawn=fake_spawn)
    w.start()
    _wait(lambda: w.latest_frame() == frame, 2.0)
    assert w.latest_frame() == frame
    _wait(lambda: w.latest_level() > 0.0, 2.0)
    assert 0.0 < w.latest_level() <= 1.0
    w.stop()


class _FakeFeedWithRing(_FakeFeed):
    """_FakeFeed extended with a `.ring` attribute."""

    def __init__(self, ch, ring=None):
        super().__init__(ch)
        self.ring = ring


class _FakeRelayWithFanout:
    """Relay stub with fanout=True and feeds that carry a ring."""

    def __init__(self, live="A"):
        ring_a = _FakeRing(b"A stream")
        ring_b = _FakeRing(b"B stream")
        self.feeds = {
            "A": _FakeFeedWithRing("https://twitch.tv/a", ring_a),
            "B": _FakeFeedWithRing("https://twitch.tv/b", ring_b),
        }
        self._live = live
        self.pov = None
        self.fanout = True

    def live_feed(self):
        return self._live

    def pov_active(self):
        return False


def t_manager_offair_uses_ring_tap_when_fanout():
    """PreviewManager routes off-air feed to the ring-tap worker when fanout is on."""
    started = {}

    def ring_factory(ring, target, log):
        class _W:
            def __init__(self): self.target = target; self.ok = True
            def start(self): started["t"] = target; return self
            def stop(self): started["stopped"] = True
            def latest_frame(self): return b"\xff\xd8R\xff\xd9"
            def latest_level(self): return 0.8
        return _W().start()

    relay = _FakeRelayWithFanout(live="A")
    mgr = m.PreviewManager(relay, _FakeObs, _quiet_log(),
                           ring_factory=ring_factory)
    data, note = mgr.still("B")        # B is off-air; fanout=True
    assert data == b"\xff\xd8R\xff\xd9", f"unexpected data: {data!r}"
    assert started.get("t") == "B"
    assert mgr.levels() == {"B": 0.8}



def t_preview_ring_tap_prepends_init_and_aligns_an_fmp4_join():
    """#577: the preview tap joins at the live edge like OBS does, so an fMP4
    feed needs the same init segment + moof alignment before ffmpeg sees it."""
    def box(typ, payload=b""):
        return (8 + len(payload)).to_bytes(4, "big") + typ + payload
    init = box(b"ftyp", b"mp42" + b"\x00" * 8) + box(b"moov", b"\x11" * 200)
    frag1 = box(b"moof", b"\x22" * 60) + box(b"mdat", b"\x33" * 400)
    frag2 = box(b"moof", b"\x22" * 60) + box(b"mdat", b"\x44" * 400)
    ring = m.FeedRing(1 << 20)
    ring.write(init + frag1[:100])                    # the tap joins mid-mdat
    written = bytearray()

    def fake_spawn(worker):
        class _V:
            @staticmethod
            def read(n=65536):
                time.sleep(0.05)
                return b"" if worker._stop.is_set() else b"\x00"
        class _Stdin:
            def write(self, data): written.extend(data)
            def flush(self): pass
            def close(self): pass
        return _FakeProc(), _Stdin(), _V(), iter([])

    w = m._PreviewRingTap(ring, "B", _quiet_log(), spawn=fake_spawn)
    w.start()
    time.sleep(0.2)
    ring.write(frag1[100:] + frag2)
    _wait(lambda: len(written) >= len(init) + len(frag2), 2.0)
    w.stop()
    assert bytes(written) == init + frag2, (bytes(written[:40]), len(written))

if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

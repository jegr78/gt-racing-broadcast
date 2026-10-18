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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

#!/usr/bin/env python3
"""The public timing viewer load tool (#1110), against a stand-in timing route on loopback.
Run: python3 tests/test_timing_viewer_load.py"""
import http.server, importlib.util, os, sys, threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))

_spec = importlib.util.spec_from_file_location(
    "timing_viewer_load", os.path.join(ROOT, "tools", "timing-viewer-load.py"))
tvl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tvl)

BODY = b'{"entries": []}'
ETAG = '"abc"'


class _Timing(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        path, _, query = self.path.partition("?")
        type(self).seen.append((path, query, self.headers.get("If-None-Match"),
                                self.headers.get("Accept-Encoding")))
        if query != "l=good":
            return self._answer(403, b"{}")
        if path == "/console/timing":
            return self._answer(200, b"<html></html>")
        if self.headers.get("If-None-Match") == ETAG:
            return self._answer(304, b"", {"X-Poll-After": "0.1", "X-Data-Age": "1.5"})
        return self._answer(200, BODY, {"ETag": ETAG, "X-Poll-After": "0.1", "X-Data-Age": "0.5"})

    def _answer(self, status, body, headers=None):
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _serve():
    _Timing.seen = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Timing)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/console/timing"


def t_data_url_follows_the_page():
    assert tvl.data_url("https://h.example/console/timing?l=x") == "https://h.example/console/timing/data?l=x"
    assert tvl.data_url("https://h.example/console/timing/?l=x") == "https://h.example/console/timing/data?l=x"


def t_wait_follows_the_page():
    assert tvl.next_wait(200, "4.0") == 4.0, "X-Poll-After of 2 s or more sets the wait"
    assert tvl.next_wait(200, "0.5") == 2.0, "a pace below 2 s keeps the page's 2 s"
    assert tvl.next_wait(200, None) == 2.0
    assert tvl.next_wait(429, None) == 5.0, "429 without a pace waits 5 s"
    assert tvl.next_wait(429, "8.0") == 8.0
    assert tvl.next_wait(403, "4.0") is None, "a dead link ends the viewer"
    assert tvl.next_wait(None, None) == 5.0, "a failed request waits 5 s"


def t_viewers_poll_with_etag_and_report():
    srv, link = _serve()
    try:
        report = tvl.run(link + "?l=good", viewers=3, duration=1.0, ramp=0.0, min_wait=0.1)
    finally:
        srv.shutdown()
    assert report["viewers"] == 3 and report["ended_403"] == 0, report
    assert report["page_loads"] == 3, "every viewer loads the page once"
    assert report["status"].get("200") == 3, f"one full body per viewer, then 304: {report['status']}"
    assert report["status"].get("304", 0) >= 6, report["status"]
    assert report["errors"] == 0, report
    data = [s for s in _Timing.seen if s[0].endswith("/data")]
    assert all("gzip" in (s[3] or "") for s in data), "viewers ask for gzip like a browser"
    assert any(s[2] == ETAG for s in data), "viewers revalidate with If-None-Match"
    assert report["bytes"]["body"] >= 3 * len(BODY), report["bytes"]
    assert report["upload_kbps"] > 0 and report["refresh_s"]["p50"] is not None, report
    assert report["data_age_s"]["max"] == 1.5, report["data_age_s"]


def t_dead_link_ends_viewers():
    srv, link = _serve()
    try:
        report = tvl.run(link + "?l=bad", viewers=2, duration=1.0, ramp=0.0, min_wait=0.1, page=False)
    finally:
        srv.shutdown()
    assert report["ended_403"] == 2 and report["status"] == {"403": 2}, report


def t_unreachable_counts_as_error():
    srv, link = _serve()
    srv.shutdown()
    srv.server_close()
    report = tvl.run(link + "?l=good", viewers=1, duration=0.3, ramp=0.0, min_wait=0.1, page=False)
    assert report["errors"] >= 1 and report["status"] == {}, report


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

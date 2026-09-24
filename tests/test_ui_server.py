#!/usr/bin/env python3
"""Stdlib checks for the Control Center HTTP server, run against a real server on
an ephemeral port so CI needs no fixed port.
Run: python3 tests/test_ui_server.py"""
import json, os, re, sys, tempfile, threading, time, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))   # ui_server imports logsetup
sys.path.insert(0, os.path.join(ROOT, "src", "ui"))
import ui_jobs
import ui_server as us


# Pure helpers.

def t_ui_port_default_and_override():
    assert us.ui_port({}) == 8089
    assert us.ui_port({"RACECAST_UI_PORT": "9100"}) == 9100
    assert us.ui_port({"RACECAST_UI_PORT": ""}) == 8089
    assert us.ui_port({"RACECAST_UI_PORT": "not-a-port"}) == 8089


def t_classify_ping():
    ours = json.dumps({"app": us.APP_ID, "version": "x"}).encode()
    assert us.classify_ping(ours) == "ours"
    assert us.classify_ping(b'{"app": "something-else"}') == "foreign"
    assert us.classify_ping(b"<html>hi</html>") == "foreign"


def t_sse_frames():
    assert us.sse_frame("hello") == b"data: hello\n\n"
    assert us.sse_done(3) == b"event: done\ndata: 3\n\n"


# The live server.

def _export_stub(name, assets):
    fd, p = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with open(p, "wb") as f:
        f.write(b"PK\x03\x04stub-zip-bytes")
    return {"ok": True, "path": p, "slug": (name or "active")}


_IMPORTED = {}


def _import_stub(path, force):
    with open(path, "rb") as f:
        _IMPORTED["bytes"] = f.read()
    return {"ok": True, "name": "iro-gtec", "display": "IRO GTEC",
            "includes_assets": True}


# A stand-in for the bundled onboarding-decks tree (offline copy served at /docs/slides).
# realpath the base too (macOS /var -> /private/var) so the traversal guard matches.
_SLIDES_TMP = os.path.realpath(tempfile.mkdtemp(prefix="cc-slides-"))
with open(os.path.join(_SLIDES_TMP, "index.html"), "w") as _f:
    _f.write("<!doctype html><h1>decks offline</h1>")


def _slides_serve(rel):
    rel = (rel or "").strip("/") or "index.html"
    p = os.path.realpath(os.path.join(_SLIDES_TMP, rel))
    if p != _SLIDES_TMP and not p.startswith(_SLIDES_TMP + os.sep):
        return None
    if not os.path.isfile(p):
        return None
    return p, "text/html; charset=utf-8"


def _ctx(jobs=None, init_plan=None, init_step=None, profile_logo=None,
         devices_enumerate=None, devices_write=None,
         ps_discover=None, ps_write=None):
    page = os.path.join(ROOT, "src", "ui", "control-center.html")
    return {"version": "test",
            "page_path": page,
            "favicon_path": os.path.join(ROOT, "src", "assets", "app-icon.svg"),
            "status": lambda: {"relay": {"alive": False}},
            "ops": {"echo": ["echo-args"]},
            "build_argv": lambda name, params=None: ["echo-args"],
            "assets": lambda: {"ok": True,
                               "graphics": {"level": "PASS", "detail": "g"},
                               "media": {"level": "PASS", "detail": "m"}},
            "producer_schedule": lambda: {
                "rows": [{"part": "1", "producer": "Alice",
                          "magicdns": "producer-a.ts.net", "self": False},
                         {"part": "2", "producer": "Bob",
                          "magicdns": "producer-b.ts.net", "self": True}],
                "self_name": "producer-b.ts.net", "self_known": True},
            "asset_files": lambda: {"ok": True,
                                    "graphics": ["Overlay.png"],
                                    "media": ["intro.mp4"]},
            "asset_roots": lambda: {"graphics": ROOT, "media": ROOT},
            "tools": lambda: {"ok": True, "tools": [
                {"name": "yt-dlp", "installed": True, "version": "1.2.3"},
                {"name": "ffmpeg", "installed": False, "version": None}]},
            "apps": lambda: {"ok": True, "apps": [
                {"name": "obs", "installed": True},
                {"name": "discord", "installed": False}]},
            "preflight": lambda: {"ok": True, "sections": [
                {"title": "Hardware", "results": [
                    {"level": "PASS", "name": "RAM", "detail": "32 GB"}]}]},
            "relay_live": lambda: {"ok": True, "schedule_len": 5, "uptime_s": 60,
                                   "feeds": [{"feed": "A", "stint": 3,
                                              "state": "serving"}],
                                   "timer": {"mode": "running"}},
            "tailscale_peers": lambda: [
                {"hostname": "producer-b", "ip": "100.64.0.5", "online": True, "os": "macOS"}],
            "obs_collection": lambda: {"ok": True, "current": "Other",
                                       "expected": "GT Racing Endurance", "match": False,
                                       "expected_present": True,
                                       "renamed_variant": None},
            "update_check": lambda force=False: {"ok": True, "current": "v1.0.0",
                                     "latest": "v1.1.0", "update_available": True,
                                     "forced": force,
                                     "releases_url": "https://example/releases"},
            "previews": lambda force=False: {"ok": True, "forced": force, "previews": [
                {"tag": "preview-pr-42", "title": "Preview: PR #42", "commit": "abc1234",
                 "published_at": "2026-06-10T08:00:00Z", "asset_url": "https://x/p42",
                 "notes": "n"}]},
            "streams_read": lambda: {"ok": True, "path": "/x/streams.json",
                                     "entries": [{"label": "Feed A",
                                                  "channel": "UC1", "port": "53001"}]},
            "streams_write": lambda entries: {"ok": True, "path": "/x/streams.json",
                                              "_got": entries},
            "docs": lambda: {"ok": True, "wiki_url": "https://example/wiki",
                             "decks_url": "https://example.github.io/repo/",
                             "decks_local_url": "/docs/slides/",
                             "local": [{"key": "setup-readme", "title": "Setup README",
                                        "desc": "d", "kind": "markdown"}]},
            "docs_content": lambda key: (("text/html; charset=utf-8",
                                          b"<!doctype html><h1>readme</h1>")
                                         if key == "setup-readme" else None),
            "docs_slides_serve": _slides_serve,
            "jobs": jobs or ui_jobs.JobManager(
                lambda a: [sys.executable, "-c", "print('hi from job')"]),
            "log_sources": {},
            "env_read": lambda: {"ok": True, "path": "/x/.env",
                                 "entries": [{"key": "RACECAST_SHEET_ID", "value": "abc"}]},
            "env_write": lambda entries: {"ok": True, "path": "/x/.env", "_got": entries},
            "devices_enumerate": devices_enumerate or (lambda: {
                "ok": True, "devices": [], "note": "", "mic": [], "mic_note": ""}),
            "devices_write": devices_write or (lambda webcam, capture, mic=None, tyres=None, mic_name=None: {
                "ok": True, "path": "/x/.env"}),
            # default = the "no console found" shape (ok:False for an empty list, the
            # real ps_discover_data contract); every test that asserts otherwise overrides.
            "ps_discover": ps_discover or (lambda: {
                "ok": False, "consoles": [], "note": "", "from_relay": False}),
            "ps_write": ps_write or (lambda ip: {"ok": True, "path": "/x/.env"}),
            "init_plan": init_plan or (lambda browser="firefox": {
                "ok": True, "steps": [], "next_steps": []}),
            "init_step": init_step or (lambda key: {"ok": True, "key": key,
                                                    "done": True,
                                                    "skip_reason": None}),
            "profile_logo": profile_logo or (lambda: None),
            "profiles": lambda: {"ok": True, "active": "demo",
                                 "profiles": [{"name": "demo"}, {"name": "erf"}]},
            "profile_use": lambda name: {"ok": True, "active": name},
            "profile_new": lambda name, source=None, kind=None, template=None: {
                "ok": True, "name": name, "from": source,
                "kind": kind, "template": template},
            "profile_env_read": lambda: {"ok": True, "path": "/x/profile.env",
                                         "entries": [{"key": "K", "value": "v"}]},
            "profile_env_write": lambda entries: {"ok": True,
                                                  "path": "/x/profile.env",
                                                  "_got": entries},
            "overlay_read": lambda page: {"ok": True, "page": page,
                                          "active": "demo", "css": "",
                                          "path": "/x/overlay/%s.css" % page},
            "overlay_write": lambda page, content: {"ok": True,
                                                    "path": "/x/overlay/%s.css" % page},
            "overlay_slots": lambda page: {"ok": True, "page": page,
                                           "slots": [{"id": "stint",
                                                      "label": "Stint banner",
                                                      "props": ["left", "top"]}],
                                           "css": "#stint{}", "body": "<div></div>",
                                           "sample": {"stint": "STINT 3"},
                                           "flagPresets": [{"state": "safety-car", "label": "Safety Car"}]},
            "overlay_layout_read": lambda page: {"ok": True, "page": page,
                                                 "active": "demo", "migrated": False,
                                                 "layout": {"version": 1, "page": page,
                                                            "slots": {}, "fonts": [],
                                                            "customCss": ""}},
            "overlay_layout_write": lambda page, layout: {
                "ok": True, "path": "/x/overlay/%s.css" % page,
                "css": "#stint { left: 10px; }\n", "_got": (page, layout)},
            "overlay_fonts": lambda: {"ok": True, "active": "demo",
                                      "fonts": ["League.woff2"],
                                      "library": ["Oswald.woff2"]},
            "machine_fonts": lambda: {"ok": True, "fonts": ["Oswald.woff2"]},
            "font_catalog": lambda: {"ok": True, "source": "google",
                                     "families": ["Oswald", "Roboto", "Teko"]},
            "machine_font_download": lambda name: {"ok": bool(name),
                                                   "name": (name or "") + ".woff2"},
            "machine_font_delete": lambda name: {"ok": True, "removed": name},
            "overlay_font_upload": lambda name, data: {"ok": bool(name),
                                                       "name": name,
                                                       "_len": len(data)},
            "overlay_bg": lambda: None,
            "overlay_font_serve": lambda name: None,
            "backup_list": lambda: {"ok": True, "active": "demo",
                                    "items": [{"label": "Winter", "slug": "winter",
                                               "created": "2026-06-12T10:00:00Z",
                                               "bytes": 10, "counts": {}}]},
            "backup_create": lambda label, force=None: {"ok": True,
                                    "_got": {"label": label, "force": force}},
            "backup_restore": lambda slug: {"ok": True, "slug": slug},
            "backup_delete": lambda slug: {"ok": True, "removed": True},
            "profile_export": lambda name=None, assets=True: _export_stub(name, assets),
            "profile_import": lambda path, force=False: _import_stub(path, force),
            "console_status": lambda: {"ok": True, "has_secret": True,
                                       "funnel_auto": False, "funnel_capable": True,
                                       "funnel_on": False,
                                       "links": [{"name": "Alpha",
                                                  "internal": "http://127.0.0.1:8088/console?t=x",
                                                  "funnel": "https://h/console?t=x"}]},
            "console_funnel": lambda on: {"ok": True, "_got": on},
            "console_set_funnel_auto": lambda auto: {"ok": True, "_got": auto},
            "console_revoke": lambda streamer: {"ok": True, "_got": streamer},
            "console_post_link": lambda: {"ok": True},
            "speedtest": lambda: {"ok": True, "latest": None, "history": []},
            "event_title_read": lambda: {"ok": True, "title": "",
                                         "source": "default", "relay_alive": False},
            "event_title_write": lambda value: {"ok": True, "title": value or "",
                                                "applied": "file"},
            "crew_read": lambda: {"ok": True, "entries": [
                {"name": "Dana", "director": True, "producer": False}]},
            "crew_write": lambda row, name, director, producer, commentator=None, race_control=None, discord=None: {
                "ok": True, "row": row,
                "_got": (row, name, director, producer, commentator, race_control, discord)},
            "crew_delete": lambda row: {"ok": True, "row": row, "_got": row},
            "report_generate": lambda: {"ok": True,
                                        "html": "<!doctype html><html></html>",
                                        "path": "/x/report.html",
                                        "summary": "1 session, 2 feeds"},
            "report_send": lambda path=None: {"ok": True},
            "resources": lambda: {"available": False}}


def _serve(ctx):
    httpd = us.serve(ctx, "127.0.0.1", 0)        # port 0 -> ephemeral
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


_real_urlopen = urllib.request.urlopen


def _urlopen(url_or_req, timeout=5, _tries=4):
    """urlopen with a short retry on transient connection-abort errors. These tests
    drive a real ThreadingHTTPServer and Windows CI occasionally aborts the client
    socket mid-handshake with ConnectionAbortedError, which the relay also treats as
    benign (#25). An HTTPError is a real response and any non-transient error
    propagates at once; the final attempt re-raises the real exception rather than a
    sentinel, so there is no `raise None` path."""
    for attempt in range(_tries):
        try:
            return _real_urlopen(url_or_req, timeout=timeout)
        except urllib.error.HTTPError:
            raise                                   # a real response, not a flake
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)    # URLError wraps the cause in .reason
            transient = isinstance(exc, (ConnectionError, TimeoutError)) or \
                isinstance(reason, (ConnectionError, TimeoutError))
            if not transient or attempt == _tries - 1:
                raise                               # give up: surface the real error
        time.sleep(0.1 * (attempt + 1))
    raise AssertionError("unreachable: _tries must be >= 1")


def _with_fake_urlopen(fake):
    """Swap _real_urlopen + neutralise the retry backoff for a test; returns a
    restore() callable."""
    global _real_urlopen
    saved_open, saved_sleep = _real_urlopen, time.sleep
    _real_urlopen = fake
    time.sleep = lambda *_a, **_k: None
    def restore():
        global _real_urlopen
        _real_urlopen, time.sleep = saved_open, saved_sleep
    return restore


def t_urlopen_retries_then_raises_the_real_error():
    calls = []
    def always_abort(url, timeout=None):
        calls.append(url)
        raise ConnectionAbortedError(10053, "aborted")
    restore = _with_fake_urlopen(always_abort)
    try:
        try:
            _urlopen("http://x", timeout=1, _tries=3)
            raise AssertionError("expected the connection error to surface")
        except ConnectionAbortedError:
            pass                                    # the real error, never TypeError/None
    finally:
        restore()
    assert len(calls) == 3                          # retried up to _tries


def t_urlopen_returns_after_a_transient_then_success():
    calls = []
    def flaky(url, timeout=None):
        calls.append(url)
        if len(calls) < 2:
            raise ConnectionResetError(10054, "reset")
        return "RESPONSE"
    restore = _with_fake_urlopen(flaky)
    try:
        assert _urlopen("http://x", _tries=3) == "RESPONSE"
    finally:
        restore()
    assert len(calls) == 2


def _get(port, path):
    try:
        with _urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _post(port, path):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 method="POST", data=b"")
    try:
        with _urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def t_ping_identifies_app():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/api/ping")
        assert code == 200
        data = json.loads(body)
        assert data["app"] == us.APP_ID and data["version"] == "test"
    finally:
        httpd.shutdown()


def t_status_route_wraps_provider():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/api/status")
        data = json.loads(body)
        assert code == 200 and data["ok"] is True and data["relay"] == {"alive": False}
    finally:
        httpd.shutdown()


def t_relay_live_route_wraps_provider():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/api/relay-live")
        data = json.loads(body)
        assert code == 200 and data["ok"] is True
        assert data["feeds"][0]["stint"] == 3 and data["timer"]["mode"] == "running"
    finally:
        httpd.shutdown()


def t_tailscale_peers_route_wraps_provider():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/api/tailscale-peers")
        data = json.loads(body)
        assert code == 200 and data["ok"] is True
        assert data["peers"][0] == {"hostname": "producer-b", "ip": "100.64.0.5",
                                    "online": True, "os": "macOS"}
    finally:
        httpd.shutdown()


def t_obs_collection_route_wraps_provider():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/api/obs-collection")
        data = json.loads(body)
        assert code == 200 and data["ok"] is True
        assert data["expected"] == "GT Racing Endurance" and data["match"] is False
    finally:
        httpd.shutdown()


def t_update_route_wraps_provider():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/api/update")
        data = json.loads(body)
        assert code == 200 and data["update_available"] is True
        assert data["latest"] == "v1.1.0" and data["forced"] is False
        _c, body2 = _get(port, "/api/update?force=1")          # force re-check
        assert json.loads(body2)["forced"] is True
    finally:
        httpd.shutdown()


def t_previews_route_wraps_provider():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/api/previews")
        assert code == 200
        d = json.loads(body)
        assert d["ok"] and d["previews"][0]["tag"] == "preview-pr-42"
        _c, body2 = _get(port, "/api/previews?force=1")        # force re-check
        d2 = json.loads(body2)
        assert d2["ok"] and d2["forced"] is True               # force forwarded to provider
    finally:
        httpd.shutdown()


def t_streams_get_and_post_routes():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/api/streams")
        data = json.loads(body)
        assert code == 200 and data["ok"] and data["entries"][0]["port"] == "53001"
        code, body = _post_json(port, "/api/streams",
                                {"entries": [{"channel": "UC2", "port": "53002"}]})
        got = json.loads(body)
        assert code == 200 and got["ok"] and got["_got"] == [{"channel": "UC2",
                                                              "port": "53002"}]
    finally:
        httpd.shutdown()


def t_docs_route_and_file():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/api/docs")
        data = json.loads(body)
        assert code == 200 and data["ok"] and data["local"][0]["key"] == "setup-readme"
        assert urllib.parse.urlparse(data["decks_url"]).hostname.endswith(".github.io")  # hub
        code, body = _get(port, "/api/docs/file/setup-readme")    # allowlisted -> served
        assert code == 200 and b"<h1" in body.lower()
        code, _b = _get(port, "/api/docs/file/unknown")           # not allowlisted -> 404
        assert code == 404
        assert "/docs/slides/" in data["decks_local_url"]          # offline decks hub
    finally:
        httpd.shutdown()


def t_docs_slides_offline_route():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/docs/slides/")                  # -> index.html
        assert code == 200 and b"decks offline" in body
        code, body = _get(port, "/docs/slides/index.html")        # explicit file
        assert code == 200 and b"decks offline" in body
        code, _b = _get(port, "/docs/slides/missing.html")        # absent -> 404
        assert code == 404
    finally:
        httpd.shutdown()


def t_unknown_routes_are_json_404():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/api/nope")
        assert code == 404 and json.loads(body)["ok"] is False
        code, body = _post(port, "/api/op/not-an-op")
        assert code == 404 and "unknown operation" in json.loads(body)["error"]
    finally:
        httpd.shutdown()


def t_op_starts_job_and_snapshot_completes():
    httpd, port = _serve(_ctx())
    try:
        code, body = _post(port, "/api/op/echo")
        assert code == 200
        job_id = json.loads(body)["job_id"]
        deadline = time.time() + 10
        while time.time() < deadline:
            _c, body = _get(port, f"/api/jobs/{job_id}")
            snap = json.loads(body)
            if snap["exit_code"] is not None:
                break
            time.sleep(0.1)
        assert snap["exit_code"] == 0 and snap["op"] == "echo"
        code, _b = _get(port, "/api/jobs/unknown-id")
        assert code == 404
    finally:
        httpd.shutdown()


def t_quit_shuts_the_server_down():
    httpd, port = _serve(_ctx())
    code, body = _post(port, "/api/quit")
    assert code == 200 and json.loads(body)["ok"] is True
    deadline = time.time() + 5
    while time.time() < deadline:           # serve_forever() must return
        try:
            _get(port, "/api/ping")
            time.sleep(0.1)
        except (urllib.error.URLError, ConnectionError, OSError):
            break
    httpd.server_close()


def t_empty_path_segments_are_404():
    httpd, port = _serve(_ctx())
    try:
        code, _b = _get(port, "/api/jobs//stream")
        assert code == 404
        code, _b = _get(port, "/api/logs//stream")
        assert code == 404
    finally:
        httpd.shutdown()


def t_job_stream_delivers_lines_then_done():
    httpd, port = _serve(_ctx())
    try:
        _c, body = _post(port, "/api/op/echo")
        job_id = json.loads(body)["job_id"]
        req = _urlopen(
            f"http://127.0.0.1:{port}/api/jobs/{job_id}/stream", timeout=10)
        assert req.headers["Content-Type"] == "text/event-stream"
        raw = b""
        deadline = time.time() + 10
        while b"event: done\ndata: 0\n\n" not in raw and time.time() < deadline:
            raw += req.read(1)                  # tiny reads, no buffering surprises
        req.close()
        assert b"data: hi from job\n\n" in raw
        assert b"event: done\ndata: 0\n\n" in raw
    finally:
        httpd.shutdown()


def t_job_stream_unknown_id_is_404():
    httpd, port = _serve(_ctx())
    try:
        code, _b = _get(port, "/api/jobs/nope/stream")
        assert code == 404
    finally:
        httpd.shutdown()


def _ctx_with_sources(tmp):
    """A ctx whose 'relay' log source points at tmp/logs, mirroring the
    {files, dir, archives, read} shape of racecast._log_sources(). Kept
    self-contained so this server test does not depend on racecast.py."""
    import re as _re
    d = os.path.join(tmp, "logs")

    def files():
        # Live files = base logs in the dir (no rotation-date suffix).
        try:
            names = os.listdir(d)
        except OSError:
            return []
        out = [os.path.join(d, n) for n in names
               if os.path.isfile(os.path.join(d, n))
               and not _re.search(r"\.\d{4}-\d{2}-\d{2}$", n)]
        return sorted(out)

    def archives():
        bases = [os.path.basename(f) for f in files()]
        dates = set()
        try:
            names = os.listdir(d)
        except OSError:
            names = []
        for name in names:
            for base in bases:
                m = _re.fullmatch(_re.escape(base) + r"\.(\d{4}-\d{2}-\d{2})", name)
                if m:
                    dates.add(m.group(1))
        return sorted(dates, reverse=True)

    def read(token):
        # Resolve a date token to the concatenated archive text; guard traversal.
        if (not token or "/" in token or "\\" in token or os.sep in token
                or ".." in token or not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", token)):
            return None
        chunks = []
        for f in files():
            arch = os.path.join(d, os.path.basename(f) + "." + token)
            if os.path.isfile(arch):
                with open(arch, encoding="utf-8", errors="replace") as fh:
                    chunks.append(fh.read())
        return "\n".join(chunks)

    ctx = _ctx()
    ctx["log_sources"] = {"relay": {"files": files, "dir": d,
                                    "archives": archives, "read": read}}
    return ctx


def t_log_archives_lists_dates():
    tmp = tempfile.mkdtemp()
    d = os.path.join(tmp, "logs")
    os.makedirs(d)
    for n in ("relay.console.log", "relay.console.log.2026-06-17"):
        open(os.path.join(d, n), "w").close()
    httpd, port = _serve(_ctx_with_sources(tmp))
    try:
        code, body = _get(port, "/api/logs/relay/archives")
        assert code == 200 and "2026-06-17" in body.decode("utf-8")
    finally:
        httpd.shutdown()


def t_log_file_rejects_traversal():
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, "logs"))
    httpd, port = _serve(_ctx_with_sources(tmp))
    try:
        code, _b = _get(port, "/api/logs/relay/file?token=../../etc/passwd")
        assert code == 400
    finally:
        httpd.shutdown()


def t_log_stream_tails_appended_lines_via_reopen():
    """The live log SSE stream seeds with history and then delivers lines appended
    after the client connected, which exercises the re-open-per-poll follow()
    (logsetup.read_new_lines). That never holds the file open, so it cannot block
    the relay's midnight rotation on Windows."""
    tmp = tempfile.mkdtemp()
    d = os.path.join(tmp, "logs")
    os.makedirs(d)
    logf = os.path.join(d, "relay.console.log")
    with open(logf, "w", encoding="utf-8") as fh:
        fh.write("seeded line one\n")
    httpd, port = _serve(_ctx_with_sources(tmp))
    try:
        req = _urlopen(f"http://127.0.0.1:{port}/api/logs/relay/stream", timeout=10)
        assert req.headers["Content-Type"] == "text/event-stream"
        raw = b""
        deadline = time.time() + 10
        while b"seeded line one" not in raw and time.time() < deadline:
            raw += req.read(1)                  # tiny reads, no buffering surprises
        assert b"seeded line one" in raw, raw
        # Append after the client is connected; the re-open poll must pick it up.
        with open(logf, "a", encoding="utf-8") as fh:
            fh.write("appended after connect\n")
        while b"appended after connect" not in raw and time.time() < deadline:
            raw += req.read(1)
        req.close()
        assert b"appended after connect" in raw, raw
    finally:
        httpd.shutdown()


def t_root_serves_the_page():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/")
        assert code == 200
        assert b"racecast Control Center" in body
        assert b"/api/status" in body          # the page talks to our API
    finally:
        httpd.shutdown()


def t_page_survives_its_bundled_file_being_deleted():
    # A frozen build unpacks the page into the OS temp dir, and the OS can reap that
    # dir under the running process. Serving from memory survives it.
    import shutil as _shutil
    tmp = tempfile.mkdtemp()
    page = os.path.join(tmp, "control-center.html")
    _shutil.copyfile(os.path.join(ROOT, "src", "ui", "control-center.html"), page)
    ctx = _ctx()
    ctx["page_path"] = page
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/")
        assert code == 200 and b"racecast Control Center" in body
        os.unlink(page)                       # the OS cleaner strikes
        code, body = _get(port, "/")
        assert code == 200, code
        assert b"racecast Control Center" in body
    finally:
        httpd.shutdown()
        _shutil.rmtree(tmp, ignore_errors=True)


def t_missing_page_reports_how_to_recover():
    # Never read and never on disk, so the message must say that a restart helps.
    ctx = _ctx()
    ctx["page_path"] = os.path.join(tempfile.gettempdir(), "racecast-no-such.html")
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/")
        assert code == 404, code
        assert b"restart" in body.lower(), body
    finally:
        httpd.shutdown()


def t_apps_view_hides_tailscale_gui_buttons_on_linux():
    # Linux Tailscale has no GUI app, so the apps view drops the GUI-only Start and
    # Stop there through appActions, gated on lastStatus.os, and renders through
    # appActions rather than the raw APP_ACTION map.
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/")
        assert code == 200
        text = body.decode("utf-8")
        assert "function appActions(" in text
        assert "guiApp" in text                       # Start/Stop tagged GUI-only
        assert "appActions(x.name)" in text           # render path uses the filter
        # The filter keys off the OS the status payload reports.
        assert "lastStatus.os" in text
    finally:
        httpd.shutdown()


def t_overlay_view_has_slot_picker():
    # A "jump to slot" dropdown wired to the editor selection and populated from the
    # page's slot list, so an operator does not hunt on the canvas. (#140)
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/")
        assert code == 200
        assert b'id="ov-slotpick"' in body
        assert b"ovPopulateSlotPicker" in body   # populated on load
        assert b"ovSelect(" in body              # selecting jumps to the slot
    finally:
        httpd.shutdown()


def t_load_profiles_refreshes_asset_gallery():
    # A profile switch or fresh import reloads the profile list through
    # loadProfiles, so loadProfiles must also re-pull the graphics and media gallery
    # and its count badges. Loading those once at startup hides an imported league's
    # assets even though /api/assets/files serves them. (#162)
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/")
        assert code == 200
        text = body.decode("utf-8")
        start = text.index("function loadProfiles(")
        # The function body ends at the next top-level `async function`.
        end = text.index("\nasync function ", start)
        assert "fetchAssetFiles(" in text[start:end], \
            "loadProfiles must refresh the asset gallery so a profile switch/import updates it"
    finally:
        httpd.shutdown()


def t_page_sets_csp_header():
    # The served page carries a Content-Security-Policy. The page is fully
    # self-contained, so 'self' plus inline is enough.
    httpd, port = _serve(_ctx())
    try:
        with _urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
            csp = r.headers.get("Content-Security-Policy")
        assert csp, "expected a Content-Security-Policy header"
        assert "object-src 'none'" in csp
        assert "base-uri 'none'" in csp
        assert "script-src" in csp
    finally:
        httpd.shutdown()


def t_probe_instance_classifies():
    ours = json.dumps({"app": us.APP_ID}).encode()
    assert us.probe_instance("h", 1, fetch=lambda h, p: ours) == "ours"
    assert us.probe_instance("h", 1, fetch=lambda h, p: b"nope") == "foreign"
    def boom(h, p):
        raise OSError("connection refused")
    assert us.probe_instance("h", 1, fetch=boom) == "free"


def _post_json(port, path, obj):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method="POST",
        data=json.dumps(obj).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with _urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def t_op_param_validation_is_400():
    ctx = _ctx()
    def boom(name, params=None):
        raise ValueError("browser must be one of: firefox")
    ctx["build_argv"] = boom
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/op/echo", {"params": {"browser": "x"}})
        assert code == 400 and "browser" in json.loads(body)["error"]
    finally:
        httpd.shutdown()


def t_op_malformed_body_is_400():
    httpd, port = _serve(_ctx())
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/op/echo", method="POST",
            data=b"{not json", headers={"Content-Type": "application/json"})
        try:
            with _urlopen(req, timeout=5) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        assert code == 400
    finally:
        httpd.shutdown()


def t_op_with_params_passes_them_to_build_argv():
    seen = []
    ctx = _ctx()
    ctx["build_argv"] = lambda name, params=None: seen.append((name, params)) or ["echo-args"]
    httpd, port = _serve(ctx)
    try:
        code, _b = _post_json(port, "/api/op/echo", {"params": {"browser": "firefox"}})
        assert code == 200
        assert seen == [("echo", {"browser": "firefox"})]
    finally:
        httpd.shutdown()


def t_assets_route():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/api/assets")
        data = json.loads(body)
        assert code == 200 and data["ok"] is True
        assert data["graphics"]["level"] == "PASS"
    finally:
        httpd.shutdown()


def t_assets_route_provider_error_is_500():
    ctx = _ctx()
    def boom():
        raise RuntimeError("sheet down")
    ctx["assets"] = boom
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/assets")
        assert code == 500 and "sheet down" in json.loads(body)["error"]
    finally:
        httpd.shutdown()


def t_producer_schedule_route_wraps_provider():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/api/producer-schedule")
        data = json.loads(body)
        assert code == 200
        assert data["self_known"] is True
        assert data["self_name"] == "producer-b.ts.net"
        assert [r["producer"] for r in data["rows"]] == ["Alice", "Bob"]
        assert data["rows"][1]["self"] is True
    finally:
        httpd.shutdown()


def t_cancel_route():
    httpd, port = _serve(_ctx())
    try:
        _c, body = _post(port, "/api/op/echo")
        job_id = json.loads(body)["job_id"]
        code, body = _post(port, f"/api/jobs/{job_id}/cancel")
        assert code == 200 and json.loads(body)["ok"] is True
        code, _b = _post(port, "/api/jobs/nope/cancel")
        assert code == 404
    finally:
        httpd.shutdown()


def t_setup_route():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/api/setup")
        data = json.loads(body)
        assert code == 200
        assert data["tools"]["ok"] is True and data["apps"]["ok"] is True
        assert data["tools"]["tools"][0]["name"] == "yt-dlp"
    finally:
        httpd.shutdown()


def t_setup_route_provider_error_is_500():
    ctx = _ctx()
    def boom():
        raise RuntimeError("which down")
    ctx["tools"] = boom
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/setup")
        assert code == 500 and "which down" in json.loads(body)["error"]
    finally:
        httpd.shutdown()


def t_preflight_route():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/api/preflight")
        data = json.loads(body)
        assert code == 200 and data["ok"] is True
        assert data["sections"][0]["title"] == "Hardware"
    finally:
        httpd.shutdown()


def t_preflight_route_provider_error_is_500():
    ctx = _ctx()
    def boom():
        raise RuntimeError("gather down")
    ctx["preflight"] = boom
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/preflight")
        assert code == 500 and "gather down" in json.loads(body)["error"]
    finally:
        httpd.shutdown()


def t_api_speedtest_route():
    ctx = _ctx()
    ctx["speedtest"] = lambda: {"ok": True, "latest": None, "history": []}
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/speedtest")
        data = json.loads(body)
        assert code == 200 and data == {"ok": True, "latest": None, "history": []}
    finally:
        httpd.shutdown()


def t_api_speedtest_route_provider_error_is_500():
    ctx = _ctx()
    def boom():
        raise RuntimeError("history unreadable")
    ctx["speedtest"] = boom
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/speedtest")
        assert code == 500 and "history unreadable" in json.loads(body)["error"]
    finally:
        httpd.shutdown()


def t_favicon_served_as_svg():
    # The Control Center serves a real favicon, the racecast "rc" mark, rather than
    # an empty data: URI. (#57)
    httpd, port = _serve(_ctx())
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/favicon.svg")
        with _urlopen(req, timeout=5) as r:
            body = r.read()
            assert r.status == 200
            assert r.headers.get("Content-Type") == "image/svg+xml"
            assert body.startswith(b"<svg") and b">rc<" in body
            # An XML comment must not contain "--", or the browser silently drops
            # the favicon as malformed. Checked directly rather than by parsing,
            # because stdlib XML parsers carry an XXE surface.
            for comment in re.findall(rb"<!--.*?-->", body, re.DOTALL):
                assert b"--" not in comment[4:-3], "XML comment contains '--'"
    finally:
        httpd.shutdown()


def t_asset_files_route():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/api/assets/files")
        data = json.loads(body)
        assert code == 200 and data["ok"] is True
        assert data["graphics"] == ["Overlay.png"]
    finally:
        httpd.shutdown()


def t_asset_file_serves_bytes_with_ctype():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "Overlay.png"), "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\nFAKE")
    ctx = _ctx()
    ctx["asset_roots"] = lambda: {"graphics": d, "media": d}
    httpd, port = _serve(ctx)
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/assets/file/graphics/Overlay.png")
        with _urlopen(req, timeout=5) as r:
            assert r.status == 200
            assert r.headers.get("Content-Type") == "image/png"
            assert r.read().startswith(b"\x89PNG")
    finally:
        httpd.shutdown()


def t_asset_file_rejects_traversal():
    ctx = _ctx()
    ctx["asset_roots"] = lambda: {"graphics": ROOT, "media": ROOT}
    httpd, port = _serve(ctx)
    try:
        for bad in ("/api/assets/file/graphics/..%2F..%2Fsecret",
                    "/api/assets/file/graphics/%2Fetc%2Fpasswd",
                    "/api/assets/file/nope/Overlay.png"):
            code, _b = _get(port, bad)
            assert code == 404, bad
    finally:
        httpd.shutdown()


def t_asset_file_missing_is_404():
    ctx = _ctx()
    ctx["asset_roots"] = lambda: {"graphics": tempfile.mkdtemp(),
                                  "media": tempfile.mkdtemp()}
    httpd, port = _serve(ctx)
    try:
        code, _b = _get(port, "/api/assets/file/graphics/nothere.png")
        assert code == 404
    finally:
        httpd.shutdown()


def t_asset_file_root_resolved_live_per_request():
    # Serving must resolve the runtime root live per request, the same way the
    # /api/assets/files listing does, not from a dict snapshotted at startup: a
    # stale snapshot 404s files the gallery correctly lists. asset_roots is
    # therefore a zero-arg callable and serving follows its current return. (#55)
    empty = tempfile.mkdtemp()
    real = tempfile.mkdtemp()
    with open(os.path.join(real, "Overlay.png"), "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\nFAKE")
    state = {"graphics": empty, "media": empty}
    ctx = _ctx()
    ctx["asset_roots"] = lambda: state          # live: reflects the current root
    httpd, port = _serve(ctx)
    try:
        code, _b = _get(port, "/api/assets/file/graphics/Overlay.png")
        assert code == 404                       # not under the current root yet
        state["graphics"] = real                 # root resolves (profile settles)
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/assets/file/graphics/Overlay.png")
        with _urlopen(req, timeout=5) as r:
            assert r.status == 200 and r.read().startswith(b"\x89PNG")
    finally:
        httpd.shutdown()


def t_env_get_route():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/api/env")
        data = json.loads(body)
        assert code == 200 and data["ok"] is True
        assert data["entries"][0]["key"] == "RACECAST_SHEET_ID"
    finally:
        httpd.shutdown()


def t_env_get_route_error_is_500():
    ctx = _ctx()
    def boom():
        raise RuntimeError("disk gone")
    ctx["env_read"] = boom
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/env")
        assert code == 500 and "disk gone" in json.loads(body)["error"]
    finally:
        httpd.shutdown()


def t_env_post_saves_entries():
    seen = []
    ctx = _ctx()
    ctx["env_write"] = lambda entries: seen.append(entries) or {"ok": True, "path": "/x/.env"}
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/env",
                                {"entries": [{"key": "A", "value": "1"}]})
        assert code == 200 and json.loads(body)["ok"] is True
        assert seen == [[{"key": "A", "value": "1"}]]
    finally:
        httpd.shutdown()


def t_env_post_validation_error_is_400():
    ctx = _ctx()
    ctx["env_write"] = lambda entries: {"ok": False, "error": "invalid key: 'bad key'"}
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/env", {"entries": [{"key": "bad key", "value": "x"}]})
        assert code == 400 and "invalid key" in json.loads(body)["error"]
    finally:
        httpd.shutdown()


def t_env_post_malformed_body_is_400():
    httpd, port = _serve(_ctx())
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/env", method="POST",
            data=b"{not json", headers={"Content-Type": "application/json"})
        try:
            with _urlopen(req, timeout=5) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        assert code == 400
    finally:
        httpd.shutdown()


def t_get_devices_returns_enumerated_list():
    ctx = _ctx(devices_enumerate=lambda: {
        "ok": True, "devices": [{"name": "Cam", "value": "v0"}], "note": "",
        "mic": [{"name": "Mic", "value": "m0"}], "mic_note": ""})
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/devices")
        data = json.loads(body)
        assert code == 200 and data["ok"] is True
        assert data["devices"] == [{"name": "Cam", "value": "v0"}]
        # The shape carries a separate mic list, because audio devices differ from
        # the video capture list. (#307)
        assert data["mic"] == [{"name": "Mic", "value": "m0"}]
    finally:
        httpd.shutdown()


def t_get_devices_route_error_is_500():
    ctx = _ctx()
    def boom():
        raise RuntimeError("obs gone")
    ctx["devices_enumerate"] = boom
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/devices")
        assert code == 500 and "obs gone" in json.loads(body)["error"]
    finally:
        httpd.shutdown()


def t_post_devices_select_writes():
    seen = {}
    ctx = _ctx(devices_write=lambda w, c, mic=None, tyres=None, mic_name=None: seen.update(
        webcam=w, capture=c, mic=mic, tyres=tyres, mic_name=mic_name)
        or {"ok": True, "_got": [w, c, mic, tyres]})
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/devices/select",
                                {"webcam": "v0", "capture": "v1", "mic": "m0", "tyres": "t0",
                                 "mic_name": "Mikrofon (K66)"})
        data = json.loads(body)
        assert code == 200 and data["ok"] is True
        assert data["_got"] == ["v0", "v1", "m0", "t0"]
        assert seen == {"webcam": "v0", "capture": "v1", "mic": "m0", "tyres": "t0",
                        "mic_name": "Mikrofon (K66)"}   # #668
    finally:
        httpd.shutdown()


def t_post_devices_select_validation_error_is_400():
    ctx = _ctx(devices_write=lambda w, c, mic=None, tyres=None, mic_name=None: {
        "ok": False, "error": "no device selected"})
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/devices/select", {"webcam": "", "capture": ""})
        assert code == 400 and "no device selected" in json.loads(body)["error"]
    finally:
        httpd.shutdown()


def t_post_devices_select_malformed_body_is_400():
    httpd, port = _serve(_ctx())
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/devices/select", method="POST",
            data=b"{not json", headers={"Content-Type": "application/json"})
        try:
            with _urlopen(req, timeout=5) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        assert code == 400
    finally:
        httpd.shutdown()


def t_init_plan_route_returns_plan():
    ctx = _ctx(init_plan=lambda browser="firefox": {
        "ok": True, "steps": [{"key": "env", "label": ".env", "kind": "gate",
                               "op": None, "done": False, "skip_reason": None,
                               "instruction": "set it"}],
        "next_steps": []})
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/init/plan")
        data = json.loads(body)
        assert code == 200
        assert data["ok"] is True
        assert data["steps"][0]["key"] == "env"
    finally:
        httpd.shutdown()


def t_init_plan_route_passes_browser_query():
    seen = {}
    def plan(browser="firefox"):
        seen["browser"] = browser
        return {"ok": True, "steps": [], "next_steps": []}
    httpd, port = _serve(_ctx(init_plan=plan))
    try:
        code, body = _get(port, "/api/init/plan?browser=edge")
        json.loads(body)
        assert code == 200
        assert seen["browser"] == "edge"
    finally:
        httpd.shutdown()


def t_init_step_route_runs_action():
    ctx = _ctx(init_step=lambda key: {"ok": True, "key": key, "done": True,
                                      "skip_reason": "config already exported"})
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/init/step/export-companion", {})
        data = json.loads(body)
        assert code == 200
        assert data["ok"] is True
        assert data["done"] is True
    finally:
        httpd.shutdown()


def t_init_step_route_reports_error_as_400():
    ctx = _ctx(init_step=lambda key: {"ok": False, "error": "nope"})
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/init/step/cookies", {})
        data = json.loads(body)
        assert code == 400
        assert data["ok"] is False
    finally:
        httpd.shutdown()


def t_profiles_get_route_wraps_provider():
    seen = []
    ctx = _ctx()
    ctx["profiles"] = lambda: seen.append(True) or {"ok": True, "active": "demo",
                                                    "profiles": [{"name": "demo"}]}
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/profiles")
        data = json.loads(body)
        assert code == 200 and data["ok"] is True
        assert data["active"] == "demo" and data["profiles"][0]["name"] == "demo"
        assert seen == [True]
    finally:
        httpd.shutdown()


def t_profile_use_post_passes_name():
    seen = []
    ctx = _ctx()
    ctx["profile_use"] = lambda name: seen.append(name) or {"ok": True,
                                                            "active": name}
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/profile/use", {"name": "erf"})
        data = json.loads(body)
        assert code == 200 and data["ok"] is True and data["active"] == "erf"
        assert seen == ["erf"]
    finally:
        httpd.shutdown()


def t_profile_new_post_passes_name_and_source():
    seen = []
    ctx = _ctx()
    ctx["profile_new"] = lambda name, source=None, kind=None, template=None: (
        seen.append((name, source)) or {"ok": True, "name": name, "from": source})
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/profile/new",
                                {"name": "gt3", "from": "demo"})
        data = json.loads(body)
        assert code == 200 and data["ok"] is True
        assert data["name"] == "gt3" and data["from"] == "demo"
        assert seen == [("gt3", "demo")]
    finally:
        httpd.shutdown()


def t_profile_new_route_forwards_kind_template():
    seen = []
    ctx = _ctx()
    ctx["profile_new"] = lambda name, source=None, kind=None, template=None: (
        seen.append((name, source, kind, template))
        or {"ok": True, "name": name, "from": source})
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/profile/new",
                                {"name": "solo1", "from": None,
                                 "kind": "solo", "template": "pov"})
        data = json.loads(body)
        assert code == 200 and data["ok"] is True, (code, body)
        assert seen == [("solo1", None, "solo", "pov")], seen
    finally:
        httpd.shutdown()


def t_profile_env_get_route_wraps_provider():
    seen = []
    ctx = _ctx()
    ctx["profile_env_read"] = lambda: seen.append(True) or {
        "ok": True, "path": "/x/profile.env",
        "entries": [{"key": "K", "value": "v"}]}
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/profile/env")
        data = json.loads(body)
        assert code == 200 and data["ok"] is True
        assert data["entries"][0]["key"] == "K"
        assert seen == [True]
    finally:
        httpd.shutdown()


def t_console_status_route_wraps_provider():
    ctx = _ctx()
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/console/status")
        data = json.loads(body)
        assert code == 200 and data["has_secret"] is True
        assert data["links"][0]["name"] == "Alpha"
        # Both the public Funnel link and the internal tailnet link ride through.
        assert data["links"][0]["funnel"] == "https://h/console?t=x"
        assert data["links"][0]["internal"] == "http://127.0.0.1:8088/console?t=x"
    finally:
        httpd.shutdown()


def t_console_post_routes_pass_args():
    seen = {}
    ctx = _ctx()
    ctx["console_funnel"] = lambda on: seen.update(fn=on) or {"ok": True}
    ctx["console_revoke"] = lambda streamer: seen.update(rv=streamer) or {"ok": True}
    ctx["console_post_link"] = lambda: seen.update(post=True) or {"ok": True}
    httpd, port = _serve(ctx)
    try:
        assert _post_json(port, "/api/console/funnel", {"on": True})[0] == 200
        assert _post_json(port, "/api/console/revoke", {"streamer": "Alpha"})[0] == 200
        assert _post_json(port, "/api/console/post-link", {})[0] == 200
        assert seen == {"fn": True, "rv": "Alpha", "post": True}
    finally:
        httpd.shutdown()


def t_profile_env_post_passes_entries():
    seen = []
    ctx = _ctx()
    ctx["profile_env_write"] = lambda entries: seen.append(entries) or {
        "ok": True, "path": "/x/profile.env"}
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/profile/env",
                                {"entries": [{"key": "A", "value": "1"}]})
        assert code == 200 and json.loads(body)["ok"] is True
        assert seen == [[{"key": "A", "value": "1"}]]
    finally:
        httpd.shutdown()


def t_overlay_get_route_wraps_provider():
    seen = []
    ctx = _ctx()
    ctx["overlay_read"] = lambda page: seen.append(page) or {
        "ok": True, "page": page, "active": "demo",
        "css": "#x{}", "path": "/x/overlay/%s.css" % page}
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/overlay?page=hud")
        data = json.loads(body)
        assert code == 200 and data["ok"] is True
        assert data["page"] == "hud" and data["css"] == "#x{}"
        assert seen == ["hud"]
    finally:
        httpd.shutdown()


def t_overlay_get_route_defaults_to_hud():
    seen = []
    ctx = _ctx()
    ctx["overlay_read"] = lambda page: seen.append(page) or {
        "ok": True, "page": page, "active": "demo", "css": "", "path": "/x"}
    httpd, port = _serve(ctx)
    try:
        code, _b = _get(port, "/api/overlay")
        assert code == 200 and seen == ["hud"]
    finally:
        httpd.shutdown()


def t_overlay_post_passes_page_and_content():
    seen = []
    ctx = _ctx()
    ctx["overlay_write"] = lambda page, content: seen.append((page, content)) or {
        "ok": True, "path": "/x/overlay/%s.css" % page}
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/overlay",
                                {"page": "timer", "content": "#t{}"})
        assert code == 200 and json.loads(body)["ok"] is True
        assert seen == [("timer", "#t{}")]
    finally:
        httpd.shutdown()


def t_overlay_post_provider_error_is_400():
    ctx = _ctx()
    ctx["overlay_write"] = lambda page, content: {"ok": False,
                                                  "error": "no active profile or invalid page"}
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/overlay",
                                {"page": "panel", "content": "x"})
        assert code == 400 and "invalid page" in json.loads(body)["error"]
    finally:
        httpd.shutdown()


def t_overlay_slots_route_passes_page():
    seen = []
    ctx = _ctx()
    base = ctx["overlay_slots"]
    ctx["overlay_slots"] = lambda page: seen.append(page) or base(page)
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/overlay/slots?page=timer")
        data = json.loads(body)
        assert code == 200 and data["ok"] is True and seen == ["timer"]
        assert data["slots"][0]["id"] == "stint"
    finally:
        httpd.shutdown()


def t_overlay_layout_get_passes_page():
    seen = []
    ctx = _ctx()
    base = ctx["overlay_layout_read"]
    ctx["overlay_layout_read"] = lambda page: seen.append(page) or base(page)
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/overlay/layout?page=hud")
        data = json.loads(body)
        assert code == 200 and data["ok"] is True and seen == ["hud"]
        assert data["layout"]["page"] == "hud"
    finally:
        httpd.shutdown()


def t_overlay_layout_post_passes_page_and_layout():
    seen = []
    ctx = _ctx()
    ctx["overlay_layout_write"] = lambda page, layout: seen.append((page, layout)) or {
        "ok": True, "path": "/x/overlay/%s.css" % page, "css": "#stint{}"}
    httpd, port = _serve(ctx)
    try:
        layout = {"version": 1, "page": "hud", "slots": {"stint": {"left": 10}}}
        code, body = _post_json(port, "/api/overlay/layout",
                                {"page": "hud", "layout": layout})
        assert code == 200 and json.loads(body)["ok"] is True
        assert seen == [("hud", layout)]
    finally:
        httpd.shutdown()


def t_overlay_layout_post_provider_error_is_400():
    ctx = _ctx()
    ctx["overlay_layout_write"] = lambda page, layout: {"ok": False,
                                                        "error": "layout page mismatch"}
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/overlay/layout",
                                {"page": "hud", "layout": {"page": "timer"}})
        assert code == 400 and "mismatch" in json.loads(body)["error"]
    finally:
        httpd.shutdown()


def t_overlay_fonts_list_route():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/api/overlay/fonts")
        data = json.loads(body)
        assert code == 200 and data["fonts"] == ["League.woff2"]
    finally:
        httpd.shutdown()


def t_overlay_font_upload_passes_name_and_bytes():
    seen = []
    ctx = _ctx()
    ctx["overlay_font_upload"] = lambda name, data: seen.append((name, data)) or {
        "ok": True, "name": name}
    httpd, port = _serve(ctx)
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/overlay/fonts?name=League.woff2",
            method="POST", data=b"OTTOfontbytes",
            headers={"Content-Type": "font/woff2"})
        with _urlopen(req, timeout=5) as r:
            assert r.status == 200
        assert seen == [("League.woff2", b"OTTOfontbytes")]
    finally:
        httpd.shutdown()


def t_overlay_font_upload_empty_body_is_413():
    httpd, port = _serve(_ctx())
    try:
        code, _ = _post(port, "/api/overlay/fonts?name=League.woff2")
        assert code == 413
    finally:
        httpd.shutdown()


def t_font_library_list_route():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/api/fonts")
        data = json.loads(body)
        assert code == 200 and "catalog" not in data
        assert "Oswald.woff2" in data["fonts"]
    finally:
        httpd.shutdown()


def t_font_catalog_route():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/api/fonts/catalog")
        data = json.loads(body)
        assert code == 200 and data["source"] == "google"
        assert "Teko" in data["families"]
    finally:
        httpd.shutdown()


def t_font_download_route_passes_name():
    seen = []
    ctx = _ctx()
    ctx["machine_font_download"] = lambda name: seen.append(name) or {
        "ok": True, "name": name + ".woff2"}
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/fonts/download", {"name": "Oswald"})
        assert code == 200 and json.loads(body)["ok"] is True
        assert seen == ["Oswald"]
    finally:
        httpd.shutdown()


def t_font_delete_route_passes_name():
    seen = []
    ctx = _ctx()
    ctx["machine_font_delete"] = lambda name: seen.append(name) or {
        "ok": True, "removed": name}
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/fonts/delete", {"name": "Oswald.woff2"})
        assert code == 200 and json.loads(body)["ok"] is True
        assert seen == ["Oswald.woff2"]
    finally:
        httpd.shutdown()


def t_overlay_fonts_list_includes_library():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/api/overlay/fonts")
        data = json.loads(body)
        assert code == 200 and "Oswald.woff2" in data["library"]
    finally:
        httpd.shutdown()


def t_overlay_bg_missing_is_404():
    httpd, port = _serve(_ctx())            # overlay_bg stub returns None
    try:
        code, _ = _get(port, "/api/overlay/bg")
        assert code == 404
    finally:
        httpd.shutdown()


def t_overlay_font_serve_missing_is_404():
    httpd, port = _serve(_ctx())            # overlay_font_serve stub returns None
    try:
        code, _ = _get(port, "/api/overlay/font/Nope.woff2")
        assert code == 404
    finally:
        httpd.shutdown()


def t_overlay_font_serve_returns_bytes_and_type():
    import tempfile
    ctx = _ctx()
    fd, fpath = tempfile.mkstemp(suffix=".woff2")
    os.write(fd, b"FONTDATA"); os.close(fd)
    ctx["overlay_font_serve"] = lambda name: (fpath, "font/woff2")
    httpd, port = _serve(ctx)
    try:
        with _urlopen(f"http://127.0.0.1:{port}/api/overlay/font/X.woff2") as r:
            assert r.status == 200
            assert r.headers["Content-Type"] == "font/woff2"
            assert r.read() == b"FONTDATA"
    finally:
        httpd.shutdown()
        os.unlink(fpath)


def t_profile_logo_route_serves_image_with_type():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        svg = os.path.join(td, "logo.svg")
        with open(svg, "wb") as fh:
            fh.write(b"<svg/>")
        httpd, port = _serve(_ctx(profile_logo=lambda: svg))
        try:
            with _urlopen(f"http://127.0.0.1:{port}/api/profile/logo") as r:
                assert r.status == 200
                assert r.headers.get("Content-Type") == "image/svg+xml"
                assert r.read() == b"<svg/>"
        finally:
            httpd.shutdown()


def t_profile_logo_route_404_when_no_logo():
    httpd, port = _serve(_ctx())            # default profile_logo -> None
    try:
        code, _ = _get(port, "/api/profile/logo")
        assert code == 404
    finally:
        httpd.shutdown()


def t_api_backup_routes():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/api/backup")
        data = json.loads(body)
        assert code == 200 and data["ok"] and data["items"][0]["label"] == "Winter"
        code, body = _post_json(port, "/api/backup", {"label": "Spring", "force": False})
        got = json.loads(body)
        assert code == 200 and got["ok"] and got["_got"] == {"label": "Spring", "force": False}
        code, body = _post_json(port, "/api/backup/restore", {"slug": "winter"})
        assert code == 200 and json.loads(body)["ok"]
        code, body = _post_json(port, "/api/backup/delete", {"slug": "winter"})
        assert code == 200 and json.loads(body)["removed"] is True
    finally:
        httpd.shutdown()


def t_profile_export_streams_zip():
    httpd, port = _serve(_ctx())
    try:
        r = _urlopen(f"http://127.0.0.1:{port}/api/profile/export?name=iro-gtec")
        assert r.status == 200
        assert r.headers.get("Content-Disposition", "").startswith("attachment")
        body = r.read()
        assert body.startswith(b"PK")
    finally:
        httpd.shutdown()


def t_profile_import_accepts_raw_body():
    httpd, port = _serve(_ctx())
    try:
        data = b"PK\x03\x04uploaded"
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/profile/import?force=1",
            data=data, method="POST")
        req.add_header("Content-Length", str(len(data)))
        r = _urlopen(req)
        out = json.loads(r.read())
        assert out["ok"] is True and out["name"] == "iro-gtec"
        assert _IMPORTED["bytes"] == data
    finally:
        httpd.shutdown()


# request_csrf_ok: the localhost trust-boundary guard.

def t_csrf_same_origin_loopback_ok():
    assert us.request_csrf_ok({"Host": "127.0.0.1:8089"})
    assert us.request_csrf_ok({"Host": "localhost:8089"})
    assert us.request_csrf_ok({"Host": "127.0.0.1:8089", "Origin": "http://127.0.0.1:8089"})
    assert us.request_csrf_ok({"Host": "localhost:8089", "Origin": "http://localhost:8089"})
    assert us.request_csrf_ok({"Host": "[::1]:8089", "Origin": "http://[::1]:8089"})
    assert us.request_csrf_ok({})                       # non-browser client (no Host/Origin)


def t_csrf_foreign_host_blocked():
    # DNS rebinding: the browser connected to 127.0.0.1 but sent the attacker's name.
    assert not us.request_csrf_ok({"Host": "evil.example.com:8089"})
    assert not us.request_csrf_ok({"Host": "attacker.com"})


def t_csrf_cross_origin_blocked():
    # Classic CSRF: a foreign page POSTing to the localhost API carries its Origin.
    assert not us.request_csrf_ok({"Host": "127.0.0.1:8089",
                                   "Origin": "http://evil.example.com"})
    assert not us.request_csrf_ok({"Host": "127.0.0.1:8089",
                                   "Referer": "http://evil.example.com/x"})
    assert not us.request_csrf_ok({"Host": "127.0.0.1:8089",
                                   "Origin": "https://youtube.com"})


def t_csrf_guard_blocks_foreign_host_on_real_server():
    # A forged Host header is refused with 403 by the live server.
    import http.client
    httpd, port = _serve(_ctx())
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.putrequest("GET", "/api/status", skip_host=True)
        conn.putheader("Host", "evil.example.com")
        conn.endheaders()
        resp = conn.getresponse()
        assert resp.status == 403, resp.status
        conn.close()
    finally:
        httpd.shutdown()


def t_event_title_get_route():
    ctx = _ctx()
    ctx["event_title_read"] = lambda: {"ok": True, "title": "Round 4",
                                       "source": "relay", "relay_alive": True}
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/event-title")
        d = json.loads(body)
        assert code == 200 and d["title"] == "Round 4" and d["source"] == "relay"
    finally:
        httpd.shutdown()


def t_event_title_get_route_error_is_500():
    ctx = _ctx()
    def boom():
        raise RuntimeError("relay gone")
    ctx["event_title_read"] = boom
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/event-title")
        assert code == 500 and "relay gone" in json.loads(body)["error"]
    finally:
        httpd.shutdown()


def t_event_title_post_route_saves():
    seen = []
    ctx = _ctx()
    ctx["event_title_write"] = lambda value: seen.append(value) or {
        "ok": True, "title": (value or "").strip(), "applied": "file"}
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/event-title", {"title": " Round 5 "})
        d = json.loads(body)
        # The route forwards the raw value, as `seen` proves, and relays the provider
        # result verbatim. The strip lives in the provider, not the route.
        assert code == 200 and d["ok"] and d["title"] == "Round 5"
        assert seen == [" Round 5 "]
    finally:
        httpd.shutdown()


def t_event_title_post_malformed_body_is_400():
    httpd, port = _serve(_ctx())
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/event-title", method="POST",
            data=b"{not json", headers={"Content-Type": "application/json"})
        try:
            with _urlopen(req, timeout=5) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        assert code == 400
    finally:
        httpd.shutdown()


def t_event_title_post_validation_error_is_400():
    ctx = _ctx()
    ctx["event_title_write"] = lambda value: {"ok": False, "error": "relay rejected"}
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/event-title", {"title": "x"})
        assert code == 400 and "relay rejected" in json.loads(body)["error"]
    finally:
        httpd.shutdown()


def t_post_obs_stream_target_malformed_body_is_400():
    httpd, port = _serve(_ctx())
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/obs/stream-target", method="POST",
            data=b"{not json", headers={"Content-Type": "application/json"})
        try:
            with _urlopen(req, timeout=5) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        assert code == 400
    finally:
        httpd.shutdown()


def t_post_obs_stream_target_routes_to_provider():
    calls = {}
    def provider(part):
        calls["part"] = part
        return {"ok": True, "note": "stream target set for Part 1 on twitch — stream key set"}
    ctx = _ctx()
    ctx["obs_stream_target"] = provider
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/obs/stream-target", {"part": "1"})
        d = json.loads(body)
        assert code == 200 and d["ok"] is True
        assert calls["part"] == "1"
        assert "key set" in d["note"] and "SECRET" not in d["note"]
    finally:
        httpd.shutdown()


def t_post_obs_stream_target_error_is_400():
    ctx = _ctx()
    ctx["obs_stream_target"] = lambda part: {
        "ok": False,
        "note": "OBS is streaming — stop the broadcast before changing the stream target."}
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/obs/stream-target", {"part": "1"})
        d = json.loads(body)
        assert code == 400 and d["ok"] is False
    finally:
        httpd.shutdown()


def t_api_crew_get_returns_entries():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/api/crew")
        assert code == 200
        data = json.loads(body)
        assert data["entries"][0]["name"] == "Dana"
    finally:
        httpd.shutdown()


def t_api_crew_post_writes_row():
    httpd, port = _serve(_ctx())
    try:
        code, body = _post_json(port, "/api/crew",
                                {"row": 2, "name": "Pia", "director": False, "producer": True,
                                 "commentator": True, "race_control": True, "discord": "pia_d"})
        data = json.loads(body)
        assert code == 200 and data["ok"] is True
        assert data["_got"] == [2, "Pia", False, True, True, True, "pia_d"]
    finally:
        httpd.shutdown()


def t_api_crew_delete_post():
    httpd, port = _serve(_ctx())
    try:
        code, body = _post_json(port, "/api/crew/delete", {"row": 3})
        data = json.loads(body)
        assert code == 200 and data["_got"] == 3
    finally:
        httpd.shutdown()


def t_report_generate_and_send_routes():
    calls = {}
    ctx = _ctx()
    ctx["report_generate"] = lambda: {"ok": True, "html": "<!doctype html><html></html>",
                                      "path": "/x/r.html", "summary": "sum"}
    ctx["report_send"] = lambda path=None: calls.update(send=path) or {"ok": True}
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/report/generate", {})
        data = json.loads(body)
        assert code == 200 and data["ok"] is True and "<!doctype html>" in data["html"]
        code, body = _post_json(port, "/api/report/send", {"path": "/x/r.html"})
        data = json.loads(body)
        assert code == 200 and data["ok"] is True
        assert calls["send"] == "/x/r.html"
    finally:
        httpd.shutdown()


def t_panel_link_has_no_obs_credential_fragment():
    # The Director Panel is relay-mediated and reads no OBS-WS credentials from the
    # URL fragment, so the Control Center must not append an `#ip=…&port=…&pw=…`
    # fragment to the /panel link: it leaks the OBS password into the address bar.
    with open(os.path.join(ROOT, "src", "ui", "control-center.html"),
              encoding="utf-8") as fh:                # cp1252 on Windows would choke
        page = fh.read()
    assert "/api/obs-ws" not in page          # the dead creds fetch is gone
    assert "f.set('pw'" not in page            # no password into the panel URL
    assert "obsWs" not in page                 # the whole creds plumbing is gone
    assert "/panel" in page                    # but the plain panel link still exists


def t_api_resources_route():
    ctx = _ctx()
    ctx["resources"] = lambda: {"available": True, "cpu_pct": 42.0, "cpu_level": "green",
                                "mem_used": 8, "mem_total": 16, "mem_pct": 50.0,
                                "mem_level": "green", "net_up_bps": 2000.0,
                                "net_down_bps": 1000.0, "disk_free": 100,
                                "disk_level": "green"}
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/resources")
        data = json.loads(body)
        assert code == 200 and data["available"] is True
        assert data["cpu_pct"] == 42.0 and data["disk_level"] == "green"
    finally:
        httpd.shutdown()


def t_api_ps_discover_route():
    ctx = _ctx(ps_discover=lambda: {"ok": True, "consoles": ["192.168.1.42"],
                                    "note": "", "from_relay": False})
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/ps/discover", {})
        data = json.loads(body)
        assert code == 200 and data["consoles"] == ["192.168.1.42"]
    finally:
        httpd.shutdown()


def t_api_ps_save_route():
    saved = {}
    # dict.update() returns None, so the `or` falls through to the dict.
    # dict.setdefault() would return the bare ip string instead, and the route's
    # `result.get("ok")` would then raise an uncaught AttributeError.
    ctx = _ctx(ps_write=lambda ip: saved.update(ip=ip) or {"ok": True})
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/ps/save", {"ip": "192.168.1.42"})
        assert code == 200 and json.loads(body)["ok"] is True
        assert saved["ip"] == "192.168.1.42"
    finally:
        httpd.shutdown()


def t_api_ps_save_rejects_bad_ip():
    ctx = _ctx(ps_write=lambda ip: {"ok": False, "error": f"invalid host/IP: {ip!r}"})
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/ps/save", {"ip": "bad host!"})
        assert code == 400 and json.loads(body)["ok"] is False
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")

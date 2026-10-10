#!/usr/bin/env python3
"""Stdlib checks for the Control Center HTTP server, run against a real server on
an ephemeral port so CI needs no fixed port.
Run: python3 tests/test_ui_server.py"""
import json, os, re, shutil, subprocess, sys, tempfile, threading, time, urllib.error, urllib.parse, urllib.request

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
            "fonts_restore": lambda force: {"ok": True, "library": [], "profiles": {}},
            "gt7_data_status": lambda: {"ok": True, "checked": None, "files": {}},
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
            "report_read": lambda name: {"ok": True,
                                         "html": "<!doctype html><html></html>",
                                         "path": "/x/" + str(name)},
            "report_send": lambda path=None: {"ok": True},
            "telemetry_recordings": lambda: {"ok": True, "recordings": [
                {"name": "20261007-201503.gt7rec", "rec": "20261007-201503",
                 "started": "2026-10-07T20:15:03+02:00", "size": 1000, "duration_s": 600.0,
                 "laps": None, "partial": False, "recording": False, "indexed": False,
                 "track": None}]},
            "telemetry_laps": lambda rec=None, session=None, track=None, car=None, build=True: {
                "ok": True, "laps": []},
            "telemetry_lap": lambda rec, session, lap, build=True: {"ok": False, "error": "no lap"},
            "telemetry_tracks": lambda: {"ok": True, "tracks": [
                {"id": "suzuka01", "track": "Suzuka Circuit", "layout": "Full Course",
                 "reverse": False}]},
            "telemetry_learn": lambda rec, track_id, build=True: {"ok": True, "track": {"id": track_id}},
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
    d = os.path.join(tmp, "logs")

    def files():
        # Live files = base logs in the dir (no rotation-date suffix).
        try:
            names = os.listdir(d)
        except OSError:
            return []
        out = [os.path.join(d, n) for n in names
               if os.path.isfile(os.path.join(d, n))
               and not re.search(r"\.\d{4}-\d{2}-\d{2}$", n)]
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
                m = re.fullmatch(re.escape(base) + r"\.(\d{4}-\d{2}-\d{2})", name)
                if m:
                    dates.add(m.group(1))
        return sorted(dates, reverse=True)

    def read(token):
        # Resolve a date token to the concatenated archive text; guard traversal.
        if (not token or "/" in token or "\\" in token or os.sep in token
                or ".." in token or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", token)):
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
    tmp = tempfile.mkdtemp()
    page = os.path.join(tmp, "control-center.html")
    shutil.copyfile(os.path.join(ROOT, "src", "ui", "control-center.html"), page)
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
        shutil.rmtree(tmp, ignore_errors=True)


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


def t_fonts_restore_route_passes_only_a_literal_true_force():
    ctx = _ctx()
    seen = []
    ctx["fonts_restore"] = lambda force: seen.append(force) or {
        "ok": True, "library": ["Oswald.woff2"], "profiles": {"demo": ["Oswald.woff2"]}}
    httpd, port = _serve(ctx)
    try:
        code, body = _post_json(port, "/api/fonts/restore", {"force": True})
        assert code == 200 and json.loads(body)["profiles"] == {"demo": ["Oswald.woff2"]}
        _post_json(port, "/api/fonts/restore", {"force": "yes"})
        assert seen == [True, False], seen
    finally:
        httpd.shutdown()


def t_gt7_data_routes():
    httpd, port = _serve(_ctx())
    try:
        code, body = _get(port, "/api/gt7-data")
        assert code == 200 and json.loads(body)["ok"] is True, (code, body)
        code, _b = _post_json(port, "/api/gt7-data/update", {})
        assert code == 404, "the update runs as the gt7-data-update job, not in a request thread"
    finally:
        httpd.shutdown()


def t_gt7_data_status_route_hides_paths():
    ctx = _ctx()
    hidden = os.path.join(os.sep, "home", "producer", "runtime", "gt7")

    def locked():
        raise PermissionError(13, "Permission denied", hidden)
    ctx["gt7_data_status"] = locked
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/gt7-data")
        text = body.decode() if isinstance(body, bytes) else body
        assert code == 500 and hidden not in text, (code, text)
    finally:
        httpd.shutdown()


def t_job_snapshot_returns_the_output_tail_on_request():
    ctx = _ctx(jobs=ui_jobs.JobManager(
        lambda a: [sys.executable, "-c", "print('one'); print('two'); print('three')"]))
    httpd, port = _serve(ctx)
    try:
        job_id = json.loads(_post(port, "/api/op/echo")[1])["job_id"]
        deadline = time.time() + 10
        while time.time() < deadline:
            snap = json.loads(_get(port, f"/api/jobs/{job_id}?tail=2")[1])
            if snap["exit_code"] is not None:
                break
            time.sleep(0.1)
        assert snap["lines"] == ["two", "three"], snap
        assert "lines" not in json.loads(_get(port, f"/api/jobs/{job_id}")[1]), \
            "a plain snapshot stays small"
        assert len(json.loads(_get(port, f"/api/jobs/{job_id}?tail=999999")[1])["lines"]) == 3
        assert _get(port, f"/api/jobs/{job_id}?tail=x")[0] == 200
    finally:
        httpd.shutdown()


_JOB_HARNESS = r"""
class El { constructor() { this.textContent = ''; this.disabled = false; } }
const els = {};
const $ = id => els[id] || (els[id] = new El());
const pending = [], calls = [];
globalThis.fetch = (url, opts) => new Promise(res => { calls.push(url); pending.push({url, res}); });
function answer(part, data) {
  const hit = pending.filter(p => p.url.includes(part));
  hit.forEach(p => { pending.splice(pending.indexOf(p), 1); p.res({json: async () => data}); });
  return hit.length;
}
const tick = async () => { for (let i = 0; i < 20; i++) await new Promise(r => setTimeout(r, 0)); };
const JOB_POLL_MS = 0;
"""


def _job_fns(page):
    return "".join(_page_fn(page, n) for n in ("startJob", "jobResult"))


def _page_fn(page, name):
    i = page.index("function " + name + "(")
    i = page.rindex("\n", 0, i) + 1
    return page[i:page.index("\n}\n", i) + 3]


def t_job_snapshot_tail_takes_ascii_digits_only():
    httpd, port = _serve(_ctx())
    try:
        job_id = json.loads(_post(port, "/api/op/echo")[1])["job_id"]
        code, body = _get(port, "/api/jobs/" + job_id + "?tail=" + urllib.parse.quote("\u00b2"))
        assert code == 200 and "lines" not in json.loads(body), (code, body)
    finally:
        httpd.shutdown()


def t_op_already_running_names_the_running_job():
    ctx = _ctx(jobs=ui_jobs.JobManager(
        lambda a: [sys.executable, "-c", "import time; time.sleep(3)"]))
    httpd, port = _serve(ctx)
    try:
        first = json.loads(_post(port, "/api/op/echo")[1])["job_id"]
        try:
            code, body = _post(port, "/api/op/echo")
        except urllib.error.HTTPError as e:
            code, body = e.code, e.read()
        d = json.loads(body)
        assert code == 409 and d["job_id"] == first and "already running" in d["error"], d
    finally:
        httpd.shutdown()


def t_gt7_data_update_runs_as_a_job_and_says_updating():
    page = _cc_page()
    out = _run_js(_JOB_HARNESS + _job_fns(page) + _page_fn(page, "updateGt7Data") + """
const alerts = []; let loads = 0;
globalThis.alertModal = async t => { alerts.push(t); };
globalThis.loadGt7Data = () => { loads++; };
(async () => {
  updateGt7Data();
  await tick();
  const busy = [$('d-gt7data').textContent, $('gt7data-update').disabled].join(',');
  answer('/api/op/gt7-data-update', {ok: true, job_id: 'j1'});
  await tick();
  answer('/api/jobs/j1', {ok: true, running: true, exit_code: null});
  await tick();
  const still = $('d-gt7data').textContent;
  answer('/api/jobs/j1', {ok: true, running: false, exit_code: 1,
                          lines: ['cars.csv: error: offline', 'GT7 data update failed (offline?)']});
  await tick();
  console.log([busy, still, $('gt7data-update').disabled, loads,
               alerts.join('/').includes('cars.csv: error: offline'),
               calls.filter(u => u.includes('/api/jobs/j1?tail=')).length].join('|'));
})();""")
    if out is not None:
        assert out.strip() == "updating\u2026,true|updating\u2026|false|1|true|2", \
            f"the row says updating while the job runs, the failure lines show after it: {out!r}"


def t_gt7_data_update_shows_a_refused_start():
    page = _cc_page()
    out = _run_js(_JOB_HARNESS + _job_fns(page) + _page_fn(page, "updateGt7Data") + """
const alerts = []; let loads = 0;
globalThis.alertModal = async t => { alerts.push(t); };
globalThis.loadGt7Data = () => { loads++; };
(async () => {
  updateGt7Data();
  await tick();
  answer('/api/op/gt7-data-update', {ok: false, error: 'gt7-data-update is already running'});
  await tick();
  console.log([alerts.join('/'), loads, $('gt7data-update').disabled].join('|'));
})();""")
    if out is not None:
        assert out.strip() == "gt7-data-update is already running|1|false", out


def _report_run(job_answer):
    page = _cc_page()
    return _run_js(_JOB_HARNESS + _job_fns(page) + _page_fn(page, "reportGenerate") + """
let _reportPath = null;
(async () => {
  reportGenerate();
  await tick();
  const busy = [$('report-msg').textContent, $('report-gen').disabled].join(',');
  answer('/api/op/report-generate', {ok: true, job_id: 'r1'});
  await tick();
  answer('/api/jobs/r1', """ + job_answer + """);
  await tick();
  answer('/api/report/read?name=Event%202026-10-09.html',
         {ok: true, path: '/reports/Event 2026-10-09.html', html: '<p>report</p>'});
  await tick();
  console.log(JSON.stringify([busy, $('report-msg').textContent, $('report-gen').disabled,
    $('report-frame').srcdoc || '', calls.filter(u => u.includes('/api/report/read')).length,
    calls.filter(u => u === '/api/report/generate').length]));
})();""")


def t_report_generates_in_a_job_and_shows_the_written_file():
    out = _report_run("""{ok: true, running: false, exit_code: 0,
                         lines: ['1 session', 'Report written -> /reports/Event 2026-10-09.html']}""")
    if out is not None:
        busy, msg, disabled, frame, reads, posts = json.loads(out)
        assert busy == "Generating\u2026,true", busy
        assert (msg, disabled, frame, reads, posts) == (
            "Written to /reports/Event 2026-10-09.html", False, "<p>report</p>", 1, 0), \
            f"the page runs report-generate, then reads the file the job wrote: {out}"


def t_report_job_failure_shows_its_last_line():
    out = _report_run("""{ok: true, running: false, exit_code: 1,
                         lines: ['racecast: no health data for that window. Nothing to report.']}""")
    if out is not None:
        _busy, msg, disabled, frame, reads, _posts = json.loads(out)
        assert (msg, disabled, frame, reads) == (
            "Error: no health data for that window. Nothing to report.", False, "", 0), \
            f"a failed report job shows its reason and reads no file: {out}"


def t_ui_server_queues_a_browser_burst():
    httpd, _ = _serve(_ctx())
    try:
        assert httpd.request_queue_size >= 128, \
            "the Control Center fires parallel fetches; the stdlib backlog of 5 resets them"
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
    ctx["report_read"] = lambda name: calls.update(read=name) or {
        "ok": True, "html": "<!doctype html><html></html>", "path": "/x/r.html"}
    ctx["report_send"] = lambda path=None: calls.update(send=path) or {"ok": True}
    httpd, port = _serve(ctx)
    try:
        try:
            code = _post_json(port, "/api/report/generate", {})[0]
        except urllib.error.HTTPError as e:
            code = e.code
        assert code == 404, "a report runs as the report-generate job, not in a request thread"
        code, body = _get(port, "/api/report/read?name=r.html")
        data = json.loads(body)
        assert code == 200 and data["ok"] is True and "<!doctype html>" in data["html"]
        assert calls["read"] == "r.html", calls
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


def _cc_page():
    with open(os.path.join(ROOT, "src", "ui", "control-center.html"),
              encoding="utf-8") as fh:                # cp1252 on Windows would choke
        return fh.read()



def _run_js(src):
    """stdout of `src` under node, or None where node is not installed."""
    node = shutil.which("node")
    if not node:
        print("  (node not installed, JS check skipped)")
        return None
    return subprocess.run([node, "-"], input=src, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True, timeout=30).stdout


def t_gt7_data_age_reads_naturally():
    page = _cc_page()
    i = page.index("function fmtAgeS(")
    fn = page[i:page.index("\n}\n", i) + 2]
    out = _run_js(fn + """
const now = Date.now() / 1000;
console.log([fmtAgeS(0)].concat([20, 90, 7200, 86400, 3 * 86400].map(a => fmtAgeS(now - a))).join("|"));""")
    if out is not None:
        assert out.strip() == "never|just now|2 min ago|2 h ago|1 day ago|3 days ago", out


def _row_classes(page, label):
    """The class attribute of the General-Settings row whose name cell is `label`."""
    m = re.search(r'<div class="([^"]*)"[^>]*>\s*<span class="name">' + re.escape(label) + "<",
                  page)
    assert m, f"no settings row named {label!r}"
    return m.group(1).split()


def t_device_pickers_shown_for_endurance_profiles():
    # #720: an endurance league puts its own capture card on air (`local:`, #592) and
    # mixes the commentary mic into that feed (#670), so the capture and mic pickers
    # must not be solo-only. Webcam, tyres/fuel and the GT7 PlayStation stay solo:
    # only the solo collections carry those inputs, and telemetry runs in solo POV only.
    page = _cc_page()
    style = page[page.index("<style>") + len("<style>"):page.index("</style>")]
    style = re.sub(r"/\*.*?\*/", "", style, flags=re.S)
    hiding = set()
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", style):
        if re.search(r"display\s*:\s*none", body):
            hiding.update(" ".join(s.split()) for s in sel.split(","))
    assert not [s for s in hiding if "#dev-section" in s or "#dev-head" in s], \
        f"the device section is hidden: {sorted(hiding)}"
    assert "body:not(.solo) .solo-only" in hiding, \
        "solo-only rows must be hidden outside a solo profile"
    assert "body.solo .endurance-only" in hiding, \
        "endurance-only notes must be hidden in a solo profile"
    assert "solo-only" not in _row_classes(page, "Capture"), "the capture picker must show for endurance"
    assert "solo-only" not in _row_classes(page, "Mic"), "the mic picker must show for endurance"
    assert "solo-only" in _row_classes(page, "Webcam"), "the webcam picker is solo-only"
    assert "solo-only" in _row_classes(page, "Tyres/Fuel"), "the tyres/fuel picker is solo-only"
    ps = re.search(r'<div class="solo-only[^"]*"[^>]*>(.*?)</div>\s*</section>', page, re.S)
    assert ps and 'id="ps-ip"' in ps.group(1) and 'id="ps-hint"' in ps.group(1), \
        "the PlayStation IP block must sit in a solo-only wrapper"
    assert ">Solo devices<" not in page, "the heading must not call the section solo-only"


def t_solo_device_rows_follow_the_template():
    page = _cc_page()
    style = re.sub(r"/\*.*?\*/", "", page[page.index("<style>"):page.index("</style>")], flags=re.S)
    assert re.search(r"body:not\(\.tpl-pov\)\s+\.pov-only[^{]*\{[^}]*display\s*:\s*none", style), \
        "pov-only rows must be hidden outside a solo POV profile"
    assert re.search(r"body:not\(\.tpl-commentary\)\s+\.commentary-only[^{]*\{[^}]*display\s*:\s*none",
                     style), "commentary-only rows must be hidden outside a solo commentary profile"
    assert "commentary-only" in _row_classes(page, "Tyres/Fuel"), \
        "only the commentary collection has the tyres/fuel capture"
    ps = re.search(r'<div class="([^"]*)"[^>]*>\s*<div class="row"[^>]*><span class="name">PlayStation IP', page)
    assert ps and "pov-only" in ps.group(1).split(), "GT7 telemetry runs in solo POV only"
    assert "classList.toggle('tpl-pov'" in page and "classList.toggle('tpl-commentary'" in page, \
        "applyKindGating must set the template class"


def _tm_script(page):
    start = page.index("// Telemetry view (solo POV, #788)")
    return page[start:page.index("// end of the Telemetry view", start)]


def _tm_fn(tm, name):
    i = tm.index("function " + name + "(")
    return tm[i:tm.index("\n}\n", i) + 2]


def t_telemetry_view_is_solo_pov_only():
    page = _cc_page()
    nav = re.search(r'<button class="([^"]*)" data-nav="telemetry"', page)
    assert nav and "pov-only" in nav.group(1).split(), "the nav item exists for solo POV only"
    view = re.search(r'<div class="([^"]*)" data-view="telemetry"', page)
    assert view and {"view", "pov-only"} <= set(view.group(1).split()), \
        "the view is hidden outside solo POV by the existing .pov-only rule"
    show = page[page.index("function showView(name)"):page.index("let _reportPath")]
    assert "name === 'telemetry'" in show, "opening the view loads the recordings"
    gate = page[page.index("function applyKindGating(data)"):page.index("async function useProfile(")]
    assert "currentView === 'telemetry'" in gate, "leaving solo POV leaves the Telemetry view"
    assert "tmLoad()" in gate, "another solo POV profile shows its own recordings at once"
    use = page[page.index("async function useProfile("):page.index("function onKindChange()")]
    assert "tmReset()" in use, "another profile has other recordings"
    tm = _tm_script(page)
    assert "innerHTML" not in tm, "recording values reach the page only as text"
    for route in ("/api/telemetry/recordings", "/api/telemetry/laps?", "/api/telemetry/lap?"):
        assert route in tm, route
    assert "tmRenderSectors()" in tm[tm.index("function tmRender()"):]


def t_telemetry_charts_share_one_cursor():
    tm = _tm_script(_cc_page())
    keys = re.findall(r"\{key: '(\w+)'", tm[tm.index("const TM_CH"):tm.index("];", tm.index("const TM_CH"))])
    assert keys == ["speed_kmh", "throttle", "brake", "steer_deg", "gear", "delta"], keys
    assert "addEventListener('pointermove', tmHover)" in tm
    assert "tmRenderCharts()" in _tm_fn(tm, "tmRender"), "every pair render redraws the charts"


def t_telemetry_map_colours_mini_sectors():
    tm = _tm_script(_cc_page())
    fn = tm[tm.index("function tmRenderMap()"):tm.index("function tmMapCursor(")]
    assert "(p.z - z0) * s" in fn, "GT7 z runs downward on the map, as in the report"
    assert "tmSecClass(diff)" in fn and "tm-seg" in fn
    assert "tmMapCursor(ra, rb)" in tm[tm.index("function tmHover("):tm.index("function tmLeave(")]
    assert "tmRenderMap()" in _tm_fn(tm, "tmRender"), "every pair render redraws the map"


def t_telemetry_set_track_confirms_then_learns():
    tm = _tm_script(_cc_page())
    learn = tm[tm.index("async function tmLearn()"):]
    learn = learn[:learn.index("\n}\n")]
    assert learn.index("confirmModal(") < learn.index("fetch('/api/telemetry/learn'"), \
        "learning writes machine-wide data, so it is confirmed first"
    assert "tmDropLaps()" in learn and "tmSelectRec(" in learn
    assert "/api/telemetry/tracks" in tm
    sel = tm[tm.index("async function tmSelectRec("):tm.index("function tmRenderLaps()")]
    assert "tmShowSetTrack(d.recording.track)" in sel


_TM_MAP_COUNT = """
const drawn = () => {
  const n = {};
  const walk = e => {
    const a = e.attrs || {};
    const k = e.tagName + (a.class ? '.' + a.class : '');
    n[k] = (n[k] || 0) + 1;
    if (e.tagName === 'text') n.note = (n.note || 0) + 1;
    e.kids.forEach(walk);
  };
  $('tm-map').kids.forEach(walk);
  return ['path.tm-a', 'path.tm-seg gain', 'path.tm-seg loss', 'path.tm-seg even', 'note']
    .map(k => n[k] || 0).join(',');
};
const trace = n => Array.from({length: n}, (_, i) => ({d: i * 5, t: i, x: i, z: i * i}));
"""


def t_telemetry_map_draws_sectors_and_hides_cursor_past_the_lap():
    out = _tm_node(_TM_MAP_COUNT + """
tmState.sectorM = 10;
tmState.lapA = {trace: trace(7), sectors: [1.5, 1.5, 3.0002]};
tmState.lapB = {trace: trace(7), sectors: [1, 2, 3]};
tmRenderMap();
const pair = drawn();
const P = tmState.map.P;
const axes = [P({x: 6, z: 0})[0] > P({x: 0, z: 0})[0], P({x: 0, z: 36})[1] > P({x: 0, z: 0})[1]];
tmMapCursor({x: 1, z: 1}, null);
const vis = [tmState.map.dotA.attrs.visibility, tmState.map.dotB.attrs.visibility];
tmState.lapA = null;
tmRenderMap();
const alone = drawn();
tmState.lapB = {trace: trace(7).map(p => ({...p, x: null, z: null})), sectors: [1, 2, 3]};
tmRenderMap();
console.log([pair, axes.join(','), vis.join(','), alone, drawn(), String(tmState.map)].join('|'));""")
    if out is not None:
        assert out.strip() == "1,1,1,1,0|true,true|visible,hidden|0,0,0,3,0|0,0,0,0,1|null", \
            f"B per mini-sector gain/loss/even, x right and z down, no dot past a lap: {out!r}"


def t_telemetry_map_keeps_a_on_top_and_centres_the_layout():
    out = _tm_node(_TM_MAP_COUNT + """
tmState.sectorM = 10;
tmState.lapA = {trace: trace(5), sectors: [1.5, 1.5]};
tmState.lapB = {trace: trace(9), sectors: [1, 2, 3, 4]};
tmRenderMap();
const pair = drawn();
const kids = $('tm-map').kids.map(k => k.tagName + '.' + ((k.attrs || {}).class || ''));
const top = kids.indexOf('path.tm-a') > kids.lastIndexOf('path.tm-seg even');
const tall = tmState.map.P({x: 0, z: 0})[0], W = tmState.map.W;
const xs = [tall, tmState.map.P({x: 8, z: 0})[0]];
tmState.lapA = null;
tmState.lapB = {trace: trace(9).map(p => ({...p, x: p.z, z: p.x})), sectors: [1, 2, 3, 4]};
tmRenderMap();
const vb = $('tm-map').attrs.viewBox.split(' ').map(Number);
const wide = [tmState.map.P({x: 0, z: 0}), tmState.map.P({x: 64, z: 8})];
console.log([pair, top, Math.round(xs[0] + xs[1]) === W,
             wide[0][0] === 12, Math.round(wide[1][0]) === W - 12,
             wide[0][1] === 12, Math.round(wide[1][1]) === vb[3] - 12].join('|'));""")
    if out is not None:
        assert out.strip() == "1,1,1,2,0|true|true|true|true|true|true", \
            f"A lies on top, a sector past A's end is even, the layout sits centred: {out!r}"


def t_telemetry_sector_class_is_shared_by_map_and_table():
    tm = _tm_script(_cc_page())
    out = _run_js(_tm_fn(tm, "tmSecClass") + """
console.log([null, 0.0004, -0.0004, 0.0005, -0.0005, 1].map(tmSecClass).join(','));""")
    if out is not None:
        assert out.strip() == "even,even,even,loss,gain,loss", out
    for fn in ("tmRenderMap", "tmRenderSectors"):
        body = tm[tm.index("function " + fn + "("):]
        body = body[:body.index("\n}\n")]
        assert "tmSecClass(" in body and "'gain'" not in body and "'loss'" not in body, \
            f"{fn} uses the shared gain/loss rule"


def t_telemetry_refit_redraws_only_on_a_width_change():
    out = _tm_node(_TM_MAP_COUNT + """
tmState.sectorM = 10;
tmState.lapB = {trace: trace(7), sectors: [1, 2, 3]};
tmRender();
const first = [$('tm-charts').kids[0], $('tm-map').kids[0]];
tmRefit();
const same = [$('tm-charts').kids[0] === first[0], $('tm-map').kids[0] === first[1]];
$('tm-charts').getBoundingClientRect = () => ({left: 0, width: 640});
$('tm-map').getBoundingClientRect = () => ({left: 0, width: 300});
tmRefit();
const moved = [$('tm-charts').kids[0] === first[0], $('tm-map').kids[0] === first[1]];
$('tm-charts').getBoundingClientRect = () => ({left: 0, width: 0});
$('tm-map').getBoundingClientRect = () => ({left: 0, width: 0});
const kept = $('tm-charts').kids[0];
tmRefit();
console.log([same.join(','), moved.join(','), $('tm-charts').kids[0] === kept, tmState.chart.W].join('|'));""")
    if out is not None:
        assert out.strip() == "true,true|false,false|true|640", \
            f"a refit redraws only a changed width and skips a hidden view: {out!r}"


def t_telemetry_showing_the_view_refits_a_loaded_pair():
    page = _cc_page()
    show = page[page.index("function showView(name)"):page.index("let _reportPath")]
    assert "name === 'telemetry' && tmState.lapB) tmRefit()" in show, \
        "a pair drawn while the view was hidden is redrawn at the real width"
    tm = _tm_script(page)
    resize = tm[tm.index("window.addEventListener('resize'"):]
    assert "tmRefit()" in resize, "a resize redraws through the width check"


def t_telemetry_delta_and_paths_follow_the_traces():
    tm = _tm_script(_cc_page())
    out = _run_js(_tm_fn(tm, "tmDelta") + _tm_fn(tm, "tmPath") + """
const tr = ts => ts.map((t, i) => ({d: i * 5, t}));
console.log(JSON.stringify(tmDelta({trace: tr([0, 1, 2])}, {trace: tr([0, 0.5, 1, 1.5])})));
const rows = [{d: 0, v: 1}, {d: 5, v: 2}, {d: 10, v: null}, {d: 15, v: 3}];
console.log(tmPath(rows, 'v', d => d, v => 10 * v, false));
console.log(tmPath(rows.slice(0, 2), 'v', d => d, v => 10 * v, true));
console.log(tmPath([], 'v', d => d, v => v, false));""")
    if out is not None:
        assert out.strip().splitlines() == [
            '[{"d":0,"delta":0},{"d":5,"delta":-0.5},{"d":10,"delta":-1}]',
            "M0.0 10.0L5.0 20.0M15.0 30.0", "M0.0 10.0H5.0V20.0", "M0 0"], \
            "delta is B minus A over the stations both laps reach; a gap lifts the pen"


def t_node_script_goes_through_stdin():
    seen = []
    real = subprocess.run
    subprocess.run = lambda args, **kw: seen.append((args, kw)) or real(args, **kw)
    try:
        out = _run_js("console.log('x'.repeat(40000).length)")
    finally:
        subprocess.run = real
    if out is not None:
        assert out.strip() == "40000" and seen[0][0][1:] == ["-"] and "input" in seen[0][1], \
            f"the script reaches node on stdin, so no command line grows with the page: {seen[0][0][1:]}"


def t_telemetry_closing_point_off_the_grid_keeps_delta_and_hover_aligned():
    out = _tm_node("""
const tr = (ts, end) => ts.map((t, i) => ({d: i * 5, t, speed_kmh: 100, throttle: 50, brake: 0,
                                         steer_deg: 0, gear: 3, x: i, z: 0}))
  .concat([{d: end, t: ts.length, speed_kmh: 100, throttle: 50, brake: 0, steer_deg: 0, gear: 3,
            x: 99, z: 0}]);
const same = tmDelta({trace: tr([0, 1, 2], 12.3)}, {trace: tr([0, 0.5, 1], 12.3)});
const apart = tmDelta({trace: tr([0, 1, 2], 12.3)}, {trace: tr([0, 0.5, 1, 1.5], 17.1)});
tmState.lapA = {trace: tr([0, 1, 2, 3], 17.4)};
tmState.lapB = {trace: tr([0, 0.5, 1], 12.3), sectors: [1]};
tmRenderCharts();
const maxD = tmState.chart.maxD;
tmHover({clientX: 799});
const edge = tmState.chart.panels[0].read.textContent.replace(/\u00b7/g, '-');
console.log([JSON.stringify(same), JSON.stringify(apart), maxD, edge].join('|'));""")
    if out is not None:
        same, apart, max_d, edge = out.strip().split("|")
        assert same.endswith('{"d":10,"delta":-1},{"d":12.3,"delta":0}]'), \
            f"two laps closing at the same length pair their closing points: {same}"
        assert "12.3" not in apart and "17.1" not in apart, \
            f"closing points at different distances are never paired: {apart}"
        assert max_d == "17.4", max_d
        assert edge.startswith("0.02 km") and "100.0 / -" in edge, \
            f"past B's closing point the hover shows A only: {edge!r}"


def t_telemetry_charts_without_lap_a_show_b_alone():
    out = _tm_node("""
const trace = ts => ts.map((t, i) => ({d: i * 5, t, speed_kmh: 100 + i, throttle: 50, brake: 0,
                                       steer_deg: i - 1, gear: 3}));
const drawn = () => {
  const n = {};
  const walk = e => {
    const a = e.attrs || {};
    const k = e.tagName + (a.class ? '.' + a.class : '');
    n[k] = (n[k] || 0) + 1;
    if (e.tagName === 'text' && e._t === 'no lap A to compare') n.note = (n.note || 0) + 1;
    e.kids.forEach(walk);
  };
  $('tm-charts').kids.forEach(walk);
  return ['path.tm-a', 'path.tm-b', 'path.tm-gain', 'path.tm-loss', 'line.tm-zero', 'note']
    .map(k => n[k] || 0).join(',');
};
tmState.lapA = null; tmState.lapB = {trace: trace([0, 1, 2, 3])};
tmRenderCharts();
const alone = drawn();
tmState.lapA = {trace: trace([0, 1.1, 2.2])};
tmRenderCharts();
const pair = drawn();
tmHover({clientX: 799});
const late = tmState.chart.panels[5].read.textContent;
tmClearPair('');
console.log([alone, pair, JSON.stringify(late), String(tmState.chart), $('tm-charts').kids.length].join('|'));""")
    if out is not None:
        assert out.strip() == '0,5,0,0,1,1|5,6,1,1,2,0|""|null|0', \
            f"without A only B is drawn, the delta panel holds a note and no zero line: {out!r}"


def t_telemetry_times_read_as_lap_times():
    tm = _tm_script(_cc_page())
    out = _run_js(_tm_fn(tm, "tmTime") + _tm_fn(tm, "tmSigned") + """
console.log([tmTime(83.456), tmTime(9.5), tmTime(3600), tmSigned(0.25), tmSigned(-1),
             tmSigned(0.0001)].join("|"));""")
    if out is not None:
        assert out.strip() == "1:23.456|0:09.500|60:00.000|+0.250|-1.000|0.000", out


def t_telemetry_track_label_names_a_reverse_layout():
    tm = _tm_script(_cc_page())
    out = _run_js(_tm_fn(tm, "tmTrackName") + _tm_fn(tm, "tmTrackLabel") + """
console.log([tmTrackLabel(null), tmTrackLabel({candidates: []}),
             tmTrackLabel({track: 'Alsace', layout: 'Village', reverse: true}),
             tmTrackLabel({track: 'Ring', layout: '', reverse: false})].join('|'));""")
    if out is not None:
        assert out.strip() == "track unknown|track ambiguous|Alsace - Village (reverse)|Ring", \
            f"the Laps label names the layout like the picker: {out!r}"


def t_telemetry_track_label_drops_a_layout_the_track_name_carries():
    tm = _tm_script(_cc_page())
    out = _run_js(_tm_fn(tm, "tmTrackName") + _tm_fn(tm, "tmTrackLabel") + _tm_fn(tm, "tmLapTrack") + """
console.log([tmTrackLabel({track: 'Grand Valley - Highway 1', layout: 'Highway 1', reverse: false}),
             tmTrackLabel({track: 'Grand Valley - Highway 1', layout: 'highway 1', reverse: true}),
             tmLapTrack({track: 'Grand Valley - Highway 1', layout: 'Highway 1'}),
             tmLapTrack({track: 'Alsace', layout: 'Village'}),
             tmLapTrack({track: '', layout: ''})].join('|'));""")
    if out is not None:
        assert out.strip() == ("Grand Valley - Highway 1|Grand Valley - Highway 1 (reverse)|"
                               "Grand Valley - Highway 1|Alsace - Village|track unknown"), \
            f"a layout already ending the track name is not repeated: {out!r}"


def t_telemetry_list_rows_keep_their_height_and_scroll():
    page = _cc_page()
    item = re.search(r"\n  \.tmitem \{([^}]*)\}", page).group(1)
    assert "flex-shrink:0" in item and "min-height:0" not in item, \
        f"a lap row must not shrink to fit the capped list, the list scrolls instead: {item!r}"
    wrap = re.search(r"\n  \.tmsecwrap \{([^}]*)\}", page)
    assert wrap is None or "max-height" not in wrap.group(1), \
        "the mini-sector table flows with the page instead of a nested scroll box"


def t_telemetry_lap_rows_show_status_badges_markers_and_only_odd_cars():
    out = _tm_node("""
const mk = (n, t, status, reason, car) => Object.assign(lap('R', n, t, 7),
  {status, reason: reason || '', car: car || 'Car'});
tmState.recLaps = [mk(0, 6, 'not counted', 'paused, loading or off track'), mk(1, 116, 'reference'),
                   mk(2, 117, 'counted'), mk(3, 110, 'reference'), mk(4, 111, 'counted', '', 'Other Car')];
tmState.b = tmKey(tmState.recLaps[2]);
tmState.a = tmKey(tmState.recLaps[3]);
tmRenderLaps();
const rows = $('tm-laps').kids;
const span = (r, cls) => r.kids.find(k => (k.className || '').split(' ').includes(cls));
console.log(rows.map(r => r.className + '=' + r.textContent).join('|'));
console.log(rows.map(r => span(r, 'tmtime').className).join('|'));
console.log([span(rows[0], 'tmbadge').title, span(rows[1], 'tmbadge').title].join('|'));
tmPick('a', tmKey(tmState.recLaps[1]));
console.log($('tm-laps').kids.map(r => r.className).join('|'));""")
    if out is not None:
        rows, times, titles, after = out.strip().split("\n")
        assert rows == ("tmitem nc=Lap 0not counted0:06.000paused, loading or off track|"
                        "tmitem=Lap 1ref1:56.000|tmitem sel=Lap 21:57.000|"
                        "tmitem is-a=Lap 3ref1:50.000|tmitem=Lap 41:51.000Other Car"), \
            f"one line per lap; a car shows only where it differs from the recording: {rows!r}"
        assert times == "tmtime|tmtime|tmtime|tmtime best|tmtime", \
            f"the fastest counted lap is marked: {times!r}"
        assert titles.startswith("Not counted: paused, loading or off track") \
            and "fastest lap so far" in titles, f"badges explain themselves: {titles!r}"
        assert after == "tmitem nc|tmitem is-a|tmitem sel|tmitem|tmitem", \
            f"picking lap A moves the A marker: {after!r}"


def t_telemetry_pickers_name_the_lap_before_the_recording():
    out = _tm_node("""
const l1 = lap('R', 1, 80, 1), l2 = lap('R', 2, 79.5, 1);
tmState.pool = {laps: [l1, l2]}; tmState.a = tmKey(l1); tmState.b = tmKey(l2);
tmFillPickers(l2);
const one = $('tm-a').options.map(o => o.textContent).join(',');
const nc = Object.assign(lap('S', 3, 81, 1), {status: 'not counted'});
tmState.pool = {laps: [l1, l2, nc]};
tmFillPickers(l2);
console.log(one + '|' + $('tm-a').options.map(o => o.textContent).join(','));""")
    if out is not None:
        assert out.strip() == ("Lap 1 · 1:20.000|Lap 1 · 1:20.000 · R,"
                               "Lap 3 · 1:21.000 · S · not counted"), \
            f"lap and time first, the recording only when the pool spans several: {out!r}"


def t_telemetry_charts_label_the_value_range_and_the_final_delta():
    out = _tm_node("""
const trace = ts => ts.map((t, i) => ({d: i * 5, t, speed_kmh: 100 + i, throttle: 50, brake: 0,
                                       steer_deg: i - 1, gear: 3}));
tmState.lapA = {trace: trace([0, 1.1, 2.2])};
tmState.lapB = {trace: trace([0, 1, 2, 3])};
tmRenderCharts();
const axis = [], fin = [];
$('tm-charts').kids.forEach(e => {
  const c = (e.attrs || {}).class || '';
  if (c === 'tm-axis') axis.push(e.textContent);
  if (c.startsWith('tm-final')) fin.push(c + '=' + e.textContent);
});
console.log(axis.join(',') + '|' + fin.join(','));""")
    if out is not None:
        assert out.strip() == ("103,100,100,0,100,0,2,0,-2,3,+0.20,0,-0.20|"
                               "tm-final gain=-0.200 s"), \
            f"each panel labels its range top to bottom, delta its zero and final value: {out!r}"


def t_telemetry_map_and_sectors_carry_legends_and_distances():
    page = _cc_page()
    for lid in ("tm-map-legend", "tm-sec-legend"):
        assert f'id="{lid}"' in page, f"#{lid} explains the colours"
    out = _tm_node("""
const pt = i => ({d: i * 5, t: i, speed_kmh: 100, throttle: 0, brake: 0, steer_deg: 0, gear: 3,
                  x: i, z: i % 3});
const trace = Array.from({length: 81}, (_, i) => pt(i));
tmClearPair('');
const cleared = [$('tm-map-legend').hidden, $('tm-sec-legend').hidden].join(',');
tmState.sectorM = 200;
tmState.lapA = {trace, sectors: [10, 11], time_s: 21};
tmState.lapB = {trace, sectors: [9.5, 11.2], time_s: 20.7};
tmState.pool = {laps: [], best_sectors: [9.5, 11], theoretical_best: 20.5};
tmRender();
const shown = [$('tm-map-legend').hidden, $('tm-sec-legend').hidden].join(',');
const t = $('tm-sectors');
const cells = sec => sec.kids.map(r => r.kids.map(c => c.textContent).join(' '));
console.log([cleared, shown, cells(t.kids[0])[0], cells(t.kids[1])[1]].join('|'));""")
    if out is not None:
        assert out.strip() == ("true,true|false,false|Sector km A B B-A Best|"
                               "2 0.2–0.4 11.000 11.200 +0.200 11.000"), \
            f"legends follow the drawn lap and each sector names its distance: {out!r}"


def t_telemetry_summary_shows_the_report_figures_and_a_clickable_trend():
    page = _cc_page()
    assert 'id="tm-summary"' in page, "the view carries the per-recording summary"
    out = _tm_node("""
const l = (n, t, status, st) => Object.assign(lap('R', n, t, 7), {status, start_t_s: st});
tmState.recLaps = [l(1, 116, 'reference', 10), l(0, 6, 'not counted', 4), l(2, 110, 'counted', 130)];
tmRenderSummary({laps_total: 3, laps_counted: 2, groups: [{
  track: 'Grand Valley - Highway 1', car: "Nissan GT-R GT500 '99", laps_total: 3, laps_counted: 2,
  best_s: 110.104, best_lap: {n: 3, lap: 2}, theoretical_s: 108.937, consistency_s: 1.9991,
  fuel_per_lap_l: 7.744, tyre_avg_c: [78.61, 74.4, 71.6, 69.2],
  trend: [{n: 1, lap: 0, time_s: 6, counted: false, best: false},
          {n: 2, lap: 1, time_s: 116, counted: true, best: false},
          {n: 3, lap: 2, time_s: 110.104, counted: true, best: true}]}]});
const box = $('tm-summary');
const walk = (e, out) => { out.push(e); (e.kids || []).forEach(k => walk(k, out)); return out; };
const all = walk(box, []);
const tiles = all.filter(e => e.className === 'tmtile').map(e => e.textContent);
const dots = all.filter(e => e.tagName === 'circle').map(e => (e.attrs || {}).class);
const hit = all.filter(e => e.tagName === 'circle')[2];
if (typeof hit.onclick === 'function') hit.onclick();
console.log([box.hidden, tiles.join(','), dots.join(','), tmState.b].join('|'));
tmRenderSummary(null);
console.log(String(box.hidden));""")
    if out is not None:
        first, second = out.strip().split("\n")
        assert first == ("false|1:50.104best lap · lap 2,1:48.937theoretical best,"
                         "± 1.999 sconsistency,7.74 Lfuel per lap,2 of 3counted laps,"
                         "78.6 · 74.4 · 71.6 · 69.2 °Ctyres FL · FR · RL · RR|"
                         "tm-tr nc,tm-tr,tm-tr best|R|1|2"), \
            f"the summary repeats the report's figures and a trend dot picks lap B: {first!r}"
        assert second == "true", "no summary hides the section"


def t_telemetry_recording_rows_mark_open_files_and_unindexed_laps():
    tm = _tm_script(_cc_page())
    fn = tm[tm.index("function tmRenderRecs()"):tm.index("async function tmSelectRec(")]
    assert "r.recording ? ' (recording)'" in fn and "r.partial ? ' (unclosed)'" in fn
    assert "r.laps == null ? ''" in fn, "the list has no lap count before the first index"


_TM_HARNESS = r"""
class El {
  constructor(tag) { this.tagName = tag; this.kids = []; this.cells = []; this._t = '';
                     this.hidden = false; this.disabled = false; this.className = '';
                     this.value = ''; this.title = ''; this.selected = false; }
  get textContent() { return this._t + this.kids.map(k => k.textContent).join(''); }
  set textContent(v) { this._t = String(v); this.kids = []; this.cells = []; }
  appendChild(c) { this.kids.push(c); return c; }
  append(...c) { c.forEach(x => this.appendChild(x)); }
  get options() { return this.kids.filter(k => k.tagName === 'option'); }
  createTHead() { return this.appendChild(new El('sec')); }
  createTBody() { return this.appendChild(new El('sec')); }
  createTFoot() { return this.appendChild(new El('sec')); }
  insertRow() { return this.appendChild(new El('tr')); }
  insertCell() { const c = this.appendChild(new El('td')); this.cells.push(c); return c; }
  setAttribute(k, v) { (this.attrs = this.attrs || {})[k] = String(v); }
  addEventListener() {}
  getBoundingClientRect() { return {left: 0, width: 800}; }
}
const window = {addEventListener() {}};
const els = {};
const $ = id => els[id] || (els[id] = new El(id));
const document = {createElement: t => new El(t), createElementNS: (n, t) => new El(t)};
const pending = [];
const calls = [];
globalThis.fetch = url => new Promise(res => { calls.push(url); pending.push({url, res}); });
function answer(part, data) {
  const hit = pending.filter(p => p.url.includes(part));
  hit.forEach(p => { pending.splice(pending.indexOf(p), 1); p.res({json: async () => data}); });
  return hit.length;
}
const tick = async () => { for (let i = 0; i < 20; i++) await new Promise(r => setTimeout(r, 0)); };
const lap = (rec, n, t, car) => ({rec, session: 1, lap: n, time_s: t, status: 'counted',
  car: 'Car', car_id: car, track_id: 't1', track: 'Track', layout: '', sectors: [t / 2, t / 2],
  trace: []});
const recLaps = (rec, laps) => ({ok: true, recording: {rec, track: null}, laps});
const pool = laps => ({ok: true, laps, best_sectors: [], theoretical_best: null,
                       reference: laps[0] || null});
"""


def _tm_node(body):
    """stdout of `body` run against the Telemetry block with a fake DOM and fetch."""
    page = _cc_page()
    return _run_js(_TM_HARNESS + "const JOB_POLL_MS = 0;\n" + _job_fns(page) + _tm_script(page)
                   + "\n(async () => {\n" + body + "\n})();")


def t_telemetry_open_recording_is_not_indexed_on_load():
    tm = _tm_script(_cc_page())
    load = _tm_fn(tm, "tmLoad")
    assert "!r.recording" in load, "the first selection skips the file the relay is writing"


def t_telemetry_late_laps_answer_for_another_recording_is_dropped():
    out = _tm_node("""
tmSelectRec('X'); tmSelectRec('X'); tmSelectRec('Y');
await tick();
const xCalls = calls.filter(u => u.includes('rec=X')).length;
answer('rec=Y', recLaps('Y', [lap('Y', 1, 90, 7)]));
await tick();
answer('rec=X', recLaps('X', [lap('X', 1, 80, 7), lap('X', 2, 81, 7)]));
await tick();
console.log([xCalls, tmState.rec, tmState.recLaps.map(tmKey).join(','), tmState.b].join(' '));""")
    if out is not None:
        assert out.strip() == "1 Y Y|1|1 Y|1|1", \
            f"one request per recording and the late answer for X must not win: {out!r}"


def t_telemetry_late_pool_answer_for_another_lap_is_dropped():
    out = _tm_node("""
const l1 = lap('R', 1, 80, 1), l2 = lap('R', 2, 79, 2);
tmState.recLaps = [l1, l2];
tmSelectB(l1); tmSelectB(l2); tmSelectB(l2);
await tick();
const n2 = calls.filter(u => u.includes('car=2')).length;
const status = $('tm-sec-sub').textContent.replace(/\\u2026/g, '...');
answer('car=2', pool([l2, lap('S', 1, 78, 2)]));
await tick();
answer('car=1', pool([l1]));
await tick();
answer('/lap?', {ok: true, lap: lap('S', 1, 78, 2), sector_m: 200});
await tick();
console.log([n2, status, tmState.b, tmState.pool.laps.length].join('|'));""")
    if out is not None:
        assert out.strip() == "1|Loading comparable laps...|R|1|2|2", \
            f"one pool request per query, a loading note and the newest lap wins: {out!r}"


def t_telemetry_pool_indexes_missing_recordings_through_one_job():
    out = _tm_node("""
const l1 = lap('R', 1, 80, 1), l2 = lap('R', 2, 79, 1);
tmState.recLaps = [l1, l2];
tmSelectB(l1);
await tick();
answer('car=1', Object.assign(pool([l1]), {unindexed: 2}));
await tick();
const status = $('tm-sec-sub').textContent.replace(/\\u2026/g, '...');
answer('/api/op/telemetry-index', {ok: true, job_id: 'j'});
await tick();
answer('/api/jobs/j', {ok: true, running: true, exit_code: null});
await tick();
answer('/api/jobs/j', {ok: true, running: false, exit_code: 0});
await tick();
const asked = calls.filter(u => u.includes('car=1')).length;
answer('car=1', Object.assign(pool([l1, l2, lap('S', 1, 78, 1)]), {unindexed: 1}));
await tick();
console.log([status, asked, calls.filter(u => u.includes('telemetry-index')).length,
             tmState.pool.laps.length, tmState.a].join('|'));""")
    if out is not None:
        assert out.strip() == "Indexing 2 recordings...|2|1|3|S|1|1", \
            f"the pool runs telemetry-index once, then asks again and draws what it has: {out!r}"


def t_telemetry_failed_index_job_runs_once_and_says_why():
    out = _tm_node("""
const l1 = lap('R', 1, 80, 1), l2 = lap('R', 2, 79, 1);
tmState.recLaps = [l1, l2];
tmSelectB(l1);
await tick();
answer('car=1', Object.assign(pool([l1]), {unindexed: 1}));
await tick();
answer('/api/op/telemetry-index', {ok: true, job_id: 'j'});
await tick();
answer('/api/jobs/j', {ok: true, running: false, exit_code: 1,
                       lines: ['B: not indexed (bad header)', '0 recording(s) indexed, 1 failed']});
await tick();
answer('car=1', Object.assign(pool([l1]), {unindexed: 1}));
await tick();
tmSelectB(l2);
await tick();
answer('car=1', Object.assign(pool([l1, l2]), {unindexed: 1}));
await tick();
const once = calls.filter(u => u.includes('telemetry-index')).length;
const note = [$('tm-note').hidden, $('tm-note').textContent, $('tm-err').textContent];
tmLoad();
await tick();
answer('/recordings', {ok: true, recordings: []});
await tick();
tmState.recLaps = [l1];
tmSelectB(l1);
await tick();
answer('car=1', Object.assign(pool([l1]), {unindexed: 1}));
await tick();
console.log([once, JSON.stringify(note), calls.filter(u => u.includes('telemetry-index')).length,
             tmState.pool ? tmState.pool.laps.length : '-'].join('|'));""")
    if out is not None:
        once, note, again, _n = out.strip().split("|")
        assert once == "1", f"a failing job is not restarted for every lap B: {out!r}"
        hidden, text, err = json.loads(note)
        assert hidden is False and "B: not indexed (bad header)" in text and err == "", \
            f"the failed lines show once as a note, not as an error: {note}"
        assert again == "2", f"Refresh re-arms the job: {out!r}"


def t_telemetry_index_job_running_elsewhere_is_awaited_quietly():
    out = _tm_node("""
const l1 = lap('R', 1, 80, 1);
tmState.recLaps = [l1];
tmSelectB(l1);
await tick();
answer('car=1', Object.assign(pool([l1]), {unindexed: 1}));
await tick();
answer('/api/op/telemetry-index', {ok: false, error: 'telemetry-index is already running',
                                   job_id: 'other'});
await tick();
const waiting = [$('tm-note').hidden, $('tm-note').textContent, $('tm-err').textContent];
answer('/api/jobs/other', {ok: true, running: false, exit_code: 0, lines: []});
await tick();
const asked = calls.filter(u => u.includes('car=1')).length;
answer('car=1', Object.assign(pool([l1, lap('S', 1, 78, 1)]), {unindexed: 0}));
await tick();
console.log(JSON.stringify([waiting, asked, tmState.pool.laps.length, $('tm-err').textContent,
                            $('tm-note').hidden]));""")
    if out is not None:
        waiting, asked, n, err, hidden = json.loads(out)
        assert waiting[0] is False and "another window" in waiting[1] and waiting[2] == "", \
            f"a job from another tab is a neutral note, not a red banner: {waiting}"
        assert (asked, n, err, hidden) == (2, 2, "", True), \
            f"the page waits for that job, then asks once more: {out!r}"


def t_telemetry_index_join_note_never_lands_on_a_reset_view():
    out = _tm_node("""
const l1 = lap('R', 1, 80, 1);
tmState.recLaps = [l1];
tmSelectB(l1);
await tick();
answer('car=1', Object.assign(pool([l1]), {unindexed: 1}));
await tick();
tmReset();
answer('/api/op/telemetry-index', {ok: false, error: 'telemetry-index is already running',
                                   job_id: 'other'});
await tick();
console.log(JSON.stringify([$('tm-note').hidden, $('tm-note').textContent]));""")
    if out is not None:
        hidden, text = json.loads(out)
        assert hidden is True, f"a join answered after a reset leaves the new view alone: {text!r}"


_TM_UNINDEXED = """
const unindexed = rec => ({ok: true, unindexed: 1,
                           note: rec + ' has no lap index yet: racecast telemetry index builds it'});
const jobs = () => calls.filter(u => u.includes('telemetry-index')).length;
const asks = part => calls.filter(u => u.includes(part)).length;
tmState.recs = [{rec: 'X', indexed: false, laps: null, duration_s: 60, size: 1e6, track: null}];
"""


def t_telemetry_first_open_indexes_the_recording_through_the_job():
    out = _tm_node(_TM_UNINDEXED + """
tmSelectRec('X');
await tick();
const status = $('tm-laps').textContent.replace(/\\u2026/g, '...');
answer('rec=X', unindexed('X'));
await tick();
answer('/api/op/telemetry-index', {ok: true, job_id: 'j'});
await tick();
answer('/api/jobs/j', {ok: true, running: false, exit_code: 0, lines: ['X: 2 laps']});
await tick();
answer('rec=X', recLaps('X', [lap('X', 1, 80, 7), lap('X', 2, 81, 7)]));
await tick();
console.log(JSON.stringify([status, jobs(), asks('rec=X'), tmState.recLaps.length, tmState.b,
                            $('tm-err').textContent, tmState.recs[0].indexed]));""")
    if out is not None:
        assert json.loads(out) == ["Indexing...", 1, 2, 2, "X|1|2", "", True], \
            f"the first open runs telemetry-index once, then asks again and shows the laps: {out}"


def t_telemetry_recording_the_job_cannot_index_shows_its_line_once():
    out = _tm_node(_TM_UNINDEXED + """
tmSelectRec('X');
await tick();
answer('rec=X', unindexed('X'));
await tick();
answer('/api/op/telemetry-index', {ok: true, job_id: 'j'});
await tick();
answer('/api/jobs/j', {ok: true, running: false, exit_code: 1,
                       lines: ['X: not indexed (bad data)', '0 recording(s) indexed, 1 failed']});
await tick();
answer('rec=X', unindexed('X'));
await tick();
const first = $('tm-err').textContent;
tmSelectRec('X');
await tick();
answer('rec=X', unindexed('X'));
await tick();
console.log(JSON.stringify([first, $('tm-err').textContent, jobs(), asks('rec=X'),
                            tmState.recLaps.length, tmState.recs[0].indexed]));""")
    if out is not None:
        assert json.loads(out) == ["X: not indexed (bad data)", "X: not indexed (bad data)",
                                   1, 3, 0, False], \
            f"a recording the job cannot index shows the job's line and starts no second job: {out}"


def t_telemetry_stale_lap_runs_the_index_job_and_asks_once_more():
    out = _tm_node(_TM_UNINDEXED + """
const got = tmFetchLap('X|1|2');
await tick();
answer('/lap?', unindexed('X'));
await tick();
answer('/api/op/telemetry-index', {ok: true, job_id: 'j'});
await tick();
answer('/api/jobs/j', {ok: true, running: false, exit_code: 0, lines: []});
await tick();
answer('/lap?', {ok: true, lap: lap('X', 2, 81, 7), sector_m: 200});
const r = await got;
console.log(JSON.stringify([jobs(), asks('/lap?'), r.lap && r.lap.lap, r.error || '',
                            tmLapCache.has('X|1|2')]));""")
    if out is not None:
        assert json.loads(out) == [1, 2, 2, "", True], \
            f"a stale lap runs telemetry-index, then asks once more: {out}"


def t_telemetry_set_track_on_a_stale_index_runs_the_job_first():
    out = _tm_node(_TM_UNINDEXED + """
globalThis.confirmModal = async () => true;
tmState.rec = 'X';
const pick = $('tm-track-pick'), o = new El('option');
o.value = 't9'; o.textContent = 'Nine';
pick.appendChild(o); pick.value = 't9'; pick.selectedIndex = 0;
const gen = tmLapGen;
tmLearn();
await tick();
answer('/api/telemetry/learn', unindexed('X'));
await tick();
const kept = tmLapGen === gen;
answer('/api/op/telemetry-index', {ok: true, job_id: 'j'});
await tick();
answer('/api/jobs/j', {ok: true, running: false, exit_code: 0, lines: []});
await tick();
answer('/api/telemetry/learn', {ok: true, track: {id: 't9'}, learned: true});
await tick();
console.log(JSON.stringify([kept, jobs(), asks('/api/telemetry/learn'), tmLapGen > gen,
                            $('tm-err').textContent]));""")
    if out is not None:
        assert json.loads(out) == [True, 1, 2, True, ""], \
            f"Set track runs telemetry-index on a stale index, then learns once more: {out}"


def t_telemetry_failure_outside_the_job_tail_still_names_the_recording():
    out = _tm_node(_TM_UNINDEXED + """
tmSelectRec('X');
await tick();
answer('rec=X', unindexed('X'));
await tick();
answer('/api/op/telemetry-index', {ok: true, job_id: 'j'});
await tick();
const tail = Array.from({length: 19}, (_, i) => 'R' + i + ': 4 laps')
  .concat(['19 recording(s) indexed, 2 failed: W, X']);
answer('/api/jobs/j', {ok: true, running: false, exit_code: 1, lines: tail});
await tick();
answer('rec=X', unindexed('X'));
await tick();
console.log(JSON.stringify([$('tm-err').textContent, $('tm-note').textContent]));""")
    if out is not None:
        err, note = json.loads(out)
        want = "19 recording(s) indexed, 2 failed: W, X"
        assert want in err and want in note, \
            f"the summary line names a failure whose own line left the tail: {out}"


def t_telemetry_set_track_shows_indexing_while_its_job_runs():
    out = _tm_node(_TM_UNINDEXED + """
globalThis.confirmModal = async () => true;
tmState.rec = 'X';
const pick = $('tm-track-pick'), o = new El('option');
o.value = 't9'; o.textContent = 'Nine';
pick.appendChild(o); pick.value = 't9'; pick.selectedIndex = 0;
$('tm-learn').textContent = 'Set track';
tmLearn();
await tick();
answer('/api/telemetry/learn', unindexed('X'));
await tick();
answer('/api/op/telemetry-index', {ok: true, job_id: 'j'});
await tick();
const during = $('tm-learn').textContent.replace(/\\u2026/g, '...');
answer('/api/jobs/j', {ok: true, running: false, exit_code: 0, lines: []});
await tick();
const retry = $('tm-learn').textContent;
answer('/api/telemetry/learn', {ok: true, track: {id: 't9'}, learned: true});
await tick();
console.log(JSON.stringify([during, retry, $('tm-learn').textContent]));""")
    if out is not None:
        assert json.loads(out) == ["Indexing...", "Set track", "Set track"], \
            f"Set track shows Indexing while its job runs and its label afterwards: {out}"


def t_telemetry_new_data_version_allows_one_more_index_job():
    out = _tm_node(_TM_UNINDEXED + """
const v = (d, ver) => Object.assign(d, {data_version: ver});
const finish = async (id, code) => {
  answer('/api/op/telemetry-index', {ok: true, job_id: id});
  await tick();
  answer('/api/jobs/' + id, {ok: true, running: false, exit_code: code,
                             lines: code ? ['X: not indexed (bad data)'] : []});
  await tick();
};
tmSelectRec('X');
await tick();
answer('rec=X', v(unindexed('X'), 'v1'));
await tick();
await finish('j1', 1);
answer('rec=X', v(unindexed('X'), 'v1'));
await tick();
tmSelectRec('X');
await tick();
answer('rec=X', v(unindexed('X'), 'v1'));
await tick();
const sameVersion = jobs();
tmSelectRec('X');
await tick();
answer('rec=X', v(unindexed('X'), 'v2'));
await tick();
await finish('j2', 1);
answer('rec=X', v(unindexed('X'), 'v2'));
await tick();
const err = $('tm-err').textContent;
tmSelectRec('X');
await tick();
answer('rec=X', v(unindexed('X'), 'v2'));
await tick();
const l1 = lap('R', 1, 80, 1);
tmState.recLaps = [l1];
tmSelectB(l1);
await tick();
answer('car=1', v(Object.assign(pool([l1]), {unindexed: 1}), 'v3'));
await tick();
await finish('j3', 0);
answer('car=1', v(Object.assign(pool([l1]), {unindexed: 1}), 'v3'));
await tick();
tmSelectB(l1);
await tick();
answer('car=1', v(Object.assign(pool([l1]), {unindexed: 1}), 'v3'));
await tick();
console.log(JSON.stringify([sameVersion, err, jobs()]));""")
    if out is not None:
        assert json.loads(out) == [1, "X: not indexed (bad data)", 3], \
            f"one index job per GT7 data version, for a recording and for a pool: {out}"


def t_telemetry_joined_index_job_records_no_data_version():
    out = _tm_node(_TM_UNINDEXED + """
const v = (d, ver) => Object.assign(d, {data_version: ver});
tmSelectRec('X');
await tick();
answer('rec=X', v(unindexed('X'), 'v1'));
await tick();
answer('/api/op/telemetry-index', {ok: false, error: 'telemetry-index is already running',
                                   job_id: 'other'});
await tick();
answer('/api/jobs/other', {ok: true, running: false, exit_code: 0, lines: []});
await tick();
const joinedVer = tmIndexedVer;
answer('rec=X', v(unindexed('X'), 'v1'));
await tick();
answer('/api/op/telemetry-index', {ok: true, job_id: 'j2'});
await tick();
answer('/api/jobs/j2', {ok: true, running: false, exit_code: 0, lines: []});
await tick();
answer('rec=X', recLaps('X', [lap('X', 1, 80, 7), lap('X', 2, 81, 7)]));
await tick();
console.log(JSON.stringify([joinedVer, jobs(), tmIndexedVer, tmState.recLaps.length,
                            $('tm-err').textContent]));""")
    if out is not None:
        assert json.loads(out) == [None, 2, "v1", 2, ""], \
            f"a joined run ran for an unknown version, so this page runs its own once: {out}"


def t_telemetry_caller_joining_a_run_for_an_older_version_starts_one_more():
    out = _tm_node(_TM_UNINDEXED + """
const v = (d, ver) => Object.assign(d, {data_version: ver});
const a = tmFetchLap('X|1|1');
await tick();
answer('rec=X&', v(unindexed('X'), 'v1'));
await tick();
const b = tmFetchLap('Y|1|1');
await tick();
answer('rec=Y&', v(unindexed('Y'), 'v2'));
await tick();
answer('/api/op/telemetry-index', {ok: true, job_id: 'j1'});
await tick();
answer('/api/jobs/j1', {ok: true, running: false, exit_code: 0, lines: []});
await tick();
answer('rec=X&', {ok: true, lap: lap('X', 1, 80, 7), sector_m: 200});
answer('rec=Y&', v(unindexed('Y'), 'v2'));
await tick();
answer('/api/op/telemetry-index', {ok: true, job_id: 'j2'});
await tick();
answer('/api/jobs/j2', {ok: true, running: false, exit_code: 0, lines: []});
await tick();
answer('rec=Y&', {ok: true, lap: lap('Y', 1, 79, 7), sector_m: 200});
const [ra, rb] = [await a, await b];
console.log(JSON.stringify([jobs(), ra.lap && ra.lap.rec, rb.lap && rb.lap.rec,
                            rb.error || '', tmIndexedVer]));""")
    if out is not None:
        assert json.loads(out) == [2, "X", "Y", "", "v2"], \
            f"B joined A's run for v1, so exactly one more job runs for v2: {out}"


def t_telemetry_new_lap_generation_forgets_the_old_job_lines():
    out = _tm_node(_TM_UNINDEXED + """
tmIndexAll();
await tick();
answer('/api/op/telemetry-index', {ok: true, job_id: 'j'});
await tick();
answer('/api/jobs/j', {ok: true, running: false, exit_code: 1,
                       lines: ['X: not indexed (bad data)']});
await tick();
const before = tmIndexLines.length;
tmDropLaps();
const afterDrop = tmIndexLines.length;
tmIndexLines = ['X: not indexed (bad data)'];
tmReset();
console.log(JSON.stringify([before, afterDrop, tmIndexLines.length]));""")
    if out is not None:
        assert json.loads(out) == [1, 0, 0], \
            f"a new lap generation never shows lines of an older run: {out}"


def t_telemetry_lap_fetch_from_an_old_generation_stops_after_the_job():
    out = _tm_node(_TM_UNINDEXED + """
const got = tmFetchLap('X|1|2');
await tick();
answer('/lap?', unindexed('X'));
await tick();
answer('/api/op/telemetry-index', {ok: true, job_id: 'j'});
await tick();
tmDropLaps();
answer('/api/jobs/j', {ok: true, running: false, exit_code: 0, lines: []});
let r = null;
got.then(x => { r = x; });
await tick();
console.log(JSON.stringify([asks('/lap?'), r ? r.error : 'still asking', r ? r.lap : null]));""")
    if out is not None:
        assert json.loads(out) == [1, "", None], \
            f"a lap fetch whose generation ended asks no more and shows no error: {out}"


def t_telemetry_index_job_refreshes_the_recording_list():
    out = _tm_node("""
const l1 = lap('R', 1, 80, 1);
tmState.recs = [{rec: 'R', indexed: true, laps: 1, duration_s: 60, size: 1e6, track: null},
                {rec: 'S', indexed: false, laps: null, duration_s: 60, size: 1e6, track: null}];
tmState.recLaps = [l1];
tmSelectB(l1);
await tick();
answer('car=1', Object.assign(pool([l1]), {unindexed: 1}));
await tick();
answer('/api/op/telemetry-index', {ok: true, job_id: 'j'});
await tick();
answer('/api/jobs/j', {ok: true, running: false, exit_code: 0, lines: []});
await tick();
answer('/recordings', {ok: true, recordings: [
  {rec: 'R', indexed: true, laps: 1, duration_s: 60, size: 1e6, track: null},
  {rec: 'S', indexed: true, laps: 7, duration_s: 60, size: 1e6, track: null}]});
await tick();
console.log($('tm-recs').textContent);""")
    if out is not None:
        assert "7 laps" in out and "not indexed yet" not in out, \
            f"the list shows what the job indexed: {out!r}"


def t_telemetry_reset_drops_the_shared_index_job_and_refresh_the_lap_requests():
    out = _tm_node("""
tmIndexAll();
await tick();
const during = tmIndexing !== null;
tmReset();
const afterReset = tmIndexing;
tmShared('/api/telemetry/lap?rec=R&session=1&lap=1');
tmLoad();
console.log([during, String(afterReset), tmLapsReq.size].join(','));""")
    if out is not None:
        assert out.strip() == "true,null,0", \
            f"a reset forgets the old profile's job and Refresh the pending lap requests: {out!r}"


def t_telemetry_pool_job_answer_for_another_lap_is_dropped():
    out = _tm_node("""
const l1 = lap('R', 1, 80, 1), l2 = lap('R', 2, 79, 2);
tmState.recLaps = [l1, l2];
tmSelectB(l1);
await tick();
answer('car=1', Object.assign(pool([l1]), {unindexed: 1}));
await tick();
answer('/api/op/telemetry-index', {ok: false, error: 'telemetry-index is already running'});
tmSelectB(l2);
await tick();
answer('car=2', pool([l2]));
await tick();
console.log([calls.filter(u => u.includes('car=1')).length, tmState.b, tmState.pool.laps.length,
             $('tm-err').textContent].join('|'));""")
    if out is not None:
        assert out.strip() == "1|R|1|2|1|", \
            f"a pool whose lap was replaced neither asks again nor shows its job error: {out!r}"


def t_telemetry_clearing_the_pair_drops_a_pending_load():
    out = _tm_node("""
const b = lap('R', 1, 80, 1);
tmSelectB(b);
await tick();
answer('car=1', pool([b]));
await tick();
tmSelectB(b);
await tick();
answer('/lap?', {ok: true, lap: b, sector_m: 200});
await tick();
console.log([String(tmState.lapB), $('tm-sec-sub').textContent.replace(/\\u2026/g, '...')].join('|'));""")
    if out is not None:
        assert out.strip() == "null|Loading comparable laps...", \
            f"a lap answer from before the clear must not draw under the loading note: {out!r}"


def t_telemetry_reference_is_the_fastest_other_lap():
    out = _tm_node("""
const b = lap('R', 3, 79, 1);
tmSelectB(b);
await tick();
answer('car=1', pool([b, lap('R', 1, 80, 1), lap('R', 2, 81, 1)]));
await tick();
const multi = tmState.a;
tmSelectB(b);
await tick();
answer('car=1', pool([b]));
await tick();
answer('/lap?', {ok: true, lap: b, sector_m: 200});
await tick();
console.log([multi, String(tmState.a), $('tm-a').options.length, $('tm-a').disabled,
             $('tm-sec-sub').textContent.replace(/\\u00b7/g, '-')].join('|'));""")
    if out is not None:
        assert out.strip() == "R|1|1|null|1|true|200 m - no other lap", \
            f"A is the fastest lap other than B, and a lone lap says so: {out!r}"


def t_telemetry_errors_leave_placeholders_and_no_stale_banner():
    out = _tm_node("""
tmState.recs = [{rec: 'X'}, {rec: 'Y'}];
tmSelectRec('X');
await tick();
answer('rec=X', {ok: false, error: 'recording in progress: stop the recording to analyse it'});
await tick();
const placeholder = [$('tm-a').options.length, $('tm-a').options[0].disabled, $('tm-a').disabled].join(',');
tmState.a = 'X|1|1'; tmState.b = 'X|1|2';
tmLoadPair();
tmSelectRec('Y');
await tick();
answer('/lap?', {ok: false, error: 'stale lap'});
await tick();
console.log([placeholder, $('tm-err').hidden, $('tm-err').textContent].join('|'));""")
    if out is not None:
        assert out.strip() == "1,true,true|true|", \
            f"empty pickers carry a disabled placeholder and a stale lap error stays off the banner: {out!r}"


def t_telemetry_failed_list_retries_on_the_next_visit():
    out = _tm_node("""
tmLoad(); tmLoad();
await tick();
answer('/recordings', {ok: false, error: 'boom'});
await tick();
console.log([tmState.loaded, calls.length, $('tm-recs').textContent === ''].join('|'));""")
    if out is not None:
        assert out.strip() == "false|2|true", f"a failed list must not count as loaded: {out!r}"


_TM_TRACKS = """
const tracks = {ok: true, tracks: [
  {id: '9', track: 'Suzuka', layout: 'East', reverse: false},
  {id: '3', track: 'Brands Hatch', layout: 'Indy', reverse: false},
  {id: '5', track: 'Suzuka', layout: 'Circuit', reverse: false},
  {id: '7', track: 'Brands Hatch', layout: 'Grand Prix', reverse: true}]};
const opts = () => $('tm-track-pick').options.map(o => o.value).join(',');
"""


def t_telemetry_set_track_lists_candidates_first_and_loads_once():
    out = _tm_node(_TM_TRACKS + """
tmState.recs = [{rec: 'X'}, {rec: 'Y'}, {rec: 'Z'}];
tmSelectRec('X'); tmSelectRec('Y');
await tick();
answer('rec=X', {ok: true, recording: {rec: 'X', track: null}, laps: []});
answer('rec=Y', {ok: true, recording: {rec: 'Y', track: {candidates: [{id: '5'}, {id: '9'}]}}, laps: []});
await tick();
const one = calls.filter(u => u.includes('/tracks')).length;
answer('/tracks', tracks);
await tick();
const ambiguous = [$('tm-settrack').hidden, opts(), $('tm-track-pick').options[2].textContent].join(' ');
tmSelectRec('Z');
await tick();
answer('rec=Z', {ok: true, recording: {rec: 'Z', track: {id: '9', track: 'Suzuka', layout: 'East'}}, laps: []});
await tick();
const known = $('tm-settrack').hidden;
tmSelectRec('X');
await tick();
answer('rec=X', recLaps('X', []));
await tick();
console.log([one, ambiguous, known, $('tm-settrack').hidden, opts(),
             calls.filter(u => u.includes('/tracks')).length].join('|'));""")
    if out is not None:
        assert out.strip() == ("1|false 5,9,7,3 Brands Hatch - Grand Prix (reverse)|true|false"
                               "|7,3,5,9|1"), \
            f"candidates first, the rest by track and layout, one track list per view load: {out!r}"


def t_telemetry_set_track_failed_list_retries():
    out = _tm_node(_TM_TRACKS + """
tmShowSetTrack(null); tmShowSetTrack(null);
await tick();
const once = calls.length;
answer('/tracks', {ok: false, error: 'no list'});
await tick();
const failed = [String(tmState.tracks), $('tm-err').textContent].join(',');
tmShowSetTrack(null);
await tick();
answer('/tracks', tracks);
await tick();
console.log([once, failed, opts()].join('|'));""")
    if out is not None:
        assert out.strip() == "1|null,no list|7,3,5,9", \
            f"a failed track list shows its error and is fetched again: {out!r}"


def t_telemetry_learn_reloads_the_recording_and_drops_a_late_answer():
    out = _tm_node(_TM_TRACKS + """
globalThis.confirmModal = async () => true;
tmState.recs = [{rec: 'X', indexed: true}, {rec: 'Y', indexed: true}];
tmState.rec = 'X';
tmShowSetTrack(null);
await tick();
answer('/tracks', tracks);
await tick();
$('tm-track-pick').value = '5'; $('tm-track-pick').selectedIndex = 2;
tmLapCache.set('X|1|1', {});
let body = null;
const realFetch = globalThis.fetch;
globalThis.confirmModal = async () => { $('tm-track-pick').value = '9'; return true; };
globalThis.fetch = (url, opts) => { if (opts && opts.body) body = JSON.parse(opts.body).track_id;
                                    return realFetch(url); };
tmLearn();
await tick();
answer('/learn', {ok: false, error: 'recording in progress: stop the recording to analyse it'});
await tick();
const refused = $('tm-err').textContent + (body === '5' ? '' : ' wrong id ' + body);
globalThis.confirmModal = async () => true;
tmLearn();
await tick();
answer('/learn', {ok: true, track: {id: '5'}});
await tick();
const reload = [tmLapCache.size, calls.filter(u => u.includes('rec=X')).length, tmState.rec,
                tmState.recs[1].indexed,
                $('tm-err').hidden, $('tm-learn').disabled].join(',');
answer('rec=X', recLaps('X', []));
await tick();
tmLearn();
await tick();
tmSelectRec('Y');
await tick();
answer('/learn', {ok: false, error: 'late'});
await tick();
console.log([refused, reload, tmState.rec, calls.filter(u => u.includes('rec=X')).length,
             $('tm-err').textContent].join('|'));""")
    if out is not None:
        assert out.strip() == ("recording in progress: stop the recording to analyse it"
                               "|0,1,X,false,true,false|Y|1|"), \
            f"the server error shows, success reloads the recording, a late answer is dropped: {out!r}"


def t_telemetry_lap_answer_from_before_set_track_is_not_cached():
    out = _tm_node(_TM_TRACKS + """
globalThis.confirmModal = async () => true;
tmState.recs = [{rec: 'R', indexed: true}];
tmState.rec = 'R';
tmShowSetTrack(null);
await tick();
answer('/tracks', tracks);
await tick();
$('tm-track-pick').value = '5'; $('tm-track-pick').selectedIndex = 2;
tmFetchLap('R|1|1');
await tick();
tmLearn();
await tick();
answer('/learn', {ok: true, track: {id: '5'}});
await tick();
tmFetchLap('R|1|1');
await tick();
const lapCalls = calls.filter(u => u.includes('/lap?')).length;
const old = pending.find(p => p.url.includes('/lap?'));
pending.splice(pending.indexOf(old), 1);
old.res({json: async () => ({ok: true, lap: {mark: 'pre'}, sector_m: 200})});
await tick();
const afterOld = [tmLapCache.has('R|1|1'),
                  [...tmLapsReq.keys()].filter(u => u.includes('/lap?')).length].join(',');
answer('/lap?', {ok: true, lap: {mark: 'post'}, sector_m: 200});
await tick();
console.log([lapCalls, afterOld, (tmLapCache.get('R|1|1') || {}).mark].join('|'));""")
    if out is not None:
        assert out.strip() == "2|false,1|post", \
            f"a lap request from before Set track never fills the cache or the shared request: {out!r}"


def t_telemetry_reset_and_reload_drop_a_pending_lap_answer():
    out = _tm_node("""
const res = [];
for (const [fn, key] of [[tmReset, 'R|1|1'], [tmLoad, 'R|1|2']]) {
  tmFetchLap(key);
  await tick();
  fn();
  await tick();
  answer('/lap?', {ok: true, lap: {mark: 'old'}, sector_m: 200});
  answer('/recordings', {ok: true, recordings: []});
  await tick();
  res.push(tmLapCache.has(key));
}
console.log(res.join(','));""")
    if out is not None:
        assert out.strip() == "false,false", \
            f"a lap answer from before a reset or reload is not cached: {out!r}"


def t_telemetry_open_recording_row_is_not_clickable():
    out = _tm_node("""
tmState.recs = [{rec: 'A', recording: true, duration_s: 60, size: 1e6, laps: null},
                {rec: 'B', duration_s: 60, size: 1e6, laps: 3, indexed: true, track: null}];
tmRenderRecs();
const [a, b] = $('tm-recs').kids;
console.log([a.disabled, typeof a.onclick, b.disabled, typeof b.onclick].join(' '));""")
    if out is not None:
        assert out.strip() == "true undefined false function", \
            f"the file the relay writes is listed but cannot be opened: {out!r}"
    assert ".tmitem:disabled" in _cc_page(), "a disabled row needs its own look"


def t_telemetry_learn_says_whether_a_line_was_learned():
    out = _tm_node(_TM_TRACKS + """
globalThis.confirmModal = async () => true;
tmState.recs = [{rec: 'X'}];
tmState.rec = 'X';
tmShowSetTrack(null);
await tick();
answer('/tracks', tracks);
await tick();
const learn = async (learned) => {
  $('tm-track-pick').value = '5'; $('tm-track-pick').selectedIndex = 2;
  tmLearn();
  await tick();
  answer('/learn', {ok: true, learned, track: {id: '5'}});
  await tick();
  answer('rec=X', recLaps('X', []));
  await tick();
  return [$('tm-note').hidden, $('tm-note').textContent].join(' ');
};
const assigned = await learn(false), learnedNote = await learn(true);
tmSelectRec('X');
console.log([assigned, learnedNote, $('tm-note').hidden].join('|'));""")
    if out is not None:
        assert out.strip() == ("false X now uses Suzuka - Circuit. The downloaded racing line "
                               "for it stays as it is."
                               "|false X now uses Suzuka - Circuit. racecast learned the line "
                               "from its longest counted lap, so later recordings on it are "
                               "recognised.|true"), \
            (f"the note says whether a line was learned, survives a reload without laps "
             f"and goes with the next selection: {out!r}")


def t_telemetry_live_recording_offers_no_set_track():
    out = _tm_node(_TM_TRACKS + """
tmState.recs = [{rec: 'X', recording: true}];
tmSelectRec('X');
await tick();
answer('rec=X', {ok: false, error: 'recording in progress: stop the recording to analyse it'});
await tick();
tmState.rec = 'X';
tmShowSetTrack(null);
await tick();
console.log([$('tm-settrack').hidden, calls.filter(u => u.includes('/tracks')).length].join('|'));""")
    if out is not None:
        assert out.strip() == "true|0", f"the file the relay writes cannot be learned from: {out!r}"


def t_telemetry_view_follows_the_active_profile():
    page = _cc_page()
    gate = page[page.index("function applyKindGating(data)"):page.index("async function useProfile(")]
    assert "tmState.profile" in gate and "tmReset()" in gate, \
        "every profile switch path (use, import) resets the Telemetry view"


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


def t_telemetry_routes_pass_their_arguments():
    calls = []
    ctx = _ctx()
    ctx["telemetry_laps"] = lambda *a, **kw: calls.append(("laps",) + a + (kw,)) or {
        "ok": True, "laps": []}
    ctx["telemetry_lap"] = lambda *a, **kw: calls.append(("lap",) + a + (kw,)) or {
        "ok": False, "error": "x"}
    ctx["telemetry_learn"] = lambda *a, **kw: calls.append(("learn",) + a + (kw,)) or {
        "ok": False, "error": "unknown track layout"}
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/telemetry/recordings")
        assert code == 200 and json.loads(body)["recordings"][0]["rec"] == "20261007-201503"
        assert _get(port, "/api/telemetry/laps?rec=20261007-201503")[0] == 200
        assert _get(port, "/api/telemetry/laps?track=&car=3424&rec=r&session=2")[0] == 200
        code, body = _get(port, "/api/telemetry/lap?rec=r&session=1&lap=3")
        assert code == 200 and json.loads(body)["ok"] is False, "a GET reports a miss in the body"
        code, body = _get(port, "/api/telemetry/tracks")
        assert code == 200 and json.loads(body)["tracks"][0]["id"] == "suzuka01"
        code, _ = _post_json(port, "/api/telemetry/learn", {"rec": "r", "track_id": "x"})
        assert code == 400, "a refused learn is a client error"
        assert calls == [("laps", "20261007-201503", None, None, None, {"build": False}),
                         ("laps", "r", "2", "", "3424", {"build": False}),
                         ("lap", "r", "1", "3", {"build": False}),
                         ("learn", "r", "x", {"build": False})], \
            f"no request builds a lap index: {calls}"
    finally:
        httpd.shutdown()


def t_telemetry_routes_stay_json_on_errors():
    ctx = _ctx()

    def boom(*_a, **_kw):
        raise RuntimeError("disk at /srv/league/rec.gt7rec")
    ctx["telemetry_recordings"] = boom
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/telemetry/recordings")
        err = json.loads(body)["error"]
        assert code == 500 and "RuntimeError" in err and "/srv/league" not in err, err
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/telemetry/learn",
                                     method="POST", data=b"{bad",
                                     headers={"Content-Type": "application/json"})
        try:
            with _urlopen(req, timeout=5) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        assert code == 400, "a malformed body is refused"
        code, body = _post_json(port, "/api/telemetry/learn", {"rec": "r", "track_id": "suzuka01"})
        assert code == 200 and json.loads(body)["track"]["id"] == "suzuka01"
    finally:
        httpd.shutdown()


def t_telemetry_routes_500_paths_report_only_the_exception_type():
    ctx = _ctx()

    def boom(*_a, **_kw):
        raise RuntimeError("disk at /srv/league/rec.gt7rec")
    ctx["telemetry_laps"] = boom
    ctx["telemetry_lap"] = boom
    ctx["telemetry_tracks"] = boom
    ctx["telemetry_learn"] = boom
    httpd, port = _serve(ctx)
    try:
        code, body = _get(port, "/api/telemetry/laps?rec=r")
        err = json.loads(body)["error"]
        assert code == 500 and "RuntimeError" in err and "/srv/league" not in err, err
        code, body = _get(port, "/api/telemetry/lap?rec=r&session=1&lap=1")
        err = json.loads(body)["error"]
        assert code == 500 and "RuntimeError" in err and "/srv/league" not in err, err
        code, body = _get(port, "/api/telemetry/tracks")
        err = json.loads(body)["error"]
        assert code == 500 and "RuntimeError" in err and "/srv/league" not in err, err
        code, body = _post_json(port, "/api/telemetry/learn", {"rec": "r", "track_id": "x"})
        err = json.loads(body)["error"]
        assert code == 500 and "RuntimeError" in err and "/srv/league" not in err, err
    finally:
        httpd.shutdown()


def t_telemetry_learn_rejects_a_non_object_json_body():
    ctx = _ctx()
    httpd, port = _serve(ctx)
    try:
        for bad in ([1, 2, 3], "x", 5, None):
            code, body = _post_json(port, "/api/telemetry/learn", bad)
            assert code == 400, (bad, code, body)
    finally:
        httpd.shutdown()


def t_body_json_rejects_negative_and_oversized_content_length():
    import http.client
    ctx = _ctx()
    httpd, port = _serve(ctx)
    try:
        payload = json.dumps({"rec": "r", "track_id": "x"}).encode("utf-8")
        for path, length, body, want in (
                ("/api/telemetry/learn", "-1", payload, "invalid Content-Length"),
                ("/api/telemetry/learn", str(us.MAX_JSON_BODY_BYTES + 1), payload,
                 "request body too large"),
                ("/api/telemetry/learn", "5", b"{oops", "malformed JSON body"),
                ("/api/profile/use", str(us.MAX_JSON_BODY_BYTES + 1), payload,
                 "request body too large")):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.putrequest("POST", path)
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", length)
            conn.endheaders()
            conn.send(body)
            resp = conn.getresponse()
            err = json.loads(resp.read())["error"]
            assert (resp.status, err) == (400, want), (path, length, resp.status, err)
            conn.close()
    finally:
        httpd.shutdown()
    with open(us.__file__, encoding="utf-8") as fh:
        src = fh.read()
    assert '"error": "malformed JSON body"' not in src, \
        "every route reports the body error _body_json found"


def t_restore_fonts_button_confirms_before_forcing():
    with open(os.path.join(ROOT, "src", "ui", "control-center.html"), encoding="utf-8") as fh:
        html = fh.read()
    assert 'id="font-restore" onclick="restoreBundledFonts()"' in html, "the Settings button must call restoreBundledFonts"
    fn = html[html.index("async function restoreBundledFonts()"):]
    fn = fn[:fn.index("\n}\n")]
    assert fn.index("confirmModal(") < fn.index("fetch('/api/fonts/restore'"), "the overwrite must be confirmed first"
    assert "JSON.stringify({force: true})" in fn, "the button must request the forced restore"


def t_every_button_icon_is_styled():
    # An unstyled inline SVG renders as a black filled shape that fills the button.
    with open(os.path.join(ROOT, "src", "ui", "control-center.html"), encoding="utf-8") as fh:
        html = fh.read()
    style = html[html.index("<style"):html.index("</style>")]
    assert re.search(r"(?:^|[{},])\s*button svg\s*[,{]", style, re.M), \
        "buttons outside .row need a base icon rule"
    assert "cap.title = cap.textContent" in html, "an ellipsized asset name needs its full text as a tooltip"
    assert re.search(r"\.viewhead button\s*\{[^}]*white-space:\s*nowrap", style), \
        "a crowded header must wrap its buttons to a new line, not squeeze their labels"
    assert re.search(r"\.viewhead\s*\{[^}]*flex-wrap:\s*wrap", style), "the header row must be allowed to wrap"
    fill = html[html.index("function ovFillSample()"):html.index("function ovFitName(")]
    assert "document.fonts.ready" in fill, "team names must be fitted again once the webfont has loaded"
    assert "ResizeObserver" in fill, "a name filled while the canvas is hidden must be fitted once it is laid out"
    assert ".cplinks .cprow { display:contents; }" in style, "crew link rows must share their columns"

if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")

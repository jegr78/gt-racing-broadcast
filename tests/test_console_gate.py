#!/usr/bin/env python3
"""Live-server integration checks for the /console auth gate (#216 phase 3a).
Run: python3 tests/test_console_gate.py"""
import importlib.util, os, tempfile, threading, json
import urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOGDIR = tempfile.mkdtemp(prefix="racecast-test-logs-")
spec = importlib.util.spec_from_file_location(
    "irofeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

# Manual feed arm defaults ON (#492 follow-up): a bare Relay would start feeds
# disarmed (paused). These checks exercise the legacy auto-pull path; pin the
# opt-out so they stay focused (same guard as tests/test_pov.py).
os.environ.setdefault("RACECAST_MANUAL_FEED_ARM", "0")

SECRET = "s3cret-league"
_URLS8 = [f"https://www.youtube.com/watch?v=stint{i}" for i in range(1, 9)]


class _FakeSource:
    """A schedule source exposing .get() (URL list) and get_rows()/.rows (the
    (url,name,stint,line) tuples the gate reads for schedule_keys and the cockpit
    chat handler reads for the speaker name)."""
    def __init__(self, urls, rows):
        self.items = list(urls)
        self.rows = list(rows)
    def get(self): return self.items
    def get_rows(self): return self.rows
    def refresh(self, timeout=None): pass
    def health(self): return {"ok": True}


class _Crew:
    def __init__(self, rows, rc=frozenset()):
        self._rows = list(rows)
        self._rc = frozenset(rc)
    def get(self): return list(self._rows)
    def commentator_keys(self): return frozenset()
    def race_control_keys(self): return self._rc


def _serve(companion_url=None, logo_path=None, sheet_id=None, graphics_dir=None):
    rows = [("https://youtu.be/a", "Alice", "1", 2)]           # alice -> commentator
    src = _FakeSource(_URLS8, rows)
    relay = m.Relay(src, [53001, 53002], LOGDIR, sheet_id=sheet_id)
    # bob=director, carol=producer; dave=race_control desk (no other role)
    crew = _Crew([("Bob", True, False), ("Carol", False, True)], rc={"dave"})
    SRC = os.path.join(ROOT, "src")
    handler = m.make_handler(
        relay, console_secret=SECRET, console_versions_path=None,
        chat_store=m.ChatStore(os.path.join(LOGDIR, "chat.json")),
        crew_source=crew,
        panel_path=os.path.join(SRC, "director", "director-panel.html"),
        cockpit_page_path=os.path.join(SRC, "cockpit", "cockpit.html"),
        console_page_path=os.path.join(SRC, "console", "console.html"),
        race_control_page_path=os.path.join(SRC, "racecontrol", "race-control.html"),
        buttons_page_path=os.path.join(SRC, "console", "buttons.html"),
        health_store=m.HealthStore(os.path.join(LOGDIR, "health.db")),
        health_monitor_page_path=os.path.join(SRC, "console", "health-monitor.html"),
        uplot_dir=os.path.join(SRC, "assets", "vendor", "uplot"),
        companion_url=companion_url,
        logo_path=logo_path,
        graphics_dir=graphics_dir)
    srv = m.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _tok(key):
    return m.console_auth.mint_token(SECRET, key)


def _get(port, path, token=None, secret=None, secret_header="X-Console-Secret",
         headers=None):
    url = f"http://127.0.0.1:{port}{path}"
    if token:
        url += ("&" if "?" in path else "?") + "t=" + token
    req = urllib.request.Request(url)
    if secret:
        req.add_header(secret_header, secret)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _post(port, path, token=None, secret=None, body=None, secret_header="X-Console-Secret"):
    url = f"http://127.0.0.1:{port}{path}"
    if token:
        url += ("&" if "?" in path else "?") + "t=" + token
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    if secret:
        req.add_header(secret_header, secret)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def t_no_token_is_401():
    srv = _serve(); port = srv.server_address[1]
    try:
        assert _get(port, "/console/status")[0] == 401
    finally:
        srv.shutdown()


def t_any_authenticated_read_allowed():
    srv = _serve(); port = srv.server_address[1]
    try:
        code, body = _get(port, "/console/status", _tok("alice"))   # commentator -> any read ok
        assert code == 200, (code, body)
    finally:
        srv.shutdown()


def t_commentator_forbidden_from_director_op():
    srv = _serve(); port = srv.server_address[1]
    try:
        assert _get(port, "/console/next", _tok("alice"))[0] == 403
    finally:
        srv.shutdown()


def t_director_allowed_director_op():
    srv = _serve(); port = srv.server_address[1]
    try:
        assert _get(port, "/console/reload", _tok("bob"))[0] == 200
    finally:
        srv.shutdown()


def t_producer_stepup_required_without_secret():
    srv = _serve(); port = srv.server_address[1]
    try:
        assert _get(port, "/console/set/stint/2", _tok("carol"))[0] == 403
    finally:
        srv.shutdown()


def t_producer_stepup_allowed_with_secret():
    srv = _serve(); port = srv.server_address[1]
    try:
        assert _get(port, "/console/set/stint/2", _tok("carol"), secret=SECRET)[0] == 200
    finally:
        srv.shutdown()


def t_role_gate_precedes_stepup():
    # A director (not producer) with the correct secret is still FORBIDDEN.
    srv = _serve(); port = srv.server_address[1]
    try:
        assert _get(port, "/console/set/stint/2", _tok("bob"), secret=SECRET)[0] == 403
    finally:
        srv.shutdown()


def t_unknown_console_route_is_404():
    srv = _serve(); port = srv.server_address[1]
    try:
        assert _get(port, "/console/bogus", _tok("bob"))[0] == 404
    finally:
        srv.shutdown()


def t_chat_send_forces_token_identity():
    srv = _serve(); port = srv.server_address[1]
    try:
        url = f"http://127.0.0.1:{port}/console/chat/send?t=" + _tok("alice")
        body = json.dumps({"text": "hello from the console"}).encode()
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
        code, data = _get(port, "/console/chat/data", _tok("alice"))
        assert code == 200, (code, data)
        msgs = json.loads(data).get("messages", [])
        # ChatStore messages are {"ts","user","text"}; the speaker is "user" and is
        # server-forced to the token's streamer (display name), never client-declared.
        assert any(msg.get("text") == "hello from the console"
                   and m.asset_key(msg.get("user", "")) == "alice" for msg in msgs), data
    finally:
        srv.shutdown()


def t_chat_send_ignores_client_supplied_user():
    # A client-supplied "user" field in the POST body must be overridden by the
    # token identity — the stored speaker must be the token's key, never "bob".
    srv = _serve(); port = srv.server_address[1]
    try:
        url = f"http://127.0.0.1:{port}/console/chat/send?t=" + _tok("alice")
        body = json.dumps({"user": "bob", "text": "spoof attempt"}).encode()
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
        code, data = _get(port, "/console/chat/data", _tok("alice"))
        msgs = json.loads(data).get("messages", [])
        spoof = [msg for msg in msgs if msg.get("text") == "spoof attempt"]
        assert spoof, data
        # The stored speaker must be the TOKEN identity (alice), never the client's "bob".
        for msg in spoof:
            assert m.asset_key(msg.get("user", "")) == "alice", msg
    finally:
        srv.shutdown()


def t_wrong_method_console_route_is_404():
    # /status is GET-only; POSTing it through /console authorizes (ANY) then 404s
    # at the root dispatch. Pins the GET/POST asymmetry.
    srv = _serve(); port = srv.server_address[1]
    try:
        url = f"http://127.0.0.1:{port}/console/status?t=" + _tok("alice")
        req = urllib.request.Request(url, data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
        assert code == 404, code
    finally:
        srv.shutdown()


def t_root_cockpit_page_has_empty_base():
    srv = _serve(); port = srv.server_address[1]
    try:
        code, body = _get(port, "/cockpit", _tok("alice"))
        assert code == 200, (code, body)
        assert 'window.RC_API_BASE = ""' in body, body[:400]
    finally:
        srv.shutdown()


def t_console_whoami_returns_roles():
    srv = _serve(); port = srv.server_address[1]
    try:
        code, data = _get(port, "/console/whoami", _tok("bob"))   # bob = director
        assert code == 200, (code, data)
        body = json.loads(data)
        assert body["subject"] == "bob"
        assert "director" in body["roles"]
    finally:
        srv.shutdown()


def t_console_launcher_served_any_auth():
    srv = _serve(); port = srv.server_address[1]
    try:
        code, body = _get(port, "/console", _tok("alice"))   # commentator
        assert code == 200, (code, body)
        assert 'window.RC_API_BASE = "/console"' in body, body[:400]
    finally:
        srv.shutdown()


def t_console_cockpit_page_any_auth_with_console_base_and_cookie():
    srv = _serve(); port = srv.server_address[1]
    try:
        url = f"http://127.0.0.1:{port}/console/cockpit?t=" + _tok("alice")
        with urllib.request.urlopen(url, timeout=5) as r:
            body = r.read().decode()
            setc = r.headers.get("Set-Cookie", "")
        assert 'window.RC_API_BASE = "/console"' in body, body[:400]
        assert "Path=/console" in setc, setc
    finally:
        srv.shutdown()


def t_console_whoami_includes_race_control_role():
    srv = _serve(); port = srv.server_address[1]
    try:
        code, data = _get(port, "/console/whoami", _tok("dave"))   # dave = race_control
        assert code == 200, (code, data)
        body = json.loads(data)
        assert body["subject"] == "dave"
        assert "race_control" in body["roles"], body
    finally:
        srv.shutdown()


def t_console_race_control_page_requires_role():
    srv = _serve(); port = srv.server_address[1]
    try:
        # commentator + director are NOT race_control -> 403
        assert _get(port, "/console/race-control", _tok("alice"))[0] == 403
        assert _get(port, "/console/race-control", _tok("bob"))[0] == 403
        code, body = _get(port, "/console/race-control", _tok("dave"))   # race_control
        assert code == 200, (code, body)
        assert 'window.RC_API_BASE = "/console"' in body, body[:400]
    finally:
        srv.shutdown()


def t_console_race_control_data_redacted_and_gated():
    srv = _serve(); port = srv.server_address[1]
    try:
        assert _get(port, "/console/race-control/data", _tok("alice"))[0] == 403
        code, data = _get(port, "/console/race-control/data", _tok("dave"))
        assert code == 200, (code, data)
        blob = json.loads(data)
        assert "schedule" in blob and "on_air" in blob, blob
        assert blob["mode"] == "race", blob
        # Redaction: no stream URLs leave the tailnet via the public Funnel desk.
        serialised = json.dumps(blob)
        assert "url" not in serialised and "http" not in serialised.lower(), serialised
        # The schedule row carries stint + streamer + the live marker only.
        assert blob["schedule"] and set(blob["schedule"][0]) == {"stint", "streamer", "live"}, blob
    finally:
        srv.shutdown()


def t_console_race_control_reuses_any_cockpit_endpoints():
    # The desk reuses the ANY cockpit monitors; a race_control token reaches them.
    srv = _serve(); port = srv.server_address[1]
    try:
        assert _get(port, "/console/cockpit/timer", _tok("dave"))[0] in (200, 404)
        assert _get(port, "/console/cockpit/chat/data", _tok("dave"))[0] == 200
    finally:
        srv.shutdown()


def t_console_panel_requires_director():
    srv = _serve(); port = srv.server_address[1]
    try:
        assert _get(port, "/console/panel", _tok("alice"))[0] == 403   # commentator -> no
        assert _get(port, "/console/panel", _tok("bob"))[0] == 200      # director -> yes
    finally:
        srv.shutdown()


def t_console_launcher_links_are_mount_absolute():
    # Card hrefs must be built through RC_API so they resolve under /console
    # (a bare relative 'cockpit' against /console would navigate to /cockpit).
    srv = _serve(); port = srv.server_address[1]
    try:
        code, body = _get(port, "/console", _tok("bob"))   # bob = director: both cards render
        assert code == 200, (code, body)
        assert "RC_API('/cockpit')" in body, body
        assert "RC_API('/panel')" in body, body
        # The old bare-relative forms must be gone.
        assert "card('cockpit'" not in body and "card('panel'" not in body, body
    finally:
        srv.shutdown()


def t_takeover_status_needs_step_up_secret():
    # Producer-to-producer takeover is authorized by the shared step-up secret
    # ALONE — producer B holds the league CONSOLE_SECRET, not a per-person
    # commentator token. No secret -> 403 step-up (NEVER 401, which falsely
    # implied a token problem); the SECRET with NO token -> 200 + a REDACTED body.
    srv = _serve(); port = srv.server_address[1]
    try:
        code, _ = _get(port, "/console/takeover/status")                          # no auth at all
        assert code == 403, code
        code, _ = _get(port, "/console/takeover/status", _tok("carol"))           # token, no secret
        assert code == 403, code
        code, _ = _get(port, "/console/takeover/status", secret="wrong-secret")   # bad secret
        assert code == 403, code
        code, body = _get(port, "/console/takeover/status", secret=SECRET)        # SECRET ONLY, no token
        assert code == 200, (code, body)
        blob = json.loads(body)
        assert "live" in blob and "league" in blob, blob
        # Redaction: the public takeover status must NOT carry the feed map or stream URLs.
        assert "feeds" not in blob and "pov" not in blob, blob
        serialised = json.dumps(blob)
        assert "youtube" not in serialised.lower() and "http" not in serialised.lower(), serialised
    finally:
        srv.shutdown()



def t_takeover_authorized_by_secret_regardless_of_token():
    # The step-up secret signs every token, so possessing it is strictly stronger
    # than holding any single token: takeover no longer ALSO requires a producer
    # token. A director token + secret -> 200, and the secret with NO token -> 200.
    srv = _serve(); port = srv.server_address[1]
    try:
        assert _get(port, "/console/takeover/status", _tok("bob"), SECRET)[0] == 200
        assert _get(port, "/console/takeover/status", secret=SECRET)[0] == 200
    finally:
        srv.shutdown()


def t_console_obs_scene_requires_director():
    srv = _serve(); port = srv.server_address[1]
    try:
        assert _post(port, "/console/obs/scene", body={"scene": "Stint"})[0] == 401   # no token
        assert _post(port, "/console/obs/scene", _tok("alice"),                        # commentator, not director
                     body={"scene": "Stint"})[0] == 403
        # A producer implies director -> OBS control is allowed for carol.
        assert _post(port, "/console/obs/scene", _tok("carol"),
                     body={"scene": "Stint"})[0] in (200, 503)
        code, _ = _post(port, "/console/obs/scene", _tok("bob"), body={"scene": "Stint"})
        assert code in (200, 503), code   # director allowed (200 ok, or 503 when no OBS in the test)
    finally:
        srv.shutdown()


def t_obs_scene_forwards_transition_and_clamps_duration():
    # The /obs/scene endpoint must forward transition + a clamped duration_ms to
    # _obs_ws.set_current_program_scene and return {"ok": True} (or the note).
    srv = _serve(); port = srv.server_address[1]
    calls = {}

    class _FakeObs:
        def set_current_program_scene(self, scene, transition=None, duration_ms=None):
            calls["args"] = (scene, transition, duration_ms)
            return True, ""

    orig, m._obs_ws = m._obs_ws, _FakeObs()
    try:
        code, body = _post(port, "/console/obs/scene", _tok("bob"),
                           body={"scene": "Stint", "transition": "fade",
                                 "duration": 999999})
        assert code == 200, (code, body)
        assert "args" in calls, "set_current_program_scene was not called"
        assert calls["args"][0] == "Stint", calls["args"]
        assert calls["args"][1] == "fade", calls["args"]
        assert calls["args"][2] == 10000, calls["args"]   # clamped to 0..10000 ceiling
    finally:
        m._obs_ws = orig
        srv.shutdown()


def t_takeover_chat_and_versions_gated_and_routed():
    srv = _serve(); port = srv.server_address[1]
    try:
        # Secret ALONE (no token) authorizes the full-data pull and routes to the
        # underlying handler (chat history / versions map).
        code, body = _get(port, "/console/takeover/chat", secret=SECRET)
        assert code == 200, (code, body)
        assert "messages" in json.loads(body), body
        code, body = _get(port, "/console/takeover/versions", secret=SECRET)
        assert code == 200, (code, body)
        assert "versions" in json.loads(body), body
        # Without the step-up secret both are 403 (even with a producer token).
        assert _get(port, "/console/takeover/chat", _tok("carol"))[0] == 403
        assert _get(port, "/console/takeover/versions", _tok("carol"))[0] == 403
    finally:
        srv.shutdown()


from http.server import BaseHTTPRequestHandler


class _StubCompanion(BaseHTTPRequestHandler):
    last = {}
    def do_GET(self):
        _StubCompanion.last = {"path": self.path,
                               "prefix": self.headers.get("Companion-custom-prefix"),
                               "cookie": self.headers.get("Cookie")}
        body = b"<html>companion web buttons</html>"
        self.send_response(200); self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass


def _stub_companion():
    srv = m.ThreadingHTTPServer(("127.0.0.1", 0), _StubCompanion)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def t_buttons_http_proxies_for_director():
    up = _stub_companion(); upurl = f"http://127.0.0.1:{up.server_address[1]}"
    srv = _serve(companion_url=upurl); port = srv.server_address[1]
    try:
        code, body = _get(port, "/console/buttons/tablet", _tok("bob"))   # director
        assert code == 200, (code, body)
        assert "companion web buttons" in body, body
        assert _StubCompanion.last["prefix"] == "console/buttons", _StubCompanion.last  # no slash
        assert _StubCompanion.last["path"] == "/tablet", _StubCompanion.last
    finally:
        srv.shutdown(); up.shutdown()


def t_buttons_forbidden_for_commentator():
    up = _stub_companion(); upurl = f"http://127.0.0.1:{up.server_address[1]}"
    srv = _serve(companion_url=upurl); port = srv.server_address[1]
    try:
        assert _get(port, "/console/buttons/tablet", _tok("alice"))[0] == 403
    finally:
        srv.shutdown(); up.shutdown()


def t_buttons_502_when_companion_down():
    srv = _serve(companion_url="http://127.0.0.1:1"); port = srv.server_address[1]
    try:
        assert _get(port, "/console/buttons/tablet", _tok("bob"))[0] == 502
    finally:
        srv.shutdown()


def t_buttons_health_shape_and_director_gated():
    srv = _serve(companion_url="http://127.0.0.1:1"); port = srv.server_address[1]
    try:
        assert _get(port, "/console/buttons/health", _tok("alice"))[0] == 403   # commentator
        code, body = _get(port, "/console/buttons/health", _tok("bob"))         # director
        assert code == 200, (code, body)
        blob = json.loads(body)
        assert set(blob) == {"reachable", "version", "ok"}, blob
        assert blob["reachable"] is False and blob["ok"] is False               # nothing on port 1
    finally:
        srv.shutdown()


import socket as _socket


class _WSStub:
    last_request = b""


def _ws_echo_stub():
    """Raw-socket upstream: records the handshake request line, completes a 101, echoes bytes."""
    srv = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    srv.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0)); srv.listen(1)
    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            _WSStub.last_request = data
            conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\n"
                         b"Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
            try:
                while True:
                    b = conn.recv(4096)
                    if not b:
                        break
                    conn.sendall(b)
            except OSError:
                pass  # client disconnected — normal echo-stub teardown
            conn.close()
    threading.Thread(target=serve, daemon=True).start()
    return srv


def t_buttons_ws_passthrough_and_strips_token():
    up = _ws_echo_stub(); upurl = f"http://127.0.0.1:{up.getsockname()[1]}"
    srv = _serve(companion_url=upurl); port = srv.server_address[1]
    try:
        c = _socket.create_connection(("127.0.0.1", port), timeout=5)
        req = ("GET /console/buttons/trpc?t=%s HTTP/1.1\r\n"
               "Host: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n") % _tok("bob")
        c.sendall(req.encode())
        resp = c.recv(4096)
        assert resp.split(b"\r\n")[0].endswith(b"101 Switching Protocols"), resp
        c.sendall(b"hello-trpc")
        assert c.recv(4096) == b"hello-trpc"
        c.close()
        # The upstream handshake must hit /trpc with the relay token stripped.
        first_line = _WSStub.last_request.split(b"\r\n")[0]
        assert first_line.startswith(b"GET /trpc"), first_line
        assert b"t=" not in first_line, first_line
    finally:
        srv.shutdown(); up.close()


def t_buttons_http_does_not_forward_relay_cookie():
    # The rc_console auth cookie must be scrubbed before forwarding upstream;
    # other cookies in the same header must be preserved.
    up = _stub_companion(); upurl = f"http://127.0.0.1:{up.server_address[1]}"
    srv = _serve(companion_url=upurl); port = srv.server_address[1]
    try:
        url = f"http://127.0.0.1:{port}/console/buttons/tablet?t=" + _tok("bob")
        req = urllib.request.Request(url)
        req.add_header("Cookie", "rc_console=secret; keep=1")
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
        # The stub saw the Cookie header with keep=1 but NOT rc_console.
        received_cookie = _StubCompanion.last.get("cookie") or ""
        assert "rc_console" not in received_cookie, received_cookie
        assert "keep=1" in received_cookie, received_cookie
    finally:
        srv.shutdown(); up.shutdown()


def t_buttons_ws_does_not_forward_relay_cookie_and_carries_prefix():
    # The WS upgrade path must scrub rc_console and inject the sub-path prefix header.
    up = _ws_echo_stub(); upurl = f"http://127.0.0.1:{up.getsockname()[1]}"
    srv = _serve(companion_url=upurl); port = srv.server_address[1]
    try:
        c = _socket.create_connection(("127.0.0.1", port), timeout=5)
        req = ("GET /console/buttons/trpc?t=%s HTTP/1.1\r\n"
               "Host: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
               "Sec-WebSocket-Version: 13\r\n"
               "Cookie: rc_console=%s\r\n\r\n") % (_tok("bob"), _tok("bob"))
        c.sendall(req.encode())
        resp = c.recv(4096)
        assert resp.split(b"\r\n")[0].endswith(b"101 Switching Protocols"), resp
        c.close()
        # rc_console must not appear in the upstream handshake.
        assert b"rc_console" not in _WSStub.last_request, _WSStub.last_request[:400]
        # The prefix header must be present.
        assert b"Companion-custom-prefix: console/buttons" in _WSStub.last_request, \
            _WSStub.last_request[:400]
    finally:
        srv.shutdown(); up.close()


def t_console_launcher_has_buttons_wiring_for_director():
    srv = _serve(companion_url="http://127.0.0.1:1"); port = srv.server_address[1]
    try:
        code, body = _get(port, "/console", _tok("bob"))   # director
        assert code == 200, (code, body)
        assert "/buttons/health" in body, body
        # The card lands on the /console/buttons wrapper (same tab; the wrapper embeds
        # Companion in an iframe), not directly on Companion's tablet/admin page.
        assert "RC_API('/buttons')" in body, body
        assert "/buttons/tablet" not in body, body
        assert "_blank" not in body, body
    finally:
        srv.shutdown()


def t_console_buttons_wrapper_page_director_only():
    # GET /console/buttons (exact) -> our wrapper HTML (back bar + iframe), director-gated.
    # /console/buttons/<rest> still proxies to Companion (covered elsewhere).
    srv = _serve(companion_url="http://127.0.0.1:1"); port = srv.server_address[1]
    try:
        code, body = _get(port, "/console/buttons", _tok("bob"))   # director
        assert code == 200, (code, body)
        assert "<iframe" in body, body
        assert "RC_API('/buttons/tablet')" in body, body   # iframe targets the proxied UI
        assert 'id="back"' in body, body                    # carries the back link
        # A commentator may not reach the buttons wrapper.
        code2, _ = _get(port, "/console/buttons", _tok("alice"))
        assert code2 == 403, code2
    finally:
        srv.shutdown()


def t_console_logo_served_any_auth():
    import tempfile
    # Write a tiny valid PNG (8-byte signature + minimal IHDR would be complex; just
    # use the minimal bytes that pass os.path.splitext extension check).
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
        logo_path = fh.name
    try:
        srv = _serve(logo_path=logo_path); port = srv.server_address[1]
        try:
            # Any authenticated subject (alice = commentator) can GET /console/logo.
            url = f"http://127.0.0.1:{port}/console/logo?t=" + _tok("alice")
            with urllib.request.urlopen(url, timeout=5) as r:
                code = r.status
                body = r.read()
            assert code == 200, code
            assert len(body) > 0, "logo body was empty"
            # No token -> 401.
            code2, _ = _get(port, "/console/logo")
            assert code2 == 401, code2
        finally:
            srv.shutdown()
    finally:
        os.unlink(logo_path)


def t_console_logo_404_when_unset():
    srv = _serve(); port = srv.server_address[1]
    try:
        code, body = _get(port, "/console/logo", _tok("alice"))
        assert code == 404, (code, body)
    finally:
        srv.shutdown()


# ---------- helpers for OAuth endpoint tests ----------

class _CrewWithDiscord(_Crew):
    """Extends _Crew with a discord_map() that returns a fixed handle->name mapping."""
    def __init__(self, rows, discord):
        super().__init__(rows)
        self._discord = discord
    def discord_map(self):
        return dict(self._discord)


def _serve_oauth(discord_client_id="cid", discord_client_secret="sec"):
    rows = [("https://youtu.be/a", "Alice", "1", 2)]
    src = _FakeSource(_URLS8, rows)
    relay = m.Relay(src, [53001, 53002], LOGDIR)
    crew = _CrewWithDiscord(
        [("Alice", False, False), ("Bob", True, False)],
        {"alice_discord": "Alice"})
    SRC = os.path.join(ROOT, "src")
    handler = m.make_handler(
        relay, console_secret=SECRET, console_versions_path=None,
        chat_store=m.ChatStore(os.path.join(LOGDIR, "chat2.json")),
        crew_source=crew,
        panel_path=os.path.join(SRC, "director", "director-panel.html"),
        cockpit_page_path=os.path.join(SRC, "cockpit", "cockpit.html"),
        console_page_path=os.path.join(SRC, "console", "console.html"),
        discord_client_id=discord_client_id,
        discord_client_secret=discord_client_secret)
    srv = m.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect)


def _get_with_headers(port, path, extra_headers=None):
    """Like _get but supports arbitrary request headers; no auth token.
    Does NOT follow redirects — the 302 Location is returned as-is."""
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url)
    for k, v in (extra_headers or {}).items():
        req.add_header(k, v)
    try:
        with _NO_REDIRECT_OPENER.open(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


# ---------- OAuth endpoint tests ----------

def t_console_login_redirects_when_oauth_configured():
    srv = _serve_oauth(); port = srv.server_address[1]
    try:
        code, headers, _ = _get_with_headers(port, "/console/login",
                                             {"Host": "box.tail1.ts.net",
                                              "X-Forwarded-Proto": "https"})
        assert code == 302, code
        loc = headers.get("Location") or headers.get("location") or ""
        assert loc.startswith("https://discord.com/oauth2/authorize?"), loc
        assert "client_id=cid" in loc, loc
        assert "redirect_uri=https%3A%2F%2Fbox.tail1.ts.net%2Fconsole%2Foauth%2Fcallback" in loc, loc
    finally:
        srv.shutdown()


def t_console_login_404_when_oauth_unconfigured():
    srv = _serve_oauth(discord_client_id="", discord_client_secret="")
    port = srv.server_address[1]
    try:
        code, _h, _b = _get_with_headers(port, "/console/login",
                                          {"Host": "box.tail1.ts.net"})
        assert code == 404, code
    finally:
        srv.shutdown()


def t_oauth_callback_sets_cookie_on_crew_match():
    # The CSRF state cookie (rc_oauth_state) must carry the same nonce embedded in
    # the signed `state`, or the callback rejects it as a forged/expired login.
    import time as _t
    m._TEST_EXCHANGE = lambda code, redirect_uri: "alice_discord"
    try:
        srv = _serve_oauth(); port = srv.server_address[1]
        try:
            state = m.discord_oauth.sign_state(SECRET, "n1", int(_t.time()))
            code, headers, _ = _get_with_headers(
                port, f"/console/oauth/callback?code=abc&state={state}",
                {"Host": "box.tail1.ts.net", "X-Forwarded-Proto": "https",
                 "Cookie": "rc_oauth_state=n1"})
            assert code == 302, code
            loc = headers.get("Location") or headers.get("location") or ""
            assert loc.endswith("/console"), loc
            setc = headers.get("Set-Cookie") or headers.get("set-cookie") or ""
            assert "rc_console=" in setc, setc
            assert "Path=/console" in setc, setc
            assert "HttpOnly" in setc, setc
            # Behind https the auth cookie must be marked Secure.
            assert "Secure" in setc, setc
        finally:
            srv.shutdown()
    finally:
        del m._TEST_EXCHANGE


def t_oauth_callback_bad_state_400():
    m._TEST_EXCHANGE = lambda code, redirect_uri: "alice_discord"
    try:
        srv = _serve_oauth(); port = srv.server_address[1]
        try:
            code, _h, _b = _get_with_headers(
                port, "/console/oauth/callback?code=abc&state=not.valid.sig",
                {"Host": "box.tail1.ts.net"})
            assert code == 400, code
        finally:
            srv.shutdown()
    finally:
        del m._TEST_EXCHANGE


def t_oauth_callback_csrf_cookie_mismatch_400():
    # A correctly-signed state but a missing/mismatched session cookie is the
    # login-CSRF case: the callback must reject with 400 BEFORE any token exchange.
    import time as _t
    called = []
    m._TEST_EXCHANGE = lambda code, redirect_uri: called.append(1) or "alice_discord"
    try:
        srv = _serve_oauth(); port = srv.server_address[1]
        try:
            state = m.discord_oauth.sign_state(SECRET, "n1", int(_t.time()))
            # No cookie at all.
            code, _h, _b = _get_with_headers(
                port, f"/console/oauth/callback?code=abc&state={state}",
                {"Host": "box.tail1.ts.net"})
            assert code == 400, code
            # Wrong nonce in the cookie.
            code2, _h2, _b2 = _get_with_headers(
                port, f"/console/oauth/callback?code=abc&state={state}",
                {"Host": "box.tail1.ts.net", "Cookie": "rc_oauth_state=WRONG"})
            assert code2 == 400, code2
            assert not called, "exchange must not run on a CSRF mismatch"
        finally:
            srv.shutdown()
    finally:
        del m._TEST_EXCHANGE


def t_oauth_callback_502_when_exchange_fails():
    # The token-exchange returning "" (Discord error) -> 502, no rc_console cookie.
    import time as _t
    m._TEST_EXCHANGE = lambda *_: ""
    try:
        srv = _serve_oauth(); port = srv.server_address[1]
        try:
            state = m.discord_oauth.sign_state(SECRET, "n1", int(_t.time()))
            code, headers, _b = _get_with_headers(
                port, f"/console/oauth/callback?code=abc&state={state}",
                {"Host": "box.tail1.ts.net", "Cookie": "rc_oauth_state=n1"})
            assert code == 502, code
            setc = headers.get("Set-Cookie") or headers.get("set-cookie") or ""
            assert "rc_console=" not in setc, setc
        finally:
            srv.shutdown()
    finally:
        del m._TEST_EXCHANGE


def t_oauth_callback_no_crew_match_denies():
    import time as _t
    m._TEST_EXCHANGE = lambda code, redirect_uri: "ghost_user"
    try:
        srv = _serve_oauth(); port = srv.server_address[1]
        try:
            state = m.discord_oauth.sign_state(SECRET, "n1", int(_t.time()))
            code, headers, body = _get_with_headers(
                port, f"/console/oauth/callback?code=abc&state={state}",
                {"Host": "box.tail1.ts.net", "Cookie": "rc_oauth_state=n1"})
            assert code == 403, code
            assert b"crew" in body.lower() or b"not on the crew" in body.lower(), body
            setc = headers.get("Set-Cookie") or headers.get("set-cookie") or ""
            assert "rc_oauth_state=" in setc and "Max-Age=0" in setc, \
                f"403 should clear state cookie; got Set-Cookie: {setc!r}"
        finally:
            srv.shutdown()
    finally:
        del m._TEST_EXCHANGE


def t_console_root_auth_optional_with_oauth():
    # With OAuth configured and NO token cookie, GET /console serves the launcher
    # page (200) so an unauthenticated visitor sees the Login-with-Discord button.
    srv = _serve_oauth(); port = srv.server_address[1]
    try:
        code, _h, body = _get_with_headers(port, "/console",
                                           {"Host": "box.tail1.ts.net"})
        assert code == 200, code
        assert body, "launcher page should have a body"
    finally:
        srv.shutdown()


def t_console_cockpit_graphics_list_any_auth():
    # A commentator token reaches the graphics list over the /console mount.
    srv = _serve(); port = srv.server_address[1]
    try:
        code, body = _get(port, "/console/cockpit/graphics", _tok("alice"))
        assert code == 200, (code, body)
        assert json.loads(body)["graphics"] == [], body   # no dir -> empty
    finally:
        srv.shutdown()


def t_console_cockpit_graphic_file_served_over_mount():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "Standings.png"), "wb") as fh:
            fh.write(b"\x89PNG\r\nX")
        srv = _serve(graphics_dir=d)
        try:
            url = (f"http://127.0.0.1:{srv.server_address[1]}"
                   f"/console/cockpit/graphics/Standings.png?t=" + _tok("alice"))
            with urllib.request.urlopen(url, timeout=5) as r:
                code = r.status; body = r.read()
            assert code == 200 and body == b"\x89PNG\r\nX", (code, body)
        finally:
            srv.shutdown()


def t_status_league_includes_name():
    rows = [("https://youtu.be/a", "Alice", "1", 2)]
    src = _FakeSource(_URLS8, rows)
    relay = m.Relay(src, [53001, 53002], LOGDIR, league_name="IRO GTEC")
    assert relay.status()["league"]["name"] == "IRO GTEC"


def t_console_launcher_fetches_status_and_logo():
    srv = _serve(); port = srv.server_address[1]
    try:
        code, body = _get(port, "/console", _tok("bob"))   # director
        assert code == 200, (code, body)
        assert "RC_API('/status')" in body, body
        assert "RC_API('/logo')" in body, body
    finally:
        srv.shutdown()


def t_console_status_strips_feed_urls_for_commentator():
    # Funnel-exposed /console/status must NOT leak the commentator feed stream
    # URLs (feeds[*].channel) to a commentator token, nor the POV url / Sheet id.
    srv = _serve(sheet_id="SHEET-XYZ"); port = srv.server_address[1]
    try:
        code, body = _get(port, "/console/status", _tok("alice"))   # commentator
        assert code == 200, (code, body)
        d = json.loads(body)
        assert d["feeds"]["A"]["stint"], d            # operational data kept
        assert "channel" not in d["feeds"]["A"], d    # stream URL stripped
        assert "name" in d["league"], d               # league name kept
        assert "sheet_id" not in d["league"], d       # sheet id stripped for commentator
    finally:
        srv.shutdown()


def t_console_status_keeps_sheet_id_and_feed_urls_for_director():
    srv = _serve(sheet_id="SHEET-XYZ"); port = srv.server_address[1]
    try:
        code, body = _get(port, "/console/status", _tok("bob"))     # director
        assert code == 200, (code, body)
        d = json.loads(body)
        # #493: feed stream URLs are KEPT for director/producer over the Funnel — the same
        # boundary as /schedule/data (which already gives directors per-stint URLs); they
        # power the Director-Panel Preview button. Stripped for every other role (see the
        # commentator test above). redact_console_status is unit-tested in test_pov.py.
        assert d["feeds"]["A"]["channel"] == "https://www.youtube.com/watch?v=stint1", d
        assert d["league"]["sheet_id"] == "SHEET-XYZ", d  # director keeps sheet id
    finally:
        srv.shutdown()


def t_console_schedule_data_is_director_only():
    # /schedule/data + /qualifying/data return per-stint stream URLs, so over the
    # Funnel a commentator must NOT reach them (sibling of the /status leak).
    srv = _serve(); port = srv.server_address[1]
    try:
        assert _get(port, "/console/schedule/data", _tok("alice"))[0] == 403   # commentator
        assert _get(port, "/console/qualifying/data", _tok("alice"))[0] == 403
        assert _get(port, "/console/schedule/data", _tok("bob"))[0] == 200      # director
    finally:
        srv.shutdown()


def t_tailnet_status_is_unredacted():
    # The plain tailnet /status (never through the /console gate) keeps the full
    # payload incl. feed stream URLs — the tailnet is the trust boundary.
    srv = _serve(sheet_id="SHEET-XYZ"); port = srv.server_address[1]
    try:
        code, body = _get(port, "/status")                # no /console, no token
        assert code == 200, (code, body)
        d = json.loads(body)
        assert d["feeds"]["A"]["channel"], d              # full feed URL present
        assert d["league"]["sheet_id"] == "SHEET-XYZ", d
    finally:
        srv.shutdown()


def t_console_health_monitor_page_any_authenticated():
    srv = _serve(); port = srv.server_address[1]
    try:
        # alice=commentator, bob=director, dave=race_control — all may view.
        for who in ("alice", "bob", "dave"):
            code, body = _get(port, "/console/health-monitor", _tok(who))
            assert code == 200, (who, code)
            assert 'window.RC_API_BASE = "/console"' in body or '__RC_API_BASE__' not in body
        # No token -> 401.
        assert _get(port, "/console/health-monitor", None)[0] in (401, 404)
    finally:
        srv.shutdown()


def t_console_health_monitor_data_shape_and_redaction():
    srv = _serve(); port = srv.server_address[1]
    try:
        code, body = _get(port, "/console/health-monitor/data", _tok("alice"))
        assert code == 200, (code, body)
        blob = json.loads(body)
        for key in ("now", "current", "bands", "incidents", "series"):
            assert key in blob, key
        serialised = json.dumps(blob)
        assert "youtu" not in serialised and "watch?v=" not in serialised   # redaction
    finally:
        srv.shutdown()


def t_takeover_health_requires_step_up_secret():
    srv = _serve(); port = srv.server_address[1]
    try:
        # carol=producer; without the secret header -> step-up (403/401).
        assert _get(port, "/console/takeover/health", _tok("carol"))[0] in (401, 403)
        code, body = _get(port, "/console/takeover/health", _tok("carol"),
                          headers={"X-Console-Secret": SECRET})
        assert code == 200, (code, body)
        # JSON-Lines body (possibly empty) — every non-empty line parses.
        for line in body.splitlines():
            if line.strip():
                json.loads(line)
    finally:
        srv.shutdown()


def t_health_monitor_assets_served():
    srv = _serve(); port = srv.server_address[1]
    try:
        code, body = _get(port, "/health-monitor/assets/uPlot.min.css", None)
        assert code == 200 and body.strip(), code
        # Path traversal is refused (multi-segment and a bare dot-dot segment).
        assert _get(port, "/health-monitor/assets/../../racecast.py", None)[0] in (400, 404)
        assert _get(port, "/health-monitor/assets/..", None)[0] in (400, 404)
    finally:
        srv.shutdown()


def t_console_obs_split_audio_resolves_on_air_feed():
    # #534: SPLIT audio must resolve the ACTUAL on-air feed server-side — the
    # Suzuka bug hardcoded "unmute A / mute B", muting the live commentator
    # whenever B was on air. Force live_feed() -> "B" and assert B gets
    # unmuted while A + the Discord bus get muted.
    srv = _serve(); port = srv.server_address[1]
    calls = []

    class _FakeObs:
        def set_input_mute(self, name, muted):
            calls.append((name, muted)); return True, ""

    orig_obs, m._obs_ws = m._obs_ws, _FakeObs()
    orig_live_feed = m.Relay.live_feed
    m.Relay.live_feed = lambda self: "B"
    try:
        code, body = _post(port, "/console/obs/split-audio", _tok("bob"))
        assert code == 200, (code, body)
        data = json.loads(body)
        assert data["live"] == "B", data
        assert ("Feed B", False) in calls, calls          # on-air unmuted
        assert ("Feed A", True) in calls, calls           # off-air muted
        assert ("Discord Audio Capture", True) in calls, calls
    finally:
        m._obs_ws = orig_obs
        m.Relay.live_feed = orig_live_feed
        srv.shutdown()


def t_obs_split_audio_get_route_for_companion():
    # #534: the Companion "Split Scene" button hits the tailnet-root GET route
    # (ungated, no token — like /obs/flag). Same handler, so B on air -> B
    # unmuted, A + Discord muted.
    srv = _serve(); port = srv.server_address[1]
    calls = []

    class _FakeObs:
        def set_input_mute(self, name, muted):
            calls.append((name, muted)); return True, ""

    orig_obs, m._obs_ws = m._obs_ws, _FakeObs()
    orig_live_feed = m.Relay.live_feed
    m.Relay.live_feed = lambda self: "B"
    try:
        code, body = _get(port, "/obs/split-audio")       # root path, no token
        assert code == 200, (code, body)
        data = json.loads(body)
        assert data["live"] == "B", data
        assert ("Feed B", False) in calls, calls
        assert ("Feed A", True) in calls, calls
        assert ("Discord Audio Capture", True) in calls, calls
    finally:
        m._obs_ws = orig_obs
        m.Relay.live_feed = orig_live_feed
        srv.shutdown()


class _SplitFakeObs:
    """Records the per-verb calls /obs/split makes. Swapped in for the real
    obs_ws module: this Mac runs a real OBS on 4455 that a test must not drive."""

    def __init__(self):
        self.calls = []

    def set_scene_item_enabled(self, scene, source, enabled):
        self.calls.append(("item", scene, source, enabled)); return True, ""

    def set_input_mute(self, name, muted):
        self.calls.append(("mute", name, muted)); return True, ""


def _split_with_b_on_air(fire):
    srv = _serve(); port = srv.server_address[1]
    fake = _SplitFakeObs()
    orig_obs, m._obs_ws = m._obs_ws, fake
    orig_live_feed = m.Relay.live_feed
    m.Relay.live_feed = lambda self: "B"
    try:
        code, body = fire(port)
        return code, body, fake.calls
    finally:
        m._obs_ws = orig_obs
        m.Relay.live_feed = orig_live_feed
        srv.shutdown()


def _assert_split_b_on_air(code, body, calls):
    assert code == 200, (code, body)
    data = json.loads(body)
    assert data["live"] == "B" and data["unmute"] == ["Feed B"], data
    assert ("item", "Splitscreen", "Feed A", True) in calls, calls
    assert ("item", "Splitscreen", "Feed B", True) in calls, calls
    assert ("mute", "Feed B", False) in calls, calls       # on-air commentator stays live
    assert ("mute", "Feed B", True) not in calls, calls
    assert ("mute", "Feed A", True) in calls, calls
    assert ("mute", "Discord Audio Capture", True) in calls, calls


def t_console_obs_split_resolves_on_air_feed():
    # #591: the director panel's SPLIT goes through /console, director-gated.
    _assert_split_b_on_air(*_split_with_b_on_air(
        lambda port: _post(port, "/console/obs/split", _tok("bob"))))


def t_console_obs_split_forbidden_for_commentator():
    code, _body, calls = _split_with_b_on_air(
        lambda port: _post(port, "/console/obs/split", _tok("alice")))
    assert code == 403 and calls == [], (code, calls)


def t_obs_split_get_route_for_companion():
    # #591: the Companion SPLIT button hits the tailnet-root GET route (no token).
    _assert_split_b_on_air(*_split_with_b_on_air(lambda port: _get(port, "/obs/split")))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

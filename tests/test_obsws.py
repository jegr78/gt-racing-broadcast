#!/usr/bin/env python3
"""Stdlib unit checks for the minimal obs-websocket v5 client (src/scripts/obs_ws.py):
feed-port release on `racecast relay|streams|event stop` and browser-source refresh on
`racecast relay|event start`. Run: python3 tests/test_obsws.py"""
import base64
import hashlib
import importlib.util
import json
import os
import socket
import struct
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "src", "scripts")
sys.path.insert(0, SCRIPTS)
spec = importlib.util.spec_from_file_location("obs_ws", os.path.join(SCRIPTS, "obs_ws.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

# apply_split_audio and apply_split_state live in the relay module rather than in
# obs_ws.py, because they resolve relay.live_feed(). Load it under the same
# "irofeeds" alias the other relay tests use. (#534, #591)
_relay_spec = importlib.util.spec_from_file_location(
    "irofeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
irofeeds = importlib.util.module_from_spec(_relay_spec)
_relay_spec.loader.exec_module(irofeeds)


# WebSocket plumbing (RFC 6455)
def t_accept_key_rfc6455_vector():
    # Known vector straight from RFC 6455 section 1.3.
    assert m.accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def t_handshake_request_format():
    req = m.handshake_request("127.0.0.1", 4455, "AAAAAAAAAAAAAAAAAAAAAA==")
    assert isinstance(req, bytes)
    text = req.decode()
    assert text.startswith("GET / HTTP/1.1\r\n")
    assert "Host: 127.0.0.1:4455\r\n" in text
    assert "Upgrade: websocket\r\n" in text
    assert "Connection: Upgrade\r\n" in text
    assert "Sec-WebSocket-Key: AAAAAAAAAAAAAAAAAAAAAA==\r\n" in text
    assert "Sec-WebSocket-Version: 13\r\n" in text
    assert text.endswith("\r\n\r\n")


def t_parse_handshake_accepts_valid_response():
    key = "dGhlIHNhbXBsZSBub25jZQ=="
    resp = (b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n\r\n")
    m.parse_handshake(resp, key)  # must not raise


def t_parse_handshake_rejects_bad_status_and_bad_accept():
    key = "dGhlIHNhbXBsZSBub25jZQ=="
    _raises(lambda: m.parse_handshake(b"HTTP/1.1 403 Forbidden\r\n\r\n", key))
    _raises(lambda: m.parse_handshake(
        b"HTTP/1.1 101 Switching Protocols\r\n"
        b"Sec-WebSocket-Accept: bogus=\r\n\r\n", key))


def t_encode_frame_masks_text_payload():
    mask = b"\x01\x02\x03\x04"
    frame = m.encode_frame(b"hi", mask)
    assert frame[0] == 0x81                       # FIN + text opcode
    assert frame[1] == 0x80 | 2                   # mask bit + length 2
    assert frame[2:6] == mask
    assert bytes(b ^ mask[i % 4] for i, b in enumerate(frame[6:])) == b"hi"


def t_encode_frame_extended_lengths():
    mask = b"\x00\x00\x00\x00"                    # zero mask: payload stays readable
    f126 = m.encode_frame(b"a" * 200, mask)
    assert f126[1] == 0x80 | 126
    assert struct.unpack(">H", f126[2:4])[0] == 200
    f127 = m.encode_frame(b"a" * 70000, mask)
    assert f127[1] == 0x80 | 127
    assert struct.unpack(">Q", f127[2:10])[0] == 70000


def t_decode_frame_single_and_rest():
    op, payload, rest = m.decode_frame(b"\x81\x05hello" + b"\x81\x02hi")
    assert (op, payload, rest) == (0x1, b"hello", b"\x81\x02hi")
    op, payload, rest = m.decode_frame(rest)
    assert (op, payload, rest) == (0x1, b"hi", b"")


def t_decode_frame_incomplete_returns_none():
    assert m.decode_frame(b"") is None
    assert m.decode_frame(b"\x81") is None
    assert m.decode_frame(b"\x81\x05hel") is None         # short payload
    assert m.decode_frame(b"\x81\x7e\x00") is None        # short 16-bit length


def t_decode_frame_16bit_length():
    payload = b"x" * 300
    frame = b"\x81\x7e" + struct.pack(">H", 300) + payload
    op, got, rest = m.decode_frame(frame)
    assert (op, got, rest) == (0x1, payload, b"")


# obs-websocket v5 protocol helpers
AUTH_SALT = "lM1GncleQOaCu9lT1yeUZhFYnqhsLLP1G5lAGo3ixaI="
AUTH_CHALLENGE = "ztTBnnuqrqaKDzRM3xcVdbYm"
# Independently computed from the documented formula:
# base64(sha256(base64(sha256(password + salt)) + challenge))
AUTH_EXPECTED = "1CHyRqIyanJT0eSP/mfMQR1AWZ9KgFl5l6/rPs76VDE="


def t_auth_token_matches_spec_vector():
    assert m.auth_token("supersecret", AUTH_SALT, AUTH_CHALLENGE) == AUTH_EXPECTED


def t_identify_payload_with_auth():
    hello = {"op": 0, "d": {"rpcVersion": 1, "authentication":
                            {"salt": AUTH_SALT, "challenge": AUTH_CHALLENGE}}}
    ident = m.identify_payload(hello, "supersecret")
    assert ident["op"] == 1
    assert ident["d"]["rpcVersion"] == 1
    assert ident["d"]["eventSubscriptions"] == 0   # we never want events
    assert ident["d"]["authentication"] == AUTH_EXPECTED


def t_identify_payload_without_auth():
    ident = m.identify_payload({"op": 0, "d": {"rpcVersion": 1}}, None)
    assert "authentication" not in ident["d"]


def t_identify_payload_auth_required_but_no_password():
    hello = {"op": 0, "d": {"rpcVersion": 1, "authentication":
                            {"salt": AUTH_SALT, "challenge": AUTH_CHALLENGE}}}
    _raises(lambda: m.identify_payload(hello, None))
    _raises(lambda: m.identify_payload(hello, ""))


# Which OBS inputs hold relay-feed connections?
def t_feed_input_names_picks_relay_fed_inputs():
    inputs = [{"inputName": "Feed A", "inputKind": "ffmpeg_source"},
              {"inputName": "Feed B", "inputKind": "ffmpeg_source"},
              {"inputName": "Intro Video", "inputKind": "ffmpeg_source"},
              {"inputName": "HUD", "inputKind": "browser_source"}]
    settings = {"Feed A": {"input": "http://127.0.0.1:53001", "is_local_file": False},
                "Feed B": {"input": "http://127.0.0.1:53002", "is_local_file": False},
                "Intro Video": {"local_file": "/x/intro.mp4", "is_local_file": True}}
    names = m.feed_input_names(inputs, lambda n: settings.get(n, {}),
                               ports=(53001, 53002, 53003))
    assert names == ["Feed A", "Feed B"]           # not the local file, not the browser


def t_feed_input_names_ignores_other_hosts_and_ports():
    inputs = [{"inputName": "X", "inputKind": "ffmpeg_source"},
              {"inputName": "Y", "inputKind": "ffmpeg_source"}]
    settings = {"X": {"input": "http://192.168.1.5:53001"},
                "Y": {"input": "http://127.0.0.1:9999"}}
    assert m.feed_input_names(inputs, lambda n: settings[n], ports=(53001,)) == []


def t_feed_input_names_tolerates_settings_failure():
    inputs = [{"inputName": "A", "inputKind": "ffmpeg_source"}]
    def boom(name):
        raise RuntimeError("no settings")
    assert m.feed_input_names(inputs, boom, ports=(53001,)) == []


# Which OBS browser sources show relay-served pages?
def t_browser_input_names_picks_relay_pages():
    inputs = [{"inputName": "HUD Lower Third", "inputKind": "browser_source"},
              {"inputName": "HUD Race Timer", "inputKind": "browser_source"},
              {"inputName": "Docs Panel", "inputKind": "browser_source"},
              {"inputName": "Feed A", "inputKind": "ffmpeg_source"}]
    settings = {"HUD Lower Third": {"url": "http://127.0.0.1:8088/hud"},
                "HUD Race Timer": {"url": "http://127.0.0.1:8088/timer"},
                "Docs Panel": {"url": "https://example.com/docs"},
                "Feed A": {"input": "http://127.0.0.1:53001"}}
    names = m.browser_input_names(inputs, lambda n: settings.get(n, {}),
                                  needle="127.0.0.1:8088")
    assert names == ["HUD Lower Third", "HUD Race Timer"]


def t_browser_input_names_tolerates_settings_failure():
    inputs = [{"inputName": "A", "inputKind": "browser_source"}]
    def boom(name):
        raise RuntimeError("no settings")
    assert m.browser_input_names(inputs, boom) == []


def t_browser_input_names_ignores_local_file_pages():
    # A browser source rendering a local HTML file has no "url" setting.
    inputs = [{"inputName": "Local HTML", "inputKind": "browser_source"}]
    assert m.browser_input_names(
        inputs, lambda n: {"local_file": "/x/p.html"}) == []


# Password discovery (env override, else OBS's own websocket config)
def t_obs_config_path_per_platform():
    env = {"APPDATA": r"C:\Users\x\AppData\Roaming"}
    assert m.obs_config_path("darwin", env, "/Users/x") == \
        "/Users/x/Library/Application Support/obs-studio/plugin_config/obs-websocket/config.json"
    assert m.obs_config_path("win32", env, r"C:\Users\x") == \
        r"C:\Users\x\AppData\Roaming" + "\\obs-studio\\plugin_config\\obs-websocket\\config.json"
    assert m.obs_config_path("linux", env, "/home/x") == \
        "/home/x/.config/obs-studio/plugin_config/obs-websocket/config.json"


def t_read_ws_config_roundtrip_and_missing():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "config.json")
        with open(path, "w") as fh:
            json.dump({"auth_required": True, "server_password": "pw",
                       "server_port": 4456, "server_enabled": True}, fh)
        cfg = m.read_ws_config(path)
        assert cfg == {"password": "pw", "port": 4456, "auth_required": True,
                       "enabled": True}
        assert m.read_ws_config(os.path.join(tmp, "nope.json")) is None
        broken = os.path.join(tmp, "broken.json")
        with open(broken, "w") as fh:
            fh.write("{not json")
        assert m.read_ws_config(broken) is None


def t_find_password_env_overrides_config():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "config.json")
        with open(path, "w") as fh:
            json.dump({"auth_required": True, "server_password": "from-config"}, fh)
        assert m.find_password({"RACECAST_OBS_WS_PASSWORD": "from-env"}, path) == "from-env"
        assert m.find_password({}, path) == "from-config"
        assert m.find_password({}, os.path.join(tmp, "nope.json")) is None


# release_feed_inputs, the best-effort entry point behind `racecast ... stop`.
def t_release_feed_inputs_unreachable_is_quiet():
    # Nothing listens on this port: must return a note, never raise.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    free_port = sock.getsockname()[1]
    sock.close()
    names, note = m.release_feed_inputs(port=free_port, password="x", timeout=0.5)
    assert names == []
    assert note


# Screenshot request shape + data-URI decode (pure)
def t_screenshot_request_data_shape():
    d = m.screenshot_request_data("Feed A", width=480, fmt="jpg", quality=55)
    assert d == {"sourceName": "Feed A", "imageFormat": "jpg",
                 "imageWidth": 480, "imageCompressionQuality": 55}


def t_parse_screenshot_data_uri_valid():
    raw = b"\xff\xd8\xff\xd9"
    uri = "data:image/jpg;base64," + base64.b64encode(raw).decode()
    assert m.parse_screenshot_data_uri(uri) == raw


def t_parse_screenshot_data_uri_rejects_garbage():
    assert m.parse_screenshot_data_uri("not a data uri") is None
    assert m.parse_screenshot_data_uri("data:image/jpg;base64,@@@@") is None
    assert m.parse_screenshot_data_uri(None) is None
    assert m.parse_screenshot_data_uri(12345) is None


# fake obs-websocket v5 server (loopback, one connection)
def _srv_recv_frame(conn):
    """Read one masked client frame; return (opcode, payload)."""
    head = _srv_read(conn, 2)
    opcode = head[0] & 0x0F
    length = head[1] & 0x7F
    assert head[1] & 0x80, "client frames must be masked"
    if length == 126:
        length = struct.unpack(">H", _srv_read(conn, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", _srv_read(conn, 8))[0]
    mask = _srv_read(conn, 4)
    data = _srv_read(conn, length)
    return opcode, bytes(b ^ mask[i % 4] for i, b in enumerate(data))


def _srv_read(conn, n):
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        assert chunk, "client closed early"
        buf += chunk
    return buf


def _srv_send_json(conn, obj):
    payload = json.dumps(obj).encode()
    head = b"\x81" + (bytes([len(payload)]) if len(payload) < 126
                      else b"\x7e" + struct.pack(">H", len(payload)))
    conn.sendall(head + payload)


def _fake_obs_server(server_sock, password, state):
    conn, _ = server_sock.accept()
    conn.settimeout(5)
    # HTTP upgrade
    req = b""
    while b"\r\n\r\n" not in req:
        req += conn.recv(4096)
    key = [l.split(":", 1)[1].strip() for l in req.decode().split("\r\n")
           if l.lower().startswith("sec-websocket-key:")][0]
    accept = base64.b64encode(hashlib.sha1(
        (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
    conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                  "Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept +
                  "\r\n\r\n").encode())
    # Hello -> expect Identify with the documented auth answer
    _srv_send_json(conn, {"op": 0, "d": {"rpcVersion": 1, "authentication":
                                         {"salt": AUTH_SALT, "challenge": AUTH_CHALLENGE}}})
    op, payload = _srv_recv_frame(conn)
    ident = json.loads(payload)
    secret = base64.b64encode(hashlib.sha256(
        (password + AUTH_SALT).encode()).digest()).decode()
    expected = base64.b64encode(hashlib.sha256(
        (secret + AUTH_CHALLENGE).encode()).digest()).decode()
    if ident["d"].get("authentication") != expected:
        conn.close()
        return
    _srv_send_json(conn, {"op": 2, "d": {"negotiatedRpcVersion": 1}})
    # Serve requests until the client goes away
    inputs = [{"inputName": "Feed A", "inputKind": "ffmpeg_source"},
              {"inputName": "Feed B", "inputKind": "ffmpeg_source"},
              {"inputName": "Intro Video", "inputKind": "ffmpeg_source"},
              {"inputName": "HUD Lower Third", "inputKind": "browser_source"},
              {"inputName": "HUD Race Timer", "inputKind": "browser_source"},
              {"inputName": "Docs Panel", "inputKind": "browser_source"}]
    settings = {"Feed A": {"input": "http://127.0.0.1:53001"},
                "Feed B": {"input": "http://127.0.0.1:53002"},
                "Intro Video": {"local_file": "/x/i.mp4", "is_local_file": True},
                "HUD Lower Third": {"url": "http://127.0.0.1:8088/hud"},
                "HUD Race Timer": {"url": "http://127.0.0.1:8088/timer"},
                "Docs Panel": {"url": "https://example.com/docs"}}
    while True:
        try:
            op, payload = _srv_recv_frame(conn)
        except (AssertionError, OSError):
            return
        if op == 0x8:                              # close
            conn.close()
            return
        req = json.loads(payload)
        rtype, rid = req["d"]["requestType"], req["d"]["requestId"]
        rdata = req["d"].get("requestData", {})
        if rtype == "GetSceneCollectionList":
            resp = {"currentSceneCollectionName": state.get("current_collection", ""),
                    "sceneCollections": state.get("collections", [])}
        elif rtype == "SetCurrentSceneCollection":
            if state.get("output_active"):     # OBS refuses while streaming/recording
                _srv_send_json(conn, {"op": 7, "d": {
                    "requestType": rtype, "requestId": rid,
                    "requestStatus": {"result": False, "code": 501,
                                      "comment": "output active"},
                    "responseData": {}}})
                continue
            state["set_collection"] = rdata["sceneCollectionName"]
            state["current_collection"] = rdata["sceneCollectionName"]
            resp = {}
        elif rtype == "GetInputList":
            kind = rdata.get("inputKind")
            resp = {"inputs": [i for i in inputs
                               if not kind or i["inputKind"] == kind]}
        elif rtype == "GetCurrentProgramScene":
            resp = {"currentProgramSceneName": state.get("program_scene", "Stint"),
                    "sceneName": state.get("program_scene", "Stint")}
        elif rtype == "SetCurrentProgramScene":
            state["set_scene"] = rdata["sceneName"]
            state["program_scene"] = rdata["sceneName"]
            resp = {}
        elif rtype == "GetSourceScreenshot":
            state.setdefault("shot_requests", []).append(rdata)
            raw = state.get("shot_bytes", b"\xff\xd8\xff\xd9")
            resp = {"imageData": "data:image/jpg;base64," + base64.b64encode(raw).decode()}
        elif rtype == "PressInputPropertiesButton":
            # The refresh presses OBS's own 'Refresh cache of current page'
            # button and nothing else. A wrong button gets a failed requestStatus,
            # because an assert would die silently in this daemon thread and hang
            # the client into its timeout.
            if rdata["propertyName"] != "refreshnocache":
                _srv_send_json(conn, {"op": 7, "d": {
                    "requestType": rtype, "requestId": rid,
                    "requestStatus": {"result": False, "code": 400},
                    "responseData": {}}})
                continue
            state.setdefault("refreshed", []).append(rdata["inputName"])
            resp = {}
        elif rtype == "GetInputSettings":
            resp = {"inputSettings": settings[rdata["inputName"]]}
        elif rtype == "SetInputSettings":
            # The release re-applies the input's own settings to force a source
            # rebuild, so it must never change them.
            assert rdata["inputSettings"] == settings[rdata["inputName"]]
            assert rdata["overlay"] is True
            state["released"].append(rdata["inputName"])
            resp = {}
        elif rtype == "GetSceneItemId":
            state.setdefault("get_item_id", []).append(
                (rdata["sceneName"], rdata["sourceName"]))
            resp = {"sceneItemId": 7}
        elif rtype == "SetSceneItemEnabled":
            state.setdefault("set_enabled", []).append(
                (rdata["sceneName"], rdata["sceneItemId"], rdata["sceneItemEnabled"]))
            resp = {}
        elif rtype == "GetStreamStatus":
            resp = {"outputActive": state.get("stream_active", False),
                    "outputReconnecting": state.get("stream_reconnecting", False),
                    "outputTimecode": state.get("stream_timecode", "00:00:00.000"),
                    "outputBytes": state.get("output_bytes", 0)}
        elif rtype == "StartStream":
            state.setdefault("stream_calls", []).append("start")
            state["stream_active"] = True
            resp = {}
        elif rtype == "StopStream":
            state.setdefault("stream_calls", []).append("stop")
            state["stream_active"] = False
            resp = {}
        elif rtype == "SetStreamServiceSettings":
            state.setdefault("service_settings", []).append(rdata)
            resp = {}
        elif rtype == "GetInputKindList":
            resp = {"inputKinds": state.get("input_kinds",
                    ["image_source", "av_capture_input_v2", "coreaudio_input_capture"])}
        elif rtype == "CreateScene":
            state.setdefault("created_scenes", []).append(rdata["sceneName"])
            resp = {}
        elif rtype == "RemoveScene":
            state.setdefault("removed_scenes", []).append(rdata["sceneName"])
            resp = {}
        elif rtype == "CreateInput":
            state.setdefault("created_inputs", []).append(
                (rdata["sceneName"], rdata["inputName"], rdata["inputKind"],
                 rdata.get("sceneItemEnabled")))
            if state.get("create_raises"):      # OBS created it, but the response fails
                _srv_send_json(conn, {"op": 7, "d": {
                    "requestType": rtype, "requestId": rid,
                    "requestStatus": {"result": False, "code": 604},
                    "responseData": {}}})
                continue
            resp = {}
        elif rtype == "RemoveInput":
            state.setdefault("removed_inputs", []).append(rdata["inputName"])
            resp = {}
        elif rtype == "GetInputPropertiesListPropertyItems":
            state.setdefault("prop_reads", []).append(
                (rdata["inputName"], rdata["propertyName"]))
            if state.get("prop_raises"):
                _srv_send_json(conn, {"op": 7, "d": {
                    "requestType": rtype, "requestId": rid,
                    "requestStatus": {"result": False, "code": 604},
                    "responseData": {}}})
                continue
            table = state.get("prop_items", {})
            resp = {"propertyItems": table.get(rdata["propertyName"], [])}
        else:
            resp = {}
        _srv_send_json(conn, {"op": 7, "d": {
            "requestType": rtype, "requestId": rid,
            "requestStatus": {"result": True, "code": 100},
            "responseData": resp}})


def t_release_feed_inputs_end_to_end_against_fake_server():
    server_sock = socket.socket()
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    port = server_sock.getsockname()[1]
    state = {"released": []}
    thread = threading.Thread(target=_fake_obs_server,
                              args=(server_sock, "supersecret", state), daemon=True)
    thread.start()
    names, note = m.release_feed_inputs(port=port, password="supersecret", timeout=5)
    assert note == "", note
    assert names == ["Feed A", "Feed B"]
    assert state["released"] == ["Feed A", "Feed B"]
    server_sock.close()


def t_release_feed_inputs_wrong_password_is_note_not_crash():
    server_sock = socket.socket()
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    port = server_sock.getsockname()[1]
    thread = threading.Thread(target=_fake_obs_server,
                              args=(server_sock, "supersecret", {"released": []}),
                              daemon=True)
    thread.start()
    names, note = m.release_feed_inputs(port=port, password="WRONG", timeout=2)
    assert names == []
    assert note
    server_sock.close()


# set_stream, the Director Panel's broadcast start and stop, over the shared
# _start_fake_obs helper defined further down. (#295)
def t_set_stream_starts_when_offline():
    state = {"stream_active": False}
    port, srv = _start_fake_obs(state)
    ok, note = m.set_stream(True, port=port, password="supersecret", timeout=5)
    assert ok and note == "", note
    assert state["stream_calls"] == ["start"]
    assert state["stream_active"] is True
    srv.close()


def t_set_stream_stops_when_live():
    state = {"stream_active": True}
    port, srv = _start_fake_obs(state)
    ok, note = m.set_stream(False, port=port, password="supersecret", timeout=5)
    assert ok and note == "", note
    assert state["stream_calls"] == ["stop"]
    assert state["stream_active"] is False
    srv.close()


def t_set_stream_is_idempotent_noop_when_already_live():
    state = {"stream_active": True}
    port, srv = _start_fake_obs(state)
    ok, note = m.set_stream(True, port=port, password="supersecret", timeout=5)
    assert ok and note == "", note
    assert "stream_calls" not in state
    srv.close()


def t_set_stream_unreachable_is_note_not_crash():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    free_port = sock.getsockname()[1]
    sock.close()
    ok, note = m.set_stream(True, port=free_port, password="x", timeout=0.5)
    assert ok is False
    assert note


def t_parse_stream_status_includes_timecode():
    out = m.parse_stream_status({"outputActive": True,
                                 "outputReconnecting": False,
                                 "outputTimecode": "00:12:34.567"})
    assert out["stream_timecode"] == "00:12:34.567"
    assert out["stream_active"] is True


def t_read_obs_state_includes_stream():
    state = {"released": [], "stream_active": True,
             "stream_timecode": "01:02:03.000"}
    port, srv = _start_fake_obs(state)
    out, note = m.read_obs_state([("Stint", "Feed A")], ["Feed A"],
                                 port=port, password="supersecret", timeout=5)
    assert note == "", note
    assert out["stream"] == {"active": True, "reconnecting": False,
                             "timecode": "01:02:03.000"}
    srv.close()


# refresh_browser_inputs, the auto-refresh behind `racecast relay|event start`.
def t_refresh_browser_inputs_end_to_end_against_fake_server():
    server_sock = socket.socket()
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    port = server_sock.getsockname()[1]
    state = {"released": [], "refreshed": []}
    thread = threading.Thread(target=_fake_obs_server,
                              args=(server_sock, "supersecret", state), daemon=True)
    thread.start()
    names, note = m.refresh_browser_inputs(port=port, password="supersecret",
                                           timeout=5)
    assert note == "", note
    assert names == ["HUD Lower Third", "HUD Race Timer"]
    assert state["refreshed"] == ["HUD Lower Third", "HUD Race Timer"]
    assert state["released"] == []                 # refresh must not touch feeds
    server_sock.close()


def t_refresh_browser_inputs_unreachable_is_quiet():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    free_port = sock.getsockname()[1]
    sock.close()
    names, note = m.refresh_browser_inputs(port=free_port, password="x",
                                           timeout=0.5)
    assert names == []
    assert note


# probe, the side-effect-free reachability check behind /status's obs.reachable.
def t_probe_unreachable_is_quiet():
    # Nothing listens here: (False, note), never an exception.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    free_port = sock.getsockname()[1]
    sock.close()
    reachable, note = m.probe(port=free_port, password="x", timeout=0.5)
    assert reachable is False
    assert note


def t_probe_end_to_end_against_fake_server():
    # A full handshake + auth -> reachable, no note, and the probe touches
    # nothing in OBS (no released feeds, no refreshed sources).
    state = {"released": [], "refreshed": []}
    port, srv = _start_fake_obs(state)
    reachable, note = m.probe(port=port, password="supersecret", timeout=5)
    assert reachable is True
    assert note == "", note
    assert state["released"] == [] and state["refreshed"] == []
    srv.close()


def t_probe_wrong_password_is_not_reachable():
    state = {"released": []}
    port, srv = _start_fake_obs(state)
    reachable, note = m.probe(port=port, password="WRONG", timeout=2)
    assert reachable is False
    assert note
    srv.close()


# set_scene_item_enabled, the relay-driven POV PiP show and hide. (#130)
def t_set_scene_item_enabled_end_to_end_against_fake_server():
    server_sock = socket.socket()
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    port = server_sock.getsockname()[1]
    state = {"released": [], "set_enabled": []}
    thread = threading.Thread(target=_fake_obs_server,
                              args=(server_sock, "supersecret", state), daemon=True)
    thread.start()
    ok, note = m.set_scene_item_enabled("Stint", "Feed POV", True,
                                        port=port, password="supersecret", timeout=5)
    assert ok is True, note
    assert note == ""
    assert state["set_enabled"] == [("Stint", 7, True)]
    server_sock.close()


def t_set_scene_item_enabled_unreachable_is_quiet():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    free_port = sock.getsockname()[1]
    sock.close()
    ok, note = m.set_scene_item_enabled("Stint", "Feed POV", False,
                                        port=free_port, password="x", timeout=0.5)
    assert ok is False
    assert note


# get_scene_collection and set_scene_collection, best-effort like the others.
def _start_fake_obs(state, password="supersecret"):
    """Spin up the loopback fake OBS server; return its port (daemon thread)."""
    server_sock = socket.socket()
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    port = server_sock.getsockname()[1]
    thread = threading.Thread(target=_fake_obs_server,
                              args=(server_sock, password, state), daemon=True)
    thread.start()
    return port, server_sock


def t_get_scene_collection_reads_current_and_list():
    state = {"released": [], "current_collection": "GT Racing Endurance",
             "collections": ["GT Racing Endurance", "Other"]}
    port, srv = _start_fake_obs(state)
    status, note = m.get_scene_collection(port=port, password="supersecret", timeout=5)
    assert note == "", note
    assert status["current"] == "GT Racing Endurance"
    assert status["match"] is True
    assert status["available"] == ["GT Racing Endurance", "Other"]
    srv.close()


def t_get_scene_collection_honors_custom_expected():
    state = {"released": [], "current_collection": "ERF Endurance",
             "collections": ["ERF Endurance", "GT Racing Endurance"]}
    port, srv = _start_fake_obs(state)
    status, note = m.get_scene_collection(port=port, password="supersecret",
                                          timeout=5, expected="ERF Endurance")
    assert note == "", note
    assert status["expected"] == "ERF Endurance"
    assert status["match"] is True          # would be False against the default
    srv.close()


def t_get_scene_collection_unreachable_is_quiet():
    sock = socket.socket(); sock.bind(("127.0.0.1", 0))
    free_port = sock.getsockname()[1]; sock.close()
    status, note = m.get_scene_collection(port=free_port, password="x", timeout=0.5)
    assert status is None
    assert note


def t_set_scene_collection_switches_when_present_and_different():
    state = {"released": [], "current_collection": "Other",
             "collections": ["GT Racing Endurance", "Other"]}
    port, srv = _start_fake_obs(state)
    ok, note = m.set_scene_collection(port=port, password="supersecret", timeout=5)
    assert ok is True, note
    assert state["set_collection"] == "GT Racing Endurance"
    srv.close()


def t_set_scene_collection_noop_when_already_correct():
    state = {"released": [], "current_collection": "GT Racing Endurance",
             "collections": ["GT Racing Endurance"]}
    port, srv = _start_fake_obs(state)
    ok, note = m.set_scene_collection(port=port, password="supersecret", timeout=5)
    assert ok is True
    assert "already" in note
    assert "set_collection" not in state
    srv.close()


def t_set_scene_collection_refuses_when_absent():
    state = {"released": [], "current_collection": "Other",
             "collections": ["Other", "Spare"]}
    port, srv = _start_fake_obs(state)
    ok, note = m.set_scene_collection(port=port, password="supersecret", timeout=5)
    assert ok is False
    assert "not found" in note
    assert "set_collection" not in state
    srv.close()


def t_set_scene_collection_output_active_is_note_not_crash():
    state = {"released": [], "current_collection": "Other",
             "collections": ["GT Racing Endurance", "Other"], "output_active": True}
    port, srv = _start_fake_obs(state)
    ok, note = m.set_scene_collection(port=port, password="supersecret", timeout=5)
    assert ok is False
    assert note                                    # carries OBS's rejection
    srv.close()


def t_set_scene_collection_unreachable_is_quiet():
    sock = socket.socket(); sock.bind(("127.0.0.1", 0))
    free_port = sock.getsockname()[1]; sock.close()
    ok, note = m.set_scene_collection(port=free_port, password="x", timeout=0.5)
    assert ok is False
    assert note


# switch_to_scene_if_idle parks OBS on Standby at `event start`, never cutting a
# live program.
def t_switch_to_scene_if_idle_switches_when_offline():
    state = {"stream_active": False, "program_scene": "Stint"}
    port, srv = _start_fake_obs(state)
    action, note = m.switch_to_scene_if_idle("Standby", port=port,
                                             password="supersecret", timeout=5)
    assert action == "switched", (action, note)
    assert note == "", note
    assert state["set_scene"] == "Standby"
    srv.close()


def t_switch_to_scene_if_idle_skips_when_live():
    state = {"stream_active": True, "program_scene": "Stint"}
    port, srv = _start_fake_obs(state)
    action, note = m.switch_to_scene_if_idle("Standby", port=port,
                                             password="supersecret", timeout=5)
    assert action == "live", (action, note)
    assert note
    assert "set_scene" not in state              # SAFETY: no scene switch sent while live
    assert state["program_scene"] == "Stint"
    srv.close()


def t_switch_to_scene_if_idle_unreachable_is_note_not_crash():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    free_port = sock.getsockname()[1]
    sock.close()
    action, note = m.switch_to_scene_if_idle("Standby", port=free_port,
                                             password="x", timeout=0.5)
    assert action == "error", (action, note)
    assert note


# get_source_screenshot and get_program_screenshot, best-effort fetchers.
def t_get_source_screenshot_returns_bytes():
    state = {"shot_bytes": b"\xff\xd8hello\xff\xd9"}
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    port = srv.getsockname()[1]
    threading.Thread(target=_fake_obs_server, args=(srv, "pw", state), daemon=True).start()
    data, note = m.get_source_screenshot("Feed A", width=320, host="127.0.0.1",
                                         port=port, password="pw", timeout=5)
    srv.close()
    assert note == "" and data == b"\xff\xd8hello\xff\xd9"
    assert state["shot_requests"][0]["sourceName"] == "Feed A"
    assert state["shot_requests"][0]["imageWidth"] == 320


def t_get_program_screenshot_uses_current_scene():
    state = {"program_scene": "Stint", "shot_bytes": b"\xff\xd8PGM\xff\xd9"}
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
    port = srv.getsockname()[1]
    threading.Thread(target=_fake_obs_server, args=(srv, "pw", state), daemon=True).start()
    data, note = m.get_program_screenshot(width=640, host="127.0.0.1",
                                          port=port, password="pw", timeout=5)
    srv.close()
    assert note == "" and data == b"\xff\xd8PGM\xff\xd9"
    assert state["shot_requests"][0]["sourceName"] == "Stint"


def t_get_source_screenshot_unreachable_is_quiet():
    sock = socket.socket(); sock.bind(("127.0.0.1", 0))
    free = sock.getsockname()[1]; sock.close()
    data, note = m.get_source_screenshot("Feed A", port=free, password="x", timeout=0.5)
    assert data is None and note


# get_current_program_scene and set_current_program_scene, the auto-failover. (#378)
def t_get_current_program_scene_reads_current():
    state = {"program_scene": "Intermission"}
    port, srv = _start_fake_obs(state)
    scene, note = m.get_current_program_scene(port=port, password="supersecret", timeout=5)
    srv.close()
    assert note == "" and scene == "Intermission"


def t_get_current_program_scene_unreachable_is_note_not_crash():
    sock = socket.socket(); sock.bind(("127.0.0.1", 0))
    free = sock.getsockname()[1]; sock.close()
    scene, note = m.get_current_program_scene(port=free, password="x", timeout=0.5)
    assert scene is None and note


def t_set_current_program_scene_switches_to_intermission():
    state = {"program_scene": "Stint"}
    port, srv = _start_fake_obs(state)
    ok, note = m.set_current_program_scene(m.INTERMISSION_SCENE, port=port,
                                           password="supersecret", timeout=5)
    srv.close()
    assert ok and note == "", note
    assert state["set_scene"] == "Intermission"
    assert m.INTERMISSION_SCENE == "Intermission"


# The pure scene-collection classifier, scene_collection_status.
def t_scene_collection_status_match():
    s = m.scene_collection_status("GT Racing Endurance", ["GT Racing Endurance", "Other"])
    assert s == {"current": "GT Racing Endurance", "expected": "GT Racing Endurance",
                 "available": ["GT Racing Endurance", "Other"], "match": True,
                 "expected_present": True, "renamed_variant": None}


def t_scene_collection_status_wrong_but_present():
    s = m.scene_collection_status("Other", ["GT Racing Endurance", "Other"])
    assert s["match"] is False
    assert s["expected_present"] is True
    assert s["renamed_variant"] is None
    assert s["current"] == "Other"


def t_scene_collection_status_renamed_variant():
    s = m.scene_collection_status("GT Racing Endurance 2", ["GT Racing Endurance 2", "Scene"])
    assert s["match"] is False
    assert s["expected_present"] is False
    assert s["renamed_variant"] == "GT Racing Endurance 2"
    assert s["current"] == "GT Racing Endurance 2"


def t_scene_collection_status_match_suppresses_renamed_variant():
    # A correct collection plus an old import-renamed duplicate must not report a
    # renamed_variant: the match wins, with no false "looks renamed" warning.
    s = m.scene_collection_status("GT Racing Endurance",
                                  ["GT Racing Endurance", "GT Racing Endurance 2"])
    assert s["match"] is True
    assert s["renamed_variant"] is None


def t_scene_collection_status_overlap_present_and_renamed():
    # A renamed duplicate is active while the real collection also exists, so both
    # flags are truthy and consumers must prefer the switchable case.
    s = m.scene_collection_status("GT Racing Endurance 2",
                                  ["GT Racing Endurance", "GT Racing Endurance 2"])
    assert s["match"] is False
    assert s["expected_present"] is True
    assert s["renamed_variant"] == "GT Racing Endurance 2"


def t_scene_collection_status_expected_absent():
    s = m.scene_collection_status("Scene", ["Scene", "Foo"])
    assert s["match"] is False
    assert s["expected_present"] is False
    assert s["renamed_variant"] is None


def t_scene_collection_status_empty_current():
    s = m.scene_collection_status(None, [])
    assert s["match"] is False
    assert s["expected_present"] is False
    assert s["renamed_variant"] is None
    assert s["current"] is None


# The pure event-start decision, scene_collection_action.
def t_scene_collection_action_skip_when_no_status():
    assert m.scene_collection_action(None, "OBS closed", True) == ("skip", "OBS closed")


def t_scene_collection_action_ok_when_match():
    s = m.scene_collection_status("GT Racing Endurance", ["GT Racing Endurance"])
    assert m.scene_collection_action(s, "", True) == ("ok", "GT Racing Endurance")


def t_scene_collection_action_switch_when_mismatch_present_enabled():
    s = m.scene_collection_status("Other", ["GT Racing Endurance", "Other"])
    assert m.scene_collection_action(s, "", True) == ("switch", "GT Racing Endurance")


def t_scene_collection_action_warn_present_when_switch_disabled():
    s = m.scene_collection_status("Other", ["GT Racing Endurance", "Other"])
    action, detail = m.scene_collection_action(s, "", False)
    assert action == "warn_present" and detail is s        # dict passed through


def t_scene_collection_action_warn_absent_when_not_imported():
    s = m.scene_collection_status("Other", ["Other", "Foo"])
    action, detail = m.scene_collection_action(s, "", True)
    assert action == "warn_absent" and detail is s


def t_scene_collection_action_never_switches_to_renamed_variant():
    # expected exact name absent, only a "GT Racing Endurance 2" variant present
    s = m.scene_collection_status("GT Racing Endurance 2", ["GT Racing Endurance 2"])
    assert m.scene_collection_action(s, "", True)[0] == "warn_absent"
    assert s["renamed_variant"] == "GT Racing Endurance 2"


class _FakeSock:
    """Records sendall/recv/shutdown/settimeout/close for _Session.close() tests.
    recv_chunks is a list of bytes (b"" means EOF) or exceptions to raise in order."""
    def __init__(self, recv_chunks=None, raise_on_send=None):
        self.sent = b""
        self.calls = []                 # ordered method names
        self.timeout = None
        self._recv = list(recv_chunks or [b""])
        self._raise_on_send = raise_on_send
    def sendall(self, data):
        self.calls.append("sendall")
        if self._raise_on_send:
            raise self._raise_on_send
        self.sent += data
    def recv(self, n):
        self.calls.append("recv")
        if not self._recv:
            return b""
        item = self._recv.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    def shutdown(self, how):
        self.calls.append("shutdown")
    def settimeout(self, t):
        self.calls.append("settimeout")
        self.timeout = t
    def close(self):
        self.calls.append("close")


def _unmask_client_frame(buf):
    """Decode one masked client->server frame; return (opcode, unmasked_payload)."""
    opcode = buf[0] & 0x0F
    length = buf[1] & 0x7F
    mask = buf[2:6]
    masked = buf[6:6 + length]
    payload = bytes(b ^ mask[i % 4] for i, b in enumerate(masked))
    return opcode, payload


def t_close_sends_status_1000_then_drains_then_close():
    sock = _FakeSock(recv_chunks=[b""])          # immediate EOF
    sess = m._Session(sock, b"")
    sess.close()
    opcode, payload = _unmask_client_frame(sock.sent)
    assert opcode == 0x8, opcode                  # close frame
    assert payload == struct.pack(">H", 1000), payload
    assert "close" in sock.calls
    # close() must not shutdown(SHUT_WR): an early TCP FIN makes OBS log the
    # disconnect as 1006 "End of File" instead of 1000.
    assert "shutdown" not in sock.calls, sock.calls
    assert sock.timeout == m.CLOSE_DRAIN_TIMEOUT_S
    # settimeout must precede the draining recv, or the drain can hang.
    if "recv" in sock.calls:
        assert sock.calls.index("settimeout") < sock.calls.index("recv")


def t_close_returns_on_echo_then_eof():
    # OBS echoes a close frame (server->client, unmasked), then EOF.
    echo = m.encode_frame(struct.pack(">H", 1000), mask=b"\x00\x00\x00\x00", opcode=0x8)
    sock = _FakeSock(recv_chunks=[echo, b""])
    sess = m._Session(sock, b"")
    sess.close()                                  # must not raise
    assert sock.calls.count("close") == 1


def t_close_does_not_hang_on_silent_socket():
    # recv raising timeout simulates the drain deadline; close() must still finish.
    sock = _FakeSock(recv_chunks=[socket.timeout()])
    sess = m._Session(sock, b"")
    sess.close()                                  # must not raise, must not loop
    assert "close" in sock.calls


def t_close_safe_when_obs_already_dropped_socket():
    # sendall raising OSError (OBS gone) must be swallowed; socket still closed.
    sock = _FakeSock(raise_on_send=OSError("broken pipe"))
    sess = m._Session(sock, b"")
    sess.close()                                  # must not raise
    assert "close" in sock.calls


def _raises(fn, exc=ValueError):
    try:
        fn()
    except exc:
        return
    raise AssertionError(f"expected {exc.__name__}")


# The pure intent planner, feed_state_intents.
def t_feed_state_intents_live_a_with_cut():
    assert m.feed_state_intents("A", True) == [
        ("show", "Feed A"), ("hide", "Feed B"),
        ("unmute", "Feed A"), ("mute", "Feed B"),
        ("cut", "Stint"),
    ]


def t_feed_state_intents_live_b_no_cut():
    assert m.feed_state_intents("B", False) == [
        ("show", "Feed B"), ("hide", "Feed A"),
        ("unmute", "Feed B"), ("mute", "Feed A"),
    ]


# The Splitscreen state, resolved from the on-air slot like the Stint scene. (#591)
def t_split_state_intents_live_a():
    assert m.split_state_intents("A", False) == [
        ("show", "Feed A"), ("show", "Feed B"),
        ("unmute", "Feed A"),
        ("mute", "Feed B"), ("mute", "Discord Audio Capture"),
    ]


def t_split_state_intents_live_b():
    # B on air must keep B audible and mute A, never the reverse.
    intents = m.split_state_intents("B", False)
    assert intents == [
        ("show", "Feed A"), ("show", "Feed B"),
        ("unmute", "Feed B"),
        ("mute", "Feed A"), ("mute", "Discord Audio Capture"),
    ]
    assert ("mute", "Feed B") not in intents and ("unmute", "Feed A") not in intents


def t_split_state_intents_cut_is_last():
    assert m.split_state_intents("A", True)[-1] == ("cut", "Splitscreen")
    assert all(verb != "cut" for verb, _ in m.split_state_intents("A", False))


def t_split_state_intents_slot_with_two_audio_inputs():
    # A local slot contributes its media source plus the commentary microphone:
    # both are audible while it is on air and both are muted while it is not.
    slots = {"A": ("Feed Local", ["Feed Local", "Commentary Mic"]),
             "B": ("Feed B", ["Feed B"])}
    on = m.split_state_intents("A", False, slots=slots)
    assert on == [
        ("show", "Feed Local"), ("show", "Feed B"),
        ("unmute", "Feed Local"), ("unmute", "Commentary Mic"),
        ("mute", "Feed B"), ("mute", "Discord Audio Capture"),
    ]
    off = m.split_state_intents("B", False, slots=slots)
    assert off == [
        ("show", "Feed Local"), ("show", "Feed B"),
        ("unmute", "Feed B"),
        ("mute", "Feed Local"), ("mute", "Commentary Mic"),
        ("mute", "Discord Audio Capture"),
    ]


# The relay-mediated OBS control helpers: set_current_program_scene,
# set_input_volume, set_input_mute and read_obs_state.
class _FakeSession:
    def __init__(self, responses=None):
        self.sent = []
        self._responses = responses or {}

    def request(self, request_type, request_data=None):
        self.sent.append((request_type, request_data or {}))
        return self._responses.get(request_type, {})

    def close(self):
        self.sent.append(("close", {}))


# The commentary microphone of a local stint is live only while the on-air slot is
# the local capture, and muted in every other state. (#593)
MIC = "Commentary Mic Device"


def t_feed_audio_plan_without_a_managed_mic_is_one_input_per_feed():
    assert m.feed_audio_plan({"A"}, mic=None) == (
        {"A": ["Feed A"], "B": ["Feed B"]}, [])


def t_feed_audio_plan_gives_the_mic_to_the_local_slot():
    assert m.feed_audio_plan({"B"}, mic=MIC) == (
        {"A": ["Feed A"], "B": ["Feed B", MIC]}, [MIC])
    assert m.feed_audio_plan(set(), mic=MIC) == (
        {"A": ["Feed A"], "B": ["Feed B"]}, [MIC])


def t_feed_state_intents_local_on_air_opens_the_mic():
    audio, extra = m.feed_audio_plan({"A"}, mic=MIC)
    assert m.feed_state_intents("A", True, audio=audio, extra_mute=extra) == [
        ("show", "Feed A"), ("hide", "Feed B"),
        ("unmute", "Feed A"), ("unmute", MIC), ("mute", "Feed B"),
        ("cut", "Stint"),
    ]


def t_feed_state_intents_local_off_air_mutes_the_mic_once():
    audio, extra = m.feed_audio_plan({"A"}, mic=MIC)
    assert m.feed_state_intents("B", False, audio=audio, extra_mute=extra) == [
        ("show", "Feed B"), ("hide", "Feed A"),
        ("unmute", "Feed B"), ("mute", "Feed A"), ("mute", MIC),
    ]


def t_feed_state_intents_no_local_slot_still_mutes_the_mic():
    # The outgoing local stint may already have advanced to a remote row when the
    # handover lands, and the mic must close anyway.
    audio, extra = m.feed_audio_plan(set(), mic=MIC)
    intents = m.feed_state_intents("B", False, audio=audio, extra_mute=extra)
    assert intents[-1] == ("mute", MIC), intents
    assert ("unmute", MIC) not in intents


def t_split_state_intents_mic_follows_the_on_air_slot():
    audio, extra = m.feed_audio_plan({"B"}, mic=MIC)
    slots = {f: (m.FEED_SOURCES[f], audio[f]) for f in ("A", "B")}
    on = m.split_state_intents("B", False, slots=slots, extra_mute=extra)
    assert on == [
        ("show", "Feed A"), ("show", "Feed B"),
        ("unmute", "Feed B"), ("unmute", MIC),
        ("mute", "Feed A"), ("mute", "Discord Audio Capture"),
    ]
    off = m.split_state_intents("A", False, slots=slots, extra_mute=extra)
    assert ("mute", MIC) in off and ("unmute", MIC) not in off
    assert off.count(("mute", MIC)) == 1, off


def t_split_state_intents_never_mutes_an_input_it_just_opened():
    # Two back-to-back local stints put the mic in both slots; the on-air one wins.
    audio, extra = m.feed_audio_plan({"A", "B"}, mic=MIC)
    slots = {f: (m.FEED_SOURCES[f], audio[f]) for f in ("A", "B")}
    intents = m.split_state_intents("A", False, slots=slots, extra_mute=extra)
    assert ("unmute", MIC) in intents and ("mute", MIC) not in intents, intents


class _MuteFailSession(_FakeSession):
    """SetInputMute raises for the named inputs, like OBS for a missing input."""

    def __init__(self, missing, responses=None):
        super().__init__(responses)
        self.missing = set(missing)

    def request(self, request_type, request_data=None):
        if request_type == "SetInputMute" and (request_data or {}).get("inputName") in self.missing:
            self.sent.append((request_type, request_data))
            raise ValueError(f"request SetInputMute failed: {request_data['inputName']} not found")
        return super().request(request_type, request_data)


def t_reflect_feed_state_cuts_even_without_the_mic_input():
    # A collection imported before the mic existed has no mic input, and the
    # handover cut must still land. (#593)
    sess = _MuteFailSession({MIC}, {"GetSceneItemId": {"sceneItemId": 3}})
    audio, extra = m.feed_audio_plan({"A"}, mic=MIC)
    applied, note = m.reflect_feed_state("B", True, audio=audio, extra_mute=extra,
                                         session=sess)
    assert ("SetCurrentProgramScene", {"sceneName": "Stint"}) in sess.sent, sess.sent
    assert ("cut", "Stint") in applied and ("mute", MIC) not in applied, applied
    assert MIC in note, note


def t_set_current_program_scene_sends_request():
    sess = _FakeSession()
    orig, m._connect = m._connect, lambda *a, **k: (sess, "")
    try:
        ok, note = m.set_current_program_scene("Stint")
    finally:
        m._connect = orig
    assert ok is True and note == ""
    assert ("SetCurrentProgramScene", {"sceneName": "Stint"}) in sess.sent


def t_set_input_volume_and_mute():
    sess = _FakeSession()
    orig, m._connect = m._connect, lambda *a, **k: (sess, "")
    try:
        assert m.set_input_volume("Mic", -6.0)[0] is True
        assert m.set_input_mute("Mic", True)[0] is True
    finally:
        m._connect = orig
    assert ("SetInputVolume", {"inputName": "Mic", "inputVolumeDb": -6.0}) in sess.sent
    assert ("SetInputMute", {"inputName": "Mic", "inputMuted": True}) in sess.sent


def t_read_obs_state_batches_one_session():
    sess = _FakeSession({
        "GetCurrentProgramScene": {"currentProgramSceneName": "Stint"},
        "GetSceneItemId": {"sceneItemId": 7},
        "GetSceneItemEnabled": {"sceneItemEnabled": True},
        "GetInputMute": {"inputMuted": False},
        "GetInputVolume": {"inputVolumeDb": -3.0},
    })
    orig, m._connect = m._connect, lambda *a, **k: (sess, "")
    try:
        state, note = m.read_obs_state([("Stint", "HUD")], ["Mic"])
    finally:
        m._connect = orig
    assert note == "" and state["scene"] == "Stint"
    assert state["sources"] == [{"scene": "Stint", "source": "HUD", "enabled": True}]
    assert state["audio"] == [{"input": "Mic", "muted": False, "volumeDb": -3.0}]


def t_obs_helpers_unreachable_return_failure_not_raise():
    orig, m._connect = m._connect, lambda *a, **k: (None, "OBS not running")
    try:
        assert m.set_current_program_scene("Stint") == (False, "OBS not running")
        assert m.read_obs_state([], []) == (None, "OBS not running")
    finally:
        m._connect = orig


def t_resolve_obs_target_env_overrides_then_config():
    # RACECAST_OBS_WS_HOST and _PORT override the discovered target.
    env = {"RACECAST_OBS_WS_HOST": "100.64.0.5", "RACECAST_OBS_WS_PORT": "4466"}
    assert m.resolve_obs_target("127.0.0.1", None, env, {"port": 4455}) == ("100.64.0.5", 4466)
    # With no override: the caller's host and OBS's own config port.
    assert m.resolve_obs_target("127.0.0.1", None, {}, {"port": 4455}) == ("127.0.0.1", 4455)
    # With no override and no config: the 4455 default.
    assert m.resolve_obs_target("127.0.0.1", None, {}, None) == ("127.0.0.1", m.DEFAULT_PORT)
    # A non-numeric port override is ignored and falls back to config or default.
    assert m.resolve_obs_target(
        "127.0.0.1", None, {"RACECAST_OBS_WS_PORT": "x"}, {"port": 4455}) == ("127.0.0.1", 4455)
    # A host override alone still resolves the port via config or default.
    assert m.resolve_obs_target("127.0.0.1", None, {"RACECAST_OBS_WS_HOST": "100.64.0.9"},
                                {"port": 4455}) == ("100.64.0.9", 4455)


# parse_obs_stats / parse_stream_status / get_health_stats
def t_parse_obs_stats():
    p = {"cpuUsage": 12.5, "memoryUsage": 910.0, "availableDiskSpace": 51200.0,
         "activeFps": 60.0, "renderSkippedFrames": 3, "renderTotalFrames": 1000}
    out = m.parse_obs_stats(p)
    assert out["obs_cpu_pct"] == 12.5
    assert out["obs_mem_mb"] == 910.0
    assert out["obs_disk_free_mb"] == 51200.0
    assert out["obs_fps"] == 60.0
    assert out["obs_render_skipped_pct"] == 0.3
    # The raw cumulative counts carry through for the render-drift rate. (#488)
    assert out["obs_render_skipped_frames"] == 3 and out["obs_render_total_frames"] == 1000
    # A missing field gives None rather than a KeyError, and a zero total gives
    # None rather than a division by zero.
    out2 = m.parse_obs_stats({"renderSkippedFrames": 0, "renderTotalFrames": 0})
    assert out2["obs_cpu_pct"] is None and out2["obs_render_skipped_pct"] is None
    assert out2["obs_render_skipped_frames"] == 0 and out2["obs_render_total_frames"] == 0


def t_parse_stream_status():
    p = {"outputActive": True, "outputReconnecting": False, "outputCongestion": 0.2,
         "outputSkippedFrames": 5, "outputTotalFrames": 500, "outputBytes": 1234567}
    out = m.parse_stream_status(p)
    assert out["stream_active"] is True
    assert out["stream_reconnecting"] is False
    assert out["stream_congestion"] == 0.2
    assert out["stream_dropped_pct"] == 1.0
    assert out["output_bytes"] == 1234567
    out2 = m.parse_stream_status({})
    assert out2["stream_active"] is None and out2["stream_dropped_pct"] is None


def t_get_health_stats_unreachable_is_quiet():
    # Nothing listens here: (False, {}, note), never an exception.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    free_port = sock.getsockname()[1]
    sock.close()
    reachable, stats, note = m.get_health_stats(port=free_port, password="x", timeout=0.5)
    assert reachable is False
    assert stats == {}
    assert note


def t_get_health_stats_merges_stats_and_stream_status():
    # Use a fake session that returns canned GetStats + GetStreamStatus payloads.
    sess = _FakeSession({
        "GetStats": {"cpuUsage": 5.0, "memoryUsage": 800.0,
                     "availableDiskSpace": 20000.0, "activeFps": 60.0,
                     "renderSkippedFrames": 0, "renderTotalFrames": 500},
        "GetStreamStatus": {"outputActive": True, "outputReconnecting": False,
                            "outputCongestion": 0.0, "outputSkippedFrames": 0,
                            "outputTotalFrames": 200, "outputBytes": 999},
    })
    orig, m._connect = m._connect, lambda *a, **k: (sess, "")
    try:
        reachable, stats, note = m.get_health_stats()
    finally:
        m._connect = orig
    assert reachable is True
    assert note == ""
    # parse_obs_stats keys present
    assert stats["obs_cpu_pct"] == 5.0
    assert stats["obs_mem_mb"] == 800.0
    assert stats["obs_render_skipped_pct"] == 0.0   # 0 skipped / 500 total -> 0%
    # parse_stream_status keys present
    assert stats["stream_active"] is True
    assert stats["output_bytes"] == 999
    # session was closed
    assert ("close", {}) in sess.sent


def t_parse_video_settings_configured_fps():
    # The configured frame rate is the reference the measured activeFps is judged
    # against. (#586)
    assert m.parse_video_settings({"fpsNumerator": 60, "fpsDenominator": 1}) == \
        {"obs_fps_target": 60.0}
    assert m.parse_video_settings({"fpsNumerator": 60000, "fpsDenominator": 1001}) == \
        {"obs_fps_target": 59.94}
    for bad in ({}, None, {"fpsNumerator": 60, "fpsDenominator": 0},
                {"fpsNumerator": 0, "fpsDenominator": 1},
                {"fpsNumerator": "60", "fpsDenominator": 1}):
        assert m.parse_video_settings(bad) == {"obs_fps_target": None}, bad


def t_get_health_stats_carries_configured_fps():
    sess = _FakeSession({"GetStats": {"activeFps": 45.8},
                         "GetVideoSettings": {"fpsNumerator": 60, "fpsDenominator": 1}})
    orig, m._connect = m._connect, lambda *a, **k: (sess, "")
    try:
        reachable, stats, _ = m.get_health_stats()
    finally:
        m._connect = orig
    assert reachable is True
    assert (stats["obs_fps"], stats["obs_fps_target"]) == (45.8, 60.0)


def t_get_health_stats_keeps_stats_when_video_settings_fails():
    # A failing GetVideoSettings loses the fps reference, never the stats. (#586)
    class _Sess(_FakeSession):
        def request(self, request_type, request_data=None):
            if request_type == "GetVideoSettings":
                raise RuntimeError("boom")
            return super().request(request_type, request_data)
    sess = _Sess({"GetStats": {"activeFps": 60.0, "cpuUsage": 5.0}})
    orig, m._connect = m._connect, lambda *a, **k: (sess, "")
    try:
        reachable, stats, note = m.get_health_stats()
    finally:
        m._connect = orig
    assert (reachable, note) == (True, "")
    assert stats.get("obs_cpu_pct") == 5.0 and stats.get("obs_fps_target") is None
    assert ("close", {}) in sess.sent


def t_stream_kbps():
    # 125000 bytes over 1 s = 1000 kbps.
    assert m.stream_kbps(0, 100.0, 125000, 101.0, True) == 1000.0
    assert m.stream_kbps(None, None, 125000, 101.0, True) is None   # first sample
    assert m.stream_kbps(0, 100.0, 125000, 101.0, False) is None    # not streaming
    assert m.stream_kbps(200000, 100.0, 1000, 101.0, True) is None  # counter reset
    assert m.stream_kbps(0, 101.0, 125000, 101.0, True) is None     # dt == 0


def t_pov_scene_item_transform_maps_box():
    assert m.pov_scene_item_transform(
        {"left": 1516, "top": 600, "width": 384, "height": 216}) == {
            "positionX": 1516, "positionY": 600,
            "boundsType": 2, "boundsAlignment": 0, "alignment": 5,
            "boundsWidth": 384, "boundsHeight": 216}


def t_set_scene_item_transform_sends_request():
    sess = _FakeSession({"GetSceneItemId": {"sceneItemId": 7}})
    orig, m._connect = m._connect, lambda *a, **k: (sess, "")
    try:
        tf = m.pov_scene_item_transform(
            {"left": 1516, "top": 600, "width": 384, "height": 216})
        ok, note = m.set_scene_item_transform("Stint", "Feed POV", tf)
    finally:
        m._connect = orig
    assert ok is True and note == ""
    assert ("SetSceneItemTransform",
            {"sceneName": "Stint", "sceneItemId": 7,
             "sceneItemTransform": tf}) in sess.sent


def t_set_scene_item_transform_missing_item():
    sess = _FakeSession({"GetSceneItemId": {}})        # no sceneItemId
    orig, m._connect = m._connect, lambda *a, **k: (sess, "")
    try:
        ok, note = m.set_scene_item_transform("Stint", "Feed POV", {})
    finally:
        m._connect = orig
    assert ok is False and "not found" in note


def t_set_scene_item_transform_unreachable():
    orig, m._connect = m._connect, lambda *a, **k: (None, "OBS not running")
    try:
        assert m.set_scene_item_transform("Stint", "Feed POV", {}) == \
            (False, "OBS not running")
    finally:
        m._connect = orig


# set_feed_close_when_inactive tells OBS to drop off-air feeds under fan-out.
def t_set_feed_close_when_inactive_builds_setinputsettings():
    sess = _FakeSession()
    orig, m._connect = m._connect, lambda *a, **k: (sess, "")
    try:
        note = m.set_feed_close_when_inactive(["Feed A", "Feed B"], True)
    finally:
        m._connect = orig
    assert note == "", note
    reqs = [{"requestType": rt, "requestData": rd}
            for rt, rd in sess.sent if rt == "SetInputSettings"]
    assert len(reqs) == 2
    for r in reqs:
        d = r["requestData"]
        assert d["inputSettings"]["close_when_inactive"] is True
        assert d.get("overlay") is True       # merge, not replace


def t_set_feed_close_when_inactive_revert_to_false():
    """value=False reverts A/B so the direct-serve fallback is safe (no stale backlog)."""
    sess = _FakeSession()
    orig, m._connect = m._connect, lambda *a, **k: (sess, "")
    try:
        note = m.set_feed_close_when_inactive(["Feed A", "Feed B"], False)
    finally:
        m._connect = orig
    assert note == "", note
    reqs = [{"requestType": rt, "requestData": rd}
            for rt, rd in sess.sent if rt == "SetInputSettings"]
    assert len(reqs) == 2
    for r in reqs:
        d = r["requestData"]
        assert d["inputSettings"]["close_when_inactive"] is False
        assert d.get("overlay") is True       # merge, not replace


def t_set_feed_close_when_inactive_unreachable_is_note_not_crash():
    orig, m._connect = m._connect, lambda *a, **k: (None, "OBS not running")
    try:
        note = m.set_feed_close_when_inactive(["Feed A"], True)
    finally:
        m._connect = orig
    assert note == "OBS not running"


# resolve_transition, the director's transition-choice resolver.
def t_resolve_transition_by_kind_and_fallback():
    tlist = [{"transitionName": "Cut", "transitionKind": "cut_transition"},
             {"transitionName": "Fade", "transitionKind": "fade_transition"},
             {"transitionName": "My Wipe", "transitionKind": "stinger_transition"}]
    assert m.resolve_transition("cut", tlist) == ("Cut", "")
    assert m.resolve_transition("fade", tlist) == ("Fade", "")
    # A stinger resolves by kind regardless of the name.
    assert m.resolve_transition("stinger", tlist) == ("My Wipe", "")
    # Older OBS payloads carry no kind, so the name is the fallback.
    nokind = [{"transitionName": "Fade", "transitionKind": ""}]
    assert m.resolve_transition("fade", nokind) == ("Fade", "")
    # An absent stinger gives None plus a note.
    name, note = m.resolve_transition("stinger", [{"transitionName": "Cut",
                                                   "transitionKind": "cut_transition"}])
    assert name is None and "Stinger" in note


def t_set_scene_with_fade_sets_transition_then_switches():
    sess = _FakeSession(responses={"GetSceneTransitionList": {"transitions": [
        {"transitionName": "Fade", "transitionKind": "fade_transition"},
        {"transitionName": "Cut", "transitionKind": "cut_transition"}]}})
    orig, m._connect = m._connect, lambda *a, **k: (sess, "")
    try:
        ok, note = m.set_current_program_scene("Stint", transition="fade", duration_ms=500)
    finally:
        m._connect = orig
    assert ok is True and note == ""
    types = [t for t, _ in sess.sent]
    # The order is: list transitions, set transition, set duration, then switch.
    assert types.index("SetCurrentSceneTransition") < types.index("SetCurrentProgramScene")
    assert ("SetCurrentSceneTransition", {"transitionName": "Fade"}) in sess.sent
    assert ("SetCurrentSceneTransitionDuration", {"transitionDuration": 500}) in sess.sent
    assert ("SetCurrentProgramScene", {"sceneName": "Stint"}) in sess.sent


def t_set_scene_cut_sets_cut_no_duration():
    sess = _FakeSession(responses={"GetSceneTransitionList": {"transitions": [
        {"transitionName": "Cut", "transitionKind": "cut_transition"}]}})
    orig, m._connect = m._connect, lambda *a, **k: (sess, "")
    try:
        m.set_current_program_scene("Stint", transition="cut", duration_ms=500)
    finally:
        m._connect = orig
    assert ("SetCurrentSceneTransition", {"transitionName": "Cut"}) in sess.sent
    # A cut is instant, so there is no duration call.
    assert all(t != "SetCurrentSceneTransitionDuration" for t, _ in sess.sent)


def t_set_scene_stinger_absent_degrades_to_cut_with_note():
    sess = _FakeSession(responses={"GetSceneTransitionList": {"transitions": [
        {"transitionName": "Cut", "transitionKind": "cut_transition"},
        {"transitionName": "Fade", "transitionKind": "fade_transition"}]}})
    orig, m._connect = m._connect, lambda *a, **k: (sess, "")
    try:
        ok, note = m.set_current_program_scene("Stint", transition="stinger", duration_ms=300)
    finally:
        m._connect = orig
    assert ok is True and "Stinger" in note
    assert ("SetCurrentSceneTransition", {"transitionName": "Cut"}) in sess.sent   # fell back
    assert ("SetCurrentProgramScene", {"sceneName": "Stint"}) in sess.sent


def t_set_scene_no_transition_is_plain_switch():
    sess = _FakeSession()
    orig, m._connect = m._connect, lambda *a, **k: (sess, "")
    try:
        m.set_current_program_scene("Stint")
    finally:
        m._connect = orig
    types = [t for t, _ in sess.sent]
    assert "GetSceneTransitionList" not in types and "SetCurrentSceneTransition" not in types
    assert ("SetCurrentProgramScene", {"sceneName": "Stint"}) in sess.sent


# stream_service_payload, the Sheet-driven OBS stream target per Producer Part.
def t_stream_service_payload_youtube():
    # YouTube's "YouTube - RTMPS" service has no "auto" server in OBS's
    # services.json, so "auto" resolves to an empty URL and OBS rejects StartStream
    # with "Invalid Path or Connection URL". The payload must carry YouTube's
    # concrete primary ingest URL.
    d = m.stream_service_payload("youtube", "live_abc")
    assert d == {"streamServiceType": "rtmp_common",
                 "streamServiceSettings": {
                     "service": "YouTube - RTMPS",
                     "server": "rtmps://a.rtmps.youtube.com:443/live2",
                     "key": "live_abc"}}


def t_stream_service_payload_youtube_server_is_never_auto():
    # YouTube must never be sent "auto", which OBS cannot resolve, leaving an empty
    # stream URL. Twitch's plugin does resolve "auto", so only YouTube is guarded.
    assert m.stream_service_payload("youtube", "k")[
        "streamServiceSettings"]["server"] != "auto"


def t_stream_service_payload_twitch_case_insensitive():
    d = m.stream_service_payload("  Twitch ", "sk_1")
    assert d["streamServiceSettings"]["service"] == "Twitch"
    # Twitch's rtmp-common plugin resolves "auto" to the nearest ingest.
    assert d["streamServiceSettings"]["server"] == "auto"
    assert d["streamServiceSettings"]["key"] == "sk_1"


def t_stream_service_payload_unknown_platform_raises():
    try:
        m.stream_service_payload("kick", "x")
    except ValueError as exc:
        assert "kick" in str(exc)
    else:
        raise AssertionError("expected ValueError")


# set_stream_service, the guarded Sheet-driven OBS stream target.
def t_set_stream_service_applies_when_offline():
    state = {"stream_active": False}
    port, srv = _start_fake_obs(state)
    ok, note = m.set_stream_service("twitch", "sk_live", port=port,
                                    password="supersecret", timeout=5)
    assert ok and note == "", note
    assert state["service_settings"] == [
        {"streamServiceType": "rtmp_common",
         "streamServiceSettings": {"service": "Twitch", "server": "auto",
                                   "key": "sk_live"}}]
    srv.close()


def t_set_stream_service_refused_while_streaming():
    state = {"stream_active": True}
    port, srv = _start_fake_obs(state)
    ok, note = m.set_stream_service("youtube", "sk", port=port,
                                    password="supersecret", timeout=5)
    assert ok is False
    assert "streaming" in note
    assert "service_settings" not in state
    srv.close()


def t_set_stream_service_unknown_platform_is_note_not_crash():
    ok, note = m.set_stream_service("kick", "sk", port=1, password="x", timeout=0.5)
    assert ok is False
    assert "kick" in note                           # short-circuits before connect


def t_set_stream_service_unreachable_is_note_not_crash():
    sock = socket.socket(); sock.bind(("127.0.0.1", 0))
    free_port = sock.getsockname()[1]; sock.close()
    ok, note = m.set_stream_service("twitch", "sk", port=free_port,
                                    password="x", timeout=0.5)
    assert ok is False and note


# On-air-aware SPLIT audio: apply_split_audio, defined in the relay module loaded
# as "irofeeds" above. (#534)
def t_apply_split_audio_mutes_offair_unmutes_onair():
    calls = []

    class _Obs:
        def set_input_mute(self, name, muted):
            calls.append((name, muted)); return True, ""

    class _Relay:
        def live_feed(self):
            return "B"

    payload, status = irofeeds.apply_split_audio(_Relay(), _Obs())
    assert status == 200 and payload["ok"] is True and payload["live"] == "B"
    # Older boards read `unmute` as one name, and it is a list on /obs/split only.
    # (#591)
    assert payload["unmute"] == "Feed B", payload
    assert payload["mute"] == ["Feed A", "Discord Audio Capture"], payload
    assert "show" not in payload, payload
    assert ("Feed B", False) in calls          # on-air unmuted
    assert ("Feed A", True) in calls           # off-air muted
    assert ("Discord Audio Capture", True) in calls


def t_apply_split_audio_no_obs_is_503():
    class _Relay:
        def live_feed(self):
            return "A"

    payload, status = irofeeds.apply_split_audio(_Relay(), None)
    assert status == 503 and payload.get("ok") is not True


class _SplitObs:
    """Records the per-verb obs_ws calls apply_split_state makes (#591)."""

    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def set_scene_item_enabled(self, scene, source, enabled):
        self.calls.append(("item", scene, source, enabled))
        return (False, f"{source} missing") if source in self.fail else (True, "")

    def set_input_mute(self, name, muted):
        self.calls.append(("mute", name, muted))
        return (False, f"{name} missing") if name in self.fail else (True, "")


class _LiveRelay:
    def __init__(self, live):
        self.live = live

    def live_feed(self):
        return self.live


def t_apply_split_state_b_on_air():
    obs = _SplitObs()
    payload, status = irofeeds.apply_split_state(_LiveRelay("B"), obs)
    assert status == 200 and payload["ok"] is True and payload["live"] == "B", payload
    assert payload["show"] == ["Feed A", "Feed B"], payload
    assert payload["unmute"] == ["Feed B"], payload
    assert payload["mute"] == ["Feed A", "Discord Audio Capture"], payload
    assert obs.calls == [
        ("item", "Splitscreen", "Feed A", True), ("item", "Splitscreen", "Feed B", True),
        ("mute", "Feed B", False),
        ("mute", "Feed A", True), ("mute", "Discord Audio Capture", True),
    ], obs.calls


def t_apply_split_state_keeps_going_after_a_failed_step():
    # A missing Discord input must not stop the feed audio from landing, and the
    # failure is reported rather than swallowed.
    obs = _SplitObs(fail={"Feed A"})
    payload, status = irofeeds.apply_split_state(_LiveRelay("A"), obs)
    assert status == 503 and payload["ok"] is False, payload
    assert "Feed A missing" in payload["note"], payload
    assert ("mute", "Feed A", False) in obs.calls
    assert ("mute", "Feed B", True) in obs.calls
    assert ("mute", "Discord Audio Capture", True) in obs.calls


def t_apply_split_in_solo_touches_nothing():
    # Solo mode has no A/B feed on air, so live_feed() is None. Both routes must
    # answer that plainly instead of raising or muting inputs at random.
    for apply in (irofeeds.apply_split_state, irofeeds.apply_split_audio):
        obs = _SplitObs()
        payload, status = apply(_LiveRelay(None), obs)
        assert status == 409 and payload["ok"] is False, (apply.__name__, payload)
        assert payload["error"] == "no on-air feed", payload
        assert obs.calls == [], obs.calls


def t_apply_split_state_opens_the_mic_of_a_local_on_air_slot():
    class _LocalRelay(_LiveRelay):
        def obs_audio_plan(self):
            return irofeeds._OBS_WS_MODULE.feed_audio_plan({"A"}, mic=MIC)

    obs = _SplitObs()
    payload, status = irofeeds.apply_split_state(_LocalRelay("A"), obs)
    assert status == 200 and payload["unmute"] == ["Feed A", MIC], payload
    assert ("mute", MIC, False) in obs.calls and ("mute", MIC, True) not in obs.calls
    payload, _ = irofeeds.apply_split_audio(_LocalRelay("A"), _SplitObs())
    assert payload["unmute"] == "Feed A", payload   # older boards still read one name
    obs = _SplitObs()
    irofeeds.apply_split_state(_LocalRelay("B"), obs)
    assert ("mute", MIC, True) in obs.calls and ("mute", MIC, False) not in obs.calls


def t_apply_split_state_logs_a_mic_it_could_not_open():
    import logging

    class _LocalRelay(_LiveRelay):
        def obs_audio_plan(self):
            return irofeeds._OBS_WS_MODULE.feed_audio_plan({"A"}, mic=MIC)

    records = []

    class _Cap(logging.Handler):
        def emit(self, rec):
            records.append(rec)

    cap = _Cap(level=logging.WARNING)
    irofeeds.LOG.addHandler(cap)
    try:
        payload, status = irofeeds.apply_split_state(_LocalRelay("A"), _SplitObs(fail={MIC}))
    finally:
        irofeeds.LOG.removeHandler(cap)
    assert status == 503 and MIC in payload["note"], payload
    assert any(MIC in r.getMessage() and "racecast setup" in r.getMessage()
               for r in records if r.levelno == logging.WARNING), records


# The STINT A/B override, resolved on the relay like SPLIT: both the Director Panel
# and Companion call it, so there is one way a STINT press behaves.
class _StintRelay(_LiveRelay):
    def __init__(self, local=(), solo=False):
        super().__init__("A")
        self.local, self.solo = set(local), solo

    def obs_audio_plan(self):
        return irofeeds._OBS_WS_MODULE.feed_audio_plan(self.local, mic=MIC)


def t_apply_stint_state_shows_the_pick_and_mutes_the_rest():
    obs = _SplitObs()
    payload, status = irofeeds.apply_stint_state(_StintRelay(), obs, "b")
    assert status == 200 and payload["ok"] is True and payload["feed"] == "B", payload
    assert obs.calls == [
        ("item", "Stint", "Feed B", True), ("item", "Stint", "Feed A", False),
        ("mute", "Feed B", False),
        ("mute", "Feed A", True), ("mute", MIC, True), ("mute", "Discord Audio Capture", True),
    ], obs.calls
    assert payload["show"] == ["Feed B"] and payload["hide"] == ["Feed A"], payload


def t_apply_stint_state_opens_the_mic_of_a_local_pick():
    obs = _SplitObs()
    payload, _ = irofeeds.apply_stint_state(_StintRelay(local={"A"}), obs, "A")
    assert payload["unmute"] == ["Feed A", MIC], payload
    assert ("mute", MIC, False) in obs.calls and ("mute", MIC, True) not in obs.calls


def t_apply_stint_state_closes_the_mic_when_the_other_feed_is_local():
    # Moving off a local stint by hand must never leave the producer's mic open
    # on a remote commentator's stint.
    obs = _SplitObs()
    irofeeds.apply_stint_state(_StintRelay(local={"A"}), obs, "B")
    assert ("mute", MIC, True) in obs.calls and ("mute", MIC, False) not in obs.calls


def t_apply_stint_state_rejects_a_bad_feed_and_solo():
    obs = _SplitObs()
    payload, status = irofeeds.apply_stint_state(_StintRelay(), obs, "C")
    assert status == 400 and payload["ok"] is False and "feed" in payload["error"], payload
    payload, status = irofeeds.apply_stint_state(_StintRelay(solo=True), obs, "A")
    assert status == 409 and payload["error"] == "no feed pair", payload
    assert obs.calls == []
    assert irofeeds.apply_stint_state(_StintRelay(), None, "A") == ({"error": "obs unavailable"}, 503)


def t_apply_stint_state_keeps_going_and_warns_about_a_missing_mic():
    import logging
    records = []

    class _Cap(logging.Handler):
        def emit(self, rec):
            records.append(rec)

    cap = _Cap(level=logging.WARNING)
    irofeeds.LOG.addHandler(cap)
    obs = _SplitObs(fail={MIC})
    try:
        payload, status = irofeeds.apply_stint_state(_StintRelay(local={"A"}), obs, "A")
    finally:
        irofeeds.LOG.removeHandler(cap)
    assert status == 503 and MIC in payload["note"], payload
    assert ("mute", "Discord Audio Capture", True) in obs.calls   # later steps still ran
    assert any("racecast setup" in r.getMessage() for r in records), records


def t_apply_split_state_no_obs_is_503():
    payload, status = irofeeds.apply_split_state(_LiveRelay("A"), None)
    assert status == 503 and payload == {"error": "obs unavailable"}, payload


def _companion_splitscreen_buttons():
    """[(label, down-actions)] of every Companion button that cuts to Splitscreen.
    A list, not a dict, so two buttons sharing a label are both checked."""
    path = os.path.join(ROOT, "src", "companion", "racecast-buttons.companionconfig")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    found = []
    for page in cfg["pages"].values():
        for row in (page.get("controls") or {}).values():
            for btn in (row or {}).values():
                downs = ((btn.get("steps") or {}).get("0") or {}) \
                    .get("action_sets", {}).get("down", [])
                if any(a.get("definitionId") == "set_scene"
                       and a["options"]["scene"]["value"] == "Splitscreen" for a in downs):
                    found.append((btn["style"]["text"], downs))
    return found


def t_companion_split_buttons_resolve_audio_server_side():
    # Every Splitscreen button takes visibility and audio from the relay. A button
    # that names a feed itself cannot know which one is on air, nor that a stint is
    # local. (#589, #591)
    buttons = _companion_splitscreen_buttons()
    assert {"SPLIT", "Split Scene"} <= {label for label, _ in buttons}, buttons
    for label, downs in buttons:
        muted = [a["options"]["source"]["value"] for a in downs
                 if a.get("definitionId") == "set_source_mute"]
        assert muted == [], f"{label!r} hardcodes mutes {muted}"
        toggled = [a["options"]["source"]["value"] for a in downs
                   if a.get("definitionId") == "toggle_scene_item"]
        assert toggled == [], f"{label!r} hardcodes visibility {toggled}"
        urls = [a["options"]["url"]["value"] for a in downs if a.get("definitionId") == "get"]
        assert "http://127.0.0.1:8088/obs/split" in urls, (label, urls)


def t_companion_split_button_keeps_the_cut_and_race_control():
    # Only the sources moved to the relay. SPLIT still cuts to Splitscreen itself,
    # so the cut survives a relay outage, and still writes Race Control. (#591)
    [downs] = [d for label, d in _companion_splitscreen_buttons() if label == "SPLIT"]
    assert downs[0]["definitionId"] == "set_scene", downs[0]
    urls = [a["options"]["url"]["value"] for a in downs if a.get("definitionId") == "get"]
    assert urls == ["http://127.0.0.1:8088/obs/split",
                    "http://127.0.0.1:8088/setup/set/racecontrol/Driver%20Swaps"], urls


def _companion_stint_buttons():
    """{label: down-actions} of the Companion STINT A/B buttons."""
    path = os.path.join(ROOT, "src", "companion", "racecast-buttons.companionconfig")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    found = {}
    for page in cfg["pages"].values():
        for row in (page.get("controls") or {}).values():
            for btn in (row or {}).values():
                label = (btn.get("style") or {}).get("text", "")
                if label in ("STINT\nA", "STINT\nB"):
                    found[label] = ((btn.get("steps") or {}).get("0") or {}) \
                        .get("action_sets", {}).get("down", [])
    return found


def t_companion_stint_buttons_end_on_the_relay_like_the_panel():
    # The panel and Companion behave the same: a STINT press ends with the relay's
    # /obs/stint/<X> setting visibility, audio and the producer's commentary mic,
    # which only the relay can decide because only it knows a stint is local.
    # Companion keeps its direct OBS actions in front as the break-glass path for a
    # relay that cannot reach OBS, so they must set exactly what the relay sets for
    # the feeds, never touch the mic, and leave the relay call the last word.
    buttons = _companion_stint_buttons()
    assert set(buttons) == {"STINT\nA", "STINT\nB"}, buttons
    for label, downs in buttons.items():
        feed = label[-1]
        other = "B" if feed == "A" else "A"
        assert downs[0]["definitionId"] == "set_scene", downs[0]
        assert downs[0]["options"]["scene"]["value"] == "Stint", downs[0]
        direct = irofeeds._OBS_WS_MODULE.feed_state_intents(
            feed, False, extra_mute=["Discord Audio Capture"])
        got = []
        for a in downs:
            o = a.get("options", {})
            if a.get("definitionId") == "toggle_scene_item":
                got.append(("show" if o["visible"]["value"] == "true" else "hide",
                            o["source"]["value"]))
            elif a.get("definitionId") == "set_source_mute":
                got.append(("mute" if o["mute"]["value"] == "true" else "unmute",
                            o["source"]["value"]))
        assert sorted(got) == sorted(direct), (label, got)
        assert MIC not in [t for _, t in got], label
        urls = [(i, a["options"]["url"]["value"]) for i, a in enumerate(downs)
                if a.get("definitionId") == "get"]
        assert [u for _, u in urls] == [f"http://127.0.0.1:8088/obs/stint/{feed}",
                                        "http://127.0.0.1:8088/setup/clear/racecontrol"], urls
        last_direct = max(i for i, a in enumerate(downs)
                          if a.get("definitionId") in ("toggle_scene_item", "set_source_mute"))
        assert urls[0][0] > last_direct, (label, "the relay call must come last")
        assert other in {t[-1] for _, t in got}, label


class _AliveFakeSock:
    """Minimal socket stand-in for the _Session.alive checks. Distinct from
    _FakeSock above, which tracks the close() handshake calls. (#537)"""

    def __init__(self, recv_chunks=None, send_raises=False):
        self._recv = list(recv_chunks or [])
        self._send_raises = send_raises
        self.sent = []

    def sendall(self, b):
        if self._send_raises:
            raise OSError("broken pipe")
        self.sent.append(b)

    def recv(self, n):
        if self._recv:
            return self._recv.pop(0)
        return b""                       # EOF

    def settimeout(self, t):
        pass

    def close(self):
        pass


def t_session_alive_starts_true():
    s = m._Session(_AliveFakeSock(), b"")
    assert s.alive is True


def t_session_send_marks_dead_on_oserror():
    s = m._Session(_AliveFakeSock(send_raises=True), b"")
    try:
        s.send_json({"x": 1})
    except OSError:
        pass  # expected; the point is the alive assertion below
    assert s.alive is False, "send failure must flag the session dead"


def t_session_next_json_marks_dead_on_eof():
    s = m._Session(_AliveFakeSock(recv_chunks=[]), b"")   # recv -> b"" -> EOF
    try:
        s.next_json()
    except ConnectionError:
        pass  # expected; the point is the alive assertion below
    assert s.alive is False


# session= reuse: a passed-in session skips connect and close, while an omitted
# one keeps the own-connect, own-close behaviour unchanged. (#537)
class _ReuseFakeSession:
    def __init__(self, responses):
        self.responses = dict(responses)   # request_type -> responseData
        self.alive = True
        self.closed = False
        self.requests = []

    def request(self, rt, rd):
        self.requests.append((rt, rd))
        return self.responses.get(rt, {})

    def close(self):
        self.closed = True


def t_set_input_mute_uses_passed_session_no_connect_no_close():
    fs = _ReuseFakeSession({"SetInputMute": {}})
    called = {"connect": 0}
    orig = m._connect

    def _fail_if_called(*a, **k):
        called["connect"] += 1
        return None, "x"

    m._connect = _fail_if_called
    try:
        ok, note = m.set_input_mute("Feed A", True, session=fs)
    finally:
        m._connect = orig
    assert ok is True and note == "", (ok, note)
    assert called["connect"] == 0, "must NOT open its own connection"
    assert fs.closed is False, "must NOT close a session it did not open"
    assert fs.requests and fs.requests[0][0] == "SetInputMute"


def t_get_program_screenshot_uses_passed_session_no_connect_no_close():
    raw = b"JPEGDATA"
    data_uri = "data:image/jpg;base64," + base64.b64encode(raw).decode()
    fs = _ReuseFakeSession({
        "GetCurrentProgramScene": {"currentProgramSceneName": "Stint"},
        "GetSourceScreenshot": {"imageData": data_uri},
    })
    called = {"connect": 0}
    orig = m._connect

    def _fail_if_called(*a, **k):
        called["connect"] += 1
        return None, "x"

    m._connect = _fail_if_called
    try:
        data, note = m.get_program_screenshot(session=fs)
    finally:
        m._connect = orig
    assert data == raw and note == "", (data, note)
    assert called["connect"] == 0, "must NOT open its own connection"
    assert fs.closed is False, "must NOT close a session it did not open"
    assert [rt for rt, _ in fs.requests] == ["GetCurrentProgramScene", "GetSourceScreenshot"]


def t_omitting_session_still_connects_and_closes():
    # On the default path a stub _connect returns a fake session, and the function
    # must still own the connect and close it. (#537)
    fs = _ReuseFakeSession({"SetInputMute": {}})
    orig = m._connect
    m._connect = lambda *a, **k: (fs, "")
    try:
        ok, note = m.set_input_mute("Feed A", True)
    finally:
        m._connect = orig
    assert ok is True and note == ""
    assert fs.closed is True, "own-session path must close"


# _ObsConn is the persistent session holder and _PassthroughConn the
# connect-per-call passthrough used when the holder is off. (#537)
class _ConnFakeSession:
    """Distinct from the _FakeSession above, which tracks .sent for the
    set_current_program_scene family. This one models .alive, .closed and
    .requests for the _ObsConn holder tests. (#537)"""

    def __init__(self, responses=None):
        self.responses = dict(responses or {})   # request_type -> responseData
        self.alive = True
        self.closed = False
        self.requests = []

    def request(self, rt, rd=None):
        self.requests.append((rt, rd or {}))
        return self.responses.get(rt, {})

    def close(self):
        self.closed = True


def _script_conn(sessions):
    """An _ObsConn whose _connect yields the given fake sessions in order (then None)."""
    c = m._ObsConn()
    seq = list(sessions)
    def fake_connect(*a, **k):
        return (seq.pop(0), "") if seq else (None, "OBS down")
    c._connect_fn = fake_connect      # test seam
    return c


def t_obsconn_reuses_one_session():
    fs = _ConnFakeSession({"GetVersion": {}})
    c = _script_conn([fs])
    calls = {"n": 0}
    def fn(session=None):
        calls["n"] += 1
        session.request("GetVersion", {})
        return "ok", ""
    assert c.run(fn) == ("ok", "")
    assert c.run(fn) == ("ok", "")
    assert calls["n"] == 2 and len(fs.requests) == 2   # reused: one session, two calls


def t_obsconn_reconnects_and_retries_once_on_death():
    dead = _ConnFakeSession({}); dead.alive = True
    good = _ConnFakeSession({"GetVersion": {}})
    c = _script_conn([dead, good])
    def fn(session=None):
        if session is dead:
            session.alive = False          # simulate socket dying mid-call
            return None, "died"
        session.request("GetVersion", {})
        return "ok", ""
    assert c.run(fn) == ("ok", "")         # retried on the fresh (good) session
    assert dead.closed is True


def t_obsconn_no_retry_on_request_level_failure():
    fs = _ConnFakeSession({}); fs.alive = True
    c = _script_conn([fs])
    calls = {"n": 0}
    def fn(session=None):
        calls["n"] += 1
        return None, "scene not found"     # request-level failure; session stays alive
    assert c.run(fn) == (None, "scene not found")
    assert calls["n"] == 1                 # NOT retried
    assert fs.closed is False


def t_obsconn_obs_down_falls_back_to_no_session():
    c = _script_conn([])                   # _connect always returns (None, ...)
    seen = {"session": "unset"}
    def fn(session=None):
        seen["session"] = session
        return None, "obs unavailable"
    assert c.run(fn) == (None, "obs unavailable")
    assert seen["session"] is None         # called WITHOUT a session (per-call fallback)


def t_passthrough_calls_without_session():
    c = m._PassthroughConn()
    seen = {"kw": "unset"}
    def fn(x, session="MISSING"):
        seen["kw"] = session
        return x
    assert c.run(fn, 7) == 7
    assert seen["kw"] == "MISSING"         # no session kwarg injected
    c.close()                              # no-op, must not raise


class _RecordConn:
    def __init__(self, tag): self.tag, self.calls = tag, []
    def run(self, func, *a, **k):
        self.calls.append((getattr(func, "__name__", func), a, k)); return self.tag
    def close(self): self.calls.append(("closed", (), {}))


# The shipped facade is the relay's _RelayObsFacade (racecast-feeds.py), which
# late-binds the relay module's `_obs_ws` name; in this test that name is the real
# obs_ws module (irofeeds._obs_ws, exposing _ROUTED_FNS), so routed names go through
# the holders and everything else delegates straight to that module.
_RMOD = irofeeds._obs_ws


def t_route_kind_classifies_calls():
    assert m.route_kind("get_program_screenshot") == "shot"
    assert m.route_kind("get_source_screenshot") == "shot"
    assert m.route_kind("set_input_mute") == "ctrl"
    assert m.route_kind("feed_media_cursors") == "ctrl"
    assert m.route_kind("stream_kbps") is None          # a pure helper, not routed
    assert m.route_kind("STINT_SCENE") is None          # a constant, not routed


def t_relay_facade_routes_screenshot_vs_control():
    shot, ctrl = _RecordConn("shot"), _RecordConn("ctrl")
    fac = irofeeds._RelayObsFacade(shot, ctrl)
    assert fac.get_program_screenshot(width=640) == "shot"   # _SHOT_FNS -> shot conn
    assert fac.set_input_mute("Feed A", True) == "ctrl"      # other _ROUTED_FNS -> ctrl conn
    assert shot.calls[0][0] == "get_program_screenshot"
    assert ctrl.calls[0][0] == "set_input_mute"


def t_relay_facade_passes_through_non_routed_attrs():
    shot, ctrl = _RecordConn("shot"), _RecordConn("ctrl")
    fac = irofeeds._RelayObsFacade(shot, ctrl)
    assert fac.STINT_SCENE is _RMOD.STINT_SCENE          # constant pass-through
    assert fac.stream_kbps is _RMOD.stream_kbps          # pure helper pass-through (not routed)
    assert not shot.calls and not ctrl.calls


def t_relay_facade_close_closes_both():
    shot, ctrl = _RecordConn("shot"), _RecordConn("ctrl")
    irofeeds._RelayObsFacade(shot, ctrl).close()
    assert shot.calls[-1][0] == "closed" and ctrl.calls[-1][0] == "closed"


def t_relay_facade_direct_call_when_module_swapped_to_fake():
    # A fake _obs_ws has no _ROUTED_FNS, so the facade calls it directly, with no
    # session= and no holder.run. That is what keeps unit tests off a real OBS.
    shot, ctrl = _RecordConn("shot"), _RecordConn("ctrl")
    fac = irofeeds._RelayObsFacade(shot, ctrl)
    class _FakeMod:
        def set_input_mute(self, *a, **k): return ("fake", k)
    orig = irofeeds._obs_ws
    irofeeds._obs_ws = _FakeMod()
    try:
        got = fac.set_input_mute("Feed A", True)
    finally:
        irofeeds._obs_ws = orig
    assert got == ("fake", {}), got                      # direct call, NO session= injected
    assert not shot.calls and not ctrl.calls


def t_feed_media_cursors_accepts_session():
    # feed_media_cursors is in _ROUTED_FNS, so it must accept a passed session and
    # skip _connect and close. (#537)
    fs = _ConnFakeSession({"GetInputList": {"inputs": []}})
    orig = m._connect
    m._connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not connect"))
    try:
        out, note = m.feed_media_cursors(session=fs)
    finally:
        m._connect = orig
    assert out == {} and note == "" and fs.closed is False


# Device enumeration: a pure parser plus the per-OS property-name map. (#304)
def t_parse_property_items_basic():
    payload = {"propertyItems": [
        {"itemName": "FaceTime HD", "itemEnabled": True, "itemValue": "0x14000000"},
        {"itemName": "Elgato", "itemEnabled": True, "itemValue": "0x14200000"},
        {"itemName": "Disabled Dummy", "itemEnabled": False, "itemValue": ""},
    ]}
    items = m.parse_property_items(payload)
    assert items == [
        {"name": "FaceTime HD", "value": "0x14000000", "enabled": True},
        {"name": "Elgato", "value": "0x14200000", "enabled": True},
    ]  # empty-value item dropped


def t_parse_property_items_malformed():
    assert m.parse_property_items({}) == []
    assert m.parse_property_items({"propertyItems": None}) == []
    assert m.parse_property_items("garbage") == []


def t_device_property_name_per_platform():
    assert m.device_property_name("darwin") == "device"
    assert m.device_property_name("win32") == "video_device_id"
    assert m.device_property_name("linux") == "device_id"
    assert m.device_property_name("sunos5") is None


def t_device_property_name_matches_setup_assets_variants():
    # obs_ws enumeration and setup-assets localization must agree on the per-OS
    # device-id settings key, or a scanned value lands in the wrong field.
    spec_sa = importlib.util.spec_from_file_location(
        "setup_assets_x", os.path.join(ROOT, "src", "setup-assets.py"))
    sa = importlib.util.module_from_spec(spec_sa); spec_sa.loader.exec_module(sa)
    for os_key, plat in (("darwin", "darwin"), ("win", "win32"), ("linux", "linux")):
        _src_id, prop_key = sa.DEVICE_VARIANTS[os_key]
        assert m.device_property_name(plat) == prop_key, os_key


def t_device_property_name_audio_kind_per_platform():
    assert m.device_property_name("darwin", kind="audio") == "device_id"
    assert m.device_property_name("win32", kind="audio") == "device_id"
    assert m.device_property_name("linux", kind="audio") == "device_id"
    assert m.device_property_name("sunos5", kind="audio") is None


def t_device_property_name_audio_matches_setup_assets_audio_variants():
    # obs_ws enumeration and setup-assets localization must agree on the mic
    # settings key, or a scanned value lands in the wrong field. (#307)
    spec_sa = importlib.util.spec_from_file_location(
        "setup_assets_y", os.path.join(ROOT, "src", "setup-assets.py"))
    sa = importlib.util.module_from_spec(spec_sa); spec_sa.loader.exec_module(sa)
    for os_key, plat in (("darwin", "darwin"), ("win", "win32"), ("linux", "linux")):
        _src_id, prop_key = sa.AUDIO_VARIANTS[os_key]
        assert m.device_property_name(plat, kind="audio") == prop_key == "device_id", os_key


def t_pick_input_kind_finds_macos_v2_via_substring():
    # macOS reports av_capture_input_v2; the matcher is the av_capture_input substring.
    kinds = ["image_source", "av_capture_input_v2", "coreaudio_input_capture"]
    assert m.pick_input_kind(kinds, m.VIDEO_INPUT_KIND_MATCHERS) == "av_capture_input_v2"
    assert m.pick_input_kind(kinds, m.AUDIO_INPUT_KIND_MATCHERS) == "coreaudio_input_capture"


def t_pick_input_kind_honors_matcher_priority_over_list_order():
    # dshow appears before v4l2 in the list, but the matcher order is av_capture,
    # dshow, v4l2, so dshow's matcher outranks v4l2's whatever the list position.
    kinds = ["v4l2_input", "dshow_input"]
    assert m.pick_input_kind(kinds, m.VIDEO_INPUT_KIND_MATCHERS) == "dshow_input"


def t_pick_input_kind_none_when_no_match_or_bad_input():
    assert m.pick_input_kind(["image_source", "color_source"], m.VIDEO_INPUT_KIND_MATCHERS) is None
    assert m.pick_input_kind([], m.VIDEO_INPUT_KIND_MATCHERS) is None
    assert m.pick_input_kind(None, m.VIDEO_INPUT_KIND_MATCHERS) is None


_VIDEO_ITEMS = [{"itemName": "FaceTime HD Camera", "itemValue": "0x1", "itemEnabled": True},
                {"itemName": "Elgato Cam Link", "itemValue": "0x2", "itemEnabled": True}]
_MIC_ITEMS = [{"itemName": "MacBook Air Microphone", "itemValue": "mic-uid", "itemEnabled": True}]


def t_probe_device_options_lists_video_and_mic_and_cleans_up():
    # darwin properties: video "device", audio "device_id".
    state = {"prop_items": {"device": _VIDEO_ITEMS, "device_id": _MIC_ITEMS}}
    port, srv = _start_fake_obs(state)
    try:
        out = m.probe_device_options(port=port, password="supersecret", timeout=5)
    finally:
        srv.close()
    # The value-carrying assertions rely on the probe running on macOS, where those
    # device property names apply. The lifecycle assertions below do not.
    assert isinstance(out["devices"], list) and isinstance(out["mic"], list)
    # A throwaway scene is created and then removed. The pre-clear RemoveScene
    # before CreateScene and the finally block's own RemoveScene both fire, which
    # is exactly two removals.
    assert m.PROBE_SCENE_NAME in state.get("created_scenes", [])
    assert state.get("removed_scenes", []).count(m.PROBE_SCENE_NAME) == 2
    # Every temp input created was also removed.
    created = [n for (_s, n, _k, _e) in state.get("created_inputs", [])]
    assert created, "expected at least one temp input"
    assert sorted(created) == sorted(state.get("removed_inputs", []))
    # Temp inputs were created DISABLED (never enter program output).
    assert all(enabled is False for (_s, _n, _k, enabled) in state["created_inputs"])


def t_probe_device_options_cleans_up_even_when_read_raises():
    # The property read fails mid-probe; the temp scene + input must STILL be removed.
    state = {"prop_raises": True,
             "prop_items": {"device": _VIDEO_ITEMS, "device_id": _MIC_ITEMS}}
    port, srv = _start_fake_obs(state)
    try:
        out = m.probe_device_options(port=port, password="supersecret", timeout=5)
    finally:
        srv.close()
    assert out["devices"] == [] and out["note"]      # degraded, note explains
    # Both the pre-clear RemoveScene and the finally block's RemoveScene fired.
    assert state.get("removed_scenes", []).count(m.PROBE_SCENE_NAME) == 2
    created = [n for (_s, n, _k, _e) in state.get("created_inputs", [])]
    assert sorted(created) == sorted(state.get("removed_inputs", []))


def t_probe_device_options_removes_input_even_if_create_response_fails():
    # OBS creates the input but the CreateInput response fails mid-exchange. The
    # probe tracks the input for cleanup before calling CreateInput, so the finally
    # block still removes every input OBS actually created.
    state = {"create_raises": True,
             "prop_items": {"device": _VIDEO_ITEMS, "device_id": _MIC_ITEMS}}
    port, srv = _start_fake_obs(state)
    try:
        out = m.probe_device_options(port=port, password="supersecret", timeout=5)
    finally:
        srv.close()
    assert out["devices"] == [] and out["note"]      # degraded (create failed)
    created = [n for (_s, n, _k, _e) in state.get("created_inputs", [])]
    assert created, "OBS should have recorded the create attempt"
    assert sorted(created) == sorted(state.get("removed_inputs", []))


def t_probe_device_options_no_capture_kind_is_note_not_crash():
    # With no capture kinds at all the result is empty lists plus explanatory notes,
    # no crash, and the scene is still created and removed.
    state = {"input_kinds": ["image_source", "color_source"], "prop_items": {}}
    port, srv = _start_fake_obs(state)
    try:
        out = m.probe_device_options(port=port, password="supersecret", timeout=5)
    finally:
        srv.close()
    assert out["devices"] == [] and out["note"]
    assert out["mic"] == [] and out["mic_note"]
    assert state.get("created_inputs", []) == []
    assert state.get("removed_scenes", []).count(m.PROBE_SCENE_NAME) == 2


def t_probe_device_options_unreachable_is_quiet():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    free_port = sock.getsockname()[1]
    sock.close()
    out = m.probe_device_options(port=free_port, password="x", timeout=0.5)
    assert out["devices"] == [] and out["mic"] == []
    assert out["note"] and out["mic_note"]           # both carry the connect reason


# readiness

def t_is_not_ready_recognises_the_207_note():
    """obs-websocket answers 207 between accepting the socket and being able to
    serve. That window silently swallows the scene-collection check, the page
    refresh and the Standby switch on a just-launched OBS."""
    note = ("GetSceneCollectionList failed: {'code': 207, 'comment': "
            "'OBS is not ready to perform the request.', 'result': False}")
    assert m.is_not_ready(note)
    assert not m.is_not_ready("OBS WebSocket not reachable on 127.0.0.1:4455")
    assert not m.is_not_ready("")
    assert not m.is_not_ready(None)


def t_wait_until_ready_polls_until_obs_answers():
    calls = []

    def _probe(host, port, password):
        calls.append(1)
        return (len(calls) >= 3), "not ready yet"

    ticks = iter([0, 1, 2, 3, 4, 5, 6])
    ok, note = m.wait_until_ready(timeout=10, interval=0, probe=_probe,
                                  clock=lambda: next(ticks), sleep=lambda _s: None)
    assert ok and note == "", note
    assert len(calls) == 3, calls


def t_wait_until_ready_gives_up_and_says_why():
    ticks = iter([0, 5, 11])
    ok, note = m.wait_until_ready(timeout=10, interval=0,
                                  probe=lambda h, p, w: (False, "still loading"),
                                  clock=lambda: next(ticks), sleep=lambda _s: None)
    assert not ok and note == "still loading", note


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

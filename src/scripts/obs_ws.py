#!/usr/bin/env python3
"""Minimal obs-websocket v5 client (stdlib only, all platforms).

OBS's media sources hold an HTTP connection to the relay feeds (ports
53001-53003). When a feed source is not in the active scene OBS stops draining
the socket, so killing the relay leaves an orphaned kernel socket stuck in
FIN_WAIT_1 with the port still bound, and preflight then warns "port in use".
`release_feed_inputs()` makes OBS drop exactly those connections AFTER the feeds
were killed, so the ports tear down cleanly.

Re-applying an input's own settings, an unchanged `SetInputSettings`, is the one
request that forces OBS to rebuild the ffmpeg source and close its socket. Media
STOP and RESTART actions are ignored for sources outside the active scene. The
rebuild must happen after the feed is dead: against a live relay an active source
would simply reconnect. The sources keep `restart_on_activate`, so they come back
on the next scene activation after a relay restart.

Everything is best effort and the entry point never raises: a stop must never hang
or crash because OBS is closed, locked, or speaks a newer protocol.

The WebSocket password is auto-discovered from OBS's own obs-websocket
config.json on the same machine and user; `RACECAST_OBS_WS_PASSWORD` in the
environment or .env overrides it for a non-standard setup.
"""
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time
import urllib.parse

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"   # RFC 6455 magic
DEFAULT_PORT = 4455
RELAY_PORTS = (53001, 53002, 53003)
CLOSE_DRAIN_TIMEOUT_S = 1.0   # max seconds to wait for OBS's close echo before closing the socket

STINT_SCENE = "Stint"                       # single-cam scene holding both feeds
INTERMISSION_SCENE = "Intermission"          # the safe holding scene and auto-failover target (#371, #378)
POV_SOURCE = "Feed POV"                      # the Stint-scene driver-POV PiP scene item
FEED_SOURCES = {"A": "Feed A", "B": "Feed B"}   # scene-item name == audio input name
SPLIT_SCENE = "Splitscreen"                  # the handover layout: outgoing + incoming stint
SPLIT_DISCORD_INPUT = "Discord Audio Capture"   # the interview bus, muted during a SPLIT (#534)
# The producer's own commentary microphone for a local stint (#593). Always the
# LEAF input, never a scene, because a scene wrapper cannot be muted.
COMMENTARY_MIC_INPUT = "Commentary Mic Device"

# The scene collection the broadcast assumes. Mirrors the "name" field of
# src/obs/GT_Racing_Endurance.json, the name OBS shows after importing the
# localized collection, so keep the two in sync. It is not parsed at runtime
# because the file is renamed and tokenized in the shipped package and bundled
# differently when frozen.
EXPECTED_SCENE_COLLECTION = "GT Racing Endurance"


def scene_collection_status(current, available, expected=EXPECTED_SCENE_COLLECTION):
    """Classify the active OBS scene collection. `current` is OBS's
    currentSceneCollectionName and `available` is the full list it reported.
    The only correct state is match=True. renamed_variant flags a non-exact
    "GT Racing Endurance*", such as an import-renamed 'GT Racing Endurance 2',
    which is never switched to automatically."""
    available = list(available)
    # A correct collection wins: never flag a renamed variant when we already match.
    renamed = None if current == expected else next(
        (n for n in available
         if n != expected and isinstance(n, str) and n.startswith(expected)), None)
    return {"current": current, "expected": expected, "available": available,
            "match": current == expected,
            "expected_present": expected in available,
            "renamed_variant": renamed}


def scene_collection_action(status, note, switch_enabled):
    """Decide what `event start` should do about the OBS scene collection.
    `status` and `note` are a get_scene_collection() result; `switch_enabled` is the
    RACECAST_OBS_COLLECTION_SWITCH gate. Returns (action, detail):
      ("skip", note)            OBS unreachable or no status: print the note
      ("ok", current)           already on the expected collection
      ("switch", expected)      mismatch, expected present, switch on
      ("warn_present", status)  mismatch, expected present, switch off
      ("warn_absent", status)   mismatch, expected not imported, including a
                                renamed-only variant, which is never auto-selected
    The executor requests the switch with the exact expected name, and
    set_scene_collection re-checks presence, so a renamed variant can never be
    selected."""
    if status is None:
        return ("skip", note)
    if status["match"]:
        return ("ok", status["current"])
    if not status["expected_present"]:
        return ("warn_absent", status)
    if not switch_enabled:
        return ("warn_present", status)
    return ("switch", status["expected"])


def feed_audio_plan(local_feeds, mic=None):
    """(audio, extra_mute) for the intent planners below (#593). `audio` maps A and
    B to that slot's audio inputs: its media source, plus `mic` when the slot carries
    the local capture. `extra_mute` lists `mic` again so it is muted even when no
    slot is local any more, since the outgoing local stint may already have advanced
    to a remote row when a handover lands. mic=None leaves the mic alone, so a
    machine without a capture card never touches it."""
    audio = {f: [src] + ([mic] if mic and f in local_feeds else [])
             for f, src in FEED_SOURCES.items()}
    return audio, ([mic] if mic else [])


def _audio_intents(live_inputs, other_inputs):
    """Unmute the on-air inputs, then mute every other input once, never one that
    was just unmuted: two back-to-back local stints share the one microphone."""
    intents = [("unmute", i) for i in live_inputs]
    for i in other_inputs:
        if i not in live_inputs and ("mute", i) not in intents:
            intents.append(("mute", i))
    return intents


def feed_state_intents(live, do_cut, feeds=("A", "B"),
                       scene=STINT_SCENE, sources=None, audio=None, extra_mute=()):
    """The OBS intent list that makes `live`, A or B, the on-air feed in the Stint
    scene. Visibility first, then audio, then the program cut when do_cut is set.
    `audio` and `extra_mute` come from feed_audio_plan(); the default is one audio
    input per feed, named like its scene item.
    reflect_feed_state() turns each (verb, target) into obs-websocket requests."""
    sources = sources or FEED_SOURCES
    audio = audio or {f: [sources[f]] for f in feeds}
    others = [f for f in feeds if f != live]
    intents = [("show", sources[live])] + [("hide", sources[f]) for f in others]
    intents += _audio_intents(audio[live],
                              [i for f in others for i in audio[f]] + list(extra_mute))
    if do_cut:
        intents.append(("cut", scene))
    return intents


def split_state_intents(live, do_cut, slots=None, scene=SPLIT_SCENE, extra_mute=()):
    """The OBS intent list for the Splitscreen with `live`, A or B, on air (#591).
    Both slots are visible; the on-air slot's audio inputs are unmuted, while the
    off-air slot's, `extra_mute` and the Discord bus are muted; the program cut
    comes last when do_cut is set. `slots` maps A and B to (scene-item name, [audio
    input names]), a list because a local slot contributes its media source plus the
    commentary microphone (#593). The default derives both slots from FEED_SOURCES
    with one audio input each."""
    slots = slots or {f: (src, [src]) for f, src in FEED_SOURCES.items()}
    others = [f for f in slots if f != live]
    intents = [("show", slots[f][0]) for f in slots]
    intents += _audio_intents(slots[live][1],
                              [i for f in others for i in slots[f][1]]
                              + list(extra_mute) + [SPLIT_DISCORD_INPUT])
    if do_cut:
        intents.append(("cut", scene))
    return intents


def pov_scene_item_transform(box):
    """Map a full POV box {left,top,width,height} to an obs-websocket
    sceneItemTransform. The Feed POV item is top-left anchored (alignment 5) with
    SCALE_INNER bounds (boundsType 2). Every field is sent explicitly so the result
    is idempotent whatever the item's current bounds settings are."""
    return {"positionX": box["left"], "positionY": box["top"],
            "boundsType": 2, "boundsAlignment": 0, "alignment": 5,
            "boundsWidth": box["width"], "boundsHeight": box["height"]}


# The RFC 6455 WebSocket plumbing below is pure and unit-tested.
def accept_key(key):
    """Server's expected Sec-WebSocket-Accept for our Sec-WebSocket-Key."""
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()


def handshake_request(host, port, key):
    """The HTTP Upgrade request opening the WebSocket."""
    return (f"GET / HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n").encode()


def parse_handshake(response, key):
    """Validate the 101 response and return any bytes past the headers. OBS sends
    its Hello immediately, so the first frame may ride in with the response."""
    head, sep, rest = response.partition(b"\r\n\r\n")
    if not sep:
        raise ValueError("incomplete WebSocket handshake response")
    lines = head.decode("iso-8859-1").split("\r\n")
    if " 101 " not in lines[0] + " ":
        raise ValueError(f"WebSocket upgrade refused: {lines[0]}")
    accept = None
    for line in lines[1:]:
        name, _, value = line.partition(":")
        if name.strip().lower() == "sec-websocket-accept":
            accept = value.strip()
    if accept != accept_key(key):
        raise ValueError("WebSocket handshake: Sec-WebSocket-Accept mismatch")
    return rest


def encode_frame(payload, mask=None, opcode=0x1):
    """One masked client->server frame (RFC 6455 requires clients to mask)."""
    if mask is None:
        mask = os.urandom(4)
    length = len(payload)
    if length < 126:
        head = bytes([0x80 | opcode, 0x80 | length])
    elif length < 1 << 16:
        head = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack(">H", length)
    else:
        head = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack(">Q", length)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return head + mask + masked


def decode_frame(buf):
    """Parse one frame from buf. Returns (opcode, payload, rest) or None if
    buf does not yet hold a complete frame. Handles masked frames too."""
    if len(buf) < 2:
        return None
    opcode = buf[0] & 0x0F
    masked = bool(buf[1] & 0x80)
    length = buf[1] & 0x7F
    pos = 2
    if length == 126:
        if len(buf) < pos + 2:
            return None
        length = struct.unpack(">H", buf[pos:pos + 2])[0]
        pos += 2
    elif length == 127:
        if len(buf) < pos + 8:
            return None
        length = struct.unpack(">Q", buf[pos:pos + 8])[0]
        pos += 8
    mask = b""
    if masked:
        if len(buf) < pos + 4:
            return None
        mask = buf[pos:pos + 4]
        pos += 4
    if len(buf) < pos + length:
        return None
    payload = buf[pos:pos + length]
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload, buf[pos + length:]


# The obs-websocket v5 protocol helpers below are pure and unit-tested.
def auth_token(password, salt, challenge):
    """The documented v5 answer: base64(sha256(base64(sha256(pw+salt)) + challenge))."""
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


def identify_payload(hello, password):
    """Build the Identify (op 1) for a received Hello (op 0).
    Raises ValueError when OBS requires auth and we have no password."""
    d = {"rpcVersion": 1, "eventSubscriptions": 0}   # requests only
    auth = hello.get("d", {}).get("authentication")
    if auth:
        if not password:
            raise ValueError("OBS WebSocket requires a password "
                             "(set RACECAST_OBS_WS_PASSWORD or enable auto-discovery)")
        d["authentication"] = auth_token(password, auth["salt"], auth["challenge"])
    return {"op": 1, "d": d}


def feed_input_names(inputs, get_settings, ports=RELAY_PORTS):
    """Which media inputs hold connections to the relay feed ports. Matches ffmpeg
    sources whose network URL points at localhost:<feed port>; local files and
    other URLs are left alone."""
    wanted = set()
    for port in ports:
        wanted.add(f"127.0.0.1:{port}")
        wanted.add(f"localhost:{port}")
    names = []
    for inp in inputs:
        if inp.get("inputKind") != "ffmpeg_source":
            continue
        name = inp.get("inputName")
        try:
            settings = get_settings(name) or {}
        except Exception:                            # one bad input must not stop the rest
            continue
        if settings.get("is_local_file"):
            continue
        url = settings.get("input")
        if isinstance(url, str) and urllib.parse.urlsplit(url.strip()).netloc in wanted:
            names.append(name)
    return names


def browser_input_names(inputs, get_settings, needle="127.0.0.1:8088"):
    """Which browser sources show relay-served pages such as the HUD. Matches by
    URL substring so any future relay page is covered without a name list;
    local-file pages and other URLs are left alone."""
    names = []
    for inp in inputs:
        if inp.get("inputKind") != "browser_source":
            continue
        name = inp.get("inputName")
        try:
            settings = get_settings(name) or {}
        except Exception:                            # one bad input must not stop the rest
            continue
        url = settings.get("url")
        if isinstance(url, str) and needle in url:
            names.append(name)
    return names


def screenshot_request_data(source_name, width=640, fmt="jpg", quality=60):
    """requestData for GetSourceScreenshot: a scaled still of a source or scene."""
    return {"sourceName": source_name, "imageFormat": fmt,
            "imageWidth": int(width), "imageCompressionQuality": int(quality)}


def parse_screenshot_data_uri(data_uri):
    """Decode a GetSourceScreenshot 'imageData' value, a
    data:image/<fmt>;base64,<payload> URI, to raw bytes. None when it is
    malformed."""
    if not isinstance(data_uri, str):
        return None
    head, sep, payload = data_uri.partition(",")
    if not sep or not head.startswith("data:") or "base64" not in head:
        return None
    try:
        return base64.b64decode(payload, validate=True)   # binascii.Error subclasses ValueError
    except ValueError:
        return None


# Password and port discovery from OBS's own obs-websocket config.
def obs_config_path(platform, env, home):
    """Per-OS location of obs-websocket's config.json. The separators are explicit
    so the function gives identical answers on every host OS."""
    if platform == "darwin":
        return home + "/Library/Application Support/obs-studio/plugin_config/obs-websocket/config.json"
    if platform.startswith("win"):
        base = env.get("APPDATA") or home + "\\AppData\\Roaming"
        return base + "\\obs-studio\\plugin_config\\obs-websocket\\config.json"
    return home + "/.config/obs-studio/plugin_config/obs-websocket/config.json"


def read_ws_config(path):
    """obs-websocket's config.json as a small dict, or None if unreadable."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return {"password": data.get("server_password"),
            "port": data.get("server_port", DEFAULT_PORT),
            "auth_required": data.get("auth_required", True),
            "enabled": data.get("server_enabled", True)}


def default_config_path():
    return obs_config_path(sys.platform, os.environ, os.path.expanduser("~"))


def find_password(env, config_path):
    """RACECAST_OBS_WS_PASSWORD wins, else OBS's own stored server password."""
    override = env.get("RACECAST_OBS_WS_PASSWORD")
    if override:
        return override
    cfg = read_ws_config(config_path)
    return cfg["password"] if cfg else None


class _Session:
    """One identified obs-websocket connection. request() is synchronous."""

    def __init__(self, sock, buf):
        self.sock = sock
        self.buf = buf
        self.counter = 0
        self.alive = True

    def next_json(self):
        """Next text message as JSON. Answers pings, raises on close or EOF."""
        while True:
            frame = decode_frame(self.buf)
            if frame is None:
                try:
                    chunk = self.sock.recv(65536)
                except OSError:
                    self.alive = False
                    raise
                if not chunk:
                    self.alive = False
                    raise ConnectionError("OBS closed the connection")
                self.buf += chunk
                continue
            opcode, payload, self.buf = frame
            if opcode == 0x9:                       # answer a ping with a pong
                self.sock.sendall(encode_frame(payload, opcode=0xA))
            elif opcode == 0x8:
                self.alive = False
                raise ConnectionError("OBS closed the connection")
            elif opcode == 0x1:
                return json.loads(payload)

    def send_json(self, obj):
        try:
            self.sock.sendall(encode_frame(json.dumps(obj).encode()))
        except OSError:
            self.alive = False
            raise

    def request(self, request_type, request_data):
        self.counter += 1
        rid = f"racecast-{self.counter}"
        self.send_json({"op": 6, "d": {"requestType": request_type,
                                       "requestId": rid,
                                       "requestData": request_data}})
        while True:
            msg = self.next_json()
            if msg.get("op") == 7 and msg["d"].get("requestId") == rid:
                if not msg["d"].get("requestStatus", {}).get("result"):
                    raise ValueError(f"{request_type} failed: "
                                     f"{msg['d'].get('requestStatus')}")
                return msg["d"].get("responseData", {})

    def close(self):
        """Best-effort RFC 6455 closing handshake so OBS logs a clean 1000 close
        instead of an abnormal 1006: send a status-1000 close frame, then briefly
        read OBS's close echo, bounded by CLOSE_DRAIN_TIMEOUT_S, so OBS can finish
        its side before the socket goes away, then close it.

        It deliberately does NOT shutdown(SHUT_WR): a TCP FIN right after the close
        frame makes OBS's WebSocket server log the disconnect as 1006 "End of File"
        instead of 1000. Letting the server send its close echo and close the TCP
        first, which the read to EOF does, yields a clean 1000. Never raises and
        never blocks past the drain timeout."""
        try:
            self.sock.sendall(encode_frame(struct.pack(">H", 1000), opcode=0x8))
        except OSError:
            pass  # OBS may have dropped the socket first; the rest is courtesy
        try:
            self.sock.settimeout(CLOSE_DRAIN_TIMEOUT_S)
            while self.sock.recv(65536):   # drain OBS's close echo until EOF
                pass
        except OSError:
            pass  # timeout or reset: stop draining and close
        self.sock.close()


def _open_session(host, port, password, timeout):
    """Connect, upgrade to WebSocket and run the obs-websocket identify. Returns an
    identified _Session and raises on any failure; callers translate that into
    their best-effort (names, note) contract."""
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall(handshake_request(host, port, key))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(65536)
            if not chunk:
                raise ConnectionError("OBS closed during the handshake")
            response += chunk
        session = _Session(sock, parse_handshake(response, key))
        hello = session.next_json()
        session.send_json(identify_payload(hello, password))
        identified = session.next_json()
        if identified.get("op") != 2:
            raise ValueError("OBS WebSocket identify failed")
        return session
    except Exception:
        sock.close()
        raise


def resolve_obs_target(host, port, env, cfg):
    """Where to reach OBS. RACECAST_OBS_WS_HOST and RACECAST_OBS_WS_PORT override
    everything, a test and proxy seam that points the relay at a simulated OBS or at
    OBS on another host. Otherwise it is the host the caller passed plus OBS's own
    config port, falling back to the 4455 default."""
    host = env.get("RACECAST_OBS_WS_HOST") or host
    if port is None:
        p = (env.get("RACECAST_OBS_WS_PORT") or "").strip()
        port = int(p) if p.isdigit() else ((cfg or {}).get("port") or DEFAULT_PORT)
    return host, port


def _connect(host, port, password, timeout):
    """(session, "") or (None, reason). Host, port and password fall back to the
    RACECAST_OBS_WS_* overrides, then OBS's own obs-websocket config. Never
    raises."""
    cfg = read_ws_config(default_config_path())
    host, port = resolve_obs_target(host, port, os.environ, cfg)
    if password is None:
        password = find_password(os.environ, default_config_path())
    try:
        return _open_session(host, port, password, timeout), ""
    except OSError:
        return None, f"OBS WebSocket not reachable on {host}:{port} (OBS not running?)"
    except Exception as exc:                         # noqa: BLE001  best-effort contract
        return None, str(exc) or exc.__class__.__name__


class _ObsConn:
    """One persistent, lock-guarded obs-websocket session, reused across calls and
    reconnecting with one retry (#537). Best-effort: never raises."""

    def __init__(self, host="127.0.0.1", port=None, password=None, timeout=2.0):
        self.host, self.port, self.password, self.timeout = host, port, password, timeout
        self._lock = threading.Lock()
        self._session = None
        self._connect_fn = _connect        # test seam

    def _drop(self):
        s, self._session = self._session, None
        if s is not None:
            s.close()

    def _ensure(self):
        if self._session is not None and not self._session.alive:
            self._drop()
        if self._session is None:
            self._session, _ = self._connect_fn(self.host, self.port, self.password, self.timeout)
        return self._session

    def run(self, func, *args, **kwargs):
        with self._lock:
            result = None
            for attempt in (0, 1):
                sess = self._ensure()
                if sess is None:
                    return func(*args, **kwargs)      # OBS down: per-call path, clean note
                result = func(*args, session=sess, **kwargs)
                if sess.alive:
                    return result                     # success OR request-level failure
                self._drop()                          # the socket died during the call
                if attempt == 1:
                    return result                     # already retried once
            return result

    def close(self):
        with self._lock:
            self._drop()


class _PassthroughConn:
    """Kill-switch off: connect per call, no session reuse (#537)."""
    def run(self, func, *args, **kwargs):
        return func(*args, **kwargs)
    def close(self):
        pass


# The two routing registries the relay's _RelayObsFacade reads off this module, by
# getattr, to decide which calls go through a persistent _ObsConn and which
# connection carries them (#537). _SHOT_FNS ride the dedicated screenshot
# connection, the rest of _ROUTED_FNS ride the shared control connection, and
# everything else, the constants and pure helpers, is a direct module call.
_SHOT_FNS = frozenset({"get_program_screenshot", "get_source_screenshot"})
_ROUTED_FNS = _SHOT_FNS | frozenset({
    "read_obs_state", "get_health_stats", "get_current_program_scene",
    "set_current_program_scene", "switch_to_scene_if_idle", "set_scene_item_enabled",
    "set_scene_item_transform", "set_input_volume", "set_input_mute", "set_stream",
    "set_stream_service", "reflect_feed_state", "refresh_browser_inputs",
    "release_feed_inputs", "feed_media_cursors", "get_scene_collection",
    "set_scene_collection",
})


def route_kind(name):
    """Which persistent connection carries the obs_ws call `name` (#537): 'shot'
    for the dedicated screenshot connection, 'ctrl' for the shared control
    connection, or None when it is not routed and is called directly. The relay's
    _RelayObsFacade consults this on the real module to place each call, so the
    policy sits next to the _SHOT_FNS and _ROUTED_FNS registries."""
    if name not in _ROUTED_FNS:
        return None
    return "shot" if name in _SHOT_FNS else "ctrl"


def _pct(part, total):
    """skipped/total as a rounded percentage, or None when either is missing or
    total is zero, which avoids a division by zero and a meaningless 0/0."""
    if part is None or not total:
        return None
    return round(part / total * 100.0, 2)


def stream_kbps(prev_bytes, prev_ts, bytes_, ts, active):
    """Upstream kbps from successive outputBytes samples. None resets the line on a
    stream stop or restart, so no ghost spike appears."""
    if not active or bytes_ is None or prev_bytes is None or prev_ts is None:
        return None
    dt = ts - prev_ts
    if dt <= 0 or bytes_ < prev_bytes:
        return None
    return round((bytes_ - prev_bytes) * 8 / 1000.0 / dt, 1)


def parse_obs_stats(payload):
    """Flatten a GetStats response into the health field names. A missing key gives
    None."""
    p = payload or {}
    return {
        "obs_cpu_pct": p.get("cpuUsage"),
        "obs_mem_mb": p.get("memoryUsage"),
        "obs_disk_free_mb": p.get("availableDiskSpace"),
        "obs_fps": p.get("activeFps"),
        "obs_render_skipped_pct": _pct(p.get("renderSkippedFrames"),
                                       p.get("renderTotalFrames")),
        # Raw cumulative counts: the relay derives the per-interval render-skip RATE
        # for the health chart from successive samples, because the cumulative
        # percentage barely moves during a spike. A diagnostic, never a trigger (#582).
        "obs_render_skipped_frames": p.get("renderSkippedFrames"),
        "obs_render_total_frames": p.get("renderTotalFrames"),
    }


def parse_video_settings(payload):
    """OBS's configured frame rate from a GetVideoSettings response (#586), the
    reference the measured activeFps is judged against. It divides fpsNumerator by
    fpsDenominator, so 60000/1001 gives 59.94. Missing or non-positive gives
    None."""
    p = payload or {}
    num, den = p.get("fpsNumerator"), p.get("fpsDenominator")
    if not isinstance(num, (int, float)) or not isinstance(den, (int, float)) \
            or num <= 0 or den <= 0:
        return {"obs_fps_target": None}
    return {"obs_fps_target": round(num / den, 3)}


def parse_stream_status(payload):
    """Flatten a GetStreamStatus response. outputBytes is returned raw, since the
    caller derives kbps from successive samples. A missing key gives None."""
    p = payload or {}
    active = p.get("outputActive")
    recon = p.get("outputReconnecting")
    return {
        "stream_active": None if active is None else bool(active),
        "stream_reconnecting": None if recon is None else bool(recon),
        "stream_timecode": p.get("outputTimecode"),
        "stream_congestion": p.get("outputCongestion"),
        "stream_dropped_pct": _pct(p.get("outputSkippedFrames"),
                                   p.get("outputTotalFrames")),
        "output_bytes": p.get("outputBytes"),
    }


# A single-channel event maps to an OBS rtmp_common service and server. The
# platform values come from the Sheet `Channel` tab, lowercased.
#
# The `server` matters. YouTube's "YouTube - RTMPS" service has NO "auto" server in
# OBS's services.json, only concrete ingest URLs, and unlike Twitch it has no
# ingest-auto-select plugin path. Sending "auto" for YouTube makes OBS resolve an
# EMPTY stream URL and reject StartStream with "Invalid Path or Connection URL"
# (OBS_OUTPUT_BAD_PATH), so YouTube needs its concrete primary ingest URL. Twitch's
# rtmp-common plugin DOES resolve "auto" to the nearest ingest, so "auto" is both
# correct and region-agnostic there.
OBS_STREAM_SERVICES = {
    "youtube": {"service": "YouTube - RTMPS",
                "server": "rtmps://a.rtmps.youtube.com:443/live2"},
    "twitch": {"service": "Twitch", "server": "auto"},
}


def stream_service_payload(platform, key):
    """Build SetStreamServiceSettings request data for a single-channel event.
    `platform` is the case-insensitive Channel-tab value, 'youtube' or 'twitch'; an
    unknown one raises ValueError, which the caller turns into a producer-facing
    note rather than a crash. The key is passed through verbatim and never logged."""
    svc = OBS_STREAM_SERVICES.get((platform or "").strip().lower())
    if not svc:
        raise ValueError(f"unknown stream platform: {platform!r}")
    return {"streamServiceType": "rtmp_common",
            "streamServiceSettings": {"service": svc["service"],
                                      "server": svc["server"], "key": key}}


def get_health_stats(host="127.0.0.1", port=None, password=None, timeout=2.0, session=None):
    """One obs-websocket session to (reachable, stats, note). `stats` merges
    parse_obs_stats, parse_stream_status and parse_video_settings, and is empty when
    the stats requests fail but the session opened. Best-effort: never raises."""
    note = ""
    own = session is None
    if own:
        session, note = _connect(host, port, password, timeout)
    if session is None:
        return False, {}, note
    try:
        try:
            stats = parse_obs_stats(session.request("GetStats", {}))
            stats.update(parse_stream_status(session.request("GetStreamStatus", {})))
        except Exception as exc:                     # noqa: BLE001  best-effort contract
            return True, {}, str(exc) or exc.__class__.__name__
        try:
            # Losing the fps reference must never lose the stats above (#586).
            stats.update(parse_video_settings(session.request("GetVideoSettings", {})))
        except Exception:                            # noqa: BLE001  best-effort contract
            stats["obs_fps_target"] = None
        return True, stats, ""
    finally:
        if own:
            session.close()


def probe(host="127.0.0.1", port=None, password=None, timeout=2.0):
    """Lightweight OBS reachability check used by the relay's /status: open an
    obs-websocket session, handshake and auth, and close it at once, touching
    nothing in OBS. Returns (False, reason) when OBS is closed, locked or
    mis-keyed, and (True, "") on a full identify. Never raises."""
    session, note = _connect(host, port, password, timeout)
    if session is None:
        return False, note
    session.close()
    return True, ""


DEVICE_PROPERTY_NAMES = {"darwin": "device", "win": "video_device_id", "linux": "device_id"}
# The audio device property is "device_id" on every OS. It MUST match
# setup-assets.AUDIO_VARIANTS, which a test cross-checks, because enumeration writes
# into the same field localization later reads.
AUDIO_DEVICE_PROPERTY_NAMES = {"darwin": "device_id", "win": "device_id", "linux": "device_id"}


def device_property_name(platform, kind="video"):
    """OBS input-settings property key holding the device id for `platform`, or
    None if unknown. The default kind="video" MUST match
    setup-assets.DEVICE_VARIANTS and kind="audio" MUST match
    setup-assets.AUDIO_VARIANTS, both cross-checked by a test, because enumeration
    writes into the same field localization later reads."""
    names = AUDIO_DEVICE_PROPERTY_NAMES if kind == "audio" else DEVICE_PROPERTY_NAMES
    if platform.startswith("win"):
        return names["win"]
    if platform == "darwin":
        return names["darwin"]
    if platform.startswith("linux"):
        return names["linux"]
    return None


# Platform capture-input kinds, as OBS reports them in GetInputKindList. Matched by
# substring, because macOS reports av_capture_input_v2 and an exact match would miss
# it, and tried in this order, so a preferred matcher wins over a later one whatever
# order OBS lists the kinds in. Windows and Linux go through the same mechanism; see
# the design spec for that assumption.
VIDEO_INPUT_KIND_MATCHERS = ("av_capture_input", "dshow_input", "v4l2_input")
AUDIO_INPUT_KIND_MATCHERS = ("coreaudio_input_capture", "wasapi_input_capture",
                             "pulse_input_capture")

# Throwaway names for the device-enumeration probe: a temp scene plus disabled temp
# inputs. They never appear in program output and are always removed afterwards.
PROBE_SCENE_NAME = "__racecast_device_probe__"
PROBE_VIDEO_INPUT = "__racecast_probe_video__"
PROBE_MIC_INPUT = "__racecast_probe_mic__"


def pick_input_kind(kind_list, matchers):
    """First kind in `kind_list` whose lowercased value contains a `matchers`
    substring. Matcher order wins over list order, so a preferred matcher beats a
    less-preferred kind that appears earlier in `kind_list`. Returns None if nothing
    matches or `kind_list` is not a list or tuple. Case-insensitive."""
    if not isinstance(kind_list, (list, tuple)):
        return None
    lowered = [(k, str(k).lower()) for k in kind_list]
    for sub in matchers:
        needle = sub.lower()
        for original, low in lowered:
            if needle in low:
                return original
    return None


def parse_property_items(payload):
    """[{name,value,enabled}] from a GetInputPropertiesListPropertyItems response,
    dropping items with an empty itemValue. A bad shape returns []."""
    if not isinstance(payload, dict):
        return []
    items = payload.get("propertyItems")
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        val = it.get("itemValue")
        if val is None or val == "":
            continue
        out.append({"name": it.get("itemName", ""), "value": val,
                    "enabled": bool(it.get("itemEnabled", True))})
    return out


def probe_device_options(host="127.0.0.1", port=None, password=None, timeout=2.0):
    """Enumerate the local video-capture and microphone devices OBS offers, WITHOUT
    any solo collection imported, which is exactly what OBS shows when you add a
    capture source by hand. Opens one session, creates a throwaway scene plus a
    disabled temp input of the platform capture kind, reads its device dropdown,
    then removes both. Returns {"devices": [...], "note": str, "mic": [...],
    "mic_note": str}, each list being [{name, value, enabled}].

    Best-effort: an unreachable OBS, a missing capture kind or a protocol surprise
    gives empty lists and a readable note, and NEVER raises. The throwaway scene and
    inputs are ALWAYS removed, including mid-probe, and the current program scene is
    never switched, because the temp input is created disabled.

    The probe uses FIXED scene and input names, so two probes running at once
    contend on the same throwaway objects and the loser degrades to an empty list
    plus a note. That is the trade-off of fixed names: the blast radius stays inside
    the throwaway scene, never the real collection or the program, and the next
    probe's pre-clear RemoveScene reclaims any straggler."""
    session, note = _connect(host, port, password, timeout)
    if session is None:
        return {"devices": [], "note": note, "mic": [], "mic_note": note}
    out = {"devices": [], "note": "", "mic": [], "mic_note": ""}
    created = []
    scene_made = False

    def read_options(input_name, kind, prop):
        # Track for cleanup BEFORE CreateInput: if OBS creates the input but the
        # response is lost mid-exchange, the finally still attempts RemoveInput. A
        # RemoveInput for a never-created input is guarded and harmless.
        created.append(input_name)
        try:
            session.request("CreateInput", {"sceneName": PROBE_SCENE_NAME,
                                            "inputName": input_name, "inputKind": kind,
                                            "sceneItemEnabled": False})
            payload = session.request("GetInputPropertiesListPropertyItems",
                                      {"inputName": input_name, "propertyName": prop})
            return parse_property_items(payload), ""
        except Exception as exc:                     # noqa: BLE001  best-effort contract
            return [], (str(exc) or exc.__class__.__name__)

    try:
        kinds = session.request("GetInputKindList", {}).get("inputKinds", [])
        vid_kind = pick_input_kind(kinds, VIDEO_INPUT_KIND_MATCHERS)
        aud_kind = pick_input_kind(kinds, AUDIO_INPUT_KIND_MATCHERS)
        try:                                         # clear a stale scene from a crash
            session.request("RemoveScene", {"sceneName": PROBE_SCENE_NAME})
        except Exception:                            # noqa: BLE001  best-effort contract
            pass
        session.request("CreateScene", {"sceneName": PROBE_SCENE_NAME})
        scene_made = True
        # device_property_name(...) can be None on an unknown platform, but
        # read_options is only reached when a kind matched, and the matchers also
        # return None on an unknown platform, so a None property never reaches OBS.
        # Keep that invariant if you add a matcher for an exotic platform.
        if vid_kind:
            out["devices"], out["note"] = read_options(
                PROBE_VIDEO_INPUT, vid_kind, device_property_name(sys.platform))
        else:
            out["note"] = "no video capture input kind in this OBS"
        if aud_kind:
            out["mic"], out["mic_note"] = read_options(
                PROBE_MIC_INPUT, aud_kind,
                device_property_name(sys.platform, kind="audio"))
        else:
            out["mic_note"] = "no audio capture input kind in this OBS"
    except Exception as exc:                         # noqa: BLE001  best-effort contract
        reason = str(exc) or exc.__class__.__name__
        out["note"] = out["note"] or reason
        out["mic_note"] = out["mic_note"] or reason
    finally:
        for name in created:
            try:
                session.request("RemoveInput", {"inputName": name})
            except Exception:                        # noqa: BLE001  best-effort contract
                pass
        if scene_made:
            try:
                session.request("RemoveScene", {"sceneName": PROBE_SCENE_NAME})
            except Exception:                        # noqa: BLE001  best-effort contract
                pass
        session.close()
    return out


def release_feed_inputs(ports=RELAY_PORTS, host="127.0.0.1", port=None,
                        password=None, timeout=2.0, session=None):
    """Make OBS drop its connections to the just-killed relay feed ports by
    re-applying each feed input's own settings, a forced source rebuild that closes
    the socket without changing anything. See the module docstring.

    Returns (released_input_names, note). Best effort by design: any failure, from
    OBS not running to a protocol surprise, yields ([], reason) and NEVER an
    exception, because stopping the relay must always go through.
    """
    note = ""
    own = session is None
    if own:
        session, note = _connect(host, port, password, timeout)
    if session is None:
        return [], note
    try:
        inputs = session.request("GetInputList",
                                 {"inputKind": "ffmpeg_source"}).get("inputs", [])
        settings = {}                                # filled by the name filter

        def get_settings(name):
            settings[name] = session.request(
                "GetInputSettings", {"inputName": name}).get("inputSettings", {})
            return settings[name]

        names = feed_input_names(inputs, get_settings, ports)
        for name in names:
            session.request("SetInputSettings",      # unchanged: a rebuild only
                            {"inputName": name, "inputSettings": settings[name],
                             "overlay": True})
        return names, ""
    except Exception as exc:                         # noqa: BLE001  see docstring
        return [], str(exc) or exc.__class__.__name__
    finally:
        if own:
            session.close()


def feed_media_cursors(ports=RELAY_PORTS, host="127.0.0.1", port=None,
                       password=None, timeout=2.0, session=None):
    """({feed_port: mediaCursor_ms or None}, note) for the relay feed media inputs
    OBS holds. The freeze detector uses whether the on-air feed's cursor ADVANCES to
    tell a live demuxer from a frozen one, a signal renderSkippedFrames is blind to
    (#488). Best-effort: an unreachable OBS or a protocol surprise gives
    ({}, reason), never an exception."""
    note = ""
    own = session is None
    if own:
        session, note = _connect(host, port, password, timeout)
    if session is None:
        return {}, note
    try:
        inputs = session.request("GetInputList",
                                 {"inputKind": "ffmpeg_source"}).get("inputs", [])
        want = {}
        for p in ports:
            want[f"127.0.0.1:{p}"] = p
            want[f"localhost:{p}"] = p
        out = {}
        for inp in inputs:
            name = inp.get("inputName")
            if not name:
                continue
            try:
                settings = session.request(
                    "GetInputSettings", {"inputName": name}).get("inputSettings", {})
            except Exception:                        # noqa: BLE001  one bad input mustn't stop the rest
                continue
            if settings.get("is_local_file"):
                continue
            url = settings.get("input")
            if not isinstance(url, str):
                continue
            fp = want.get(urllib.parse.urlsplit(url.strip()).netloc)
            if fp is None:
                continue
            try:
                out[fp] = session.request(
                    "GetMediaInputStatus", {"inputName": name}).get("mediaCursor")
            except Exception:                        # noqa: BLE001  a source with no media status
                out[fp] = None
        return out, ""
    except Exception as exc:                         # noqa: BLE001  best-effort contract
        return {}, str(exc) or exc.__class__.__name__
    finally:
        if own:
            session.close()


def refresh_browser_inputs(needle="127.0.0.1:8088", host="127.0.0.1", port=None,
                           password=None, timeout=2.0, session=None):
    """Press 'Refresh cache of current page' (refreshnocache) on every browser
    source whose URL points at the relay, the programmatic right-click -> Refresh,
    used after a shipped overlay page changed. OBS's CEF caches the page JS until
    then.

    Returns (refreshed_input_names, note). Best effort like release_feed_inputs():
    any failure yields ([], reason), never an exception.
    """
    note = ""
    own = session is None
    if own:
        session, note = _connect(host, port, password, timeout)
    if session is None:
        return [], note
    try:
        inputs = session.request("GetInputList",
                                 {"inputKind": "browser_source"}).get("inputs", [])

        def get_settings(name):
            return session.request("GetInputSettings",
                                   {"inputName": name}).get("inputSettings", {})

        names = browser_input_names(inputs, get_settings, needle)
        for name in names:
            session.request("PressInputPropertiesButton",
                            {"inputName": name, "propertyName": "refreshnocache"})
        return names, ""
    except Exception as exc:                         # noqa: BLE001  see docstring
        return [], str(exc) or exc.__class__.__name__
    finally:
        if own:
            session.close()


def get_source_screenshot(source_name, width=640, fmt="jpg", quality=60,
                          host="127.0.0.1", port=None, password=None, timeout=2.0,
                          session=None):
    """A scaled screenshot of an OBS source or scene as raw JPEG bytes.
    Returns (bytes, "") or (None, note). Best effort: never raises."""
    note = ""
    own = session is None
    if own:
        session, note = _connect(host, port, password, timeout)
    if session is None:
        return None, note
    try:
        resp = session.request(
            "GetSourceScreenshot",
            screenshot_request_data(source_name, width, fmt, quality))
        data = parse_screenshot_data_uri(resp.get("imageData"))
        if data is None:
            return None, "OBS returned no image data"
        return data, ""
    except Exception as exc:                          # noqa: BLE001  best-effort contract
        return None, str(exc) or exc.__class__.__name__
    finally:
        if own:
            session.close()


def get_program_screenshot(width=640, fmt="jpg", quality=60,
                           host="127.0.0.1", port=None, password=None, timeout=2.0,
                           session=None):
    """Screenshot the current OBS program scene, what viewers see, as raw JPEG
    bytes. Resolves the active scene name, then screenshots it on the same session.
    Returns (bytes, "") or (None, note). Best effort: never raises."""
    note = ""
    own = session is None
    if own:
        session, note = _connect(host, port, password, timeout)
    if session is None:
        return None, note
    try:
        cur = session.request("GetCurrentProgramScene", {})
        scene = cur.get("currentProgramSceneName") or cur.get("sceneName")
        if not scene:
            return None, "OBS returned no program scene"
        resp = session.request(
            "GetSourceScreenshot",
            screenshot_request_data(scene, width, fmt, quality))
        data = parse_screenshot_data_uri(resp.get("imageData"))
        if data is None:
            return None, "OBS returned no image data"
        return data, ""
    except Exception as exc:                          # noqa: BLE001  best-effort contract
        return None, str(exc) or exc.__class__.__name__
    finally:
        if own:
            session.close()


def get_current_program_scene(host="127.0.0.1", port=None,
                              password=None, timeout=2.0, session=None):
    """The name of the current OBS program scene, what viewers see, or None.
    Returns (scene_name, "") or (None, note). Best effort: never raises. The
    auto-failover guard uses it to fire ONLY while OBS is still on the on-air feed
    scene (#378)."""
    note = ""
    own = session is None
    if own:
        session, note = _connect(host, port, password, timeout)
    if session is None:
        return None, note
    try:
        cur = session.request("GetCurrentProgramScene", {})
        scene = cur.get("currentProgramSceneName") or cur.get("sceneName")
        if not scene:
            return None, "OBS returned no program scene"
        return scene, ""
    except Exception as exc:                          # noqa: BLE001  best-effort contract
        return None, str(exc) or exc.__class__.__name__
    finally:
        if own:
            session.close()


_TRANSITION_KIND = {"cut": "cut_transition", "fade": "fade_transition",
                    "stinger": "stinger_transition"}
_TRANSITION_NAME_FALLBACK = {"cut": "cut", "fade": "fade"}


def resolve_transition(choice, transitions):
    """Resolve a director choice, 'cut', 'fade' or 'stinger', to a concrete OBS
    transition NAME, matched by kind against a GetSceneTransitionList payload of
    {transitionName, transitionKind}. Falls back to a case-insensitive name match
    for cut and fade. Returns (name or None, note); a stinger with none configured
    gives (None, note). Never raises."""
    kind = _TRANSITION_KIND.get(choice)
    for t in transitions or []:
        if kind and t.get("transitionKind") == kind:
            return (t.get("transitionName"), "")
    fb = _TRANSITION_NAME_FALLBACK.get(choice)
    if fb:
        for t in transitions or []:
            if (t.get("transitionName") or "").lower() == fb:
                return (t.get("transitionName"), "")
    if choice == "stinger":
        return (None, "no Stinger configured in OBS; used Cut")
    return (None, "")


def set_current_program_scene(scene, host="127.0.0.1", port=None,
                              password=None, timeout=2.0,
                              transition=None, duration_ms=None, session=None):
    """Switch the OBS program scene, best effort. When `transition` is given, it
    sets that transition, resolved by kind via GetSceneTransitionList, and its
    duration first, then switches, so a director take uses the chosen transition. A
    stinger with none configured degrades to a cut and returns a note. Returns
    (ok, note); never raises."""
    note = ""
    own = session is None
    if own:
        session, note = _connect(host, port, password, timeout)
    if session is None:
        return False, note
    out_note = ""
    try:
        if transition:
            tlist = session.request("GetSceneTransitionList", {}).get("transitions", [])
            name, resolve_note = resolve_transition(transition, tlist)
            if name is None and transition == "stinger":
                name, _ = resolve_transition("cut", tlist)     # degrade to a cut
                out_note = resolve_note
            if name:
                session.request("SetCurrentSceneTransition", {"transitionName": name})
                if transition != "cut" and duration_ms is not None:
                    session.request("SetCurrentSceneTransitionDuration",
                                    {"transitionDuration": int(duration_ms)})
        session.request("SetCurrentProgramScene", {"sceneName": scene})
        return True, out_note
    except Exception as exc:                          # noqa: BLE001  best-effort contract
        return False, str(exc) or exc.__class__.__name__
    finally:
        if own:
            session.close()


def switch_to_scene_if_idle(scene, host="127.0.0.1", port=None,
                            password=None, timeout=2.0, session=None):
    """Switch OBS to `scene` ONLY when no stream output is active, so a live program
    is never cut. Reads GetStreamStatus first and leaves the program scene untouched
    while the stream is live. Best effort: never raises. Returns (action, note)
    where action is one of:
      "switched"  OBS was idle and SetCurrentProgramScene was sent
      "live"      OBS is streaming and NO switch was sent; the note explains
      "error"     OBS was unreachable or a request failed; the note has the reason."""
    note = ""
    own = session is None
    if own:
        session, note = _connect(host, port, password, timeout)
    if session is None:
        return "error", note
    try:
        status = parse_stream_status(session.request("GetStreamStatus", {}))
        if status.get("stream_active"):
            return "live", "OBS is streaming, so the program scene is untouched"
        session.request("SetCurrentProgramScene", {"sceneName": scene})
        return "switched", ""
    except Exception as exc:                          # noqa: BLE001  best-effort contract
        return "error", str(exc) or exc.__class__.__name__
    finally:
        if own:
            session.close()


def set_input_volume(input_name, volume_db, host="127.0.0.1", port=None,
                     password=None, timeout=2.0, session=None):
    """Set an OBS audio input volume in dB, best effort. Returns (ok, note)."""
    note = ""
    own = session is None
    if own:
        session, note = _connect(host, port, password, timeout)
    if session is None:
        return False, note
    try:
        session.request("SetInputVolume",
                        {"inputName": input_name, "inputVolumeDb": float(volume_db)})
        return True, ""
    except Exception as exc:                          # noqa: BLE001  best-effort contract
        return False, str(exc) or exc.__class__.__name__
    finally:
        if own:
            session.close()


def set_input_mute(input_name, muted, host="127.0.0.1", port=None,
                   password=None, timeout=2.0, session=None):
    """Set an OBS audio input mute state, best effort. Returns (ok, note)."""
    note = ""
    own = session is None
    if own:
        session, note = _connect(host, port, password, timeout)
    if session is None:
        return False, note
    try:
        session.request("SetInputMute",
                        {"inputName": input_name, "inputMuted": bool(muted)})
        return True, ""
    except Exception as exc:                          # noqa: BLE001  best-effort contract
        return False, str(exc) or exc.__class__.__name__
    finally:
        if own:
            session.close()


def set_stream(active, host="127.0.0.1", port=None,
               password=None, timeout=2.0, session=None):
    """Start or stop the OBS stream output, best effort: `active` True sends
    StartStream, False StopStream. Idempotent, so when OBS is ALREADY in the
    requested state it returns (True, "") without sending anything and a
    double-click never surfaces OBS's "output already active" error. Returns
    (ok, note); never raises."""
    note = ""
    own = session is None
    if own:
        session, note = _connect(host, port, password, timeout)
    if session is None:
        return False, note
    try:
        status = parse_stream_status(session.request("GetStreamStatus", {}))
        if status.get("stream_active") == bool(active):
            return True, ""                       # already in the desired state
        session.request("StartStream" if active else "StopStream", {})
        return True, ""
    except Exception as exc:                       # noqa: BLE001  best-effort contract
        return False, str(exc) or exc.__class__.__name__
    finally:
        if own:
            session.close()


def set_stream_service(platform, key, host="127.0.0.1", port=None,
                       password=None, timeout=2.0, session=None):
    """Set OBS's stream service and key for a single-channel event, best effort.
    HARD GUARD: it refuses while OBS is streaming, because a live service or key
    change is unsafe, and returns (False, note). An unknown platform or an
    unreachable OBS also returns (False, note). The key is applied to OBS and NEVER
    logged. Returns (ok, note); never raises."""
    try:
        data = stream_service_payload(platform, key)
    except ValueError as exc:
        return False, str(exc)
    note = ""
    own = session is None
    if own:
        session, note = _connect(host, port, password, timeout)
    if session is None:
        return False, note
    try:
        status = parse_stream_status(session.request("GetStreamStatus", {}))
        if status.get("stream_active"):
            return False, ("OBS is streaming. Stop the broadcast before "
                           "changing the stream target.")
        session.request("SetStreamServiceSettings", data)
        return True, ""
    except Exception as exc:                       # noqa: BLE001  best-effort contract
        return False, str(exc) or exc.__class__.__name__
    finally:
        if own:
            session.close()


def read_obs_state(sources, inputs, host="127.0.0.1", port=None,
                   password=None, timeout=2.0, session=None):
    """One-session panel-refresh snapshot: the current program scene, the enabled
    state of each (scene, source) and the mute and volume of each audio input.
    `sources` is [(scene, source), ...] and `inputs` is [name, ...]. Returns
    (state, "") or (None, note); a per-item OBS error leaves that item's fields None
    rather than failing the whole read. Best effort: never raises."""
    note = ""
    own = session is None
    if own:
        session, note = _connect(host, port, password, timeout)
    if session is None:
        return None, note
    try:
        cur = session.request("GetCurrentProgramScene", {})
        scene = cur.get("currentProgramSceneName") or cur.get("sceneName")
        src_out = []
        for sc, src in sources:
            try:
                sid = session.request(
                    "GetSceneItemId",
                    {"sceneName": sc, "sourceName": src}).get("sceneItemId")
                enabled = session.request(
                    "GetSceneItemEnabled",
                    {"sceneName": sc, "sceneItemId": sid}).get("sceneItemEnabled")
            except Exception:                         # noqa: BLE001  per-item best effort
                enabled = None
            src_out.append({"scene": sc, "source": src, "enabled": enabled})
        aud_out = []
        for name in inputs:
            try:
                muted = session.request(
                    "GetInputMute", {"inputName": name}).get("inputMuted")
                vol = session.request(
                    "GetInputVolume", {"inputName": name}).get("inputVolumeDb")
            except Exception:                         # noqa: BLE001  per-item best effort
                muted, vol = None, None
            aud_out.append({"input": name, "muted": muted, "volumeDb": vol})
        try:
            st = parse_stream_status(session.request("GetStreamStatus", {}))
            stream = {"active": st.get("stream_active"),
                      "reconnecting": st.get("stream_reconnecting"),
                      "timecode": st.get("stream_timecode")}
        except Exception:                         # noqa: BLE001  per-item best effort
            stream = None
        return {"scene": scene, "sources": src_out,
                "audio": aud_out, "stream": stream}, ""
    except Exception as exc:                          # noqa: BLE001  best-effort contract
        return None, str(exc) or exc.__class__.__name__
    finally:
        if own:
            session.close()


def reflect_feed_state(live, do_cut, scene=STINT_SCENE, sources=None,
                       host="127.0.0.1", port=None, password=None, timeout=2.0,
                       session=None, audio=None, extra_mute=()):
    """Reflect which feed, A or B, is on air into OBS: show and hide the Stint-scene
    sources, mute and unmute the feed audio inputs plus the commentary mic of a local
    stint (see feed_audio_plan), and cut the program to Stint when do_cut is set.
    Best effort by design: it returns (applied_intents, note) and NEVER raises,
    because a handover must go through even if OBS is closed. A failed mute is noted
    and skipped, so an input the collection lacks never stops the cut, while a failed
    show or hide still aborts. On any failure the relay falls back to the manual
    panel and Companion controls."""
    intents = feed_state_intents(live, do_cut, scene=scene, sources=sources,
                                 audio=audio, extra_mute=extra_mute)
    note = ""
    own = session is None
    if own:
        session, note = _connect(host, port, password, timeout)
    if session is None:
        return [], note
    applied, notes = [], []
    try:
        for verb, target in intents:
            if verb in ("show", "hide"):
                sid = session.request("GetSceneItemId",
                                      {"sceneName": scene, "sourceName": target}).get("sceneItemId")
                if sid is None:
                    raise ValueError(f"scene item '{target}' not found in scene '{scene}'")
                session.request("SetSceneItemEnabled",
                                {"sceneName": scene, "sceneItemId": sid,
                                 "sceneItemEnabled": verb == "show"})
            elif verb in ("mute", "unmute"):
                try:
                    session.request("SetInputMute",
                                    {"inputName": target, "inputMuted": verb == "mute"})
                except Exception as exc:          # noqa: BLE001  one input, not the handover
                    notes.append(f"{verb} {target}: {str(exc) or exc.__class__.__name__}")
                    continue
            elif verb == "cut":
                session.request("SetCurrentProgramScene", {"sceneName": target})
            applied.append((verb, target))
        return applied, "; ".join(notes)
    except Exception as exc:                         # noqa: BLE001  best-effort contract
        return applied, "; ".join(notes + [str(exc) or exc.__class__.__name__])
    finally:
        if own:
            session.close()


def set_feed_close_when_inactive(inputs, value=True, host="127.0.0.1", port=None,
                                  password=None, timeout=2.0):
    """Set close_when_inactive on each named feed media input, best effort. When
    fan-out is enabled OBS disconnects off-air sources, so no stale backlog forms
    and the stale-on-activation glitch disappears. `inputs` is a list of OBS input
    names. Returns "" on success or a short note on any failure; never raises."""
    session, note = _connect(host, port, password, timeout)
    if session is None:
        return note
    try:
        for name in inputs:
            session.request("SetInputSettings",
                            {"inputName": name,
                             "inputSettings": {"close_when_inactive": bool(value)},
                             "overlay": True})
        return ""
    except Exception as exc:                         # noqa: BLE001  best-effort contract
        return str(exc) or exc.__class__.__name__
    finally:
        session.close()


def set_scene_item_enabled(scene, source, enabled, host="127.0.0.1", port=None,
                           password=None, timeout=2.0, session=None):
    """Enable or disable a scene item, best effort. Returns (ok, note), with
    (False, reason) on any failure, from a closed OBS to a missing item, and NEVER
    an exception."""
    note = ""
    own = session is None
    if own:
        session, note = _connect(host, port, password, timeout)
    if session is None:
        return False, note
    try:
        sid = session.request("GetSceneItemId",
                              {"sceneName": scene, "sourceName": source}).get("sceneItemId")
        if sid is None:
            return False, f"scene item '{source}' not found in scene '{scene}'"
        session.request("SetSceneItemEnabled",
                        {"sceneName": scene, "sceneItemId": sid,
                         "sceneItemEnabled": bool(enabled)})
        return True, ""
    except Exception as exc:                         # noqa: BLE001  best-effort contract
        return False, str(exc) or exc.__class__.__name__
    finally:
        if own:
            session.close()


def set_scene_item_transform(scene, source, transform, host="127.0.0.1", port=None,
                             password=None, timeout=2.0, session=None):
    """Set a scene item's transform, best effort. `transform` is the obs-websocket
    sceneItemTransform dict (see pov_scene_item_transform), applied as
    GetSceneItemId then SetSceneItemTransform. Returns (ok, note), with
    (False, reason) on any failure and NEVER an exception."""
    note = ""
    own = session is None
    if own:
        session, note = _connect(host, port, password, timeout)
    if session is None:
        return False, note
    try:
        sid = session.request("GetSceneItemId",
                              {"sceneName": scene, "sourceName": source}).get("sceneItemId")
        if sid is None:
            return False, f"scene item '{source}' not found in scene '{scene}'"
        session.request("SetSceneItemTransform",
                        {"sceneName": scene, "sceneItemId": sid,
                         "sceneItemTransform": dict(transform)})
        return True, ""
    except Exception as exc:                         # noqa: BLE001  best-effort contract
        return False, str(exc) or exc.__class__.__name__
    finally:
        if own:
            session.close()


OBS_NOT_READY_CODE = 207


def is_not_ready(note):
    """True when a best-effort note is obs-websocket's "OBS is not ready to perform
    the request" (code 207), the window between OBS accepting the connection and
    having finished loading."""
    text = str(note or "")
    return str(OBS_NOT_READY_CODE) in text and "not ready" in text.lower()


def wait_until_ready(timeout=30.0, interval=1.0, host="127.0.0.1", port=None,
                     password=None, probe=None, clock=time.monotonic,
                     sleep=time.sleep):
    """Poll until obs-websocket can actually serve a request. Returns (ok, note).

    `app_running("obs")` only proves the PROCESS exists. obs-websocket accepts the
    connection several seconds earlier than OBS can answer and replies 207 in
    between, so a bring-up that launched OBS itself ran the scene-collection check,
    the page refresh and the Standby switch inside that window and silently skipped
    all three. Best effort: never raises.
    """
    probe = _probe_ready if probe is None else probe
    deadline = clock() + timeout
    note = ""
    while True:
        ok, note = probe(host, port, password)
        if ok:
            return True, ""
        if clock() >= deadline:
            return False, note or "OBS did not become ready"
        sleep(interval)


def _probe_ready(host, port, password):
    """(ready, note) from one cheap request. Not ready and unreachable are both
    False, because the caller only waits and does not diagnose."""
    session, note = _connect(host, port, password, 2.0)
    if session is None:
        return False, note
    try:
        session.request("GetVersion", {})
        return True, ""
    except Exception as exc:                         # noqa: BLE001  best effort
        return False, str(exc) or exc.__class__.__name__
    finally:
        session.close()


def get_scene_collection(host="127.0.0.1", port=None, password=None, timeout=2.0,
                         expected=EXPECTED_SCENE_COLLECTION, session=None):
    """Ask OBS which scene collection is active and classify it against `expected`,
    which defaults to EXPECTED_SCENE_COLLECTION. Returns (status_dict, note), with
    (None, reason) on any failure, from a closed OBS to a protocol surprise, and
    NEVER an exception."""
    note = ""
    own = session is None
    if own:
        session, note = _connect(host, port, password, timeout)
    if session is None:
        return None, note
    try:
        resp = session.request("GetSceneCollectionList", {})
        status = scene_collection_status(resp.get("currentSceneCollectionName"),
                                         resp.get("sceneCollections", []),
                                         expected=expected)
        return status, ""
    except Exception as exc:                         # noqa: BLE001  best-effort contract
        return None, str(exc) or exc.__class__.__name__
    finally:
        if own:
            session.close()


def set_scene_collection(name=EXPECTED_SCENE_COLLECTION, host="127.0.0.1",
                         port=None, password=None, timeout=2.0, session=None):
    """Switch OBS to scene collection `name`. Returns (ok, note). Best effort:
    - already on `name`            -> (True, "already on '<name>'"), no switch
    - `name` not in the live list  -> (False, "...not found..."), never created
    - OBS rejects it, output active -> (False, <obs error>); _Session.request
      raises ValueError on a failed requestStatus, caught here and never re-raised
    - OBS unreachable              -> (False, reason)
    The switch is heavyweight in OBS: it tears down and rebuilds ALL sources,
    including the relay feeds. `racecast event start` calls it automatically to
    align OBS with the active profile, gated by RACECAST_OBS_COLLECTION_SWITCH;
    `racecast obs collection set` is the manual path."""
    note = ""
    own = session is None
    if own:
        session, note = _connect(host, port, password, timeout)
    if session is None:
        return False, note
    try:
        resp = session.request("GetSceneCollectionList", {})
        current = resp.get("currentSceneCollectionName")
        available = resp.get("sceneCollections", [])
        if current == name:
            return True, f"already on '{name}'"
        if name not in available:
            return False, (f"scene collection '{name}' not found in OBS "
                           f"(import it with `racecast setup`)")
        session.request("SetCurrentSceneCollection", {"sceneCollectionName": name})
        return True, ""
    except Exception as exc:                         # noqa: BLE001  best-effort contract
        return False, str(exc) or exc.__class__.__name__
    finally:
        if own:
            session.close()

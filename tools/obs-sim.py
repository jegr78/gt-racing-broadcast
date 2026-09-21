#!/usr/bin/env python3
"""Simulated OBS WebSocket server: a stand-in OBS for reproducible screenshots.

Speaks just enough obs-websocket v5 (no-auth handshake, GetCurrentProgramScene,
GetSourceScreenshot) to answer the relay's `get_program_screenshot`, serving a
FIXED program image. Wiki and e2e captures can then show a simulated broadcast
program without touching, or even running, the producer's real OBS.

Point the relay at it with the RACECAST_OBS_WS_* overrides (see obs_ws.py):

    python3 tools/obs-sim.py --image runtime/demo/program.jpg --port 4466 &
    RACECAST_OBS_WS_HOST=127.0.0.1 RACECAST_OBS_WS_PORT=4466 \
        python3 src/racecast.py relay start

Maintainer tool, not shipped. It reuses accept_key and decode_frame from
src/scripts/obs_ws.py so the framing stays byte-identical to what the real
client expects.
"""
import argparse
import base64
import importlib.util
import json
import os
import socket
import struct
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "obs_ws", os.path.join(ROOT, "src", "scripts", "obs_ws.py"))
obs_ws = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(obs_ws)


def _send_json(conn, obj):
    """One unmasked server->client text frame; servers must not mask."""
    payload = json.dumps(obj).encode()
    n = len(payload)
    if n < 126:
        head = bytes([0x81, n])
    elif n < 1 << 16:
        head = bytes([0x81, 126]) + struct.pack(">H", n)
    else:
        head = bytes([0x81, 127]) + struct.pack(">Q", n)
    conn.sendall(head + payload)


def _recv_json(conn, buf):
    """Next text message as JSON. Reuses obs_ws.decode_frame, which handles the
    client's masked frames. Raises on close or EOF. `buf` is a mutable 1-element
    list."""
    while True:
        frame = obs_ws.decode_frame(buf[0])
        if frame is None:
            chunk = conn.recv(65536)
            if not chunk:
                raise ConnectionError("client gone")
            buf[0] += chunk
            continue
        opcode, payload, buf[0] = frame
        if opcode == 0x8:
            raise ConnectionError("client closed")
        if opcode == 0x1:
            return json.loads(payload)


def _serve_one(conn, image_data_uri, scene):
    conn.settimeout(30)
    # HTTP upgrade
    req = b""
    while b"\r\n\r\n" not in req:
        chunk = conn.recv(4096)
        if not chunk:
            return
        req += chunk
    key = next(line.split(":", 1)[1].strip()
               for line in req.decode("iso-8859-1").split("\r\n")
               if line.lower().startswith("sec-websocket-key:"))
    conn.sendall(("HTTP/1.1 101 Switching Protocols\r\n"
                  "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                  "Sec-WebSocket-Accept: " + obs_ws.accept_key(key) +
                  "\r\n\r\n").encode())
    # Hello has no authentication field, so the client skips auth, then Identify.
    _send_json(conn, {"op": 0, "d": {"obsWebSocketVersion": "5.5.0", "rpcVersion": 1}})
    buf = [b""]
    _recv_json(conn, buf)                              # Identify (op 1)
    _send_json(conn, {"op": 2, "d": {"negotiatedRpcVersion": 1}})
    while True:
        try:
            msg = _recv_json(conn, buf)
        except (ConnectionError, OSError):
            return
        d = msg.get("d", {})
        rtype, rid = d.get("requestType"), d.get("requestId")
        if rtype == "GetCurrentProgramScene":
            resp = {"currentProgramSceneName": scene, "sceneName": scene}
        elif rtype == "GetSourceScreenshot":
            resp = {"imageData": image_data_uri}
        else:                                          # everything else: bare OK
            resp = {}
        _send_json(conn, {"op": 7, "d": {
            "requestType": rtype, "requestId": rid,
            "requestStatus": {"result": True, "code": 100},
            "responseData": resp}})


def _safe_serve(conn, image_data_uri, scene):
    try:
        _serve_one(conn, image_data_uri, scene)
    except Exception:                                  # one bad client must not kill the server
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass  # socket already torn down, nothing to clean up


def main():
    ap = argparse.ArgumentParser(description="Simulated OBS WebSocket server (screenshots).")
    ap.add_argument("--image", required=True, help="program image to serve (jpg or png)")
    ap.add_argument("--port", type=int, default=4466, help="listen port (default 4466)")
    ap.add_argument("--scene", default="Race", help="reported program scene name")
    args = ap.parse_args()

    with open(args.image, "rb") as fh:
        raw = fh.read()
    fmt = "png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else "jpg"
    image_data_uri = f"data:image/{fmt};base64," + base64.b64encode(raw).decode()

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", args.port))
    srv.listen(5)
    print(f"obs-sim: serving {args.image} ({fmt}, {len(raw)} B) on "
          f"127.0.0.1:{args.port} as program scene {args.scene!r}", flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=_safe_serve,
                         args=(conn, image_data_uri, args.scene), daemon=True).start()


if __name__ == "__main__":
    main()

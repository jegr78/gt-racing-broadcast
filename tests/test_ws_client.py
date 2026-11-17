#!/usr/bin/env python3
"""The stdlib WebSocket client behind the timing host publisher (#1161).
Run: python3 tests/test_ws_client.py"""
import os, socket, struct, sys, threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "scripts"))
import obs_ws  # noqa: E402
import ws_client  # noqa: E402


def _server_frame(payload, opcode=0x1):
    n = len(payload)
    if n < 126:
        head = bytes([0x80 | opcode, n])
    elif n < 1 << 16:
        head = bytes([0x80 | opcode, 126]) + struct.pack(">H", n)
    else:
        head = bytes([0x80 | opcode, 127]) + struct.pack(">Q", n)
    return head + payload


class _EchoServer:
    """One-connection loopback WebSocket server: records the request, echoes text,
    optionally sends a ping first."""

    def __init__(self, ping=False, echo=True):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.request = b""
        self.got_pong = threading.Event()
        self.ping, self.echo = ping, echo
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        conn, _ = self.sock.accept()
        conn.settimeout(5)
        while b"\r\n\r\n" not in self.request:
            self.request += conn.recv(4096)
        key = [ln.split(":", 1)[1].strip() for ln in self.request.decode().split("\r\n")
               if ln.lower().startswith("sec-websocket-key:")][0]
        conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                      "Connection: Upgrade\r\nSec-WebSocket-Accept: "
                      f"{obs_ws.accept_key(key)}\r\n\r\n").encode())
        if self.ping:
            conn.sendall(_server_frame(b"p", opcode=0x9))
        buf = b""
        try:
            while True:
                data = conn.recv(65536)
                if not data:
                    return
                buf += data
                while True:
                    frame = obs_ws.decode_frame(buf)
                    if frame is None:
                        break
                    opcode, payload, buf = frame
                    if opcode == 0xA:
                        self.got_pong.set()
                    elif opcode == 0x1 and self.echo:
                        conn.sendall(_server_frame(payload))
                    elif opcode == 0x8:
                        conn.close()
                        return
        except OSError:
            return


def t_ws_client_roundtrip_large_frame():
    srv = _EchoServer()
    c = ws_client.WsClient.connect(f"ws://127.0.0.1:{srv.port}/t/abc/publish?v=1",
                                   {"Authorization": "Bearer k"}, timeout=5)
    text = "x" * 70000
    c.send_text(text)
    assert c.recv_text(timeout=5) == text, "a frame over 64 KiB comes back intact"
    c.close()
    req = srv.request.decode()
    assert req.startswith("GET /t/abc/publish?v=1 HTTP/1.1\r\n"), req
    assert "Authorization: Bearer k\r\n" in req, req


def t_ws_client_recv_timeout_returns_none():
    srv = _EchoServer(echo=False)
    c = ws_client.WsClient.connect(f"ws://127.0.0.1:{srv.port}/", {}, timeout=5)
    assert c.recv_text(timeout=0.2) is None, "a quiet socket is a timeout, not an error"
    c.close()


def t_ws_client_answers_ping():
    srv = _EchoServer(ping=True, echo=False)
    c = ws_client.WsClient.connect(f"ws://127.0.0.1:{srv.port}/", {}, timeout=5)
    c.recv_text(timeout=0.5)
    assert srv.got_pong.wait(2), "a ping gets a pong"
    c.close()


def t_ws_client_close_from_peer_raises():
    srv = _EchoServer(echo=False)
    c = ws_client.WsClient.connect(f"ws://127.0.0.1:{srv.port}/", {}, timeout=5)
    c.send_text("x")
    c.close()
    try:
        c.recv_text(timeout=0.5)
    except ConnectionError:
        return
    raise AssertionError("a closed client raises ConnectionError")


def t_ws_client_refuses_other_schemes():
    try:
        ws_client.WsClient.connect("http://127.0.0.1:1/", {}, timeout=1)
    except ValueError:
        return
    raise AssertionError("only ws:// and wss:// are accepted")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

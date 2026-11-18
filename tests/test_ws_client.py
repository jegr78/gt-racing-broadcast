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

    def __init__(self, ping=False, echo=True, read_delay=0.0, close_reason=None):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.request = b""
        self.got_pong = threading.Event()
        self.ping, self.echo = ping, echo
        self.read_delay, self.close_reason = read_delay, close_reason
        self.received = 0
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
        if self.close_reason is not None:
            conn.sendall(_server_frame(struct.pack(">H", 1000) + self.close_reason.encode(), opcode=0x8))
        if self.read_delay:
            import time
            time.sleep(self.read_delay)
        buf = b""
        try:
            while True:
                data = conn.recv(65536)
                if not data:
                    return
                buf += data
                self.received += len(data)
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


def t_ws_client_send_keeps_its_timeout_after_a_short_read():
    srv = _EchoServer(echo=False, read_delay=1.0)
    c = ws_client.WsClient.connect(f"ws://127.0.0.1:{srv.port}/", {}, timeout=5)
    assert c.recv_text(timeout=0.05) is None
    c.send_text("x" * 4_000_000)      # fills the socket buffers while the peer sleeps 1 s
    c.close()


def t_ws_client_close_reason_reaches_the_caller():
    srv = _EchoServer(echo=False, close_reason="replaced by a newer publisher")
    c = ws_client.WsClient.connect(f"ws://127.0.0.1:{srv.port}/", {}, timeout=5)
    try:
        c.recv_text(timeout=2)
    except ConnectionError as e:
        assert "replaced by a newer publisher" in str(e), str(e)
        return
    raise AssertionError("a close frame raises ConnectionError")


def t_ws_client_unknown_host_is_an_oserror():
    try:
        ws_client.WsClient.connect("ws://name.invalid/", {}, timeout=5)
    except OSError as e:
        assert not isinstance(e, TimeoutError) or "resolving" in str(e), e
        return
    raise AssertionError("an unresolvable host fails")


def t_wss_requires_tls_1_2():
    import ssl
    seen = []

    class _Ctx:
        minimum_version = None

        def wrap_socket(self, sock, server_hostname):
            seen.append(self.minimum_version)
            raise OSError("stop after the TLS setup")
    srv = _EchoServer(echo=False)
    try:
        ws_client.WsClient.connect(f"wss://127.0.0.1:{srv.port}/", {}, timeout=2, ssl_context=_Ctx())
    except OSError:
        pass  # the fake context stops the connect on purpose
    assert seen == [ssl.TLSVersion.TLSv1_2], seen


def t_ws_client_refuses_other_schemes():
    try:
        ws_client.WsClient.connect("http://127.0.0.1:1/", {}, timeout=1)
    except ValueError:
        return
    raise AssertionError("only ws:// and wss:// are accepted")



class _FakeSock:
    def __init__(self, addr):
        self.addr, self.timeout = addr, None

    def settimeout(self, t):
        self.timeout = t


def _fake_open(addrs, outcomes):
    """ws_client._open_socket with a fake resolver and connect; outcomes per address."""
    tried = []

    def create(addr, timeout):
        tried.append((addr, timeout))
        err = outcomes.get(addr[0])
        if err:
            raise err
        return _FakeSock(addr)
    sock = ws_client._open_socket("h.example", 443, 6.0, resolve=lambda h, p, t: addrs,
                                  create=create)
    return sock, tried


def t_ws_client_falls_back_to_the_next_address():
    v6, v4 = ("2001:db8::1", 443), ("192.0.2.1", 443)
    sock, tried = _fake_open([v6, v4], {"2001:db8::1": TimeoutError("timed out")})
    assert sock.addr == v4, "a dead IPv6 address falls back to IPv4"
    assert [a for a, _ in tried] == [v6, v4], tried
    assert tried[0][1] <= 3.0, "the first address must leave time for the next"
    assert sock.timeout == 6.0, "the connected socket gets the caller's timeout back"


def t_ws_client_raises_the_last_error_when_every_address_fails():
    try:
        _fake_open([("2001:db8::1", 443), ("192.0.2.1", 443)],
                   {"2001:db8::1": TimeoutError("v6"), "192.0.2.1": ConnectionRefusedError("v4")})
    except ConnectionRefusedError as e:
        assert "v4" in str(e)
        return
    raise AssertionError("every address failed: the last error must surface")


def t_ws_client_resolve_returns_every_address():
    addrs = ws_client._resolve("127.0.0.1", 80, 2.0)
    assert addrs == [("127.0.0.1", 80)], addrs


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

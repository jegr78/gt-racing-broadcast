"""A small stdlib WebSocket client (text frames, ping/pong, TLS) for the relay's
timing host publisher (#1161). Framing comes from obs_ws."""
import base64
import os
import socket
import ssl
import threading
import time
import urllib.parse

import obs_ws

OP_TEXT, OP_CLOSE, OP_PING, OP_PONG = 0x1, 0x8, 0x9, 0xA


def _resolve(host, port, timeout):
    """(address, port) with the DNS lookup bounded too; create_connection's timeout is not."""
    found, failed = [], []

    def lookup():
        try:
            found.append(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError as e:
            failed.append(e)
    t = threading.Thread(target=lookup, daemon=True)
    t.start()
    t.join(timeout)
    if failed:
        raise failed[0]
    if not found:
        raise TimeoutError(f"resolving {host} timed out")
    return found[0][0][4][:2]


class WsClient:
    def __init__(self, sock, buf=b"", timeout=5.0):
        self._sock = sock
        self._buf = buf
        self._timeout = timeout       # every send; a read sets its own shorter one
        self._closed = False

    @classmethod
    def connect(cls, url, headers, timeout, ssl_context=None):
        """Open ws:// or wss:// (certificate verified) and finish the handshake."""
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("ws", "wss") or not parts.hostname:
            raise ValueError(f"not a WebSocket URL: {parts.scheme}://")
        tls = parts.scheme == "wss"
        port = parts.port or (443 if tls else 80)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        sock = socket.create_connection(_resolve(parts.hostname, port, timeout), timeout=timeout)
        try:
            if tls:
                ctx = ssl_context or ssl.create_default_context()
                ctx.minimum_version = ssl.TLSVersion.TLSv1_2
                sock = ctx.wrap_socket(sock, server_hostname=parts.hostname)
            key = base64.b64encode(os.urandom(16)).decode()
            host_header = parts.hostname if port == (443 if tls else 80) else None
            sock.sendall(obs_ws.handshake_request(parts.hostname, port, key, path=path,
                                                  headers=headers, host_header=host_header))
            response = b""
            deadline = time.monotonic() + timeout
            while b"\r\n\r\n" not in response:
                if time.monotonic() > deadline or len(response) > 16384:
                    raise ConnectionError("WebSocket handshake timed out")
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("connection closed during the WebSocket handshake")
                response += chunk
            rest = obs_ws.parse_handshake(response, key)
        except BaseException:
            sock.close()
            raise
        return cls(sock, rest, timeout)

    def _send(self, opcode, payload):
        if self._closed:
            raise ConnectionError("WebSocket is closed")
        self._sock.settimeout(self._timeout)
        self._sock.sendall(obs_ws.encode_frame(payload, opcode=opcode))

    def send_text(self, text):
        self._send(OP_TEXT, text.encode("utf-8"))

    def recv_text(self, timeout):
        """The next text message, or None after `timeout` seconds without one.
        Answers pings; raises ConnectionError when the connection is gone."""
        if self._closed:
            raise ConnectionError("WebSocket is closed")
        deadline = time.monotonic() + timeout
        while True:
            frame = obs_ws.decode_frame(self._buf)
            if frame is not None:
                opcode, payload, self._buf = frame
                if opcode == OP_TEXT:
                    return payload.decode("utf-8", "replace")
                if opcode == OP_PING:
                    self._send(OP_PONG, payload)
                elif opcode == OP_CLOSE:
                    reason = payload[2:].decode("utf-8", "replace") if len(payload) > 2 else ""
                    self.close()
                    raise ConnectionError("closed by the peer" + (f": {reason}" if reason else ""))
                continue
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            self._sock.settimeout(left)
            try:
                chunk = self._sock.recv(65536)
            except (socket.timeout, TimeoutError):
                return None
            except OSError as e:
                self.close()
                raise ConnectionError(str(e)) from e
            if not chunk:
                self.close()
                raise ConnectionError("connection closed")
            self._buf += chunk

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._sock.settimeout(1.0)
            self._sock.sendall(obs_ws.encode_frame(b"", opcode=OP_CLOSE))
        except OSError:
            pass  # the peer is already gone
        try:
            self._sock.close()
        except OSError:
            pass  # closing a dead socket

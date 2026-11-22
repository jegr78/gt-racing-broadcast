"""A loopback HTTP server base for the event simulation's fakes.

It binds 127.0.0.1 (port 0 picks a free one) and refuses what a web page could send:
a `Host` other than 127.0.0.1 or localhost with this port, any `Origin`, and a POST
whose Content-Type is not in `POST_TYPES`. Subclasses answer in `route()`."""
import json
import os
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
from http_server import NumericThreadingHTTPServer  # noqa: E402

MAX_BODY = 8 << 20
HANDLER_TIMEOUT_S = 10
CONTROL_PREFIX = "/_sim/"


def json_reply(obj, status=200):
    return status, "application/json", json.dumps(obj).encode()


class LoopbackServer:
    """start()/stop() or a `with` block; `route(method, path, query, headers, body)`
    returns (status, content type, bytes)."""
    POST_TYPES = ("application/json",)

    def __init__(self, port=0):
        self.requested_port = port
        self.port = None
        self._server = None

    @property
    def http_url(self):
        return f"http://127.0.0.1:{self.port}"

    def start(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            timeout = HANDLER_TIMEOUT_S
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                owner._handle(self, "GET")

            def do_POST(self):
                owner._handle(self, "POST")

        self._server = NumericThreadingHTTPServer(("127.0.0.1", self.requested_port), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self):
        return self if self._server else self.start()

    def __exit__(self, *exc):
        self.stop()

    def refusal(self, method, headers):
        """(status, reason) refusing a request a web page could have sent, or None."""
        if headers.get("Origin") is not None:
            return 403, "Forbidden"
        if (headers.get("Host") or "").lower() not in (f"127.0.0.1:{self.port}",
                                                       f"localhost:{self.port}"):
            return 403, "Forbidden"
        ctype = (headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if method == "POST" and ctype not in self.POST_TYPES:
            return 415, "Unsupported Media Type"
        return None

    def route(self, method, path, query, headers, body):
        raise NotImplementedError

    def _handle(self, req, method):
        refused = self.refusal(method, req.headers)
        raw_length = req.headers.get("Content-Length") or "0"
        if refused is None and not raw_length.isdigit():
            refused = 400, "Bad Request"
        if refused is None and int(raw_length) > MAX_BODY:
            refused = 413, "Payload Too Large"
        if refused is not None:
            self._send(req, *json_reply({"error": refused[1]}, refused[0]))
            return
        body = req.rfile.read(int(raw_length)) if method == "POST" else b""
        u = urlsplit(req.path)
        try:
            reply = self.route(method, u.path, parse_qs(u.query, keep_blank_values=True),
                               req.headers, body)
        except Exception as exc:  # noqa: BLE001  a bad request gets an answer, not a dropped socket
            reply = json_reply({"error": str(exc) or exc.__class__.__name__}, 400)
        self._send(req, *reply)

    @staticmethod
    def _send(req, status, ctype, payload):
        req.send_response(status)
        if status != 204:
            req.send_header("Content-Type", ctype)
            req.send_header("Content-Length", str(len(payload)))
        req.send_header("Cache-Control", "no-store")
        req.send_header("Connection", "close")
        req.end_headers()
        if status != 204:
            req.wfile.write(payload)
        req.close_connection = True


def write_ready_file(path, info):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(info, fh)
    os.replace(tmp, path)


def fetch(base_url, path, data=None, timeout=5.0):
    """JSON from a fake's check endpoint; POSTs `data` when given. Loopback only."""
    if urlsplit(base_url).hostname not in ("127.0.0.1", "localhost"):
        raise ValueError(f"the fakes live on loopback, not {base_url}")
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(base_url.rstrip("/") + path, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())

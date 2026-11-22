"""A loopback HTTP server base for the event simulation's fakes.

It binds 127.0.0.1 (port 0 picks a free one) and refuses what a web page could send:
a `Host` other than 127.0.0.1 or localhost with this port, any `Origin`, and a POST
whose Content-Type is not in `POST_TYPES`. Subclasses answer in `route()`.

Faults: a rule {"target", "delay", "status", "error", "times"} applies to the requests
whose `fault_keys()` contain its target. It sleeps `delay` seconds through the `sleep`
seam, then answers `fault_reply()` instead of the request when it has a status or an
error; `times` limits how many requests it hits. POST /_sim/fail sets rules (one or a
list), an empty body clears them."""
import json
import os
import time
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

    def __init__(self, port=0, faults=None, sleep=time.sleep):
        self.requested_port = port
        self.port = None
        self._server = None
        self.sleep = sleep
        self._fault_lock = threading.Lock()
        self._faults = []
        self.faults_hit = 0
        for rule in faults or []:
            self.fail(**rule)

    def fail(self, target, delay=0, status=None, error=None, times=None):
        """Add a fault rule; see the module docstring."""
        rule = fault_rule({"target": target, "delay": delay, "status": status,
                           "error": error, "times": times})
        with self._fault_lock:
            self._faults.append(rule)
        return rule

    def clear_faults(self):
        with self._fault_lock:
            self._faults = []

    def faults(self):
        with self._fault_lock:
            return [dict(r) for r in self._faults]

    def fault_keys(self, method, path, query, body):
        """The fault targets a request answers to; none by default."""
        return []

    def fault_reply(self, rule):
        """The answer for a rule with a status or an error."""
        return json_reply({"error": rule["error"] or "simulated failure"}, rule["status"] or 500)

    def _take_fault(self, keys):
        with self._fault_lock:
            for rule in self._faults:
                if rule["target"] in keys:
                    if rule["times"] is not None:
                        rule["times"] -= 1
                        if rule["times"] == 0:
                            self._faults.remove(rule)
                    self.faults_hit += 1
                    return dict(rule)
        return None

    def _fail_route(self, body):
        data = json.loads(body) if body.strip() else None
        if data is None:
            self.clear_faults()
        else:
            for rule in [fault_rule(r) for r in (data if isinstance(data, list) else [data])]:
                with self._fault_lock:
                    self._faults.append(rule)
        return json_reply({"faults": self.faults()})

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
        raw_length = req.headers.get("Content-Length") or "0"
        refused = None
        if not raw_length.isdigit():
            refused = 400, "Bad Request"
        elif int(raw_length) > MAX_BODY:
            refused = 413, "Payload Too Large"
        # A refusal still reads the body: closing on unread data resets the connection,
        # and on Windows the client then loses the answer.
        body = req.rfile.read(int(raw_length)) if refused is None else b""
        refused = refused or self.refusal(method, req.headers)
        if refused is not None:
            self._send(req, *json_reply({"error": refused[1]}, refused[0]))
            return
        u = urlsplit(req.path)
        query = parse_qs(u.query, keep_blank_values=True)
        try:
            if method == "POST" and u.path == CONTROL_PREFIX + "fail":
                reply = self._fail_route(body)
            else:
                rule = self._take_fault(self.fault_keys(method, u.path, query, body))
                if rule and rule["delay"]:
                    self.sleep(rule["delay"])
                if rule and (rule["status"] or rule["error"] is not None):
                    reply = self.fault_reply(rule)
                else:
                    reply = self.route(method, u.path, query, req.headers, body)
        except Exception as exc:  # noqa: BLE001  a bad request gets an answer, not a dropped socket
            reply = json_reply({"error": str(exc) or exc.__class__.__name__}, 400)
        self._send(req, *reply)

    @staticmethod
    def _send(req, status, ctype, payload):
        try:
            LoopbackServer._write(req, status, ctype, payload)
        except ConnectionError:
            pass  # the client hung up before the answer

    @staticmethod
    def _write(req, status, ctype, payload):
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


def fault_rule(data):
    """A validated fault rule; ValueError names what is wrong."""
    if not isinstance(data, dict) or not isinstance(data.get("target"), str) or not data["target"]:
        raise ValueError("a fault needs a target string")
    delay, status = data.get("delay") or 0, data.get("status")
    error, times = data.get("error"), data.get("times")
    if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not 0 <= delay <= 600:
        raise ValueError("delay must be 0-600 seconds")
    if status is not None and (isinstance(status, bool) or not isinstance(status, int)
                               or not 100 <= status <= 599):
        raise ValueError("status must be an HTTP status 100-599")
    if error is not None and not isinstance(error, str):
        raise ValueError("error must be a string")
    if times is not None and (isinstance(times, bool) or not isinstance(times, int) or times < 1):
        raise ValueError("times must be a positive whole number")
    return {"target": data["target"], "delay": delay, "status": status, "error": error,
            "times": times}


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

"""A loopback HTTP server base for the event simulation's fakes.

It binds 127.0.0.1 (port 0 picks a free one) and refuses what a web page could send:
a `Host` other than 127.0.0.1 or localhost with this port, any `Origin`, and a POST
whose Content-Type is not in `POST_TYPES`. Subclasses answer in `route()`.

Faults: a rule {"target", "delay", "status", "error", "times"} applies to the requests
whose `fault_keys()` contain its target. Every matching rule adds its `delay`, slept
through the `sleep` seam (by default a wait that stop() ends); the first matching rule
with a status (400-599) or an error answers `fault_reply()` in place of the request, and
rules behind it are not hit. `times` limits how many requests a rule hits. POST
/_sim/fail sets rules (one or a list), an empty body clears them.

stop() refuses new work, wakes handlers that wait in a delay and joins them, bounded."""
import json
import os
import socket
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
from http_server import NumericThreadingHTTPServer  # noqa: E402

HANDLER_TIMEOUT_S = 10
DRAIN_LIMIT = 1 << 20
DRAIN_TIMEOUT_S = 0.5
STOP_JOIN_S = 5.0
CONTROL_PREFIX = "/_sim/"


def json_reply(obj, status=200):
    return status, "application/json", json.dumps(obj).encode()


class _Server(NumericThreadingHTTPServer):
    """Starts each handler on a thread its owner can wake and join on stop()."""
    daemon_threads = True

    def __init__(self, address, handler, owner):
        self.owner = owner
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        thread = threading.Thread(target=self._run, args=(request, client_address), daemon=True)
        with self.owner._live_lock:
            self.owner._live[thread] = request
        thread.start()

    def _run(self, request, client_address):
        try:
            self.process_request_thread(request, client_address)
        finally:
            with self.owner._live_lock:
                self.owner._live.pop(threading.current_thread(), None)


class LoopbackServer:
    """start()/stop() or a `with` block; `route(method, path, query, headers, body)`
    returns (status, content type, bytes[, extra headers])."""
    POST_TYPES = ("application/json",)
    MAX_BODY = 1 << 20

    def __init__(self, port=0, faults=None, sleep=None):
        self.requested_port = port
        self.port = None
        self._server = None
        self._stopping = threading.Event()
        self.sleep = self._stopping.wait if sleep is None else sleep
        self._live_lock = threading.Lock()
        self._live = {}
        self._fault_lock = threading.Lock()
        self._faults = []
        self.faults_hit = 0
        for rule in faults or []:
            self.fail(**rule)

    def fail(self, target, delay=0, status=None, error=None, times=None):
        """Add a fault rule; see the module docstring."""
        return self._add_rules([{"target": target, "delay": delay, "status": status,
                                 "error": error, "times": times}])[0]

    def clear_faults(self):
        with self._fault_lock:
            self._faults = []

    def faults(self):
        with self._fault_lock:
            return [dict(r) for r in self._faults]

    def validate_fault(self, rule):
        """Raise ValueError for a rule this fake cannot answer faithfully."""

    def fault_keys(self, method, path, query, body):
        """The fault targets a request answers to; none by default."""
        return []

    def fault_reply(self, rule):
        """The answer for a rule with a status or an error."""
        return json_reply({"error": rule["error"] or "simulated failure"}, rule["status"] or 500)

    def _add_rules(self, rules):
        checked = [fault_rule(r) for r in rules]
        for rule in checked:
            self.validate_fault(rule)
        with self._fault_lock:
            self._faults.extend(checked)
        return [dict(r) for r in checked]

    def _take_faults(self, keys):
        """(total delay, the answering rule or None) for one request."""
        delay, answer = 0, None
        with self._fault_lock:
            for rule in list(self._faults):
                if rule["target"] not in keys:
                    continue
                self.faults_hit += 1
                if rule["times"] is not None:
                    rule["times"] -= 1
                    if rule["times"] == 0:
                        self._faults.remove(rule)
                delay += rule["delay"]
                if rule["status"] or rule["error"] is not None:
                    answer = dict(rule)
                    break
        return delay, answer

    def _fail_route(self, body):
        data = json.loads(body) if body.strip() else None
        if data is None:
            self.clear_faults()
        else:
            self._add_rules(data if isinstance(data, list) else [data])
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

        self._stopping.clear()
        self._server = _Server(("127.0.0.1", self.requested_port), Handler, self)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        """Refuse new work, wake every handler and join them, at most STOP_JOIN_S in all."""
        self._stopping.set()
        server, self._server = self._server, None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        with self._live_lock:
            live = dict(self._live)
        for request in live.values():
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # already closed
        deadline = threading.Event()
        timer = threading.Timer(STOP_JOIN_S, deadline.set)
        timer.start()
        for thread in live:
            while thread.is_alive() and not deadline.is_set():
                thread.join(0.05)
        timer.cancel()

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

    def too_large(self, length):
        """The answer for a body over MAX_BODY."""
        return json_reply({"error": "Payload Too Large"}, 413)

    @staticmethod
    def _drain(req, limit):
        """Read up to `limit` waiting bytes, so closing does not reset the connection."""
        try:
            req.connection.settimeout(DRAIN_TIMEOUT_S)
            while limit > 0:
                chunk = req.rfile.read1(min(limit, 65536))
                if not chunk:
                    return
                limit -= len(chunk)
        except OSError:
            pass  # nothing more arrives in time

    def _handle(self, req, method):
        if self._stopping.is_set():
            self._send(req, *json_reply({"error": "stopped"}, 503))
            return
        raw_length = req.headers.get("Content-Length") or "0"
        if not (raw_length.isascii() and raw_length.isdigit()):
            self._drain(req, DRAIN_LIMIT)
            self._send(req, *json_reply({"error": "Bad Request"}, 400))
            return
        length = int(raw_length)
        if length > self.MAX_BODY:
            self._drain(req, min(length, DRAIN_LIMIT))
            self._send(req, *self.too_large(length))
            return
        try:
            body = req.rfile.read(length)
        except OSError:
            return  # the client went away or stalled past the handler timeout
        if len(body) < length:
            return
        refused = self.refusal(method, req.headers)
        if refused is not None:
            self._send(req, *json_reply({"error": refused[1]}, refused[0]))
            return
        u = urlsplit(req.path)
        query = parse_qs(u.query, keep_blank_values=True)
        try:
            if method == "POST" and u.path == CONTROL_PREFIX + "fail":
                reply = self._fail_route(body)
            else:
                delay, rule = self._take_faults(self.fault_keys(method, u.path, query, body))
                if delay:
                    self.sleep(delay)
                if self._stopping.is_set():
                    reply = json_reply({"error": "stopped"}, 503)
                elif rule:
                    reply = self.fault_reply(rule)
                else:
                    reply = self.route(method, u.path, query, req.headers, body)
        except Exception as exc:  # noqa: BLE001  a bad request gets an answer, not a dropped socket
            reply = json_reply({"error": str(exc) or exc.__class__.__name__}, 400)
        self._send(req, *reply)

    @staticmethod
    def _send(req, status, ctype, payload, headers=None):
        try:
            LoopbackServer._write(req, status, ctype, payload, headers or {})
        except OSError:
            pass  # the client hung up before the answer

    @staticmethod
    def _write(req, status, ctype, payload, headers):
        req.send_response(status)
        for name, value in headers.items():
            req.send_header(name, value)
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
                               or not 400 <= status <= 599):
        raise ValueError("status must be an HTTP error status 400-599")
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


def remove_ready_file(path):
    try:
        os.remove(path)
    except OSError:
        pass  # never written or already gone


def serve_cli(server, ready_file, info, banner):
    """Run a started fake until Ctrl+C or SIGTERM: write the ready-file, print the
    banner, and on exit stop the fake and remove the ready-file."""
    import signal

    def terminate(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    try:
        if ready_file:
            write_ready_file(ready_file, info)
        print(banner, flush=True)
        threading.Event().wait()
    except KeyboardInterrupt:
        pass  # Ctrl+C ends the run
    finally:
        server.stop()
        if ready_file:
            remove_ready_file(ready_file)


def fetch(base_url, path, data=None, timeout=5.0):
    """JSON from a fake's check endpoint; POSTs `data` when given. Loopback only."""
    if urlsplit(base_url).hostname not in ("127.0.0.1", "localhost"):
        raise ValueError(f"the fakes live on loopback, not {base_url}")
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(base_url.rstrip("/") + path, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())

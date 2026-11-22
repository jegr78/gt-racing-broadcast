#!/usr/bin/env python3
"""Simulated OBS: an obs-websocket v5 server over `ObsState`, plus a JSON endpoint for checks.

One loopback port carries both. A WebSocket upgrade is an OBS client (no authentication,
events per the client's `eventSubscriptions`); any other HTTP request reads or pokes the
state. Requests must name this port on 127.0.0.1 or localhost in `Host`, carry no
`Origin` and POST `application/json`, so a web page cannot reach it:

    GET  /state                  ObsState.snapshot(); `scene` is the program scene
    GET  /events?since=N         events emitted after sequence N
    GET  /requests?since=N       requests received after sequence N
    GET  /program.png?width=W    the program screenshot
    POST /request                {"requestType", "requestData"}: an operator acting in OBS
    POST /labels                 {"Feed A": "Stint 1 Comms 1"}: text of a feed's pattern
    POST /freeze                 {"input": "Feed A", "frozen": true, "state": ...}: stop a media cursor

Point the relay at it with `RACECAST_SIM_OBS_WS=ws://127.0.0.1:<port>` in simulation
mode (`ObsSim.env()`), or with the RACECAST_OBS_WS_HOST/PORT overrides otherwise.

    python3 tools/event-sim/fakes/obs_sim.py --port 0 --ready-file sim.json

Maintainer tool, not shipped. Framing reuses accept_key and decode_frame from
src/scripts/obs_ws.py."""
import argparse
import json
import os
import queue
import socket
import struct
import sys
import threading
import time
import urllib.request
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))   # obs_ws imports its siblings
import obs_grab  # noqa: E402
import obs_state  # noqa: E402
import obs_ws  # noqa: E402

OBS_WS_ENV = "RACECAST_SIM_OBS_WS"        # simulation.OBS_WS
HANDSHAKE_TIMEOUT_S = 10
DEFAULT_GRABBER = object()
MAX_BODY = 1 << 20


def frame(payload, opcode=0x1):
    """One unmasked server->client frame; servers must not mask."""
    n = len(payload)
    if n < 126:
        head = bytes([0x80 | opcode, n])
    elif n < 1 << 16:
        head = bytes([0x80 | opcode, 126]) + struct.pack(">H", n)
    else:
        head = bytes([0x80 | opcode, 127]) + struct.pack(">Q", n)
    return head + payload


class _Closed(Exception):
    pass


class _Client:
    """One WebSocket client. A writer thread owns the socket's send side, so an event
    never waits on a slow reader while the state lock is held. A client whose queue
    fills is closed, as OBS drops a client that stops reading."""
    QUEUE_MAX = 1024

    def __init__(self, conn, state):
        self.conn, self.state = conn, state
        self.buf = b""
        self.mask = 0
        self.out = queue.Queue(self.QUEUE_MAX)
        self.writer = threading.Thread(target=self._write, daemon=True)

    def _write(self):
        while True:
            data = self.out.get()
            if data is None:
                return
            try:
                self.conn.sendall(data)
            except OSError:
                return

    def _put(self, data):
        try:
            self.out.put_nowait(data)
        except queue.Full:
            try:
                self.conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # already gone

    def send(self, obj):
        self._put(frame(json.dumps(obj).encode()))

    def on_event(self, event):
        if self.mask & event["eventIntent"]:
            self.send({"op": 5, "d": {"eventType": event["eventType"],
                                      "eventIntent": event["eventIntent"],
                                      "eventData": event["eventData"]}})

    def recv(self):
        while True:
            parsed = obs_ws.decode_frame(self.buf)
            if parsed is None:
                chunk = self.conn.recv(65536)
                if not chunk:
                    raise _Closed()
                self.buf += chunk
                continue
            opcode, payload, self.buf = parsed
            if opcode == 0x8:
                self._put(frame(payload[:2], 0x8))
                raise _Closed()
            if opcode == 0x9:
                self._put(frame(payload, 0xA))
            elif opcode == 0x1:
                return json.loads(payload)

    def _answer(self, d):
        rtype, rid = d.get("requestType"), d.get("requestId")
        try:
            resp = self.state.request(rtype, d.get("requestData") or {})
            status = {"result": True, "code": obs_state.SUCCESS}
        except obs_state.ObsError as exc:
            resp, status = None, {"result": False, "code": exc.code, "comment": exc.comment}
        out = {"requestType": rtype, "requestId": rid, "requestStatus": status}
        if resp is not None:
            out["responseData"] = resp
        return out

    def serve(self, first):
        self.writer.start()
        try:
            self.send({"op": 0, "d": {"obsWebSocketVersion": "5.5.0", "rpcVersion": 1}})
            hello = first if first is not None else self.recv()
            if hello.get("op") != 1:
                return
            subs = hello.get("d", {}).get("eventSubscriptions")
            self.mask = obs_state.ALL_EVENTS if subs is None else int(subs)
            self.state.subscribe(self.on_event)
            self.send({"op": 2, "d": {"negotiatedRpcVersion": 1}})
            self.conn.settimeout(None)
            while True:
                msg = self.recv()
                op, d = msg.get("op"), msg.get("d") or {}
                if op == 3:
                    if d.get("eventSubscriptions") is not None:
                        self.mask = int(d["eventSubscriptions"])
                    self.send({"op": 2, "d": {"negotiatedRpcVersion": 1}})
                elif op == 6:
                    self.send({"op": 7, "d": self._answer(d)})
                elif op == 8:
                    results = []
                    for req in d.get("requests") or []:
                        results.append(self._answer(req))
                        if d.get("haltOnFailure") and not results[-1]["requestStatus"]["result"]:
                            break
                    self.send({"op": 9, "d": {"requestId": d.get("requestId"), "results": results}})
        except (_Closed, OSError, ValueError):
            pass  # the client went away or sent garbage: end this session
        finally:
            self.state.unsubscribe(self.on_event)
            try:
                self.out.put(None, timeout=2)
            except queue.Full:
                pass  # the writer is stuck on a dead socket; it is a daemon
            self.writer.join(timeout=2)


class ObsSim:
    """A running simulated OBS. `collection` is a scene-collection JSON path or dict
    (None: an empty OBS with one `scene`); `strict=False` keeps the screenshot
    stand-in's lenient answers; `image` serves fixed bytes for every screenshot;
    `grabber` reads feed frames (default ffmpeg, None: always the synthetic pattern)."""

    def __init__(self, collection=obs_state.DEFAULT_COLLECTION, scene=None, strict=True,
                 image=None, port=0, clock=time.monotonic, collection_names=None, opaque=None,
                 grabber=DEFAULT_GRABBER):
        if grabber is DEFAULT_GRABBER:
            grabber = obs_grab.FeedGrabber()
        self.state = obs_state.ObsState(collection, scene=scene, strict=strict, clock=clock,
                                        collection_names=collection_names, opaque=opaque,
                                        grabber=grabber)
        if image is not None:
            self.state.set_image(image)
        self.requested_port = port
        self.port = None
        self.sock = None
        self.conns = set()
        self.lock = threading.Lock()
        self.stopping = threading.Event()

    @property
    def url(self):
        return f"ws://127.0.0.1:{self.port}"

    @property
    def http_url(self):
        return f"http://127.0.0.1:{self.port}"

    @property
    def state_url(self):
        return self.http_url + "/state"

    def env(self):
        """The simulation-mode environment that points racecast at this OBS."""
        return {OBS_WS_ENV: self.url}

    def start(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", self.requested_port))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()
        threading.Thread(target=self._ticker, daemon=True).start()
        return self

    def stop(self):
        self.stopping.set()
        if getattr(self.state.grabber, "close", None):
            self.state.grabber.close()
        with self.lock:
            socks = [self.sock] + list(self.conns)
        for s in socks:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except (OSError, AttributeError):
                pass  # never connected or already down
            try:
                s.close()
            except (OSError, AttributeError):
                pass  # already closed

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def _ticker(self):
        while not self.stopping.wait(0.05):
            self.state.tick()

    def _accept(self):
        while not self.stopping.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with self.lock:
                self.conns.add(conn)
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        try:
            conn.settimeout(HANDSHAKE_TIMEOUT_S)
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = conn.recv(4096)
                if not chunk or len(head) > 65536:
                    return
                head += chunk
            head, _, rest = head.partition(b"\r\n\r\n")
            lines = head.decode("iso-8859-1").split("\r\n")
            headers = {k.strip().lower(): v.strip() for k, _, v in
                       (line.partition(":") for line in lines[1:])}
            refused = self.refusal(lines[0].split(" ")[0], headers)
            if refused:
                conn.sendall((f"HTTP/1.1 {refused}\r\nContent-Length: 0\r\n"
                              "Connection: close\r\n\r\n").encode())
                return
            if headers.get("upgrade", "").lower() == "websocket":
                conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                              "Connection: Upgrade\r\nSec-WebSocket-Accept: "
                              + obs_ws.accept_key(headers.get("sec-websocket-key", ""))
                              + "\r\n\r\n").encode())
                client = _Client(conn, self.state)
                client.buf = rest
                client.serve(None)
            else:
                self._http(conn, lines[0], headers, rest)
        except Exception:  # noqa: BLE001  one bad client must not stop the server
            pass
        finally:
            with self.lock:
                self.conns.discard(conn)
            try:
                conn.close()
            except OSError:
                pass  # already torn down

    def refusal(self, method, headers):
        """The status line refusing a request a web page could have sent, or ""."""
        if "origin" in headers:
            return "403 Forbidden"
        if headers.get("host", "").lower() not in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            return "403 Forbidden"
        if method == "POST" and headers.get("content-type", "").split(";")[0].strip().lower() \
                != "application/json":
            return "415 Unsupported Media Type"
        return ""

    def _http(self, conn, request_line, headers, body):
        method, target = (request_line.split(" ") + ["", ""])[:2]
        length = min(int(headers.get("content-length") or 0), MAX_BODY)
        while len(body) < length:
            chunk = conn.recv(65536)
            if not chunk:
                break
            body += chunk
        try:
            status, ctype, payload = self.route(method, target, body[:length])
        except Exception as exc:  # noqa: BLE001  a bad request gets an answer, not a dropped socket
            status, ctype = "400 Bad Request", "application/json"
            payload = json.dumps({"ok": False, "code": obs_state.INVALID_FIELD,
                                  "comment": str(exc) or exc.__class__.__name__}).encode()
        conn.sendall((f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\n"
                      f"Content-Length: {len(payload)}\r\nCache-Control: no-store\r\n"
                      "Connection: close\r\n\r\n").encode() + payload)

    def route(self, method, target, body=b""):
        """(status line, content type, bytes) for one HTTP request."""
        u = urlsplit(target)
        query = parse_qs(u.query)

        def number(name, default):
            try:
                return int(query.get(name, [default])[0])
            except ValueError:
                return default

        def ok(obj):
            return "200 OK", "application/json", json.dumps(obj).encode()
        try:
            data = json.loads(body or b"{}") if method == "POST" else {}
        except ValueError:
            return "400 Bad Request", "application/json", b'{"error": "body is not JSON"}'
        st = self.state
        if method == "GET" and u.path == "/state":
            return ok(st.snapshot())
        if method == "GET" and u.path == "/events":
            return ok({"events": st.events_since(number("since", 0)), "seq": st.event_seq})
        if method == "GET" and u.path == "/requests":
            return ok({"requests": st.requests_since(number("since", 0)), "seq": st.request_seq})
        if method == "GET" and u.path == "/program.png":
            raw, fmt = st.screenshot(st.snapshot()["scene"], number("width", 640))
            return "200 OK", "image/" + ("jpeg" if fmt == "jpg" else fmt), raw
        if method == "POST" and u.path == "/request" and isinstance(data, dict):
            if not isinstance(data.get("requestType"), str) or \
                    not isinstance(data.get("requestData", {}), dict):
                return "400 Bad Request", "application/json", json.dumps(
                    {"ok": False, "code": obs_state.INVALID_FIELD,
                     "comment": "requestType must be a string and requestData an object"}).encode()
            try:
                resp = st.request(data.get("requestType"), data.get("requestData") or {},
                                  origin="http")
                return ok({"ok": True, "code": obs_state.SUCCESS, "responseData": resp})
            except obs_state.ObsError as exc:
                return ok({"ok": False, "code": exc.code, "comment": exc.comment})
        if method == "POST" and u.path == "/labels" and isinstance(data, dict):
            st.set_labels(data)
            return ok({"labels": st.snapshot()["labels"]})
        if method == "POST" and u.path == "/freeze" and isinstance(data, dict):
            st.freeze_media(str(data.get("input")), bool(data.get("frozen", True)),
                            str(data.get("state") or obs_state.PLAYING))
            return ok({"frozen": sorted(st.media_frozen)})
        return "404 Not Found", "application/json", b'{"error": "not found"}'


def fetch(base_url, path="/state", data=None, timeout=5.0):
    """JSON from a running obs-sim's endpoint; POSTs `data` when given. Loopback only."""
    if urlsplit(base_url).hostname not in ("127.0.0.1", "localhost"):
        raise ValueError(f"obs-sim lives on loopback, not {base_url}")
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(base_url.rstrip("/") + path, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def read_image(path):
    with open(path, "rb") as fh:
        return fh.read()


def build_parser():
    ap = argparse.ArgumentParser(description="Simulated OBS: obs-websocket v5 plus a state endpoint.")
    ap.add_argument("--port", type=int, default=4466, help="listen port, 0 picks a free one (default 4466)")
    ap.add_argument("--collection", default=obs_state.DEFAULT_COLLECTION,
                    help="OBS scene collection JSON to seed scenes and inputs (default: the shipped one)")
    ap.add_argument("--scene", help="initial program scene (default: the collection's)")
    ap.add_argument("--collection-name", action="append", dest="collection_names",
                    help="another name OBS lists for the collection, repeatable "
                         "(default: the demo profile's OBS_COLLECTION)")
    ap.add_argument("--image", help="serve this fixed image for every screenshot")
    ap.add_argument("--opaque", action="append",
                    help="an input that hides the feeds below it, repeatable "
                         "(default: the full-page graphics and feed covers)")
    ap.add_argument("--no-grab", action="store_true",
                    help="never read feed frames; screenshots show the synthetic pattern")
    ap.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg binary for feed frames (default: on PATH)")
    ap.add_argument("--grab-interval", type=float, default=5.0,
                    help="seconds between two reads of one feed (default 5)")
    ap.add_argument("--feed-port", type=int, action="append",
                    help="a feed port frames may be read from, repeatable (default 53001-53003)")
    ap.add_argument("--ready-file", help="write {port, url, state_url} here once listening")
    return ap


def run(sim, ready_file=None, banner=""):
    sim.start()
    info = {"port": sim.port, "url": sim.url, "state_url": sim.state_url}
    if ready_file:
        tmp = ready_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(info, fh)
        os.replace(tmp, ready_file)
    print(f"obs-sim: {banner or 'collection ' + sim.state.collection} on {sim.url}, "
          f"state {sim.state_url}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass  # Ctrl+C ends the run
    finally:
        sim.stop()


def main(argv=None):
    args = build_parser().parse_args(argv)
    image = read_image(args.image) if args.image else None
    run(ObsSim(args.collection, scene=args.scene, image=image, port=args.port,
               collection_names=args.collection_names, opaque=args.opaque,
               grabber=None if args.no_grab else obs_grab.FeedGrabber(
                   [args.ffmpeg], ports=args.feed_port or obs_ws.RELAY_PORTS,
                   interval=args.grab_interval)),
        args.ready_file)


if __name__ == "__main__":
    main()

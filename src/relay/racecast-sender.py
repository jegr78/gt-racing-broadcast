#!/usr/bin/env python3
"""Send this console's GT7 telemetry to a racecast relay.

Run it on a device on the same network as the PlayStation. It asks the console for
the base packet format (A), so a dashboard app or SimHub that already receives the
telemetry keeps working, and posts the encrypted packets unchanged to the relay.

  python3 racecast-sender.py --url https://<host>/console/drive/ingest --code CODE

The invite page of the event prints that line with your code. Until Race Control
approves the registration the sender prints that it waits and asks again every 10 s.
A maintainer probe run takes --key and --name instead of --code.

Modes:
  default     heartbeat the console, receive on UDP 33740
  --forward   also pass every packet unchanged to HOST:PORT (SimHub or GT Race
              Engineer on this PC)
  --listen    no heartbeat: read the packets another app forwards to this port (SimHub)
  --console   the PlayStation's IP address, needed on an iPhone
  --check     test heartbeat, receive and upload for 12 s, send no telemetry, then
              print OK or FAIL (exit code 0 or 1); run it before the session

A firewall that filters inbound UDP must allow 33740, or the --listen port when
another device forwards to it with --bind 0.0.0.0. The console sends from a port of
its own, so the packets do not count as a reply to the heartbeat.

Standard library only, Python 3.8 or newer.
"""
import argparse
import base64
import collections
import http.client
import io
import json
import re
import socket
import ssl
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = "1"
MAGIC = b"RCT1"
MAX_BATCH = 120           # the relay accepts two seconds of packets per post
BACKLOG = 600             # ten seconds of packets wait through an upload outage
HEARTBEAT = b"A"
HEARTBEAT_EVERY_S = 10.0
CONSOLE_PORT = 33739
RECEIVE_PORT = 33740
MIN_PACKET = 0x94         # shorter datagrams are not GT7 telemetry
MAX_PACKET = 512          # the relay refuses longer ones; GT7 sends at most 368 bytes
REFUSED_WAIT_S = 60.0     # a refused link or key is retried once a minute
PENDING_WAIT_S = 10.0     # a registration waiting for Race Control's approval
KEEPALIVE_S = 5.0         # an empty post while the console is quiet keeps the entry slot
RELATCH_S = 30.0          # a discovered console silent this long is searched again
QUIET_S = 2.0             # no packet for this long is an interruption
DISCOVERY_HINT_S = 15.0
STATUS_EVERY_S = 10.0
CHECK_S = 12.0            # below DISCOVERY_HINT_S: the check prints its own hint
CHECK_POSTS = 3
CHECK_MIN_HZ = 50.0       # GT7 sends about 60 packets per second
LATENCY_WINDOW = 200      # the posts the latency stats cover
MAX_POST_MS = 60000
USER_AGENT = "racecast-sender/" + VERSION
CONSOLE_HINT = ("start the sender with --console <PlayStation IP>; Settings, Network, Connection status "
                "on the PlayStation shows it")
ROUTE_PROBE = "192.0.2.1"   # TEST-NET-1: picks the default-route interface, never contacted
NAME = re.compile(r"[A-Za-z0-9 _.\-]{1,32}\Z")   # the relay accepts these names only


def encode_batch(packets):
    """The relay's batch format, version 1: MAGIC, then per packet a big-endian uint16
    length and the packet bytes as received."""
    return MAGIC + b"".join(struct.pack(">H", len(p)) + p for p in packets)


def default_bind(console=None, listen=None):
    """The local address to receive on: loopback in listen mode (the other app forwards
    on this device), else the interface that reaches the console, or the default-route
    interface while the console is unknown. None when no interface can be found. Never
    all interfaces: the sender has no business on a VPN or a second network."""
    if listen is not None:
        return "127.0.0.1"
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((console or ROUTE_PROBE, CONSOLE_PORT))   # UDP connect sends nothing
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


REFUSED = {   # what the driver does about a refusal; the sender retries once a minute
    401: "Check the link and the code",
    403: "Register again on the invite page",
    404: "Check the link: the event may not run yet",
    409: "Someone else sends for this entry: ask the producer",
    429: "Retrying in a minute",
}


def _reason(exc):
    """The relay's error text from a refused post, or the HTTP reason."""
    try:
        return json.loads(exc.read().decode("utf-8", "replace"))["error"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return exc.reason or "refused"


class Connection:
    """One kept-open connection to the relay, called like urllib's urlopen for a POST.
    Never follows a redirect: a reply of 300 or more raises HTTPError. After an error
    the next call connects again. A post on a reused connection that the relay closed
    in the meantime goes out once more on a new one; a timeout is never repeated."""

    _CLOSED = (http.client.RemoteDisconnected, BrokenPipeError, ConnectionResetError,
               ConnectionAbortedError, ssl.SSLEOFError)

    def __init__(self):
        self._conn = None
        self._target = None
        self._tls = ssl.create_default_context()
        self._lock = threading.Lock()

    def _new(self, scheme, host, port, timeout):
        """A connection to the relay. When getproxies() names a proxy for the scheme and
        proxy_bypass() does not exempt host:port, it runs through a CONNECT tunnel to the
        relay, for http too; TLS inside the tunnel checks the relay's name. A port left
        out is the scheme's, for the relay and for the proxy, as urlopen does."""
        port = port or (443 if scheme == "https" else 80)
        proxy = urllib.request.getproxies().get(scheme)
        netloc = ("[%s]" % host if ":" in host else host) + ":%d" % port
        if proxy and urllib.request.proxy_bypass(netloc):
            proxy = None
        target = (host, port)
        if proxy:
            via = urllib.parse.urlsplit(proxy if "://" in proxy else "http://" + proxy)
            target = (via.hostname, via.port or (443 if scheme == "https" else 80))
        if scheme == "https":
            conn = http.client.HTTPSConnection(*target, timeout=timeout, context=self._tls)
        else:
            conn = http.client.HTTPConnection(*target, timeout=timeout)
        if proxy:
            auth = {}
            if via.username is not None:
                login = "%s:%s" % (urllib.parse.unquote(via.username),
                                   urllib.parse.unquote(via.password or ""))
                auth["Proxy-Authorization"] = "Basic " + base64.b64encode(login.encode()).decode()
            conn.set_tunnel(host, port, headers=auth)
        return conn

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __call__(self, req, timeout):
        url = urllib.parse.urlsplit(req.full_url)
        if url.scheme not in ("http", "https") or not url.hostname:
            raise ValueError("the relay URL must start with http:// or https://")
        target = (url.scheme, url.hostname, url.port)
        path = url.path + ("?" + url.query if url.query else "")
        with self._lock:
            if target != self._target:
                self.close()
                self._target = target
            again = self._conn is not None
            while True:
                if self._conn is None:
                    self._conn = self._new(url.scheme, url.hostname, url.port, timeout)
                try:
                    self._conn.request("POST", path, body=req.data, headers=dict(req.header_items()))
                    resp = self._conn.getresponse()
                    body = resp.read()
                except self._CLOSED:
                    self.close()
                    if again:
                        again = False
                        continue
                    raise
                except BaseException:
                    self.close()             # never reuse a connection a request stopped half-way
                    raise
                if resp.will_close:
                    self.close()
                if resp.status >= 300:
                    raise urllib.error.HTTPError(req.full_url, resp.status, resp.reason,
                                                 resp.headers, io.BytesIO(body))
                return io.BytesIO(body)


def flush(up, poster, wait=10.0):
    """Post what is still queued once the poster has stopped. False, and nothing posted,
    while it still runs: a post beside it could send a batch twice."""
    poster.join(wait)
    if poster.is_alive():
        return False
    deadline = time.monotonic() + wait
    while up.pending() and up._failing_since is None and time.monotonic() < deadline:
        up.post_once()
    return True


def _say(line):
    print(time.strftime("%H:%M:%S ") + line, flush=True)


class Uploader:
    """Posts queued packets to the relay at the rate it asks for. Thread-safe."""

    def __init__(self, url, key=None, name=None, rate_hz=20.0, opener=None, say=_say,
                 clock=time.monotonic, code=None):
        if code:
            self.url, self._auth = url, {"X-Driver-Code": code}
        else:
            self.url = url + ("&" if "?" in url else "?") + "sender=" + urllib.parse.quote(name)
            self._auth = {"X-Probe-Key": key}
        self.rate_hz = rate_hz
        self._open = opener or Connection()
        self._say = say
        self._clock = clock
        self._lock = threading.Lock()
        self._queue = collections.deque()        # (sequence, packet)
        self._seq = 0
        self._inflight = None                    # last sequence of the post under way
        self._evicted_inflight = 0
        self.stats = dict.fromkeys(("posted", "dropped", "post_failed", "rejected"), 0)
        self.extra_stats = lambda: {}
        self._failing_since = None
        self._waiting = False
        self._failures = 0                       # consecutive, for the backoff
        self._last_post = clock()
        self.latencies = collections.deque(maxlen=2000)

    def put(self, packet):
        with self._lock:
            if len(self._queue) >= BACKLOG:
                seq, _packet = self._queue.popleft()
                if self._inflight is not None and seq <= self._inflight:
                    self._evicted_inflight += 1  # still delivered, unless that post fails
                else:
                    self.stats["dropped"] += 1
            self._seq += 1
            self._queue.append((self._seq, packet))

    def _remove_through(self, last):
        """Drop the queued packets up to sequence `last`, whatever put() evicted since."""
        with self._lock:
            while self._queue and self._queue[0][0] <= last:
                self._queue.popleft()

    def pending(self):
        with self._lock:
            return len(self._queue)

    def post_once(self):
        """Post up to MAX_BATCH queued packets; returns the seconds until the next try."""
        with self._lock:
            batch = [self._queue[i] for i in range(min(MAX_BATCH, len(self._queue)))]
            if batch:
                self._inflight, self._evicted_inflight = batch[-1][0], 0
        if not batch and self._clock() - self._last_post < KEEPALIVE_S:
            return 1.0 / self.rate_hz
        last = batch[-1][0] if batch else None
        req = self._request([p for _s, p in batch])
        started = time.perf_counter()        # monotonic ticks in ~15 ms steps on Windows
        try:
            with self._open(req, timeout=5) as resp:
                reply = json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            if exc.code == 400:              # this batch can never be accepted: drop it
                self._settle(delivered=True)     # counted as rejected below, not as dropped
                self._remove_through(last)
                self.stats["rejected"] += len(batch)
                return self._failed("the relay refused a batch (400)", wait=1.0 / self.rate_hz)
            if exc.code == 410:              # this code never works again
                self._failed(f"the relay answered 410 ({_reason(exc)}). Start the sender again "
                             "with the new code", repeat=True)
                return None
            if exc.code == 423:              # the relay takes nothing before the approval
                self._settle(delivered=True)
                if last is not None:
                    self._remove_through(last)
                if not self._waiting:
                    self._waiting = True
                    self._say("%s. Trying again every %.0f s" % (_reason(exc), PENDING_WAIT_S))
                return PENDING_WAIT_S
            if exc.code in REFUSED:
                return self._failed(f"the relay answered {exc.code} ({_reason(exc)}). "
                                    + REFUSED[exc.code], wait=REFUSED_WAIT_S, repeat=True)
            return self._failed(f"the relay answered {exc.code}")
        except (OSError, ValueError, http.client.HTTPException) as exc:
            return self._failed(f"cannot reach the relay ({exc})")
        self.latencies.append(time.perf_counter() - started)
        self._last_post = self._clock()
        self._settle(delivered=True)
        if last is not None:
            self._remove_through(last)
        self._failures = 0
        with self._lock:
            self.stats["posted"] += 1
        if self._waiting:
            self._waiting = False
            self._say("approved: sending telemetry")
        if self._failing_since is not None:
            self._say("upload works again after %.0f s" % (self._clock() - self._failing_since))
            self._failing_since = None
        rate = reply.get("rate_hz") if isinstance(reply, dict) else None
        if isinstance(rate, (int, float)) and 1 <= rate <= 60:
            self.rate_hz = float(rate)
        return 1.0 / self.rate_hz

    def _request(self, packets):
        stats = dict(self.stats, **self.extra_stats(), **self._latency_stats())
        return urllib.request.Request(self.url, data=encode_batch(packets), method="POST", headers={
            "Content-Type": "application/octet-stream", "User-Agent": USER_AGENT,
            "X-Sender-Stats": json.dumps(stats), **self._auth})

    def _latency_stats(self):
        recent = sorted(list(self.latencies)[-LATENCY_WINDOW:])
        if not recent:
            return {}
        def ms(q):
            return min(MAX_POST_MS, max(0, round(recent[min(len(recent) - 1, int(q * len(recent)))] * 1000)))
        return {"post_ms_p50": ms(0.5), "post_ms_p95": ms(0.95)}

    def check(self, posts=CHECK_POSTS):
        """Post `posts` empty batches: (ok, what the driver reads, median seconds or None)."""
        took, verdict = [], None
        for _ in range(posts):
            started = time.perf_counter()
            try:
                with self._open(self._request([]), timeout=5) as resp:
                    resp.read()
                verdict = (True, "approved, the broadcast takes your telemetry")
            except urllib.error.HTTPError as exc:
                if exc.code == 423:
                    verdict = (True, "registered, waiting for approval by Race Control. "
                                     "Start the sender anyway; it waits")
                else:
                    hint = REFUSED.get(exc.code)
                    return False, f"the relay answered {exc.code} ({_reason(exc)})" + \
                        (f". {hint}" if hint else ""), None
            except (OSError, ValueError, http.client.HTTPException) as exc:
                return False, f"cannot reach the relay ({exc}). Check the link and this device's " \
                              "internet connection", None
            took.append(time.perf_counter() - started)
        took.sort()
        return verdict[0], verdict[1], took[len(took) // 2]

    def _settle(self, delivered):
        with self._lock:
            if not delivered:
                self.stats["dropped"] += self._evicted_inflight
            self._inflight, self._evicted_inflight = None, 0

    def _failed(self, why, wait=None, repeat=False):
        self._settle(delivered=False)
        self.stats["post_failed"] += 1
        self._failures += 1
        if self._failing_since is None or repeat:
            self._failing_since = self._failing_since or self._clock()
            self._say("upload interrupted: " + why + ("" if repeat else ". Retrying"))
        return wait if wait is not None else min(5.0, 0.5 * 2 ** min(self._failures, 4))

    def run(self, stop):
        while not stop.is_set():
            wait = self.post_once()
            if wait is None:
                stop.set()                   # the relay dropped this code for good
                return
            stop.wait(wait)


class Sender:
    """Receives the console's packets and hands them to the uploader."""

    def __init__(self, uploader, console=None, listen=None, forward=None, bind=None,
                 console_port=CONSOLE_PORT, receive_port=RECEIVE_PORT, say=_say,
                 clock=time.monotonic):
        self.up = uploader
        self.console = console
        self._pinned = console is not None        # --console: never searched again
        self.listen = listen
        self.forward = forward
        self.console_port = console_port
        self.say = say
        self.clock = clock
        port = listen if listen is not None else receive_port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if listen is None:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            # Windows lets a second program bind the same UDP port unless it is exclusive.
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        bind = bind or default_bind(console, listen)
        if not bind:
            raise OSError("no network interface found: pass --bind <this device's LAN address>")
        self.sock.bind((bind, port))
        self.bind_addr = bind
        self._swept = False              # a search went address by address
        self.sock.settimeout(0.5)
        self.fwd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) if forward else None
        self.stats = dict.fromkeys(("received", "forwarded", "interruptions", "heartbeats",
                                    "oversized"), 0)
        uploader.extra_stats = lambda: dict(self.stats)
        self._last_rx = None
        self._quiet = False
        self.heartbeat_failed = 0
        self.first_rx = None            # (clock, received) at the first packet
        self._search_hinted = False

    def heartbeat(self):
        target = self.console or "255.255.255.255"
        try:
            self.sock.sendto(HEARTBEAT, (target, self.console_port))
            self.stats["heartbeats"] += 1
            return
        except OSError as exc:
            if self.console:
                self.heartbeat_failed += 1
                self.say(f"heartbeat to {target} failed: {exc}")
                return
            error = exc
        # iOS refuses the broadcast but lets unicast through: this round goes address by address.
        if not self._swept:
            self.say(f"the broadcast failed ({error}): searching the local network address by address")
        self._swept = True
        self._heartbeat_each(error)

    def _heartbeat_each(self, error):
        """One heartbeat to every other address of the bound interface's /24."""
        net, _, own = self.bind_addr.rpartition(".")
        if self.bind_addr == "0.0.0.0" or self.bind_addr.startswith("127."):
            net = None
        sent = 0
        for host in range(1, 255) if net else ():
            if str(host) == own:
                continue
            try:
                self.sock.sendto(HEARTBEAT, (f"{net}.{host}", self.console_port))
                sent += 1
            except OSError as exc:
                error = exc
        if sent:
            self.stats["heartbeats"] += 1
            return
        self.heartbeat_failed += 1
        hint = ""
        if not self._search_hinted:
            self._search_hinted = True
            hint = ". " + CONSOLE_HINT
        where = f"no address of {net}.0/24 took a heartbeat" if net else "the search needs the device's network address"
        self.say(f"{where}: {error}{hint}")

    def receive_once(self):
        try:
            data, addr = self.sock.recvfrom(4096)
        except socket.timeout:
            return False
        except ConnectionResetError:
            return False  # Windows reports an ICMP port unreachable of a heartbeat here
        except OSError as exc:
            if getattr(exc, "winerror", None) == 10040:   # a datagram over 4096 bytes
                self.stats["oversized"] += 1
                return False
            raise
        if len(data) < MIN_PACKET:
            return False
        if len(data) > MAX_PACKET:
            self.stats["oversized"] += 1
            return False
        if self.listen is None:
            if self.console is None:
                self.console = addr[0]
                self.say(f"found the console at {addr[0]}")
            elif addr[0] != self.console:
                return False
        now = self.clock()
        if self._quiet:
            self.say("packets again after %.1f s" % (now - self._last_rx))
            self._quiet = False
        self._last_rx = now
        self.stats["received"] += 1
        self.up.put(data)
        if self.fwd is not None:
            try:
                self.fwd.sendto(data, self.forward)
                self.stats["forwarded"] += 1
            except OSError:
                pass  # the local app is not listening; the relay still gets everything
        return True

    def check_relatch(self):
        """Search the console again when a discovered one stays silent (new IP)."""
        if (self.listen is None and not self._pinned and self.console is not None
                and self._last_rx is not None and self.clock() - self._last_rx >= RELATCH_S):
            self.say(f"no packets from {self.console} for {RELATCH_S:.0f} s: searching again")
            self.console = None
            self._last_rx = None
            self._quiet = False

    def check_quiet(self):
        if self._last_rx is not None and not self._quiet and self.clock() - self._last_rx >= QUIET_S:
            self._quiet = True
            self.stats["interruptions"] += 1
            self.say("no packets from the console for %.0f s (menu, replay or network?)" % QUIET_S)

    def run(self, stop, duration=None, status=True):
        started = self.clock()
        next_beat, next_status = started, started + STATUS_EVERY_S
        hinted = False
        rx_mark = (started, 0)
        while not stop.is_set():
            now = self.clock()
            if duration is not None and now - started >= duration:
                break
            if self.listen is None and now >= next_beat:
                self.heartbeat()
                next_beat = now + HEARTBEAT_EVERY_S
            if (self.listen is None and not hinted and self._last_rx is None
                    and now - started >= DISCOVERY_HINT_S):
                hinted = True
                self.say("no console found: pass --console <PlayStation IP> and allow "
                         "inbound UDP %d in the firewall" % self.sock.getsockname()[1])
            if self.receive_once() and self.first_rx is None:
                self.first_rx = (self.clock(), self.stats["received"])
            self.check_quiet()
            self.check_relatch()
            if status and now >= next_status:
                rate = (self.stats["received"] - rx_mark[1]) / (now - rx_mark[0])
                self.say("received %.1f/s, posted %d, dropped %d, upload failures %d" % (
                    rate, self.up.stats["posted"], self.up.stats["dropped"],
                    self.up.stats["post_failed"]))
                rx_mark = (now, self.stats["received"])
                next_status = now + STATUS_EVERY_S
        stop.set()


class _CheckSink:
    """Takes the packets of a check in place of the uploader: counted, never sent."""

    def __init__(self):
        self.sizes = collections.Counter()

    def put(self, packet):
        self.sizes[len(packet)] += 1


def self_check(sender, up, duration=CHECK_S, say=_say):
    """Heartbeat and receive for `duration` seconds, then post empty batches. Prints one
    verdict per part and returns the exit code: 0 when no part failed, else 1."""
    sink = _CheckSink()
    sender.up = sink
    say("checking for %.0f s: heartbeat, receive and upload, no telemetry is sent" % duration)
    sender.run(threading.Event(), duration=duration, status=False)
    ended = sender.clock()
    port = sender.sock.getsockname()[1]
    results = []
    if sender.listen is not None:
        results.append(("heartbeat", "skipped", "listening on UDP %d for another app's packets" % port))
    elif sender.stats["heartbeats"]:
        results.append(("heartbeat", "OK", "%d sent to %s" % (
            sender.stats["heartbeats"], sender.console or ("the local network, address by address"
                                                           if sender._swept else "the local network (broadcast)"))))
    else:
        results.append(("heartbeat", "FAIL", "%d failed: pass --console <PlayStation IP>. %s" % (
            sender.heartbeat_failed, CONSOLE_HINT)))
    received = sender.stats["received"]
    first_t, first_n = sender.first_rx or (ended, received)
    span = ended - first_t
    rate = (received - first_n) / span if span > 0 else 0.0     # from the first packet on
    if not received:
        fix = ("check that the other app forwards to UDP %d" % port if sender.listen is not None else
               "start a session in GT7 (on track or in the pits), pass --console <PlayStation IP> "
               "and allow inbound UDP %d in the firewall" % port)
        results.append(("receive", "FAIL", "no packets in %.0f s: %s" % (duration, fix)))
    else:
        where = " from %s" % sender.console if sender.console else ""
        sizes = ", ".join("%d bytes" % n for n, _c in sink.sizes.most_common(2))
        low = "" if rate >= CHECK_MIN_HZ else " (GT7 sends about 60/s: a weak Wi-Fi or a busy device?)"
        results.append(("receive", "OK" if rate >= CHECK_MIN_HZ else "FAIL",
                        "%d packets%s, %.1f/s, %s%s" % (received, where, rate, sizes, low)))
    ok, text, took = up.check()
    results.append(("relay", "OK" if ok else "FAIL",
                    text + ("" if took is None else ", post %.0f ms" % (took * 1000))))
    for n, (part, verdict, text) in enumerate(results, 1):
        say("check %d/%d %s: %s %s" % (n, len(results), part, verdict, text))
    failed = [part for part, verdict, _t in results if verdict == "FAIL"]
    say("RESULT: OK, ready for the session" if not failed else "RESULT: FAIL (%s)" % ", ".join(failed))
    return 1 if failed else 0


def _hostport(value):
    host, _, port = value.rpartition(":")
    try:
        return (host or "127.0.0.1", int(port))
    except ValueError:
        raise argparse.ArgumentTypeError("use HOST:PORT") from None


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass  # an older or replaced stream keeps its settings
    parser = argparse.ArgumentParser(description="Send GT7 telemetry to a racecast relay.")
    parser.add_argument("--url", required=True, help="ingest URL from the invite page")
    parser.add_argument("--code", help="your code from the invite page")
    parser.add_argument("--key", help="maintainer probe only: the probe key")
    parser.add_argument("--name", help="maintainer probe only: up to 32 ASCII letters, "
                                       "digits, spaces, _ . -")
    parser.add_argument("--console", help="PlayStation IP: needed on an iPhone, elsewhere "
                                          "when discovery does not find it")
    parser.add_argument("--forward", type=_hostport, metavar="HOST:PORT",
                        help="also pass every packet to this local app (SimHub, "
                             "GT Race Engineer)")
    parser.add_argument("--listen", type=int, metavar="PORT",
                        help="no heartbeat: read packets another app forwards to PORT")
    parser.add_argument("--bind", help="local address to receive on (default: the interface "
                                       "that reaches the console; 127.0.0.1 with --listen)")
    parser.add_argument("--receive-port", type=int, default=RECEIVE_PORT, help=argparse.SUPPRESS)
    parser.add_argument("--console-port", type=int, default=CONSOLE_PORT, help=argparse.SUPPRESS)
    parser.add_argument("--duration", type=float, help="stop after this many seconds")
    parser.add_argument("--check", action="store_true",
                        help="test heartbeat, receive and upload for %.0f s (or --duration), "
                             "send no telemetry, print OK or FAIL" % CHECK_S)
    args = parser.parse_args(argv)
    if args.listen is not None and args.forward is not None:
        parser.error("--listen and --forward do not combine: the other app already receives")
    if args.code:
        if args.key or args.name:
            parser.error("--code already names you: leave out --key and --name")
        up = Uploader(args.url, code=args.code)
    else:
        if not args.key or not args.name:
            parser.error("pass --code from the invite page (or --key and --name for a probe)")
        if not NAME.match(args.name):
            parser.error("--name takes up to 32 ASCII letters, digits, spaces, _ . -")
        up = Uploader(args.url, args.key, args.name)
    try:
        sender = Sender(up, console=args.console, listen=args.listen, forward=args.forward,
                        bind=args.bind, console_port=args.console_port,
                        receive_port=args.receive_port)
    except OSError as exc:
        port = args.listen if args.listen is not None else args.receive_port
        _say(f"cannot receive on UDP {port}: {exc}. Another app on this device may own "
             "the port: use --listen with its forwarding, or run the sender on another device")
        if args.check:
            _say("RESULT: FAIL (receive)")
            return 1
        return 2
    if args.check:
        try:
            return self_check(sender, up, duration=args.duration or CHECK_S)
        except KeyboardInterrupt:
            return 1
        finally:
            sender.sock.close()
    stop = threading.Event()
    poster = threading.Thread(target=up.run, args=(stop,), daemon=True)
    poster.start()
    _say("sending %sto %s%s" % ("as %r " % args.name if args.name else "", args.url,
                                     " (listening, no heartbeat)" if args.listen is not None else ""))
    try:
        sender.run(stop, duration=args.duration)
    except KeyboardInterrupt:
        stop.set()
    try:
        if not flush(up, poster):
            _say("the last upload still runs: packets still queued are not sent")
    except KeyboardInterrupt:
        pass  # a second Ctrl+C skips the flush
    _say("stopped: received %d, posted %d batches, dropped %d" % (
        sender.stats["received"], up.stats["posted"], up.stats["dropped"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

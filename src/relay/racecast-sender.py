#!/usr/bin/env python3
"""Send this console's GT7 telemetry to a racecast relay.

Run it on a device on the same network as the PlayStation. It asks the console for
the base packet format (A), so a dashboard app or SimHub that already receives the
telemetry keeps working, and posts the encrypted packets unchanged to the relay.

  python3 racecast-sender.py --url https://<host>/console/drive/ingest --code CODE

The invite page of the event prints that line with your code. A maintainer probe
run takes --key and --name instead of --code.

Modes:
  default     heartbeat the console, receive on UDP 33740
  --forward   also pass every packet unchanged to HOST:PORT (SimHub on this PC)
  --listen    no heartbeat: read the packets another app forwards to this port

The firewall must allow inbound UDP 33740 (or the --listen port): the console sends
from a port of its own, so the packets do not count as a reply to the heartbeat.

Standard library only, Python 3.8 or newer.
"""
import argparse
import collections
import json
import re
import socket
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
MAX_PACKET = 512          # the relay refuses longer ones; GT7 sends at most 344 bytes
REFUSED_WAIT_S = 60.0     # a refused link or key is retried once a minute
KEEPALIVE_S = 5.0         # an empty post while the console is quiet keeps the entry slot
RELATCH_S = 30.0          # a discovered console silent this long is searched again
QUIET_S = 2.0             # no packet for this long is an interruption
DISCOVERY_HINT_S = 15.0
STATUS_EVERY_S = 10.0
USER_AGENT = "racecast-sender/" + VERSION
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
    410: "Register again on the invite page",
    429: "Retrying in a minute",
}


def _reason(exc):
    """The relay's error text from a refused post, or the HTTP reason."""
    try:
        return json.loads(exc.read().decode("utf-8", "replace"))["error"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return exc.reason or "refused"


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
        self._open = opener or urllib.request.urlopen
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
        stats = dict(self.stats, **self.extra_stats())
        req = urllib.request.Request(self.url, data=encode_batch([p for _s, p in batch]),
                                     method="POST", headers={
            "Content-Type": "application/octet-stream", "User-Agent": USER_AGENT,
            "X-Sender-Stats": json.dumps(stats), **self._auth})
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
            if exc.code in REFUSED:
                return self._failed(f"the relay answered {exc.code} ({_reason(exc)}). "
                                    + REFUSED[exc.code], wait=REFUSED_WAIT_S, repeat=True)
            return self._failed(f"the relay answered {exc.code}")
        except (OSError, ValueError) as exc:
            return self._failed(f"cannot reach the relay ({exc})")
        self.latencies.append(time.perf_counter() - started)
        self._last_post = self._clock()
        self._settle(delivered=True)
        if last is not None:
            self._remove_through(last)
        self._failures = 0
        with self._lock:
            self.stats["posted"] += 1
        if self._failing_since is not None:
            self._say("upload works again after %.0f s" % (self._clock() - self._failing_since))
            self._failing_since = None
        rate = reply.get("rate_hz") if isinstance(reply, dict) else None
        if isinstance(rate, (int, float)) and 1 <= rate <= 60:
            self.rate_hz = float(rate)
        return 1.0 / self.rate_hz

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
            stop.wait(self.post_once())


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
        bind = bind or default_bind(console, listen)
        if not bind:
            raise OSError("no network interface found: pass --bind <this device's LAN address>")
        self.sock.bind((bind, port))
        self.sock.settimeout(0.5)
        self.fwd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) if forward else None
        self.stats = dict.fromkeys(("received", "forwarded", "interruptions", "heartbeats",
                                    "oversized"), 0)
        uploader.extra_stats = lambda: dict(self.stats)
        self._last_rx = None
        self._quiet = False

    def heartbeat(self):
        target = self.console or "255.255.255.255"
        try:
            self.sock.sendto(HEARTBEAT, (target, self.console_port))
            self.stats["heartbeats"] += 1
        except OSError as exc:
            self.say(f"heartbeat to {target} failed: {exc}")

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

    def run(self, stop, duration=None):
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
            self.receive_once()
            self.check_quiet()
            self.check_relatch()
            if now >= next_status:
                rate = (self.stats["received"] - rx_mark[1]) / (now - rx_mark[0])
                self.say("received %.1f/s, posted %d, dropped %d, upload failures %d" % (
                    rate, self.up.stats["posted"], self.up.stats["dropped"],
                    self.up.stats["post_failed"]))
                rx_mark = (now, self.stats["received"])
                next_status = now + STATUS_EVERY_S
        stop.set()


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
    parser.add_argument("--console", help="PlayStation IP when discovery does not find it")
    parser.add_argument("--forward", type=_hostport, metavar="HOST:PORT",
                        help="also pass every packet to this local app (SimHub)")
    parser.add_argument("--listen", type=int, metavar="PORT",
                        help="no heartbeat: read packets another app forwards to PORT")
    parser.add_argument("--bind", help="local address to receive on (default: the interface "
                                       "that reaches the console; 127.0.0.1 with --listen)")
    parser.add_argument("--receive-port", type=int, default=RECEIVE_PORT, help=argparse.SUPPRESS)
    parser.add_argument("--console-port", type=int, default=CONSOLE_PORT, help=argparse.SUPPRESS)
    parser.add_argument("--duration", type=float, help="stop after this many seconds")
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
        return 2
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
        poster.join(10)                                 # never post one batch twice
        deadline = time.monotonic() + 10
        while up.pending() and up._failing_since is None and time.monotonic() < deadline:
            up.post_once()                              # flush what is still queued
    except KeyboardInterrupt:
        pass  # a second Ctrl+C skips the flush
    _say("stopped: received %d, posted %d batches, dropped %d" % (
        sender.stats["received"], up.stats["posted"], up.stats["dropped"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

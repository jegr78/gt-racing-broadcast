#!/usr/bin/env python3
"""A simulated PlayStation for the driver telemetry probe (#1110).

Maintainer tool. It answers GT7 heartbeats like the console and replays a racecast
`.gt7rec` recording to every device that heartbeated within the last 16 seconds,
encrypted again in the format that device asked for (A, B or ~). Like the real
console it sends from a port of its own, not from 33739. The probe's sender and
the relay's own listener can then be tested without a console.

    python3 tools/gt7-sim-ps5.py runtime/<profile>/telemetry-recordings/<file>.gt7rec
    python3 tools/gt7-sim-ps5.py REC --bind 127.0.0.1 --port 33739 --speed 2 --loop
"""
import argparse
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import gt7_crypto
import gt7_recording
import logsetup

ALIVE_S = 16.0          # a receiver without a heartbeat for this long gets nothing
KINDS = {b"A": "A", b"B": "B", b"~": "~"}


class Console:
    """Heartbeat bookkeeping and the replay loop; the sockets are injected."""

    def __init__(self, packets, send, clock=time.monotonic, reply_port=33740):
        self.packets = packets          # [(relative_s, plain)]
        self.send = send                # callable(bytes, (host, port))
        self.clock = clock
        self.reply_port = reply_port
        self.receivers = {}             # host -> (kind, last heartbeat)
        self.sent = 0
        self._lock = threading.Lock()

    def heartbeat(self, data, addr):
        kind = KINDS.get(bytes(data[:1]))
        if kind is None:
            return False
        now = self.clock()
        with self._lock:
            known = self.receivers.get(addr[0])
            # A running stream keeps its format, like GT7; after a lapse the new one counts.
            running = known is not None and now - known[1] <= ALIVE_S
            self.receivers[addr[0]] = ((known[0] if running else kind), now)
        return True

    def emit(self, plain):
        now = self.clock()
        with self._lock:
            for host in [h for h, (_k, seen) in self.receivers.items() if now - seen > ALIVE_S]:
                del self.receivers[host]
            targets = list(self.receivers.items())
        iv = int.from_bytes(plain[0x70:0x74], "little")
        for host, (kind, _seen) in targets:
            try:
                self.send(gt7_crypto.encrypt(plain, kind, iv=iv), (host, self.reply_port))
                self.sent += 1
            except OSError:
                pass  # a receiver that went away (Windows reports its ICMP here)

    def run(self, stop, speed=1.0, loop=False, sleep=time.sleep):
        while not stop.is_set():
            started = self.clock()
            for rel, plain in self.packets:
                wait = started + rel / speed - self.clock()
                if wait > 0:
                    sleep(wait)
                if stop.is_set():
                    return
                self.emit(plain)
            if not loop:
                return


def load(path):
    rec = gt7_recording.Recording(path)
    out, first = [], None
    for wall_ts, _kind, plain in rec.packets():
        first = wall_ts if first is None else first
        out.append((wall_ts - first, plain))
    return out


def main(argv=None):
    logsetup.harden_stdio()
    ap = argparse.ArgumentParser(description="Answer GT7 heartbeats and replay a recording.")
    ap.add_argument("recording", help=".gt7rec file to replay")
    ap.add_argument("--bind", default="0.0.0.0", help="address to receive heartbeats on")
    ap.add_argument("--port", type=int, default=33739, help="heartbeat port")
    ap.add_argument("--reply-port", type=int, default=33740, help="port the receivers listen on")
    ap.add_argument("--speed", type=float, default=1.0, help="replay speed")
    ap.add_argument("--loop", action="store_true", help="start over at the end")
    args = ap.parse_args(argv)
    import socket
    packets = load(args.recording)
    if not packets:
        sys.exit("the recording holds no packets")
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind((args.bind, args.port))
    rx.settimeout(0.5)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)   # its own port, like the console
    console = Console(packets, tx.sendto, reply_port=args.reply_port)
    stop = threading.Event()

    def listen():
        while not stop.is_set():
            try:
                data, addr = rx.recvfrom(64)
            except OSError:
                continue
            if console.heartbeat(data, addr) and console.sent == 0:
                print(f"heartbeat from {addr[0]}", flush=True)

    threading.Thread(target=listen, daemon=True).start()
    print(f"simulated PS5 on UDP {args.port}: {len(packets)} packets, "
          f"{packets[-1][0] / args.speed:.0f} s per pass", flush=True)
    try:
        console.run(stop, speed=args.speed, loop=args.loop)
    except KeyboardInterrupt:
        pass  # Ctrl+C ends the replay; the summary below still prints
    stop.set()
    print(f"sent {console.sent} packets", flush=True)


if __name__ == "__main__":
    main()

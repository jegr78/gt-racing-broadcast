#!/usr/bin/env python3
"""Standalone GT7 telemetry probe (maintainer, not shipped). Heartbeat, decrypt and
field dump against a live PS4/PS5, the way to validate the real packet path and the
struct offsets. Mirrors tools/broadcast-chat-probe.py.

    python3 tools/gt7-telemetry-probe.py                       # auto-discover the console
    python3 tools/gt7-telemetry-probe.py --ps-ip 192.168.1.42  # explicit IP
    python3 tools/gt7-telemetry-probe.py --capture caps.hex -n 20   # save 20 raw packets

The console IP is auto-discovered by default with a limited broadcast heartbeat,
latching the first responder, so no IP is needed on a flat home LAN. --capture writes
each RAW encrypted packet as one hex line, so a real packet can be baked into a CI
fixture that validates the field offsets against reality, not just the wiring.
"""
import argparse
import contextlib
import importlib.util
import os
import socket
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


crypto = _load("gt7_crypto", ("src", "scripts", "gt7_crypto.py"))
tm = _load("gt7_telemetry", ("src", "scripts", "gt7_telemetry.py"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ps-ip", default=None,
                    help="PS4/PS5 IP. Omit to auto-discover the console via broadcast.")
    ap.add_argument("--capture", default=None, metavar="FILE",
                    help="append each raw encrypted packet as one hex line (for a CI fixture)")
    ap.add_argument("-n", "--count", type=int, default=0,
                    help="stop after N decoded packets (0 = run until Ctrl-C)")
    args = ap.parse_args()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("0.0.0.0", 33740)); sock.settimeout(2.0)
    dest = args.ps_ip
    last = 0.0
    seen = 0
    print(f"listening on 33740 ({dest or 'auto-discovery'}); Ctrl-C to stop")
    with contextlib.ExitStack() as stack:
        stack.enter_context(sock)   # close the socket on Ctrl-C or error
        cap = (stack.enter_context(open(args.capture, "a", encoding="utf-8"))
               if args.capture else None)
        while True:
            now = time.monotonic()
            if now - last >= 10:
                sock.sendto(b"A", (dest or "255.255.255.255", 33739)); last = now
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                print("… no packet (is GT7 in a session? heartbeat sent)"); continue
            if dest is None:
                dest = addr[0]; print("console discovered:", dest)
            plain = crypto.decrypt_packet(data)
            if plain is None:
                print("undecryptable/foreign packet"); continue
            p = tm.parse_packet(plain)
            print(f"lap {p.lap} spd {p.speed_mps*3.6:5.1f} km/h "
                  f"tyres {tuple(round(t) for t in p.tyre_temp)} "
                  f"thr {p.throttle} brk {p.brake} fuel {p.fuel_level:.1f} "
                  f"on_track={p.on_track} paused={p.paused}")
            if cap is not None:
                cap.write(data.hex() + "\n"); cap.flush()
                print(f"  captured {seen + 1}" + (f"/{args.count}" if args.count else ""))
            seen += 1
            if args.count and seen >= args.count:
                break
    if args.capture:
        print("capture written to", args.capture)


if __name__ == "__main__":
    main()

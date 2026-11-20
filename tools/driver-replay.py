#!/usr/bin/env python3
"""Maintainer tool (not shipped): simulated driver senders for a running relay.

Registers up to 15 cars on the event's driver invite, approves them on the tailnet
route and posts encrypted GT7 packets
along the picked layout's racing line through the real ingest route, with lap times,
best laps and fuel. Use it to see the driver dashboard, the desks and the cockpit move
without a PS5, and with --gap to check that a dense pack on the track map stays
readable.

  python3 tools/driver-replay.py --profile demo --track 6e378b --cars 15 --gap 4

The relay must run with the event's invite (racecast event invite --new) and the
same track picked (racecast event track ID). Never run it against a league during an
event: its senders take real entries and its laps reach the real race, fastest lap
included. Any profile but demo needs --force.
"""
import argparse
import bisect
import json
import math
import os
import struct
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "src", "scripts")]
import gt7_crypto  # noqa: E402
import gt7_telemetry  # noqa: E402
import gt7_tracks  # noqa: E402
import telemetry_probe  # noqa: E402

NAMES = ("Lena Hart", "Marco Vidal", "Sam Okafor", "Iris Nakamura", "Tom Becker", "Ana Ruiz",
         "Jonas Weber", "Mia Laine", "Luca Bianchi", "Noah Kim", "Eva Novak", "Ole Strand",
         "Rui Costa", "Kai Brandt", "Zoe Martin")
WAITING = ("Finn Olsen", "Aya Mori", "Leo Brandt")
TICK_S = 0.1
PACKETS_PER_TICK = 6        # one sample per batch at the probe's SAMPLE_EVERY


def packet(pid, lap, speed, x, z, last_ms, best_ms, fuel):
    """One encrypted GT7 'A' packet with the fields the race engine reads."""
    b = bytearray(0x128)
    struct.pack_into("<I", b, gt7_telemetry.OFF_MAGIC, 0x47375330)
    struct.pack_into("<f", b, gt7_telemetry.OFF_FUEL_LEVEL, fuel)
    struct.pack_into("<f", b, gt7_telemetry.OFF_FUEL_CAP, 60.0)
    struct.pack_into("<f", b, gt7_telemetry.OFF_SPEED, speed)
    struct.pack_into("<h", b, gt7_telemetry.OFF_LAP, lap)
    struct.pack_into("<i", b, gt7_telemetry.OFF_BEST_MS, best_ms)
    struct.pack_into("<i", b, gt7_telemetry.OFF_LAST_MS, last_ms)
    struct.pack_into("<H", b, gt7_telemetry.OFF_FLAGS, gt7_telemetry.FLAG_ON_TRACK)
    struct.pack_into("<3f", b, gt7_telemetry.OFF_POS, x, 0.0, z)
    struct.pack_into("<i", b, 0x70, pid)
    return gt7_crypto.encrypt(bytes(b), "A", iv=pid)


class Line:
    """Position along the layout's reference line, wrapping at its length."""

    def __init__(self, trace):
        self.trace = trace
        self.length = trace[-1]["d"]
        self.ds = [p["d"] for p in trace]

    def at(self, s):
        s %= self.length
        i = max(1, bisect.bisect_left(self.ds, s))
        a, b = self.trace[i - 1], self.trace[i]
        u = (s - a["d"]) / ((b["d"] - a["d"]) or 1)
        return a["x"] + u * (b["x"] - a["x"]), a["z"] + u * (b["z"] - a["z"])


def request(base, path, body=None, headers=None):
    req = urllib.request.Request(base + path, data=body, headers=headers or {},
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read() or b"{}")


def approve(base, name):
    """Approve the waiting registration of `name` on the relay's tailnet route."""
    for r in request(base, "/drivers/requests")["requests"]:
        if r["name"] == name and r["status"] == "pending":
            request(base, "/drivers/requests/approve", json.dumps({"conn": r["conn"]}).encode(),
                    {"Content-Type": "application/json"})


def main():
    ap = argparse.ArgumentParser(description="Simulated driver senders for a running relay.")
    ap.add_argument("--profile", default="demo", help="profile whose runtime holds the driver invite")
    ap.add_argument("--track", required=True, help="GT7 layout id the relay has picked")
    ap.add_argument("--cars", type=int, default=15, help="number of cars, 1 to 15")
    ap.add_argument("--gap", type=float, default=22.0, help="metres between cars at the start")
    ap.add_argument("--seconds", type=float, default=600.0, help="how long to drive")
    ap.add_argument("--relay", default="http://127.0.0.1:8088", help="relay base URL")
    ap.add_argument("--pending", type=int, default=0,
                    help="register this many more drivers and leave them waiting for approval")
    ap.add_argument("--force", action="store_true", help="allow a profile other than demo")
    args = ap.parse_args()
    if args.profile != "demo" and not args.force:
        ap.error("this registers fake drivers on real entries; pass --force for a profile other than demo")
    if not 1 <= args.cars <= len(NAMES):
        ap.error(f"--cars is 1 to {len(NAMES)}")
    with open(os.path.join(ROOT, "runtime", args.profile, "driver-invite.json"), encoding="utf-8") as fh:
        invite = json.load(fh)["id"]
    trace = gt7_tracks.TrackDB.load(os.path.join(ROOT, "runtime"),
                                    os.path.join(ROOT, "src", "assets", "gt7")).reference_trace(args.track)
    if not trace:
        sys.exit(f"no racing line for {args.track}: run racecast gt7-data update")
    line = Line(trace)
    data = request(args.relay, f"/console/drive/data?i={invite}")
    segment = data["segments"][0] if data["segments"] else None
    entries = data["entries"][:args.cars]
    if len(entries) < args.cars:
        print(f"the roster has {len(entries)} entries; driving {len(entries)} cars")
    cars = []
    for k, entry in enumerate(entries):
        body = {"entry": entry, "name": NAMES[k]} | ({"segment": segment} if segment else {})
        code = request(args.relay, f"/console/drive/register?i={invite}", json.dumps(body).encode(),
                       {"Content-Type": "application/json"})["code"]
        approve(args.relay, NAMES[k])
        cars.append({"code": code, "along": -20.0 - args.gap * k, "v": 100.0 - 1.2 * k - (k % 3) * 0.4,
                     "pid": 0, "lap": None, "line_t": None, "last": -1, "best": -1, "fuel": 60.0 - k})
    later = data["segments"][1] if len(data["segments"]) > 1 else segment    # the next stint's drivers
    for k, entry in enumerate(data["entries"][:args.pending]):
        body = {"entry": entry, "name": WAITING[k % len(WAITING)]} | ({"segment": later} if later else {})
        request(args.relay, f"/console/drive/register?i={invite}", json.dumps(body).encode(),
                {"Content-Type": "application/json"})
    end = time.monotonic() + args.seconds
    while time.monotonic() < end:
        now = time.monotonic()
        for c in cars:
            c["along"] += c["v"] * TICK_S
            c["fuel"] = max(0.0, c["fuel"] - 0.004)
            lap = max(0, math.floor(c["along"] / line.length) + 1)
            if c["lap"] is not None and lap != c["lap"]:
                if c["line_t"] is not None:
                    c["last"] = round((now - c["line_t"]) * 1000)
                    c["best"] = c["last"] if c["best"] < 0 else min(c["best"], c["last"])
                c["line_t"] = now
            c["lap"] = lap
            x, z = line.at(c["along"])
            batch = []
            for _ in range(PACKETS_PER_TICK):
                c["pid"] += 1
                batch.append(packet(c["pid"], lap, c["v"], x, z, c["last"], c["best"], c["fuel"]))
            try:
                request(args.relay, "/console/drive/ingest", telemetry_probe.encode_batch(batch),
                        {"X-Driver-Code": c["code"]})
            except OSError as exc:
                print(f"ingest failed: {exc}", file=sys.stderr)
        time.sleep(max(0.0, TICK_S - (time.monotonic() - now)))


if __name__ == "__main__":
    main()

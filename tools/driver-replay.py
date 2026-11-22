#!/usr/bin/env python3
"""Maintainer tool (not shipped): simulated driver senders for a running relay.

Registers up to 15 cars on the event's driver invite, approves them on the tailnet
route and posts encrypted GT7 packets
along the picked layout's racing line through the real ingest route, with lap times,
best laps and fuel. Use it to see the driver dashboard, the desks and the cockpit move
without a PS5, and with --gap to check that a dense pack on the track map stays
readable. The cars and senders are the event simulation's (tools/event-sim/drive.py).

  python3 tools/driver-replay.py --profile demo --track 6e378b --cars 15 --gap 4

The relay must run with the event's invite (racecast event invite --new) and the
same track picked (racecast event track ID). Never run it against a league during an
event: its senders take real entries and its laps reach the real race, fastest lap
included. Any profile but demo needs --force.
"""
import argparse
import http.client
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "src", "scripts"), os.path.join(ROOT, "tools", "event-sim")]
import drive  # noqa: E402
import gt7_tracks  # noqa: E402

NAMES = ("Lena Hart", "Marco Vidal", "Sam Okafor", "Iris Nakamura", "Tom Becker", "Ana Ruiz",
         "Jonas Weber", "Mia Laine", "Luca Bianchi", "Noah Kim", "Eva Novak", "Ole Strand",
         "Rui Costa", "Kai Brandt", "Zoe Martin")
WAITING = ("Finn Olsen", "Aya Mori", "Leo Brandt")
TICK_S = 0.1
PACE_MPS = 99.4
packet, Line = drive.packet, drive.Line


def approve(client, name):
    """Approve the waiting registration of `name` on the relay's tailnet route."""
    for r in client.call("GET", "/drivers/requests")[1].get("requests") or []:
        if r["name"] == name and r["status"] == "pending":
            client.call("POST", "/drivers/requests/approve", json.dumps({"conn": r["conn"]}).encode(),
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
    client = drive.Client(args.relay, loopback_only=False)
    try:
        data = client.data(invite)
        segments = data.get("segments") or []
        entries = (data.get("entries") or [])[:args.cars]
        if len(entries) < args.cars:
            print(f"the roster has {len(entries)} entries; driving {len(entries)} cars")
        field = drive.Field(trace, client.ingest, pace_mps=PACE_MPS, gap_m=args.gap)
        for k, entry in enumerate(entries):
            field.add(entry, [NAMES[k]])
            field.set_code(entry, 0, client.register(invite, entry, NAMES[k], segments[0] if segments else None))
            approve(client, NAMES[k])
        later = segments[1] if len(segments) > 1 else (segments[0] if segments else None)
        for k, entry in enumerate((data.get("entries") or [])[:args.pending]):   # the next stint's drivers
            client.register(invite, entry, WAITING[k % len(WAITING)], later)
    except (drive.DriveError, OSError, http.client.HTTPException) as exc:
        sys.exit(f"driver-replay: {exc}")
    field.start()
    drive.Runner(field, tick_s=TICK_S).loop(until=time.monotonic() + args.seconds)
    if field.errors:
        print(f"{field.errors} telemetry posts were refused or failed", file=sys.stderr)


if __name__ == "__main__":
    main()

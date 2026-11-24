#!/usr/bin/env python3
"""Maintainer tool (not shipped): play a race recording against a league service.

Sends each recording sample as the contract's live state (POST /v1/live) through the
relay's own client (src/scripts/league_service.py), paced like the relay (one call per
2 s of race time, `--speed` faster), and prints the widget values the service answers.
A league builds and checks its endpoints against a real race history this way, without
a race running.

  LEAGUE_SERVICE_KEY=... python3 tools/league-replay.py runtime/<profile>/race-recording-X.sqlite \\
      --url https://league.example/racecast --ref MATCH-12 --race leg-1 --speed 10

The key comes from LEAGUE_SERVICE_KEY or --key-file, never from the command line. The
URL must be https; RACECAST_SIMULATION=1 also takes the loopback fake
(tools/event-sim/fakes/league_sim.py). A recording keeps no team, number or lap time,
so those fields stay empty, and its time gaps go out as gap_s and interval_s.
The file is opened read-only.
"""
import argparse
import json
import os
import pathlib
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import league_service  # noqa: E402
import logsetup  # noqa: E402

KEY_ENV = "LEAGUE_SERVICE_KEY"
ROW_KEYS = ("entry", "driver", "laps", "lapped", "status", "pit_stops", "finished", "class", "class_pos")


def samples(path):
    """(t, doc) of every recording sample, oldest first."""
    uri = pathlib.Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        return [(t, json.loads(doc)) for t, doc in conn.execute("SELECT t, doc FROM samples ORDER BY t")]
    finally:
        conn.close()


def snapshot(doc):
    """A dashboard-like snapshot of one recording sample."""
    rows = []
    for i, r in enumerate(doc.get("rows") or (), 1):
        if not isinstance(r, dict):
            continue
        row = {k: r[k] for k in ROW_KEYS if r.get(k) is not None}
        row["pos"] = r.get("pos") if isinstance(r.get("pos"), int) else i
        row["gap_s"] = None if r.get("lapped") else r.get("gap_time_s")
        row["interval_s"] = r.get("interval_time_s")
        rows.append(row)
    leader = max((r.get("laps") or 0 for r in rows), default=0)
    return {"mode": "race", "state": doc.get("state"), "red_flag": bool(doc.get("red_flag")),
            "lap": leader + 1 if rows else None, "entries": rows}


def paced(items, tick_s):
    """The samples at least `tick_s` of race time apart, the last one always included."""
    out, last = [], None
    for t, doc in items:
        if last is None or t - last >= tick_s:
            out.append((t, doc))
            last = t
    if items and out[-1][0] != items[-1][0]:
        out.append(items[-1])
    return out


def _key(args):
    if args.key_file:
        with open(args.key_file, encoding="utf-8") as fh:
            return fh.read().strip()
    return os.environ.get(KEY_ENV, "").strip()


def _summary(values):
    parts = []
    for wid, v in sorted(values.items()):
        if "values" in v:
            parts.append(wid + ": " + ", ".join(f"{x['label']} {x['value']}" for x in v["values"]))
        if "rows" in v:
            top = v["rows"][:3]
            parts.append(wid + ": " + ", ".join(f"{x['rank']}. {x['name']} {x['value']}" for x in top))
    return " | ".join(parts) or "(no widget values)"


def build_parser():
    ap = argparse.ArgumentParser(description="Play a race recording as live state against a league service.")
    ap.add_argument("recording", help="a race-recording-*.sqlite file")
    ap.add_argument("--url", required=True, help="the service's base URL (LEAGUE_SERVICE_URL)")
    ap.add_argument("--ref", required=True, help="the event reference the service showed")
    ap.add_argument("--race", required=True, help="the race id from the event's list")
    ap.add_argument("--speed", type=float, default=1.0, help="replay speed, 1 = race time (default)")
    ap.add_argument("--limit", type=int, default=0, help="send at most N states (0 = all)")
    ap.add_argument("--key-file", help="read the key from this file instead of " + KEY_ENV)
    ap.add_argument("--dry-run", action="store_true", help="print the first state, send nothing")
    return ap


def main(argv=None, sleep=time.sleep, out=sys.stdout):
    logsetup.harden_stdio()
    args = build_parser().parse_args(argv)
    if args.speed <= 0:
        print("--speed must be above 0", file=out)
        return 2
    if not os.path.isfile(args.recording):
        print(f"no such recording: {args.recording}", file=out)
        return 2
    items = paced(samples(args.recording), league_service.TICK_S)
    if args.limit:
        items = items[:args.limit]
    if not items:
        print("the recording holds no samples", file=out)
        return 2
    if args.dry_run:
        print(json.dumps(league_service.live_state(args.ref, args.race, snapshot(items[0][1])), indent=1),
              file=out)
        return 0
    cfg, problem = league_service.settings(args.url, _key(args))
    if cfg is None:
        print(problem or f"set {KEY_ENV} or --key-file", file=out)
        return 2
    failed = 0
    for i, (t, doc) in enumerate(items):
        if i:
            sleep((t - items[i - 1][0]) / args.speed)
        body = league_service.live_state(args.ref, args.race, snapshot(doc))
        try:
            values = league_service.post_live(cfg, body)
        except league_service.ServiceError as e:
            failed += 1
            print(f"{i + 1}/{len(items)} {doc.get('state')}: {e}", file=out)
            continue
        print(f"{i + 1}/{len(items)} {doc.get('state')}: {_summary(values)}", file=out)
    print(f"sent {len(items)} states, {failed} failed", file=out)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

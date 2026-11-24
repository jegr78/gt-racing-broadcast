#!/usr/bin/env python3
"""Driver telemetry probe stage 2 (#1116), the producer's side of the lobby test.
Maintainer tool; the runbook is docs/superpowers/plans/2026-11-24-telemetry-probe-stage2-runbook.md.

    python3 tools/probe-stage2.py kit --out KIT          # one setup sheet per platform from the invite
    python3 tools/probe-stage2.py watch --out RESULTS    # live table per sender, writes RESULTS/
    python3 tools/probe-stage2.py mark --out RESULTS "block 3 change"
    python3 tools/probe-stage2.py collect --out RESULTS --runtime runtime/<profile>
    python3 tools/probe-stage2.py report RESULTS         # RESULTS/report.md and analysis.json

The relay is read on its tailnet routes (default http://127.0.0.1:8088). Run it on the
producer machine. Nothing is written to the relay.
"""
import argparse
import glob
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import logsetup  # noqa: E402
import probe_stage2 as ps  # noqa: E402

RELAY = "http://127.0.0.1:8088"
USER_AGENT = "racecast-probe-stage2/1"


def fetcher(relay, timeout=3.0):
    """GET `relay + path` as JSON; None for an error answer or no answer."""
    def fetch(path):
        req = urllib.request.Request(relay.rstrip("/") + path, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.HTTPError):
            return None
    return fetch


def _get_bytes(relay, path, timeout=30.0):
    req = urllib.request.Request(relay.rstrip("/") + path, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def cmd_kit(args, fetch):
    invite = fetch("/drivers/invite") or {}
    if not invite.get("active") or not invite.get("id"):
        print("No event invite: start the event, then `racecast event invite --new`.")
        return 1
    base = (args.base or invite.get("base") or "").rstrip("/")
    if not base:
        print("The relay knows no Funnel address: pass --base https://<host>.")
        return 1
    invite_url = f"{base}/console/drive?i={invite['id']}"
    script_url = f"{base}/console/drive/sender.py?i={invite['id']}"
    os.makedirs(args.out, exist_ok=True)
    for platform in ps.PLATFORMS:
        with open(os.path.join(args.out, platform + ".md"), "w", encoding="utf-8") as fh:
            fh.write(ps.kit(invite_url, script_url, platform))
    print(f"Wrote {len(ps.PLATFORMS)} setup sheets to {args.out}. They carry the event's invite "
          "link: send them to the test drivers only, and write them again after a new invite.")
    return 0


def cmd_watch(args, fetch):
    mon = ps.Monitor(args.out, fetch)
    deadline = None if args.duration is None else time.time() + args.duration
    print(f"Writing {os.path.join(args.out, ps.POLL_FILE)}; Ctrl+C ends the watch.")
    try:
        mon.run(lambda: deadline is not None and time.time() >= deadline, interval=args.interval)
    except KeyboardInterrupt:
        pass  # the operator ends the watch; the summary below still prints
    print(f"{mon.polls} polls written.")
    return 0


def cmd_mark(args, fetch):
    rec = ps.mark(args.out, " ".join(args.text))
    print(f"{time.strftime('%H:%M:%S', time.localtime(rec['t']))} {rec['text']}")
    return 0


def cmd_collect(args, fetch):
    os.makedirs(args.out, exist_ok=True)
    probe = fetch("/telemetry/probe")
    if isinstance(probe, dict) and probe.get("senders") is not None:
        ps.Monitor(args.out, fetch).snapshot_probe(probe, "final")
    try:
        with open(os.path.join(args.out, "race-recording.csv"), "wb") as fh:
            fh.write(_get_bytes(args.relay, "/drivers/race/recording/csv"))
        print("race-recording.csv saved.")
    except (OSError, urllib.error.HTTPError) as exc:
        print(f"race-recording.csv: {exc}")
    if args.runtime:
        audit = os.path.join(args.runtime, "driver-race-audit.jsonl")
        if os.path.isfile(audit):
            shutil.copy2(audit, args.out)
        recordings = sorted(glob.glob(os.path.join(args.runtime, "race-recording-*.sqlite")),
                            key=os.path.getmtime)
        if recordings:
            shutil.copy2(recordings[-1], args.out)
        print(f"copied {'the audit log, ' if os.path.isfile(audit) else ''}"
              f"{os.path.basename(recordings[-1]) if recordings else 'no race recording'}")
    return 0


def cmd_report(args, fetch):
    polls, marks = ps.load(args.dir)
    if not polls:
        print(f"{args.dir} holds no {ps.POLL_FILE}.")
        return 1
    a = ps.analyse(polls, marks)
    with open(os.path.join(args.dir, "analysis.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(a, facts=ps.facts(a)), fh, indent=1, default=sorted)
    with open(os.path.join(args.dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(ps.report_md(a))
    print(f"Wrote {os.path.join(args.dir, 'report.md')} and analysis.json: {json.dumps(ps.facts(a))}")
    return 0


def main(argv=None, fetch=None):
    logsetup.harden_stdio()
    ap = argparse.ArgumentParser(description="Probe stage 2: setup sheets, live watch, report.")
    ap.add_argument("--relay", default=RELAY, help="the relay's control URL on this machine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("kit", help="one setup sheet per platform from the event's invite")
    k.add_argument("--out", required=True)
    k.add_argument("--base", help="public base URL when the relay knows no Funnel address")
    w = sub.add_parser("watch", help="poll the relay, print a table per sender, write the results")
    w.add_argument("--out", required=True)
    w.add_argument("--interval", type=float, default=ps.POLL_S)
    w.add_argument("--duration", type=float, help="stop after this many seconds")
    m = sub.add_parser("mark", help="note a block start or an observation in the timeline")
    m.add_argument("--out", required=True)
    m.add_argument("text", nargs="+")
    c = sub.add_parser("collect", help="save the race recording, the audit log and a probe snapshot")
    c.add_argument("--out", required=True)
    c.add_argument("--runtime", help="the profile's runtime folder, e.g. runtime/<profile>")
    r = sub.add_parser("report", help="turn a results folder into report.md and analysis.json")
    r.add_argument("dir")
    args = ap.parse_args(argv)
    fetch = fetch or fetcher(args.relay)
    return {"kit": cmd_kit, "watch": cmd_watch, "mark": cmd_mark, "collect": cmd_collect,
            "report": cmd_report}[args.cmd](args, fetch)


if __name__ == "__main__":
    sys.exit(main())

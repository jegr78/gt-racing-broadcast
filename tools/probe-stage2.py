#!/usr/bin/env python3
"""Driver telemetry probe stage 2 (#1116), the producer's side of the lobby test.
Maintainer tool; the runbook is docs/superpowers/plans/2026-11-24-telemetry-probe-stage2-runbook.md.

    python3 tools/probe-stage2.py kit --out KIT          # one setup sheet per platform from the invite
    python3 tools/probe-stage2.py watch --out RESULTS    # live table per sender, writes RESULTS/
    python3 tools/probe-stage2.py mark --out RESULTS "block 3 change"
    python3 tools/probe-stage2.py race-reset --out RESULTS   # start the race over between blocks
    python3 tools/probe-stage2.py collect --out RESULTS [--runtime runtime/<profile>]
    python3 tools/probe-stage2.py save-check --out RESULTS LABEL FILE   # a driver's --check output
    python3 tools/probe-stage2.py report RESULTS         # RESULTS/report.md and analysis.json

The relay is read on its tailnet routes (default http://127.0.0.1:8088). Run it on the
producer machine. Only `race-reset` changes the relay: `POST /drivers/race/reset`, a
tailnet route no desk offers.
"""
import argparse
import glob
import json
import os
import re
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


def poster(relay, timeout=10.0):
    """POST JSON to `relay + path`: (status, decoded reply)."""
    def post(path, body):
        req = urllib.request.Request(relay.rstrip("/") + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode("utf-8") or "{}")
            except ValueError:
                return exc.code, {}
        except (OSError, ValueError) as exc:
            return None, {"error": str(exc)}
    return post


def cmd_kit(args, fetch):
    invite = fetch("/drivers/invite") or {}
    if not invite.get("active") or not invite.get("id"):
        print("No event invite: `racecast event start` creates it.")
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
    mon = ps.Monitor(args.out, fetch, say=lambda line: print(line, flush=True))
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


def cmd_race_reset(args, fetch, post):
    status, reply = post("/drivers/race/reset", {})
    if status != 200:
        print(f"The race was not reset: {reply.get('error') or status}")
        return 1
    rec = ps.mark(args.out, ps.RESET_MARK)
    print(f"{time.strftime('%H:%M:%S', time.localtime(rec['t']))} race reset, state {reply.get('state')}")
    return 0


def cmd_collect(args, fetch, get_bytes):
    """The relay's reads first; with --runtime, after `event stop`, the files the relay wrote."""
    os.makedirs(args.out, exist_ok=True)
    probe = fetch("/telemetry/probe")
    if isinstance(probe, dict) and probe.get("senders") is not None:
        ps.Monitor(args.out, fetch).snapshot_probe(probe, "final")
    private = os.path.join(args.out, ps.PRIVATE_DIR)
    try:
        body = get_bytes("/drivers/race/recording/csv")
        os.makedirs(private, exist_ok=True)
        with open(os.path.join(private, "race-recording.csv"), "wb") as fh:
            fh.write(body)
        print("private/race-recording.csv saved: it names the drivers.")
    except (OSError, urllib.error.HTTPError) as exc:
        print(f"race-recording.csv: {exc}")
    if args.runtime:
        files = [os.path.join(args.runtime, "driver-race-audit.jsonl")]
        recordings = sorted(glob.glob(os.path.join(args.runtime, "race-recording-*.sqlite")),
                            key=os.path.getmtime)
        if recordings:
            files += [recordings[-1] + tail for tail in ("", "-wal", "-shm")]
        files = [f for f in files if os.path.isfile(f)]
        if files:
            os.makedirs(private, exist_ok=True)
        for f in files:
            shutil.copy2(f, private)
        print(f"copied {len(files)} files to {private}: the audit log and the recording carry "
              "connection ids and crew names, so keep private/ out of a public issue")
    return 0


LAN_ADDRESS = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
DRIVER_CODE = re.compile(r"D1:\S+")


def cmd_save_check(args, fetch):
    """A driver's check output into checks/<label>.txt without addresses or a code."""
    with open(args.file, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    text = DRIVER_CODE.sub("<code>", LAN_ADDRESS.sub("<LAN address>", text))
    folder = os.path.join(args.out, "checks")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, args.label + ".txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"saved {path}")
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


def _label(value):
    if not re.fullmatch(r"[A-Za-z0-9 _.\-]{1,40}", value):
        raise argparse.ArgumentTypeError("letters, digits, spaces, _ . - only")
    return value


def main(argv=None, fetch=None, post=None, get_bytes=None):
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
    x = sub.add_parser("race-reset", help="start the relay's race over (between blocks) and mark it")
    x.add_argument("--out", required=True)
    c = sub.add_parser("collect", help="save the race recording, the audit log and a probe snapshot")
    c.add_argument("--out", required=True)
    c.add_argument("--runtime", help="the profile's runtime folder, e.g. runtime/<profile>")
    v = sub.add_parser("save-check", help="save a driver's --check output without addresses or the code")
    v.add_argument("--out", required=True)
    v.add_argument("label", type=_label, help="a name for the file, e.g. the platform")
    v.add_argument("file", help="the pasted output")
    r = sub.add_parser("report", help="turn a results folder into report.md and analysis.json")
    r.add_argument("dir")
    args = ap.parse_args(argv)
    fetch = fetch or fetcher(args.relay)
    if args.cmd == "race-reset":
        return cmd_race_reset(args, fetch, post or poster(args.relay))
    if args.cmd == "collect":
        return cmd_collect(args, fetch, get_bytes or (lambda path: _get_bytes(args.relay, path)))
    return {"kit": cmd_kit, "watch": cmd_watch, "mark": cmd_mark, "report": cmd_report,
            "save-check": cmd_save_check}[args.cmd](args, fetch)


if __name__ == "__main__":
    sys.exit(main())

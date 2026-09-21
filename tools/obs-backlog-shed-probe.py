#!/usr/bin/env python3
"""Prove the automatic backlog shed on a REAL OBS backlog — maintainer, NOT shipped.

The shed's loop was only ever seen against a synthetic consumer
(`tools/slow-consumer-probe.py`), which is not OBS, so a stand-down there was
correct behaviour and proved nothing about the remedy. This probe puts the real
OBS into the state the shed exists for and watches the whole loop: detect,
decide, rebuild, recover.

The lever is SIGSTOP on the OBS process. A stopped OBS keeps its feed socket open
and stops draining it, so the relay's `sendall` blocks, the consumer's accepted
position freezes and `consumer_backlog` grows in real time: measured 2.9 s -> 28.4 s
over a 45 s freeze on this machine.

MEASURED, and it is the point of the probe: SIGCONT heals it on its own. OBS reads
the socket greedily, not at playback rate, so it sprints back to the trailing mark
within one sample (28.4 s -> 2.6 s in under 10 s) and the shed has nothing to do.
A transient OBS stall is therefore NOT what the shed is for. What it is for is a
consumer that stays slower than real time, which this lever cannot simulate: use
`--drift` to watch the organic backlog instead.

Needs a LIVE source (a VOD races ahead through the ring and is not a valid test),
a running OBS whose current scene shows Feed A, and no active OBS output.

    python3 tools/obs-backlog-shed-probe.py --source https://www.youtube.com/@LofiGirl/live
"""
import argparse
import http.server
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import e2e_checks  # noqa: E402
import obs_ws  # noqa: E402

RELAY = "http://127.0.0.1:8088"
SAMPLE_S = 5.0


def serve_schedule(url):
    """A loopback CSV server holding one stint on *url*. Returns (base_url, httpd)."""
    body = e2e_checks.build_schedule_csv([(url, "Probe", "1")]).encode("utf-8")

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_port}/schedule.csv", httpd


def status():
    try:
        with urllib.request.urlopen(RELAY + "/status", timeout=4) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:                      # noqa: BLE001 — a probe never dies on this
        return {}


def feed_a(st):
    return ((st.get("feeds") or {}).get("A")) or {}


def obs_pid():
    for name in ("OBS", "obs64", "obs"):
        try:
            out = subprocess.run(["pgrep", "-x", name], capture_output=True,
                                 text=True, errors="replace").stdout.strip()
        except OSError:
            continue
        if out:
            return int(out.split()[0])
    return None


def obs_output_active():
    sess, _why = obs_ws._connect(None, None, None, 3.0)
    if sess is None:
        return None
    for req in ("GetStreamStatus", "GetRecordStatus"):
        r = sess.request(req, {}) or {}
        if r.get("outputActive"):
            return True
    return False


def wait_for(predicate, timeout_s, label):
    """Poll `predicate()` until truthy. Returns its value, or None on timeout."""
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        v = predicate()
        if v:
            return v
        time.sleep(1.0)
    print(f"  TIMEOUT waiting for {label}")
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", required=True, help="a LIVE YouTube/Twitch URL")
    ap.add_argument("--freeze", type=float, default=75.0,
                    help="seconds to hold OBS stopped (default 75)")
    ap.add_argument("--relay-log", default="/tmp/shed-probe-relay.log")
    ap.add_argument("--watch", type=float, default=240.0,
                    help="seconds to watch for the shed after SIGCONT")
    args = ap.parse_args()

    pid = obs_pid()
    if pid is None:
        sys.exit("OBS is not running — this probe measures the real consumer.")
    active = obs_output_active()
    if active is None:
        sys.exit("OBS WebSocket not reachable; the probe needs it to read the rebuild.")
    if active:
        sys.exit("an OBS output is active — refusing to freeze OBS while it streams/records.")
    print(f"OBS pid {pid}, no active output.")

    csv_url, httpd = serve_schedule(args.source)
    env = dict(os.environ, RACECAST_MANUAL_FEED_ARM="0")
    with open(args.relay_log, "w", encoding="utf-8", errors="replace") as log:
        print(f"relay output -> {args.relay_log}")
        relay = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "src", "relay", "racecast-feeds.py"),
             "--sheet-csv-url", csv_url, "--bind", "127.0.0.1", "--no-hud"],
            env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            print("waiting for Feed A to serve ...")
            if not wait_for(lambda: feed_a(status()).get("state") == "serving", 180, "Feed A"):
                return 2
            prebuffer = status().get("feed_prebuffer_s")
            print(f"  serving; prebuffer {prebuffer} s")

            print("waiting for OBS to attach (its scene must show Feed A) ...")
            if not wait_for(lambda: feed_a(status()).get("backlog_s") is not None, 90, "a consumer"):
                print("  no consumer on Feed A: switch OBS to the Stint scene and retry.")
                return 2
            base = feed_a(status()).get("backlog_s")
            print(f"  attached; backlog {base:.1f} s")

            print(f"\nSIGSTOP OBS for {args.freeze:.0f} s — it stops draining the socket.")
            os.kill(pid, signal.SIGSTOP)
            try:
                t0 = time.monotonic()
                while time.monotonic() - t0 < args.freeze:
                    time.sleep(SAMPLE_S)
                    b = feed_a(status()).get("backlog_s")
                    print(f"  +{time.monotonic()-t0:5.0f}s  backlog {b}")
            finally:
                os.kill(pid, signal.SIGCONT)
            print("SIGCONT — OBS is alive and behind live.\n")

            print(f"{'t':>6}  {'backlog':>8}  {'flagged':>7}  stood_down")
            t0, fired, low = time.monotonic(), None, None
            while time.monotonic() - t0 < args.watch:
                st = status()
                f = feed_a(st)
                b, flagged = f.get("backlog_s"), f.get("backlogged")
                stood = (st.get("rebuild_guard") or {}).get("stood_down")
                print(f"{time.monotonic()-t0:6.0f}  {str(b):>8}  {str(flagged):>7}  {stood}")
                if b is not None:
                    if fired is None and isinstance(low, float) and b < low - 5.0:
                        fired = time.monotonic() - t0
                        print(f"  -> backlog collapsed {low:.1f} s -> {b:.1f} s at +{fired:.0f}s")
                    low = b if low is None else min(low, b)
                time.sleep(SAMPLE_S)
            return 0 if fired is not None else 1
        finally:
            relay.terminate()
            try:
                relay.wait(timeout=15)
            except subprocess.TimeoutExpired:
                relay.kill()
            httpd.shutdown()


if __name__ == "__main__":
    sys.exit(main())

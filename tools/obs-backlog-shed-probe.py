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
a running OBS whose current scene shows Feed A, and no active OBS output. The relay is
started with the HUD served, because the HUD browser source is part of the render load
a broadcast actually puts on OBS.

    python3 tools/obs-backlog-shed-probe.py --source https://www.youtube.com/@LofiGirl/live
"""
import argparse
import csv as _csv
import functools
import http.server
import io
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
import logsetup  # noqa: E402
import obs_ws  # noqa: E402

RELAY = "http://127.0.0.1:8088"
_ADDR = __import__("re").compile(r"^[\d.]+[.:]\d+$|^\*[.:]\*$|^[\d.]+[.:]\*$")
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


def feed_peer(port=53001):
    """The consumer's ephemeral port on *port*, which is the discriminator that matters:
    a rebuild gives OBS a NEW port, a burst keeps the old one. A count cannot tell those
    apart, and correlating a backlog drop with a log line cannot either."""
    # Arch ships no net-tools, macOS no iproute2, so try both rather than return None.
    if os.name != "nt":
        try:
            out = subprocess.run(["ss", "-tnH", "state", "established",
                                  f"sport = :{port}"], capture_output=True, text=True,
                                 errors="replace").stdout
        except OSError:
            out = None
        if out is not None:
            peers = [line.split()[3].rsplit(":", 1)[-1] for line in out.splitlines()
                     if len(line.split()) > 3]
            return ",".join(sorted(peers)) or "-"
    cmd = ["netstat", "-ano"] if os.name == "nt" else ["netstat", "-an"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             errors="replace").stdout
    except OSError:
        return None
    peers = []
    for line in out.splitlines():
        fields = [f for f in line.split() if _ADDR.match(f)]
        if len(fields) < 2 or fields[0].rsplit(_SEP(fields[0]), 1)[-1] != str(port):
            continue
        peer = fields[1]
        if peer.rsplit(_SEP(peer), 1)[-1] in ("0", "*"):
            continue
        peers.append(peer.rsplit(_SEP(peer), 1)[-1])
    return ",".join(sorted(peers)) or "-"


def feed_sockets(port=53001):
    """How many consumers are attached. Prefer feed_peer when the question is whether
    the consumer was REPLACED."""
    cmd = ["netstat", "-ano"] if os.name == "nt" else ["netstat", "-an"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             errors="replace").stdout
    except OSError:
        return None
    # No state word: a German Windows console prints HERGESTELLT, not ESTABLISHED.
    # A connection is a line naming the feed port on one side and a real peer on the
    # other; the listener's peer is the wildcard.
    n = 0
    for line in out.splitlines():
        fields = [f for f in line.split() if _ADDR.match(f)]
        if len(fields) < 2:
            continue
        local, peer = fields[0], fields[1]
        if local.rsplit(_SEP(local), 1)[-1] != str(port):
            continue
        if peer.rsplit(_SEP(peer), 1)[-1] in ("0", "*"):
            continue                       # the listening socket, not a consumer
        n += 1
    return n


def _SEP(addr):
    return ":" if ":" in addr else "."


def feed_a(st):
    return ((st.get("feeds") or {}).get("A")) or {}


def suspend_obs(pid, during):
    """Hold OBS stopped, call *during*, then let it run again. POSIX uses SIGSTOP;
    Windows has no signal for it, so it calls NtSuspendProcess through ctypes."""
    if os.name != "nt":
        os.kill(pid, signal.SIGSTOP)
        try:
            during()
        finally:
            os.kill(pid, signal.SIGCONT)
        return
    import ctypes
    nt, k32 = ctypes.WinDLL("ntdll"), ctypes.WinDLL("kernel32")
    handle = k32.OpenProcess(0x0800, False, pid)          # PROCESS_SUSPEND_RESUME
    if not handle:
        raise OSError(f"OpenProcess({pid}) failed")
    try:
        nt.NtSuspendProcess(handle)
        try:
            during()
        finally:
            nt.NtResumeProcess(handle)
    finally:
        k32.CloseHandle(handle)


def obs_pid():
    if os.name == "nt":
        try:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq obs64.exe",
                                  "/NH", "/FO", "CSV"], capture_output=True,
                                 text=True, errors="replace").stdout
        except OSError:
            return None
        for row in _csv.reader(io.StringIO(out)):
            if len(row) > 1 and row[0].lower() == "obs64.exe":
                return int(row[1])
        return None
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


def _obs_session():
    sess, why = obs_ws._connect(None, None, None, 4.0)
    if sess is None:
        raise SystemExit(f"OBS WebSocket unreachable: {why}")
    return sess


def show_layers(scene, names, on):
    """Show or hide scene items by name. Returns the ones actually switched."""
    names = [n.strip() for n in (names or "").split(",") if n.strip()]
    if not names:
        return []
    sess = _obs_session()
    items = {i["sourceName"]: i["sceneItemId"]
             for i in (sess.request("GetSceneItemList", {"sceneName": scene}) or {})
             .get("sceneItems", [])}
    done = []
    for name in names:
        if name in items:
            sess.request("SetSceneItemEnabled",
                         {"sceneName": scene, "sceneItemId": items[name],
                          "sceneItemEnabled": on})
            done.append(name)
        else:
            print(f"  no scene item {name!r} in {scene!r}")
    return done


def obs_render():
    """(fps, avg render ms, cpu%) — what tells a slow host from a slow source."""
    st = _obs_session().request("GetStats", {}) or {}
    return (st.get("activeFps"), st.get("averageFrameRenderTime"), st.get("cpuUsage"))


def start_recording():
    """Start OBS recording: the render cliff this box shows needs an ACTIVE output."""
    return (_obs_session().request("StartRecord", {}) or {}).get("outputPath", "started")


def stop_recording():
    """Stop it and wait for OBS to finish the file. StopRecord returns before the
    muxer is done, so the path it hands back is not yet complete."""
    sess = _obs_session()
    path = (sess.request("StopRecord", {}) or {}).get("outputPath")
    for _ in range(30):
        time.sleep(1.0)
        if not (sess.request("GetRecordStatus", {}) or {}).get("outputActive"):
            break
    return path


def _sampler(hold_s):
    """Print the relay's view of Feed A every SAMPLE_S while OBS is held stopped."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < hold_s:
        time.sleep(SAMPLE_S)
        f = feed_a(status())
        print(f"  {time.strftime('%H:%M:%S')} +{time.monotonic()-t0:5.0f}s  "
              f"backlog {f.get('backlog_s')}  peer {feed_peer()}")


def main():
    logsetup.harden_stdio()    # the German console is cp850; see tests/test_logs.py
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", required=True, help="a LIVE YouTube/Twitch URL")
    ap.add_argument("--freeze", type=float, default=75.0,
                    help="seconds to hold OBS stopped (default 75)")
    ap.add_argument("--mode", choices=("freeze", "reset", "load"), default="freeze",
                    help="freeze: stop OBS and watch the recovery. reset: trigger the "
                         "director's /obs/feed-reset and watch whether the socket OBS "
                         "abandons is actually freed. load: put OBS under render load "
                         "(an active output) and watch whether it falls behind on its "
                         "own — the case a healthy host cannot produce")
    ap.add_argument("--relay-log", default="/tmp/shed-probe-relay.log")
    ap.add_argument("--layers", default="",
                    help="load mode: comma-separated scene items to show for the run and "
                         "hide again afterwards, to reach the render load of a broadcast")
    ap.add_argument("--scene", default="Stint", help="scene holding the feed and layers")
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
             "--sheet-csv-url", csv_url, "--bind", "127.0.0.1"],
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

            if args.mode == "load":
                shown = show_layers(args.scene, args.layers, True)
                if shown:
                    print("layers shown:", ", ".join(shown))
                rec = start_recording()
                print(f"recording started: {rec}\n"
                      "watching whether OBS drains the ring slower than it fills.\n")
            elif args.mode == "reset":
                print(f"\nsockets on the feed port: {feed_sockets()}")
                print("triggering /obs/feed-reset — OBS opens a new socket and leaves "
                      "the old one; only shutdown() wakes its blocked handler.")
                req = urllib.request.Request(
                    RELAY + "/obs/feed-reset", method="POST",
                    data=json.dumps({"feed": "A"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(req, timeout=15) as r:
                        print("  ", r.read().decode("utf-8", "replace")[:200])
                except Exception as exc:                # noqa: BLE001 — a probe reports
                    print("  reset failed:", exc)
            else:
                print(f"\nstopping OBS for {args.freeze:.0f} s — it stops draining "
                      "the socket.")
                suspend_obs(pid, functools.partial(_sampler, args.freeze))
                print("OBS running again, and behind live.\n")

            print(f"{'clock':>8} {'t':>6}  {'backlog':>8}  {'flagged':>7}  "
                  f"{'peer':>12}  stood_down")
            t0, fired, low = time.monotonic(), None, None
            while time.monotonic() - t0 < args.watch:
                st = status()
                f = feed_a(st)
                b, flagged = f.get("backlog_s"), f.get("backlogged")
                stood = (st.get("rebuild_guard") or {}).get("stood_down")
                extra = ""
                if args.mode == "load":
                    fps, ms, _cpu = obs_render()
                    extra = f"  fps {fps and round(fps, 1)}  render {ms and round(ms, 1)}"
                print(f"{time.strftime('%H:%M:%S'):>8} {time.monotonic()-t0:6.0f}  "
                      f"{str(b):>8}  {str(flagged):>7}  {str(feed_peer()):>12}  "
                      f"{stood}{extra}")
                if b is not None:
                    if fired is None and isinstance(low, float) and b < low - 5.0:
                        fired = time.monotonic() - t0
                        print(f"  -> backlog collapsed {low:.1f} s -> {b:.1f} s at +{fired:.0f}s")
                    low = b if low is None else min(low, b)
                time.sleep(SAMPLE_S)
            return 0 if fired is not None else 1
        finally:
            if args.mode == "load":
                try:
                    show_layers(args.scene, args.layers, False)
                    print("recording stopped:", stop_recording())
                except Exception as exc:        # noqa: BLE001 — cleanup reports
                    print("could not stop the recording:", exc)
            relay.terminate()
            try:
                relay.wait(timeout=15)
            except subprocess.TimeoutExpired:
                relay.kill()
            httpd.shutdown()


if __name__ == "__main__":
    sys.exit(main())

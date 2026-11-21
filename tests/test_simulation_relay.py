#!/usr/bin/env python3
"""A relay started in simulation mode reads its Sheet tabs from the loopback stand-in and
nothing else. Run: python3 tests/test_simulation_relay.py"""
import json, os, socket, subprocess, sys, tempfile, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RELAY = os.path.join(ROOT, "src", "relay", "racecast-feeds.py")
SCHEDULE = "Stint,Commentator,URL\n1,Comms 1,sim://comms1\n2,Comms 2,sim://comms2\n"


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _isolated_args(tmp, port=None):
    """Free control and feed ports, loopback only, and a throwaway runtime dir."""
    feeds = [_free_port() for _ in range(3)]
    return ["--bind", "127.0.0.1", "--http-port", str(port or _free_port()),
            "--runtime-dir", tmp, "--ports", f"{feeds[0]},{feeds[1]}",
            "--pov-port", str(feeds[2]), "--gt7-bind", "127.0.0.1", "--gt7-port", "0",
            "--gt7-ps-ip", "127.0.0.1"]


def _sheet():
    """A loopback Sheet that records which tab every request asked for."""
    tabs = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            u = urlparse(self.path)
            tab = parse_qs(u.query).get("sheet", [""])[0]
            tabs.append((u.path, tab))
            body = SCHEDULE.encode() if tab == "Schedule" else b""
            self.send_response(200)
            self.send_header("Content-Type", "text/csv"); self.end_headers()
            self.wfile.write(body)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, tabs


def _stream_bin(tmp):
    d = os.path.join(tmp, "bin")
    os.makedirs(d)
    for tool in ("yt-dlp", "streamlink"):
        if os.name == "nt":   # never launched: feeds start disarmed
            open(os.path.join(d, tool + ".exe"), "w").close()
        else:
            p = os.path.join(d, tool)
            with open(p, "w") as fh:
                fh.write("#!/bin/sh\nexit 1\n")
            os.chmod(p, 0o755)
    return d


def t_simulated_relay_reads_only_the_loopback_sheet():
    srv, tabs = _sheet()
    tmp = tempfile.mkdtemp(prefix="racecast-sim-relay-")
    port = _free_port()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("RACECAST_SIM", "RACECAST_OBS_WS", "RACECAST_SHEET"))}
    env.update(RACECAST_SIMULATION="1", RACECAST_FEED_FANOUT="0", RACECAST_MANUAL_FEED_ARM="1",
               RACECAST_SIM_SHEET_URL=f"http://127.0.0.1:{srv.server_address[1]}/sim",
               RACECAST_SIM_STREAM_BIN=_stream_bin(tmp), RACECAST_UPDATE_CHECK="0")
    argv = [sys.executable, RELAY, "--sheet-id", "SIMSHEET"] + _isolated_args(tmp, port)
    log = open(os.path.join(tmp, "relay.out"), "wb")  # noqa: SIM115  closed below
    proc = subprocess.Popen(argv, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        status, deadline = None, time.monotonic() + 60
        while time.monotonic() < deadline and proc.poll() is None:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=2) as r:
                    status = json.loads(r.read())
                    break
            except OSError:
                time.sleep(0.3)
        log.flush()
        with open(os.path.join(tmp, "relay.out"), encoding="utf-8", errors="replace") as fh:
            out = fh.read()
        assert status is not None, f"relay did not come up:\n{out[-3000:]}"
        assert "Simulation mode" in out, out[-3000:]
        asked = {tab for _path, tab in tabs}
        assert {"Schedule", "Crew", "Overlay"} <= asked, f"tabs read from the stand-in: {asked}"
        assert all(path == "/sim/gviz/tq" for path, _tab in tabs), tabs
        assert "simulation mode: lookup" not in out and "simulation mode: connection" not in out, \
            f"the relay tried to leave the machine:\n{out[-3000:]}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait()
        log.close()
        srv.shutdown(); srv.server_close()


def t_simulated_relay_refuses_to_start_without_its_endpoints():
    env = {k: v for k, v in os.environ.items() if not k.startswith("RACECAST_SIM")}
    env["RACECAST_SIMULATION"] = "1"
    tmp = tempfile.mkdtemp(prefix="racecast-sim-relay-")
    out = subprocess.run([sys.executable, RELAY] + _isolated_args(tmp) + ["--sheet-id", "X"],
                         env=env, capture_output=True, text=True, errors="replace", timeout=60)
    assert out.returncode != 0, "the relay started in simulation without a simulated Sheet"
    assert "RACECAST_SIM_SHEET_URL" in out.stderr, out.stderr[-2000:]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

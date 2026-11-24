"""The probe stage 2 rehearsal (#1116): the producer's tool and the real sender script.

Every producer action runs `tools/probe-stage2.py` as the runbook does, against the
run's relay and into `OUT/probe-stage2/`: `watch`, `mark`, `kit` (into the role's
temporary folder, since the sheets carry the invite link), `race_reset`, and `report`,
which stops the watch, runs `collect` and `report` and returns `probe_stage2.facts` from
`analysis.json`. `spectator` starts the real `racecast-sender.py` with a probe key
against a simulated PS5 on loopback, the stand-in for the broadcast's spectator
console; `check` registers on the invite and runs the sender's `--check` against its own
simulated PS5. Needs `[event] probe = true` for the spectator."""
import http.client
import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading

import drive
import probes
from roles import Role, StepError, register

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
SENDER = os.path.join(ROOT, "src", "relay", "racecast-sender.py")
TOOL = os.path.join(ROOT, "tools", "probe-stage2.py")
RESULTS = "probe-stage2"
RECORDING_S = 90.0
HZ = 60
SPECTATOR_MPS = 40.0
CHECK_TIMEOUT_S = 60.0
TOOL_TIMEOUT_S = 60.0
CHECK_DEVICE = "198.51.100.250"


def _sim_module():
    spec = importlib.util.spec_from_file_location("gt7_sim_ps5", os.path.join(ROOT, "tools", "gt7-sim-ps5.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def synthetic_packets(length_m, position=None, cars=None, seconds=RECORDING_S):
    """What the simulated PS5 replays, [(seconds, plain packet)]: one car lapping the
    synthetic line at a steady pace."""
    line = drive.Line(drive.trace(drive.synthetic_path(length_m)))
    out, lap, prev = [], 0, 0.0
    for i in range(1, int(seconds * HZ) + 1):
        odo = SPECTATOR_MPS * i / HZ
        if int(odo // line.length) > int(prev // line.length):
            lap += 1
        prev = odo
        x, z = line.at(odo)
        out.append((i / HZ, drive.plain_packet(i, lap, SPECTATOR_MPS, x, z, -1, -1, 50.0,
                                               position=position, cars=cars)))
    return out


class _Ps5:
    """A simulated console on loopback that answers heartbeats on its own port."""

    def __init__(self, packets, reply_port):
        sim = _sim_module()
        self.hb = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.hb.bind(("127.0.0.1", 0))
        self.hb.settimeout(0.2)
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.console = sim.Console(packets, self.tx.sendto, reply_port=reply_port)
        self.stop = threading.Event()
        self.threads = [threading.Thread(target=self._answer, daemon=True),
                        threading.Thread(target=self.console.run, args=(self.stop,), kwargs={"loop": True},
                                         daemon=True)]
        for t in self.threads:
            t.start()

    @property
    def port(self):
        return self.hb.getsockname()[1]

    def _answer(self):
        while not self.stop.is_set():
            try:
                data, addr = self.hb.recvfrom(64)
            except OSError:
                continue
            self.console.heartbeat(data, addr)

    def close(self):
        self.stop.set()
        for t in self.threads:
            t.join(2)
        self.hb.close()
        self.tx.close()


def _free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _sender_args(ps5, rx_port):
    return ["--console", "127.0.0.1", "--bind", "127.0.0.1", "--receive-port", str(rx_port),
            "--console-port", str(ps5.port)]


def _stop(proc, log):
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(5)
    log.close()


@register
class ProbeRole(Role):
    """`watch`, `mark` (text), `kit`, `race_reset`, `spectator` (name, position, cars),
    `stop_spectator`, `check` (entry, segment, name), `report`."""
    kind = "probe"

    def open(self):
        self.watcher = None          # (process, log handle)
        self.spectator = None        # (ps5, process, log handle)
        self.tmp = tempfile.mkdtemp(prefix="probe-stage2-")
        self.out = os.path.join(self.ctx.out_dir or self.tmp, RESULTS)

    def _fetch(self, path):
        try:
            status, data = probes.http_json(self.ctx.url("relay"), path)
        except (OSError, http.client.HTTPException, ValueError):
            return None
        return data if status == 200 else None

    def _length(self):
        return float(self.ctx.event.get("track_length_m", drive.DEFAULT_TRACK_M))

    def _tool(self, *argv):
        """One `probe-stage2.py` command run to its end; StepError unless it exits 0."""
        try:
            done = subprocess.run([sys.executable, TOOL, "--relay", self.ctx.url("relay"), *argv],
                                  capture_output=True, text=True, errors="replace", timeout=TOOL_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise StepError(f"probe-stage2.py {argv[0]} did not end") from None
        if done.returncode:
            raise StepError(f"probe-stage2.py {argv[0]}: {(done.stdout + done.stderr).strip()}")
        return done.stdout

    def act_watch(self, args):
        if self.watcher is not None:
            raise StepError("the watch runs already")
        os.makedirs(self.out, exist_ok=True)
        log = io.open(os.path.join(self.out, "watch.log"), "w", encoding="utf-8")  # noqa: SIM115  kept open for the process
        proc = subprocess.Popen([sys.executable, TOOL, "--relay", self.ctx.url("relay"), "watch", "--out", self.out],
                                stdout=log, stderr=subprocess.STDOUT)
        self.watcher = (proc, log)
        return {"out": RESULTS}

    def act_mark(self, args):
        text = str(args.get("text") or "mark")
        self._tool("mark", "--out", self.out, text)
        return {"text": text}

    def act_kit(self, args):
        """The setup sheets of the event's invite, with the run's relay as the public base."""
        folder = os.path.join(self.tmp, "kit")
        self._tool("kit", "--out", folder, "--base", self.ctx.url("relay"))
        return {"sheets": sorted(os.listdir(folder))}

    def act_race_reset(self, args):
        self._tool("race-reset", "--out", self.out)
        return {"ok": True}

    def act_spectator(self, args):
        if self.spectator is not None:
            raise StepError("the spectator sends already")
        probe = self._fetch("/telemetry/probe")
        if not probe or not probe.get("key"):
            raise StepError("the relay has no telemetry probe: set [event] probe = true")
        rx = _free_udp_port()
        ps5 = _Ps5(synthetic_packets(self._length(), args.get("position"), args.get("cars")), rx)
        name = str(args.get("name") or "Spectator")
        os.makedirs(self.out, exist_ok=True)
        log = io.open(os.path.join(self.out, "spectator-sender.log"), "w", encoding="utf-8")  # noqa: SIM115  kept open for the process
        proc = subprocess.Popen([sys.executable, SENDER, "--url", self.ctx.url("relay") + "/console/probe/ingest",
                                 "--key", probe["key"], "--name", name] + _sender_args(ps5, rx),
                                stdout=log, stderr=subprocess.STDOUT)
        self.spectator = (ps5, proc, log)
        return {"name": name}

    def act_stop_spectator(self, args):
        if self.spectator is None:
            raise StepError("no spectator sends")
        self._end_spectator()

    def _end_spectator(self):
        ps5, proc, log = self.spectator
        self.spectator = None
        _stop(proc, log)
        ps5.close()

    def act_check(self, args):
        """Register on the invite and run `racecast-sender.py --check` with that code."""
        invite = self.ctx.event_link("invite")
        client = drive.Client(self.ctx.url("relay"))
        try:
            segments = client.data(invite).get("segments") or [None]
            code = client.register(invite, args["entry"], str(args.get("name") or "Check"),
                                   args.get("segment") or segments[0], CHECK_DEVICE)
        except (OSError, ValueError, KeyError, http.client.HTTPException, drive.DriveError) as exc:
            raise StepError(f"the check could not register: {exc}") from None
        rx = _free_udp_port()
        ps5 = _Ps5(synthetic_packets(self._length(), seconds=20.0), rx)
        try:
            done = subprocess.run([sys.executable, SENDER, "--url", self.ctx.url("relay") + "/console/drive/ingest",
                                   "--code", code, "--check"] + _sender_args(ps5, rx),
                                  capture_output=True, text=True, errors="replace", timeout=CHECK_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise StepError("the sender's check did not end") from None
        finally:
            ps5.close()
        lines = [line for line in done.stdout.splitlines() if " check " in line or "RESULT" in line]
        os.makedirs(self.out, exist_ok=True)
        with io.open(os.path.join(self.out, "check.log"), "w", encoding="utf-8") as fh:
            fh.write(done.stdout)
        return {"ok": done.returncode == 0, "lines": lines, "result": lines[-1] if lines else ""}

    def act_report(self, args):
        if self.watcher is None:
            raise StepError("the watch never started")
        proc, log = self.watcher
        self.watcher = None
        _stop(proc, log)
        self._tool("collect", "--out", self.out)
        self._tool("report", self.out)
        with io.open(os.path.join(self.out, "analysis.json"), encoding="utf-8") as fh:
            facts = json.load(fh)["facts"]
        return dict(facts, csv=os.path.isfile(os.path.join(self.out, "private", "race-recording.csv")))

    def close(self):
        out = []
        if self.watcher is not None:
            proc, log = self.watcher
            if proc.poll() is not None:
                out.append(("hard", f"the probe watch ended early with exit code {proc.returncode}"))
            _stop(proc, log)
            self.watcher = None
        if self.spectator is not None:
            proc = self.spectator[1]
            if proc.poll() is not None:
                out.append(("hard", f"the spectator's sender ended early with exit code {proc.returncode}"))
            self._end_spectator()
        shutil.rmtree(self.tmp, ignore_errors=True)
        return out

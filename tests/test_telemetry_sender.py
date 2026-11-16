#!/usr/bin/env python3
"""The driver telemetry sender and the simulated PS5 (#1110), end to end on loopback.
Run: python3 tests/test_telemetry_sender.py"""
import importlib.util, io, json, os, socket, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import telemetry_probe as tp  # noqa: E402


def _load(name, *rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


snd = _load("racecast_sender", "src", "relay", "racecast-sender.py")
sim = _load("gt7_sim_ps5", "tools", "gt7-sim-ps5.py")


class _Reply:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


def t_batch_format_matches_the_relay():
    packets = [b"\x01" * 296, b"\x02" * 344]
    assert snd.encode_batch(packets) == tp.encode_batch(packets)
    assert snd.MAX_BATCH <= tp.MAX_BATCH, "the sender must never post more than the relay accepts"


def t_uploader_follows_the_rate_and_reports_outages_once():
    said, calls = [], []

    def opener(req, timeout):
        calls.append((req.full_url, dict(req.header_items()), req.data))
        if len(calls) in (2, 3):
            raise OSError("offline")
        return _Reply(json.dumps({"ok": True, "rate_hz": 10}).encode())
    up = snd.Uploader("https://h/console/probe/ingest", "KEY", "Car 7", opener=opener, say=said.append)
    up.put(b"p1")
    assert up.post_once() == 0.1 and up.rate_hz == 10, "the relay sets the send rate"
    url, headers, data = calls[0]
    assert url.endswith("?sender=Car%207") and headers["X-probe-key"] == "KEY", (url, headers)
    assert headers["User-agent"].startswith("racecast-sender/"), "a bare Python UA is refused by some hosts"
    assert tp.decode_batch(data) == [b"p1"]
    up.put(b"p2")
    up.post_once(); up.post_once()
    assert up.pending() == 1 and up.stats["post_failed"] == 2, "a failed post keeps its packets"
    assert [s for s in said if s.startswith("upload interrupted")] == ["upload interrupted: cannot reach the relay (offline). Retrying"], said
    up.post_once()
    assert up.pending() == 0 and any(s.startswith("upload works again") for s in said), said
    for i in range(snd.BACKLOG + 5):
        up.put(bytes([i % 256]))
    assert up.pending() == snd.BACKLOG and up.stats["dropped"] == 5, "an outage keeps only the newest packets"


def _recording(d, seconds=3.0, hz=60):
    import gt7_recording
    from test_gt7_telemetry import _packet
    import struct
    w = gt7_recording.RecordingWriter(d, "Solo", "dev", flush_s=0.05)
    t0 = 1_700_000_000.0
    n = int(seconds * hz)
    for i in range(1, n + 1):
        plain = bytearray(_packet(lap=1, speed_mps=40.0))
        struct.pack_into("<i", plain, 0x70, i)
        w.put(t0 + i / hz, "~", bytes(plain) + bytes(0x158 - len(plain)))
    w.close()
    return w.path, n


def _udp(port=0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", port))
    return s


def t_sender_relays_the_simulated_console_through_the_relay_probe():
    import test_console_gate as g
    probe = tp.Probe("probe-key-0123456789")
    srv = g._serve(telemetry_probe_obj=probe)
    url = f"http://127.0.0.1:{srv.server_address[1]}/console/probe/ingest"
    with tempfile.TemporaryDirectory() as d:
        path, n = _recording(d)
        hb = _udp()
        tx = _udp()
        local_app = _udp()
        local_app.settimeout(0.2)
        up = snd.Uploader(url, probe.key, "Car 7", say=lambda line: None)
        sender = snd.Sender(up, console="127.0.0.1", bind="127.0.0.1", receive_port=0,
                            console_port=hb.getsockname()[1], forward=local_app.getsockname(),
                            say=lambda line: None)
        console = sim.Console(sim.load(path), tx.sendto, reply_port=sender.sock.getsockname()[1])
        stop = threading.Event()

        def answer():
            hb.settimeout(0.2)
            while not stop.is_set():
                try:
                    data, addr = hb.recvfrom(64)
                except OSError:
                    continue
                console.heartbeat(data, addr)
        threading.Thread(target=answer, daemon=True).start()
        threading.Thread(target=up.run, args=(stop,), daemon=True).start()
        sender_stop = threading.Event()
        runner = threading.Thread(target=sender.run, args=(sender_stop, 8.0), daemon=True)
        runner.start()
        deadline = time.monotonic() + 3
        while not console.receivers and time.monotonic() < deadline:
            time.sleep(0.02)
        assert console.receivers, "the sender must heartbeat the console"
        console.run(stop, speed=3.0)
        forwarded = 0
        try:
            while True:
                local_app.recvfrom(4096)
                forwarded += 1
        except OSError:
            pass
        deadline = time.monotonic() + 5
        while (probe.snapshot()["senders"].get("Car 7", {}).get("packets", {}).get("received", 0) < n
               and time.monotonic() < deadline):
            time.sleep(0.05)
        stop.set()
        sender_stop.set()
        runner.join(2)
        snap = probe.snapshot()["senders"]["Car 7"]
        srv.shutdown()
        for s in (hb, tx, local_app, sender.sock):
            s.close()
    assert snap["packets"]["received"] == n and snap["packets"]["lost"] == 0, snap["packets"]
    assert snap["kinds"] == {"A": n}, f"the sender asks for format A: {snap['kinds']}"
    assert forwarded == n, f"the forwarder passes every packet to the local app: {forwarded}/{n}"
    assert sender.stats["heartbeats"] >= 1 and snap["sender_stats"]["heartbeats"] >= 1, snap["sender_stats"]


def t_listener_sends_no_heartbeat_and_takes_forwarded_packets():
    posted = []
    up = snd.Uploader("http://x/ingest", "K", "L", say=lambda line: None,
                      opener=lambda req, timeout: posted.append(req.data) or _Reply(b"{}"))
    sender = snd.Sender(up, listen=0, bind="127.0.0.1", say=lambda line: None)
    port = sender.sock.getsockname()[1]
    other_app = _udp()
    for i in range(3):
        other_app.sendto(bytes([i]) * 296, ("127.0.0.1", port))
    other_app.sendto(b"short", ("127.0.0.1", port))
    sender.run(threading.Event(), duration=0.8)
    up.post_once()
    other_app.close(); sender.sock.close()
    assert sender.stats["heartbeats"] == 0, "listener mode must leave the console to the other app"
    assert sender.stats["received"] == 3 and len(tp.decode_batch(posted[0])) == 3, sender.stats


def t_interruptions_are_reported_once_per_gap():
    said = []
    clock = [0.0]
    up = snd.Uploader("http://x/ingest", "K", "L", opener=lambda r, timeout: _Reply(b"{}"))
    sender = snd.Sender(up, listen=0, bind="127.0.0.1", say=said.append, clock=lambda: clock[0])
    other = _udp()
    other.sendto(b"\0" * 296, ("127.0.0.1", sender.sock.getsockname()[1]))
    assert sender.receive_once()
    clock[0] = 2.5
    sender.check_quiet(); sender.check_quiet()
    other.sendto(b"\0" * 296, ("127.0.0.1", sender.sock.getsockname()[1]))
    sender.receive_once()
    other.close(); sender.sock.close()
    assert said == ["no packets from the console for 2 s (menu, replay or network?)",
                    "packets again after 2.5 s"], said
    assert sender.stats["interruptions"] == 1


def t_load_tool_reports_what_the_relay_counted():
    import contextlib
    import test_console_gate as g
    load = _load("gt7_probe_load", "tools", "gt7-probe-load.py")
    probe = tp.Probe("probe-key-0123456789")
    srv = g._serve(telemetry_probe_obj=probe)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            assert load.main(["--url", base + "/console/probe/ingest", "--key", probe.key,
                              "--senders", "2", "--duration", "1",
                              "--status", base + "/telemetry/probe"]) == 0
    finally:
        srv.shutdown()
    text = out.getvalue()
    report = json.loads(text[text.index("{"):])
    assert report["senders"] == 2 and report["post_failed"] == 0, report
    assert report["relay"]["received"] == report["packets_sent"] > 100, report
    assert report["relay"]["lost"] == 0 and report["latency_ms"]["p50"] > 0, report


def t_status_line_reports_the_rate_of_the_last_interval():
    said, clock = [], [0.0]
    up = snd.Uploader("http://x/ingest", "K", "L", opener=lambda r, timeout: _Reply(b"{}"))
    sender = snd.Sender(up, listen=0, bind="127.0.0.1", say=said.append, clock=lambda: clock[0])
    stop = threading.Event()

    def receive_once():
        sender.stats["received"] += 6
        clock[0] = round(clock[0] + 0.1, 6)
        if clock[0] > 10.05:
            stop.set()
        return True
    sender.receive_once = receive_once
    sender.run(stop)
    sender.sock.close()
    status = [line for line in said if line.startswith("received")]
    assert len(status) == 1, f"one status line after ten seconds, none at the start: {said}"
    rate = float(status[0].split()[1].rstrip("/s,"))
    assert 59.0 <= rate <= 61.0, f"the status reports the interval's packet rate: {status[0]}"


def t_cli_refuses_listen_with_forward_and_keeps_help_ascii():
    out = io.StringIO()
    old = sys.stderr
    sys.stderr = out
    try:
        snd.main(["--url", "u", "--key", "k", "--name", "n", "--listen", "1", "--forward", "h:2"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("--listen with --forward must be refused")
    finally:
        sys.stderr = old
    assert "do not combine" in out.getvalue(), out.getvalue()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

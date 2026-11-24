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
# The tests never follow this machine's proxy: none from the environment, none from the system.
for _k in [k for k in os.environ if k.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")]:
    del os.environ[_k]
import urllib.request  # noqa: E402
urllib.request.getproxies = urllib.request.getproxies_environment
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


def _wire_a(pid):
    import struct
    import gt7_crypto
    from test_gt7_telemetry import _packet
    plain = bytearray(_packet())
    struct.pack_into("<i", plain, 0x70, pid)
    return gt7_crypto.encrypt(bytes(plain), "A", iv=pid)


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
        forwarded = [0]

        def drain():
            while not stop.is_set():
                try:
                    local_app.recvfrom(4096)
                    forwarded[0] += 1
                except OSError:
                    pass  # a timeout: keep reading until the test ends
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
        threading.Thread(target=drain, daemon=True).start()
        threading.Thread(target=up.run, args=(stop,), daemon=True).start()
        sender_stop = threading.Event()
        runner = threading.Thread(target=sender.run, args=(sender_stop, 8.0), daemon=True)
        runner.start()
        deadline = time.monotonic() + 3
        while not console.receivers and time.monotonic() < deadline:
            time.sleep(0.02)
        assert console.receivers, "the sender must heartbeat the console"
        console.run(stop, speed=3.0)
        deadline = time.monotonic() + 5
        while ((probe.snapshot()["senders"].get("Car 7", {}).get("packets", {}).get("received", 0) < n
                or forwarded[0] < n) and time.monotonic() < deadline):
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
    assert forwarded[0] == n, f"the forwarder passes every packet to the local app: {forwarded[0]}/{n}"
    assert sender.stats["heartbeats"] >= 1 and snap["sender_stats"]["heartbeats"] >= 1, snap["sender_stats"]


def t_listener_sends_no_heartbeat_and_takes_forwarded_packets():
    posted = []
    up = snd.Uploader("http://x/ingest", "K", "L", say=lambda line: None,
                      opener=lambda req, timeout: posted.append(req.data) or _Reply(b"{}"))
    said = []
    sender = snd.Sender(up, listen=0, bind="127.0.0.1", say=said.append)
    port = sender.sock.getsockname()[1]
    other_app = _udp()
    for i in range(3):
        other_app.sendto(bytes([i]) * 296, ("127.0.0.1", port))
    other_app.sendto(b"short", ("127.0.0.1", port))
    sender.run(threading.Event(), duration=0.8)
    up.post_once()
    other_app.close(); sender.sock.close()
    assert sender.stats["heartbeats"] == 0 and not any("heartbeat" in line for line in said), \
        f"listener mode must leave the console to the other app: {said}"
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
    probe.ingest("load 01", tp.encode_batch([_wire_a(999)]), None)     # an earlier run
    srv = g._serve(telemetry_probe_obj=probe)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            assert load.main(["--url", base + "/console/probe/ingest", "--key", probe.key,
                              "--senders", "2", "--duration", "1",
                              "--status", base + "/telemetry/probe", "--reset"]) == 0
    finally:
        srv.shutdown()
    text = out.getvalue()
    report = json.loads(text[text.index("{"):])
    assert report["senders"] == 2 and report["post_failed"] == 0, report
    assert report["relay"]["received"] == report["packets_sent"] > 100, report
    assert report["relay"]["lost"] == 0 and report["latency_ms"]["p50"] > 0, report
    assert report["generated_hz_per_sender"] > 20, f"the report shows the rate it achieved: {report}"


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


def t_cli_refuses_listen_with_forward_and_bad_names():
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



def t_the_default_bind_is_one_interface_never_all():
    assert snd.default_bind(listen=5000) == "127.0.0.1", "listen mode receives from this device only"
    assert snd.default_bind(console="127.0.0.1") == "127.0.0.1", "the interface that reaches the console"
    found = snd.default_bind()
    assert found is None or (found != "0.0.0.0" and socket.inet_aton(found)), found
    up = snd.Uploader("http://x/ingest", "K", "L", say=lambda line: None)
    sender = snd.Sender(up, console="127.0.0.1", receive_port=0, say=lambda line: None)
    try:
        assert sender.sock.getsockname()[0] == "127.0.0.1", sender.sock.getsockname()
    finally:
        sender.sock.close()


def t_an_invite_code_authenticates_and_a_quiet_console_keeps_the_slot():
    import urllib.error
    calls, clock, said = [], [0.0], []

    def opener(req, timeout):
        calls.append((req.full_url, dict(req.header_items()), req.data))
        if len(calls) == 3:
            raise urllib.error.HTTPError(req.full_url, 409, "Conflict", {}, None)
        return _Reply(b'{"ok": true}')
    up = snd.Uploader("https://h/console/drive/ingest", code="D1:abc.def", opener=opener,
                      say=said.append, clock=lambda: clock[0])
    up.put(b"p1")
    up.post_once()
    url, headers, data = calls[0]
    assert url == "https://h/console/drive/ingest" and headers["X-driver-code"] == "D1:abc.def", (url, headers)
    assert "X-probe-key" not in headers and tp.decode_batch(data) == [b"p1"]
    clock[0] = snd.KEEPALIVE_S - 1
    up.post_once()
    assert len(calls) == 1, "no empty post while packets flow or the console was quiet only briefly"
    clock[0] = 1 + snd.KEEPALIVE_S
    up.post_once()
    assert len(calls) == 2 and tp.decode_batch(calls[1][2]) == [], "a keepalive is an empty batch"
    clock[0] = 2 * snd.KEEPALIVE_S + 2
    assert up.post_once() == snd.REFUSED_WAIT_S
    assert said and "Someone else sends for this entry" in said[-1], said


def t_cli_takes_a_code_or_the_probe_key_and_name_never_both():
    quick = ["--listen", "0", "--bind", "127.0.0.1", "--duration", "0"]
    for argv, text in ((["--url", "http://127.0.0.1:9/i", "--code", "D1:a.b", "--name", "n"] + quick,
                        "leave out --key and --name"),
                       (["--url", "http://127.0.0.1:9/i"] + quick, "pass --code from the invite page")):
        err = io.StringIO()
        old = sys.stderr
        sys.stderr = err
        try:
            snd.main(argv)
        except SystemExit as exc:
            assert exc.code == 2, argv
        else:
            raise AssertionError(f"{argv} must be refused")
        finally:
            sys.stderr = old
        assert text in err.getvalue(), (argv, err.getvalue())


def t_cli_help_is_ascii():
    out = io.StringIO()
    old = sys.stdout
    sys.stdout = out
    try:
        snd.main(["--help"])
    except SystemExit:
        pass  # argparse exits after printing the help
    finally:
        sys.stdout = old
    assert "--forward" in out.getvalue() and out.getvalue().isascii(), out.getvalue()


def t_names_the_relay_refuses_are_refused_at_start():
    for bad in ("Car #7", "J\u00f6rg", "x" * 33):
        err = io.StringIO()
        old = sys.stderr
        sys.stderr = err
        try:
            snd.main(["--url", "http://127.0.0.1:9/ingest", "--key", "k", "--name", bad,
                      "--listen", "0", "--bind", "127.0.0.1", "--duration", "0"])
        except SystemExit as exc:
            assert exc.code == 2, bad
        else:
            raise AssertionError(f"{bad!r} must be refused before any post")
        finally:
            sys.stderr = old
        assert "--name takes" in err.getvalue(), err.getvalue()
        assert not snd.NAME.match(bad) and not tp._NAME.match(bad), "the sender and relay rules must agree"


def t_packets_queued_during_a_post_are_never_lost_or_sent_twice():
    posted = []
    up = None

    def opener(req, timeout):
        packets = tp.decode_batch(req.data)
        posted.append(packets)
        if len(posted) == 1:             # the queue overflows while this post is in flight
            for i in range(60):
                up.put(b"new%03d" % i)
        return _Reply(b"{}")
    up = snd.Uploader("http://x/ingest", "K", "L", opener=opener, say=lambda line: None)
    for i in range(snd.BACKLOG):
        up.put(b"old%03d" % i)
    while up.pending():
        up.post_once()
    flat = [p for batch in posted for p in batch]
    assert len(flat) == len(set(flat)), "a packet was posted twice"
    assert flat[-60:] == [b"new%03d" % i for i in range(60)], "packets queued during a post were lost"
    assert len(flat) == snd.BACKLOG + 60 and up.stats["dropped"] == 0, \
        f"evicted packets of a delivered post are not dropped: {len(flat)} {up.stats}"


def t_packets_evicted_during_a_failed_post_count_as_dropped():
    up = None

    def opener(req, timeout):
        for i in range(10):
            up.put(b"new%03d" % i)
        raise OSError("offline")
    up = snd.Uploader("http://x/ingest", "K", "L", opener=opener, say=lambda line: None)
    for i in range(snd.BACKLOG):
        up.put(b"old%03d" % i)
    up.post_once()
    assert up.stats["dropped"] == 10 and up.pending() == snd.BACKLOG, up.stats


def t_refused_posts_drop_or_wait_instead_of_retrying_forever():
    import urllib.error
    said, codes = [], [400, 403, 403]

    def opener(req, timeout):
        code = codes.pop(0) if codes else 200
        if code != 200:
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)
        return _Reply(b"{}")
    up = snd.Uploader("http://x/ingest", "K", "L", opener=opener, say=said.append)
    up.put(b"bad batch")
    assert up.post_once() == 0.05 and up.pending() == 0 and up.stats["rejected"] == 1, \
        "a batch the relay refuses (400) is dropped, not retried"
    up.put(b"p")
    assert up.post_once() == snd.REFUSED_WAIT_S and up.post_once() == snd.REFUSED_WAIT_S, \
        "a refused key waits a minute between tries"
    assert sum("Register again on the invite page" in line for line in said) == 2, said
    assert up.post_once() == 0.05 and up.pending() == 0


def t_a_pending_code_waits_quietly_and_announces_the_approval():
    import io
    import urllib.error
    said, replies = [], [423, 423, 423, 200]

    def opener(req, timeout):
        code = replies.pop(0)
        if code != 200:
            body = io.BytesIO(json.dumps({"ok": False, "pending": True,
                                          "error": "waiting for approval by Race Control"}).encode())
            raise urllib.error.HTTPError(req.full_url, code, "Locked", {}, body)
        return _Reply(b'{"ok": true}')
    up = snd.Uploader("https://h/console/drive/ingest", code="D1:abc.def", opener=opener, say=said.append)
    for _ in range(3):
        up.put(b"p")
        assert up.post_once() == snd.PENDING_WAIT_S, "a pending code is asked again every few seconds"
    assert up.pending() == 0 and up.stats["dropped"] == 0 and up.stats["post_failed"] == 0, \
        ("packets the relay will not take are no upload failure", up.stats)
    assert len(said) == 1 and "waiting for approval by Race Control" in said[0], said
    assert all(ord(ch) < 128 for ch in said[0]), "the console text stays ASCII"
    up.put(b"p")
    up.post_once()
    assert len(said) == 2 and "approved" in said[1], said


def t_a_dropped_registration_stops_the_sender_once():
    import io
    import threading
    import urllib.error
    said, calls = [], []

    def opener(req, timeout):
        calls.append(1)
        body = io.BytesIO(json.dumps({"error": "your registration was dropped: register again on the invite page"}).encode())
        raise urllib.error.HTTPError(req.full_url, 410, "Gone", {}, body)
    up = snd.Uploader("https://h/console/drive/ingest", code="D1:abc.def", opener=opener, say=said.append)
    up.put(b"p")
    stop = threading.Event()
    up.run(stop)
    assert stop.is_set() and len(calls) == 1, "a code the relay dropped is never tried again"
    assert len(said) == 1 and "register again" in said[0], said


def t_receiver_survives_windows_udp_errors_and_skips_oversized_packets():
    class Sock:
        def __init__(self, events):
            self.events = events

        def recvfrom(self, n):
            event = self.events.pop(0)
            if isinstance(event, BaseException):
                raise event
            return event, ("10.0.0.5", 1)
    up = snd.Uploader("http://x/ingest", "K", "L", opener=lambda r, timeout: _Reply(b"{}"))
    sender = snd.Sender(up, listen=0, bind="127.0.0.1", say=lambda line: None)
    sender.sock.close()
    win = OSError("message too long")
    win.winerror = 10040
    sender.sock = Sock([ConnectionResetError(10054, "reset"), win, b"\0" * 600, b"\0" * 296])
    assert [sender.receive_once() for _ in range(4)] == [False, False, False, True]
    assert sender.stats["oversized"] == 2 and up.pending() == 1, sender.stats


def t_a_failed_search_tells_the_driver_to_name_the_console():
    class Sock:
        def sendto(self, data, addr):
            raise OSError(65, "No route to host")
    up = snd.Uploader("http://x/ingest", "K", "L", opener=lambda r, timeout: _Reply(b"{}"))
    said = []
    searching = snd.Sender(up, bind="127.0.0.1", receive_port=0, say=said.append)
    pinned = snd.Sender(up, console="10.0.0.9", bind="127.0.0.1", receive_port=0, say=said.append)
    for s in (searching, pinned):
        s.sock.close()
        s.sock = Sock()
    searching.heartbeat()
    assert "--console <PlayStation IP>" in said[-1], f"a failed search must name the fix: {said}"
    searching.heartbeat()
    assert sum("--console" in line for line in said) == 1, f"the hint shows once: {said}"
    pinned.heartbeat()
    assert "--console" not in said[-1], f"a sender that has --console needs no hint: {said}"


def t_a_discovered_console_is_searched_again_after_silence():
    said, clock = [], [0.0]
    up = snd.Uploader("http://x/ingest", "K", "L", opener=lambda r, timeout: _Reply(b"{}"))
    sender = snd.Sender(up, bind="127.0.0.1", receive_port=0, say=said.append, clock=lambda: clock[0])
    peer = _udp()
    peer.sendto(b"\0" * 296, ("127.0.0.1", sender.sock.getsockname()[1]))
    assert sender.receive_once() and sender.console == "127.0.0.1"
    clock[0] = snd.RELATCH_S
    sender.check_relatch()
    assert sender.console is None and "searching again" in said[-1], said
    pinned = snd.Sender(up, console="10.0.0.9", bind="127.0.0.1", receive_port=0,
                        say=said.append, clock=lambda: clock[0])
    pinned._last_rx = 0.0
    clock[0] = 10 * snd.RELATCH_S
    pinned.check_relatch()
    assert pinned.console == "10.0.0.9", "--console is never searched again"
    for s in (peer, sender.sock, pinned.sock):
        s.close()


def t_simulated_console_keeps_a_receiver_through_a_late_heartbeat_and_lapses_after():
    clock, sent = [0.0], []
    console = sim.Console([], lambda data, addr: sent.append((len(data), addr)),
                          clock=lambda: clock[0], reply_port=7)
    plain = bytes(0x158)
    console.heartbeat(b"A", ("10.0.0.2", 1))
    clock[0] = 10.2                      # the sender's next heartbeat is a little late
    console.emit(plain)
    assert sent == [(296, ("10.0.0.2", 7))], f"no packet may be lost at the heartbeat boundary: {sent}"
    console.heartbeat(b"~", ("10.0.0.2", 1))
    console.emit(plain)
    assert sent[-1][0] == 296, "a running stream keeps its format"
    clock[0] += sim.ALIVE_S + 1
    console.heartbeat(b"~", ("10.0.0.2", 1))
    console.emit(plain)
    assert sent[-1][0] == 344, "after a lapse the console takes the newly requested format"
    console.heartbeat(b"A", ("10.0.0.3", 1))
    clock[0] += sim.ALIVE_S + 1
    console.emit(plain)
    assert len(sent) == 3 and not console.receivers, "a lapsed receiver gets nothing and is forgotten"



class _Relay:
    """A loopback HTTP/1.1 server that counts connections and keeps the batches it
    answered. `script` maps a 1-based request number to what goes wrong with it."""

    def __init__(self, script=None, idle_close=False):
        import socketserver
        from http.server import BaseHTTPRequestHandler
        outer = self
        self.connections, self.requests, self.accepted = 0, 0, []
        self.script, self.idle_close = dict(script or {}), idle_close

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                outer.connections += 1

            def log_message(self, *a):
                pass

            def do_POST(self):
                outer.requests += 1
                body = self.rfile.read(int(self.headers["Content-Length"]))
                fault = outer.script.get(outer.requests)
                if fault == "drop":
                    self.close_connection = True
                    return
                if fault == "garble":
                    self.wfile.write(b"no http here\r\n\r\n")
                    self.close_connection = True
                    return
                if fault == "redirect":
                    page = b"<a href='elsewhere'>moved</a>"
                    self.send_response(302)
                    self.send_header("Location", outer.location)
                    self.send_header("Content-Length", str(len(page)))
                    self.end_headers()
                    self.wfile.write(page)
                    return
                if fault == "stall":
                    time.sleep(1.0)
                    self.close_connection = True
                    return
                outer.accepted.append(tp.decode_batch(body))
                reply = b'{"ok": true}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)
                if outer.idle_close:
                    self.close_connection = True   # closes without saying so, like an idle timeout

        class S(socketserver.ThreadingMixIn, socketserver.TCPServer):
            daemon_threads = True
        self.srv = S(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d/console/probe/ingest" % self.srv.server_address[1]
        self.location = self.url
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def _uploader(relay):
    return snd.Uploader(relay.url, "K", "L", say=lambda line: None)


def t_posts_reuse_one_connection():
    relay = _Relay()
    try:
        up = _uploader(relay)
        for i in range(5):
            up.put(b"p%d" % i)
            up.post_once()
    finally:
        relay.close()
    assert up.stats["posted"] == 5 and up.stats["post_failed"] == 0, up.stats
    assert relay.connections == 1, f"five posts must share one connection, not {relay.connections}"


def t_a_connection_the_relay_closed_is_opened_again_without_a_failure():
    relay = _Relay(idle_close=True)
    try:
        up = _uploader(relay)
        for i in range(3):
            up.put(b"p%d" % i)
            up.post_once()
    finally:
        relay.close()
    assert up.stats["post_failed"] == 0, f"a closed idle connection is no upload failure: {up.stats}"
    assert relay.accepted == [[b"p0"], [b"p1"], [b"p2"]], relay.accepted


def t_a_dropped_post_is_sent_again_and_delivered_once():
    relay = _Relay(script={2: "drop", 4: "drop", 5: "drop"})
    try:
        up = _uploader(relay)
        up.put(b"p0")
        up.post_once()                      # request 1 opens the connection
        up.put(b"p1")
        up.post_once()                      # request 2 drops on the reused connection, 3 retries
        assert up.stats["post_failed"] == 0 and relay.connections == 2, (up.stats, relay.connections)
        up.put(b"p2")
        up.post_once()                      # 4 drops on the reused one, 5 drops on a fresh one
        assert up.stats["post_failed"] == 1 and up.pending() == 1, \
            f"a drop on a fresh connection is an upload failure and keeps the batch: {up.stats}"
        up.post_once()                      # 6 delivers it
    finally:
        relay.close()
    assert relay.accepted == [[b"p0"], [b"p1"], [b"p2"]], \
        f"every batch arrives once, in order: {relay.accepted}"


def t_a_garbled_reply_is_an_upload_failure():
    relay = _Relay(script={1: "garble"})
    try:
        up = _uploader(relay)
        up.put(b"p0")
        up.post_once()
        up.post_once()
    finally:
        relay.close()
    assert up.stats["post_failed"] == 1 and relay.accepted == [[b"p0"]], (up.stats, relay.accepted)


def t_a_redirect_is_never_followed():
    relay, other = _Relay(script={1: "redirect"}), _Relay()
    relay.location = other.url
    try:
        up = _uploader(relay)
        up.put(b"p0")
        up.post_once()
    finally:
        relay.close()
        other.close()
    assert other.connections == 0, "the sender posts to the relay's host only"
    assert up.stats["post_failed"] == 1 and up.pending() == 1, up.stats


def t_https_checks_the_certificate_and_the_host_name():
    import http.client
    import ssl
    conn = snd.Connection()._new("https", "relay.example", None, 5)
    assert isinstance(conn, http.client.HTTPSConnection) and conn.port == 443
    assert conn._context.verify_mode == ssl.CERT_REQUIRED and conn._context.check_hostname, \
        "the sender never posts telemetry over an unverified TLS connection"
    assert isinstance(snd.Connection()._new("http", "127.0.0.1", 8, 5), http.client.HTTPConnection)



class _ConnectProxy:
    """A loopback CONNECT proxy that counts tunnels and keeps what each one asked for."""

    def __init__(self):
        import socketserver
        outer = self
        self.tunnels, self.auth = [], []

        class H(socketserver.BaseRequestHandler):
            def handle(self):
                f = self.request.makefile("rb")
                target = f.readline().split()[1].decode()
                for line in iter(f.readline, b"\r\n"):
                    if line.lower().startswith(b"proxy-authorization:"):
                        outer.auth.append(line.split(b":", 1)[1].strip().decode())
                outer.tunnels.append(target)
                host, port = target.rsplit(":", 1)
                up = socket.create_connection((host, int(port)))
                self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")

                def back():
                    try:
                        for chunk in iter(lambda: up.recv(65536), b""):
                            self.request.sendall(chunk)
                    except OSError:
                        pass  # the relay closed its side
                    try:
                        self.request.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass  # the client is already gone
                threading.Thread(target=back, daemon=True).start()
                try:
                    for chunk in iter(lambda: f.read1(65536), b""):
                        up.sendall(chunk)
                except OSError:
                    pass  # the client closed its side
                up.close()

        class S(socketserver.ThreadingMixIn, socketserver.TCPServer):
            daemon_threads = True
        self.srv = S(("127.0.0.1", 0), H)
        self.url = "http://user:secret@127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def _proxy_env(**values):
    """Replace every proxy variable of the environment; returns the undo."""
    keys = [k for k in os.environ if k.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")]
    saved = {k: os.environ.pop(k) for k in keys}
    os.environ.update(values)

    def undo():
        for k in values:
            os.environ.pop(k, None)
        os.environ.update(saved)
    return undo


def t_a_proxy_from_the_environment_tunnels_to_the_relay():
    import base64
    relay, proxy = _Relay(), _ConnectProxy()
    undo = _proxy_env(HTTP_PROXY=proxy.url)
    try:
        up = _uploader(relay)
        for i in range(3):
            up.put(b"p%d" % i)
            up.post_once()
    finally:
        undo()
        relay.close()
        proxy.close()
    host_port = relay.url.split("/")[2]
    assert proxy.tunnels == [host_port], f"one CONNECT tunnel to the relay, not {proxy.tunnels}"
    assert proxy.auth == ["Basic " + base64.b64encode(b"user:secret").decode()], proxy.auth
    assert relay.accepted == [[b"p0"], [b"p1"], [b"p2"]] and up.stats["post_failed"] == 0, up.stats


def t_no_proxy_reaches_the_relay_directly():
    relay, proxy = _Relay(), _ConnectProxy()
    undo = _proxy_env(HTTP_PROXY=proxy.url, NO_PROXY="127.0.0.1")
    try:
        up = _uploader(relay)
        up.put(b"p0")
        up.post_once()
    finally:
        undo()
        relay.close()
        proxy.close()
    assert proxy.tunnels == [] and relay.accepted == [[b"p0"]], (proxy.tunnels, relay.accepted)


def t_a_tunnel_still_checks_the_relays_certificate():
    import ssl
    undo = _proxy_env(HTTPS_PROXY="http://127.0.0.1:3128")
    try:
        conn = snd.Connection()._new("https", "relay.example", None, 5)
    finally:
        undo()
    assert (conn.host, conn.port) == ("127.0.0.1", 3128), (conn.host, conn.port)
    assert conn._tunnel_host == "relay.example" and conn._tunnel_port == 443, \
        "TLS runs inside the tunnel and checks the relay's name, not the proxy's"
    assert conn._context.verify_mode == ssl.CERT_REQUIRED and conn._context.check_hostname



def _request(relay, **headers):
    return urllib.request.Request(relay.url, data=tp.encode_batch([b"p"]), method="POST",
                                  headers=dict({"Content-Type": "application/octet-stream"}, **headers))


def t_a_redirect_body_is_read_and_the_connection_kept():
    relay = _Relay(script={2: "redirect"})
    try:
        up = _uploader(relay)
        for i in range(3):
            up.put(b"p%d" % i)
            up.post_once()
    finally:
        relay.close()
    assert relay.connections == 1, f"a refused 302 with a body leaves the connection usable: {relay.connections}"
    assert relay.accepted == [[b"p0"], [b"p1", b"p2"]] and up.stats["post_failed"] == 1, \
        (relay.accepted, up.stats)


def t_a_timeout_is_never_retried():
    relay = _Relay(script={2: "stall"})
    conn = snd.Connection()
    try:
        with conn(_request(relay), timeout=0.3) as resp:
            resp.read()
        try:
            conn(_request(relay), timeout=0.3)
        except TimeoutError:
            pass  # expected: the relay did not answer in time
        else:
            raise AssertionError("a stalled post must time out")
        time.sleep(0.8)
        requests = relay.requests
    finally:
        relay.close()
    assert requests == 2, f"a timed-out post may have arrived: it is not sent again at once ({requests})"
    assert conn._conn is None, "a timed-out connection is not reused"


def t_a_bad_header_leaves_no_half_sent_request_behind():
    relay = _Relay()
    conn = snd.Connection()
    try:
        with conn(_request(relay), timeout=5) as resp:
            resp.read()
        try:
            conn(_request(relay, **{"X-Bad": "a\nb"}), timeout=5)
        except ValueError:
            pass  # expected: http.client refuses the header
        else:
            raise AssertionError("a header with a line break must be refused")
        with conn(_request(relay), timeout=5) as resp:
            resp.read()
    finally:
        relay.close()
    assert len(relay.accepted) == 2, f"the next post goes out on a fresh connection: {relay.accepted}"


def t_a_tls_eof_on_a_reused_connection_is_sent_again_once():
    import ssl

    class Fake:
        def __init__(self, fail):
            self.fail, self.closed = fail, False

        def request(self, *a, **kw):
            if self.fail:
                self.fail -= 1
                raise ssl.SSLEOFError("EOF occurred in violation of protocol")

        def getresponse(self):
            class R:
                status, reason, headers, will_close = 200, "OK", {}, False

                def read(self):
                    return b"{}"
            return R()

        def close(self):
            self.closed = True
    made = []

    class Conn(snd.Connection):
        def _new(self, *a):
            made.append(Fake(fail=0))
            return made[-1]
    conn = Conn()
    req = urllib.request.Request("https://relay.example/x", data=b"RCT1", method="POST")
    conn(req, timeout=5)
    made[0].fail = 1                       # the relay's side ended while the connection idled
    conn(req, timeout=5)
    assert len(made) == 2 and made[0].closed, f"one new connection after a TLS EOF: {len(made)}"


def t_an_ipv6_relay_without_a_port_gets_the_scheme_port():
    conn = snd.Connection()._new("https", "::1", None, 5)
    assert (conn.host, conn.port) == ("::1", 443), (conn.host, conn.port)
    undo = _proxy_env(HTTP_PROXY="http://127.0.0.1:3128")
    try:
        conn = snd.Connection()._new("http", "::1", None, 5)
    finally:
        undo()
    assert (conn._tunnel_host, conn._tunnel_port) == ("::1", 80), (conn._tunnel_host, conn._tunnel_port)


def t_no_proxy_matches_the_host_with_its_port_and_a_proxy_without_port_uses_the_default():
    undo = _proxy_env(HTTPS_PROXY="http://127.0.0.1", NO_PROXY="relay.example:8443")
    try:
        direct = snd.Connection()._new("https", "relay.example", 8443, 5)
        tunnel = snd.Connection()._new("https", "relay.example", 443, 5)
    finally:
        undo()
    assert (direct.host, direct.port) == ("relay.example", 8443), "no_proxy names the host with its port"
    assert (tunnel.host, tunnel.port) == ("127.0.0.1", 443), \
        f"a proxy without a port gets the scheme's port, as urlopen does: {(tunnel.host, tunnel.port)}"


def t_the_shutdown_flush_waits_for_a_poster_that_still_runs():
    class Poster:
        def __init__(self, alive):
            self.alive = alive

        def join(self, timeout=None):
            pass

        def is_alive(self):
            return self.alive
    posted = []
    up = snd.Uploader("http://x/ingest", "K", "L", say=lambda line: None,
                      opener=lambda req, timeout: posted.append(1) or _Reply(b"{}"))
    up.put(b"p")
    assert snd.flush(up, Poster(alive=True)) is False and posted == [], \
        "a poster still posting: a second post beside it could send the batch twice"
    assert snd.flush(up, Poster(alive=False)) is True and posted == [1] and up.pending() == 0


def _free_udp_port():
    s = _udp()
    port = s.getsockname()[1]
    s.close()
    return port


class _SimConsole:
    """The simulated PS5 on loopback: answers heartbeats and loops a recording to `reply_port`."""

    def __init__(self, path, reply_port):
        self.hb, self.tx = _udp(), _udp()
        self.hb.settimeout(0.2)
        self.console = sim.Console(sim.load(path), self.tx.sendto, reply_port=reply_port)
        self.stop = threading.Event()
        threading.Thread(target=self._answer, daemon=True).start()
        threading.Thread(target=self.console.run, args=(self.stop,), kwargs={"loop": True}, daemon=True).start()

    def _answer(self):
        while not self.stop.is_set():
            try:
                data, addr = self.hb.recvfrom(64)
            except OSError:
                continue
            self.console.heartbeat(data, addr)

    def close(self):
        self.stop.set()
        self.hb.close()
        self.tx.close()


def _check(argv):
    out = io.StringIO()
    old = sys.stdout
    sys.stdout = out
    try:
        code = snd.main(argv)
    finally:
        sys.stdout = old
    return code, out.getvalue()


def t_check_proves_heartbeat_receive_and_a_waiting_code_and_sends_no_telemetry():
    import test_driver_routes as r
    import driver_invite
    srv, port, path, hub = r._serve()
    with tempfile.TemporaryDirectory() as d:
        rec, _n = _recording(d, seconds=1.0)
        rx_port = _free_udp_port()
        ps5 = _SimConsole(rec, rx_port)
        try:
            invite = driver_invite.create(path)["id"]
            st, text, _h = r._register(port, invite, entry="Team A #1", name="Max")
            assert st == 200, text
            pending = json.loads(text)["code"]
            base = ["--url", f"http://127.0.0.1:{port}/console/drive/ingest", "--check", "--duration", "2",
                    "--console", "127.0.0.1", "--bind", "127.0.0.1", "--receive-port", str(rx_port),
                    "--console-port", str(ps5.hb.getsockname()[1])]
            code, out = _check(base + ["--code", pending])
            assert code == 0, f"a waiting code is ready for the session: {out}"
            for part in ("heartbeat: OK", "receive: OK", "relay: OK", "waiting for approval", "RESULT: OK"):
                assert part in out, f"the check prints {part!r}: {out}"
            approved = r._approved(port, path, invite, entry="Team B #2", name="Ann")
            code, out = _check(base + ["--code", approved])
            assert code == 0 and "approved" in out, out
            conns = json.loads(r._req(port, "/drivers/data")[1])["connections"]
            assert all(c["packets"].get("received", 0) == 0 for c in conns), \
                f"a check posts empty batches only, never telemetry into the race: {conns}"
            driver_invite.create(path)                      # the producer renews the invite
            code, out = _check(base + ["--code", approved])
            assert code == 1 and "relay: FAIL" in out and "RESULT: FAIL" in out, out
            assert "Register again" in out, f"a refused code tells the driver what to do: {out}"
        finally:
            ps5.close()
            srv.shutdown()


def t_check_without_a_console_fails_and_names_the_fix():
    up = snd.Uploader("http://x/ingest", "K", "L", say=lambda line: None,
                      opener=lambda req, timeout: _Reply(b'{"ok": true}'))
    silent = _udp()
    said = []
    sender = snd.Sender(up, console="127.0.0.1", bind="127.0.0.1", receive_port=0,
                        console_port=silent.getsockname()[1], say=said.append)
    try:
        code = snd.self_check(sender, up, duration=0.6, say=said.append)
    finally:
        sender.sock.close()
        silent.close()
    text = "\n".join(said)
    assert code == 1 and "receive: FAIL" in text and "RESULT: FAIL" in text, text
    assert "--console" in text and "UDP" in text, f"a check without packets names the console and the firewall: {text}"
    assert "relay: OK" in text, f"the upload verdict stands on its own: {text}"


def t_check_in_listen_mode_skips_the_heartbeat():
    up = snd.Uploader("http://x/ingest", "K", "L", say=lambda line: None,
                      opener=lambda req, timeout: _Reply(b'{"ok": true}'))
    said, clock = [], [0.0]
    sender = snd.Sender(up, listen=0, bind="127.0.0.1", say=said.append, clock=lambda: clock[0])
    other = _udp()
    for _ in range(60):                       # one second of packets, queued once the port is bound
        other.sendto(b"\0" * 296, ("127.0.0.1", sender.sock.getsockname()[1]))
    real = sender.receive_once

    def receive_once():
        got = real()                           # the real socket path, on a clock of 60 packets a second
        clock[0] += 1 / 60
        return got
    sender.receive_once = receive_once
    try:
        code = snd.self_check(sender, up, duration=1.0, say=said.append)
    finally:
        sender.sock.close()
        other.close()
    text = "\n".join(said)
    assert code == 0 and "heartbeat: skipped" in text and "receive: OK 60 packets" in text, \
        f"listen mode: heartbeat skipped, 60 packets at 60/s, check output:\n{text}"
    assert sender.stats["heartbeats"] == 0, "listen mode leaves the console to the other app"
    assert up.pending() == 0, "packets of a check are counted, never queued for upload"


def t_sender_stats_carry_the_post_latency_the_relay_keeps():
    headers = []
    up = snd.Uploader("http://x/ingest", "K", "L", say=lambda line: None,
                      opener=lambda req, timeout: headers.append(dict(req.header_items())) or _Reply(b"{}"))
    up.latencies.extend([0.010, 0.020, 0.030, 0.200])
    up.put(b"p")
    up.post_once()
    stats = json.loads(headers[0]["X-sender-stats"])
    assert stats["post_ms_p50"] == 30 and stats["post_ms_p95"] == 200, stats
    probe = tp.Probe("k")
    probe.ingest("L", tp.encode_batch([]), stats)
    kept = probe.snapshot()["senders"]["L"]["sender_stats"]
    assert kept["post_ms_p50"] == 30 and kept["post_ms_p95"] == 200, f"the relay keeps the latency: {kept}"


def t_a_full_length_check_prints_verdicts_not_upload_status_lines():
    said, clock = [], [0.0]
    up = snd.Uploader("http://x/ingest", "K", "L", say=lambda line: None,
                      opener=lambda req, timeout: _Reply(b'{"ok": true}'))
    sender = snd.Sender(up, listen=0, bind="127.0.0.1", say=said.append, clock=lambda: clock[0])

    def receive_once():
        sender.stats["received"] += 6
        clock[0] = round(clock[0] + 0.1, 6)
        return True
    sender.receive_once = receive_once
    try:
        code = snd.self_check(sender, up, duration=snd.CHECK_S, say=said.append)
    finally:
        sender.sock.close()
    assert code == 0, said
    assert not [line for line in said if line.startswith("received")], \
        f"a check posts nothing, so it prints no upload status line: {said}"


def t_a_check_whose_heartbeats_all_fail_names_the_console_option():
    up = snd.Uploader("http://x/ingest", "K", "L", say=lambda line: None,
                      opener=lambda req, timeout: _Reply(b'{"ok": true}'))
    said = []
    sender = snd.Sender(up, bind="127.0.0.1", receive_port=0, say=said.append)

    def refused():
        sender.heartbeat_failed += 1            # iOS: the broadcast has no route
    sender.heartbeat = refused
    try:
        code = snd.self_check(sender, up, duration=0.3, say=said.append)
    finally:
        sender.sock.close()
    text = "\n".join(said)
    assert code == 1 and "heartbeat: FAIL 1 failed: pass --console" in text, text


def t_a_check_that_receives_too_few_packets_fails():
    said, clock = [], [0.0]
    up = snd.Uploader("http://x/ingest", "K", "L", say=lambda line: None,
                      opener=lambda req, timeout: _Reply(b'{"ok": true}'))
    sender = snd.Sender(up, listen=0, bind="127.0.0.1", say=said.append, clock=lambda: clock[0])

    def receive_once():
        sender.stats["received"] += 1
        clock[0] = round(clock[0] + 0.1, 6)
        return True
    sender.receive_once = receive_once
    try:
        code = snd.self_check(sender, up, duration=3.0, say=said.append)
    finally:
        sender.sock.close()
    text = "\n".join(said)
    assert code == 1 and "receive: FAIL" in text and "about 60/s" in text, \
        f"10 packets/s is not what GT7 sends: {text}"



def t_the_check_rate_counts_from_the_first_packet():
    said, clock = [], [0.0]
    up = snd.Uploader("http://x/ingest", "K", "L", say=lambda line: None,
                      opener=lambda req, timeout: _Reply(b'{"ok": true}'))
    sender = snd.Sender(up, listen=0, bind="127.0.0.1", say=said.append, clock=lambda: clock[0])

    def receive_once():
        clock[0] = round(clock[0] + 0.1, 6)
        if clock[0] <= 4.0:                       # the console answers after 4 s
            return False
        sender.stats["received"] += 6
        return True
    sender.receive_once = receive_once
    try:
        code = snd.self_check(sender, up, duration=snd.CHECK_S, say=said.append)
    finally:
        sender.sock.close()
    line = [x for x in said if "receive:" in x][0]
    assert code == 0 and ", 60.0/s," in line, f"a late first packet is no low rate: {line}"


def t_on_windows_the_receive_port_is_bound_exclusively():
    calls = []

    class Fake:
        def __init__(self, *a):
            pass

        def setsockopt(self, level, opt, value):
            calls.append(("opt", opt))

        def bind(self, addr):
            calls.append(("bind", addr))

        def settimeout(self, t):
            pass

        def getsockname(self):
            return ("127.0.0.1", 1)
    up = snd.Uploader("http://x/ingest", "K", "L", say=lambda line: None)   # before the patch: ssl reads the OS
    old_socket, old_platform = snd.socket.socket, snd.sys.platform
    had = hasattr(snd.socket, "SO_EXCLUSIVEADDRUSE")
    old_opt = getattr(snd.socket, "SO_EXCLUSIVEADDRUSE", None)
    snd.socket.socket, snd.sys.platform, snd.socket.SO_EXCLUSIVEADDRUSE = Fake, "win32", -5
    try:
        snd.Sender(up, console="127.0.0.1", bind="127.0.0.1", receive_port=33740, say=lambda line: None)
        win = list(calls)
        calls.clear()
        snd.sys.platform = "linux"
        snd.Sender(up, console="127.0.0.1", bind="127.0.0.1", receive_port=33740, say=lambda line: None)
    finally:
        snd.socket.socket, snd.sys.platform = old_socket, old_platform
        if had:
            snd.socket.SO_EXCLUSIVEADDRUSE = old_opt
        else:
            del snd.socket.SO_EXCLUSIVEADDRUSE
    assert ("opt", -5) in win and win.index(("opt", -5)) < win.index(("bind", ("127.0.0.1", 33740))), \
        f"Windows lets a second program share UDP 33740 unless the port is exclusive: {win}"
    assert ("opt", -5) not in calls, calls


def t_post_latency_stats_stay_within_a_minute():
    up = snd.Uploader("http://x/ingest", "K", "L", say=lambda line: None)
    up.latencies.extend([500.0, 600.0])
    assert up._latency_stats() == {"post_ms_p50": 60000, "post_ms_p95": 60000}
    probe = tp.Probe("k")
    probe.ingest("L", tp.encode_batch([]), {"post_ms_p50": 10 ** 12, "post_ms_p95": -4, "received": 3})
    kept = probe.snapshot()["senders"]["L"]["sender_stats"]
    assert kept["post_ms_p50"] == 60000 and kept["post_ms_p95"] == 0 and kept["received"] == 3, kept

if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

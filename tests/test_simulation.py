#!/usr/bin/env python3
"""Simulation mode (RACECAST_SIMULATION): endpoints, profile guard, network guard.
Run: python3 tests/test_simulation.py"""
import os, socket, sys, tempfile, threading, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import simulation as sim  # noqa: E402

ON = {"RACECAST_SIMULATION": "1"}


def t_env_names_are_the_documented_contract():
    assert (sim.FLAG, sim.OBS_WS, sim.SHEET_URL, sim.SHEET_PUSH_URL,
            sim.DISCORD_WEBHOOK_URL, sim.STREAM_BIN, sim.FORCE) == (
        "RACECAST_SIMULATION", "RACECAST_SIM_OBS_WS", "RACECAST_SIM_SHEET_URL",
        "RACECAST_SIM_SHEET_PUSH_URL", "RACECAST_SIM_DISCORD_WEBHOOK_URL",
        "RACECAST_SIM_STREAM_BIN", "RACECAST_SIM_FORCE"), "the RACECAST_SIM_* names changed"
    assert sim.SKIPPED == "skipped (simulation)"
    assert sim.skipped("obs") == "obs: skipped (simulation)."


def t_active_reads_truthy_values_only():
    for v in ("1", "true", "yes", "on", "TRUE"):
        assert sim.active({"RACECAST_SIMULATION": v}), v
    for v in ("", "0", "false", "no", "off"):
        assert not sim.active({"RACECAST_SIMULATION": v}), v
    assert not sim.active({})


def t_loopback_hosts():
    for h in ("127.0.0.1", "127.8.9.10", "localhost", "::1", "[::1]", "::ffff:127.0.0.1"):
        assert sim.is_loopback_host(h), h
    for h in ("", None, "0.0.0.0", "100.64.0.7", "192.0.2.1", "docs.google.com",
              "localhost.example.com", "::"):
        assert not sim.is_loopback_host(h), h


def t_sheet_base_swaps_only_the_origin():
    assert sim.sheet_base("ABC", {}) == "https://docs.google.com/spreadsheets/d/ABC"
    env = dict(ON, RACECAST_SIM_SHEET_URL="http://127.0.0.1:5001/sheet/")
    assert sim.sheet_base("ABC", env) == "http://127.0.0.1:5001/sheet"
    assert sim.sheet_base("ABC", dict(ON)) == "", "an unset sheet must never fall back to Google"
    off = dict(RACECAST_SIM_SHEET_URL="http://127.0.0.1:5001/sheet")
    assert sim.sheet_base("ABC", off).startswith("https://docs.google.com/"), \
        "a RACECAST_SIM_* value outside simulation mode must change nothing"


def t_sheet_csv_url_keeps_the_gviz_contract():
    env = dict(ON, RACECAST_SIM_SHEET_URL="http://127.0.0.1:5001/s")
    assert sim.sheet_csv_url("ID", "Quali Times", env) == \
        "http://127.0.0.1:5001/s/gviz/tq?tqx=out:csv&sheet=Quali%20Times"
    assert sim.sheet_csv_url("ID", "Producer", env, headers=True) == \
        "http://127.0.0.1:5001/s/gviz/tq?tqx=out:csv&headers=1&sheet=Producer"
    assert sim.sheet_csv_url("ID", "Schedule", {}) == \
        "https://docs.google.com/spreadsheets/d/ID/gviz/tq?tqx=out:csv&sheet=Schedule"


def t_obs_target_fails_closed():
    assert sim.obs_target(dict(ON)) is None, "no sim OBS must mean no OBS, never 4455"
    assert sim.obs_target(dict(ON, RACECAST_SIM_OBS_WS="ws://127.0.0.1:4466")) == ("127.0.0.1", 4466)
    assert sim.obs_target(dict(ON, RACECAST_SIM_OBS_WS="ws://192.0.2.1:4455")) is None
    assert sim.obs_target(dict(ON, RACECAST_SIM_OBS_WS="ws://127.0.0.1")) is None


def t_endpoint_rejects_non_loopback_and_wrong_scheme():
    env = dict(ON, RACECAST_SIM_DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/1/x")
    assert sim.endpoint(sim.DISCORD_WEBHOOK_URL, env) == ""
    env = dict(ON, RACECAST_SIM_SHEET_PUSH_URL="ftp://127.0.0.1/x")
    assert sim.endpoint(sim.SHEET_PUSH_URL, env) == ""
    env = dict(ON, RACECAST_SIM_SHEET_PUSH_URL="http://127.0.0.1:9/exec?key=k")
    assert sim.endpoint(sim.SHEET_PUSH_URL, env) == "http://127.0.0.1:9/exec?key=k"


def _stream_bin():
    d = tempfile.mkdtemp()
    for tool in sim.STREAM_TOOLS:
        p = os.path.join(d, tool + (".exe" if os.name == "nt" else ""))
        with open(p, "w") as fh:
            fh.write("")
        os.chmod(p, 0o700)
    return d


def t_problems_name_every_missing_or_bad_endpoint():
    probs = sim.problems(dict(ON))
    assert any(sim.SHEET_URL in p for p in probs), probs
    assert any(sim.STREAM_BIN in p for p in probs), probs
    env = dict(ON, RACECAST_SIM_SHEET_URL="http://127.0.0.1:1/s",
               RACECAST_SIM_STREAM_BIN=_stream_bin())
    assert sim.problems(env) == [], sim.problems(env)
    env["RACECAST_SIM_OBS_WS"] = "ws://203.0.113.5:4455"
    assert any(sim.OBS_WS in p for p in sim.problems(env)), "a non-loopback OBS must be refused"
    env["RACECAST_SIM_OBS_WS"] = "ws://127.0.0.1:4455"
    env["RACECAST_SIM_STREAM_BIN"] = tempfile.mkdtemp()
    assert any("yt-dlp" in p for p in sim.problems(env)), "a stub dir without the tools is refused"
    assert sim.problems({}) == [], "outside simulation nothing is required"


def t_with_stream_bin_prepends_the_stub_dir():
    d = _stream_bin()
    env = dict(ON, RACECAST_SIM_STREAM_BIN=d, PATH="/usr/bin")
    assert sim.with_stream_bin(env)["PATH"] == d + os.pathsep + "/usr/bin"
    once = sim.with_stream_bin(env)
    assert sim.with_stream_bin(once)["PATH"] == once["PATH"], "prepending twice duplicates"
    assert sim.with_stream_bin({"PATH": "/usr/bin"})["PATH"] == "/usr/bin"


def t_config_overrides_replace_every_external_league_value():
    env = dict(ON, RACECAST_SIM_SHEET_PUSH_URL="http://127.0.0.1:7/exec",
               RACECAST_SIM_DISCORD_WEBHOOK_URL="http://127.0.0.1:7/hook")
    o = sim.config_overrides(env)
    assert o["sheet_push_url"] == "http://127.0.0.1:7/exec"
    assert o["discord_webhook_url"] == "http://127.0.0.1:7/hook"
    for k in ("discord_client_id", "discord_client_secret", "discord_voice_url",
              "timing_host_account_id", "timing_host_api_token",
              "timing_host_publish_key", "timing_host_url"):
        assert o[k] == "", f"{k} must be blank in simulation"
    assert sim.config_overrides(dict(ON))["sheet_push_url"] == ""
    assert sim.config_overrides({}) == {}


def t_profile_guard():
    assert sim.profile_refusal("demo", {}) is None
    assert sim.profile_refusal("sim-run", {"SIMULATION": "1"}) is None
    assert sim.profile_refusal(None, {}) is None
    msg = sim.profile_refusal("league", {})
    assert msg and "league" in msg and "--force" in msg
    assert sim.profile_refusal("league", {}, forced="league") is None
    assert sim.profile_refusal("league", {}, forced="other"), "a force is for one profile only"


def _loopback_server():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _refused(call):
    try:
        call()
    except sim.OutboundRefused:
        return True
    return False


def t_network_guard_refuses_everything_but_loopback():
    saved = {(o, n): getattr(o, n) for o, n in sim.GUARDED}
    srv = _loopback_server()
    try:
        assert not sim.install_network_guard({}), "outside simulation no guard"
        assert sim.install_network_guard(dict(ON))
        assert sim.install_network_guard(dict(ON)), "installing twice stays installed"
        port = srv.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
            assert r.read() == b"ok", "loopback must keep working"
        with urllib.request.urlopen(f"http://localhost:{port}/", timeout=5) as r:
            assert r.read() == b"ok"
        for url in ("https://docs.google.com/spreadsheets/d/x/gviz/tq",
                    "http://192.0.2.1/", "http://100.64.0.7:8088/status"):
            try:
                urllib.request.urlopen(url, timeout=2)
            except urllib.error.URLError as exc:
                assert isinstance(exc.reason, sim.OutboundRefused), (url, exc.reason)
            else:
                raise AssertionError(f"{url} reached the network in simulation")
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp.settimeout(1)
        try:
            for what, call in (
                    ("UDP to a non-loopback host", lambda: udp.sendto(b"x", ("192.0.2.1", 33739))),
                    ("socket.connect to a non-loopback IP", lambda: tcp.connect(("192.0.2.1", 443))),
                    ("socket.connect_ex to a non-loopback IP",
                     lambda: tcp.connect_ex(("192.0.2.1", 443))),
                    ("a DNS lookup", lambda: socket.getaddrinfo("docs.google.com", 443)),
                    ("gethostbyname", lambda: socket.gethostbyname("example.com")),
                    ("gethostbyname_ex", lambda: socket.gethostbyname_ex("example.com")),
                    ("a reverse lookup", lambda: socket.gethostbyaddr("192.0.2.1")),
                    ("a connect by name", lambda: tcp.connect_ex(("example.com", 443)))):
                assert _refused(call), f"{what} left the machine in simulation"
            if hasattr(udp, "sendmsg"):
                assert _refused(lambda: udp.sendmsg([b"x"], [], 0, ("192.0.2.1", 33739))), \
                    "UDP sendmsg left the machine in simulation"
            assert socket.gethostbyname("localhost").startswith("127."), "localhost still resolves"
            lookups = []
            orig = sim._original_getaddrinfo
            sim._original_getaddrinfo = lambda *a, **k: lookups.append(a) or orig(*a, **k)
            try:
                assert socket.getfqdn("example.com") == "example.com" and not lookups, \
                    "getfqdn resolved a name in simulation"
            finally:
                sim._original_getaddrinfo = orig
        finally:
            udp.close(); tcp.close()
    finally:
        srv.shutdown(); srv.server_close()
        for (o, n), v in saved.items():
            setattr(o, n, v)
        socket._racecast_sim_guard = False


def _patched_env(**env):
    saved = dict(os.environ)
    os.environ.clear(); os.environ.update(env)
    return lambda: (os.environ.clear(), os.environ.update(saved))


def t_obs_ws_never_reaches_the_real_obs_in_simulation():
    import obs_ws
    opened = []
    orig = obs_ws._open_session
    obs_ws._open_session = lambda host, port, password, timeout: opened.append(
        (host, port, password)) or object()
    restore = _patched_env(RACECAST_SIMULATION="1", RACECAST_OBS_WS_HOST="192.0.2.9",
                           RACECAST_OBS_WS_PORT="4455", RACECAST_OBS_WS_PASSWORD="real")
    try:
        session, note = obs_ws._connect("127.0.0.1", None, None, 1.0)
        assert session is None and opened == [], "simulation without a sim OBS opened a socket"
        assert sim.SKIPPED in note, note
        ok, note = obs_ws.wait_until_ready(timeout=30, sleep=lambda s: (_ for _ in ()).throw(
            AssertionError("waited for an OBS that simulation switched off")))
        assert not ok
        os.environ["RACECAST_SIM_OBS_WS"] = "ws://127.0.0.1:4466"
        session, _ = obs_ws._connect("127.0.0.1", None, None, 1.0)
        assert opened == [("127.0.0.1", 4466, "")], \
            f"the sim OBS must get no host override and no real password: {opened}"
    finally:
        obs_ws._open_session = orig
        restore()


LEAGUE_ENV = ("NAME=League\nSHEET_ID=SHEET\nSHEET_PUSH_URL=https://script.google.com/x/exec\n"
              "DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/1/t\n"
              "DISCORD_CLIENT_ID=cid\nDISCORD_CLIENT_SECRET=csec\n"
              "DISCORD_VOICE_URL=https://discord.com/channels/1/2\n"
              "TIMING_HOST_URL=https://timing.example.com\nTIMING_HOST_PUBLISH_KEY=k\n"
              "TIMING_HOST_ACCOUNT_ID=a\nTIMING_HOST_API_TOKEN=t\n")


def _root(name="demo", text=LEAGUE_ENV):
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, "profiles", name))
    with open(os.path.join(root, "profiles", name, "profile.env"), "w") as fh:
        fh.write(text)
    return root


def t_resolve_config_hands_out_only_simulated_endpoints():
    import config
    root = _root()
    rc = config.resolve_config(root, environ={})
    assert urlparse(rc.discord_webhook_url).hostname == "discord.com", "fixture sanity"
    env = dict(ON, RACECAST_SIM_DISCORD_WEBHOOK_URL="http://127.0.0.1:7/hook")
    rc = config.resolve_config(root, environ=env)
    assert rc.discord_webhook_url == "http://127.0.0.1:7/hook"
    assert rc.sheet_push_url == "", "the league Apps Script must not be written in simulation"
    for field in ("discord_client_id", "discord_client_secret", "discord_voice_url",
                  "timing_host_url", "timing_host_publish_key", "timing_host_api_token",
                  "timing_host_account_id"):
        assert getattr(rc, field) == "", f"{field} leaked into simulation"
    assert rc.sheet_id == "SHEET", "the sheet id stays; only its origin moves"


_RELAY = None


def _relay():
    global _RELAY
    if _RELAY is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "racecast_feeds_sim", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
        _RELAY = importlib.util.module_from_spec(spec); spec.loader.exec_module(_RELAY)
    return _RELAY


def t_feed_ports_move_only_in_simulation_and_never_onto_a_live_port():
    live = (53001, 53002, 53003)
    assert sim.FEED_PORTS == "RACECAST_SIM_FEED_PORTS"
    assert sim.feed_ports({}) == live
    assert sim.feed_ports({"RACECAST_SIM_FEED_PORTS": "41001,41002,41003"}) == live, "outside simulation nothing changes"
    assert sim.feed_ports(dict(ON)) == live, "unset keeps the product ports"
    assert sim.feed_ports(dict(ON, RACECAST_SIM_FEED_PORTS=" 41001, 41002 ,41003 ")) == (41001, 41002, 41003)
    base = dict(ON, RACECAST_SIM_SHEET_URL="http://127.0.0.1:1/s", RACECAST_SIM_STREAM_BIN=_stream_bin())
    assert sim.problems(dict(base, RACECAST_SIM_FEED_PORTS="41001,41002,41003")) == []
    bad = {"41001,41002": "three", "41001,41002,41003,41004": "three", "41001,41001,41003": "differ",
           "41001,53002,41003": "53001-53003", "8088,41002,41003": "8088", "41001,41002,8089": "Control Center",
           "a,b,c": "port number", "0,41002,41003": "port number", "41001;41002;41003": "three",
           "41001,41002,70000": "port number"}
    for value, needle in bad.items():
        env = dict(base, RACECAST_SIM_FEED_PORTS=value)
        found = [p for p in sim.problems(env) if sim.FEED_PORTS in p]
        assert found and needle in found[0], f"{value!r}: {sim.problems(env)}"
        try:
            sim.feed_ports(env)
        except ValueError:
            continue
        raise AssertionError(f"{value!r} must raise instead of falling back to the live ports")
    clash = dict(base, RACECAST_SIM_FEED_PORTS="41001,41002,41003", RACECAST_SIM_RELAY_PORT="41002")
    assert any("control port" in p for p in sim.problems(clash)), sim.problems(clash)
    ui = dict(base, RACECAST_SIM_FEED_PORTS="41001,41002,41003", RACECAST_UI_PORT="41003")
    assert any("Control Center" in p for p in sim.problems(ui)), sim.problems(ui)


def t_relay_port_moves_only_in_simulation():
    assert sim.RELAY_PORT == "RACECAST_SIM_RELAY_PORT" and sim.DEFAULT_RELAY_PORT == 8088
    assert sim.relay_port({}) == 8088
    assert sim.relay_port({"RACECAST_SIM_RELAY_PORT": "41234"}) == 8088, "outside simulation nothing changes"
    assert sim.relay_port(dict(ON)) == 8088, "unset keeps 8088"
    assert sim.relay_port(dict(ON, RACECAST_SIM_RELAY_PORT=" 41234 ")) == 41234
    base = dict(ON, RACECAST_SIM_SHEET_URL="http://127.0.0.1:1/s", RACECAST_SIM_STREAM_BIN=_stream_bin())
    assert sim.problems(dict(base, RACECAST_SIM_RELAY_PORT="41234")) == []
    assert "8088" in " ".join(sim.problems(dict(base, RACECAST_SIM_RELAY_PORT="8088"))), \
        "the live relay's port is no simulation port"
    ui = sim.problems(dict(base, RACECAST_SIM_RELAY_PORT="41235", RACECAST_UI_PORT="41235"))
    assert any("Control Center" in p for p in ui), ui
    assert any("Control Center" in p for p in sim.problems(dict(base, RACECAST_SIM_RELAY_PORT="8089")))
    for bad in ("0", "65536", "abc", "8088.5", "-1", "127.0.0.1:41234", "\u0664\u0661\u0662\u0663\u0664",
                "8088", "53001", "53002", "53003"):
        env = dict(base, RACECAST_SIM_RELAY_PORT=bad)
        assert any(sim.RELAY_PORT in p for p in sim.problems(env)), bad
        try:
            sim.relay_port(env)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} must never fall back to the live relay's port")


def t_relay_control_port_default_follows_simulation():
    m = _relay()
    assert m.control_port(None, {}) == 8088
    assert m.control_port(None, dict(ON, RACECAST_SIM_RELAY_PORT="41234")) == 41234
    assert m.control_port(5000, dict(ON, RACECAST_SIM_RELAY_PORT="41234")) == 5000, "--http-port still wins"


class _Log:
    def __init__(self):
        self.lines = []

    def info(self, msg, *a):
        self.lines.append(msg % a if a else msg)

    warning = exception = info


def t_relay_skips_host_integrations_in_simulation():
    m = _relay()
    import keep_awake
    for env, skipped in ((dict(ON), True), ({}, False)):
        restore = _patched_env(**env) if env else (lambda: None)
        saved = (m.LOG, m.logsetup.obs_log_dir)
        log, looked = _Log(), []
        m.LOG = log
        m.logsetup.obs_log_dir = looked.append
        try:
            fake = type("R", (), {})()
            m.Relay._start_av_watcher(fake)
        finally:
            m.LOG, m.logsetup.obs_log_dir = saved
            restore()
        assert bool(looked) is not skipped, "the OBS log is read only outside simulation"
        if skipped:
            assert any("skipped (simulation)" in ln for ln in log.lines), log.lines
        spawned, klog = [], _Log()
        ka = keep_awake.KeepAwake(4242, platform="linux", which=lambda n: "/usr/bin/" + n,
                                  popen=lambda *a, _s=spawned, **k: _s.append(a) or 1 / 0,
                                  environ=dict(env), log=klog)
        ka.start()
        assert bool(spawned) is not skipped, "systemd-inhibit runs only outside simulation"
        if skipped:
            assert ka.note == "skipped (simulation)" and any("skipped (simulation)" in ln for ln in klog.lines)
        both = dict(env, RACECAST_COOKIES_BROWSER="firefox")
        assert (m.cookie_refresh_browser(both) is None) is skipped, "no export from the real browser"


def t_relay_reads_every_sheet_tab_from_the_simulated_sheet():
    m = _relay()
    restore = _patched_env(RACECAST_SIMULATION="1",
                           RACECAST_SIM_SHEET_URL="http://127.0.0.1:5001/s")
    try:
        assert m._sheet_tab_csv_url("ID", "POV").startswith("http://127.0.0.1:5001/s/gviz/")
        assert m.crew_csv_url("ID", "Crew", None, None).startswith("http://127.0.0.1:5001/s/gviz/")
    finally:
        restore()
    assert m._sheet_tab_csv_url("ID", "POV").startswith("https://docs.google.com/")


def t_no_sheet_url_bypasses_the_simulation_origin():
    """Every fetched Sheet URL goes through simulation.sheet_base; the only other
    docs.google.com literal is the human-facing edit link."""
    allowed = {os.path.join("src", "scripts", "simulation.py"),
               os.path.join("src", "scripts", "config.py")}
    hits = []
    for base, _dirs, files in os.walk(os.path.join(ROOT, "src")):
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(base, f)
            rel = os.path.relpath(path, ROOT)
            with open(path, encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    code = line.split("#", 1)[0]
                    if "docs.google.com/spreadsheets/d/" in code and rel not in allowed:
                        hits.append(f"{rel}:{n}")
    assert not hits, "Sheet URLs built outside simulation.sheet_base: " + ", ".join(hits)


def t_relay_accepts_sim_channels_only_in_simulation():
    m = _relay()
    assert not m.is_channel("sim://comms2"), "sim:// must stay invalid outside simulation"
    restore = _patched_env(RACECAST_SIMULATION="1")
    try:
        assert m.is_channel("sim://comms2") and m.is_feed_source("sim://comms2")
        assert not m.is_channel("sim://../x")
    finally:
        restore()


def t_relay_simulation_startup_refuses_a_missing_endpoint():
    m = _relay()
    assert m.simulation_startup({}) is None, "outside simulation the relay starts as before"
    err = m.simulation_startup(dict(ON))
    assert err and "RACECAST_SIM_SHEET_URL" in err and "RACECAST_SIM_STREAM_BIN" in err, err
    d = _stream_bin()
    env = dict(ON, RACECAST_SIM_SHEET_URL="http://127.0.0.1:1/s",
               RACECAST_SIM_STREAM_BIN=d, PATH="/usr/bin")
    assert m.simulation_startup(env) is None
    assert env["PATH"].split(os.pathsep)[0] == d, "the relay must pull through the stream stand-ins"


def t_relay_timing_host_is_off_in_simulation():
    m = _relay()
    restore = _patched_env(RACECAST_SIMULATION="1", RACECAST_PROFILE_ENV_FILE="/x/profile.env")
    try:
        assert m._timing_host_env_file() == ""
    finally:
        restore()
    restore = _patched_env(RACECAST_PROFILE_ENV_FILE="/x/profile.env")
    try:
        assert m._timing_host_env_file() == "/x/profile.env"
    finally:
        restore()


def t_guarded_argv_runs_a_script_behind_the_network_guard():
    import subprocess
    d = tempfile.mkdtemp()
    script = os.path.join(d, "probe.py")
    with open(script, "w") as fh:
        fh.write("import socket, sys\n"
                 "assert sys.argv[1:] == ['a', 'b'], sys.argv\n"
                 "try:\n"
                 "    socket.create_connection(('192.0.2.1', 443), timeout=1)\n"
                 "except ConnectionRefusedError as exc:\n"
                 "    print('refused', exc)\n")
    env = dict(os.environ, RACECAST_SIMULATION="1")
    out = subprocess.run(sim.guarded_argv(sys.executable, script, ["a", "b"]), env=env,
                         capture_output=True, text=True, errors="replace", timeout=30)
    assert out.returncode == 0, out.stderr
    assert "refused simulation mode" in out.stdout, out.stdout + out.stderr


REAL_ENV = {"RACECAST_SHEET_PUSH_URL": "https://script.google.com/x/exec",
            "RACECAST_DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/t",
            "RACECAST_DISCORD_CLIENT_ID": "cid", "RACECAST_DISCORD_CLIENT_SECRET": "csec",
            "RACECAST_DISCORD_VOICE_URL": "https://discord.com/channels/1/2",
            "RACECAST_TIMING_HOST_URL": "https://timing.example.com"}


def t_env_overrides_replace_real_values_from_any_layer():
    env = dict(ON, **REAL_ENV, RACECAST_SIM_DISCORD_WEBHOOK_URL="http://127.0.0.1:7/hook")
    env.update(sim.env_overrides(env))
    assert env["RACECAST_DISCORD_WEBHOOK_URL"] == "http://127.0.0.1:7/hook"
    for key in REAL_ENV:
        if key != "RACECAST_DISCORD_WEBHOOK_URL":
            assert env[key] == "", f"{key} kept its real value in simulation"
    assert sim.env_overrides(dict(REAL_ENV)) == {}


def t_relay_startup_drops_real_endpoints_from_its_environment():
    m = _relay()
    env = dict(ON, **REAL_ENV, RACECAST_SIM_SHEET_URL="http://127.0.0.1:1/s",
               RACECAST_SIM_STREAM_BIN=_stream_bin(), PATH="/usr/bin")
    assert m.simulation_startup(env) is None
    leaked = {k: env[k] for k in REAL_ENV if env[k]}
    assert not leaked, f"the simulated relay would still use {leaked}"


def t_sim_channel_reaches_the_stream_stand_ins_unchanged():
    m = _relay()
    restore = _patched_env(RACECAST_SIMULATION="1")
    try:
        url = m.channel_url("sim://comms2")
        assert url == "sim://comms2", url
        assert m.platform_of(url) == "youtube", "a sim:// stream takes the yt-dlp resolve path"
        assert m.ytdlp_resolve_cmd(url, None)[-2:] == ["--", "sim://comms2"]
    finally:
        restore()


def t_windows_stand_ins_must_be_exe():
    d = tempfile.mkdtemp()
    open(os.path.join(d, "yt-dlp.bat"), "w").close()
    assert not sim.stand_in_present(d, "yt-dlp", windows=True), \
        "a bare yt-dlp call on Windows skips a .bat and runs the real tool"
    open(os.path.join(d, "yt-dlp.exe"), "w").close()
    assert sim.stand_in_present(d, "yt-dlp", windows=True)


def t_relay_bind_auto_stays_off_the_tailnet_in_simulation():
    m = _relay()
    assert m.resolve_bind_addresses("auto", "100.64.0.7") == ["127.0.0.1", "100.64.0.7"]
    restore = _patched_env(RACECAST_SIMULATION="1")
    try:
        assert m.resolve_bind_addresses("auto", "100.64.0.7") == ["127.0.0.1"], \
            "a rehearsal relay must not be reachable on the tailnet"
        assert m.resolve_bind_addresses("0.0.0.0", None) == ["0.0.0.0"], "an explicit bind stays"
    finally:
        restore()


def t_localhost_counts_only_when_it_resolves_to_loopback():
    good = lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 0)), (10, 1, 6, "", ("::1", 0, 0, 0))]
    bad = lambda *a, **k: [(2, 1, 6, "", ("192.0.2.9", 0))]
    assert sim.loopback_name_ok("localhost", good)
    assert not sim.loopback_name_ok("localhost", bad), "a remapped localhost must not pass"
    assert sim.loopback_name_ok("127.0.0.1", bad), "a literal needs no lookup"
    assert not sim.loopback_name_ok("example.com", good)


def t_an_unrecognised_flag_value_refuses_instead_of_running_for_real():
    for v in ("2", "y", "enabled", "sim"):
        msg = sim.flag_problem({"RACECAST_SIMULATION": v})
        assert msg and "RACECAST_SIMULATION" in msg, v
        assert sim.problems({"RACECAST_SIMULATION": v}), v
    for v in ("", "0", "off", "false", "no", "1", "on", "TRUE"):
        assert sim.flag_problem({"RACECAST_SIMULATION": v}) == "", v
    assert sim.flag_problem({}) == ""
    m = _relay()
    err = m.simulation_startup({"RACECAST_SIMULATION": "enabled"})
    assert err and "RACECAST_SIMULATION" in err, "the relay would start for real"


def t_endpoints_with_userinfo_or_fragment_are_refused():
    for url in ("http://evil.com@127.0.0.1:9/s", "http://user:pw@127.0.0.1:9/s",
                "http://127.0.0.1:9/s#frag"):
        env = dict(ON, RACECAST_SIM_SHEET_URL=url)
        assert sim.endpoint(sim.SHEET_URL, env) == "", url
        assert any(sim.SHEET_URL in p for p in sim.problems(env)), url
    env = dict(ON, RACECAST_SIM_SHEET_URL="http://127.0.0.1:9/s#")
    assert sim.endpoint(sim.SHEET_URL, env) == "http://127.0.0.1:9/s#", "an empty fragment is harmless"


def _load_script(name, *path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "src", *path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _stand_in(d, tool):
    return os.path.join(d, tool + (".exe" if os.name == "nt" else ""))


def t_stream_tools_start_by_absolute_path_in_simulation():
    d = _stream_bin()
    assert sim.tool("yt-dlp", {}) == "yt-dlp", "outside simulation the tool is looked up as before"
    on = dict(ON, RACECAST_SIM_STREAM_BIN=d)
    assert sim.tool("yt-dlp", on) == _stand_in(d, "yt-dlp")
    assert sim.tool("streamlink", on) == _stand_in(d, "streamlink")
    assert sim.tool("ffmpeg", on) == "ffmpeg", "only the stream tools have stand-ins"
    assert sim.tool("yt-dlp", dict(RACECAST_SIM_STREAM_BIN=d)) == "yt-dlp", "the directory alone changes nothing"


def t_every_stream_tool_call_uses_the_stand_in_path():
    """Windows' CreateProcess searches the program's folder and the working directory
    before PATH, so a bare name could still start a real yt-dlp.exe there."""
    import subprocess
    d = _stream_bin()
    yt, sl = _stand_in(d, "yt-dlp"), _stand_in(d, "streamlink")
    restore = _patched_env(**ON, RACECAST_SIM_STREAM_BIN=d, PATH=os.environ.get("PATH", ""))
    seen = []

    class Done:
        returncode, stdout, stderr = 1, "", b""

    def run(argv, **_kw):
        seen.append(list(argv))
        return Done()
    real = subprocess.run
    try:
        m = _relay()
        m._STREAMLINK_HELP = None
        import loopstream
        media = _load_script("sim_get_media", "relay", "get-media.py")
        built = [m.ytdlp_resolve_cmd("sim://c", None), m.ytdlp_live_status_cmd("sim://c", None),
                 m.streamlink_fanout_cmd("http://x/y"), m.streamlink_serve_cmd("http://x/y", 53001),
                 m.preview_pull_streamlink_cmd("http://x/y", "youtube", "best"),
                 loopstream.streamlink_argv("sim://c", "53001"),
                 loopstream.streamlink_argv("https://www.twitch.tv/x", "53001", platform="twitch"),
                 media.build_music_cmd("sim://c", os.path.join(d, "i.mp3")),
                 media.build_download_cmd("sim://c", os.path.join(d, "c.mp4"))]
        subprocess.run = run
        m._STREAMLINK_HELP = None
        m._streamlink_help()
        m.yt_live_video_lookup("sim://c", None)
        m.yt_channel_id("sim://c", None)
        m.export_cookies("firefox", os.path.join(tempfile.mkdtemp(), "yt-cookies.txt"))
        import preflight
        preflight.tool_version("yt-dlp", run=run, which=lambda name: name)
        rc = _load_script("sim_racecast", "racecast.py")
        rc._smoke_capture = lambda argv, timeout=30: (seen.append(list(argv)), (1, ""))[1]
        rc._smoke_fingerprint()
        rc._smoke_js_runtime("https://www.youtube.com/watch?v=abc")
        rc._smoke_yt_candidates("gt7")
        rc._smoke_yt_probe("https://www.youtube.com/watch?v=abc", None)
        rc._smoke_twitch_probe("someone")
        cookies = _load_script("sim_get_cookies", "relay", "get-cookies.py")
        argv = sys.argv
        sys.argv = ["get-cookies.py", "firefox", "--runtime-dir", tempfile.mkdtemp()]
        try:
            cookies.main()
        except SystemExit:
            pass  # the export "failed": only its argv matters
        finally:
            sys.argv = argv
    finally:
        subprocess.run = real
        restore()
    calls = [a for a in built + seen if os.path.basename(a[0]).split(".")[0] in sim.STREAM_TOOLS]
    assert len(calls) >= 22, len(calls)
    bare = [a[:2] for a in calls if a[0] not in (yt, sl)]
    assert not bare, f"started by bare name in simulation: {bare}"


_LAUNCHERS = {"Popen", "run", "call", "check_call", "check_output", "_smoke_capture"}


def bare_stream_tool_argvs(source):
    """Line numbers where `source` starts yt-dlp/streamlink by bare name: a list or tuple
    whose first element is the name and which carries a flag (or is a list), or a launcher
    call whose first argument is a string naming the tool."""
    import ast

    def names_tool(node):
        return (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and node.value.split(" ", 1)[0] in sim.STREAM_TOOLS)

    def is_flag(node):
        return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("-")
    hits = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts and names_tool(node.elts[0]):
            rest = node.elts[1:]
            names_only = bool(rest) and all(names_tool(e) for e in rest)
            if (isinstance(node, ast.List) and not names_only) or any(is_flag(e) for e in rest):
                hits.append(node.lineno)
        elif isinstance(node, ast.Call):
            func = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if func in _LAUNCHERS and node.args and names_tool(node.args[0]):
                hits.append(node.lineno)
    return sorted(hits)


def t_bare_stream_tool_scan_catches_every_form():
    src = """
import subprocess
a = ["yt-dlp",
     "-g", url]
b = ("streamlink", "--stdout", url)
c = ["streamlink"] + args
subprocess.run("yt-dlp --version", shell=True)
subprocess.Popen("streamlink")
ok1 = ("yt-dlp", "streamlink")
ok2 = (("yt-dlp", ["--version"]), ("streamlink", ["--version"]))
ok3 = [tool("yt-dlp"), "-g"]
"""
    assert bare_stream_tool_argvs(src) == [3, 5, 6, 7, 8], bare_stream_tool_argvs(src)


def t_no_bare_stream_tool_argv_in_src():
    hits = []
    for base, _dirs, files in os.walk(os.path.join(ROOT, "src")):
        for f in files:
            if f.endswith(".py"):
                with open(os.path.join(base, f), encoding="utf-8") as fh:
                    hits += [f"{f}:{n}" for n in bare_stream_tool_argvs(fh.read())]
    assert not hits, f"build the argv with simulation.tool(...): {hits}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

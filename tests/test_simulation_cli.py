#!/usr/bin/env python3
"""Simulation mode in the racecast CLI: event start, stop and takeover never reach OBS,
Discord, Companion, Tailscale or the Funnel, and the mode refuses a league profile.
Every external leaf is replaced by a recorder, so a broken guard records instead of
acting on this machine. Run: python3 tests/test_simulation_cli.py"""
import contextlib, importlib.util, io, json, os, subprocess, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("racecast", os.path.join(ROOT, "src", "racecast.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
import simulation as sim  # noqa: E402  (src/scripts is on sys.path via racecast)
import install_apps  # noqa: E402
import obs_ws  # noqa: E402
import tailscale  # noqa: E402

SIM_ENV = {"RACECAST_SIMULATION": "1"}


@contextlib.contextmanager
def _patched(*triples):
    saved = [(obj, name, getattr(obj, name)) for obj, name, _ in triples]
    try:
        for obj, name, value in triples:
            setattr(obj, name, value)
        yield
    finally:
        for obj, name, value in saved:
            setattr(obj, name, value)


@contextlib.contextmanager
def _env(**env):
    saved = dict(os.environ)
    os.environ.update(env)
    try:
        yield
    finally:
        os.environ.clear(); os.environ.update(saved)


def _external(calls):
    """Recorders for every leaf that reaches a real party."""
    ev = m._event_modules()[0]

    def rec(name, result=None):
        def f(*a, **k):
            calls.append(name)
            return result
        return f

    def spawn(*a, **k):
        calls.append(f"spawn {a[0] if a else k.get('args')}")
        raise OSError("spawn blocked by the test")
    return (
        (ev, "app_running", rec("app_running", False)),
        (ev, "launch_command", rec("launch_command")),
        (subprocess, "Popen", spawn), (subprocess, "run", spawn),
        (subprocess, "call", spawn), (subprocess, "check_output", spawn),
        (tailscale, "tailscale_backend", rec("tailscale_backend", (None, None, None))),
        (tailscale, "detect_tailscale_ip", rec("detect_tailscale_ip")),
        (tailscale, "funnel", rec("funnel", (True, ""))),
        (obs_ws, "_open_session", rec("obs_open_session")),
        (m, "_discord_voice_client", rec("discord_voice_client")),
        (m, "_companion_cmds", rec("companion_cmds")),
        (m, "_companion_running", rec("companion_running", False)),
    )


def _start_seams(tmp):
    ev = m._event_modules()[0]
    noop = lambda *a, **k: None   # noqa: E731
    return (
        (m, "_event_gate_results", lambda e, p: []),
        (m, "_runtime_dir", lambda: tmp),
        (m, "_relay_already_running_for_active_profile", lambda: False),
        (m, "_relay_start_signals", lambda: dict(
            port_pids=[], feed_pids=[], pidfile_pid=None, pidfile_alive=False,
            running_profile="", active_profile="demo", http_ok=False)),
        (m, "_unstopped_event_continues", lambda: False),
        (m, "_clear_on_air_record", noop), (m, "_write_part_reset", noop),
        (m, "_start_driver_invite", noop), (m, "_write_session_start", noop),
        (m, "_reset_telemetry_record", noop), (m, "relay_start", noop),
        (m, "_relay_http_ok", lambda: True), (m, "_refresh_obs_pages", noop),
        (m, "event_status", noop), (m, "_companion_tablet_port", lambda: 8000),
        (ev, "wait_until_up", lambda probes, **k: {n: p() for n, p in probes.items()}),
    )


def t_event_start_reaches_no_real_party_and_reports_each_skip():
    calls, out = [], io.StringIO()
    with tempfile.TemporaryDirectory() as tmp, _env(**SIM_ENV), \
            _patched(*_external(calls), *_start_seams(tmp)), contextlib.redirect_stdout(out):
        m.event_start([])
    text = out.getvalue()
    assert calls == [], f"simulation reached a real party: {calls}"
    for what in ("tailscale", "discord", "obs", "companion", "funnel on",
                 "discord voice auto-join"):
        assert sim.skipped(what) in text, f"no skip line for {what}:\n{text}"


def t_event_start_without_simulation_still_launches():
    calls, out = [], io.StringIO()
    with tempfile.TemporaryDirectory() as tmp, _patched(
            *_external(calls), *_start_seams(tmp), (m, "_discord_autojoin", lambda: None),
            (m, "companion_start", lambda rest: calls.append("companion_start")),
            (m, "_funnel_auto_enabled", lambda: False),
            (m, "_wait_for_obs_ready", lambda timeout=0, early=False: False),
            (m, "_align_scene_collection", lambda relay_up=None: None),
            (m, "_switch_to_standby", lambda: None),
            (install_apps, "app_present", lambda app, platform: True)), \
            contextlib.redirect_stdout(out):
        os.environ.pop("RACECAST_SIMULATION", None)
        m.event_start([])
    assert "app_running" in calls and "tailscale_backend" in calls, \
        f"the recorders must see the real path outside simulation: {calls}"
    assert sim.SKIPPED not in out.getvalue()


def t_event_stop_reaches_no_real_party():
    calls, out, sent = [], io.StringIO(), []
    noop = lambda *a, **k: None   # noqa: E731
    with tempfile.TemporaryDirectory() as tmp, _env(**SIM_ENV), _patched(
            *_external(calls),
            (m, "_stop_driver_invite", noop), (m, "_nudge_relay_timing_host", noop),
            (m, "_relay_is_alive", lambda: True), (m, "_running_relay_mode", lambda: "simulation"),
            (m, "_build_report_file", lambda: {"summary": "report", "path": "r.html"}),
            (m, "_send_report_core", lambda *a, **k: sent.append(a)),
            (m, "_streams_static_dir", lambda: tmp), (m, "_wait_timing_host_end", noop),
            (m, "relay_stop", noop)), contextlib.redirect_stdout(out):
        m.event_stop([])
    assert calls == [], f"simulation reached a real party: {calls}"
    assert sim.skipped("companion") in out.getvalue()
    assert sim.skipped("discord voice auto-leave") in out.getvalue()
    assert sent, "the report still goes to the (simulated) Discord webhook"


def t_takeover_refuses_the_funnel_and_the_tailnet():
    calls = []
    with _env(**SIM_ENV), _patched(*_external(calls)):
        for args in (["127.0.0.1", "--funnel"], ["100.64.0.7"]):
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    m.event_takeover(args)
            except SystemExit as exc:
                assert "simulated takeover" in str(exc.code), exc.code
            else:
                raise AssertionError(f"takeover {args} ran in simulation")
    assert calls == [], calls


def t_leaf_commands_skip_in_simulation():
    calls = []
    with _env(**SIM_ENV), _patched(*_external(calls)):
        for fn, args in ((m.app_launch_cmd, ["obs"]), (m.app_quit_cmd, ["obs"]),
                         (m.companion_start, []), (m.companion_stop, []),
                         (m.funnel_cmd, ["on"]), (m.tailscale_down_cmd, []),
                         (m.discord_cmd, ["join"])):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                fn(args)
            assert sim.SKIPPED in out.getvalue(), (fn.__name__, out.getvalue())
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                m.tailscale_up_cmd([])
        except SystemExit as exc:
            assert exc.code == 0, "a skipped tailscale up is not a failure"
        spawned = True
        try:
            m._gui_spawn(["obs"], None, None, "obs")
        except OSError:
            spawned = False
        assert not spawned, "a GUI app was spawned in simulation"
        assert m._tailscale_ip() is None and m._tailscale_magicdns() == ""
    assert calls == [], calls


def t_readiness_reports_skips_not_failures():
    ev, pf = m._event_modules()
    calls = []
    with tempfile.TemporaryDirectory() as tmp, _env(**SIM_ENV), _patched(
            *_external(calls), (m, "_relay_pid_path", lambda: os.path.join(tmp, "relay.pid")),
            (m, "_asset_state", lambda e: (tmp, tmp, [], [])),
            (m, "event_track_read_data", lambda: {"pick": None})):
        sections = dict(m._event_sections(ev, pf))
    rows = {r.name: r for r in sections["Apps"] + sections["Services"]}
    for name in ("OBS", "Discord", "Tailscale", "Companion"):
        assert rows[name].level == ev.INFO and sim.SKIPPED in rows[name].detail, rows[name]
    assert calls == [], calls


def t_simulation_flag_and_gate():
    env = {}
    with _patched((m.pcfg, "load_machine_env", lambda start: {})):
        assert m._apply_simulation_flag(["--simulation", "event", "start"], env) == \
            (["event", "start"], False)
    assert sim.active(env)
    with _patched((m.pcfg, "load_machine_env", lambda start: {})):
        assert m._apply_simulation_flag(["--simulation", "--force", "event", "start"], {}) == \
            (["event", "start"], True), "a global --force is the simulation profile override"
        assert m._apply_simulation_flag(["--simulation", "event", "start", "--force"], {}) == \
            (["event", "start", "--force"], False), "a subcommand --force keeps its own meaning"
        assert m._apply_simulation_flag(["--force", "event", "start"], {}) == \
            (["--force", "event", "start"], False), "outside simulation nothing is consumed"
    env = {}
    with _patched((m.pcfg, "load_machine_env",
                   lambda start: {"RACECAST_SIMULATION": "1", "RACECAST_SIM_SHEET_URL": "x",
                                  "OTHER": "y"})):
        m._apply_simulation_flag(["event", "start"], env)
    assert sim.active(env) and env["RACECAST_SIM_SHEET_URL"] == "x" and "OTHER" not in env
    d = tempfile.mkdtemp()
    for tool in sim.STREAM_TOOLS:
        p = os.path.join(d, tool + (".exe" if os.name == "nt" else ""))
        open(p, "w").close(); os.chmod(p, 0o755)
    ok = dict(SIM_ENV, RACECAST_SIM_SHEET_URL="http://127.0.0.1:1/s", RACECAST_SIM_STREAM_BIN=d)
    assert m._simulation_gate(["event", "start"], dict(ok), "demo", {}) is None
    assert m._simulation_gate(["event", "start"], {}, "league", {}) is None, "off = no gate"
    assert m._simulation_gate([], dict(ok), "league", {}), \
        "the windowed Control Center launcher passes no arguments and must be gated too"
    refused = m._simulation_gate(["event", "start"], dict(ok), "league", {})
    assert refused and "racecast --simulation --force" in refused, refused
    assert "RACECAST_SIM_SHEET_URL" in m._simulation_gate(["relay", "start"], dict(SIM_ENV), "demo", {})
    assert m._simulation_gate(["profile", "use", "demo"], dict(SIM_ENV), "league", {}) is None, \
        "switching to the demo profile must stay possible"
    assert m._simulation_gate(["event", "start", "--force"], dict(ok), "league", {}), \
        "event start --force skips the pre-flight gate, it must not pass the profile guard"
    fdir = tempfile.mkdtemp()
    forced = dict(ok)
    assert m._simulation_gate(["event", "start"], forced, "league", {}, force=True,
                              force_dir=fdir) is None
    assert forced[sim.FORCE].startswith("league:"), "the children of a forced run need a pass"
    assert m._simulation_gate(["relay", "start"], dict(forced), "league", {},
                              force_dir=fdir) is None
    assert m._simulation_gate(["relay", "start"], dict(forced), "other", {}, force_dir=fdir), \
        "a force covers only the profile it was given for"
    swapped = dict(ok, RACECAST_SIM_FORCE="other:" + forced[sim.FORCE].split(":")[1])
    assert m._simulation_gate(["relay", "start"], swapped, "other", {}, force_dir=fdir), \
        "a pass recorded for one profile must not be renamed to another"
    stale = dict(ok, RACECAST_SIM_FORCE="league")
    assert m._simulation_gate(["relay", "start"], stale, "league", {}, force_dir=fdir), \
        "a bare exported RACECAST_SIM_FORCE must not pass"
    forged = dict(ok, RACECAST_SIM_FORCE="league:" + "0" * 32)
    assert m._simulation_gate(["relay", "start"], forged, "league", {}, force_dir=fdir), \
        "a pass needs the record its forcing run wrote"
    with _patched((m.sv, "pid_alive", lambda pid: False)):
        assert m._simulation_gate(["relay", "start"], dict(forced), "league", {},
                                  force_dir=fdir), "a pass dies with the run that forced it"
    env = {}
    with _patched((m.pcfg, "load_machine_env",
                   lambda start: {"RACECAST_SIMULATION": "1", "RACECAST_SIM_FORCE": "league"})):
        m._apply_simulation_flag(["event", "start"], env)
    assert sim.FORCE not in env, "the machine .env must not carry a force"


def t_scripts_run_behind_the_guard_in_simulation():
    with _env(**SIM_ENV):
        kind, argv, _ = m._script_invocation("relay/get-media.py", ["--out", "x"], False)
    assert kind == "subprocess" and argv[1] == "-c" and "install_network_guard" in argv[2]
    assert argv[-3:] == [os.path.join(m.HERE, "relay", "get-media.py"), "--out", "x"]
    os.environ.pop("RACECAST_SIMULATION", None)
    kind, argv, _ = m._script_invocation("relay/get-media.py", [], False)
    assert argv == [sys.executable, os.path.join(m.HERE, "relay", "get-media.py")]


def t_update_check_is_off():
    with _env(**SIM_ENV):
        out = m.update_check_data(fetch=lambda: (_ for _ in ()).throw(
            AssertionError("update check went online")), current="9.9.9", frozen=True)
    assert sim.SKIPPED in out.get("note", ""), out


def t_profile_env_injection_never_hands_out_real_endpoints():
    rc = m.pcfg.ResolvedConfig(profile="demo", name="Demo", sheet_id="S",
                               sheet_push_url="https://script.google.com/x/exec")
    env = dict(SIM_ENV, RACECAST_DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/1/t")
    m._apply_profile_env(rc, env)
    assert env["RACECAST_SHEET_PUSH_URL"] == "" and env["RACECAST_DISCORD_WEBHOOK_URL"] == "", \
        "a shell or machine .env value survived the simulation override"


def t_control_center_shows_the_simulation_badge():
    with _env(**SIM_ENV):
        payload = m.ui_status_payload(
            relay=lambda: {}, companion=lambda: {}, streams=lambda: [], tailscale=lambda: None,
            cookies=lambda: {}, apps_running=lambda: {})
    assert payload["simulation"] is True, "the Control Center cannot tell it runs simulated"
    with open(os.path.join(ROOT, "src", "ui", "control-center.html"), encoding="utf-8") as fh:
        page = fh.read()
    assert 'id="sim-badge"' in page and "$('sim-badge').hidden = !s.simulation" in page, \
        "the header badge must follow /api/status"


def t_bootstrap_gate_refuses_an_unrecognised_flag():
    refused = m._simulation_gate(["event", "start"], {"RACECAST_SIMULATION": "y"}, "demo", {})
    assert refused and "RACECAST_SIMULATION" in refused, refused
    assert m._simulation_gate(["help"], {"RACECAST_SIMULATION": "y"}, "demo", {}), \
        "no command runs for real on a mistyped flag"


def _plan(running_mode, active_mode, **kw):
    args = dict(port_pids=[4242], feed_pids=[], pidfile_pid=4242, pidfile_alive=True,
                running_profile="demo", active_profile="demo", http_ok=True,
                running_mode=running_mode, active_mode=active_mode)
    args.update(kw)
    return m.relay_start_plan(**args)


def t_relay_start_never_crosses_simulation_and_live():
    assert _plan("live", "live")[0] == "running"
    assert _plan("simulation", "simulation")[0] == "running"
    action, kill, reason = _plan("live", "simulation")
    assert action == "refuse" and kill == [] and "relay stop" in reason, (action, reason)
    action, kill, reason = _plan("simulation", "live")
    assert action == "refuse" and kill == [] and "relay stop" in reason, (action, reason)
    action, kill, _ = _plan("", "simulation", pidfile_alive=False, pidfile_pid=None)
    assert action == "refuse" and kill == [], "simulation never kills an unknown holder"
    assert _plan("", "live", pidfile_alive=False, pidfile_pid=None)[0] == "heal", \
        "a live start keeps healing an orphan"
    assert _plan("", "simulation", port_pids=[])[0] == "start"
    action, _kill, reason = _plan("simulation", "live")
    assert "racecast relay stop" in reason and "--simulation" not in reason, \
        "a live operator must be able to run the hint as printed"
    assert _plan("simulation", "live", pidfile_alive=False, pidfile_pid=None)[0] == "heal", \
        "a stale simulation stamp with a dead relay must not block a live start"
    assert _plan("simulation", "live", port_pids=[555])[0] == "heal", \
        "a stale simulation stamp says nothing about a foreign holder"
    assert _plan("simulation", "live", port_pids=[])[0] == "start"


def t_event_start_refuses_a_crossed_relay_before_any_launch():
    calls = []
    signals = dict(port_pids=[4242], feed_pids=[], pidfile_pid=4242, pidfile_alive=True,
                   running_profile="demo", active_profile="demo", http_ok=True,
                   running_mode="simulation", active_mode="live")
    os.environ.pop("RACECAST_SIMULATION", None)
    tmp = tempfile.mkdtemp()
    seams = [t for t in _start_seams(tmp) if t[1] != "_relay_start_signals"]
    with _patched(*_external(calls), *seams,
                  (m, "_relay_start_signals", lambda: dict(signals)),
                  (m, "_tailscale_connect", lambda ev=None: calls.append("tailscale"))):
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                m.event_start([])
        except SystemExit as exc:
            assert "racecast relay stop" in str(exc.code), exc.code
        else:
            raise AssertionError("event start went past a simulation relay")
    assert calls == [], f"event start launched before it refused: {calls}"


def t_relay_start_refuses_without_killing():
    killed = []
    signals = dict(port_pids=[4242], feed_pids=[], pidfile_pid=4242, pidfile_alive=True,
                   running_profile="demo", active_profile="demo", http_ok=True,
                   running_mode="live", active_mode="simulation")
    with _env(**SIM_ENV), _patched((m, "_relay_start_signals", lambda: dict(signals)),
                                   (m.pt, "kill_pid", killed.append),
                                   (m, "_write_relay_profile_stamp", lambda: killed.append("stamp")),
                                   (m, "_spawn_relay_verified", lambda argv: killed.append("spawn"))):
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                m.relay_start([])
        except SystemExit as exc:
            assert "relay stop" in str(exc.code), exc.code
        else:
            raise AssertionError("a simulated start went past a live relay")
    assert killed == []


def t_simulated_stop_leaves_a_live_relay_alone():
    stopped = []
    with _env(**SIM_ENV), _patched((m.sv, "read_pid", lambda p: 4242),
                                   (m.sv, "pid_alive", lambda pid: True),
                                   (m.sv, "stop_pid", lambda *a, **k: stopped.append(a)),
                                   (m, "_running_relay_mode", lambda: "live"),
                                   (m, "_relay_is_alive", lambda: True),
                                   (m, "_stop_driver_invite", lambda: stopped.append("invite"))):
        for fn in (m.relay_stop, m.event_stop):
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    fn([])
            except SystemExit as exc:
                assert "live relay" in str(exc.code), exc.code
            else:
                raise AssertionError(f"{fn.__name__} in simulation touched a live relay")
    assert stopped == []


def t_relay_mode_stamp_round_trip():
    with tempfile.TemporaryDirectory() as tmp, _patched(
            (m, "_runtime_base_dir", lambda: tmp), (m, "_active_profile_name", lambda: "demo")):
        with _env(**SIM_ENV):
            m._write_relay_profile_stamp()
        assert m._running_relay_mode() == "simulation"
        os.environ.pop("RACECAST_SIMULATION", None)
        m._write_relay_profile_stamp()
        assert m._running_relay_mode() == "live"
        m._clear_relay_profile_stamp()
        assert m._running_relay_mode() == ""


def t_simulated_freeport_never_kills_a_live_relay():
    killed = []
    seams = ((m, "_relay_is_alive", lambda: True), (m, "streams_status_data", lambda: []),
             (m.pt, "pids_on_port", lambda port: [4242]), (m.pt, "kill_pid", killed.append))
    with _env(**SIM_ENV), _patched(*seams, (m, "_running_relay_mode", lambda: "live")):
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                m.freeport_cmd(["--force"])
        except SystemExit as exc:
            assert exc.code == 1, exc.code
    assert killed == [], "a simulated freeport --force killed a live relay"
    with _env(**SIM_ENV), _patched(*seams, (m, "_running_relay_mode", lambda: "simulation")):
        with contextlib.suppress(SystemExit), contextlib.redirect_stdout(io.StringIO()):
            m.freeport_cmd(["--force"])
    assert killed, "--force still frees the ports of a simulation relay"


def _sim_ok_env():
    d = tempfile.mkdtemp()
    for tool in sim.STREAM_TOOLS:
        p = os.path.join(d, tool + (".exe" if os.name == "nt" else ""))
        open(p, "w").close(); os.chmod(p, 0o755)
    return dict(SIM_ENV, RACECAST_SIM_SHEET_URL="http://127.0.0.1:1/s", RACECAST_SIM_STREAM_BIN=d)


def _sleeper():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def _grant(fdir):
    env = _sim_ok_env()
    with _patched((m.atexit, "register", lambda *a, **k: None)):
        assert m._simulation_gate(["event", "start"], env, "league", {}, force=True,
                                  force_dir=fdir) is None
    nonce = env[sim.FORCE].split(":")[1]
    return env, os.path.join(fdir, nonce + ".json")


def t_force_record_ties_pid_to_its_start_and_is_released_on_exit():
    fdir = tempfile.mkdtemp()
    registered = []
    env = _sim_ok_env()
    with _patched((m.atexit, "register", lambda fn, *a: registered.append((fn, a)))):
        m._simulation_gate(["event", "start"], env, "league", {}, force=True, force_dir=fdir)
    path = os.path.join(fdir, env[sim.FORCE].split(":")[1] + ".json")
    with open(path) as fh:
        holder = json.load(fh)["holders"][0]
    assert holder["pid"] == os.getpid() and "started" in holder, holder
    assert registered, "the grant must be released when the forcing run exits"
    fn, args = registered[0]
    fn(*args)
    assert not os.path.exists(path), "a normal exit leaves no force record behind"


def t_process_start_marker():
    p = subprocess.Popen([sys.executable, "-c", "pass"]); p.wait()
    assert sim.process_started(p.pid) is None, "a gone process has no start marker"
    if sys.platform.startswith("linux") or sys.platform == "darwin":
        mark = sim.process_started(os.getpid())
        assert mark and mark == sim.process_started(os.getpid()), mark


def t_a_reused_pid_does_not_revive_a_pass():
    fdir = tempfile.mkdtemp()
    env, path = _grant(fdir)
    with open(path) as fh:
        record = json.load(fh)
    record["holders"][0]["started"] = "not-this-process"
    with open(path, "w") as fh:
        json.dump(record, fh)
    if sim.process_started(os.getpid()) is not None:
        assert m._simulation_gate(["relay", "start"], dict(env), "league", {}, force_dir=fdir), \
            "a pid whose start time differs is another process"


def t_dead_force_records_are_pruned_on_every_check():
    fdir = tempfile.mkdtemp()
    p = subprocess.Popen([sys.executable, "-c", "pass"]); p.wait()
    stale = os.path.join(fdir, "a" * 32 + ".json")
    with open(stale, "w") as fh:
        json.dump({"profile": "league", "holders": [{"pid": p.pid, "started": None}]}, fh)
    m._simulation_gate(["relay", "start"], _sim_ok_env(), "demo", {}, force_dir=fdir)
    assert not os.path.exists(stale), "a record without a live holder must go"


def t_the_forced_relay_keeps_the_pass_after_the_cli_exits():
    fdir = tempfile.mkdtemp()
    env, path = _grant(fdir)
    relay = _sleeper()
    try:
        renamed = dict(env, RACECAST_SIM_FORCE="other:" + env[sim.FORCE].split(":")[1])
        m._hold_simulation_force(relay.pid, renamed, fdir)
        with open(path) as fh:
            assert len(json.load(fh)["holders"]) == 1, "a renamed pass must not gain holders"
        m._hold_simulation_force(relay.pid, env, fdir)
        m._release_simulation_force(path, os.getpid())   # the forcing CLI exits
        assert m._simulation_gate(["event", "stop"], dict(env), "league", {}, force_dir=fdir) is None, \
            "the Director Panel's Stop event must pass while the forced relay runs"
    finally:
        relay.kill(); relay.wait()
    assert m._simulation_gate(["event", "stop"], dict(env), "league", {}, force_dir=fdir), \
        "the pass ends with the relay"


def t_relay_start_registers_the_relay_as_a_holder():
    held = []
    signals = dict(port_pids=[], feed_pids=[], pidfile_pid=None, pidfile_alive=False,
                   running_profile="", active_profile="demo", http_ok=False)
    noop = lambda *a, **k: None   # noqa: E731
    with _env(**SIM_ENV, RACECAST_SIM_FORCE="league:" + "b" * 32), _patched(
            (m, "_relay_start_signals", lambda: dict(signals)), (m.pt, "FEED_PORTS", ()),
            (m, "_ensure_active_console_secret", noop), (m, "_resolve_producer_name", lambda: "P"),
            (m, "_write_relay_profile_stamp", noop), (m, "_spawn_relay_verified", lambda argv: 4242),
            (m, "_append_tailscale_snapshot", noop), (m, "_refresh_obs_pages", noop),
            (m, "_hold_simulation_force", lambda pid, *a: held.append(pid))), \
            contextlib.redirect_stdout(io.StringIO()):
        m.relay_start([])
    assert held == [4242], held


def t_force_record_updates_are_serialized():
    fdir = tempfile.mkdtemp()
    env, path = _grant(fdir)
    relay = _sleeper()
    paused, go = threading.Event(), threading.Event()
    real_read = m._read_force_record

    def slow_read(p):
        record = real_read(p)
        if threading.current_thread().name == "pruner":
            paused.set(); go.wait(5)
        return record
    try:
        with _patched((m, "_read_force_record", slow_read)):
            pruner = threading.Thread(target=m._prune_simulation_force, args=(fdir,), name="pruner")
            pruner.start(); paused.wait(5)
            holder = threading.Thread(target=m._hold_simulation_force, args=(relay.pid, env, fdir))
            holder.start(); holder.join(0.5)
            go.set(); pruner.join(5); holder.join(5)
        with open(path) as fh:
            pids = [h["pid"] for h in json.load(fh)["holders"]]
        assert relay.pid in pids, f"a concurrent prune dropped the relay holder: {pids}"
    finally:
        relay.kill(); relay.wait()


def t_force_lock_breaks_a_stale_lock_and_times_out_on_a_live_one():
    fdir = tempfile.mkdtemp()
    lock = os.path.join(fdir, ".lock")
    open(lock, "w").close()
    old = time.time() - m.FORCE_LOCK_STALE_S - 5
    os.utime(lock, (old, old))
    with m._force_lock(fdir):
        assert os.path.exists(lock)
    assert not os.path.exists(lock), "the lock is released"
    open(lock, "w").close()
    with _patched((m, "FORCE_LOCK_WAIT_S", 0.2)):
        try:
            with m._force_lock(fdir):
                raise AssertionError("took a lock another process holds")
        except OSError:
            pass  # a live lock times out
    os.remove(lock)


def t_force_record_replace_retries_a_windows_sharing_violation():
    fdir = tempfile.mkdtemp()
    path = os.path.join(fdir, "c" * 32 + ".json")
    real, fails = os.replace, []

    def flaky(src, dst):
        if len(fails) < 2:
            fails.append(1)
            raise PermissionError("in use")
        return real(src, dst)
    with _patched((m.os, "replace", flaky)):
        m._write_force_record(path, {"profile": "x", "holders": [m._force_holder(os.getpid())]})
    assert os.path.exists(path) and len(fails) == 2


def t_gate_never_tracebacks_on_a_force_record_error():
    fdir = tempfile.mkdtemp()
    env, _path = _grant(fdir)

    def boom(*a, **k):
        raise PermissionError("locked by another process")
    with _patched((m, "_prune_simulation_force", boom)):
        assert m._simulation_gate(["event", "stop"], dict(env), "league", {}, force_dir=fdir) is None, \
            "a failed prune is ignored"
    with _patched((m, "_read_force_record", boom)):
        refused = m._simulation_gate(["event", "stop"], dict(env), "league", {}, force_dir=fdir)
    assert refused and "force record" in refused, refused


def t_a_plain_gate_check_leaves_no_force_dir_behind():
    fdir = os.path.join(tempfile.mkdtemp(), "simulation-force")
    m._simulation_gate(["relay", "start"], _sim_ok_env(), "demo", {}, force_dir=fdir)
    assert not os.path.exists(fdir), "a check without a pass must not create the record dir"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")

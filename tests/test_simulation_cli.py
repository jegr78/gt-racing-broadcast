#!/usr/bin/env python3
"""Simulation mode in the racecast CLI: event start, stop and takeover never reach OBS,
Discord, Companion, Tailscale or the Funnel, and the mode refuses a league profile.
Every external leaf is replaced by a recorder, so a broken guard records instead of
acting on this machine. Run: python3 tests/test_simulation_cli.py"""
import contextlib, importlib.util, io, os, subprocess, sys, tempfile

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
            (m, "_relay_is_alive", lambda: True),
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
    forced = dict(ok)
    assert m._simulation_gate(["event", "start"], forced, "league", {}, force=True) is None
    assert forced[sim.FORCE] == "league", "the children of a forced run need the same pass"
    assert m._simulation_gate(["relay", "start"], dict(forced), "league", {}) is None
    assert m._simulation_gate(["relay", "start"], dict(forced), "other", {}), \
        "a force covers only the profile it was given for"


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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
